# Testing

## Automated

```bash
sudo apt install python3-pytest python3-pyflakes nodejs
make lint    # pyflakes + JavaScript syntax + shell syntax
make test    # 57 unit tests: validation, Samba/NFS/FTP rendering, import parsing
```

CI (`.github/workflows/ci.yml`) runs both on every push and builds the `.deb`. On a `v*` tag the package is attached to the GitHub release.

## Verified for 0.9.0 and 0.10.0

Environment: Ubuntu 24.04, Samba 4.19.5, ProFTPD 1.3.8b, nfs-utils 2.6.4, Cockpit 314. Systemd was not running in this environment (see "Not yet verified").

| Area | How it was verified | Result |
|---|---|---|
| Share create / edit / rename / delete | helper + Cockpit UI; `net conf` checked after each step | OK |
| SMB access control | `smbclient` as allowed and not-allowed users | Allowed users connect; others get `NT_STATUS_ACCESS_DENIED` |
| SMB encryption | `smbstatus --json` with encryption *desired* | Session uses AES-128-GCM |
| Recycle bin | delete over SMB, then `recycle-cleanup` with an aged file | File lands in `.recycle/<user>/`; expired files are removed |
| Previous Versions, zfs-auto-snapshot naming | snapshot tree + `smbclient allinfo` and `get @GMT-…/file` | Versions listed and readable |
| Previous Versions, sanoid naming | same | Versions listed and readable (one frequency per share) |
| Time Machine parameters | `net conf showshare` | `fruit:time machine` + size limit set; `testparm` clean |
| Home folders SMB | `smbclient //server/<user>` | Own home OK; another user's home `ACCESS_DENIED` |
| Users and passwords | create user, change password | New password works on SMB and FTP; old one fails on both |
| Samba account disable/enable | `smbclient` | `NT_STATUS_ACCOUNT_DISABLED`, then works again |
| FTP jail and per-share access | Python `ftplib` | Users see only allowed shares; `cd ..` stays in the jail; read-only share refuses `STOR` |
| FTP home folders | `ftplib` | Login lands in `/home/<user>`; other homes hidden and not readable, even when world-readable |
| FTPS required | `ftplib` with and without TLS | Plain login refused (`550 SSL/TLS required`); FTPS works |
| ProFTPD validation and rollback | enable TLS without `mod_tls` | Change refused with a clear message; previous config kept |
| NFS exports | `exportfs -v` after apply | Options rendered as expected, including quoted paths and `all_squash` with anon IDs |
| Validation | invalid names, paths, NFS hosts, sizes, ports | Rejected with clear messages |
| Components dashboard (0.11.0) | removed `wsdd-server` while WS-Discovery is on; installed it from the dashboard in Cockpit | Row flagged "Needs attention" with the missing package; Install completed and the row turned "Ready"; non-allow-listed packages refused |
| Size limit on Btrfs (0.10.0) | loop-mounted Btrfs, subvolume share, 30M limit, 45M upload over SMB | Upload refused at the limit; usage shown; raising and removing the limit work; folder (non-subvolume) refused with an explanation |
| Size limit commands for ZFS, XFS, ext4 | unit tests on generated commands and on parsing `xfs_quota` / `repquota` / `btrfs qgroup` output | OK |
| History restore | restore an earlier entry | Shares and settings back as before |
| AD join failure path | join an unreachable domain | Error shown; registry and `krb5.conf` restored |
| Import | 45Drives-style registry shares + exports file + cockpit-smbftp state | Shares, NFS clients and FTP settings imported; old files retired |
| Package | `dpkg -i`, `dpkg -P`, reinstall | Installs and bootstraps; purge removes generated files; re-import restores shares from the registry |
| UI | Cockpit 314 with Chromium: every tab, editor tabs, folder browser with folder creation, pickers, validation errors, light and dark theme | OK |

## Field test: Debian 13 in a Proxmox LXC container (0.11.0)

| Item | Result |
|---|---|
| `apt install ./cockpit-sharehub_0.11.0_all.deb` | Installs with all dependencies; bootstrap enables mod_tls |
| smbd, nmbd, proftpd, avahi-daemon, winbind | Running under systemd |
| NFS server | Cannot start: `/proc/fs/nfsd` mount denied in LXC - handled in 0.12.0 (NFS marked unsupported) |
| WS-Discovery | No `wsdd`/`wsdd-server` on Debian 13 - handled in 0.12.0 (`wsdd2` supported) |
| `samba-ad-dc` | Pulled in by Samba's recommends; inactive; documented |

## Not yet verified (field checklist)

These need real infrastructure. Run them before calling a deployment production-ready.

### Systemd / services
- [ ] After installing the package: `systemctl status smbd nfs-server proftpd sharehub-recycle.timer`
- [ ] The Overview service card shows the correct state; Start, Stop and Restart work
- [ ] Reboot: FTP bind mounts come back (`findmnt | grep ftp-shares`) and the shares are reachable

### NFS (needs a kernel NFS server)
- [ ] NFSv4 mount from a Linux client: `mount -t nfs server:/path /mnt`
- [ ] Read-only share rejects writes; `root_squash` maps root to nobody
- [ ] NFSv3 mount through the firewall with only the listed ports open
- [ ] Proxmox / VMware datastore mount (if used)

### Windows client
- [ ] Server appears under *Network* (WS-Discovery)
- [ ] Map a drive, copy files, delete a file → it appears in `.recycle`
- [ ] Previous Versions tab shows snapshots and restores a file (ZFS)
- [ ] Windows ACLs: set permissions from the Security tab (share with Windows ACLs on)
- [ ] Encryption *required* + Windows 10/11 connect; an older client is refused

### macOS client
- [ ] Server appears in the Finder sidebar (Bonjour)
- [ ] Copy a folder with resource forks and tags; no `._` files appear on the server
- [ ] Time Machine: the share is offered as a destination; a first backup completes; the size limit is respected

### Active Directory (needs a test domain)
- [ ] **Check prerequisites** is all green
- [ ] Join with `rid`; `wbinfo -u`, `getent passwd <user>`, **Computer account: Valid**
- [ ] Add a domain group to a share; a domain user connects over SMB and FTP
- [ ] Home directory is created on first login
- [ ] Leave the domain with credentials; the computer account is removed from AD

### Components dashboard (needs systemd)
- [ ] Stop `nfs-server` while an NFS share exists: NFS row shows "Needs attention"; **Start** brings it back
- [ ] Comment out the ProFTPD conf.d include: FTP row shows the failing check; **Fix** restores it
- [ ] Install a missing optional component (e.g. sanoid) from the dashboard

### Size limits (needs kernels with quota support)
- [ ] ZFS: share on a dataset, set 10G, fill it over SMB → "disk full"; Windows shows a 10 GB drive
- [ ] XFS mounted with `prjquota`: same test; `xfs_quota -x -c 'report -p'` shows the project
- [ ] ext4 with `project` feature + `prjquota`: same test; `repquota -P` shows the project

### Btrfs (needs a Btrfs volume and snapper)
- [ ] Share on a snapper-managed subvolume shows Previous Versions in Windows

### Ubuntu 22.04 / Debian 12
- [ ] Package installs; the UI loads; share create and delete over SMB/NFS/FTP work
