# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Components dashboard: for every feature, the packages, services and configuration it
depends on, whether it is in use, and what can be fixed from the UI."""
import glob
import os
import re

from . import backports
from . import features as featmod
from . import ftp, quota, samba, snapshots
from .util import (HelperError, container_type, has_systemd, have, nfs_server_support, read_text, run,
                   systemctl, unit_exists, unit_state, wsdd_unit)

# Packages the UI may install. Anything else is refused.
INSTALLABLE = {
    "samba", "samba-common-bin", "smbclient", "nfs-kernel-server", "proftpd-core", "proftpd-basic",
    "proftpd-mod-crypto", "ssl-cert", "avahi-daemon", "wsdd-server", "wsdd2", "winbind", "libnss-winbind",
    "libpam-winbind", "krb5-user", "sanoid", "zfs-auto-snapshot", "snapper", "quota", "xfsprogs",
    "btrfs-progs", "zfsutils-linux", "acl", "attr", "ufw", "cockpit-files",
}


def package_info(names):
    """{pkg: {"installed": version|None, "candidate": version|None}} using dpkg and apt."""
    info = {n: {"installed": None, "candidate": None} for n in names}
    if have("dpkg-query"):
        proc = run(["dpkg-query", "-W", "-f=${Package}|${db:Status-Status}|${Version}\n", *names], check=False)
        for line in proc.stdout.splitlines():
            pkg, status, version = (line.split("|") + ["", ""])[:3]
            if pkg in info and status == "installed":
                info[pkg]["installed"] = version
    if have("apt-cache"):
        proc = run(["apt-cache", "policy", *names], check=False)
        current = None
        for line in proc.stdout.splitlines():
            if not line.startswith(" ") and line.endswith(":"):
                current = line[:-1]
            elif current in info and line.strip().startswith("Candidate:"):
                cand = line.split(":", 1)[1].strip()
                info[current]["candidate"] = None if cand == "(none)" else cand
    return info


def _service(unit):
    if not has_systemd():
        return {"unit": unit, "installed": None, "active": None, "enabled": None}
    installed = unit_exists(f"{unit}.service") or unit_exists(unit)
    active, enabled = unit_state(unit) if installed else (None, None)
    return {"unit": unit, "installed": installed, "active": active, "enabled": enabled}


def _mod_tls_ok():
    modules = read_text("/etc/proftpd/modules.conf") or ""
    loaded = re.search(r"^\s*LoadModule\s+mod_tls\.c", modules, re.M) is not None
    return loaded and bool(glob.glob("/usr/lib/proftpd/mod_tls.so"))


FS_TOOLS = {"zfs": "zfsutils-linux", "btrfs": "btrfs-progs", "xfs": "xfsprogs", "ext4": "quota"}


def fs_evaluation(state, fid):
    """Check quota or snapshot support on the real share folders, grouped by filesystem.

    Returns {"packages", "kind", "reason", "checks"}: kind is the best case over all folders
    (ready > needs_install > needs_setup > impossible)."""
    detector = quota.detect if fid == "quota" else snapshots.detect
    paths = {}
    for name, share in state["shares"].items():
        if os.path.isdir(share["path"]):
            paths.setdefault(share["path"], []).append(name)
    if not paths:  # no shares yet: judge the places shares usually live
        for candidate in ("/srv", "/"):
            if os.path.isdir(candidate):
                paths[candidate] = []
                break
    groups = {}
    for path, names in paths.items():
        try:
            info = detector(path)
        except Exception as e:  # noqa: BLE001 - one odd folder must not break the dashboard
            info = {"fstype": "", "kind": "impossible", "reason": str(e), "install": None}
        key = (info.get("fstype") or "unknown", info["kind"], info.get("install"))
        group = groups.setdefault(key, {"names": [], "reason": info.get("reason"), "method": info.get("method")})
        group["names"].extend(names)
    rank = {"ready": 0, "needs_install": 1, "needs_setup": 2, "impossible": 3}
    ordered = sorted(groups.items(), key=lambda kv: rank[kv[0][1]])
    if not ordered:
        return {"packages": [], "kind": "impossible", "reason": "No folder to check.", "checks": []}
    (best_fs, kind, _install), best = ordered[0]
    reason = best["reason"]
    if kind == "impossible" and not container_type():
        # Not possible for today's folders, but a share on a suitable filesystem would work.
        kind = "needs_setup"
        need = "a ZFS dataset or a Btrfs subvolume" if fid == "snapshots" else "ZFS, XFS, ext4 (with project quotas) or Btrfs"
        reason = f"None of the share folders supports this ({best_fs or 'unknown filesystem'}). Put shares on {need}."
    packages = []
    for (fs, k, install), _group in ordered:
        pkg = install or (FS_TOOLS.get(fs) if k == "ready" else None)
        if pkg and [pkg] not in packages:
            packages.append([pkg])
    checks = []
    for (fs, k, _install), group in ordered:
        names = sorted(group["names"])
        who = (f"{len(names)} share{'s' if len(names) != 1 else ''}: {', '.join(names[:4])}"
               + (f" +{len(names) - 4}" if len(names) > 4 else "")) if names else "no shares yet"
        checks.append({"label": f"{fs} ({who})", "ok": k == "ready", "fix": None,
                       "detail": "Supported." if k == "ready" else group["reason"]})
    return {"packages": packages, "kind": kind, "reason": reason, "checks": checks}


def features(state):
    """Static description of each feature, evaluated against the current state."""
    shares = state["shares"].values()
    s = state["samba"]
    uses = {
        "smb": any(x["smb"]["enabled"] for x in shares) or s["homes"]["enabled"],
        "nfs": any(x["nfs"]["enabled"] for x in shares),
        "ftp": any(x["ftp"]["enabled"] for x in shares) or state["ftp"]["homes"],
        "ftps": state["ftp"]["tls_required"],
        "mdns": s["mdns"],
        "wsdd": s["wsdd"],
        "netbios": s["netbios"],
        "ad": state["ad"]["enabled"],
        "versions": any(x["smb"]["enabled"] and x["smb"]["shadow_copies"] for x in shares),
        "quota": any(x["quota"]["limit"] for x in shares),
        "recycle": any(x["smb"]["enabled"] and x["smb"]["recycle"] for x in shares),
        "snapshots": any(x["versioning"]["snapshots"] for x in shares),
    }
    return [
        {"id": "smb", "label": "SMB file server", "core": True, "requires": [["samba"], ["samba-common-bin"]],
         "services": ["smbd"], "in_use": uses["smb"]},
        {"id": "netbios", "label": "NetBIOS name service", "requires": [["samba"]],
         "services": ["nmbd"], "in_use": uses["netbios"] and uses["smb"]},
        {"id": "nfs", "label": "NFS server", "requires": [["nfs-kernel-server"]],
         "services": ["nfs-server"], "in_use": uses["nfs"]},
        {"id": "ftp", "label": "FTP server", "requires": [["proftpd-core", "proftpd-basic"]],
         "services": ["proftpd"], "in_use": uses["ftp"]},
        {"id": "ftps", "label": "FTPS (TLS for FTP)", "requires": [["proftpd-mod-crypto", "proftpd-basic"], ["ssl-cert"]],
         "services": [], "in_use": uses["ftps"] and uses["ftp"]},
        {"id": "mdns", "label": "macOS discovery (Bonjour)", "requires": [["avahi-daemon"]],
         "services": ["avahi-daemon"], "in_use": uses["mdns"] and uses["smb"]},
        {"id": "wsdd", "label": "Windows discovery (WS-Discovery)", "requires": [["wsdd-server", "wsdd2"]],
         "services": [wsdd_unit() or "wsdd-server"], "in_use": uses["wsdd"] and uses["smb"],
         "unavailable_hint": "Neither wsdd-server nor wsdd2 is in this system's package sources. Windows can still "
                             "reach shares as \\\\server\\share; turn off WS-Discovery under Settings → SMB to clear this."},
        {"id": "ad", "label": "Active Directory", "requires": [["winbind"], ["libnss-winbind"], ["libpam-winbind"], ["krb5-user"]],
         "services": ["winbind"], "in_use": uses["ad"]},
        {"id": "versions", "label": "Previous Versions (external snapshots)", "requires": [["sanoid", "zfs-auto-snapshot", "snapper"]],
         "services": [], "in_use": uses["versions"]},
        {"id": "files", "label": "Web file manager (Cockpit Files)", "requires": [["cockpit-files"]],
         "services": [], "in_use": False, "backports": True,
         "note": "Cockpit's own file manager, for administrators: browse, upload, download and edit files after "
                 "logging in to Cockpit. Nothing is exposed outside Cockpit."},
        {"id": "quota", "label": "Size limits", "fs": True, "services": [], "in_use": uses["quota"],
         "note": "Checked on the folders of your shares. Each filesystem needs its own tool: zfsutils-linux (ZFS), "
                 "xfsprogs (XFS), quota (ext4), btrfs-progs (Btrfs)."},
        {"id": "recycle", "label": "Recycle bin cleanup", "requires": [], "services": ["sharehub-recycle.timer"],
         "in_use": uses["recycle"]},
        {"id": "snapshots", "label": "Scheduled versions", "fs": True,
         "services": ["sharehub-snapshot.timer"], "in_use": uses["snapshots"],
         "note": "Read-only versions every 15 minutes or hourly, on ZFS datasets or Btrfs subvolumes. "
                 "Checked on the folders of your shares."},
    ]


def _checks(fid, state):
    """Configuration checks per feature: [{"label", "ok", "detail", "fix"}]."""
    out = []
    if fid == "smb" and have("net"):
        ok = samba.registry_enabled()
        out.append({"label": "Samba registry enabled", "ok": ok, "fix": None if ok else "registry",
                    "detail": "Settings are stored in the Samba registry." if ok else "Needed before shares can be created."})
        test = run(["testparm", "-s", "--suppress-prompt"], check=False) if have("testparm") else None
        if test is not None:
            out.append({"label": "smb.conf is valid", "ok": test.returncode == 0,
                        "detail": "testparm reports no errors." if test.returncode == 0 else test.stderr.strip()[-300:], "fix": None})
    if fid == "ftp" and have("proftpd"):
        found = ftp.directives()
        ok = ftp.include_ok(found)
        out.append({"label": "proftpd.conf includes conf.d", "ok": ok, "fix": None if ok else "proftpd",
                    "detail": "ShareHub's FTP configuration is loaded." if ok else "ShareHub's FTP configuration is not loaded."})
        out.append({"label": "No conflicting DefaultRoot", "ok": "defaultroot" not in found, "fix": None,
                    "detail": "Comment out DefaultRoot in /etc/proftpd/proftpd.conf." if "defaultroot" in found else "OK"})
        test = run(["proftpd", "-t"], check=False)
        out.append({"label": "ProFTPD configuration is valid", "ok": test.returncode == 0, "fix": None,
                    "detail": "proftpd -t reports no errors." if test.returncode == 0 else (test.stderr or test.stdout).strip()[-300:]})
    if fid == "ftps" and have("proftpd"):
        ok = _mod_tls_ok()
        out.append({"label": "mod_tls loaded", "ok": ok, "fix": None if ok or not glob.glob("/usr/lib/proftpd/mod_tls.so") else "proftpd",
                    "detail": "TLS module is loaded." if ok else "Enable 'LoadModule mod_tls.c' in /etc/proftpd/modules.conf."})
        for key, label in (("tls_cert", "TLS certificate"), ("tls_key", "TLS private key")):
            path = state["ftp"][key]
            out.append({"label": f"{label} present", "ok": os.path.isfile(path), "fix": None, "detail": path})
    if fid == "ad" and state["ad"]["enabled"] and have("wbinfo"):
        ok = run(["wbinfo", "-t"], check=False, timeout=30).returncode == 0
        out.append({"label": "Domain trust", "ok": ok, "fix": None,
                    "detail": "wbinfo -t succeeds." if ok else "wbinfo -t fails - check winbind and the computer account."})
    if fid == "versions" and state["shares"]:
        for name, share in state["shares"].items():
            if share["smb"]["enabled"] and share["smb"]["shadow_copies"]:
                fs = run(["findmnt", "-n", "-o", "FSTYPE", "--target", share["path"]], check=False).stdout.strip()
                ok = fs in ("zfs", "btrfs")
                out.append({"label": f"{name}: snapshot-capable filesystem", "ok": ok, "fix": None,
                            "detail": f"{share['path']} is on {fs or 'unknown'}."})
    if fid == "snapshots":
        import datetime
        for name, share in state["shares"].items():
            if not share["versioning"]["snapshots"]:
                continue
            if not snapshots.detect(share["path"])["supported"]:
                continue  # reported by fs_evaluation
            snaps = snapshots.list_snapshots(share["path"])
            limit = datetime.timedelta(minutes=45 if share["versioning"]["frequent"] else 150)
            fresh = bool(snaps) and datetime.datetime.now(datetime.timezone.utc) - snaps[0]["time"] < limit
            out.append({"label": f"{name}: recent version", "ok": fresh, "fix": None,
                        "detail": f"{len(snaps)} versions, latest {snaps[0]['time'].astimezone().strftime('%Y-%m-%d %H:%M')}"
                        if snaps else "No version yet - the timer runs every 15 minutes."})
    if fid == "recycle":
        bad = [(n, samba.recycle_root_problem(s)) for n, s in state["shares"].items()
               if s["smb"]["enabled"] and s["smb"]["recycle"]]
        bad = [(n, p) for n, p in bad if p]
        out.append({"label": "Every user can use the recycle bin", "ok": not bad, "fix": "recycle-perms" if bad else None,
                    "detail": " ".join(f"{n}: {p}" for n, p in bad) if bad else ".recycle folders are owned by root and open to every user."})
    if fid == "recycle" and has_systemd():
        active, _ = unit_state("sharehub-recycle.timer")
        out.append({"label": "Daily cleanup timer active", "ok": active == "active", "fix": None if active == "active" else "recycle-timer",
                    "detail": "Expired files are deleted daily." if active == "active" else "Old files in .recycle are not cleaned up."})
    return out


def system_info():
    osr = {}
    for line in (read_text("/etc/os-release") or "").splitlines():
        key, _, value = line.partition("=")
        osr[key] = value.strip().strip('"')
    virt = run(["systemd-detect-virt"], check=False).stdout.strip() if have("systemd-detect-virt") else ""
    smb = run(["smbd", "--version"], check=False).stdout.strip().replace("Version ", "") if have("smbd") else ""
    return {
        "os": osr.get("PRETTY_NAME", "Unknown Linux"),
        "os_id": osr.get("ID", ""),
        "os_version": osr.get("VERSION_ID", ""),
        "kernel": os.uname().release,
        "container": container_type(),
        "virtualization": "" if virt in ("", "none") else virt,
        "samba": smb,
    }


def verdict(feature):
    """Plain answer to 'can this feature be used on this system?'"""
    status = feature["status"]
    if status in ("unsupported", "unavailable"):
        return {"verdict": "no", "text": "Not possible on this system: " + (feature.get("reason") or "")}
    if feature["installable"]:
        return {"verdict": "install", "text": "Can be used after installing " + ", ".join(feature["installable"]) + "."}
    if feature.get("setup_needed"):
        return {"verdict": "setup", "text": "Possible after preparing the filesystem: " + (feature.get("reason") or "")}
    if status == "attention":
        return {"verdict": "fix", "text": "Can be used, but needs attention."}
    return {"verdict": "yes", "text": "Can be used on this system."}


def report(state, light=False):
    """Full dashboard data. light=True skips service states and configuration checks (used by
    the status call that every screen needs, so it stays fast)."""
    feats = features(state)
    for f in feats:
        if f.get("fs"):
            f["eval"] = fs_evaluation(state, f["id"])
            f["requires"] = f["eval"]["packages"]
    nfs_ok, nfs_reason = nfs_server_support()
    names = sorted({p for f in feats for alt in f["requires"] for p in alt})
    pkgs = package_info(names)
    out = []
    for f in feats:
        groups = []
        for alternatives in f["requires"]:
            items = [{"name": p, **pkgs.get(p, {"installed": None, "candidate": None})} for p in alternatives]
            groups.append({"satisfied": any(i["installed"] for i in items), "packages": items})
        services = [] if light else [_service(u) for u in f["services"]]
        checks = [] if light else _checks(f["id"], state)
        ev = f.get("eval")
        if ev and not light:
            checks = ev["checks"] + checks
        installed = all(g["satisfied"] for g in groups)
        unavailable = [g for g in groups if not g["satisfied"] and not any(p["candidate"] for p in g["packages"])]
        from_backports = None
        if f.get("backports") and not installed:
            bp = backports.suite()
            if bp and (not unavailable or not backports.enabled(bp)):
                # installable from <codename>-backports (enabled by ShareHub on Debian, as Cockpit's guide does)
                from_backports = bp
                for g in groups:
                    for p in g["packages"]:
                        p["candidate"] = p["candidate"] or f"from {bp}"
                unavailable = []
        unsupported = f["id"] == "nfs" and not nfs_ok
        running = all(sv["active"] in ("active", None) for sv in services if sv["installed"] is not False)
        checks_ok = all(c["ok"] for c in checks)
        reason = None
        setup_needed = False
        if ev and ev["kind"] == "impossible":
            unsupported = True
        if unsupported:
            status, reason = "unsupported", (ev["reason"] if ev and ev["kind"] == "impossible" else nfs_reason)
            services = []
        elif ev and ev["kind"] == "needs_setup":
            status, reason, setup_needed = ("attention" if f["in_use"] else "warning"), ev["reason"], True
        elif unavailable:
            status = "attention" if f["in_use"] else "unavailable"
            reason = f.get("unavailable_hint") or "Not available in this system's package sources."
        elif not installed:
            status = "attention" if f["in_use"] or f.get("core") else "absent"
            if from_backports:
                reason = (f"Comes from {from_backports}, as in Cockpit's install guide"
                          + ("" if backports.enabled(from_backports) else " (ShareHub enables it)")
                          + ". apt may also update Cockpit to the version it needs.")
        elif f["in_use"] and (not running or not checks_ok):
            status = "attention"
        elif not checks_ok:
            status = "warning"
        else:
            status = "ok"
        # for each unmet requirement, offer the first alternative apt can install
        missing = []
        for g in groups:
            if not g["satisfied"]:
                avail = [p["name"] for p in g["packages"] if p["candidate"]]
                if avail:
                    missing.append(avail[0])
        out.append({
            "id": f["id"], "label": f["label"], "core": bool(f.get("core")), "in_use": f["in_use"],
            "note": f.get("note"), "status": status, "reason": reason, "packages": groups, "services": services,
            "checks": checks, "installable": [] if unsupported else [m for m in missing if m in INSTALLABLE],
            "setup_needed": setup_needed, "from_backports": from_backports,
        })
    for item in out:
        item["availability"] = verdict(item)
        item["enabled"] = featmod.enabled(state, item["id"])
        item["ui"] = featmod.ui_state(item, item["enabled"])
    return {"systemd": has_systemd(), "container": container_type(), "system": system_info(), "features": out,
            "configured": state["features"]["configured"]}


def text_report(state, url_host=None):
    """Compatibility report printed after installation (postinst, install.sh, `sharehub-helper check`)."""
    data = report(state)
    sysinfo = data["system"]
    where = sysinfo["container"] and f"{sysinfo['container']} container" or sysinfo["virtualization"] or "physical or unknown host"
    marks = {"yes": "[ OK ]", "install": "[ + ]", "fix": "[ !! ]", "setup": "[ .. ]", "no": "[ -- ]"}
    lines = ["ShareHub compatibility report", f"System: {sysinfo['os']} ({where}), Samba {sysinfo['samba'] or 'not installed'}", ""]
    width = max(len(f["label"]) for f in data["features"])
    for f in data["features"]:
        a = f["availability"]
        detail = {"yes": "can be used", "fix": "can be used - check the Components page",
                  "install": "install: apt install " + " ".join(f["installable"]),
                  "setup": "needs filesystem setup - " + (f.get("reason") or ""),
                  "no": (f.get("reason") or "not possible here")}[a["verdict"]]
        lines.append(f"  {marks[a['verdict']]:7}{f['label']:<{width}}  {detail}")
    counts = {v: sum(1 for f in data["features"] if f["availability"]["verdict"] == v) for v in marks}
    lines += ["", f"{counts['yes'] + counts['fix']} of {len(data['features'])} features can be used now, "
                  f"{counts['install']} after installing packages, {counts['setup']} after filesystem setup, "
                  f"{counts['no']} not possible on this system."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- actions

def install(req):
    packages = req.get("packages") or []
    if not isinstance(packages, list) or not packages:
        raise HelperError("Nothing to install.")
    bad = [p for p in packages if p not in INSTALLABLE]
    if bad:
        raise HelperError("These packages cannot be installed from ShareHub: " + ", ".join(bad))
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    proc = run(["apt-get", "install", "-y", "-q", "-o", "Dpkg::Options::=--force-confold", *packages],
               check=False, env=env, timeout=1800)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if "Could not get lock" in detail or "is locked" in detail:
            raise HelperError("Another package operation is running (apt or unattended-upgrades). Try again in a few minutes.")
        if "Unable to locate package" in detail or "has no installation candidate" in detail:
            raise HelperError("A package is not available. Run 'sudo apt update' and try again.\n" + detail[-500:])
        raise HelperError("Installation failed:\n" + detail[-800:])
    return {"installed": packages}


def fix(req, warnings):
    action = req.get("action")
    if action == "registry":
        samba.enable_registry()
    elif action == "proftpd":
        from .cli import bootstrap
        bootstrap(quiet=True)
        systemctl("reload-or-restart", "proftpd", warnings=warnings)
    elif action == "recycle-perms":
        from . import state as statemod
        for share in statemod.load()["shares"].values():
            if share["smb"]["enabled"] and share["smb"]["recycle"] and os.path.isdir(share["path"]):
                samba.prepare_recycle(share, warnings)
    elif action == "recycle-timer":
        if not systemctl("enable", "--now", "sharehub-recycle.timer", warnings=warnings):
            raise HelperError(warnings[-1] if warnings else "Could not start the timer.")
    else:
        raise HelperError("Unknown fix.")
    return {"fixed": action}
