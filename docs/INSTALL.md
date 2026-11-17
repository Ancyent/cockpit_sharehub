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
sudo ./install.sh            # or: sudo ./install.sh --minimal  (Cockpit + Samba only)
```

`install.sh` installs the packages, runs `make install`, and runs the same setup step as the Debian package.

### Option C - build the package yourself

```bash
sudo apt install debhelper
make deb                       # creates dist/cockpit-sharehub_<version>_all.deb
```

## 4. First run

1. Open `https://<server>:9090` and log in with your administrator account.
2. Click **Limited access** in the top bar and confirm with your password. ShareHub needs administrative access.
3. Open **File Sharing** in the menu.
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

Samba registry shares are **kept**. Remove them with `net conf delshare <name>` if you no longer want them.

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
