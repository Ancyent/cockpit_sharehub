# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Scheduled, read-only versions of a share (ZFS or Btrfs snapshots), retention, restore,
and the protected recycle bin.

Snapshots are named sharehub_<UTC %Y.%m.%d-%H.%M.%S>. They are read-only for everyone using
the share: SMB shows them as Windows "Previous Versions" and refuses writes, and the snapshot
directory is hidden from SMB and FTP. ZFS keeps them in <dataset>/.zfs/snapshot, Btrfs in
<share>/.sharehub-snapshots (read-only subvolumes)."""
import datetime
import os
import re
import shutil

from .quota import _zfs_dataset
from .util import HelperError, fs_type, have, mount_source, run

PREFIX = "sharehub_"
TIME_FORMAT = "%Y.%m.%d-%H.%M.%S"
SAMBA_FORMAT = PREFIX + TIME_FORMAT
BTRFS_DIR = ".sharehub-snapshots"
RECYCLE_DIR = ".recycle"
NAME_RE = re.compile(r"^sharehub_\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}$")
COPY_RE = re.compile(r"^Copy #(\d+) of (.+)$")


# --------------------------------------------------------------------------- support

def detect(path):
    """How scheduled versions can be kept for path: {"method": "zfs"|"btrfs"|None, "supported", "reason"}."""
    path = os.path.normpath(path)
    fs = fs_type(path)
    if fs == "zfs":
        dataset = _zfs_dataset(path)
        if dataset:
            return {"method": "zfs", "supported": True, "dataset": dataset, "fstype": fs}
        return {"method": None, "supported": False, "fstype": fs,
                "reason": f"{path} is a folder inside a ZFS dataset. Scheduled versions need a dedicated dataset "
                          "(zfs create <pool>/<name>) mounted at the share path."}
    if fs == "btrfs":
        if os.stat(path).st_ino == 256 and have("btrfs"):
            return {"method": "btrfs", "supported": True, "fstype": fs}
        return {"method": None, "supported": False, "fstype": fs,
                "reason": f"{path} is a folder, not a Btrfs subvolume. Create the share folder with "
                          "'btrfs subvolume create' to keep scheduled versions."}
    return {"method": None, "supported": False, "fstype": fs,
            "reason": f"Scheduled versions need ZFS or Btrfs snapshots; {path} is on {fs or 'an unknown filesystem'}. "
                      "Deleted files can still be kept by the recycle bin."}


def samba_params(path):
    """shadow_copy2 parameters for ShareHub's own snapshots."""
    info = detect(path)
    if info["method"] == "zfs":
        return {"shadow:mountpoint": mount_source(path)[1] or path, "shadow:snapdir": ".zfs/snapshot",
                "shadow:format": SAMBA_FORMAT, "shadow:localtime": "no", "shadow:sort": "desc"}
    if info["method"] == "btrfs":
        return {"shadow:mountpoint": os.path.normpath(path), "shadow:snapdir": BTRFS_DIR,
                "shadow:format": SAMBA_FORMAT, "shadow:localtime": "no", "shadow:sort": "desc"}
    return {}


# --------------------------------------------------------------------------- list / create / delete

def _parse(name):
    if not NAME_RE.match(name):
        return None
    return datetime.datetime.strptime(name[len(PREFIX):], TIME_FORMAT).replace(tzinfo=datetime.timezone.utc)


def list_snapshots(path):
    """[{"name", "time"}] newest first."""
    info = detect(path)
    names = []
    if info["method"] == "zfs":
        proc = run(["zfs", "list", "-H", "-t", "snapshot", "-o", "name", "-d", "1", info["dataset"]], check=False)
        names = [line.split("@", 1)[1] for line in proc.stdout.splitlines() if "@" in line]
    elif info["method"] == "btrfs":
        try:
            names = os.listdir(os.path.join(path, BTRFS_DIR))
        except FileNotFoundError:
            names = []
    out = [{"name": n, "time": _parse(n)} for n in names if _parse(n)]
    return sorted(out, key=lambda s: s["time"], reverse=True)


