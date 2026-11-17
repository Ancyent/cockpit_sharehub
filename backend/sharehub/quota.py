# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Per-share size limits enforced by the filesystem.

    ZFS    refquota on the dataset that is mounted exactly at the share path
    XFS    project quota (filesystem mounted with prjquota)
    ext4   project quota (filesystem with the 'project' feature, mounted with prjquota)
    Btrfs  qgroup limit on the subvolume that is the share path

On ZFS, XFS and ext4 the kernel reports the limit through statfs, so SMB clients see
the share's size and free space correctly. On Btrfs the limit is enforced, but clients
see the size of the whole volume."""
import json
import os
import re

from .paths import STATE_DIR
from .util import HelperError, atomic_write, have, read_text, run

QUOTA_FILE = os.path.join(STATE_DIR, "quotas.json")
FIRST_PROJECT_ID = 20000
UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}


def to_bytes(size):
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGTP]?)$", str(size or "").strip().upper())
    if not m:
        raise HelperError("Size limit must look like 500G or 2T.")
    value = int(float(m.group(1)) * UNITS[m.group(2)])
    if value < 1024 ** 2:
        raise HelperError("Size limit must be at least 1M.")
    return value


def _mount(path):
    proc = run(["findmnt", "-n", "-o", "TARGET,FSTYPE,OPTIONS,SOURCE", "--target", path], check=False)
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    parts = line.split()
    if len(parts) < 4:
        return {"target": "", "fstype": "", "options": [], "source": ""}
    return {"target": parts[0], "fstype": parts[1], "options": parts[2].split(","), "source": parts[3]}


def _zfs_dataset(path):
    if not have("zfs"):
        return None
    proc = run(["zfs", "list", "-H", "-o", "name,mountpoint", "-t", "filesystem"], check=False)
    for line in proc.stdout.splitlines():
        name, _, mountpoint = line.partition("\t")
        if os.path.normpath(mountpoint) == os.path.normpath(path):
            return name
    return None


def detect(path):
    """How a size limit can be enforced for path: {"method", "supported", "reason", ...}."""
    path = os.path.normpath(path)
    mnt = _mount(path)
    fs = mnt["fstype"]
    out = {"path": path, "fstype": fs, "mountpoint": mnt["target"], "method": None, "supported": False}
    if fs == "zfs":
        dataset = _zfs_dataset(path)
        if dataset:
            out.update(method="zfs", supported=True, dataset=dataset)
        else:
            out["reason"] = (f"{path} is a folder inside a ZFS dataset. Size limits need a dedicated dataset "
                             f"(zfs create <pool>/<name>) mounted at the share path.")
    elif fs == "xfs":
        if any(o in mnt["options"] for o in ("prjquota", "pquota", "pqnoenforce")):
            out.update(method="xfs", supported=have("xfs_quota"),
                       reason=None if have("xfs_quota") else "Install xfsprogs.")
        else:
            out["reason"] = (f"Mount {mnt['target']} with the 'prjquota' option (in /etc/fstab, then remount) "
                             "to enable size limits.")
    elif fs == "ext4":
        features = run(["tune2fs", "-l", mnt["source"]], check=False).stdout if have("tune2fs") else ""
        has_feature = re.search(r"^Filesystem features:.*\bproject\b", features, re.M) is not None
        mounted = "prjquota" in mnt["options"]
        if has_feature and mounted and have("setquota"):
            out.update(method="ext4", supported=True)
        elif not have("setquota"):
            out["reason"] = "Install the 'quota' package."
        else:
            out["reason"] = ("ext4 needs the 'project' feature (tune2fs -O project,quota on the unmounted "
                             f"filesystem) and the 'prjquota' mount option on {mnt['target']}.")
    elif fs == "btrfs":
        is_subvol = os.stat(path).st_ino == 256
        if is_subvol and have("btrfs"):
            out.update(method="btrfs", supported=True)
        else:
            out["reason"] = (f"{path} is a folder, not a Btrfs subvolume. Create the share folder with "
                             "'btrfs subvolume create' to use size limits.")
    else:
        out["reason"] = f"Size limits are not supported on {fs or 'this filesystem'} (use ZFS, XFS, ext4 or Btrfs)."
    return out


# --------------------------------------------------------------------------- bookkeeping

def _load():
    raw = read_text(QUOTA_FILE)
    data = json.loads(raw) if raw else {}
    data.setdefault("projects", {})   # path -> project id (XFS/ext4)
    data.setdefault("applied", {})    # share name -> {"method", "path", ...}
    return data


def _save(data):
    atomic_write(QUOTA_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n", 0o600)


def _project_id(data, path):
    if path not in data["projects"]:
        used = set(data["projects"].values())
        pid = FIRST_PROJECT_ID
        while pid in used:
            pid += 1
        data["projects"][path] = pid
    return data["projects"][path]


# --------------------------------------------------------------------------- set / clear

def _set(info, limit, pid):
    path, mnt = info["path"], info["mountpoint"]
    method = info["method"]
    kib = max(1, limit // 1024)
    if method == "zfs":
        run(["zfs", "set", f"refquota={limit}", info["dataset"]])
    elif method == "xfs":
        run(["xfs_quota", "-x", "-c", f"project -s -p {path} {pid}", mnt])
        run(["xfs_quota", "-x", "-c", f"limit -p bhard={kib}k {pid}", mnt])
    elif method == "ext4":
        run(["chattr", "-R", "-p", str(pid), path])
        run(["find", path, "-xdev", "-type", "d", "-exec", "chattr", "+P", "{}", "+"])
        run(["setquota", "-P", str(pid), "0", str(kib), "0", "0", mnt])
    elif method == "btrfs":
        run(["btrfs", "quota", "enable", mnt])
        run(["btrfs", "qgroup", "limit", str(limit), path])


def _clear(entry, warnings):
    method, path = entry.get("method"), entry.get("path", "")
    try:
        if method == "zfs":
            run(["zfs", "set", "refquota=none", entry["dataset"]])
        elif method == "xfs":
            run(["xfs_quota", "-x", "-c", f"limit -p bhard=0 {entry['id']}", entry["mountpoint"]])
        elif method == "ext4":
            run(["setquota", "-P", str(entry["id"]), "0", "0", "0", "0", entry["mountpoint"]])
        elif method == "btrfs" and os.path.isdir(path):
            run(["btrfs", "qgroup", "limit", "none", path])
    except HelperError as e:
        warnings.append(f"Could not remove the old size limit on {path}: {e}")


def apply(state, warnings):
    data = _load()
    wanted = {name: s for name, s in state["shares"].items() if s["quota"]["limit"]}
    # remove limits that are no longer wanted (share deleted, limit cleared, path changed)
    for name, entry in list(data["applied"].items()):
        share = wanted.get(name)
        if not share or os.path.normpath(share["path"]) != entry["path"]:
            _clear(entry, warnings)
            del data["applied"][name]
    for name, share in wanted.items():
        limit = to_bytes(share["quota"]["limit"])
        info = detect(share["path"])
        if not info["supported"]:
            raise HelperError(f"{name}: cannot set a size limit. {info.get('reason', '')}".strip())
        entry = data["applied"].get(name)
        if entry and entry.get("limit") == limit and entry.get("method") == info["method"]:
            continue
        pid = _project_id(data, info["path"]) if info["method"] in ("xfs", "ext4") else None
        _set(info, limit, pid)
        data["applied"][name] = {"method": info["method"], "path": info["path"], "limit": limit,
                                 "mountpoint": info["mountpoint"], "id": pid, "dataset": info.get("dataset")}
    if data["applied"] or os.path.exists(QUOTA_FILE):
        _save(data)


# --------------------------------------------------------------------------- usage

def parse_xfs_report(text, pid):
    """`xfs_quota -x -c 'report -p -b -n -N'` -> (used_bytes, limit_bytes); values are KiB."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].lstrip("#") == str(pid) and len(parts) >= 4:
            return int(parts[1]) * 1024, int(parts[3]) * 1024
    return None


