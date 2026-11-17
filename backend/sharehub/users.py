# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Local users and groups with one password for the system (PAM: FTP, SSH) and Samba."""
import grp
import pwd
import re

from .util import HelperError, have, run

# Names ShareHub creates: the conservative Debian/useradd default.
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
GROUP_RE = USER_RE
# Names of accounts that already exist (created by adduser, Cockpit Accounts, other tools):
# accept upper case and dots too, but never a leading '-' or characters unsafe for
# chpasswd ("name:password"), smb.conf or ProFTPD lists.
EXISTING_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]{0,63}$")
LOGIN_SHELL = "/bin/bash"
NOLOGIN_SHELL = "/usr/sbin/nologin"
NOLOGIN_SHELLS = (NOLOGIN_SHELL, "/sbin/nologin", "/bin/false", "/usr/bin/false", "")
DOMAIN_LIMIT = 5000


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


def _local_group_members():
    members = {}
    for g in grp.getgrall():
        for m in g.gr_mem:
            members.setdefault(m, []).append(g.gr_name)
    return members


def local_users():
    accounts = smb_accounts()
    members = _local_group_members()
    out = []
    for p in sorted(pwd.getpwall(), key=lambda x: x.pw_name):
        if not (1000 <= p.pw_uid < 65534) or "\\" in p.pw_name:
            continue
        acct = accounts.get(p.pw_name)
        out.append({
            "name": p.pw_name,
            "uid": p.pw_uid,
            "full_name": p.pw_gecos.split(",")[0],
            "home": p.pw_dir,
            "shell": p.pw_shell,
            "login_shell": p.pw_shell not in NOLOGIN_SHELLS,
            "groups": sorted(members.get(p.pw_name, [])),
            "smb": "none" if acct is None else ("disabled" if acct["disabled"] else "active"),
            "domain": False,
        })
    return out


def local_groups():
    out = []
    for g in sorted(grp.getgrall(), key=lambda x: x.gr_name):
        if (1000 <= g.gr_gid < 65534 or g.gr_name in ("users", "sambashare")) and "\\" not in g.gr_name:
            out.append({"name": g.gr_name, "gid": g.gr_gid, "members": sorted(g.gr_mem), "domain": False})
    return out


def domain_names(kind):
    """Domain users or groups via winbind (empty when not joined)."""
    if not have("wbinfo"):
        return []
    proc = run(["wbinfo", "-u" if kind == "user" else "-g"], check=False, timeout=30)
    if proc.returncode != 0:
        return []
    return sorted(set(line.strip() for line in proc.stdout.splitlines() if line.strip()))[:DOMAIN_LIMIT]


def listing(state):
    users = local_users()
    groups = local_groups()
    if state["ad"]["enabled"]:
        users += [{"name": n, "domain": True} for n in domain_names("user")]
        groups += [{"name": n, "domain": True} for n in domain_names("group")]
    return users, groups


# --------------------------------------------------------------------------- validation

def regular_user(name):
    if not isinstance(name, str) or not EXISTING_RE.match(name):
        raise HelperError(f"Unsupported user name: {name!r}")
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
    return full_name[:100]


def local_group_names(value):
    if not value:
        return []
    out = []
    for g in value:
        g = str(g).strip()
        try:
            entry = grp.getgrnam(g)
        except KeyError:
            raise HelperError(f"Unknown group: {g}")
        if "\\" in g or not (1000 <= entry.gr_gid < 65534 or g in ("users", "sambashare")):
            raise HelperError(f"'{g}' is not a local file-sharing group.")
        if g not in out:
            out.append(g)
    return out


def set_passwords(name, password, smb):
    """Same password for the system account and, optionally, Samba."""
    run(["chpasswd"], inp=f"{name}:{password}\n")
    if smb:
        if not have("smbpasswd"):
            raise HelperError("smbpasswd not found - is Samba installed?")
        run(["smbpasswd", "-a", "-s", name], inp=f"{password}\n{password}\n")
        run(["smbpasswd", "-e", name])


