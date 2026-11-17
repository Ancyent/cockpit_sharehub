# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Shared helpers: process execution, atomic file writes, small parsers."""
import os
import re
import shutil
import subprocess
import tempfile


class HelperError(Exception):
    """An error that is shown to the user as-is."""


def run(cmd, check=True, inp=None, env=None, timeout=120):
    try:
        proc = subprocess.run(cmd, input=inp, capture_output=True, text=True,
                              env=env, timeout=timeout)
    except FileNotFoundError:
        raise HelperError(f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise HelperError(f"Command timed out: {' '.join(cmd)}")
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        raise HelperError(f"'{' '.join(cmd)}' failed: {msg}")
    return proc


def have(binary):
    return shutil.which(binary) is not None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def atomic_write(path, content, mode=0o644):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sharehub-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def remove_file(path):
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def is_true(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def octal_unescape(text):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def fstab_escape(text):
    return (text.replace("\\", "\\134").replace(" ", "\\040")
                .replace("\t", "\\011").replace("\n", "\\012"))


def mountpoints():
    points = set()
    with open("/proc/self/mountinfo") as f:
        for line in f:
            parts = line.split()
            if len(parts) > 4:
                points.add(octal_unescape(parts[4]))
    return points


def has_systemd():
    return os.path.isdir("/run/systemd/system") and have("systemctl")


def unit_state(unit):
    """Return (active_state, enabled_state) for a systemd unit, or (None, None)."""
    if not has_systemd():
        return None, None
    active = run(["systemctl", "is-active", unit], check=False).stdout.strip() or None
    enabled = run(["systemctl", "is-enabled", unit], check=False).stdout.strip() or None
    return active, enabled


def unit_exists(unit):
    if not has_systemd():
        return False
    proc = run(["systemctl", "list-unit-files", "--no-legend", unit], check=False)
    return bool(proc.stdout.strip())


def systemctl(*args, warnings=None):
    """Run systemctl; failures become warnings instead of errors."""
    if not has_systemd():
        if warnings is not None:
            warnings.append(f"systemd is not running - skipped 'systemctl {' '.join(args)}'.")
        return False
    proc = run(["systemctl", *args], check=False)
    if proc.returncode != 0:
        if warnings is not None:
            warnings.append(f"'systemctl {' '.join(args)}' failed: {proc.stderr.strip()}")
        return False
    return True


def container_type():
    """'lxc', 'docker', ... when running in a container, else ''."""
    if have("systemd-detect-virt"):
        out = run(["systemd-detect-virt", "-c"], check=False).stdout.strip()
        return "" if out in ("", "none") else out
    marker = read_text("/run/systemd/container")
    if marker:
        return marker.strip()
    return "docker" if os.path.exists("/.dockerenv") else ""


def nfs_server_support():
    """(supported, reason). The kernel NFS server is not available inside containers."""
    if os.path.ismount("/proc/fs/nfsd"):
        return True, None
    ctype = container_type()
    if ctype:
        return False, (f"This system runs in a container ({ctype}). The NFS server is part of the kernel "
                       "and cannot run in a container - run NFS on the host or in a virtual machine.")
    return True, None


def wsdd_unit():
    """The installed WS-Discovery daemon unit: wsdd-server (wsdd) or wsdd2."""
    for unit in ("wsdd-server", "wsdd2"):
        if unit_exists(f"{unit}.service"):
            return unit
    return None


def fs_type(path):
    """Filesystem type of the mount that contains path (e.g. ext4, zfs, btrfs)."""
    proc = run(["findmnt", "-n", "-o", "FSTYPE", "--target", path], check=False)
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""


def mount_source(path):
    proc = run(["findmnt", "-n", "-o", "SOURCE,TARGET", "--target", path], check=False)
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    parts = line.split(None, 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")
