# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Command dispatcher used by the Cockpit page.

    sharehub-helper <command>        JSON request on stdin, JSON reply on stdout:
                                     {"ok": true, "data": ...} or {"ok": false, "error": "..."}
    sharehub-helper recycle-cleanup  plain-text mode, used by the systemd timer
    sharehub-helper bootstrap        one-time system setup, used by the package
"""
import copy
import fcntl
import json
import os
import re
import sys

from . import __version__, ad, backports, ftp, health, jobs, migrate, paths, quota, reconcile, samba, services, snapshots, users
from . import features as featmod
from . import state as statemod
from .util import HelperError, have, nfs_server_support, read_text, run, wsdd_unit


# --------------------------------------------------------------------------- read-only

def _feature_states(state):
    try:
        return featmod.states(state, health.report(state, light=True))
    except Exception as e:  # noqa: BLE001 - the page must load even if a check breaks
        return {"_error": {"state": "ready", "reason": str(e)}}


def cmd_status(_req):
    state = statemod.load()
    user_list, group_list = users.listing(state)
    found = ftp.directives() if have("proftpd") else {}
    smb_version = run(["smbd", "--version"], check=False).stdout.strip() if have("smbd") else ""
    return {
        "version": __version__,
        "state": state,
        "users": user_list,
        "groups": group_list,
        "ftp_root": paths.FTP_ROOT,
        "ftp_mounts": sorted(ftp.managed_mounts()),
        "quota_usage": quota.usage(state),
        "versions": _versions_summary(state),
        "features": _feature_states(state),
        "jobs": jobs.listing(),
        "checks": {
            "samba_installed": have("net") and os.path.exists(samba.SMB_CONF),
            "samba_version": smb_version,
            "registry_enabled": samba.registry_enabled(),
            "file_shares": samba.file_shares(),
            "unmanaged_shares": samba.unmanaged_shares(state) if samba.registry_enabled() else [],
            "nfs_installed": have("exportfs"),
            "nfs_supported": nfs_server_support()[0],
            "nfs_unsupported_reason": nfs_server_support()[1],
            "proftpd_installed": have("proftpd"),
            "proftpd_include_ok": ftp.include_ok(found) if found else False,
            "proftpd_defaultroot_conflict": "defaultroot" in found,
            "wsdd_installed": bool(wsdd_unit()) or any(os.path.exists(f"/usr/lib/systemd/system/{u}.service") for u in ("wsdd-server", "wsdd2")),
            "avahi_installed": have("avahi-daemon"),
            "ad_missing_packages": ad.prerequisites(),
            "sanoid_installed": have("sanoid"),
            "zfs_auto_snapshot_installed": have("zfs-auto-snapshot"),
            "snapper_installed": have("snapper"),
        },
    }


def cmd_browse(req):
    path = os.path.normpath(str(req.get("path") or "/"))
    if not path.startswith("/"):
        raise HelperError("Path must be absolute.")
    while not os.path.isdir(path) and path != "/":
        path = os.path.dirname(path)
    dirs = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.name.startswith(".") and not req.get("hidden"):
                continue
            if entry.is_dir(follow_symlinks=False):
                dirs.append(entry.name)
    except PermissionError:
        raise HelperError(f"Cannot read {path}.")
    return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": dirs[:2000]}


def cmd_mkdir(req):
    path = os.path.normpath(str(req.get("path") or ""))
    if not path.startswith("/") or path == "/" or any(c in path for c in "\n\r\"\\"):
        raise HelperError("Enter an absolute path for the new folder.")
    if os.path.exists(path):
        raise HelperError(f"{path} already exists.")
    mode = str(req.get("mode") or "2770")
    if not re.match(r"^[0-7]{3,4}$", mode):
        raise HelperError("Permissions must be octal, e.g. 2770.")
    owner = str(req.get("owner") or "root")
    group = str(req.get("group") or "root")
    os.makedirs(path)
    run(["chown", f"{owner}:{group}", path])
    run(["chmod", mode, path])
    return {"path": path}


def _versions_summary(state):
    out = {}
    for name, share in state["shares"].items():
        if not share["versioning"]["snapshots"]:
            continue
        try:
            snaps = snapshots.list_snapshots(share["path"])
        except Exception:  # noqa: BLE001 - a broken share must not break the status page
            snaps = []
        out[name] = {"count": len(snaps), "latest": snaps[0]["time"].isoformat() if snaps else None}
    return out


def _share(req):
    st = statemod.load()
    name = statemod.find_share(st, req.get("name") or "")
    if not name:
        raise HelperError("Share not found.")
    return name, st["shares"][name]


def cmd_versions_check(req):
    path = os.path.normpath(str(req.get("path") or ""))
    if not path.startswith("/") or not os.path.isdir(path):
        raise HelperError("Choose an existing folder first.")
    info = snapshots.detect(path)
    return {k: info.get(k) for k in ("method", "supported", "reason", "fstype", "kind", "install")}


def cmd_versions_list(req):
    name, share = _share(req)
    snaps = snapshots.list_snapshots(share["path"]) if share["versioning"]["snapshots"] or \
        snapshots.detect(share["path"])["supported"] else []
    return {"name": name, "snapshots": [{"name": s["name"], "time": s["time"].isoformat()} for s in snaps],
            "versioning": share["versioning"], "recycle_enabled": share["smb"]["enabled"] and share["smb"]["recycle"],
            "recycle_exists": os.path.isdir(snapshots.recycle_root(share["path"]))}


def _browse_root(share, req):
    """(root, hide) for a request on a version (snapshot) or on the recycle bin (no snapshot)."""
    if req.get("snapshot"):
        return snapshots.snapshot_root(share["path"], req.get("snapshot")), (snapshots.BTRFS_DIR, snapshots.RECYCLE_DIR)
    root = snapshots.recycle_root(share["path"])
    if not os.path.isdir(root):
        raise HelperError("The recycle bin is empty.")
    return root, ()


def cmd_versions_browse(req):
    _name, share = _share(req)
    root, hide = _browse_root(share, req)
    page = snapshots.browse(root, req.get("path"), req.get("offset"), req.get("limit"), req.get("query"), hide)
    page["root"] = root   # absolute folder, e.g. to open it in Cockpit Files
    return page


def cmd_versions_search(req):
    _name, share = _share(req)
    root, hide = _browse_root(share, req)
    return snapshots.search(root, req.get("path"), req.get("query"), hide)


def cmd_versions_history(req):
    _name, share = _share(req)
    return snapshots.file_history(share["path"], req.get("path"))


def cmd_versions_restore(req):
    """Files are restored at once; folders start a background job and return {"job": ...}."""
    name, share = _share(req)
    rel = snapshots._safe_rel(req.get("path") or req.get("item"))
    if req.get("snapshot"):
        source = os.path.join(snapshots.snapshot_root(share["path"], req.get("snapshot")), rel)
        if os.path.isdir(source) and not os.path.islink(source):
            job = jobs.start("restore-snapshot", name, rel, {"share_path": share["path"], "snapshot": req.get("snapshot"), "path": rel})
            return {"job": job, "warnings": []}
        restored = snapshots.restore_from_snapshot(share["path"], req.get("snapshot"), rel)
    else:
        source = os.path.join(snapshots.recycle_root(share["path"]), rel)
        if os.path.isdir(source):
            if "/" not in rel:
                raise HelperError("Open a user's folder and restore the files or folders inside it.")
            job = jobs.start("restore-recycle", name, rel, {"share_path": share["path"], "path": rel})
            return {"job": job, "warnings": []}
        restored = snapshots.restore_from_recycle(share["path"], rel)
    return {"restored": restored, "warnings": []}


def cmd_install_preview(req):
    """What installing a backports package would change, for the confirmation dialog."""
    packages = [p for p in (req.get("packages") or []) if p in backports.PACKAGES]
    if not packages:
        return {"backports": False}
    name = backports.suite()
    if not name:
        raise HelperError("Backports are only supported on Debian and Ubuntu.")
    if not backports.enabled(name):
        return {"backports": True, "suite": name, "enable": True, "distro": backports.distro(), "changes": []}
    return {"backports": True, "suite": name, "enable": False, "distro": backports.distro(),
            "changes": backports.preview(packages)}


def cmd_jobs(_req):
    return {"jobs": jobs.listing()}


def cmd_job_cancel(req):
    return {"job": jobs.cancel(req.get("id")), "warnings": []}


def cmd_features_set(req):
    st = statemod.load()
    new = copy.deepcopy(st)
    warnings = []
    featmod.apply_choice(new, req.get("features") or {}, warnings)
    warnings += reconcile.commit(new, "features")
    featmod.sync_units(st, new, warnings, installed=lambda u: health._service(u)["installed"])
    return {"warnings": _relevant(warnings, new, "")}


def cmd_versions_create(req):
    name, share = _share(req)
    created = snapshots.create(share["path"])
    return {"created": created, "warnings": []}


def cmd_quota_check(req):
    path = os.path.normpath(str(req.get("path") or ""))
    if not path.startswith("/") or not os.path.isdir(path):
        raise HelperError("Choose an existing folder first.")
    info = quota.detect(path)
    return {k: info.get(k) for k in ("fstype", "method", "supported", "reason", "mountpoint", "kind", "install")}


def cmd_health(_req):
    return health.report(statemod.load())


def cmd_install_packages(req):
    packages = req.get("packages") or []
    if isinstance(packages, list) and set(packages) & backports.PACKAGES:
        bad = [p for p in packages if p not in backports.PACKAGES]
        if bad:
            raise HelperError("Install backports packages on their own.")
        if not backports.suite():
            raise HelperError("Backports are only supported on Debian and Ubuntu.")
        # in the background: apt may update Cockpit itself, which ends this session
        job = jobs.start("install", "", " ".join(packages), {"packages": packages})
        return {"job": job, "warnings": []}
    result = health.install(req)
    warnings = []
    bootstrap(quiet=True)
    services.apply_discovery(statemod.load(), warnings)
    return {**result, "warnings": warnings}


def cmd_health_fix(req):
    warnings = []
    return {**health.fix(req, warnings), "warnings": warnings}


def cmd_history(_req):
    return {"entries": statemod.history()}


def cmd_connections(_req):
    return services.connections()


def cmd_services(_req):
    return services.status()


def cmd_firewall_status(_req):
    return services.firewall_status(statemod.load())


def cmd_ad_status(_req):
    return ad.status(statemod.load())


def cmd_ad_preflight(req):
    realm = str(req.get("realm") or "").strip().lower()
    if not ad.REALM_RE.match(realm):
        raise HelperError("Domain must be a DNS name such as corp.example.com.")
    return {"checks": ad.preflight(realm)}


def cmd_migrate_detect(_req):
    return migrate.detect(statemod.load())


# --------------------------------------------------------------------------- changes

def cmd_enable_registry(_req):
    return {"changed": samba.enable_registry()}


def cmd_share_save(req):
    st = statemod.load()
    new = copy.deepcopy(st)
    name = req.get("name")
    original = req.get("original_name")
    statemod.validate_share_name(name)
    existing = statemod.find_share(st, name)
    if original:
        orig = statemod.find_share(st, original)
        if not orig:
            raise HelperError(f"Share '{original}' not found.")
        if existing and existing != orig:
            raise HelperError(f"A share named '{existing}' already exists.")
        del new["shares"][orig]
    elif existing:
        raise HelperError(f"A share named '{existing}' already exists.")
    share = statemod.normalize_share(req.get("share") or {}, paths.FTP_ROOT)
    featmod.check_share(st, name, share)
    clash = [n for n, s in new["shares"].items() if os.path.normpath(s["path"]) == share["path"]
             and s["nfs"]["enabled"] and share["nfs"]["enabled"]]
    if clash:
        raise HelperError(f"Path {share['path']} is already exported over NFS by '{clash[0]}'.")
    new["shares"][name] = share
    warnings = reconcile.commit(new, f"share-{name}")
    return {"name": name, "warnings": _relevant(warnings, new, name)}


def _relevant(warnings, st, name):
    """Drop per-share notes about other shares so saving one share is not noisy."""
    others = [n for n in st["shares"] if n != name]
    return [w for w in warnings if not any(w.startswith(f"{o}: ") for o in others)]


def cmd_share_delete(req):
    st = statemod.load()
    name = statemod.find_share(st, req.get("name") or "")
    if not name:
        raise HelperError("Share not found.")
    new = copy.deepcopy(st)
    del new["shares"][name]
    return {"warnings": _relevant(reconcile.commit(new, f"delete-{name}"), new, "")}


def cmd_settings_save(req):
    section = req.get("section")
    if section not in ("samba", "nfs", "ftp"):
        raise HelperError("Unknown settings section.")
    st = statemod.load()
    new = copy.deepcopy(st)
    new[section] = statemod.normalize_settings(section, req.get("data") or {})
    return {"warnings": _relevant(reconcile.commit(new, f"settings-{section}"), new, "")}


def cmd_apply(_req):
    st = statemod.load()
    return {"warnings": reconcile.commit(st, "re-apply")}


def cmd_history_restore(req):
    target = statemod.load_history(req.get("id"))
    for name, share in list(target["shares"].items()):
        if not os.path.isdir(share["path"]):
            raise HelperError(f"Cannot restore: path of '{name}' ({share['path']}) no longer exists.")
    return {"warnings": reconcile.commit(target, "restore")}


def _user_cmd(fn):
    def wrapper(req):
        return {**fn(req), "warnings": []}
    return wrapper


def cmd_group_delete(req):
    return {**users.delete_group(req, statemod.load()), "warnings": []}


def _samba_only_apply(st, warnings):
    samba.apply(st, warnings)


def cmd_ad_join(req):
    st = statemod.load()
    new = copy.deepcopy(st)
    warnings = []
    new = ad.join(req, new, _samba_only_apply, warnings)
    reconcile.apply(new, warnings)
    statemod.save(new, "ad-join")
    return {"warnings": warnings}


def cmd_ad_leave(req):
    st = statemod.load()
    new = copy.deepcopy(st)
    warnings = []
    new = ad.leave(req, new, _samba_only_apply, warnings)
    reconcile.apply(new, warnings)
    statemod.save(new, "ad-leave")
    return {"warnings": warnings}


def cmd_migrate_import(req):
    st = statemod.load()
    new = copy.deepcopy(st)
    warnings = []
    imported, moved = migrate.run_import(req, new, warnings)
    if not imported and not any((new[k] != st[k]) for k in ("samba", "ftp")):
        raise HelperError("Nothing was selected to import.")
    for name in imported:
        new["shares"][name] = statemod.normalize_share(new["shares"][name], paths.FTP_ROOT)
    removed = migrate.cleanup_after_import(moved, warnings)
    warnings += reconcile.commit(new, "import")
    return {"imported": imported, "removed": removed, "warnings": warnings}


def cmd_service_control(req):
    return services.control(req)


def cmd_disconnect(req):
    return services.disconnect(req)


def cmd_firewall_open(_req):
    return services.firewall_open(statemod.load())


COMMANDS = {
    "status": cmd_status,
    "browse": cmd_browse,
    "quota-check": cmd_quota_check,
    "versions-check": cmd_versions_check,
    "versions-list": cmd_versions_list,
    "versions-browse": cmd_versions_browse,
    "versions-restore": cmd_versions_restore,
    "versions-create": cmd_versions_create,
    "versions-search": cmd_versions_search,
    "versions-history": cmd_versions_history,
    "jobs": cmd_jobs,
    "install-preview": cmd_install_preview,
    "job-cancel": cmd_job_cancel,
    "features-set": cmd_features_set,
    "health": cmd_health,
    "install-packages": cmd_install_packages,
    "health-fix": cmd_health_fix,
    "mkdir": cmd_mkdir,
    "history": cmd_history,
    "history-restore": cmd_history_restore,
    "apply": cmd_apply,
    "enable-registry": cmd_enable_registry,
    "share-save": cmd_share_save,
    "share-delete": cmd_share_delete,
    "settings-save": cmd_settings_save,
    "user-create": _user_cmd(users.create_user),
    "user-update": _user_cmd(users.update_user),
    "user-password": _user_cmd(users.set_password),
    "user-smb": _user_cmd(users.smb_account),
    "group-create": _user_cmd(users.create_group),
    "group-update": _user_cmd(users.update_group),
    "group-delete": cmd_group_delete,
    "ad-status": cmd_ad_status,
    "ad-preflight": cmd_ad_preflight,
    "ad-join": cmd_ad_join,
    "ad-leave": cmd_ad_leave,
    "services": cmd_services,
    "service-control": cmd_service_control,
    "connections": cmd_connections,
    "disconnect": cmd_disconnect,
    "firewall-status": cmd_firewall_status,
    "firewall-open": cmd_firewall_open,
    "migrate-detect": cmd_migrate_detect,
    "migrate-import": cmd_migrate_import,
}
READ_ONLY = {"status", "browse", "quota-check", "health", "versions-check", "versions-list", "versions-browse",
             "versions-search", "versions-history", "jobs", "install-preview", "history", "ad-status", "ad-preflight", "services",
             "connections", "firewall-status", "migrate-detect"}


# --------------------------------------------------------------------------- plain-text commands

def bootstrap(quiet=False):
    """System preparation shared by the .deb postinst, install.sh and the Components page."""
    say = (lambda *_: None) if quiet else print
    os.makedirs(paths.STATE_DIR, mode=0o755, exist_ok=True)
    conf = read_text(ftp.PROFTPD_CONF)
    if conf is not None:
        os.makedirs(ftp.PROFTPD_CONF_D, exist_ok=True)
        if not ftp.include_ok(ftp.directives()):
            with open(ftp.PROFTPD_CONF, "a") as f:
                f.write(f"\n# Added by ShareHub\nInclude {ftp.PROFTPD_CONF_D}/\n")
            say(f"Added 'Include {ftp.PROFTPD_CONF_D}/' to {ftp.PROFTPD_CONF}")
        modules = read_text("/etc/proftpd/modules.conf")
        if modules and any(os.path.exists(p) for p in ("/usr/lib/proftpd/mod_tls.so",)):
            new = re.sub(r"^#\s*LoadModule mod_tls\.c\s*$", "LoadModule mod_tls.c", modules, flags=re.M)
            if new != modules:
                with open("/etc/proftpd/modules.conf", "w") as f:
                    f.write(new)
                say("Enabled mod_tls in /etc/proftpd/modules.conf")
    if os.path.isdir("/run/systemd/system"):
        st = statemod.load()
        timers = [t for fid, t in (("recycle", "sharehub-recycle.timer"), ("snapshots", "sharehub-snapshot.timer"))
                  if featmod.enabled(st, fid)]
        if timers:
            run(["systemctl", "enable", "--now", *timers], check=False)
    say("ShareHub bootstrap complete.")


def features_init(csv):
    """install.sh --features: record the chosen features (no services touched yet)."""
    wanted = {f.strip() for f in csv.split(",") if f.strip()}
    unknown = wanted - set(featmod.TOGGLES) - {"smb"}
    if unknown:
        print(f"Unknown features: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    st = statemod.load()
    try:
        featmod.apply_choice(st, {fid: fid in wanted for fid in featmod.TOGGLES}, [])
    except HelperError as e:
        print(f"Features not changed: {e}", file=sys.stderr)
        return 1
    statemod.save(st, "features")
    print("Features: " + ", ".join(sorted(wanted | {"smb"})))
    return 0


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "job-run" and len(sys.argv) > 2:
        if os.geteuid() != 0:
            print("Run as root.", file=sys.stderr)
            return 1
        return jobs.run(sys.argv[2])
    if command == "features-init" and len(sys.argv) > 2:
        if os.geteuid() != 0:
            print("Run as root.", file=sys.stderr)
            return 1
        return features_init(sys.argv[2])
    if command in ("bootstrap", "recycle-cleanup", "snapshot-run", "check", "--version"):
        if command == "--version":
            print(__version__)
            return 0
        if os.geteuid() != 0:
            print("Run as root.", file=sys.stderr)
            return 1
        if command == "bootstrap":
            bootstrap()
        elif command == "snapshot-run":
            snapshots.run_schedule(statemod.load())
        elif command == "check":
            try:
                print(health.text_report(statemod.load()))
            except Exception as e:  # noqa: BLE001 - never break package installation
                print(f"Compatibility report unavailable: {e}")
        else:
            print(f"Removed {samba.recycle_cleanup(statemod.load())} recycle-bin files (expired or over the version limit).")
        return 0

    try:
        if os.geteuid() != 0:
            raise HelperError("Administrative access is required.")
        if command not in COMMANDS:
            raise HelperError(f"Unknown command: {command!r}")
        raw = sys.stdin.read()
        request = json.loads(raw) if raw.strip() else {}
        os.makedirs(os.path.dirname(paths.LOCK_FILE), exist_ok=True)
        with open(paths.LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH if command in READ_ONLY else fcntl.LOCK_EX)
            reply = {"ok": True, "data": COMMANDS[command](request)}
    except HelperError as e:
        reply = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface anything unexpected to the UI
        reply = {"ok": False, "error": f"Unexpected error: {e!r}"}
    print(json.dumps(reply))
    return 0