def parse_repquota(text, pid):
    """`repquota -P -n <mnt>` -> (used_bytes, limit_bytes); values are KiB."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].lstrip("#") == str(pid) and len(parts) >= 5:
            return int(parts[2].rstrip("*")) * 1024, int(parts[4]) * 1024
    return None


def parse_btrfs_qgroup(text, subvol_id):
    """`btrfs qgroup show -re --raw <path>` -> (used_bytes, limit_bytes)."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == f"0/{subvol_id}" and len(parts) >= 4:
            limit = 0 if parts[3] == "none" else int(parts[3])
            return int(parts[1]), limit
    return None


def usage(state):
    """{share: {"used": bytes, "limit": bytes, "method": str}} for shares with a limit."""
    data = _load()
    out = {}
    for name, entry in data["applied"].items():
        if name not in state["shares"]:
            continue
        method = entry["method"]
        result = None
        if method == "zfs":
            proc = run(["zfs", "get", "-Hp", "-o", "value", "referenced,refquota", entry["dataset"]], check=False)
            values = proc.stdout.split()
            if len(values) == 2 and values[0].isdigit():
                result = (int(values[0]), int(values[1]) if values[1].isdigit() else 0)
        elif method == "xfs":
            proc = run(["xfs_quota", "-x", "-c", "report -p -b -n -N", entry["mountpoint"]], check=False)
            result = parse_xfs_report(proc.stdout, entry["id"])
        elif method == "ext4":
            proc = run(["repquota", "-P", "-n", entry["mountpoint"]], check=False)
            result = parse_repquota(proc.stdout, entry["id"])
        elif method == "btrfs":
            rootid = run(["btrfs", "inspect-internal", "rootid", entry["path"]], check=False).stdout.strip()
            proc = run(["btrfs", "qgroup", "show", "-re", "--raw", entry["path"]], check=False)
            result = parse_btrfs_qgroup(proc.stdout, rootid) if rootid.isdigit() else None
        if result:
            out[name] = {"used": result[0], "limit": result[1] or entry["limit"], "method": method}
        else:
            out[name] = {"used": None, "limit": entry["limit"], "method": method}
    return out
