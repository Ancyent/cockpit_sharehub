# Changelog

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
