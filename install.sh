#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
#
# Install ShareHub from a source checkout (for development or when you do not
# use the .deb package). Usage:  sudo ./install.sh [--minimal]
#   --minimal   install only Cockpit + Samba (skip NFS, FTP, discovery packages)
set -euo pipefail

cd "$(dirname "$0")"
MINIMAL=0
[[ "${1:-}" == "--minimal" ]] && MINIMAL=1

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi
if ! command -v apt-get >/dev/null; then
    echo "This installer supports Debian/Ubuntu (apt). Use 'make install' on other systems." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
packages=(cockpit samba samba-common-bin python3 make)
if [[ $MINIMAL -eq 0 ]]; then
    packages+=(nfs-kernel-server ssl-cert avahi-daemon acl attr smbclient)
    for optional in proftpd-core proftpd-mod-crypto wsdd-server; do
        apt-cache show "$optional" >/dev/null 2>&1 && packages+=("$optional")
    done
    [[ " ${packages[*]} " == *" proftpd-core "* ]] || packages+=(proftpd-basic)
fi

echo "==> Installing packages: ${packages[*]}"
apt-get update -q
apt-get install -y -q "${packages[@]}"

echo "==> Installing ShareHub"
make install
/usr/lib/sharehub/sharehub-helper bootstrap
if [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    systemctl enable --now sharehub-recycle.timer cockpit.socket
fi

cat <<MSG

ShareHub is installed.
Open https://$(hostname -f 2>/dev/null || hostname):9090, log in with an administrator
account, turn on administrative access, and choose "File Sharing" in the menu.
MSG
