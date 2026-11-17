# cockpit_sharehub

A [Cockpit](https://cockpit-project.org/) plugin for managing file sharing on Linux servers from a single place: **SMB, NFS and FTP on the same folder**, local users whose system and Samba passwords stay in sync, per-user home folders, and (on the roadmap) macOS/Time Machine, Windows Previous Versions, recycle bin and Active Directory.

> **Status: early development (v0.2).** SMB and FTP shares, home folders and the user/password backend work and have been tested against real Samba and ProFTPD. NFS, the new UI and the rest of the roadmap are in progress. See [docs/PLAN.md](docs/PLAN.md) for the plan (written in Romanian).

## What works today (v0.2)

- **SMB shares** stored in the Samba registry (`net conf`): path, description, allowed users and groups, read-only, guest access, visibility in network browsing.
- **"Also publish over FTP"** per share, served by ProFTPD:
  - every user is jailed in `/srv/ftp-shares`, where each share is a bind mount;
  - FTP access follows the same users and groups as Samba;
  - a share can be read-only on FTP only;
  - FTPS (TLS) can be required, which refuses plain-FTP logins.
- **Per-user home folders:** `[homes]` on SMB and `/home/<user>` on FTP. Each user sees only their own home.
- **Users** (backend only, the UI is not done yet):
  - create and edit users;
  - set one password for both the system (FTP/SSH) and Samba;
  - enable or disable the Samba account.
- **Safe changes:** every config change is validated (`testparm`, `proftpd -t`) and rolled back if validation fails.

Tested on Ubuntu 24.04 (Samba 4.19, ProFTPD 1.3.8b).

## Install (development build)

```bash
git clone https://github.com/Ancyent/cockpit_sharehub.git
cd cockpit_sharehub
sudo ./install.sh --enable-registry
```

Then open `https://<server>:9090` and choose **SMB + FTP Shares** (administrative access must be turned on).

Firewall: open 445/tcp for SMB, 21/tcp plus the passive port range for FTP.

```bash
sudo ./uninstall.sh     # removes the plugin and FTP mounts; Samba shares are kept
```

## How it works

| Piece | Where |
|---|---|
| Samba shares | Samba registry (`include = registry` in `[global]`) |
| FTP state | `/etc/cockpit-smbftp/state.json` |
| FTP folders | bind mounts in `/srv/ftp-shares`, kept in `/etc/fstab` (option `x-cockpit-smbftp`) |
| ProFTPD config | `/etc/proftpd/conf.d/cockpit-smbftp.conf` (generated) |
| Privileged work | `smbftp/helper.py`, called through `cockpit.spawn` with superuser rights; JSON on stdin and stdout |

## Roadmap

| Phase | Scope |
|---|---|
| 0 | Single state model + reconcile engine, backup/undo, migration from 45Drives *File Sharing* |
| 1 | New UI: Shares (SMB/NFS/FTP), Users & Groups, global Samba settings, home folders |
| 2 | macOS profile (vfs_fruit), Time Machine, recycle bin, discovery (Avahi + wsdd) |
| 3 | Previous Versions from ZFS/Btrfs snapshots |
| 4 | Active Directory join (winbind) |
| 5 | Overview: services, live connections, firewall |
| 6 | `.deb` packaging, CI, apt repository |

## License

Copyright (C) 2026 cockpit_sharehub contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).
