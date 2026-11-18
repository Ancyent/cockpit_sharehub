# Changelog

## 0.15.0 - 2026-11-18

- **Web file manager (optional): Cockpit Files**, the Cockpit project's own file manager. For administrators only:
  it is reachable only after logging in to Cockpit, adds no port, service, account or public link, and works with the
  rights of the Cockpit session (everything with *administrative access*). Share users cannot use it: their accounts
  have no login shell, and Cockpit refuses them
- Off by default. Turn it on in the setup, under **Settings → Features** (new entry *Web file manager*) or with
  `install.sh --features ...,files`. Debian 13 and Ubuntu 24.04 ship `cockpit-files` only in `<codename>-backports`:
  ShareHub follows Cockpit's install guide (adds the Debian backports line when it is missing, installs with
  `-t <codename>-backports`), shows beforehand exactly what apt will install or update, and takes nothing else from
  backports. On Ubuntu 24.04 this also updates Cockpit itself (Cockpit Files needs Cockpit 318 or newer)
- The installation runs as a background job, so it finishes even when apt updates Cockpit and the page reconnects;
  afterwards ShareHub offers to reload Cockpit so **File browser** appears in the menu
- **Files** in the shares list and **Open in Files** in the versions dialog (current folder, read-only for versions)
  and in the folder picker open the folder in Cockpit Files
- Components dashboard: new *Web file manager (Cockpit Files)* row

## 0.14.1 - 2026-11-18

- **Fixed: deleted files lost for every user but one.** Samba's recycle module created `<share>/.recycle`
  as the first user who deleted something, with mode `0700`. Other users could not create their own
  `.recycle/<user>` folder, and Samba then deleted their files for good. ShareHub now creates `.recycle`
  itself, owned by root with mode `1733` (protected recycle bin: users cannot list it) or `1777` (visible
  recycle bin), and repairs existing shares on the next save, on **Re-apply current configuration** or with
  **Fix** in the Components dashboard. Files lost before the fix cannot be recovered from the recycle bin
- Components dashboard: the Recycle bin row checks that every user can use the recycle bin
- Fixed: `recycle:exclude_dir` used commas instead of `|`, so the `tmp` and `temp` folders were not excluded

## 0.14.0 - 2026-11-18

- **Choose your features.** A first-run setup (on a fresh install, when no share exists yet) lets you pick NFS,
  FTP/FTPS, macOS and Windows discovery, NetBIOS, Active Directory, recycle bin, size limits, scheduled versions
  and external Previous Versions, and installs only the packages they need. Change it any time under
  **Settings → Features** ("Run setup again" reopens the setup). `install.sh --features smb,ftp,...` does the same
  from the command line; without arguments it asks in the terminal
- A feature that is **off** disappears from every screen (tabs, toggles, fields, the Active Directory page). It
  cannot be turned off while a share uses it; ShareHub lists those shares. Turning NFS, FTP, the recycle bin or
  scheduled versions off stops and disables their service or timer; turning it on starts it again
- A feature that **cannot be used** is greyed out everywhere instead of failing on save: the share editor
  (protocol toggles, Size limit, NFS/FTP/Versions tabs, Previous Versions), Settings (NFS, FTP, discovery) - with
  the reason, a **Details** link to its row in the Components dashboard and, when packages would make it work,
  an **Install** button that enables the controls without reloading the page. Values of greyed controls are kept
- **Size limits and scheduled versions are checked on the real share folders**, grouped by filesystem, in the
  dashboard and in the editor alike, so both always agree. Inside a container (LXC) ext4/XFS limits and ZFS
  snapshots are reported as *Not possible here* with the host-side alternative (a Proxmox mount point with a size);
  outside a container the dashboard says which filesystem setup is missing (new verdict *Needs setup*) instead of
  offering packages that would not help
- **Versions and deleted files at scale.** Both tabs browse one folder at a time, 500 entries per page with
  loading on scroll, a filter for the current folder and an explicit **Search subfolders** (stops after 15 seconds
  or 200 matches). Opening a view never reads the whole tree. A folder with 60 000 files lists in 0.1 s
- **History** of a file: every version in which it changed, with Restore
- **Folder restores run in the background** at low CPU and I/O priority, with progress (files and bytes) and
  Cancel, in the Versions dialog and on the Overview. Deleted folders can be restored in one step
- Folder picker: after **New folder…** the new folder is selected and labelled "Folder created", and the label
  moves to the Folder field it fills (just a highlight when the system asks for reduced motion)
- Fixed: setting a password, editing or enabling Samba for users created outside ShareHub whose names contain
  a dot or upper-case letters (e.g. `Andrei.P`)

## 0.13.0 - 2026-11-17

- **Scheduled versions** per share (Edit → Versions): read-only snapshots every 15 minutes or every hour,
  with separate retention for 15-minute, hourly, daily, weekly and monthly versions. ZFS datasets use ZFS
  snapshots; Btrfs subvolumes use read-only snapshots in a hidden `.sharehub-snapshots` folder. Versions
  appear in Windows under *Previous Versions*, cannot be changed from SMB, NFS or FTP, and are hidden from
  SMB and FTP browsing. A new `sharehub-snapshot.timer` takes and prunes them every 15 minutes
- **Protected recycle bin**: deleted files can be hidden from clients (users and ransomware cannot see,
  change or purge them), with a limit on how many versions of the same file are kept
- **Versions dialog** (Shares → Versions): pick a version, browse its folders and restore files or folders,
  or restore deleted files. An existing file is never overwritten; the restored copy gets the version's date
- Components dashboard: new "Scheduled versions" row with a check that a recent version exists

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
