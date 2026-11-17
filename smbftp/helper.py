#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""
cockpit-smbftp backend helper.

Called by the Cockpit page through cockpit.spawn() with superuser rights:
    python3 helper.py <command>      (JSON request on stdin)
Prints one JSON reply on stdout:
    {"ok": true, "data": ...}   or   {"ok": false, "error": "..."}

Design:
  * Samba shares live in the Samba registry (`net conf`), the same backend the
    45Drives "File Sharing" module uses, so both UIs see the same shares.
  * FTP exposure is stored in STATE_FILE. It is rendered into:
      - bind mounts  <share path>  ->  FTP_ROOT/<share name>   (persisted in /etc/fstab)
      - one ProFTPD include file with a chroot (DefaultRoot FTP_ROOT) and a
        <Directory> block per share, mirroring the Samba "valid users".
  * Every change to the ProFTPD config is validated with `proftpd -t` and rolled
    back if the test fails.
"""
import configparser
import fcntl
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

STATE_DIR = "/etc/cockpit-smbftp"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOCK_FILE = "/run/cockpit-smbftp.lock"
FTP_ROOT = "/srv/ftp-shares"
HOME_BASE = "/home"
FTP_HOME = os.path.join(FTP_ROOT, "home")
SMB_CONF = "/etc/samba/smb.conf"
PROFTPD_CONF = "/etc/proftpd/proftpd.conf"
PROFTPD_CONF_D = "/etc/proftpd/conf.d"
PROFTPD_INC = os.path.join(PROFTPD_CONF_D, "cockpit-smbftp.conf")
FSTAB = "/etc/fstab"
FSTAB_OPT_TAG = "x-cockpit-smbftp"  # userspace mount option, ignored by the kernel

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PORTS_RE = re.compile(r"^\s*(\d{1,5})\s*-\s*(\d{1,5})\s*$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RESERVED_NAMES = {"global", "homes", "home", "printers", "print$", "ipc$"}
LOGIN_SHELL = "/bin/bash"
NOLOGIN_SHELL = "/usr/sbin/nologin"

DEFAULT_SETTINGS = {
    "ftp_tls_required": False,
    "tls_cert": "/etc/ssl/certs/ssl-cert-snakeoil.pem",
    "tls_key": "/etc/ssl/private/ssl-cert-snakeoil.key",
    "passive_ports": "",
    "homes_smb": False,
    "homes_ftp": False,
}


class HelperError(Exception):
    pass


# --------------------------------------------------------------------------- utils

def run(cmd, check=True, inp=None):
    try:
        proc = subprocess.run(cmd, input=inp, capture_output=True, text=True)
    except FileNotFoundError:
        raise HelperError(f"Command not found: {cmd[0]}")
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        raise HelperError(f"'{' '.join(cmd)}' failed: {msg}")
    return proc


def have(binary):
    return shutil.which(binary) is not None


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return None


def atomic_write(path, content, mode=0o644):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".smbftp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def is_true(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def octal_unescape(text):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def fstab_escape(text):
    return (text.replace("\\", "\\134").replace(" ", "\\040")
                .replace("\t", "\\011").replace("\n", "\\012"))


def mountpoints():
    points = set()
    with open("/proc/self/mountinfo") as f:
        for line in f:
            parts = line.split()
            if len(parts) > 4:
                points.add(octal_unescape(parts[4]))
    return points


def systemd_active(unit):
    if not have("systemctl"):
        return False
    return run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


# --------------------------------------------------------------------------- state

def load_state():
    raw = read_text(STATE_FILE)
    state = json.loads(raw) if raw else {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(state.get("settings") or {})
    state["settings"] = settings
    state.setdefault("ftp", {})  # lower-case share name -> {"read_only": bool}
    return state


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    atomic_write(STATE_FILE, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)


# --------------------------------------------------------------------------- samba

def parse_smb_text(text):
    """Parse smb.conf-style text into {section: {param: value}}."""
    clean = "\n".join(line.strip() for line in text.splitlines())
    cp = configparser.RawConfigParser(strict=False, delimiters=("=",),
                                      comment_prefixes=("#", ";"),
                                      default_section="\x00none")
    cp.optionxform = lambda opt: " ".join(opt.lower().split())
    cp.read_string(clean)
    return {sec: dict(cp.items(sec)) for sec in cp.sections()}


def registry_shares():
    if not have("net"):
        return {}
    proc = run(["net", "conf", "list"], check=False)
    if proc.returncode != 0:
        return {}
    return {k: v for k, v in parse_smb_text(proc.stdout).items() if k.lower() != "global"}


def file_shares():
    text = read_text(SMB_CONF)
    if not text:
        return []
    try:
        sections = parse_smb_text(text)
    except configparser.Error:
        return []
    return sorted(k for k in sections if k.lower() not in ("global", "homes", "printers", "print$"))


def file_has_homes():
    text = read_text(SMB_CONF)
    if not text:
        return False
    try:
        return any(k.lower() == "homes" for k in parse_smb_text(text))
    except configparser.Error:
        return False


def apply_smb_homes(enabled, warnings):
    """[homes] in the registry: every user gets //server/<username> = own home directory."""
    existing = find_share(registry_shares(), "homes")
    if enabled:
        if not registry_enabled():
            raise HelperError("Samba registry is not enabled in smb.conf - enable it first.")
        if file_has_homes():
            warnings.append("smb.conf already defines [homes]; that definition wins over the registry one.")
        for key, value in (("comment", "Home Directories"), ("browseable", "no"),
                           ("read only", "no"), ("valid users", "%S"),
                           ("create mask", "0700"), ("directory mask", "0700")):
            run(["net", "conf", "setparm", "homes", key, value])
        reload_samba()
    elif existing:
        run(["net", "conf", "delshare", existing], check=False)
        reload_samba()


