# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""The single source of truth: /etc/sharehub/state.json, its schema, validation and history."""
import copy
import datetime
import grp
import ipaddress
import json
import os
import pwd
import re

from .paths import STATE_DIR
from .util import HelperError, atomic_write, read_text

STATE_FILE = os.path.join(STATE_DIR, "state.json")
HISTORY_DIR = os.path.join(STATE_DIR, "history")
HISTORY_KEEP = 30
SCHEMA_VERSION = 1

SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED_NAMES = {"global", "homes", "home", "printers", "print$", "ipc$"}
SIZE_RE = re.compile(r"^\d+(\.\d+)?[KMGTP]?$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9*?][A-Za-z0-9*?-]{0,62}(\.[A-Za-z0-9*?][A-Za-z0-9*?-]{0,62})*$")
PORTS_RE = re.compile(r"^\s*(\d{1,5})\s*-\s*(\d{1,5})\s*$")

MIN_PROTOCOLS = ("SMB2_10", "SMB3_00", "SMB3_11")
ENCRYPTION = ("default", "desired", "required")
SIGNING = ("default", "auto", "mandatory")
SQUASH = ("root_squash", "no_root_squash", "all_squash")

DEFAULT_SETTINGS = {
    "samba": {
        "workgroup": "WORKGROUP",
        "server_string": "%h file server",
        "netbios_name": "",
        "netbios": True,
        "min_protocol": "SMB2_10",
        "encryption": "default",
        "signing": "default",
        "multichannel": True,
        "macos": True,
        "macos_model": "MacSamba",
        "mdns": True,
        "wsdd": True,
        "homes": {"enabled": False, "browseable": False},
    },
    "nfs": {"v3": True, "v4": True, "threads": 8},
    "ftp": {
        "tls_required": False,
        "tls_cert": "/etc/ssl/certs/ssl-cert-snakeoil.pem",
        "tls_key": "/etc/ssl/private/ssl-cert-snakeoil.key",
        "passive_ports": "",
        "homes": False,
    },
    "ad": {
        "enabled": False,
        "realm": "",
        "workgroup": "",
        "idmap_backend": "rid",
        "idmap_range": "10000-999999",
        "default_domain": True,
        "login_shell": False,
        "template_homedir": "/home/%U",
    },
}

DEFAULT_SHARE = {
    "path": "",
    "comment": "",
    "access": {"users": [], "groups": [], "read_only": False},
    "smb": {
        "enabled": True,
        "guest_ok": False,
        "browseable": True,
        "time_machine": False,
        "time_machine_max_size": "",
        "recycle": False,
        "recycle_days": 30,
        "recycle_protect": True,
        "recycle_versions": 10,
        "shadow_copies": False,
        "windows_acls": False,
    },
    "nfs": {"enabled": False, "clients": []},
    "ftp": {"enabled": False, "read_only": False},
    "quota": {"limit": ""},
    "versioning": {
        "snapshots": False,
        "frequent": False,
        "keep_frequent": 8,
        "keep_hourly": 24,
        "keep_daily": 14,
        "keep_weekly": 8,
        "keep_monthly": 6,
    },
}

DEFAULT_NFS_CLIENT = {"host": "*", "rw": True, "squash": "root_squash", "sync": True,
                      "anonuid": "", "anongid": ""}


def empty_state():
    state = {"version": SCHEMA_VERSION, "shares": {}}
    state.update(copy.deepcopy(DEFAULT_SETTINGS))
    return state


def _merge(defaults, value):
    """Deep-merge user data onto defaults, keeping only known keys."""
    if not isinstance(defaults, dict):
        return value if value is not None else defaults
    out = {}
    value = value if isinstance(value, dict) else {}
    for key, default in defaults.items():
        out[key] = _merge(default, value.get(key)) if key in value else copy.deepcopy(default)
    return out


def from_dict(data):
    """Normalise raw JSON data onto the current schema (unknown keys are dropped)."""
    data = data or {}
    state = empty_state()
    for section in DEFAULT_SETTINGS:
        state[section] = _merge(DEFAULT_SETTINGS[section], data.get(section))
    for name, share in (data.get("shares") or {}).items():
        state["shares"][name] = _merge(DEFAULT_SHARE, share)
        state["shares"][name]["nfs"]["clients"] = [
            _merge(DEFAULT_NFS_CLIENT, c) for c in (share.get("nfs") or {}).get("clients", [])]
    return state


def load():
    raw = read_text(STATE_FILE)
    return from_dict(json.loads(raw) if raw else {})


def save(state, label="change"):
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    atomic_write(STATE_FILE, text, 0o600)
    _write_history(text, label)


def _write_history(text, label):
    os.makedirs(HISTORY_DIR, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label)[:60]
    atomic_write(os.path.join(HISTORY_DIR, f"{stamp}_{safe}.json"), text, 0o600)
    entries = sorted(os.listdir(HISTORY_DIR))
    for old in entries[:-HISTORY_KEEP]:
        os.unlink(os.path.join(HISTORY_DIR, old))


