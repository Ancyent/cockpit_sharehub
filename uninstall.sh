#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
# Removes the cockpit-smbftp plugin. Samba shares stay in the registry;
# FTP publishing (bind mounts, fstab lines, ProFTPD include) is removed.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

# Unmount and drop managed fstab entries
grep -E '[[:space:]]bind,[^[:space:]]*x-cockpit-smbftp' /etc/fstab | awk '{print $2}' | while read -r target; do
    target=$(printf '%b' "$target")
    umount "$target" 2>/dev/null || umount -l "$target" 2>/dev/null || true
    rmdir "$target" 2>/dev/null || true
done
cp /etc/fstab /etc/fstab.cockpit-smbftp-uninstall.bak
sed -i '/x-cockpit-smbftp/d' /etc/fstab
systemctl daemon-reload || true

rm -f /etc/proftpd/conf.d/cockpit-smbftp.conf
systemctl reload-or-restart proftpd 2>/dev/null || true

rm -rf /usr/share/cockpit/smbftp
echo "Removed. State kept in /etc/cockpit-smbftp (delete it manually if not needed)."
