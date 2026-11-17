# Changelog

## 0.12.0 - 2026-11-17

First field test on Debian 13 (trixie) in a Proxmox LXC container.

- Containers (LXC, Docker) are detected. NFS is shown as **Not supported here** and cannot be enabled
  when the kernel NFS server is unavailable (`/proc/fs/nfsd` cannot be mounted in a container)
- WS-Discovery uses `wsdd-server` or `wsdd2`, whichever the distribution provides (Debian 13 has only
  `wsdd2`); LLMNR port 5355 is added to the firewall list when wsdd2 is used
- Components dashboard: when no package for a feature exists in the package sources it shows
  **Not available** with what to do, instead of an Install button that cannot work
- Missing discovery daemons are reported on the dashboard instead of a warning on every save
- **Compatibility report**: installing the package (or running `sharehub-helper check`) prints, per feature,
  whether it can be used on this system, can be used after installing packages, or is not possible here
- Components dashboard: a **This system** summary (distribution, container/VM, Samba, kernel, "N of M features
  can be used") and a plain verdict per feature: *Can be used*, *Needs install*, *Needs attention*, *Not possible here*
- **Finder icon picker** with previews: choose how Macs show the server (file server, 1U rack server, server rack,
  backup appliance, tower, desktop), with a description of where the icon appears

## 0.11.0 - 2026-11-17

- **Components** dashboard on the Overview page (replaces the Services card): for each feature
  (SMB, NetBIOS, NFS, FTP, FTPS, Bonjour, WS-Discovery, Active Directory, Previous Versions,
  size limits, recycle-bin cleanup) it shows the required packages with installed and available
  versions, the services, configuration checks, and whether the feature is in use
- A feature that is in use but missing a package, has a stopped service or a failing check is
  marked **Needs attention** and listed first
- Actions: install missing packages (allow-listed, via apt), start or restart services, and fix
  known configuration problems (Samba registry, ProFTPD include and mod_tls, cleanup timer)

## 0.10.0 - 2026-11-17

- Per-share size limits, enforced by the filesystem:
  - ZFS: `refquota` on the dataset mounted at the share path
  - XFS: project quota (`prjquota` mount option)
  - ext4: project quota (`project` feature + `prjquota` mount option)
  - Btrfs: qgroup limit on the subvolume that is the share path
- The share editor checks the folder and explains what is needed when limits are not available
- Shares list shows usage against the limit

## 0.9.0 - 2026-11-17

First release candidate.

- One share, three protocols: SMB, NFS and FTP/FTPS on the same folder with one access list
- Local users and groups; one password for system and Samba; Samba account enable/disable
- Per-user home folders over SMB and FTP
- Global SMB settings: identity, minimum protocol, encryption, signing, multichannel, NetBIOS
- macOS: vfs_fruit tuning, Time Machine destinations with size limits, Bonjour announcements
- Windows: Previous Versions from ZFS (sanoid, zfs-auto-snapshot) and Btrfs (snapper), Windows ACLs, WS-Discovery
- Recycle bin with per-share retention and a daily cleanup timer
- NFS: per-client options, NFSv3/v4 selection, fixed NFSv3 ports for firewalls
- FTP: chroot jail with per-share bind mounts, FTPS, passive port range
- Active Directory join and leave with prerequisite checks (winbind, rid/ad ID mapping)
- Overview: services, live SMB/NFS/FTP sessions with disconnect, firewall port helper
- Validation before every change, automatic rollback, change history with restore
- Import from 45Drives File Sharing, /etc/exports and the cockpit-smbftp prototype
- Debian package, install script from source, CI with .deb build