def history():
    if not os.path.isdir(HISTORY_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not entry.endswith(".json"):
            continue
        stamp, _, label = entry[:-5].partition("_")
        try:
            when = datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f").isoformat(timespec="seconds")
        except ValueError:
            when = stamp
        out.append({"id": entry, "time": when, "label": label})
    return out


def load_history(entry_id):
    if not re.match(r"^[0-9-]+_[A-Za-z0-9_.-]*\.json$", entry_id or ""):
        raise HelperError("Invalid history entry.")
    raw = read_text(os.path.join(HISTORY_DIR, entry_id))
    if raw is None:
        raise HelperError("History entry not found.")
    return from_dict(json.loads(raw))


# --------------------------------------------------------------------------- validation

def find_share(state, name):
    for existing in state["shares"]:
        if existing.lower() == str(name).lower():
            return existing
    return None


def validate_share_name(name):
    if not isinstance(name, str) or not SHARE_NAME_RE.match(name):
        raise HelperError("Share name: 1-64 characters, letters, digits, '.', '_' or '-', "
                          "starting with a letter or digit.")
    if name.lower() in RESERVED_NAMES:
        raise HelperError(f"'{name}' is a reserved name.")


def validate_path(path, ftp_root):
    if not isinstance(path, str) or not path.startswith("/"):
        raise HelperError("Path must be an absolute directory path.")
    if any(c in path for c in "\n\r\t\"\\"):
        raise HelperError("Path contains invalid characters.")
    path = os.path.normpath(path)
    if path in ("/", "/etc", "/usr", "/boot", "/proc", "/sys", "/dev", "/run", "/var", "/root"):
        raise HelperError(f"Sharing {path} is not allowed.")
    if not os.path.isdir(path):
        raise HelperError(f"Directory does not exist: {path}")
    real = os.path.realpath(path)
    if real == ftp_root or real.startswith(ftp_root + "/"):
        raise HelperError(f"Path cannot be inside {ftp_root}.")
    return path


def clean_principals(value, kind):
    """Validate a list of user or group names against NSS (local or winbind)."""
    if not value:
        return []
    if not isinstance(value, list):
        raise HelperError(f"Invalid {kind} list.")
    out = []
    for item in value:
        item = str(item).strip()
        if not item:
            continue
        if re.search(r'[,"\n\r@+&]', item):
            raise HelperError(f"Invalid {kind} name: {item!r}")
        try:
            (pwd.getpwnam if kind == "user" else grp.getgrnam)(item)
        except KeyError:
            raise HelperError(f"Unknown {kind}: {item}")
        if item not in out:
            out.append(item)
    return out


def validate_nfs_host(host):
    host = str(host or "").strip()
    if not host:
        raise HelperError("NFS client host cannot be empty.")
    if host.startswith("@") and re.match(r"^@[A-Za-z0-9._-]+$", host):
        return host  # netgroup
    try:
        ipaddress.ip_network(host, strict=False)
        return host
    except ValueError:
        pass
    if HOSTNAME_RE.match(host) and len(host) <= 253:
        return host
    raise HelperError(f"Invalid NFS client: {host!r} (use an IP, a subnet like 10.0.0.0/24, "
                      "a hostname, a wildcard like *.lan or '*').")


def _int_field(value, label, low, high, allow_empty=False):
    if allow_empty and (value is None or str(value).strip() == ""):
        return ""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise HelperError(f"{label} must be a whole number.")
    if not low <= number <= high:
        raise HelperError(f"{label} must be between {low} and {high}.")
    return number


def normalize_share(data, ftp_root):
    share = _merge(DEFAULT_SHARE, data)
    share["path"] = validate_path(share["path"], ftp_root)
    share["comment"] = str(share["comment"] or "").strip()
    if any(c in share["comment"] for c in "\n\r"):
        raise HelperError("Description must be a single line.")
    acc = share["access"]
    acc["users"] = clean_principals(acc["users"], "user")
    acc["groups"] = clean_principals(acc["groups"], "group")
    acc["read_only"] = bool(acc["read_only"])

    smb = share["smb"]
    for key in ("enabled", "guest_ok", "browseable", "time_machine", "recycle", "recycle_protect",
                "shadow_copies", "windows_acls"):
        smb[key] = bool(smb[key])
    size = str(smb["time_machine_max_size"] or "").strip().upper().replace(" ", "")
    if size and not SIZE_RE.match(size):
        raise HelperError("Time Machine size limit must look like 500G or 2T.")
    smb["time_machine_max_size"] = size
    smb["recycle_days"] = _int_field(smb["recycle_days"], "Recycle bin retention (days)", 0, 3650)
    smb["recycle_versions"] = _int_field(smb["recycle_versions"], "Versions kept per deleted file", 0, 1000)

    v = share["versioning"]
    v["snapshots"] = bool(v["snapshots"])
    v["frequent"] = bool(v["frequent"])
    for key, label in (("keep_frequent", "15-minute versions"), ("keep_hourly", "Hourly versions"),
                       ("keep_daily", "Daily versions"), ("keep_weekly", "Weekly versions"),
                       ("keep_monthly", "Monthly versions")):
        v[key] = _int_field(v[key], label, 0, 1000)
    if v["snapshots"]:
        from .snapshots import detect
        info = detect(share["path"])
        if not info["supported"]:
            raise HelperError("Scheduled versions are not possible for this folder. " + info["reason"])

    nfs = share["nfs"]
    nfs["enabled"] = bool(nfs["enabled"])
    clients = []
    for raw in (data.get("nfs") or {}).get("clients", []) or []:
        c = _merge(DEFAULT_NFS_CLIENT, raw)
        c["host"] = validate_nfs_host(c["host"])
        c["rw"] = bool(c["rw"])
        c["sync"] = bool(c["sync"])
        if c["squash"] not in SQUASH:
            raise HelperError("NFS squash must be root_squash, no_root_squash or all_squash.")
        c["anonuid"] = _int_field(c["anonuid"], "NFS anonymous UID", 0, 4294967294, allow_empty=True)
        c["anongid"] = _int_field(c["anongid"], "NFS anonymous GID", 0, 4294967294, allow_empty=True)
        clients.append(c)
    if nfs["enabled"]:
        from .util import nfs_server_support
        supported, reason = nfs_server_support()
        if not supported:
            raise HelperError("NFS cannot be used here. " + reason)
    if nfs["enabled"] and not clients:
        raise HelperError("Add at least one NFS client (host or subnet) or turn NFS off.")
    if len({c["host"] for c in clients}) != len(clients):
        raise HelperError("Each NFS client may appear only once per share.")
    nfs["clients"] = clients

    limit = str(share["quota"]["limit"] or "").strip().upper().replace(" ", "")
    if limit:
        from .quota import to_bytes
        to_bytes(limit)
    share["quota"] = {"limit": limit}

    ftp = share["ftp"]
    ftp["enabled"] = bool(ftp["enabled"])
    ftp["read_only"] = bool(ftp["read_only"])
    if not (smb["enabled"] or nfs["enabled"] or ftp["enabled"]):
        raise HelperError("Enable at least one protocol (SMB, NFS or FTP).")
    return share


def parse_ports(text):
    if not text:
        return None
    m = PORTS_RE.match(str(text))
    if not m:
        raise HelperError("Passive ports must look like 49152-49200.")
    low, high = int(m.group(1)), int(m.group(2))
    if not (1024 <= low <= high <= 65535):
        raise HelperError("Passive ports must be within 1024-65535, start <= end.")
    return low, high


def normalize_settings(section, data):
    if section not in DEFAULT_SETTINGS:
        raise HelperError(f"Unknown settings section: {section}")
    s = _merge(DEFAULT_SETTINGS[section], data)
    if section == "samba":
        wg = str(s["workgroup"]).strip().upper()
        if not re.match(r"^[A-Z0-9][A-Z0-9_-]{0,14}$", wg):
            raise HelperError("Workgroup: 1-15 characters, letters, digits, '_' or '-'.")
        s["workgroup"] = wg
        nb = str(s["netbios_name"] or "").strip().upper()
        if nb and not re.match(r"^[A-Z0-9][A-Z0-9-]{0,14}$", nb):
            raise HelperError("NetBIOS name: up to 15 letters, digits or '-'.")
        s["netbios_name"] = nb
        s["server_string"] = str(s["server_string"] or "").strip()[:80]
        if any(c in s["server_string"] for c in "\n\r"):
            raise HelperError("Server description must be a single line.")
        if s["min_protocol"] not in MIN_PROTOCOLS:
            raise HelperError("Invalid minimum protocol.")
        if s["encryption"] not in ENCRYPTION:
            raise HelperError("Invalid encryption setting.")
        if s["signing"] not in SIGNING:
            raise HelperError("Invalid signing setting.")
        s["macos_model"] = str(s["macos_model"] or "MacSamba").strip()
        if not re.match(r"^[A-Za-z0-9,]{1,40}$", s["macos_model"]):
            raise HelperError("macOS icon model: letters, digits and commas only.")
        for key in ("netbios", "multichannel", "macos", "mdns", "wsdd"):
            s[key] = bool(s[key])
        s["homes"] = {"enabled": bool(s["homes"]["enabled"]),
                      "browseable": bool(s["homes"]["browseable"])}
    elif section == "nfs":
        s["v3"] = bool(s["v3"])
        s["v4"] = bool(s["v4"])
        if not (s["v3"] or s["v4"]):
            raise HelperError("Enable at least one NFS version.")
        s["threads"] = _int_field(s["threads"], "NFS server threads", 1, 256)
    elif section == "ftp":
        s["tls_required"] = bool(s["tls_required"])
        s["homes"] = bool(s["homes"])
        for key in ("tls_cert", "tls_key"):
            value = str(s[key] or DEFAULT_SETTINGS["ftp"][key]).strip()
            if not value.startswith("/"):
                raise HelperError("TLS certificate and key must be absolute paths.")
            s[key] = value
        s["passive_ports"] = str(s["passive_ports"] or "").strip()
        parse_ports(s["passive_ports"])
    return s
