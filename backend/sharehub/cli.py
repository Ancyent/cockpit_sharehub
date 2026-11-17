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

from . import __version__, ad, ftp, health, migrate, paths, quota, reconcile, samba, services, users
from . import state as statemod
from .util import HelperError, have, read_text, run


# --------------------------------------------------------------------------- read-only

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
        "checks": {
            "samba_installed": have("net") and os.path.exists(samba.SMB_CONF),
            "samba_version": smb_version,
            "registry_enabled": samba.registry_enabled(),
            "file_shares": samba.file_shares(),
            "unmanaged_shares": samba.unmanaged_shares(state) if samba.registry_enabled() else [],
            "nfs_installed": have("exportfs"),
            "proftpd_installed": have("proftpd"),
            "proftpd_include_ok": ftp.include_ok(found) if found else False,
            "proftpd_defaultroot_conflict": "defaultroot" in found,
            "wsdd_installed": os.path.exists("/usr/lib/systemd/system/wsdd-server.service"),
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


def cmd_quota_check(req):
    path = os.path.normpath(str(req.get("path") or ""))
    if not path.startswith("/") or not os.path.isdir(path):
        raise HelperError("Choose an existing folder first.")
    info = quota.detect(path)
    return {k: info.get(k) for k in ("fstype", "method", "supported", "reason", "mountpoint")}


def cmd_health(_req):
    return health.report(statemod.load())


def cmd_install_packages(req):
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
READ_ONLY = {"status", "browse", "quota-check", "health", "history", "ad-status", "ad-preflight", "services",
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
        run(["systemctl", "enable", "--now", "sharehub-recycle.timer"], check=False)
    say("ShareHub bootstrap complete.")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in ("bootstrap", "recycle-cleanup", "--version"):
        if command == "--version":
            print(__version__)
            return 0
        if os.geteuid() != 0:
            print("Run as root.", file=sys.stderr)
            return 1
        if command == "bootstrap":
            bootstrap()
        else:
            print(f"Removed {samba.recycle_cleanup(statemod.load())} expired recycle-bin files.")
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
