# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""FTP/FTPS via ProFTPD: a chroot under FTP_ROOT with one bind mount per share,
per-share access mirroring the share's users/groups, and optional home folders."""
import os

from .paths import FTP_ROOT, HOME_BASE
from .state import parse_ports
from .util import (HelperError, atomic_write, fstab_escape, have, mountpoints, octal_unescape,
                   read_text, remove_file, run, systemctl, unit_state)

PROFTPD_CONF = "/etc/proftpd/proftpd.conf"
PROFTPD_CONF_D = "/etc/proftpd/conf.d"
PROFTPD_INC = os.path.join(PROFTPD_CONF_D, "sharehub.conf")
FSTAB = "/etc/fstab"
FSTAB_TAG = "x-sharehub"
FTP_HOME = os.path.join(FTP_ROOT, "home")


def directives():
    """Active directives in the main proftpd.conf, keyed by lower-case name."""
    found = {}
    for line in (read_text(PROFTPD_CONF) or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<"):
            continue
        found.setdefault(s.split()[0].lower(), []).append(s)
    return found


def include_ok(found):
    for line in found.get("include", []):
        parts = line.split()
        if len(parts) > 1 and parts[1].rstrip("/*") == PROFTPD_CONF_D:
            return True
    return False


def quote(value):
    return f'"{value}"' if " " in value else value


def desired_shares(state, warnings):
    out = {}
    for name, share in state["shares"].items():
        if not share["ftp"]["enabled"]:
            continue
        if not os.path.isdir(share["path"]):
            warnings.append(f"{name}: path does not exist - not published over FTP.")
            continue
        out[name] = share
    return out


def render(state, shares, found):
    settings = state["ftp"]
    homes = settings["homes"]
    if not shares and not homes:
        return None
    out = ["# Managed by ShareHub - do not edit by hand, changes are overwritten.", "",
           "# Every FTP user is jailed in the share root; each share is a bind mount.",
           f"DefaultRoot {FTP_ROOT}"]
    if "requirevalidshell" not in found:
        out += ["# File-sharing users usually have a nologin shell", "RequireValidShell off"]
    ports = parse_ports(settings["passive_ports"])
    if ports and "passiveports" not in found:
        out.append(f"PassivePorts {ports[0]} {ports[1]}")
    if settings["tls_required"]:
        out += ["", "# FTPS (explicit TLS) required for every session",
                "TLSEngine on", "TLSLog /var/log/proftpd/tls.log", "TLSProtocol TLSv1.2 TLSv1.3",
                f"TLSRSACertificateFile {settings['tls_cert']}",
                f"TLSRSACertificateKeyFile {settings['tls_key']}",
                "TLSOptions NoSessionReuseRequired", "TLSRequired on"]
    out += ["", f"<Directory {FTP_ROOT}>", "  HideNoAccess on", "</Directory>"]
    if homes:
        out += ["", "# Home folders: each user can only enter /home/<own username>",
                "DefaultChdir /home/%u",
                f"<Directory {FTP_HOME}/*>", "  <Limit ALL>", "    DenyAll", "  </Limit>", "</Directory>",
                f"<Directory {FTP_HOME}/%u>", "  <Limit ALL>", "    AllowAll", "  </Limit>", "</Directory>"]
    for name in sorted(shares):
        share = shares[name]
        acc = share["access"]
        out += ["", f"# share: {name}  <-  {share['path']}", f"<Directory {FTP_ROOT}/{name}>"]
        if acc["users"] or acc["groups"]:
            out.append("  <Limit ALL>")
            out += [f"    AllowUser {quote(u)}" for u in acc["users"]]
            out += [f"    AllowGroup {quote(g)}" for g in acc["groups"]]
            out += ["    DenyAll", "  </Limit>"]
        if acc["read_only"] or share["ftp"]["read_only"]:
            out += ["  <Limit WRITE SITE_CHMOD>", "    DenyAll", "  </Limit>"]
        out.append("</Directory>")
    return "\n".join(out) + "\n"


def write_conf(content, warnings):
    """Write and validate the include. Returns True if it changed."""
    old = read_text(PROFTPD_INC)
    if content == old:
        return False
    if content is None:
        remove_file(PROFTPD_INC)
        return True
    os.makedirs(PROFTPD_CONF_D, exist_ok=True)
    atomic_write(PROFTPD_INC, content)
    test = run(["proftpd", "-t"], check=False)
    if test.returncode != 0:
        if old is None:
            remove_file(PROFTPD_INC)
        else:
            atomic_write(PROFTPD_INC, old)
        detail = (test.stderr or test.stdout).strip()
        if "TLS" in detail and "unknown configuration directive" in detail.lower():
            detail = ("mod_tls is not loaded. Install proftpd-mod-crypto and enable "
                      "'LoadModule mod_tls.c' in /etc/proftpd/modules.conf.\n" + detail)
        raise HelperError("ProFTPD config test failed, changes rolled back:\n" + detail)
    return True


# --------------------------------------------------------------------------- bind mounts

def _unmount(target, warnings):
    if target not in mountpoints():
        return
    if run(["umount", target], check=False).returncode != 0:
        run(["umount", "-l", target], check=False)
        warnings.append(f"{target} was busy - lazily unmounted (open FTP sessions keep it alive).")


def _rmdir(target, warnings):
    try:
        if os.path.isdir(target) and target not in mountpoints():
            os.rmdir(target)
    except OSError as e:
        warnings.append(f"Could not remove {target}: {e.strerror}")


def managed_mounts():
    out = {}
    for line in (read_text(FSTAB) or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and not line.lstrip().startswith("#") and FSTAB_TAG in parts[3].split(","):
            out[octal_unescape(parts[1])] = octal_unescape(parts[0])
    return out


def sync_mounts(want, warnings):
    text = read_text(FSTAB) or ""
    kept = []
    managed = managed_mounts()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and not line.lstrip().startswith("#") and FSTAB_TAG in parts[3].split(","):
            continue
        kept.append(line)

    for target, source in managed.items():
        if want.get(target) != source:
            _unmount(target, warnings)
            if target not in want:
                _rmdir(target, warnings)

    if want:
        os.makedirs(FTP_ROOT, exist_ok=True)
        os.chmod(FTP_ROOT, 0o755)
    while kept and not kept[-1].strip():
        kept.pop()
    new_lines = kept + [f"{fstab_escape(src)} {fstab_escape(tgt)} none bind,nofail,{FSTAB_TAG} 0 0"
                        for tgt, src in sorted(want.items())]
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


# --------------------------------------------------------------------------- apply

def apply(state, warnings):
    shares = desired_shares(state, warnings)
    homes = state["ftp"]["homes"]
    if (shares or homes) and not have("proftpd"):
        raise HelperError("ProFTPD is not installed (apt install proftpd-core).")
    if not have("proftpd"):
        sync_mounts({}, warnings)
        return

    found = directives()
    if (shares or homes) and not include_ok(found):
        raise HelperError(f"{PROFTPD_CONF} does not include {PROFTPD_CONF_D}/ - add the line "
                          f"'Include {PROFTPD_CONF_D}/' (the package does this on install).")
    if (shares or homes) and "defaultroot" in found:
        warnings.append(f"proftpd.conf has its own DefaultRoot - comment it out so users are jailed in {FTP_ROOT}.")
    if state["ftp"]["tls_required"]:
        for key in ("tls_cert", "tls_key"):
            if not os.path.isfile(state["ftp"][key]):
                raise HelperError(f"TLS file not found: {state['ftp'][key]}")

    changed = write_conf(render(state, shares, found), warnings)

    want = {os.path.join(FTP_ROOT, name): os.path.normpath(s["path"]) for name, s in shares.items()}
    if homes:
        if os.path.isdir(HOME_BASE):
            want[FTP_HOME] = HOME_BASE
        else:
            warnings.append(f"{HOME_BASE} does not exist - FTP home folders skipped.")
    sync_mounts(want, warnings)

    active, _ = unit_state("proftpd")
    if want and active != "active":
        systemctl("enable", "--now", "proftpd", warnings=warnings)
    elif changed and active == "active":
        systemctl("reload-or-restart", "proftpd", warnings=warnings)


def sessions():
    """Current FTP sessions from ftpwho."""
    if not have("ftpwho"):
        return []
    proc = run(["ftpwho", "-v", "-o", "oneline"], check=False)
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("standalone", "Service class", "no users")) or "users)" in line:
            continue
        out.append(line)
    return out
