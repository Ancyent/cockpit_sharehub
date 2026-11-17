# Architecture

## Components

```
Browser ── Cockpit (cockpit-ws) ── cockpit-bridge ──(sudo)── /usr/lib/sharehub/sharehub-helper
             │                                                  │
             └─ /usr/share/cockpit/sharehub  (HTML/CSS/JS)      └─ Python package "sharehub"
                                                                     ├─ state.py      state model + validation + history
                                                                     ├─ reconcile.py  apply/rollback orchestration
                                                                     ├─ samba.py      registry, shares, homes, VFS modules
                                                                     ├─ nfs.py        exports + nfs.conf
                                                                     ├─ ftp.py        ProFTPD + bind mounts
                                                                     ├─ quota.py      per-share size limits (ZFS/XFS/ext4/Btrfs)
                                                                     ├─ users.py      users, groups, Samba passdb
                                                                     ├─ ad.py         Active Directory (winbind)
                                                                     ├─ services.py   systemd, sessions, firewall, discovery
                                                                     └─ migrate.py    import from other tools
```

- **Frontend.** Plain JavaScript and CSS (no build step), loaded by Cockpit. It talks to the system only through `cockpit.spawn([helper, command], {superuser: "require"})`.
- **Helper.** Every request is one process: JSON on stdin, `{"ok": true, "data": …}` or `{"ok": false, "error": "…"}` on stdout.
  - Changes take an exclusive lock on `/run/sharehub.lock`; read-only commands take a shared lock.
  - Errors are written for people and shown in the UI unchanged.

## State and reconciliation

- **Source of truth.** All configuration lives in `/etc/sharehub/state.json` (mode 0600): shares and the `samba`, `nfs`, `ftp` and `ad` settings.
- **Generated files.** Everything else is generated from the state.
- **Change sequence.** Each change follows the same steps:

  1. The new state is validated and normalised (`state.py`).
  2. `reconcile.commit()` applies it module by module. Each module **validates before it writes**:

     | Module | Validation |
     |---|---|
     | Samba | The rendered configuration is checked with `testparm` before the registry is touched. |
     | NFS | The exports are written and `exportfs -ra` must succeed, otherwise the old file is restored. |
     | ProFTPD | The include is written and `proftpd -t` must succeed, otherwise it is restored. |

  3. If any module fails, the **previous state is re-applied** and the error is returned.
  4. On success the state is saved, along with a copy in `/etc/sharehub/history/` (the last 30 changes).

The **Restore** action in the UI re-applies one of those history files.

## Files ShareHub writes

| File | Content | Ownership |
|---|---|---|
| `/etc/samba/smb.conf` | One line, `include = registry`, at the end of `[global]` (backup in `smb.conf.sharehub.bak`) | once, on request |
| Samba registry `[global]` | Only the parameters listed in `samba.MANAGED_GLOBAL` | ShareHub keys only |
| Samba registry shares | Shares in the state, plus `[homes]` | sections listed in `/etc/sharehub/owned-smb.json` |
| `/etc/exports.d/sharehub.exports` | NFS exports | whole file |
| `/etc/nfs.conf.d/sharehub.conf` | NFS versions, threads, fixed NFSv3 ports | whole file |
| `/etc/proftpd/conf.d/sharehub.conf` | Jail, TLS, passive ports, per-share and home `<Directory>` rules | whole file |
| `/etc/fstab` | Bind mounts `<folder> /srv/ftp-shares/<share> none bind,nofail,x-sharehub` | lines tagged `x-sharehub` |
| `/etc/sharehub/quotas.json` | Applied size limits and XFS/ext4 project IDs (from 20000) | whole file |
| ZFS `refquota`, XFS/ext4 project quota, Btrfs qgroup limit | Size limits on share folders | set and cleared by ShareHub |
| `/etc/krb5.conf`, `/etc/nsswitch.conf`, PAM | Only when joining AD (backup in `krb5.conf.sharehub.bak`) | on join/leave |

Registry parameters and shares that ShareHub does not own are left untouched. The Overview offers to import them.

### Samba details

- **VFS order:** `catia fruit streams_xattr acl_xattr shadow_copy2|snapper recycle`. Only the modules a share needs are included.
- **Recycle bin:** `recycle:touch_mtime = yes`, so the file's modification time records when it was deleted. `sharehub-recycle.timer` runs `sharehub-helper recycle-cleanup` daily and deletes files older than each share's retention.
- **Previous Versions on ZFS:** `shadow_copy2` with `shadow:snapdir = .zfs/snapshot`.
  - zfs-auto-snapshot: a `snapprefix`/`delimiter` pattern that matches all frequencies.
  - sanoid: the name carries a suffix after the timestamp, so a fixed `shadow:format` for one frequency is used. Samba cannot map a timestamp back to a name that has a suffix.
- **Previous Versions on Btrfs:** `vfs_snapper`.
- **Values starting with `-`:** passed to `net conf` after `--`.

### FTP details

- ProFTPD jails every user in `/srv/ftp-shares` (`DefaultRoot`).
- Each FTP-enabled share is a bind mount there, so files are never copied.
- Access: `<Limit ALL>` with `AllowUser`/`AllowGroup` taken from the share's access list, then `DenyAll`.
- Read-only: `<Limit WRITE SITE_CHMOD>` set to `DenyAll`.
- Hidden entries: `HideNoAccess` hides shares a user may not enter.
- Home folders: `/home` is bind-mounted as `/srv/ftp-shares/home`. Rules for `home/*` (deny) and `home/%u` (allow) limit each user to their own home. `DefaultChdir` puts users there at login.

## Helper commands

| Group | Commands |
|---|---|
| Read-only | `status`, `browse`, `quota-check`, `history`, `services`, `connections`, `firewall-status`, `ad-status`, `ad-preflight`, `migrate-detect` |
| Changes | `enable-registry`, `share-save`, `share-delete`, `settings-save`, `apply`, `history-restore`, `user-create`, `user-update`, `user-password`, `user-smb`, `group-create`, `group-update`, `group-delete`, `mkdir`, `ad-join`, `ad-leave`, `service-control`, `disconnect`, `firewall-open`, `migrate-import` |
| Plain text | `bootstrap` (package setup), `recycle-cleanup` (timer), `--version` |

Example, run as root:

```bash
echo '{}' | sudo /usr/lib/sharehub/sharehub-helper status | python3 -m json.tool
```

## Security model

- The page runs with the user's Cockpit session. Every privileged operation goes through `sudo` (Cockpit administrative access) to the helper.
- The helper validates every input against strict patterns and against NSS (users and groups must exist). Commands are run with argument lists, never through a shell.
- Passwords reach `chpasswd` and `smbpasswd` on stdin. The AD join password goes into a temporary file (mode 0600) passed to `net ads join -A` and deleted right after; it is never stored.
- `state.json` and the history are readable by root only.
- The frontend works under Cockpit's default Content Security Policy: no inline scripts or styles, and no network access.
