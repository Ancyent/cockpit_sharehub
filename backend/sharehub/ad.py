# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Active Directory membership with winbind: join, leave, status."""
import glob
import os
import re
import shutil
import socket
import tempfile

from .util import HelperError, atomic_write, have, read_text, run, systemctl

KRB5_CONF = "/etc/krb5.conf"
NSSWITCH = "/etc/nsswitch.conf"
REALM_RE = re.compile(r"^(?=.{3,253}$)[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
WORKGROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,14}$")
RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def global_params(state):
    ad = state["ad"]
    if not ad["enabled"]:
        return {}
    wg = ad["workgroup"]
    p = {
        "security": "ADS",
        "realm": ad["realm"].upper(),
        "workgroup": wg,
        "idmap config * : backend": "tdb",
        "idmap config * : range": "3000-7999",
        f"idmap config {wg} : backend": ad["idmap_backend"],
        f"idmap config {wg} : range": ad["idmap_range"],
        "winbind use default domain": "yes" if ad["default_domain"] else "no",
        "winbind enum users": "yes",
        "winbind enum groups": "yes",
        "winbind refresh tickets": "yes",
        "template shell": "/bin/bash" if ad["login_shell"] else "/usr/sbin/nologin",
        "template homedir": ad["template_homedir"],
        "kerberos method": "secrets and keytab",
    }
    if ad["idmap_backend"] == "ad":
        p[f"idmap config {wg} : schema_mode"] = "rfc2307"
        p[f"idmap config {wg} : unix_nss_info"] = "yes"
    return p


def prerequisites():
    missing = []
    for binary, package in (("net", "samba-common-bin"), ("winbindd", "winbind"),
                            ("wbinfo", "winbind"), ("kinit", "krb5-user")):
        if not have(binary):
            missing.append(package)
    if not glob.glob("/usr/lib/*/libnss_winbind.so.2") and not glob.glob("/lib/*/libnss_winbind.so.2"):
        missing.append("libnss-winbind")
    if not glob.glob("/usr/lib/*/security/pam_winbind.so") and not glob.glob("/lib/*/security/pam_winbind.so"):
        missing.append("libpam-winbind")
    return sorted(set(missing))


def preflight(realm):
    """Checks shown in the join wizard before anything is changed."""
    checks = []
    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(realm, None)})
        checks.append({"name": "DNS", "ok": True, "detail": f"{realm} resolves to {', '.join(addrs[:4])}"})
    except socket.gaierror:
        checks.append({"name": "DNS", "ok": False,
                       "detail": f"{realm} does not resolve. Point this server's DNS at the domain controllers."})
    if have("host"):
        proc = run(["host", "-t", "SRV", f"_ldap._tcp.dc._msdcs.{realm}"], check=False, timeout=15)
        checks.append({"name": "Domain controllers (SRV)", "ok": proc.returncode == 0,
                       "detail": proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "No SRV records found."})
    if have("timedatectl"):
        synced = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], check=False).stdout.strip()
        checks.append({"name": "Time sync", "ok": synced == "yes",
                       "detail": "Clock is NTP-synchronised." if synced == "yes" else
                       "Clock is not NTP-synchronised; Kerberos needs less than 5 minutes of skew."})
    missing = prerequisites()
    checks.append({"name": "Packages", "ok": not missing,
                   "detail": "All required packages are installed." if not missing else
                   "Missing: " + ", ".join(missing)})
    return checks


def normalize_join(req):
    realm = str(req.get("realm") or "").strip().lower()
    if not REALM_RE.match(realm):
        raise HelperError("Domain must be a DNS name such as corp.example.com.")
    workgroup = str(req.get("workgroup") or realm.split(".")[0]).strip().upper()
    if not WORKGROUP_RE.match(workgroup):
        raise HelperError("NetBIOS domain name: up to 15 letters, digits, '_' or '-'.")
    backend = req.get("idmap_backend") or "rid"
    if backend not in ("rid", "ad"):
        raise HelperError("ID mapping must be 'rid' or 'ad'.")
    rng = str(req.get("idmap_range") or "10000-999999").strip()
    m = RANGE_RE.match(rng)
    if not m or not (8000 <= int(m.group(1)) < int(m.group(2)) <= 2147483647):
        raise HelperError("ID range must look like 10000-999999 and start at 8000 or higher.")
    default_domain = bool(req.get("default_domain", True))
    return {
        "enabled": True,
        "realm": realm,
        "workgroup": workgroup,
        "idmap_backend": backend,
        "idmap_range": rng,
        "default_domain": default_domain,
        "login_shell": bool(req.get("login_shell")),
        "template_homedir": "/home/%U" if default_domain else "/home/%D/%U",
    }


def _authfile(user, password, realm):
    if not user or not isinstance(user, str) or any(c in user for c in "\n\r"):
        raise HelperError("Enter the name of a domain account allowed to join computers.")
    if not password or any(c in password for c in "\n\r"):
        raise HelperError("Enter the password of the domain account.")
    fd, path = tempfile.mkstemp(prefix="sharehub-ad-")
    with os.fdopen(fd, "w") as f:
        f.write(f"username={user}\npassword={password}\ndomain={realm.upper()}\n")
    os.chmod(path, 0o600)
    return path