def registry_enabled(text=None):
    text = read_text(SMB_CONF) if text is None else text
    if not text:
        return False
    in_global = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        if s.startswith("["):
            in_global = s.lower().replace(" ", "") == "[global]"
            continue
        if in_global and "=" in s:
            key, _, value = s.partition("=")
            key = " ".join(key.lower().split())
            if key in ("include", "config backend") and value.strip().lower() == "registry":
                return True
    return False


def reload_samba():
    if have("smbcontrol"):
        run(["smbcontrol", "smbd", "reload-config"], check=False)


def find_share(shares, name):
    for existing in shares:
        if existing.lower() == name.lower():
            return existing
    return None


def split_principals(value):
    users, groups = [], []
    for token in re.split(r"[\s,]+", value or ""):
        token = token.strip().strip('"')
        if not token:
            continue
        if token[0] in "@+&":
            groups.append(token.lstrip("@+&"))
        else:
            users.append(token)
    return users, groups


def share_view(name, params, state, mounted):
    read_only = is_true(params.get("read only"), True)
    for key in ("writeable", "writable", "write ok"):
        if key in params:
            read_only = not is_true(params[key])
    users, groups = split_principals(params.get("valid users"))
    ftp = state["ftp"].get(name.lower())
    return {
        "name": name,
        "path": params.get("path", ""),
        "comment": params.get("comment", ""),
        "read_only": read_only,
        "guest_ok": is_true(params.get("guest ok", params.get("public")), False),
        "browseable": is_true(params.get("browseable", params.get("browsable")), True),
        "users": users,
        "groups": groups,
        "ftp": ftp is not None,
        "ftp_read_only": bool(ftp and ftp.get("read_only")),
        "ftp_path": os.path.join(FTP_ROOT, name),
        "ftp_mounted": os.path.join(FTP_ROOT, name) in mounted,
    }


# --------------------------------------------------------------------------- validation

def validate_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise HelperError("Share name: 1-64 characters, letters, digits, '.', '_' or '-', "
                          "starting with a letter or digit.")
    if name.lower() in RESERVED_NAMES:
        raise HelperError(f"'{name}' is a reserved Samba section name.")


