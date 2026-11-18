# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Packages that Debian/Ubuntu ship only in <codename>-backports (Cockpit Files).

Follows the Cockpit install guide (https://cockpit-project.org/running.html): on Debian add
"deb http://deb.debian.org/debian ${VERSION_CODENAME}-backports main" and install with
"apt install -t ${VERSION_CODENAME}-backports ..."; Ubuntu enables backports by default."""
import glob
import os
import re

from .util import HelperError, atomic_write, read_text, run

PACKAGES = {"cockpit-files"}
LIST_FILE = "/etc/apt/sources.list.d/backports.list"
SOURCES = ["/etc/apt/sources.list"]
SOURCES_DIRS = "/etc/apt/sources.list.d"


def os_release(text=None):
    text = read_text("/etc/os-release") if text is None else text
    out = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


def suite(rel=None):
    """<codename>-backports on Debian or Ubuntu, else None."""
    rel = os_release() if rel is None else rel
    family = {rel.get("ID", "")} | set(rel.get("ID_LIKE", "").split())
    codename = rel.get("VERSION_CODENAME") or rel.get("UBUNTU_CODENAME")
    if codename and family & {"debian", "ubuntu"}:
        return f"{codename}-backports"
    return None


def distro(rel=None):
    rel = os_release() if rel is None else rel
    return "ubuntu" if rel.get("ID") == "ubuntu" or "ubuntu" in rel.get("ID_LIKE", "").split() else rel.get("ID", "")


def _source_files():
    files = [f for f in SOURCES if os.path.exists(f)]
    files += sorted(glob.glob(os.path.join(SOURCES_DIRS, "*.list")) + glob.glob(os.path.join(SOURCES_DIRS, "*.sources")))
    return files


def enabled(name=None, files=None):
    """The backports suite appears in an active (not commented) apt source line."""
    name = name or suite()
    if not name:
        return False
    pattern = re.compile(r"(^|\s)" + re.escape(name) + r"(\s|$)")
    for path in files if files is not None else _source_files():
        for line in (read_text(path) or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("deb ") or line.startswith("Suites:"):
                if pattern.search(line.split("#", 1)[0]):
                    return True
    return False


def enable():
    """Add the backports line from the Cockpit guide (Debian). Ubuntu: backports are part of the
    default sources, so a missing suite means the admin removed it on purpose."""
    name = suite()
    if not name:
        raise HelperError("Backports are only supported on Debian and Ubuntu.")
    if enabled(name):
        return False
    if distro() != "debian":
        raise HelperError(f"{name} is not enabled in the apt sources. Ubuntu enables it by default; add "
                          f"'{name}' to the Suites line in /etc/apt/sources.list.d/ubuntu.sources and try again.")
    atomic_write(LIST_FILE, f"deb http://deb.debian.org/debian {name} main\n", 0o644)
    return True


def preview(packages):
    """What apt would install or upgrade for packages from backports: [{name, from, to}]."""
    name = suite()
    proc = run(["apt-get", "install", "-s", "-q", "-t", name, *packages], check=False)
    changes = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^Inst (\S+) (?:\[([^\]]+)\] )?\((\S+)", line)
        if m:
            changes.append({"name": m.group(1), "from": m.group(2), "to": m.group(3)})
    if proc.returncode != 0 and not changes:
        raise HelperError("apt cannot install this: " + (proc.stderr or proc.stdout).strip()[-400:])
    return changes
