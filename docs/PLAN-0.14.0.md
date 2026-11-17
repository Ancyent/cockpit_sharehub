# ShareHub 0.14.0 - implementation plan

Status: implemented in 0.14.0. Differences from the draft:
- "Search in a version / in the recycle bin" runs as one request limited to 15 seconds and 200 matches
  instead of a cancellable background job; it only runs when the admin clicks **Search subfolders**.
- mDNS, WS-Discovery and NetBIOS stay visible in Settings → SMB even when off, because their switch there is
  the same setting as in Settings → Features.
- The Users tab had no FTP-only columns, so nothing was hidden there.

## Goals

1. The admin chooses which features ShareHub manages, at first run and any time later.
2. The UI never offers something that cannot work on this system: unavailable features are greyed out
   everywhere, with the reason and, where it helps, an Install button.
3. "Can be used" means the same thing in the dashboard and in the share editor, because both check the
   real share folders (including LXC containers).
4. Small UX polish: folder-created animation in the folder picker.

Non-goals: interactive questions during `apt install` (the .deb stays non-interactive, so unattended
installs and `apt upgrade` keep working).

---

## 1. Feature model (backend)

One source of truth for every optional feature, used by the dashboard, the editor, Settings and the
first-run wizard.

Features: `smb` (always on), `netbios`, `nfs`, `ftp`, `ftps`, `mdns`, `wsdd`, `ad`, `recycle`,
`quota`, `snapshots` (scheduled versions), `prev_versions` (external snapshot tools: sanoid,
zfs-auto-snapshot, snapper).

Each feature gets a computed **state**:

| State | Meaning | UI |
|---|---|---|
| `off` | Turned off by the admin (Settings → Features) | Hidden everywhere |
| `impossible` | Cannot work on this system (e.g. NFS in LXC) | Greyed out + reason + "Details" |
| `needs_install` | Possible, a package is missing | Greyed out + reason + **Install** |
| `needs_setup` | Packages present, but the filesystem/host must be prepared (e.g. ext4 without `project`) | Greyed out + exact steps |
| `ready` | Usable | Normal |

Changes:

- `state.json`: new section `features: {nfs: true, ftp: true, ...}` (defaults: everything on for
  existing installs, so an upgrade changes nothing; the wizard sets them on a fresh install).
- `health.py`: `features()` returns the state above per feature, plus a per-share breakdown for
  `quota` and `snapshots` (see section 3).
- `reconcile`: a feature that is `off` or `impossible` is never rendered (no exports, no ProFTPD
  config, no timers enabled). Turning a feature off stops and disables its services/timers.
- Validation: turning a feature off is refused while a share uses it ("Used by: CTD-Office, ..."),
  with a link to those shares.
- New commands: `features-get`, `features-set` (JSON), used by the wizard and Settings.

## 2. First-run wizard (frontend)

Shown the first time ShareHub opens when `features` is not yet set (fresh install), and reachable
later from Settings → Features → "Run setup again".

Steps:

1. **Features**: a checklist (custom toggles, no native controls). Each row shows the live check:
   "Possible here", "Needs packages: ...", or "Not possible here: <reason>" (row disabled).
2. **Packages**: the list of packages that will be installed for the selected features, with
   versions from apt; one **Install** action (existing allow-listed `install-packages`).
3. **Done**: summary per feature, link to the Components dashboard.

The wizard can be skipped ("Use defaults"); defaults = whatever is already possible on the system.

## 3. Real checks per share (quota and versions)

Problem seen on Debian 13 LXC: the dashboard showed "Size limits: Can be used" because
`zfsutils-linux` was installed, while the share folder was ext4 and quotas cannot be set from inside
the container.

- The dashboard row for `quota` and `snapshots` is computed from the filesystems of the existing
  shares (and, with no shares yet, from the filesystems under the usual share roots).
- The row shows one line per filesystem in use, e.g. `ext4 (3 shares): needs host setup`.
- **Container rules** (`container_type()` already exists):
  - ZFS inside LXC (`/dev/zfs` missing): `impossible` - "Set the size of the container's mount point
    on the Proxmox host (`pct set <id> -mpN ...,size=500G`); take snapshots on the host."
  - ext4/XFS inside a container: `impossible` - same host hint (project quotas need the host).
  - Btrfs inside a privileged container: checked for real; unprivileged: `impossible`.
- The editor's Size limit field and Versions tab use the same function, so both screens always say
  the same thing.
- The order of the ext4 checks is fixed: filesystem feature and mount option first, package last
  (today it says "Install the 'quota' package" even when installing it would not help).
- Install buttons are offered only when installing actually makes the feature usable.

## 4. Greyed-out UI everywhere

Applied per feature state from section 1:

