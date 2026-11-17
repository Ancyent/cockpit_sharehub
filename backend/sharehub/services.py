# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Service status/control, network discovery, live connections and firewall."""
import json

from . import ftp, nfs
from .state import parse_ports
from .util import HelperError, has_systemd, have, read_text, run, systemctl, unit_exists, unit_state

SERVICES = {
    "smbd": "SMB file server",
    "nmbd": "NetBIOS name server",
    "winbind": "Active Directory (winbind)",
    "nfs-server": "NFS server",
    "proftpd": "FTP server",
    "avahi-daemon": "mDNS discovery (macOS)",
    "wsdd-server": "WS-Discovery (Windows)",
}
ACTIONS = ("start", "stop", "restart", "enable", "disable")


def status():
    out = []
    for unit, label in SERVICES.items():
        installed = unit_exists(f"{unit}.service") if has_systemd() else None
        active, enabled = unit_state(unit) if installed else (None, None)
        out.append({"unit": unit, "label": label, "installed": installed,
                    "active": active, "enabled": enabled})
    return {"systemd": has_systemd(), "units": out}


def control(req):
    unit = req.get("unit")
    action = req.get("action")
    if unit not in SERVICES or action not in ACTIONS:
        raise HelperError("Unknown service or action.")
    warnings = []
    args = [action, unit] if action not in ("enable", "disable") else [action, "--now", unit]
    if not systemctl(*args, warnings=warnings):
        raise HelperError(warnings[-1] if warnings else f"Could not {action} {unit}.")
    return {"unit": unit}


def apply_discovery(state, warnings):
    """Keep discovery daemons in line with the Samba settings."""
    if not has_systemd():
        return
    s = state["samba"]
    wanted = {
        "avahi-daemon": s["mdns"],
        "wsdd-server": s["wsdd"],
        "nmbd": s["netbios"],
    }
    for unit, on in wanted.items():
        if not unit_exists(f"{unit}.service"):
            if on and unit != "nmbd":
                warnings.append(f"{SERVICES[unit]} is enabled but {unit} is not installed.")
            continue
        active, enabled = unit_state(unit)
        if on and active != "active":
            systemctl("enable", "--now", unit, warnings=warnings)
        elif not on and active == "active":
            systemctl("disable", "--now", unit, warnings=warnings)


# --------------------------------------------------------------------------- connections

def connections():
    smb = {"sessions": [], "shares": [], "open_files": 0}
    if have("smbstatus"):
        proc = run(["smbstatus", "--json"], check=False, timeout=30)
        try:
            data = json.loads(proc.stdout) if proc.returncode == 0 else {}
        except ValueError:
            data = {}
        for sess in (data.get("sessions") or {}).values():
            smb["sessions"].append({
                "pid": str(sess.get("server_id", {}).get("pid", "")),
                "user": sess.get("username", ""),
                "machine": sess.get("remote_machine", ""),
                "address": sess.get("hostname", ""),
                "protocol": sess.get("session_dialect", ""),
                "encryption": (sess.get("encryption") or {}).get("cipher", ""),
                "signing": (sess.get("signing") or {}).get("cipher", ""),
            })
        for tcon in (data.get("tcons") or {}).values():
            smb["shares"].append({
                "pid": str(tcon.get("server_id", {}).get("pid", "")),
                "share": tcon.get("service", ""),
                "machine": tcon.get("machine", ""),
                "connected_at": tcon.get("connected_at", ""),
            })
        smb["open_files"] = len(data.get("open_files") or {})
    return {"smb": smb, "nfs": nfs.connected_clients(), "ftp": ftp.sessions()}


def disconnect(req):
    pid = str(req.get("pid") or "")
    if not pid.isdigit():
        raise HelperError("Invalid session.")
    comm = (read_text(f"/proc/{pid}/comm") or "").strip()
    if comm != "smbd" and not comm.startswith("smbd"):
        raise HelperError("That process is not an SMB session.")
    run(["smbcontrol", pid, "shutdown"])
    return {"pid": pid}


# --------------------------------------------------------------------------- firewall

def required_ports(state):
    ports = []
    shares = state["shares"].values()
    if any(s["smb"]["enabled"] for s in shares) or state["samba"]["homes"]["enabled"]:
        ports.append(("445", "tcp", "SMB"))
        if state["samba"]["netbios"]:
            ports += [("137:138", "udp", "NetBIOS"), ("139", "tcp", "NetBIOS")]
        if state["samba"]["mdns"]:
            ports.append(("5353", "udp", "mDNS"))
        if state["samba"]["wsdd"]:
            ports += [("3702", "udp", "WS-Discovery"), ("5357", "tcp", "WS-Discovery")]
    if any(s["nfs"]["enabled"] for s in shares):
        ports.append(("2049", "tcp", "NFS"))
        if state["nfs"]["v3"]:
            ports += [("111", "tcp", "rpcbind"), ("111", "udp", "rpcbind"),
                      ("20048", "tcp", "NFS mountd"), ("20048", "udp", "NFS mountd"),
                      ("32765:32768", "tcp", "NFS statd/lockd"), ("32765:32768", "udp", "NFS statd/lockd")]
    if any(s["ftp"]["enabled"] for s in shares) or state["ftp"]["homes"]:
        ports.append(("21", "tcp", "FTP"))
        passive = parse_ports(state["ftp"]["passive_ports"])
        if passive:
            ports.append((f"{passive[0]}:{passive[1]}", "tcp", "FTP passive"))
    return ports


def firewall_status(state):
    out = {"backend": None, "active": False, "required": [
        {"port": p, "proto": proto, "label": label} for p, proto, label in required_ports(state)]}
    if have("ufw"):
        proc = run(["ufw", "status"], check=False)
        out["backend"] = "ufw"
        out["active"] = "Status: active" in proc.stdout
        out["rules"] = proc.stdout
    elif have("firewall-cmd"):
        out["backend"] = "firewalld"
        out["active"] = run(["firewall-cmd", "--state"], check=False).returncode == 0
        out["rules"] = run(["firewall-cmd", "--list-ports"], check=False).stdout.strip()
    return out


def firewall_open(state):
    applied = []
    ports = required_ports(state)
    if not ports:
        raise HelperError("No protocol is in use - nothing to open.")
    if have("ufw"):
        for port, proto, label in ports:
            run(["ufw", "allow", f"{port}/{proto}", "comment", f"ShareHub {label}"])
            applied.append(f"{port}/{proto}")
    elif have("firewall-cmd"):
        for port, proto, label in ports:
            run(["firewall-cmd", "--permanent", f"--add-port={port.replace(':', '-')}/{proto}"])
            applied.append(f"{port}/{proto}")
        run(["firewall-cmd", "--reload"])
    else:
        raise HelperError("No supported firewall (ufw or firewalld) found.")
    return {"opened": applied}
