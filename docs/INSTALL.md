# Installing ShareHub

## 1. Requirements

| Item | Requirement |
|---|---|
| Operating system | Ubuntu 24.04 LTS (recommended). Ubuntu 22.04 and Debian 12 are expected to work but have not been tested yet. |
| Cockpit | 264 or newer |
| Samba | 4.15 or newer (`samba`, `samba-common-bin`) |
| Python | 3.10 or newer |
| Optional | `nfs-kernel-server` for NFS; `proftpd-core` + `proftpd-mod-crypto` for FTP/FTPS; `avahi-daemon` and `wsdd-server` for network discovery; `winbind`, `libnss-winbind`, `libpam-winbind` and `krb5-user` for Active Directory; `sanoid`, `zfs-auto-snapshot` or `snapper` for Previous Versions |

You need an account that can use `sudo`. Cockpit's "administrative access" uses it.

### Notes per distribution

| System | Notes |
|---|---|
| Ubuntu 24.04 | Everything is available. WS-Discovery uses `wsdd-server`. |
| Debian 13 (trixie) | WS-Discovery uses `wsdd2` (Debian 13 has no `wsdd`/`wsdd-server`). The `samba` package recommends `samba-ad-dc`, so a normal install also pulls in the Samba domain-controller packages. They stay inactive, but you can avoid them by installing with `--no-install-recommends` and adding what you need, or remove them afterwards: `sudo apt remove samba-ad-dc samba-ad-provision && sudo apt autoremove`. |
| LXC containers (e.g. Proxmox CT) | SMB, FTP, users, Active Directory and the UI work. **The NFS server does not**: it is part of the kernel and cannot run inside a container, so ShareHub marks NFS as not supported. Run NFS on the host or in a VM. Size limits are usually set on the host (the container's mount point size), because the container cannot manage ZFS or XFS quotas. |

## 2. Install Cockpit

If Cockpit is not installed yet, follow the official instructions at <https://cockpit-project.org/running>.

On Ubuntu, Cockpit's official instructions install it from the backports repository:

```bash
. /etc/os-release
sudo apt install -t ${VERSION_CODENAME}-backports cockpit
sudo systemctl enable --now cockpit.socket
```

On Debian, enable `${VERSION_CODENAME}-backports` first, as described on the same page.

## 3. Install ShareHub

### Option A - Debian package (recommended)

Download the `.deb` from the [Releases page](https://github.com/Ancyent/cockpit_sharehub/releases), then install it:

```bash
sudo apt install ./cockpit-sharehub_0.9.0_all.deb
```

At the end of the installation the package prints a **compatibility report** - one line per feature, saying whether it can be used on this system, can be used after installing a package, or is not possible here (for example the NFS server in a container):

```
ShareHub compatibility report
System: Debian GNU/Linux 13 (trixie) (lxc container), Samba 4.22.11-Debian

  [ OK ] SMB file server                   can be used
  [ -- ] NFS server                        This system runs in a container (lxc). ...
  [ + ]  Windows discovery (WS-Discovery)  install: apt install wsdd2
  ...
9 of 11 features can be used now, 1 after installing packages, 1 not possible on this system.
```

Run it again any time with `sudo /usr/lib/sharehub/sharehub-helper check`. The same information is on **Overview → Components**.

`apt` installs the required dependencies (Samba, Cockpit) and the recommended ones (NFS server, ProFTPD, Avahi, wsdd-server). If you don't want a protocol, install with `--no-install-recommends` and add only the packages you need.

The package then:
- installs the page to `/usr/share/cockpit/sharehub/`;
- installs the helper to `/usr/lib/sharehub/`;
- adds `Include /etc/proftpd/conf.d/` to `proftpd.conf` if it is missing;
- enables `mod_tls` in ProFTPD if the module is installed;
- enables the daily `sharehub-recycle.timer`.

### Option B - from source

```bash
git clone https://github.com/Ancyent/cockpit_sharehub.git
cd cockpit_sharehub
sudo ./install.sh                                   # asks which features to install
sudo ./install.sh --features smb,ftp,mdns,wsdd,recycle,snapshots
sudo ./install.sh --minimal                         # Cockpit + Samba only
sudo ./install.sh --dry-run --features smb,nfs      # show the packages, change nothing
```

Features: `smb` (always), `nfs`, `ftp`, `mdns`, `wsdd`, `netbios`, `ad`, `recycle`, `quota`, `snapshots`, `versions`.
Without `--features`, a terminal shows a checklist; without a terminal, the defaults are used (everything except
NetBIOS, Active Directory and external Previous Versions; NFS is skipped in containers). `install.sh` installs only
the packages of the chosen features (tools for size limits only for filesystems that are mounted), runs
`make install`, records the choice and runs the same setup step as the Debian package.

### Option C - build the package yourself

```bash
sudo apt install build-essential debhelper
make deb                       # creates dist/cockpit-sharehub_<version>_all.deb
```

## 4. First run

1. Open `https://<server>:9090` and log in with your administrator account.
2. Click **Limited access** in the top bar and confirm with your password. ShareHub needs administrative access.
3. Open **File Sharing** in the menu. On a fresh install, **Set up ShareHub** opens: tick the features you want (each shows whether it is possible on this system), confirm the packages to install, done. You can change this later under **Settings → Features**.
4. On the **Overview** tab, click **Enable registry**. This adds `include = registry` to `[global]` in `/etc/samba/smb.conf`; a backup is kept as `smb.conf.sharehub.bak`. ShareHub stores Samba settings in the Samba registry.
5. If ShareHub finds shares made by other tools (for example the 45Drives *File Sharing* module), it offers to **Import** them.
6. Optional: under **Settings → SMB**, check the workgroup, encryption and macOS options.
7. Create users under **Users & Groups**, then create shares under **Shares**.

> **Users created elsewhere.** Users created with `adduser` or Cockpit's *Accounts* page have no Samba password. ShareHub marks them **No Samba password**. Use **Password** on the Users tab to set one password for both the system and Samba.

## 5. Firewall

The **Overview → Firewall** card lists the ports needed by the protocols you have turned on. If ufw or firewalld is active, it can open them for you.

| Protocol | Ports |
|---|---|
| SMB | 445/tcp. With NetBIOS on, also 137-138/udp and 139/tcp |
| macOS discovery (mDNS) | 5353/udp |
| Windows discovery (WS-Discovery) | 3702/udp, 5357/tcp |
| NFSv4 | 2049/tcp |
| NFSv3 | adds 111/tcp+udp, 20048/tcp+udp, 32765-32768/tcp+udp (ShareHub fixes these ports) |
| FTP | 21/tcp plus the passive range set under **Settings → FTP** |

The button opens the ports to any address. To restrict them to your LAN, change the rules in ufw or firewalld afterwards, for example:

```bash
sudo ufw delete allow 445/tcp
sudo ufw allow from 192.168.1.0/24 to any port 445 proto tcp
```

## 6. Upgrading

**Debian package:** install the newer `.deb` the same way:

```bash
sudo apt install ./cockpit-sharehub_<new>_all.deb
```

**From source:** update the checkout and run the installer again:

```bash
git pull
sudo ./install.sh
```

Your configuration in `/etc/sharehub` is kept. After an upgrade, **Settings → History → Re-apply current configuration** rewrites all generated files with the new version.

## 7. Removing

**Remove the plugin:**

```bash
sudo apt remove cockpit-sharehub      # or: sudo ./uninstall.sh
```

Shares keep working with the last configuration.

**Purge everything ShareHub generated:**

```bash
sudo apt purge cockpit-sharehub       # or: sudo ./uninstall.sh --purge
```

This removes:
- the FTP bind mounts and their fstab lines;
- the ProFTPD include;
- the NFS exports and NFS settings;
- `/etc/sharehub`.

**What stays after a purge** (on purpose, so no data is lost):

| What | Where | How to remove it, if you want |
|---|---|---|
| SMB shares | Samba registry | `sudo net conf listshares`, then `sudo net conf delshare <name>` |
| `include = registry` in `[global]` | `/etc/samba/smb.conf` (backup: `smb.conf.sharehub.bak`) | Remove the line, or restore the backup, if no registry shares are left |
| Users, groups and Samba passwords | system accounts, Samba passdb | `sudo smbpasswd -x <user>`, `sudo deluser <user>` |
| Size limits | ZFS `refquota`, XFS/ext4 project quotas, Btrfs qgroups | Clear **Size limit** on each share **before** uninstalling |
| Scheduled versions | ZFS snapshots `@sharehub_*`, Btrfs `<share>/.sharehub-snapshots/` | Turn versions off first, then `sudo zfs destroy <dataset>@sharehub_…` or `sudo btrfs subvolume delete <share>/.sharehub-snapshots/sharehub_*` |
| Deleted files | `<share>/.recycle/` | Delete the folder when nobody needs the files |
| Packages (Samba, ProFTPD, NFS server, …) | apt | `sudo apt remove <package>`. Do not run `apt autoremove` blindly: it would remove Samba and stop the shares |

**Reinstalling** after `apt remove` brings back the page with all settings. After a purge, ShareHub starts empty and offers to **Import** the SMB shares still in the Samba registry.

## 8. Troubleshooting

| Symptom | What to check |
|---|---|
| "Administrative access is required" | Click **Limited access** in Cockpit's top bar. The account must be allowed to use `sudo`. |
| "The Samba registry is not enabled" | Overview → **Enable registry**, or add `include = registry` at the end of `[global]` in `smb.conf`. |
| A user cannot log in over SMB | Users & Groups: the user needs **Samba: active**. Click **Password** to set a Samba password. |
| FTP login fails for a user with no shell | ShareHub adds `RequireValidShell off`; check that `proftpd.conf` does not set it to `on`. |
| "FTP · not mounted" on a share | Run **Settings → History → Re-apply current configuration** and check `journalctl -u proftpd`. |
| FTPS cannot be enabled | Install `proftpd-mod-crypto` and make sure `LoadModule mod_tls.c` is not commented out in `/etc/proftpd/modules.conf`. |
| Previous Versions are empty | The share must be on ZFS or Btrfs and have snapshots (see the User Guide). |
| NFS clients get "access denied" | The client's address must match a line on the share's **NFS** tab. Check `sudo exportfs -v`. |
| AD join fails | Use **Check prerequisites**: DNS must point to the domain controllers, the clock must be synced, and the join account needs rights to add computers. |

Logs:
- Samba: `journalctl -u smbd` and `/var/log/samba/`
- NFS: `journalctl -u nfs-server`
- FTP: `journalctl -u proftpd` and `/var/log/proftpd/`
- ShareHub: every helper call is visible in Cockpit's *Logs* page under the `sudo` and `cockpit-bridge` entries.
