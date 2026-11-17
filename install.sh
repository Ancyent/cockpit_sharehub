#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
# Installs the cockpit-smbftp plugin on Ubuntu / Debian.
# Usage: sudo ./install.sh [--enable-registry]
set -euo pipefail

PLUGIN_DIR=/usr/share/cockpit/smbftp
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/smbftp"
ENABLE_REGISTRY=0
[[ "${1:-}" == "--enable-registry" ]] && ENABLE_REGISTRY=1

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
echo "==> Installing packages"
apt-get update -q
apt-get install -y -q cockpit samba python3 ssl-cert
if apt-cache show proftpd-core >/dev/null 2>&1; then
    apt-get install -y -q proftpd-core
else
    apt-get install -y -q proftpd-basic
fi
# mod_tls lives in a separate package on Ubuntu 24.04 / Debian 12+
if apt-cache show proftpd-mod-crypto >/dev/null 2>&1; then
    apt-get install -y -q proftpd-mod-crypto
fi

echo "==> Configuring ProFTPD"
install -d -m 0755 /etc/proftpd/conf.d
if ! grep -Eq '^[[:space:]]*Include[[:space:]]+/etc/proftpd/conf\.d/?' /etc/proftpd/proftpd.conf; then
    printf '\n# Added by cockpit-smbftp\nInclude /etc/proftpd/conf.d/\n' >> /etc/proftpd/proftpd.conf
fi
if ls /usr/lib/proftpd/mod_tls.so >/dev/null 2>&1 && [[ -f /etc/proftpd/modules.conf ]]; then
    sed -i 's/^#[[:space:]]*LoadModule mod_tls\.c[[:space:]]*$/LoadModule mod_tls.c/' /etc/proftpd/modules.conf
fi
if ! proftpd -t >/dev/null 2>&1; then
    echo "WARNING: 'proftpd -t' reports a problem in the existing ProFTPD config:" >&2
    proftpd -t >&2 || true
fi

echo "==> Installing plugin to $PLUGIN_DIR"
install -d -m 0755 "$PLUGIN_DIR" /etc/cockpit-smbftp /srv/ftp-shares
install -m 0644 "$SRC_DIR"/manifest.json "$SRC_DIR"/index.html "$SRC_DIR"/smbftp.js "$SRC_DIR"/smbftp.css "$PLUGIN_DIR"/
install -m 0755 "$SRC_DIR"/helper.py "$PLUGIN_DIR"/

if [[ $ENABLE_REGISTRY -eq 1 ]]; then
    echo "==> Enabling Samba registry"
    echo '{}' | python3 "$PLUGIN_DIR/helper.py" enable-registry
    echo
fi

systemctl enable --now cockpit.socket >/dev/null
systemctl enable --now smbd >/dev/null 2>&1 || true

cat <<EOF

Done. Open https://$(hostname -f 2>/dev/null || hostname):9090 and choose "SMB + FTP Shares".
Firewall (ufw): sudo ufw allow 21/tcp && sudo ufw allow 49152:49200/tcp   (use your passive range)
EOF
