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
                                                                     ├─ snapshots.py  scheduled versions, retention, paged browse, search, history
                                                                     ├─ jobs.py       background folder restores with progress and cancel
                                                                     ├─ features.py   feature switches and the per-feature UI state
                                                                     ├─ users.py      users, groups, Samba passdb
                                                                     ├─ ad.py         Active Directory (winbind)
                                                                     ├─ services.py   systemd, sessions, firewall, discovery
                                                                     ├─ health.py     components dashboard, package install, fixes
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
| ZFS snapshots `<dataset>@sharehub_<UTC time>` / Btrfs read-only subvolumes `<share>/.sharehub-snapshots/sharehub_<UTC time>` | Scheduled versions | created and pruned by `sharehub-snapshot.timer` |
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

### Versioning details

- Snapshot names are `sharehub_%Y.%m.%d-%H.%M.%S` in UTC. Samba's `shadow_copy2` uses the same format, so every version appears in Windows (no per-frequency suffix problem).
- **Retention** (`snapshots.keep_set`) keeps the newest snapshot, the newest N quarter-hours, and the newest snapshot of each of the last N hours, days, ISO weeks and months. One snapshot can count for several classes.
- **Btrfs.** Snapshots live in `<share>/.sharehub-snapshots`, vetoed in Samba and denied in ProFTPD. They are read-only subvolumes, so even root gets "Read-only file system".
- **ZFS.** `<dataset>/.zfs/snapshot` is read-only by design.
- **Restore** copies with `cp -a --reflink=auto`, so on Btrfs and ZFS block cloning is used where available.
- **Recycle bin protection** sets `veto files = /.recycle/` (plus the snapshot folder on Btrfs) and a `<Directory …/.recycle>` deny rule in ProFTPD. Extra `Copy #N of …` versions are trimmed by the daily cleanup.

### FTP details

- ProFTPD jails every user in `/srv/ftp-shares` (`DefaultRoot`).
- Each FTP-enabled share is a bind mount there, so files are never copied.
- Access: `<Limit ALL>` with `AllowUser`/`AllowGroup` taken from the share's access list, then `DenyAll`.
- Read-only: `<Limit WRITE SITE_CHMOD>` set to `DenyAll`.
- Hidden entries: `HideNoAccess` hides shares a user may not enter.
- Home folders: `/home` is bind-mounted as `/srv/ftp-shares/home`. Rules for `home/*` (deny) and `home/%u` (allow) limit each user to their own home. `DefaultChdir` puts users there at login.

## Features

`features.py` gives every screen the same answer per feature. `health.report(state, light=True)` (no service
states, no configuration checks) runs inside `status`, so each page gets `status.features[id] = {state, reason,
install}` with `state` one of `off`, `impossible`, `needs_install`, `needs_setup`, `ready`.

- Admin switches live in `state.json` → `features` (`nfs`, `ftp`, `ad`, `recycle`, `quota`, `snapshots`,
  `versions`, plus `configured`); mDNS, WS-Discovery and NetBIOS reuse the existing `samba.mdns/wsdd/netbios`.
  Everything defaults to on, so upgrades change nothing.
- `apply_choice` refuses to turn off a feature a share still uses; `check_share` refuses a share that uses a
  feature that is off; `sync_units` stops/starts `nfs-server`, `proftpd` and the two ShareHub timers.
- Size limits and scheduled versions are evaluated with `quota.detect` / `snapshots.detect` on every share folder
  (`health.fs_evaluation`), grouped by filesystem. Each `detect` returns `kind` (`ready`, `needs_install` with the
  package, `needs_setup` with the steps, `impossible`). In a container, ext4/XFS limits and ZFS without `/dev/zfs`
  are `impossible` with the host-side alternative; outside a container "no folder supports it" is `needs_setup`,
  because a share on suitable storage would work.

## Versions at scale

- `versions-browse` lists one folder: `scandir`, filter by name, sort folders first, then `lstat` only the
  requested page (500). It stops after 10 s and returns what it has (`truncated`). The recycle bin uses the same
  call without a snapshot.
- `versions-search` walks below the current folder only when asked, up to 200 matches or 15 s.
- `versions-history` does one `lstat` per snapshot and keeps the versions where size or mtime changed.
- Folder restores (`versions-restore` on a folder) start `jobs.start`: a transient unit
  `systemd-run --unit sharehub-job-<id> -p Nice=10 -p IOSchedulingClass=idle sharehub-helper job-run <id>`
  (or `nice`/`ionice` without systemd). The worker counts the files, then copies with `copy_file_range`
  (block cloning on Btrfs/XFS/ZFS) keeping owner, mode, times, xattrs and ACLs, writes progress to
  `/run/sharehub/jobs/<id>.json` every second and stops when `<id>.cancel` appears. Finished jobs are forgotten
  after an hour.

## Helper commands

| Group | Commands |
|---|---|
| Read-only | `status`, `browse`, `quota-check`, `health`, `versions-check`, `versions-list`, `versions-browse`, `versions-search`, `versions-history`, `jobs`, `history`, `services`, `connections`, `firewall-status`, `ad-status`, `ad-preflight`, `migrate-detect` |
| Changes | `features-set`, `versions-create`, `versions-restore`, `job-cancel`, `install-packages`, `health-fix`, `enable-registry`, `share-save`, `share-delete`, `settings-save`, `apply`, `history-restore`, `user-create`, `user-update`, `user-password`, `user-smb`, `group-create`, `group-update`, `group-delete`, `mkdir`, `ad-join`, `ad-leave`, `service-control`, `disconnect`, `firewall-open`, `migrate-import` |
| Plain text | `bootstrap` (package setup), `recycle-cleanup` (daily timer), `snapshot-run` (15-minute timer), `check` (compatibility report), `features-init <list>` (install.sh), `job-run <id>` (background job worker), `--version` |

Example, run as root:

```bash
echo '{}' | sudo /usr/lib/sharehub/sharehub-helper status | python3 -m json.tool
```

## Security model

- The page runs with the user's Cockpit session. Every privileged operation goes through `sudo` (Cockpit administrative access) to the helper.
- The helper validates every input against strict patterns and against NSS (users and groups must exist). Commands are run with argument lists, never through a shell.
- Passwords reach `chpasswd` and `smbpasswd` on stdin. The AD join password goes into a temporary file (mode 0600) passed to `net ads join -A` and deleted right after; it is never stored.
- `install-packages` only accepts packages from an allow-list (`health.INSTALLABLE`) and runs `apt-get install` non-interactively, keeping existing configuration files (`--force-confold`).
- `state.json` and the history are readable by root only.
- The frontend works under Cockpit's default Content Security Policy: no inline scripts or styles, and no network access.
