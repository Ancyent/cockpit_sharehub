# ShareHub user guide

ShareHub has five tabs: **Overview**, **Shares**, **Users & Groups**, **Settings** and **Active Directory**. Every change is applied right away and validated first. If something cannot be applied, you see the error and nothing is changed.

## Concepts

- **Share.** A folder on the server, plus how it is published: over SMB, NFS and/or FTP.
- **Access list.** The users and groups allowed on a share. It applies to **SMB and FTP**. An empty list means "any user who can log in". **NFS ignores it**: NFS trusts the client machine and maps users by UID/GID, so NFS access is controlled by the share's client list and by folder permissions.
- **Folder permissions still apply.** ShareHub decides who may connect. The Linux permissions on the folder decide what they may do once connected. Use a shared group and permissions such as `2770` (see *Creating a folder*).

---

## Overview

- **Setup alerts.** These cover the Samba registry, shares found from other tools, and ProFTPD configuration problems.
- **Counters.** Number of shares per protocol, local users, Active Directory status, and versions.
- **This system.** Distribution, whether it runs in a container or VM, Samba and kernel versions, and how many features can be used here.
- **Components.** One row per feature: SMB, NetBIOS, NFS, FTP, FTPS, macOS discovery, Windows discovery, Active Directory, Previous Versions, size limits and recycle-bin cleanup.

  | Verdict | Meaning |
  |---|---|
  | Can be used | Everything it needs is installed (and running, when in use). |
  | Needs install | Can be used after installing the listed packages - click **Install**. |
  | Needs attention | In use, but a service is stopped or a check fails. These rows are listed first. |
  | Not possible here | The system cannot run it, e.g. the NFS server in a container or a package that the distribution does not provide. The row explains why and what to do. |

  **Details** shows packages (installed and available versions), services (with Start, Restart and Stop) and configuration checks. **Install** runs `apt-get install` for the missing packages (only packages ShareHub knows are allowed). **Fix** repairs known problems: enables the Samba registry, adds the ProFTPD include and mod_tls, or starts the cleanup timer.
- **Firewall.** The ports your protocols need. Opens them in ufw or firewalld when one is active.
- **Live connections.** SMB sessions (user, client, protocol, encryption, shares in use) with **Disconnect**, NFSv4 clients, and FTP sessions. The view refreshes every 15 seconds.

---

## Shares

Click **Add share**, or **Edit** on an existing share. The editor has five tabs.

### General

- **Share name.** Used as the SMB share name (`\\server\name`) and the FTP folder name (`/name`). Allowed characters: letters, digits, `.`, `_` and `-`. Renaming a share is supported.
- **Folder.** Type a path or click **Browse…**. The browser can create a folder with an owner, a group and permissions:

  | Mode | Meaning |
  |---|---|
  | `2770` (recommended) | Owner and group have full access, others have none. New files inherit the group. |
  | `2775` | The group has full access, others can read. |
  | `0700` | Owner only. |

- **Size limit.** An optional limit such as `500G` or `2T`. It is enforced by the filesystem, for every protocol. The editor checks the folder as soon as you choose it and tells you if a limit is possible:

  | Filesystem | Requirement | What clients see |
  |---|---|---|
  | ZFS | The share folder is a dataset (`zfs create pool/projects`); ShareHub sets `refquota` (snapshots do not count). | Size = limit |
  | XFS | The filesystem is mounted with `prjquota` (add it in `/etc/fstab`, then remount). ShareHub assigns a project ID to the folder. | Size = limit |
  | ext4 | The filesystem has the `project` feature (`tune2fs -O project,quota`, unmounted) and is mounted with `prjquota`; install the `quota` package. | Size = limit |
  | Btrfs | The share folder is a subvolume (`btrfs subvolume create`). ShareHub turns on quotas for the filesystem and sets a qgroup limit. | Whole volume (the limit is still enforced) |

  The Shares list shows used space against the limit. When a share is full, writes fail with "disk full" on every protocol.

  > On Btrfs, turning on quotas has a cost on volumes with many snapshots. On ZFS, use a dataset per share if you want limits.

- **Publish over.** SMB, NFS and FTP can each be switched on or off. At least one must be on.

### Access

- **Allowed users / Allowed groups.** Searchable lists of local users and groups, plus domain users and groups when joined to Active Directory.
- **Read only.** Applies to every protocol.

### SMB