# --------------------------------------------------------------------------- user commands

def create_user(req):
    name = req.get("name")
    if not isinstance(name, str) or not USER_RE.match(name):
        raise HelperError("Username: lower-case letters, digits, '_' or '-', max 32 characters, "
                          "starting with a letter or '_'.")
    try:
        pwd.getpwnam(name)
    except KeyError:
        pass
    else:
        raise HelperError(f"User '{name}' already exists.")
    full_name = validate_full_name(req.get("full_name"))
    groups = local_group_names(req.get("groups"))
    password = req.get("password")
    validate_password(password)
    shell = LOGIN_SHELL if req.get("login_shell") else NOLOGIN_SHELL
    cmd = ["useradd", "-m", "-s", shell, "-c", full_name]
    if groups:
        cmd += ["-G", ",".join(groups)]
    run(cmd + [name])
    set_passwords(name, password, bool(req.get("smb", True)))
    return {"name": name}


def update_user(req):
    entry = regular_user(req.get("name"))
    full_name = validate_full_name(req.get("full_name"))
    groups = local_group_names(req.get("groups"))
    want_login = bool(req.get("login_shell"))
    if want_login:
        shell = entry.pw_shell if entry.pw_shell not in NOLOGIN_SHELLS else LOGIN_SHELL
    else:
        shell = NOLOGIN_SHELL
    # keep membership in non file-sharing groups (sudo, adm, ...) untouched
    keep = [g.gr_name for g in grp.getgrall()
            if entry.pw_name in g.gr_mem and not (1000 <= g.gr_gid < 65534 or g.gr_name in ("users", "sambashare"))]
    run(["usermod", "-c", full_name, "-s", shell, "-G", ",".join(keep + groups), entry.pw_name])
    return {"name": entry.pw_name}


def set_password(req):
    entry = regular_user(req.get("name"))
    password = req.get("password")
    validate_password(password)
    set_passwords(entry.pw_name, password, bool(req.get("smb", True)))
    return {"name": entry.pw_name}


def smb_account(req):
    entry = regular_user(req.get("name"))
    flags = {"enable": "-e", "disable": "-d", "remove": "-x"}
    action = req.get("action")
    if action not in flags:
        raise HelperError("Unknown Samba account action.")
    if entry.pw_name not in smb_accounts():
        raise HelperError(f"'{entry.pw_name}' has no Samba account - set a password first.")
    run(["smbpasswd", flags[action], entry.pw_name])
    return {"name": entry.pw_name}


# --------------------------------------------------------------------------- group commands

def create_group(req):
    name = req.get("name")
    if not isinstance(name, str) or not GROUP_RE.match(name):
        raise HelperError("Group name: lower-case letters, digits, '_' or '-', max 32 characters.")
    try:
        grp.getgrnam(name)
    except KeyError:
        pass
    else:
        raise HelperError(f"Group '{name}' already exists.")
    members = [regular_user(m).pw_name for m in (req.get("members") or [])]
    run(["groupadd", name])
    if members:
        run(["gpasswd", "-M", ",".join(members), name])
    return {"name": name}


def update_group(req):
    name = req.get("name")
    local_group_names([name])
    members = [regular_user(m).pw_name for m in (req.get("members") or [])]
    run(["gpasswd", "-M", ",".join(members), name])
    return {"name": name}


def delete_group(req, state):
    name = req.get("name")
    local_group_names([name])
    used = [s for s, share in state["shares"].items() if name in share["access"]["groups"]]
    if used:
        raise HelperError(f"Group '{name}' is used by: {', '.join(sorted(used))}. Remove it from those shares first.")
    for p in pwd.getpwall():
        if p.pw_gid == grp.getgrnam(name).gr_gid:
            raise HelperError(f"Group '{name}' is the primary group of {p.pw_name}.")
    run(["groupdel", name])
    return {"name": name}