def snapshot_root(path, name):
    """Directory holding the share's content as it was in snapshot `name`."""
    if not NAME_RE.match(name or ""):
        raise HelperError("Invalid version.")
    info = detect(path)
    if info["method"] == "zfs":
        mountpoint = mount_source(path)[1] or path
        rel = os.path.relpath(path, mountpoint)
        return os.path.normpath(os.path.join(mountpoint, ".zfs", "snapshot", name, rel))
    if info["method"] == "btrfs":
        return os.path.join(path, BTRFS_DIR, name)
    raise HelperError("This share has no snapshots.")


def create(path, now=None):
    info = detect(path)
    if not info["supported"]:
        raise HelperError(info["reason"])
    now = now or datetime.datetime.now(datetime.timezone.utc)
    name = PREFIX + now.strftime(TIME_FORMAT)
    if info["method"] == "zfs":
        run(["zfs", "snapshot", f"{info['dataset']}@{name}"])
    else:
        target_dir = os.path.join(path, BTRFS_DIR)
        os.makedirs(target_dir, mode=0o755, exist_ok=True)
        run(["btrfs", "subvolume", "snapshot", "-r", path, os.path.join(target_dir, name)])
    return name


def delete(path, name):
    info = detect(path)
    if not NAME_RE.match(name):
        raise HelperError("Invalid version.")
    if info["method"] == "zfs":
        run(["zfs", "destroy", f"{info['dataset']}@{name}"])
    elif info["method"] == "btrfs":
        run(["btrfs", "subvolume", "delete", os.path.join(path, BTRFS_DIR, name)])


# --------------------------------------------------------------------------- retention

def keep_set(times, policy, now):
    """Which snapshot times to keep (pure function; times are aware datetimes).

    policy: {"keep_frequent", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly"}.
    The newest snapshot is always kept. For each class the newest snapshot of each of the last
    N periods is kept, so one snapshot can count for several classes."""
    ordered = sorted(times, reverse=True)
    keep = set(ordered[:1])
    keep.update(ordered[:policy.get("keep_frequent", 0)])
    buckets = {
        "keep_hourly": lambda t: (t.year, t.month, t.day, t.hour),
        "keep_daily": lambda t: (t.year, t.month, t.day),
        "keep_weekly": lambda t: tuple(t.isocalendar())[:2],
        "keep_monthly": lambda t: (t.year, t.month),
    }
    for key, bucket in buckets.items():
        limit = policy.get(key, 0)
        seen = []
        for t in ordered:
            b = bucket(t)
            if b in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(b)
            keep.add(t)
    return keep