| Option | What it does | Recommendation |
|---|---|---|
| Guest access | Anyone can connect without a password. | Only for public, non-sensitive data. |
| Visible when browsing | Shown in network browsers; hidden shares stay reachable by name. | On. |
| Time Machine backup destination | Macs can use this share for backups; optional size limit (e.g. `1T`). | Use a dedicated share, one per group of Macs, with a limit. |
| Recycle bin | Deleted files go to `.recycle/<user>/` inside the share, keeping the folder tree. **Versions kept per file** limits how many copies of the same file are kept (`Copy #N of …`). **Protect deleted files from users** hides the recycle bin from SMB and FTP, so users and ransomware cannot see or purge it; restore through *Shares → Versions*. A daily timer removes files older than the retention (`0` = keep). | On, protected, for shared team folders. |
| Previous Versions | Exposes ZFS or Btrfs snapshots in Windows under *Properties → Previous Versions*. | See below. |
| Windows ACLs | Lets Windows clients set detailed permissions (stored as extended attributes). | Only if Windows admins need to manage permissions from Windows. |

#### Previous Versions (snapshots)

ShareHub **exposes** existing snapshots. It does not create them. Set up one of these tools on the server:

**ZFS with sanoid** (`apt install sanoid`). Configure `/etc/sanoid/sanoid.conf` for the dataset. Samba can expose one sanoid frequency per share, so ShareHub picks the most frequent one it finds: hourly, then daily, and so on.

**ZFS with zfs-auto-snapshot** (`apt install zfs-auto-snapshot`). All frequencies are shown.

**Btrfs with snapper** (`apt install snapper`, then `snapper -c <name> create-config <subvolume>`). Not yet field-tested; see TESTING.md.

On other filesystems (ext4, XFS) the option is ignored and you get a warning.

### NFS

Add one line per client:

- **Client.** An IP (`192.168.1.20`), a subnet (`10.0.0.0/24`), a hostname, a wildcard (`*.lan`), a netgroup (`@trusted`) or `*` (any host).
- **Read/write.** Forced to read-only when the share is read only.
- **Synchronous writes.** Safer; switch off only for scratch data.
- **User mapping:**

  | Mode | Meaning |
  |---|---|
  | `root_squash` (default) | Remote root acts as `nobody`. |
  | `no_root_squash` | Trust remote root. Only for servers you control, such as hypervisors. |
  | `all_squash` | Every remote user acts as the anonymous UID/GID you enter. |

Client paths: NFSv4 clients mount `server:/path/to/folder`, for example:

```bash
mount -t nfs server:/srv/shares/projects /mnt/projects
```

### Versions

Scheduled, read-only versions of the whole share. This needs a ZFS dataset or a Btrfs subvolume (the tab tells you whether it is possible for the chosen folder).

- **Keep scheduled versions.** A snapshot every hour, or **every 15 minutes**. Snapshots only store what changed, so they are cheap.
- **How many versions to keep.** Set it per class: 15-minute, hourly, daily, weekly and monthly. Older versions are removed automatically. Example: 8 / 24 / 14 / 8 / 6 means the last 2 hours every 15 minutes, the last day hourly, two weeks daily, two months weekly and half a year monthly.
- **Immutable for users.** Versions cannot be modified or deleted from SMB, NFS or FTP, so ransomware running on a client cannot destroy them. Windows users see them under *Properties → Previous Versions*; the snapshot folder itself is hidden.
- **ext4 / XFS.** Scheduled versions are not possible. Use the recycle bin for deleted files.

### Restoring files (Shares → Versions)

- **Versions tab.** Pick a version (newest first), browse its folders and click **Restore** on a file or folder. It is copied back to its original place. If a file with that name exists, it is kept, and the restored copy is named `name (restored YYYY-MM-DD HH.MM).ext`. **Create a version now** takes one immediately, for example before a risky change.
- **Deleted files tab.** Every file deleted over SMB while the recycle bin is on, with who deleted it and when. **Restore** puts it back where it was.

### FTP

- **Read only over FTP.** Read-only over FTP even when SMB is writable.
- **Where the share appears.** FTP users log in with their normal password and land in a jail (`/srv/ftp-shares`). They only see the shares they are allowed on; each share is a bind mount of its folder.

---

## Users & Groups

- **Add user.** Creates a Linux account with a non-login shell (unless you allow SSH) and, by default, a Samba account with the same password.
- **Password.** Sets the password for the system account (FTP, SSH) and the Samba account together.
- **Disable Samba / Enable Samba.** Blocks or allows SMB for a user without touching the system account.
- **Badges:**

  | Badge | Meaning |
  |---|---|
  | Samba: active | The user can use SMB. |
  | Samba: disabled | The Samba account exists but is blocked. |
  | No Samba password | The user cannot use SMB until a password is set here. This is highlighted when the user is in a share's access list. |

- **Groups.** Create groups, edit members, delete unused groups. A group used by a share cannot be deleted until you remove it from the share.
- **Deleting users.** Use Cockpit's **Accounts** page.

