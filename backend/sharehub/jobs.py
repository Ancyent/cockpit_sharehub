# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Background jobs for long restores (whole folders from a version or from the recycle bin).

A job runs as its own process at low CPU and I/O priority (a transient systemd unit when
systemd is available), writes its progress to /run/sharehub/jobs/<id>.json and stops early
when /run/sharehub/jobs/<id>.cancel appears. Single files are restored directly, not here."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import backports, snapshots
from .util import HelperError, atomic_write, has_systemd, read_text, run as run_cmd

JOBS_DIR = "/run/sharehub/jobs"
ID_RE = re.compile(r"^[0-9a-f]{12}$")
KEEP_FINISHED = 3600  # seconds a finished job stays listed
PROGRESS_EVERY = 1.0


def _path(job_id, ext="json"):
    if not ID_RE.match(job_id or ""):
        raise HelperError("Invalid job.")
    return os.path.join(JOBS_DIR, f"{job_id}.{ext}")


def _write(job):
    os.makedirs(JOBS_DIR, mode=0o700, exist_ok=True)
    atomic_write(_path(job["id"]), json.dumps(job) + "\n", 0o600)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def start(kind, share, label, params, helper=None):
    """Create a job and launch its worker; returns the job record."""
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "share": share, "label": label, "params": params,
           "state": "queued", "files": 0, "bytes": 0, "total_files": None, "total_bytes": None,
           "started": _now(), "finished": None, "result": None, "error": None}
    _write(job)
    helper = helper or os.path.abspath(sys.argv[0])
    if has_systemd() and shutil.which("systemd-run"):
        cmd = ["systemd-run", f"--unit=sharehub-job-{job['id']}", "--collect", "--quiet",
               "-p", "Nice=10", "-p", "IOSchedulingClass=idle", helper, "job-run", job["id"]]
    else:
        cmd = [helper, "job-run", job["id"]]
        if shutil.which("ionice"):
            cmd = ["ionice", "-c3"] + cmd
        if shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return public(job)


def public(job):
    return {k: v for k, v in job.items() if k != "params"}


def load(job_id):
    raw = read_text(_path(job_id))
    if raw is None:
        raise HelperError("Job not found.")
    return json.loads(raw)


def listing():
    """All jobs, newest first; finished jobs are forgotten after KEEP_FINISHED seconds."""
    out = []
    if not os.path.isdir(JOBS_DIR):
        return out
    now = time.time()
    for entry in os.listdir(JOBS_DIR):
        if not entry.endswith(".json"):
            continue
        full = os.path.join(JOBS_DIR, entry)
        try:
            job = json.loads(read_text(full) or "{}")
        except ValueError:
            continue
        if job.get("state") in ("done", "failed", "cancelled") and now - os.path.getmtime(full) > KEEP_FINISHED:
            for ext in ("json", "cancel"):
                try:
                    os.unlink(os.path.join(JOBS_DIR, f"{job['id']}.{ext}"))
                except OSError:
                    pass
            continue
        out.append(public(job))
    return sorted(out, key=lambda j: j["started"], reverse=True)


def cancel(job_id):
    job = load(job_id)
    if job["state"] in ("queued", "running"):
        with open(_path(job_id, "cancel"), "w"):
            pass
    return public(job)


# --------------------------------------------------------------------------- worker

class Cancelled(Exception):
    pass


class Progress:
    def __init__(self, job):
        self.job = job
        self.last = 0.0
        self.cancel_flag = _path(job["id"], "cancel")

    def tick(self, files=0, nbytes=0, force=False):
        self.job["files"] += files
        self.job["bytes"] += nbytes
        now = time.monotonic()
        if force or now - self.last >= PROGRESS_EVERY:
            self.last = now
            if os.path.exists(self.cancel_flag):
                raise Cancelled()
            _write(self.job)


def _count(root, progress):
    files = size = 0
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    files += 1
                    try:
                        size += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        progress.tick()
    return files, size


def _copy_data(src, dst):
    """Copy file contents; copy_file_range lets Btrfs/XFS/ZFS clone blocks instead of copying."""
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        size = os.fstat(fin.fileno()).st_size
        copied = 0
        try:
            while copied < size:
                n = os.copy_file_range(fin.fileno(), fout.fileno(), size - copied)
                if n == 0:
                    break
                copied += n
        except OSError:
            fin.seek(copied)
            fout.seek(copied)
            shutil.copyfileobj(fin, fout, 1024 * 1024)
    return size


def _copy_meta(src, dst):
    st = os.lstat(src)
    try:
        os.lchown(dst, st.st_uid, st.st_gid)
    except OSError:
        pass
    shutil.copystat(src, dst, follow_symlinks=False)  # mode, times, xattrs incl. POSIX ACLs