def run_schedule(state, now=None, log=print):
    """Called every 15 minutes by sharehub-snapshot.timer: take due snapshots, prune old ones."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for name, share in state["shares"].items():
        v = share["versioning"]
        if not v["snapshots"]:
            continue
        try:
            snaps = list_snapshots(share["path"])
            interval = datetime.timedelta(minutes=15 if v["frequent"] else 60)
            if not snaps or now - snaps[0]["time"] >= interval - datetime.timedelta(minutes=1):
                created = create(share["path"], now)
                log(f"{name}: created {created}")
                snaps = list_snapshots(share["path"])
            keep = keep_set([s["time"] for s in snaps], v, now)
            for s in snaps:
                if s["time"] not in keep:
                    delete(share["path"], s["name"])
                    log(f"{name}: removed {s['name']} (retention)")
        except HelperError as e:
            log(f"{name}: {e}")


# --------------------------------------------------------------------------- browse / restore

def _safe_rel(rel):
    rel = os.path.normpath("/" + str(rel or "")).lstrip("/")
    if rel.startswith("..") or "\0" in rel:
        raise HelperError("Invalid path.")
    return "" if rel == "." else rel


def browse(root, rel):
    """Entries of root/rel: [{"name", "type", "size", "mtime"}], folders first."""
    rel = _safe_rel(rel)
    base = os.path.join(root, rel)
    if not os.path.isdir(base):
        raise HelperError("Folder not found in this version.")
    out = []
    for entry in os.scandir(base):
        if entry.name in (BTRFS_DIR, RECYCLE_DIR) and not rel:
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        out.append({"name": entry.name, "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                    "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": rel, "entries": out[:5000]}


def _free_name(target, when):
    """target, or 'name (restored YYYY-MM-DD HH.MM).ext' when target exists."""
    if not os.path.lexists(target):
        return target
    stem, ext = os.path.splitext(target)
    if os.path.isdir(target):
        stem, ext = target, ""
    candidate = f"{stem} (restored {when}){ext}"
    i = 2
    while os.path.lexists(candidate):
        candidate = f"{stem} (restored {when} {i}){ext}"
        i += 1
    return candidate


def restore_from_snapshot(share_path, snapshot, rel):
    rel = _safe_rel(rel)
    if not rel:
        raise HelperError("Choose a file or folder to restore.")
    source = os.path.join(snapshot_root(share_path, snapshot), rel)
    if not os.path.lexists(source):
        raise HelperError("That item does not exist in this version.")
    when = _parse(snapshot).astimezone().strftime("%Y-%m-%d %H.%M")
    target = _free_name(os.path.join(share_path, rel), when)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    run(["cp", "-a", "--reflink=auto", source, target])
    return os.path.relpath(target, share_path)


def recycle_items(share_path):
    """Deleted files kept by the recycle bin: [{"id", "path", "user", "deleted", "size"}]."""
    root = os.path.join(share_path, RECYCLE_DIR)
    out = []
    if not os.path.isdir(root):
        return out
    for user in sorted(os.listdir(root)):
        user_root = os.path.join(root, user)
        if not os.path.isdir(user_root):
            continue
        for dirpath, _dirs, files in os.walk(user_root):
            for f in files:
                full = os.path.join(dirpath, f)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, user_root)
                out.append({"id": os.path.relpath(full, root), "path": rel, "user": user,
                            "deleted": int(st.st_mtime), "size": st.st_size})
    out.sort(key=lambda i: i["deleted"], reverse=True)
    return out[:5000]


def restore_from_recycle(share_path, item_id):
    item_id = _safe_rel(item_id)
    parts = item_id.split("/", 1)
    if len(parts) != 2:
        raise HelperError("Invalid item.")
    source = os.path.join(share_path, RECYCLE_DIR, item_id)
    if not os.path.isfile(source):
        raise HelperError("That file is no longer in the recycle bin.")
    rel = parts[1]
    folder, base = os.path.split(rel)
    m = COPY_RE.match(base)
    if m:
        base = m.group(2)
    when = datetime.datetime.fromtimestamp(os.lstat(source).st_mtime).strftime("%Y-%m-%d %H.%M")
    target = _free_name(os.path.join(share_path, folder, base), when)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(source, target)
    return os.path.relpath(target, share_path)


def trim_recycle_versions(share_path, keep):
    """Keep at most `keep` versions of each deleted file (current name + 'Copy #N of name')."""
    root = os.path.join(share_path, RECYCLE_DIR)
    removed = 0
    if keep <= 0 or not os.path.isdir(root):
        return 0
    for dirpath, _dirs, files in os.walk(root):
        groups = {}
        for f in files:
            m = COPY_RE.match(f)
            groups.setdefault(m.group(2) if m else f, []).append(f)
        for versions in groups.values():
            if len(versions) <= keep:
                continue
            versions.sort(key=lambda f: os.lstat(os.path.join(dirpath, f)).st_mtime, reverse=True)
            for old in versions[keep:]:
                os.unlink(os.path.join(dirpath, old))
                removed += 1
    return removed