> **Password changes made outside ShareHub.** If a password is changed in Cockpit's *Accounts* page or with `passwd`, only the system password changes. Set it again in ShareHub so SMB matches. Changes made from Windows (Ctrl+Alt+Del → *Change a password*) update both, because Ubuntu's Samba has `unix password sync` on.

---

## Settings

### SMB

- **Identity.** Workgroup, NetBIOS name, server description. **NetBIOS** is only needed for very old clients; with it off, Windows finds the server through WS-Discovery.
- **Security:**

  | Setting | Choices |
  |---|---|
  | Minimum protocol | SMB 2.1 (Windows 7+), 3.0 (Windows 8+) or 3.1.1 (Windows 10+/macOS 10.12+). SMB1 is always off. |
  | Encryption | Off unless the client asks; *desired* (recommended); or *required*, which refuses old clients. |
  | Signing | Samba default, offered or mandatory. |

- **Performance.** Multichannel.
- **Apple macOS** (see also *How Macs show this server* below):
  - `vfs_fruit` optimisations (recommended when Macs use the server);
  - the Finder icon;
  - Bonjour announcements, which are also needed for Time Machine discovery.
- **Windows.** WS-Discovery announcements (needs `wsdd-server`).
- **Home folders.** `\\server\<username>` for every user, optionally listed when browsing.

#### How Macs show this server

Pick the device type Finder uses for this server: file server (generic, default), 1U rack server, server rack, backup appliance (good for a Time Machine server), tower, or desktop. The picture next to each choice is a simplified preview; on the Mac, Finder draws Apple's own icon for that device type in the sidebar (Locations / Network) and in *Go → Connect to Server*. It needs "Announce over mDNS / Bonjour". Macs cache these icons, so a change may need a reconnect or a Finder restart. Windows and Linux are not affected.

### NFS

- **Versions.** NFSv4 is recommended. NFSv3 is for legacy clients; ShareHub fixes its helper ports (20048, 32765-32768) so it works through a firewall.
- **Server threads.** Default 8.

### FTP

- **Require FTPS.** Strongly recommended. The default certificate is self-signed. Put your own in `/etc/ssl/` and enter its paths here.
- **Passive port range.** Needed behind a firewall or NAT. Open the same range there.
- **Home folders over FTP.** Users land in `/home/<username>` and cannot see other homes.

### History

- **Restore.** Every change is saved. **Restore** brings back an earlier configuration of shares and settings; users, groups and files are not affected.
- **Re-apply current configuration.** Rewrites every generated file, for example after an upgrade or a manual edit.

---

## Active Directory

### Before you join

- The server's DNS must point to the domain controllers.
- The clock must be synchronised (Kerberos allows at most 5 minutes of difference).
- The AD packages must be installed. **Check prerequisites** verifies all of this.

### Joining

1. Enter the domain (e.g. `corp.example.com`) and, if it differs from the first label, the NetBIOS name (e.g. `CORP`).
2. Choose **ID mapping**:
   - **`rid`** (recommended) computes Linux IDs from Windows SIDs. AD needs no changes.
   - **`ad`** uses the `uidNumber`/`gidNumber` attributes stored in AD.

   Use the same choice and range on every server that shares the same files over NFS.
3. **Use short names** lets users log in as `maria` instead of `CORP\maria`.
4. Enter an account that may join computers. The password is used once and not stored.

### After joining

- Domain users and groups appear in every share's access lists.
- SMB and FTP authenticate against AD.
- Home directories are created on first login.

### Leaving

**Leave domain** removes the computer account (when you give credentials) and switches back to local users.

---

## Importing existing shares

When ShareHub finds shares it does not manage, the Overview shows **Import…**. It can take over:

- Samba registry shares, for example from the 45Drives *File Sharing* module;
- NFS exports from `/etc/exports.d/cockpit-file-sharing.exports` and `/etc/exports`;
- settings from the cockpit-smbftp prototype.

The import window lists Samba options that ShareHub does not manage and will drop. After the import:
- imported `/etc/exports` lines are commented out;
- the 45Drives exports file is renamed with `.sharehub-imported`.

Remove the 45Drives module afterwards (`sudo apt remove cockpit-file-sharing`) so two tools do not edit the same shares.

## Security recommendations

- Keep **SMB encryption** on *desired* or *required*, and the minimum protocol at SMB 3.0 or higher if all clients support it.
- **Require FTPS**, or leave FTP off.
- Use **groups** for access, a dedicated group-owned folder with mode `2770`, and avoid guest access.
- Use `root_squash` for NFS unless the client is a trusted server.
- Restrict firewall rules to your LAN.