def write_krb5(realm):
    """Minimal krb5.conf that finds KDCs through DNS. The previous file is kept as .sharehub.bak."""
    text = read_text(KRB5_CONF)
    if text and re.search(rf"default_realm\s*=\s*{re.escape(realm.upper())}\b", text):
        return
    if text is not None:
        shutil.copy2(KRB5_CONF, KRB5_CONF + ".sharehub.bak")
    atomic_write(KRB5_CONF, "# Written by ShareHub (previous file: krb5.conf.sharehub.bak)\n"
                            "[libdefaults]\n"
                            f"    default_realm = {realm.upper()}\n"
                            "    dns_lookup_realm = false\n"
                            "    dns_lookup_kdc = true\n"
                            "    rdns = false\n")


def set_nsswitch(enable):
    text = read_text(NSSWITCH)
    if text is None:
        return
    lines = []
    for line in text.splitlines():
        m = re.match(r"^(passwd|group):(\s*)(.*)$", line)
        if m:
            sources = [s for s in m.group(3).split() if s != "winbind"]
            if enable:
                sources.append("winbind")
            line = f"{m.group(1)}:{m.group(2) or ' '}{' '.join(sources)}"
        lines.append(line)
    new = "\n".join(lines) + "\n"
    if new != text:
        atomic_write(NSSWITCH, new)


def set_pam(enable, warnings):
    if not have("pam-auth-update"):
        warnings.append("pam-auth-update not found - configure PAM for winbind manually.")
        return
    flag = "--enable" if enable else "--disable"
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    proc = run(["pam-auth-update", "--package", flag, "winbind", flag, "mkhomedir"], check=False, env=env)
    if proc.returncode != 0:
        warnings.append("pam-auth-update: " + (proc.stderr or proc.stdout).strip())


def join(req, state, apply_samba, warnings):
    missing = prerequisites()
    if missing:
        raise HelperError("Install these packages first: " + " ".join(missing))
    previous = dict(state["ad"])
    state["ad"] = normalize_join(req)
    realm = state["ad"]["realm"]
    old_krb5 = read_text(KRB5_CONF)
    authfile = _authfile(req.get("admin_user"), req.get("admin_password"), realm)
    try:
        write_krb5(realm)
        apply_samba(state, warnings)
        proc = run(["net", "ads", "join", "-A", authfile], check=False, timeout=180)
        if proc.returncode != 0:
            raise HelperError("Join failed: " + (proc.stderr or proc.stdout).strip())
    except Exception:
        state["ad"] = previous
        try:
            apply_samba(state, [])
        finally:
            if old_krb5 is not None:
                atomic_write(KRB5_CONF, old_krb5)
        raise
    finally:
        os.unlink(authfile)
    set_nsswitch(True)
    set_pam(True, warnings)
    systemctl("enable", "--now", "winbind", warnings=warnings)
    systemctl("restart", "smbd", warnings=warnings)
    if run(["wbinfo", "-t"], check=False).returncode != 0:
        warnings.append("Joined, but 'wbinfo -t' does not confirm the trust yet - check winbind in a minute.")
    return state


def leave(req, state, apply_samba, warnings):
    if not state["ad"]["enabled"]:
        raise HelperError("This server is not joined to a domain.")
    realm = state["ad"]["realm"]
    if req.get("admin_user"):
        authfile = _authfile(req.get("admin_user"), req.get("admin_password"), realm)
        try:
            proc = run(["net", "ads", "leave", "-A", authfile], check=False, timeout=120)
            if proc.returncode != 0:
                warnings.append("Domain leave failed (the computer account may remain in AD): "
                                + (proc.stderr or proc.stdout).strip())
        finally:
            os.unlink(authfile)
    else:
        warnings.append("Left locally without domain credentials - delete the computer account in AD.")
    state["ad"]["enabled"] = False
    apply_samba(state, warnings)
    set_nsswitch(False)
    set_pam(False, warnings)
    systemctl("disable", "--now", "winbind", warnings=warnings)
    systemctl("restart", "smbd", warnings=warnings)
    return state


def status(state):
    ad = state["ad"]
    out = {"enabled": ad["enabled"], "realm": ad["realm"], "workgroup": ad["workgroup"],
           "idmap_backend": ad["idmap_backend"], "idmap_range": ad["idmap_range"],
           "default_domain": ad["default_domain"], "login_shell": ad["login_shell"],
           "missing_packages": prerequisites()}
    if ad["enabled"]:
        out["testjoin"] = run(["net", "ads", "testjoin"], check=False, timeout=30).returncode == 0
        out["trust"] = run(["wbinfo", "-t"], check=False, timeout=30).returncode == 0 if have("wbinfo") else False
        info = run(["net", "ads", "info"], check=False, timeout=30).stdout
        for line in info.splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key in ("ldap server name", "server time", "time offset", "realm"):
                out[key.replace(" ", "_")] = value.strip()
    return out
