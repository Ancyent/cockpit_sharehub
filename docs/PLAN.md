# Roadmap

Repository: <https://github.com/Ancyent/cockpit_sharehub> - license **GPL-3.0-or-later**.

## Goal

A Cockpit plugin that replaces the 45Drives *File Sharing* module and manages, from one place:

- shares with **SMB / NFS / FTP on the same folder**;
- local users and groups with a **synchronised password** (system + Samba), plus per-user home folders;
- **global Samba settings** with macOS and Windows profiles;
- **Time Machine**, **recycle bin**, **Previous Versions** (ZFS/Btrfs snapshots);
- **Active Directory** membership (winbind);
- delivery as a versioned **.deb** package, later through an apt repository.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | State model + reconciliation, backup/rollback, history, import from 45Drives and cockpit-smbftp | **Done** (0.9.0) |
| 1 | UI: Shares (SMB/NFS/FTP), Users & Groups, global Samba settings, home folders | **Done** (0.9.0) |
| 2 | macOS/Windows profiles, Time Machine, recycle bin with retention timer, Avahi + wsdd | **Done** (0.9.0); Mac/Windows field tests pending |
| 3 | Previous Versions on ZFS (zfs-auto-snapshot, sanoid) and Btrfs (snapper) | **Done** for ZFS; Btrfs pending field test |
| 4 | Active Directory: join wizard, domain users/groups in pickers, FTP via PAM winbind | **Done** (0.9.0); real-domain test pending |
| 5 | Overview: services, live connections, firewall | **Done** (0.9.0) |
| 6 | `.deb` package, CI, documentation | **Done**; signed apt repository pending |

## Towards 1.0

1. Run the field checklist in [TESTING.md](TESTING.md) on a VM with systemd: NFS mounts, Windows, macOS/Time Machine, Active Directory, reboot persistence.
2. Fix whatever the field tests find, then tag `v1.0.0`. CI attaches the `.deb` to the release.
3. Publish a GPG-signed apt repository (reprepro or aptly) so clients can `apt upgrade`.

## Later ideas

- Quotas per share (ZFS `quota` / XFS project quotas).
- Snapshot scheduling from the UI (sanoid / snapper configuration).
- NFS with Kerberos (`sec=krb5p`) for AD-joined servers.
- Per-share audit log (`vfs_full_audit`) with a viewer.
- Translations (Cockpit `po` files).
- Multi-server view through Cockpit's host switcher.

## Decisions

| Topic | Decision |
|---|---|
| Name | ShareHub (page title in Cockpit: "File Sharing") |
| Target OS | Ubuntu 24.04 first; 22.04 and Debian 12 best effort |
| Snapshots | Expose existing snapshots; scheduling is left to sanoid / zfs-auto-snapshot / snapper |
| License | GPL-3.0-or-later; external contributions under the DCO (`Signed-off-by`) |
| 45Drives module | Replaced; ShareHub imports its shares and exports |