def copy_tree(src, dst, progress):
    """cp -a equivalent with progress and cancel: data, owner, mode, times, xattrs/ACLs, symlinks."""
    os.mkdir(dst)
    dirs = [(src, dst)]
    stack = [(src, dst)]
    while stack:
        s_dir, d_dir = stack.pop()
        with os.scandir(s_dir) as it:
            for e in it:
                s, d = e.path, os.path.join(d_dir, e.name)
                if e.is_symlink():
                    os.symlink(os.readlink(s), d)
                    _copy_meta(s, d)
                    progress.tick(files=1)
                elif e.is_dir(follow_symlinks=False):
                    os.mkdir(d)
                    dirs.append((s, d))
                    stack.append((s, d))
                elif e.is_file(follow_symlinks=False):
                    size = _copy_data(s, d)
                    _copy_meta(s, d)
                    progress.tick(files=1, nbytes=size)
    for s, d in reversed(dirs):  # directory times last, after their content was written
        _copy_meta(s, d)


def restore_recycle_tree(share_path, rel, progress):
    """Put every file below .recycle/<rel> back where it was (never overwriting)."""
    root = snapshots.recycle_root(share_path)
    user, _, sub = rel.partition("/")
    base = os.path.join(root, rel)
    restored = 0
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            source = os.path.join(dirpath, f)
            inside = os.path.relpath(source, os.path.join(root, user))
            when = datetime.datetime.fromtimestamp(os.lstat(source).st_mtime).strftime("%Y-%m-%d %H.%M")
            target = snapshots.recycle_target(share_path, inside, when)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            size = os.lstat(source).st_size
            shutil.move(source, target)
            restored += 1
            progress.tick(files=1, nbytes=size)
    for dirpath, _dirs, _files in os.walk(base, topdown=False):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return restored


def install_backports(job, packages, progress):
    """Enable backports if needed, then apt-get install -t <codename>-backports. Runs outside the
    Cockpit session, so it finishes even when apt updates Cockpit and the page reconnects."""
    suite = backports.suite()

    def step(text):
        job["step"] = text
        progress.tick(force=True)

    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if not backports.enabled(suite):
        step(f"Enabling {suite}…")
        backports.enable()
    step("Updating package lists…")
    proc = run_cmd(["apt-get", "update", "-q"], check=False, env=env, timeout=900)
    if proc.returncode != 0:
        raise HelperError("apt-get update failed: " + (proc.stderr or proc.stdout).strip()[-500:])
    step(f"Installing {' '.join(packages)} from {suite}…")
    proc = run_cmd(["apt-get", "install", "-y", "-q", "-t", suite, "-o", "Dpkg::Options::=--force-confold", *packages],
                   check=False, env=env, timeout=1800)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if "Could not get lock" in detail or "is locked" in detail:
            raise HelperError("Another package operation is running. Try again in a few minutes.")
        raise HelperError("Installation failed: " + detail[-600:])
    step("Installed.")
    return packages


def run(job_id):
    """Entry point of the worker process (sharehub-helper job-run <id>)."""
    job = load(job_id)
    progress = Progress(job)
    p = job["params"]
    job["state"] = "running"
    progress.tick(force=True)
    try:
        if job["kind"] == "restore-snapshot":
            source = os.path.join(snapshots.snapshot_root(p["share_path"], p["snapshot"]), p["path"])
            job["total_files"], job["total_bytes"] = _count(source, progress)
            progress.tick(force=True)
            when = snapshots._parse(p["snapshot"]).astimezone().strftime("%Y-%m-%d %H.%M")
            target = snapshots._free_name(os.path.join(p["share_path"], p["path"]), when)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            job["result"] = os.path.relpath(target, p["share_path"])
            copy_tree(source, target, progress)
        elif job["kind"] == "install":
            job["result"] = install_backports(job, p["packages"], progress)
        elif job["kind"] == "restore-recycle":
            source = os.path.join(snapshots.recycle_root(p["share_path"]), p["path"])
            job["total_files"], job["total_bytes"] = _count(source, progress)
            progress.tick(force=True)
            job["result"] = restore_recycle_tree(p["share_path"], p["path"], progress)
        else:
            raise HelperError(f"Unknown job kind {job['kind']}")
        job["state"] = "done"
    except Cancelled:
        job["state"] = "cancelled"
    except Exception as e:  # noqa: BLE001 - report every failure to the UI
        job["state"], job["error"] = "failed", str(e)
    job["finished"] = _now()
    _write(job)
    return 0 if job["state"] == "done" else 1