def validate_path(path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise HelperError("Path must be an absolute directory path.")
    if any(c in path for c in "\n\r\t"):
        raise HelperError("Path contains invalid characters.")
    path = os.path.normpath(path)
    if path == "/":
        raise HelperError("Sharing the root filesystem is not allowed.")
    if not os.path.isdir(path):
        raise HelperError(f"Directory does not exist: {path}")
    real = os.path.realpath(path)
    if real == FTP_ROOT or real.startswith(FTP_ROOT + "/"):
        raise HelperError(f"Path cannot be inside {FTP_ROOT}.")
    return path


def clean_list(value):
    if not value:
        return []
    if not isinstance(value, list):
        raise HelperError("Invalid user/group list.")
    out = []
    for item in value:
        item = str(item).strip()
        if not item:
            continue
        if re.search(r'[\s,"@+&]', item):
            raise HelperError(f"Invalid user/group name: {item!r}")
        if item not in out:
            out.append(item)
    return out


def validate_principals(users, groups):
    for user in users:
        try:
            pwd.getpwnam(user)
        except KeyError:
            raise HelperError(f"Unknown system user: {user}")
    for group in groups:
        try:
            grp.getgrnam(group)
        except KeyError:
            raise HelperError(f"Unknown system group: {group}")


def parse_ports(text):
    if not text:
        return None
    m = PORTS_RE.match(text)
    if not m:
        raise HelperError("Passive ports must look like 49152-49200.")
    low, high = int(m.group(1)), int(m.group(2))
    if not (1024 <= low <= high <= 65535):
        raise HelperError("Passive ports must be within 1024-65535, start <= end.")
    return low, high


# --------------------------------------------------------------------------- proftpd

def proftpd_directives():
    """Active (non-comment) directives in the main proftpd.conf, keyed by lower-case name."""
    text = read_text(PROFTPD_CONF) or ""
    found = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<"):
            continue
        found.setdefault(s.split()[0].lower(), []).append(s)
    return found


def proftpd_include_ok(directives):
    for line in directives.get("include", []):
        parts = line.split()
        if len(parts) > 1 and parts[1].rstrip("/*") == PROFTPD_CONF_D:
            return True
    return False


def render_proftpd(desired, settings, directives):
    out = [
        "# Managed by cockpit-smbftp - do not edit by hand, changes are overwritten.",
        "",
        "# Every FTP user is jailed in the share root; each share is a bind mount.",
        f"DefaultRoot {FTP_ROOT}",
    ]
    if "requirevalidshell" not in directives:
        out.append("# Samba users often have a nologin shell")
        out.append("RequireValidShell off")
    ports = parse_ports(settings.get("passive_ports"))
    if ports and "passiveports" not in directives:
        out.append(f"PassivePorts {ports[0]} {ports[1]}")
    if settings.get("ftp_tls_required"):
        out += [
            "",
            "# FTPS (explicit TLS) required for every session",
            "TLSEngine on",
            "TLSLog /var/log/proftpd/tls.log",
            "TLSProtocol TLSv1.2 TLSv1.3",
            f"TLSRSACertificateFile {settings['tls_cert']}",
            f"TLSRSACertificateKeyFile {settings['tls_key']}",
            "TLSOptions NoSessionReuseRequired",
            "TLSRequired on",
        ]
    out += ["", f"<Directory {FTP_ROOT}>", "  HideNoAccess on", "</Directory>"]
    if settings.get("homes_ftp"):
        out += [
            "",
            f"# Home folders: every user sees only {FTP_HOME[len(FTP_ROOT):]}/<own username>",
            "DefaultChdir /home/%u",
            f"<Directory {FTP_HOME}/*>",
            "  <Limit ALL>",
            "    DenyAll",
            "  </Limit>",
            "</Directory>",
            f"<Directory {FTP_HOME}/%u>",
            "  <Limit ALL>",
            "    AllowAll",
            "  </Limit>",
            "</Directory>",
        ]
    for name in sorted(desired):
        item = desired[name]
        users, groups = split_principals(item["params"].get("valid users"))
        ro = item["read_only"]
        out += ["", f"# share: {name}  <-  {item['src']}", f"<Directory {FTP_ROOT}/{name}>"]
        if users or groups:
            out.append("  <Limit ALL>")
            out += [f"    AllowUser {u}" for u in users]
            out += [f"    AllowGroup {g}" for g in groups]
            out += ["    DenyAll", "  </Limit>"]
        if ro:
            out += ["  <Limit WRITE SITE_CHMOD>", "    DenyAll", "  </Limit>"]
        out.append("</Directory>")
    return "\n".join(out) + "\n"


def write_proftpd(desired, settings, warnings):
    """Write and validate the include file. Returns True if it changed."""
    old = read_text(PROFTPD_INC)
    if not desired and not settings.get("homes_ftp"):
        if old is not None:
            os.unlink(PROFTPD_INC)
            return True
        return False

    directives = proftpd_directives()
    if not proftpd_include_ok(directives):
        raise HelperError(f"{PROFTPD_CONF} does not include {PROFTPD_CONF_D}/ - "
                          f"add the line 'Include {PROFTPD_CONF_D}/' (install.sh does this).")
    if "defaultroot" in directives:
        warnings.append("proftpd.conf already has an active DefaultRoot - it may override "
                        f"the {FTP_ROOT} jail. Comment it out.")
    if settings.get("passive_ports") and "passiveports" in directives:
        warnings.append("PassivePorts is already set in proftpd.conf - that value is used.")
    if settings.get("ftp_tls_required"):
        for key in ("tls_cert", "tls_key"):
            if not os.path.isfile(settings[key]):
                raise HelperError(f"TLS file not found: {settings[key]}")

    content = render_proftpd(desired, settings, directives)
    if content == old:
        return False
    atomic_write(PROFTPD_INC, content)
    test = run(["proftpd", "-t"], check=False)
    if test.returncode != 0:
        if old is None:
            os.unlink(PROFTPD_INC)
        else:
            atomic_write(PROFTPD_INC, old)
        detail = (test.stderr or test.stdout).strip()
        if "TLS" in detail and "unknown configuration directive" in detail.lower():
            detail = ("mod_tls is not loaded. Install proftpd-mod-crypto and uncomment "
                      "'LoadModule mod_tls.c' in /etc/proftpd/modules.conf (install.sh does this).\n" + detail)
        raise HelperError("ProFTPD config test failed, changes rolled back:\n" + detail)
    return True


def apply_proftpd_service(has_shares, changed, warnings):
    if not have("systemctl") or not have("proftpd"):
        return
    if has_shares and not systemd_active("proftpd"):
        proc = run(["systemctl", "enable", "--now", "proftpd"], check=False)
        if proc.returncode != 0:
            warnings.append("Could not start proftpd: " + proc.stderr.strip())
    elif changed and systemd_active("proftpd"):
        proc = run(["systemctl", "reload-or-restart", "proftpd"], check=False)
        if proc.returncode != 0:
            warnings.append("Could not reload proftpd: " + proc.stderr.strip())


# --------------------------------------------------------------------------- bind mounts

def unmount(target, warnings):
    if target not in mountpoints():
        return
    if run(["umount", target], check=False).returncode != 0:
        run(["umount", "-l", target], check=False)
        warnings.append(f"{target} was busy - lazily unmounted (open FTP sessions keep it alive).")


def remove_dir(target, warnings):
    try:
        if os.path.isdir(target) and target not in mountpoints():
            os.rmdir(target)
    except OSError as e:
        warnings.append(f"Could not remove {target}: {e.strerror}")


def sync_mounts(want, warnings):
    text = read_text(FSTAB) or ""
    kept, managed = [], {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and not line.lstrip().startswith("#") and FSTAB_OPT_TAG in parts[3].split(","):
            managed[octal_unescape(parts[1])] = octal_unescape(parts[0])
            continue
        kept.append(line)

    for target, source in managed.items():
        if want.get(target) != source:
            unmount(target, warnings)
            if target not in want:
                remove_dir(target, warnings)

    os.makedirs(FTP_ROOT, exist_ok=True)
    os.chmod(FTP_ROOT, 0o755)

    while kept and not kept[-1].strip():
        kept.pop()
    new_lines = kept + [
        f"{fstab_escape(src)} {fstab_escape(tgt)} none bind,nofail,{FSTAB_OPT_TAG} 0 0"
        for tgt, src in sorted(want.items())
    ]
    new_text = "\n".join(new_lines) + "\n"
    if new_text != text:
        atomic_write(FSTAB, new_text)
        if have("systemctl"):
            run(["systemctl", "daemon-reload"], check=False)

    current = mountpoints()
    for target, source in want.items():
        os.makedirs(target, exist_ok=True)
        if target in current:
            if managed.get(target) == source:
                continue
            warnings.append(f"{target} is already mounted by something else - skipped.")
            continue
        proc = run(["mount", "--bind", source, target], check=False)
        if proc.returncode != 0:
            warnings.append(f"Bind mount failed for {target}: {proc.stderr.strip()}")


# --------------------------------------------------------------------------- sync

def sync_ftp(state, shares=None):
    warnings = []
    shares = registry_shares() if shares is None else shares
    by_key = {k.lower(): (k, v) for k, v in shares.items()}
    desired = {}
    for key, opts in state["ftp"].items():
        if key not in by_key:
            warnings.append(f"FTP entry '{key}' has no matching Samba share - skipped.")
            continue
        name, params = by_key[key]
        src = params.get("path", "")
        if not src or not os.path.isdir(src):
            warnings.append(f"Path of share '{name}' does not exist - not published over FTP.")
            continue
        smb_ro = share_view(name, params, state, set())["read_only"]
        desired[name] = {
            "src": os.path.normpath(src),
            "params": params,
            "read_only": smb_ro or bool(opts.get("read_only")),
        }

    homes = bool(state["settings"].get("homes_ftp"))
    if (desired or homes) and not have("proftpd"):
        raise HelperError("ProFTPD is not installed (sudo apt install proftpd-core).")

    want = {os.path.join(FTP_ROOT, name): item["src"] for name, item in desired.items()}
    if homes:
        if os.path.isdir(HOME_BASE):
            want[FTP_HOME] = HOME_BASE
        else:
            warnings.append(f"{HOME_BASE} does not exist - FTP home folders skipped.")

    changed = write_proftpd(desired, state["settings"], warnings) if have("proftpd") else False
    sync_mounts(want, warnings)
    apply_proftpd_service(bool(want), changed, warnings)
    return warnings


# --------------------------------------------------------------------------- commands

def cmd_status(_req):
    state = load_state()
    shares = registry_shares()
    mounted = mountpoints()
    directives = proftpd_directives() if have("proftpd") else {}
    user_details = list_users()
    users = [u["name"] for u in user_details]
    groups = sorted(g.gr_name for g in grp.getgrall()
                    if 1000 <= g.gr_gid < 65534 or g.gr_name in ("users", "sambashare"))
    return {
        "shares": sorted((share_view(n, p, state, mounted) for n, p in shares.items()
                          if n.lower() != "homes"),
                         key=lambda s: s["name"].lower()),
        "users": users,
        "user_details": user_details,
        "groups": groups,
        "homes": {
            "smb_active": find_share(shares, "homes") is not None,
            "ftp_mounted": FTP_HOME in mounted,
            "file_defined": file_has_homes(),
        },
        "settings": state["settings"],
        "ftp_root": FTP_ROOT,
        "checks": {
            "samba_installed": have("net") and os.path.exists(SMB_CONF),
            "registry_enabled": registry_enabled(),
            "file_shares": file_shares(),
            "proftpd_installed": have("proftpd"),
            "proftpd_active": systemd_active("proftpd"),
            "proftpd_include_ok": proftpd_include_ok(directives) if directives else False,
            "proftpd_defaultroot_conflict": "defaultroot" in directives,
            "pdbedit_installed": have("pdbedit"),
        },
    }


def cmd_save(req):
    if not registry_enabled():
        raise HelperError("Samba registry is not enabled in smb.conf - enable it first.")
    name = req.get("name")
    validate_name(name)
    path = validate_path(req.get("path"))
    comment = str(req.get("comment") or "").strip()
    if any(c in comment for c in "\n\r"):
        raise HelperError("Comment must be a single line.")
    users = clean_list(req.get("users"))
    groups = clean_list(req.get("groups"))
    validate_principals(users, groups)
    read_only = bool(req.get("read_only"))
    guest_ok = bool(req.get("guest_ok"))
    browseable = bool(req.get("browseable", True))
    ftp_on = bool(req.get("ftp"))
    is_new = bool(req.get("is_new"))

    shares = registry_shares()
    existing = find_share(shares, name)
    if is_new and existing:
        raise HelperError(f"A share named '{existing}' already exists.")
    if not is_new and not existing:
        raise HelperError(f"Share '{name}' not found in the Samba registry.")
    if is_new:
        run(["net", "conf", "addshare", name, path])
    else:
        name = existing

    def setparm(key, value):
        run(["net", "conf", "setparm", name, key, value])

    def delparm(key):
        run(["net", "conf", "delparm", name, key], check=False)

    setparm("path", path)
    if comment:
        setparm("comment", comment)
    else:
        delparm("comment")
    for key in ("writeable", "writable", "write ok", "public", "browsable"):
        delparm(key)
    setparm("read only", "yes" if read_only else "no")
    setparm("guest ok", "yes" if guest_ok else "no")
    setparm("browseable", "yes" if browseable else "no")
    principals = " ".join(users + ["@" + g for g in groups])
    if principals:
        setparm("valid users", principals)
    else:
        delparm("valid users")
    reload_samba()

    state = load_state()
    if ftp_on:
        state["ftp"][name.lower()] = {"read_only": bool(req.get("ftp_read_only"))}
    else:
        state["ftp"].pop(name.lower(), None)
    try:
        warnings = sync_ftp(state)
    except HelperError as e:
        raise HelperError(f"Samba share '{name}' saved, but FTP was not applied: {e}")
    save_state(state)
    return {"name": name, "warnings": warnings}


def cmd_delete(req):
    name = req.get("name")
    validate_name(name)
    ftp_only = bool(req.get("ftp_only"))
    shares = registry_shares()
    existing = find_share(shares, name)
    state = load_state()
    state["ftp"].pop(name.lower(), None)
    remaining = {k: v for k, v in shares.items() if ftp_only or k != existing}
    warnings = sync_ftp(state, remaining)
    save_state(state)
    if not ftp_only and existing:
        run(["net", "conf", "delshare", existing])
        reload_samba()
    return {"warnings": warnings}


def cmd_settings(req):
    state = load_state()
    settings = dict(state["settings"])
    settings["ftp_tls_required"] = bool(req.get("ftp_tls_required"))
    for key in ("tls_cert", "tls_key"):
        value = str(req.get(key) or DEFAULT_SETTINGS[key]).strip()
        if not value.startswith("/"):
            raise HelperError(f"{key} must be an absolute path.")
        settings[key] = value
    ports = str(req.get("passive_ports") or "").strip()
    parse_ports(ports)
    settings["passive_ports"] = ports
    settings["homes_smb"] = bool(req.get("homes_smb"))
    settings["homes_ftp"] = bool(req.get("homes_ftp"))
    state["settings"] = settings
    warnings = []
    apply_smb_homes(settings["homes_smb"], warnings)
    warnings += sync_ftp(state)
    save_state(state)
    return {"warnings": warnings}


def cmd_enable_registry(_req):
    text = read_text(SMB_CONF)
    if text is None:
        raise HelperError(f"{SMB_CONF} not found - is Samba installed?")
    if registry_enabled(text):
        return {"changed": False}
    lines = text.splitlines()
    gstart, gend = None, len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            if gstart is None and s.lower().replace(" ", "") == "[global]":
                gstart = i
            elif gstart is not None:
                gend = i
                break
    if gstart is None:
        new_lines = ["[global]", "   include = registry", ""] + lines
    else:
        insert_at = gend
        while insert_at - 1 > gstart and not lines[insert_at - 1].strip():
            insert_at -= 1
        new_lines = lines[:insert_at] + ["   include = registry"] + lines[insert_at:]
    shutil.copy2(SMB_CONF, SMB_CONF + ".cockpit-smbftp.bak")
    atomic_write(SMB_CONF, "\n".join(new_lines) + "\n")
    test = run(["testparm", "-s", SMB_CONF], check=False)
    if test.returncode != 0:
        atomic_write(SMB_CONF, text)
        raise HelperError("testparm rejected the change, rolled back:\n" + test.stderr.strip())
    reload_samba()
    return {"changed": True}


# --------------------------------------------------------------------------- users

def smb_accounts():
    """{username: {"disabled": bool}} from the Samba passdb."""
    if not have("pdbedit"):
        return {}
    proc = run(["pdbedit", "-L", "-w"], check=False)
    accounts = {}
    for line in proc.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 5:
            accounts[parts[0]] = {"disabled": "D" in parts[4]}
    return accounts


def list_users():
    accounts = smb_accounts()
    members = {}
    for g in grp.getgrall():
        for m in g.gr_mem:
            members.setdefault(m, []).append(g.gr_name)
    out = []
    for p in sorted(pwd.getpwall(), key=lambda x: x.pw_name):
        if not (1000 <= p.pw_uid < 65534):
            continue
        acct = accounts.get(p.pw_name)
        out.append({
            "name": p.pw_name,
            "uid": p.pw_uid,
            "full_name": p.pw_gecos.split(",")[0],
            "home": p.pw_dir,
            "shell": p.pw_shell,
            "can_login_shell": p.pw_shell not in (NOLOGIN_SHELL, "/bin/false", "/usr/bin/false", ""),
            "groups": sorted(members.get(p.pw_name, [])),
            "smb": "none" if acct is None else ("disabled" if acct["disabled"] else "active"),
        })
    return out


def regular_user(name):
    if not isinstance(name, str) or not USER_RE.match(name):
        raise HelperError("Username: lower-case letters, digits, '_' or '-', max 32 characters, "
                          "starting with a letter or '_'.")
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise HelperError(f"Unknown user: {name}")
    if not (1000 <= entry.pw_uid < 65534):
        raise HelperError(f"'{name}' is a system account and is not managed here.")
    return entry


def validate_password(password):
    if not isinstance(password, str) or len(password) < 8:
        raise HelperError("Password must have at least 8 characters.")
    if len(password) > 128 or any(c in password for c in "\n\r\x00"):
        raise HelperError("Password contains invalid characters or is too long.")


def validate_full_name(full_name):
    full_name = str(full_name or "").strip()
    if any(c in full_name for c in ":,\n\r"):
        raise HelperError("Full name cannot contain ':', ',' or line breaks.")
    return full_name


def set_passwords(name, password, smb):
    """System password (PAM: FTP, SSH, Cockpit) and, optionally, the Samba password - same value."""
    run(["chpasswd"], inp=f"{name}:{password}\n")
    if smb:
        if not have("smbpasswd"):
            raise HelperError("smbpasswd not found - is Samba installed?")
        run(["smbpasswd", "-a", "-s", name], inp=f"{password}\n{password}\n")
        run(["smbpasswd", "-e", name])


def cmd_user_create(req):
    name = req.get("name")
    if not isinstance(name, str) or not USER_RE.match(name):
        raise HelperError("Username: lower-case letters, digits, '_' or '-', max 32 characters, "
                          "starting with a letter or '_'.")
    try:
        pwd.getpwnam(name)
        raise HelperError(f"User '{name}' already exists.")
    except KeyError:
        pass
    full_name = validate_full_name(req.get("full_name"))
    groups = clean_list(req.get("groups"))
    validate_principals([], groups)
    password = req.get("password")
    validate_password(password)
    shell = LOGIN_SHELL if req.get("login_shell") else NOLOGIN_SHELL
    cmd = ["useradd", "-m", "-s", shell, "-c", full_name]
    if groups:
        cmd += ["-G", ",".join(groups)]
    run(cmd + [name])
    set_passwords(name, password, bool(req.get("smb", True)))
    return {"name": name, "warnings": []}


def cmd_user_update(req):
    entry = regular_user(req.get("name"))
    full_name = validate_full_name(req.get("full_name"))
    groups = clean_list(req.get("groups"))
    validate_principals([], groups)
    shell = LOGIN_SHELL if req.get("login_shell") else NOLOGIN_SHELL
    if entry.pw_shell not in (LOGIN_SHELL, NOLOGIN_SHELL) and bool(req.get("login_shell")):
        shell = entry.pw_shell  # keep a custom login shell (zsh, ...)
    run(["usermod", "-c", full_name, "-s", shell, "-G", ",".join(groups), entry.pw_name])
    return {"name": entry.pw_name, "warnings": []}


def cmd_user_password(req):
    entry = regular_user(req.get("name"))
    password = req.get("password")
    validate_password(password)
    set_passwords(entry.pw_name, password, bool(req.get("smb", True)))
    return {"name": entry.pw_name, "warnings": []}


def cmd_user_smb(req):
    entry = regular_user(req.get("name"))
    action = req.get("action")
    flags = {"enable": "-e", "disable": "-d", "remove": "-x"}
    if action not in flags:
        raise HelperError("Unknown Samba account action.")
    if entry.pw_name not in smb_accounts():
        raise HelperError(f"'{entry.pw_name}' has no Samba account - set a password first.")
    run(["smbpasswd", flags[action], entry.pw_name])
    return {"name": entry.pw_name, "warnings": []}


COMMANDS = {
    "status": cmd_status,
    "save": cmd_save,
    "delete": cmd_delete,
    "settings": cmd_settings,
    "enable-registry": cmd_enable_registry,
    "user-create": cmd_user_create,
    "user-update": cmd_user_update,
    "user-password": cmd_user_password,
    "user-smb": cmd_user_smb,
}


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if os.geteuid() != 0:
            raise HelperError("Administrative access is required.")
        if command not in COMMANDS:
            raise HelperError(f"Unknown command: {command!r}")
        raw = sys.stdin.read()
        request = json.loads(raw) if raw.strip() else {}
        with open(LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            reply = {"ok": True, "data": COMMANDS[command](request)}
    except HelperError as e:
        reply = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface anything unexpected to the UI
        reply = {"ok": False, "error": f"Unexpected error: {e!r}"}
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
