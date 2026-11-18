# Roadmap

Repository: <https://github.com/Ancyent/cockpit_sharehub> - license **GPL-3.0-or-later**.

## Goal

A Cockpit plugin that replaces the 45Drives *File Sharing* module and manages, from one place:

- shares with **SMB / NFS / FTP on the same folder**;
- local users and groups with a **synchronised password** (system + Samba), plus per-user home folders;
- **global Samba settings** with macOS and Windows profiles;
- **Time Machine**, **recycle bin**, **Previous Versions** (ZFS/Btrfs snapshots) and **scheduled read-only versions**;
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
| 7 | Per-share size limits (ZFS, XFS, ext4, Btrfs) with usage in the UI | **Done** (0.10.0); Btrfs tested, ZFS/XFS/ext4 pending field test |
| 8 | Components dashboard, compatibility report, containers (LXC) | **Done** (0.11.0-0.12.0); field-tested on Debian 13 LXC |
| 9 | Versioning: scheduled read-only versions, protected recycle bin, restore dialog | **Done** (0.13.0); Btrfs tested, ZFS pending field test |
| 10 | Feature choice (setup, Settings → Features, `install.sh --features`), greyed-out states, versions at scale | **Done** (0.14.0); recycle bin permission fix (0.14.1) |

## Towards 1.0

1. Run the field checklist in [TESTING.md](TESTING.md) on a VM with systemd: NFS mounts, Windows, macOS/Time Machine, Active Directory, reboot persistence.
2. Fix whatever the field tests find, then tag `v1.0.0`. CI attaches the `.deb` to the release.
3. Publish a GPG-signed apt repository (reprepro or aptly) so clients can `apt upgrade`.

## Later ideas

- Audit log per share (`vfs_full_audit`) with a viewer: who deleted or changed what.
- Recycle bin for FTP deletes (today only SMB deletes are kept).
- NFS with Kerberos (`sec=krb5p`) for AD-joined servers.
- Per-share audit log (`vfs_full_audit`) with a viewer.
- Translations (Cockpit `po` files).
- Multi-server view through Cockpit's host switcher.

## Decisions

| Topic | Decision |
|---|---|
| Name | ShareHub (page title in Cockpit: "File Sharing") |
| Target OS | Ubuntu 24.04 first; 22.04 and Debian 12 best effort |
| Snapshots | ShareHub schedules its own read-only versions (ZFS snapshots, Btrfs read-only subvolumes) and also exposes snapshots made by sanoid / zfs-auto-snapshot / snapper |
| Features | Everything on by default (upgrades change nothing); the admin turns features off; unavailable features are greyed out, not hidden |
| License | GPL-3.0-or-later; external contributions under the DCO (`Signed-off-by`) |
| 45Drives module | Replaced; ShareHub imports its shares and exports |
