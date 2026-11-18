#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
#
# Install ShareHub from a source checkout (for development or when you do not
# use the .deb package).
#
#   sudo ./install.sh                       ask which features to install (in a terminal)
#   sudo ./install.sh --features smb,ftp,mdns,wsdd,recycle,snapshots
#   sudo ./install.sh --minimal             same as --features smb
#   sudo ./install.sh --dry-run ...         show what would be installed, change nothing
#
# Features: smb (always), nfs, ftp, mdns, wsdd, netbios, ad, recycle, quota, snapshots, versions, files.
# They can be changed later in Cockpit: File Sharing → Settings → Features.
set -euo pipefail

cd "$(dirname "$0")"
ALL=(nfs ftp mdns wsdd netbios ad recycle quota snapshots versions files)
declare -A ABOUT=(
    [nfs]="NFS server (Linux, VMware, Proxmox clients)"
    [ftp]="FTP / FTPS server (ProFTPD)"
    [mdns]="macOS discovery - Bonjour (avahi)"
    [wsdd]="Windows discovery - WS-Discovery"
    [netbios]="NetBIOS name service (very old clients)"
    [ad]="Active Directory join (winbind, Kerberos)"
    [recycle]="Recycle bin for files deleted over SMB"
    [quota]="Size limits per share"
    [snapshots]="Scheduled read-only versions (ZFS/Btrfs)"
    [versions]="Previous Versions from sanoid/zfs-auto-snapshot/snapper"
    [files]="Web file manager in Cockpit (Cockpit Files, from backports)"
)
FEATURES=""
DRY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --features) FEATURES="${2:-}"; shift 2 ;;
        --features=*) FEATURES="${1#*=}"; shift ;;
        --minimal) FEATURES="smb"; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '4,15p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