- Share editor: General toggles (NFS/FTP), Size limit field, tabs NFS / FTP / Versions, SMB options
  that depend on a feature (Previous Versions source, recycle bin).
- Share list: feature badges and the Versions action.
- Settings: NFS, FTP/FTPS, discovery, AD sections.
- Users: Samba/FTP related columns only if the feature is on.
- Greyed controls keep their values (nothing is lost if the feature comes back), show a one-line
  reason, a **Details** link to the dashboard row and, for `needs_install`, an **Install** button.
  After a successful install the view refreshes its feature states without a page reload.
- `off` features are hidden, not greyed.

## 5. install.sh

- `sudo ./install.sh --features smb,ftp,mdns,wsdd,recycle,snapshots` installs only those packages
  and writes the same `features` section as the wizard.
- Without `--features` and with a terminal: a checklist (whiptail if present, plain prompts
  otherwise). Without a terminal: defaults (what is possible here).
- `--minimal` stays as an alias for `--features smb`.

## 6. Folder-created animation

In the folder picker (`controls.js`, "New folder…"):

1. After `mkdir` succeeds, the picker lists the new folder already selected, with a short highlight.
2. A "Folder created" label appears next to it.
3. The label fades out while moving toward the Path field; the Path field then shows the new path
   with a short highlight.

Implementation: CSS classes and `transform`/`opacity` transitions (no inline styles, CSP-safe),
position computed with `getBoundingClientRect()` and applied through CSSOM. With
`prefers-reduced-motion: reduce` there is no movement, only the highlight and label.

## 7b. Versions and deleted files at scale (millions of files)

Snapshots themselves are cheap at any size: a ZFS or Btrfs snapshot is a metadata operation and does
not depend on the number of files. The risk is in the restore UI and the recycle bin jobs.

Current state (0.13.0):
- Versions tab: already folder by folder, but a folder with 200 000 files is read and sorted in one
  call, and the result is silently cut at 5 000 entries.
- Deleted files tab: walks the **whole** `.recycle` tree on every open - with millions of files this
  is slow and loads the server.
- Restore of a big folder runs as one blocking call with no progress.

Changes:
- **Folder-like browsing everywhere.** Deleted files becomes a browser like Versions:
  `user → folder → files`, one directory per request. Nothing walks the whole tree to open a view.
- **Paging.** The backend returns entries page by page (e.g. 500 per page, sorted by name or date,
  folders first) with a total count; the UI loads the next page on scroll. No silent cut-off.
- **Filter within the current folder** (server side, name contains), not a global scan.
- **"Versions of this file".** From a file in the current share: one `stat` per snapshot (cheap even
  with thousands of snapshots), showing only the distinct versions (size/date changed) with Restore.
  This replaces searching through versions by hand.
- **Search in a version / in the recycle bin** (optional, explicit button): runs as a background job
  with a time and result limit, can be cancelled, never runs automatically.
- **Restore jobs.** Restoring a folder runs as a background job (systemd-run transient unit, `nice`
  and `ionice -c3`), with progress (files/bytes copied) and Cancel; the dialog can be closed and the
  job shows on the Overview until done. Single files stay instant.
- **Recycle bin maintenance.** Daily cleanup and the "versions per file" trim run with `nice`/`ionice`,
  and use `find -mtime` instead of Python walks for age. No index file is needed, because browsing
  per folder never reads the whole tree.
- **Limits.** Hard caps on request time (e.g. 10 s per listing) so a huge folder returns a partial page
  with "more" instead of blocking the helper or the lock.

## 7. Existing users with dots or upper case (done)

Commit `a62bc49` on `main`: users created outside ShareHub (e.g. `Andrei.P`) can now get a
password, be edited and get a Samba account. New users keep the strict name rule.

---

## Upgrade behaviour

- Existing installs: `features` is created with every feature on, then features that are
  `impossible` are shown greyed out; no service is stopped by the upgrade itself.
- The wizard does not open on upgrade (only on a fresh install), but Settings → Features is
  available.

## Tests

- Unit: feature state for each combination (container/not, fs type, package present/missing,
  turned off), refusal to turn off a used feature, reconcile skipping off features,
  install.sh argument parsing (bash, via a dry-run flag).
- Real: Btrfs and ext4 loop mounts in the sandbox; LXC behaviour simulated through the container
  marker; UI screenshots of the wizard, greyed editor and animation.
- Field (Debian 13 LXC): dashboard and editor agree for the ext4 share; NFS greyed everywhere.

## Docs

CHANGELOG 0.14.0, USER_GUIDE (wizard, Features, greyed states, LXC size limits via the host),
INSTALL (`--features`), ARCHITECTURE (feature model, new commands), TESTING.
