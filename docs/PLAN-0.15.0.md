# ShareHub 0.15.0 - web file manager (Cockpit Files)

Status: implemented in 0.15.0. Open question answered: accounts with a login shell keep Cockpit's normal behaviour (they can log in with their own rights).

## Decisions (from discovery)

- **Product: Cockpit Files** (`cockpit-files`), the official file manager of the Cockpit project. The classic
  File Browser (filebrowser.org) is excluded: its final release shipped and the repository was archived in 2026.
- **Admins only, inside Cockpit only.** No extra port, service, account database or public link. The file manager
  is reachable only after logging in to Cockpit (port 9090) and works with the rights of that session: full access
  with *administrative access* turned on.
- **Optional feature**, off by default; chosen in the setup wizard, under Settings → Features or with
  `install.sh --features ...,files`.

## Facts checked against the official sources

- Cockpit's install guide installs Cockpit on Debian from `<codename>-backports` (enable the backports line,
  then `apt install -t <codename>-backports ...`); Ubuntu 24.04 has backports enabled by default.
- `cockpit-files` is **not in Debian 13 main**: it is in `trixie-backports` (46-1~bpo13+1) and in Ubuntu
  `noble-backports` (39-1~bpo24.04.1). It needs `cockpit-bridge (>= 318)`.
- ShareHub users are created with `/usr/sbin/nologin`; Cockpit refuses them (tested: HTTP 403, "This account is
  currently not available"). So share users cannot open the file manager; only accounts with a login shell can
  log in to Cockpit at all.

## 1. Feature `files`

| Item | Behaviour |
|---|---|
| Label | Web file manager (Cockpit Files) |
| Package | `cockpit-files`, installed with `-t <codename>-backports` when the package only exists there |
| States | **Ready** installed · **Needs packages** available in the enabled sources · **Needs setup** only in backports and backports not enabled (button: *Enable backports and install*) · **Not possible** no candidate anywhere (e.g. distribution without it) |
| Off | ShareHub hides its links to the file manager. The package is not removed automatically; the row says `sudo apt remove cockpit-files` |
| Default | Off (opt-in) on a fresh install; upgrades keep it off |

## 2. Installing from backports (helper)

- New allow-listed install path in `install-packages` for `cockpit-files` only:
  1. find where a candidate exists (`apt-cache policy`), preferring the normal sources;
  2. if only in backports and the backports line is missing (Debian), write
     `/etc/apt/sources.list.d/backports.list` with exactly the line from the Cockpit guide, then `apt-get update`;
  3. `apt-get install -y -t <codename>-backports cockpit-files`.
- Nothing else is taken from backports (no automatic upgrade of Cockpit itself). The confirmation dialog says
  that backports are enabled and what will be installed.
- Uninstall docs: `sudo apt remove cockpit-files`; the backports line can stay or be removed.

## 3. Integration in ShareHub

- **Shares list:** an **Open in Files** action per share that opens Cockpit Files at the share folder
  (`cockpit.jump` to the Files page with the folder path; exact URL checked against the installed version).
- **Versions dialog:** **Open in Files** for the current folder of a Btrfs version (read-only snapshot folder),
  so an admin can look around a version with the full file manager.
- **Share editor → Folder → Browse…:** link "Open in Files" for the chosen folder.
- Shown only when the feature is **Ready**; when *administrative access* is off, the link says that Files will
  only show what the logged-in user may read.

## 4. Dashboard, setup, install.sh

- Components dashboard: new row with the states above, version, and Install / *Enable backports and install*.
- Setup wizard and Settings → Features: new entry, unchecked by default.
- `install.sh --features ...,files`: same backports logic, following the Cockpit guide.

## 5. Tests

- Unit: candidate detection (main / backports only / none), backports line content, feature states.
- Real: install on Debian 13 (trixie-backports) and Ubuntu 24.04 (noble-backports) on a VM or LXC; open a share
  from the Shares list; non-admin session; share user rejected by Cockpit; feature off hides the links.

## Open question

- Accounts **with** a login shell (created with "Allow shell login") can log in to Cockpit with their own rights
  and would see Cockpit Files too. Leave it as Cockpit does (recommended: they are trusted accounts), or add them
  to `/etc/cockpit/disallowed-users` so only administrators can log in to Cockpit?