if [[ $EUID -ne 0 && $DRY -eq 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi
if ! command -v apt-get >/dev/null; then
    echo "This installer supports Debian/Ubuntu (apt). Use 'make install' on other systems." >&2
    exit 1
fi

container=""
if command -v systemd-detect-virt >/dev/null; then
    container=$(systemd-detect-virt -c 2>/dev/null || true)
    [[ "$container" == "none" ]] && container=""
fi

# Defaults: what works on most servers; NFS only outside containers.
defaults="mdns,wsdd,recycle,quota,snapshots,ftp"
[[ -z "$container" ]] && defaults="nfs,$defaults"

if [[ -z "$FEATURES" ]]; then
    if [[ -t 0 && -t 1 ]]; then
        if command -v whiptail >/dev/null; then
            args=()
            for f in "${ALL[@]}"; do
                on=OFF; [[ ",$defaults," == *",$f,"* ]] && on=ON
                [[ "$f" == "nfs" && -n "$container" ]] && continue
                args+=("$f" "${ABOUT[$f]}" "$on")
            done
            choice=$(whiptail --title "ShareHub" --separate-output --checklist \
                "SMB file sharing is always installed. Choose the other features:" 20 76 11 "${args[@]}" 3>&1 1>&2 2>&3) || exit 1
            FEATURES="smb,$(echo "$choice" | paste -sd, -)"
        else
            echo "SMB file sharing is always installed. Choose the other features (Enter = default):"
            chosen=(smb)
            for f in "${ALL[@]}"; do
                if [[ "$f" == "nfs" && -n "$container" ]]; then
                    echo "  nfs: not possible in a $container container - skipped"; continue
                fi
                def=n; [[ ",$defaults," == *",$f,"* ]] && def=y
                read -r -p "  ${ABOUT[$f]}? [$( [[ $def == y ]] && echo Y/n || echo y/N )] " ans
                ans=${ans:-$def}
                [[ "$ans" =~ ^[Yy] ]] && chosen+=("$f")
            done
            FEATURES=$(IFS=,; echo "${chosen[*]}")
        fi
    else
        FEATURES="smb,$defaults"
    fi
fi

has() { [[ ",$FEATURES," == *",$1,"* ]]; }
for f in ${FEATURES//,/ }; do
    [[ "$f" == "smb" || -n "${ABOUT[$f]:-}" ]] || { echo "Unknown feature: $f" >&2; exit 2; }
done
if has nfs && [[ -n "$container" ]]; then
    echo "Note: NFS cannot run in a $container container - it is not installed." >&2
fi

available() { apt-cache show "$1" >/dev/null 2>&1; }
fstypes=$(findmnt -rn -o FSTYPE 2>/dev/null | sort -u || true)
packages=(cockpit samba samba-common-bin python3 make acl attr smbclient)
has nfs && [[ -z "$container" ]] && packages+=(nfs-kernel-server)
if has ftp; then
    if available proftpd-core; then packages+=(proftpd-core); else packages+=(proftpd-basic); fi
    available proftpd-mod-crypto && packages+=(proftpd-mod-crypto)
    packages+=(ssl-cert)
fi
has mdns && packages+=(avahi-daemon)
if has wsdd; then
    # WS-Discovery: wsdd-server (Ubuntu, Debian 12) or wsdd2 (Debian 13)
    for wsd in wsdd-server wsdd2; do
        if available "$wsd"; then packages+=("$wsd"); break; fi
    done
fi
has ad && packages+=(winbind libnss-winbind libpam-winbind krb5-user)
if has quota || has snapshots; then
    # one tool per filesystem that is actually mounted here (ZFS tools come with ZFS itself)
    grep -qx xfs <<<"$fstypes" && has quota && packages+=(xfsprogs)
    grep -qx ext4 <<<"$fstypes" && has quota && [[ -z "$container" ]] && packages+=(quota)
    grep -qx btrfs <<<"$fstypes" && packages+=(btrfs-progs)
fi

# Cockpit Files is only in <codename>-backports; install it as Cockpit's guide does
# (https://cockpit-project.org/running.html): enable backports, then apt install -t <codename>-backports.
. /etc/os-release
bp_suite="${VERSION_CODENAME:-}-backports"
bp_needed=0
if has files; then
    if [[ -z "${VERSION_CODENAME:-}" ]]; then
        echo "Note: cannot find the release codename - Cockpit Files skipped." >&2
    else
        bp_needed=1
    fi
fi
bp_enabled() {
    grep -rhsE "^[^#]*(deb |Suites:).*[[:space:]]${bp_suite}([[:space:]]|$)" /etc/apt/sources.list /etc/apt/sources.list.d/ >/dev/null
}

echo "==> Features: $FEATURES"
echo "==> Packages: ${packages[*]}"
[[ $bp_needed -eq 1 ]] && echo "==> From $bp_suite: cockpit-files$(bp_enabled || echo " (backports will be enabled)")"
if [[ $DRY -eq 1 ]]; then
    echo "(dry run - nothing installed)"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q "${packages[@]}"
if [[ $bp_needed -eq 1 ]]; then
    if ! bp_enabled; then
        if [[ "$ID" == "debian" ]]; then
            echo "deb http://deb.debian.org/debian ${bp_suite} main" > /etc/apt/sources.list.d/backports.list
            apt-get update -q
        else
            echo "Note: $bp_suite is not enabled in the apt sources - Cockpit Files skipped." >&2
            bp_needed=0
        fi
    fi
    [[ $bp_needed -eq 1 ]] && apt-get install -y -q -t "$bp_suite" cockpit-files
fi

echo "==> Installing ShareHub"
make install
/usr/lib/sharehub/sharehub-helper features-init "$FEATURES"
/usr/lib/sharehub/sharehub-helper bootstrap
if [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    systemctl enable --now cockpit.socket
fi

echo
/usr/lib/sharehub/sharehub-helper check || true
cat <<MSG

ShareHub is installed.
Open https://$(hostname -f 2>/dev/null || hostname):9090, log in with an administrator
account, turn on administrative access, and choose "File Sharing" in the menu.
Features can be changed later under File Sharing → Settings → Features.
MSG
