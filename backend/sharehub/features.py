# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Feature switches and the single "can this be used here?" answer per feature.

Every screen (Components dashboard, share editor, Settings, first-run wizard) reads the
state computed here, so they always agree:

    off            turned off by the admin - hidden in the UI
    impossible     cannot work on this system (e.g. NFS in a container)
    needs_install  possible after installing the packages in "install"
    needs_setup    packages present, but the filesystem or host must be prepared ("reason")
    ready          usable
"""
from .util import HelperError, has_systemd, systemctl

# feature id -> (state section, key) holding the admin's on/off choice
TOGGLES = {
    "nfs": ("features", "nfs"),
    "ftp": ("features", "ftp"),
    "ad": ("features", "ad"),
    "recycle": ("features", "recycle"),
    "quota": ("features", "quota"),
    "snapshots": ("features", "snapshots"),
    "versions": ("features", "versions"),
    "mdns": ("samba", "mdns"),
    "wsdd": ("samba", "wsdd"),
    "netbios": ("samba", "netbios"),
}
LABELS = {
    "nfs": "NFS server", "ftp": "FTP server", "ad": "Active Directory", "recycle": "Recycle bin",
    "quota": "Size limits", "snapshots": "Scheduled versions", "versions": "Previous Versions (external snapshots)",
    "mdns": "macOS discovery (Bonjour)", "wsdd": "Windows discovery (WS-Discovery)", "netbios": "NetBIOS name service",
}
# units ShareHub owns completely and starts/stops with the feature
UNITS = {"nfs": ["nfs-server"], "ftp": ["proftpd"], "recycle": ["sharehub-recycle.timer"],
         "snapshots": ["sharehub-snapshot.timer"]}


def enabled(state, fid):
    if fid == "smb":
        return True
    if fid == "ftps":
        return enabled(state, "ftp")
    section, key = TOGGLES[fid]
    return bool(state[section][key])


def users_of(state, fid):
    """What still uses a feature (share names or a short description); empty when it can be turned off."""
    shares = state["shares"].items()
    if fid == "nfs":
        return [n for n, s in shares if s["nfs"]["enabled"]]
    if fid == "ftp":
        return [n for n, s in shares if s["ftp"]["enabled"]] + (["FTP home folders"] if state["ftp"]["homes"] else [])
    if fid == "ad":
        return [f"joined to {state['ad']['realm']}"] if state["ad"]["enabled"] else []
    if fid == "recycle":
        return [n for n, s in shares if s["smb"]["enabled"] and s["smb"]["recycle"]]
    if fid == "quota":
        return [n for n, s in shares if s["quota"]["limit"]]
    if fid == "snapshots":
        return [n for n, s in shares if s["versioning"]["snapshots"]]
    if fid == "versions":
        return [n for n, s in shares if s["smb"]["enabled"] and s["smb"]["shadow_copies"]]
    return []


def check_share(state, name, share):
    """Refuse a share that uses a feature the admin turned off."""
    wanted = {
        "nfs": share["nfs"]["enabled"],
        "ftp": share["ftp"]["enabled"],
        "recycle": share["smb"]["enabled"] and share["smb"]["recycle"],
        "quota": bool(share["quota"]["limit"]),
        "snapshots": share["versioning"]["snapshots"],
        "versions": share["smb"]["enabled"] and share["smb"]["shadow_copies"],
    }
    for fid, used in wanted.items():
        if used and not enabled(state, fid):
            raise HelperError(f"{name}: {LABELS[fid]} is turned off. Turn it on under Settings → Features first.")


def ui_state(feature, on):
    """UI state for one feature of health.report()."""
    if not on:
        return "off"
    if feature["status"] in ("unsupported", "unavailable"):
        return "impossible"
    if feature.get("setup_needed"):
        return "needs_setup"
    if feature["installable"] and not all(g["satisfied"] for g in feature["packages"]):
        return "needs_install"
    return "ready"


def states(state, report):
    """{feature id: {"state", "reason", "install", "label", "in_use"}} from a health report."""
    out = {}
    for f in report["features"]:
        st = ui_state(f, enabled(state, f["id"]))
        out[f["id"]] = {
            "state": st, "label": f["label"], "in_use": f["in_use"],
            "reason": f.get("reason") or (f"Needs {', '.join(f['installable'])}." if st == "needs_install" else None),
            "install": f["installable"] if st == "needs_install" else [],
        }
    return out


def apply_choice(state, choice, warnings):
    """Set feature switches from {id: bool}; returns the new state (not yet committed)."""
    if not isinstance(choice, dict):
        raise HelperError("Invalid feature list.")
    for fid, value in choice.items():
        if fid not in TOGGLES:
            raise HelperError(f"Unknown feature: {fid}")
        value = bool(value)
        if not value and enabled(state, fid):
            used = users_of(state, fid)
            if used:
                raise HelperError(f"{LABELS[fid]} is still used by: {', '.join(used)}. "
                                  "Turn it off there first.")
        section, key = TOGGLES[fid]
        state[section][key] = value
    state["features"]["configured"] = True
    return state


def sync_units(before, after, warnings, installed=lambda unit: True):
    """Stop units of features that were turned off, start those turned back on."""
    if not has_systemd():
        return
    for fid, units in UNITS.items():
        was, now = enabled(before, fid), enabled(after, fid)
        if was == now:
            continue
        for unit in units:
            if not installed(unit):
                continue
            if now:
                systemctl("enable", "--now", unit, warnings=warnings)
            else:
                systemctl("disable", "--now", unit, warnings=warnings)
