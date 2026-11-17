#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
#
# Remove a source install of ShareHub. Usage:  sudo ./uninstall.sh [--purge]
#   (default)  remove the plugin; shares keep working with the last configuration
#   --purge    also remove everything ShareHub generated (FTP mounts and config,
#              NFS exports, /etc/sharehub). Samba registry shares are kept.
set -euo pipefail

cd "$(dirname "$0")"
if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

if [[ -d /run/systemd/system ]]; then
    systemctl disable --now sharehub-recycle.timer sharehub-snapshot.timer 2>/dev/null || true
fi
make uninstall

if [[ "${1:-}" == "--purge" ]]; then
    grep -E '[[:space:]]bind,[^[:space:]]*x-sharehub' /etc/fstab | awk '{print $2}' | while read -r target; do
        target=$(printf '%b' "$target")
        umount "$target" 2>/dev/null || umount -l "$target" 2>/dev/null || true
        rmdir "$target" 2>/dev/null || true
    done
    cp /etc/fstab /etc/fstab.sharehub-uninstall.bak
    sed -i '/x-sharehub/d' /etc/fstab
    rm -f /etc/proftpd/conf.d/sharehub.conf /etc/exports.d/sharehub.exports /etc/nfs.conf.d/sharehub.conf
    rm -rf /etc/sharehub
    command -v exportfs >/dev/null && exportfs -ra || true
    if [[ -d /run/systemd/system ]]; then
        systemctl daemon-reload
        systemctl try-reload-or-restart proftpd 2>/dev/null || true
    fi
    echo "ShareHub removed and its generated configuration purged."
else
    echo "ShareHub removed. Shares keep working; run with --purge to remove the generated configuration."
fi
