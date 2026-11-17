# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Components dashboard: for every feature, the packages, services and configuration it
depends on, whether it is in use, and what can be fixed from the UI."""
import glob
import os
import re

from . import ftp, samba
from .util import (HelperError, container_type, has_systemd, have, nfs_server_support, read_text, run,
                   systemctl, unit_exists, unit_state, wsdd_unit)

# Packages the UI may install. Anything else is refused.
INSTALLABLE = {
    "samba", "samba-common-bin", "smbclient", "nfs-kernel-server", "proftpd-core", "proftpd-basic",
    "proftpd-mod-crypto", "ssl-cert", "avahi-daemon", "wsdd-server", "wsdd2", "winbind", "libnss-winbind",
    "libpam-winbind", "krb5-user", "sanoid", "zfs-auto-snapshot", "snapper", "quota", "xfsprogs",
    "btrfs-progs", "zfsutils-linux", "acl", "attr", "ufw",
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
        {"id": "versions", "label": "Previous Versions (snapshots)", "requires": [["sanoid", "zfs-auto-snapshot", "snapper"]],
         "services": [], "in_use": uses["versions"]},
        {"id": "quota", "label": "Size limits", "requires": [["zfsutils-linux", "xfsprogs", "quota", "btrfs-progs"]],
         "services": [], "in_use": uses["quota"],
         "note": "One tool per filesystem you use: zfsutils-linux (ZFS), xfsprogs (XFS), quota (ext4), btrfs-progs (Btrfs)."
                 + (" In a container, quotas usually have to be set on the host (e.g. the container's mount point size)."
                    if container_type() else "")},
        {"id": "recycle", "label": "Recycle bin cleanup", "requires": [], "services": ["sharehub-recycle.timer"],
         "in_use": uses["recycle"]},
        {"id": "snapshots", "label": "Scheduled versions", "requires": [["zfsutils-linux", "btrfs-progs"]],
         "services": ["sharehub-snapshot.timer"], "in_use": uses["snapshots"],
         "note": "Read-only versions every 15 minutes or hourly, on ZFS datasets or Btrfs subvolumes."},
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
        from . import snapshots
        import datetime
        for name, share in state["shares"].items():
            if not share["versioning"]["snapshots"]:
                continue
            info = snapshots.detect(share["path"])
            if not info["supported"]:
                out.append({"label": f"{name}: snapshots possible", "ok": False, "fix": None, "detail": info["reason"]})
                continue
            snaps = snapshots.list_snapshots(share["path"])
            limit = datetime.timedelta(minutes=45 if share["versioning"]["frequent"] else 150)
            fresh = bool(snaps) and datetime.datetime.now(datetime.timezone.utc) - snaps[0]["time"] < limit
            out.append({"label": f"{name}: recent version", "ok": fresh, "fix": None,
                        "detail": f"{len(snaps)} versions, latest {snaps[0]['time'].astimezone().strftime('%Y-%m-%d %H:%M')}"
                        if snaps else "No version yet - the timer runs every 15 minutes."})
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
    if status == "attention":
        return {"verdict": "fix", "text": "Can be used, but needs attention."}
    return {"verdict": "yes", "text": "Can be used on this system."}


def report(state):
    feats = features(state)
    nfs_ok, nfs_reason = nfs_server_support()
    names = sorted({p for f in feats for alt in f["requires"] for p in alt})
    pkgs = package_info(names)
    out = []
    for f in feats:
        groups = []
        for alternatives in f["requires"]:
            items = [{"name": p, **pkgs.get(p, {"installed": None, "candidate": None})} for p in alternatives]
            groups.append({"satisfied": any(i["installed"] for i in items), "packages": items})
        services = [_service(u) for u in f["services"]]
        checks = _checks(f["id"], state)
        installed = all(g["satisfied"] for g in groups)
        unavailable = [g for g in groups if not g["satisfied"] and not any(p["candidate"] for p in g["packages"])]
        unsupported = f["id"] == "nfs" and not nfs_ok
        running = all(sv["active"] in ("active", None) for sv in services if sv["installed"] is not False)
        checks_ok = all(c["ok"] for c in checks)
        reason = None
        if unsupported:
            status, reason = "unsupported", nfs_reason
            services = []
        elif unavailable:
            status = "attention" if f["in_use"] else "unavailable"
            reason = f.get("unavailable_hint") or "Not available in this system's package sources."
        elif not installed:
            status = "attention" if f["in_use"] or f.get("core") else "absent"
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
        })
    for item in out:
        item["availability"] = verdict(item)
    return {"systemd": has_systemd(), "container": container_type(), "system": system_info(), "features": out}


def text_report(state, url_host=None):
    """Compatibility report printed after installation (postinst, install.sh, `sharehub-helper check`)."""
    data = report(state)
    sysinfo = data["system"]
    where = sysinfo["container"] and f"{sysinfo['container']} container" or sysinfo["virtualization"] or "physical or unknown host"
    marks = {"yes": "[ OK ]", "install": "[ + ]", "fix": "[ !! ]", "no": "[ -- ]"}
    lines = ["ShareHub compatibility report", f"System: {sysinfo['os']} ({where}), Samba {sysinfo['samba'] or 'not installed'}", ""]
    width = max(len(f["label"]) for f in data["features"])
    for f in data["features"]:
        a = f["availability"]
        detail = {"yes": "can be used", "fix": "can be used - check the Components page",
                  "install": "install: apt install " + " ".join(f["installable"]),
                  "no": (f.get("reason") or "not possible here")}[a["verdict"]]
        lines.append(f"  {marks[a['verdict']]:7}{f['label']:<{width}}  {detail}")
    counts = {v: sum(1 for f in data["features"] if f["availability"]["verdict"] == v) for v in marks}
    lines += ["", f"{counts['yes'] + counts['fix']} of {len(data['features'])} features can be used now, "
                  f"{counts['install']} after installing packages, {counts['no']} not possible on this system."]
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
    elif action == "recycle-timer":
        if not systemctl("enable", "--now", "sharehub-recycle.timer", warnings=warnings):
            raise HelperError(warnings[-1] if warnings else "Could not start the timer.")
    else:
        raise HelperError("Unknown fix.")
    return {"fixed": action}
