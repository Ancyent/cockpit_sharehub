# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Samba: registry-backed global settings, shares, [homes], macOS/Time Machine,
recycle bin, Previous Versions (shadow copies) and Windows ACLs."""
import configparser
import json
import os
import re
import shutil
import tempfile

from . import paths
from .util import HelperError, atomic_write, fs_type, have, mount_source, read_text, run

SMB_CONF = "/etc/samba/smb.conf"
OWNED_FILE = os.path.join(paths.STATE_DIR, "owned-smb.json")

# Global parameters ShareHub owns. Anything listed here and not rendered is removed
# from the registry [global]; other registry globals are left alone.
MANAGED_GLOBAL = {
    "workgroup", "server string", "netbios name", "disable netbios", "smb ports",
    "server min protocol", "server smb encrypt", "server signing", "map to guest",
    "server multi channel support", "multicast dns register",
    "fruit:metadata", "fruit:model", "fruit:posix_rename", "fruit:veto_appledouble",
    "fruit:nfs_aces", "fruit:wipe_intentionally_left_blank_rfork", "fruit:delete_empty_adfiles",
    # Active Directory (rendered by ad.global_params)
    "security", "realm", "winbind use default domain", "winbind enum users",
    "winbind enum groups", "winbind refresh tickets", "template shell", "template homedir",
    "kerberos method", "dedicated keytab file", "winbind offline logon",
}
MANAGED_GLOBAL_PREFIXES = ("idmap config ",)

RECYCLE_EXCLUDE = "*.tmp|*.temp|~$*|*.~*|.DS_Store|._*|Thumbs.db"


# --------------------------------------------------------------------------- parsing

def parse_conf(text):
    """Parse smb.conf-style text into {section: {param(lower): value}}."""
    clean = "\n".join(line.strip() for line in text.splitlines())
    cp = configparser.RawConfigParser(strict=False, delimiters=("=",),
                                      comment_prefixes=("#", ";"), default_section="\x00none")
    cp.optionxform = lambda opt: " ".join(opt.lower().split())
    cp.read_string(clean)
    return {sec: dict(cp.items(sec)) for sec in cp.sections()}


def registry():
    """All registry sections, including [global]."""
    if not have("net"):
        return {}
    proc = run(["net", "conf", "list"], check=False)
    if proc.returncode != 0:
        return {}
    return parse_conf(proc.stdout)


def file_sections():
    text = read_text(SMB_CONF)
    if not text:
        return {}
    try:
        return parse_conf(text)
    except configparser.Error:
        return {}


def file_shares():
    return sorted(k for k in file_sections()
                  if k.lower() not in ("global", "homes", "printers", "print$"))


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


def enable_registry():
    text = read_text(SMB_CONF)
    if text is None:
        raise HelperError(f"{SMB_CONF} not found - is Samba installed?")
    if registry_enabled(text):
        return False
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
        new_lines = lines[:insert_at] + ["   # Added by ShareHub: settings live in the Samba registry",
                                         "   include = registry"] + lines[insert_at:]
    shutil.copy2(SMB_CONF, SMB_CONF + ".sharehub.bak")
    atomic_write(SMB_CONF, "\n".join(new_lines) + "\n")
    test = run(["testparm", "-s", SMB_CONF], check=False)
    if test.returncode != 0:
        atomic_write(SMB_CONF, text)
        raise HelperError("testparm rejected the change, rolled back:\n" + test.stderr.strip())
    reload()
    return True


def reload():
    if have("smbcontrol"):
        run(["smbcontrol", "smbd", "reload-config"], check=False)


# --------------------------------------------------------------------------- rendering

def quote_principal(name, group=False):
    value = ("@" if group else "") + name
    return f'"{value}"' if " " in value else value


def render_global(state):
    s = state["samba"]
    g = {
        "server string": s["server_string"] or "%h file server",
        "server min protocol": s["min_protocol"],
        "server multi channel support": "yes" if s["multichannel"] else "no",
        "multicast dns register": "yes" if s["mdns"] else "no",
    }
    if not state["ad"]["enabled"]:
        g["workgroup"] = s["workgroup"]
    if s["netbios_name"]:
        g["netbios name"] = s["netbios_name"]
    if not s["netbios"]:
        g["disable netbios"] = "yes"
        g["smb ports"] = "445"
    if any(sh["smb"]["enabled"] and sh["smb"]["guest_ok"] for sh in state["shares"].values()):
        g["map to guest"] = "Bad User"
    if s["encryption"] != "default":
        g["server smb encrypt"] = s["encryption"]
    if s["signing"] != "default":
        g["server signing"] = s["signing"]
    if s["macos"]:
        g.update({
            "fruit:metadata": "stream",
            "fruit:model": s["macos_model"],
            "fruit:posix_rename": "yes",
            "fruit:veto_appledouble": "no",
            "fruit:nfs_aces": "no",
            "fruit:wipe_intentionally_left_blank_rfork": "yes",
            "fruit:delete_empty_adfiles": "yes",
        })
    from . import ad
    g.update(ad.global_params(state))
    return g


def shadow_params(path, warnings, share_name):
    """vfs module + parameters for Previous Versions, based on the filesystem under path."""
    fstype = fs_type(path)
    if fstype == "zfs" or os.path.isdir(os.path.join(mount_source(path)[1] or "/nonexistent", ".zfs")):
        mountpoint = mount_source(path)[1]
        snapdir = os.path.join(mountpoint, ".zfs", "snapshot")
        names = []
        try:
            names = os.listdir(snapdir)
        except OSError:
            pass
        sanoid = any(n.startswith("autosnap_") for n in names) or (
            not any(n.startswith("zfs-auto-snap") for n in names) and have("sanoid"))
        params = {"shadow:mountpoint": mountpoint, "shadow:snapdir": ".zfs/snapshot",
                  "shadow:sort": "desc"}
        if sanoid:
            # Samba cannot map a timestamp back to a snapshot name that has a suffix after
            # the time (autosnap_<time>_hourly), so one sanoid frequency is exposed.
            present = {n.rsplit("_", 1)[-1] for n in names if n.startswith("autosnap_")}
            freq = next((f for f in ("hourly", "daily", "frequently", "weekly", "monthly", "yearly")
                         if f in present), "hourly")
            params.update({"shadow:format": f"autosnap_%Y-%m-%d_%H:%M:%S_{freq}",
                           "shadow:localtime": "yes"})
            if len(present) > 1:
                warnings.append(f"{share_name}: Previous Versions show the sanoid '{freq}' snapshots "
                                "(Samba can expose one sanoid frequency per share).")
        else:
            params.update({
                "shadow:snapprefix": r"^zfs-auto-snap_\(frequent\)\{0,1\}\(hourly\)\{0,1\}"
                                     r"\(daily\)\{0,1\}\(weekly\)\{0,1\}\(monthly\)\{0,1\}",
                "shadow:delimiter": "-20",
                "shadow:format": "-%Y-%m-%d-%H%M",
                "shadow:localtime": "no",
            })
        if not names:
            warnings.append(f"{share_name}: no ZFS snapshots yet - Previous Versions will appear "
                            f"once {'sanoid' if sanoid else 'zfs-auto-snapshot'} creates some.")
        return "shadow_copy2", params
    if fstype == "btrfs":
        if not have("snapper"):
            warnings.append(f"{share_name}: Btrfs Previous Versions need snapper (apt install snapper) "
                            "with a config for this subvolume.")
        return "snapper", {}
    warnings.append(f"{share_name}: Previous Versions need ZFS or Btrfs snapshots; "
                    f"{path} is on {fstype or 'an unknown filesystem'} - option ignored.")
    return None, {}


def render_share(name, share, state, warnings):
    smb = share["smb"]
    acc = share["access"]
    p = {
        "path": share["path"],
        "read only": "yes" if acc["read_only"] else "no",
        "guest ok": "yes" if smb["guest_ok"] else "no",
        "browseable": "yes" if smb["browseable"] else "no",
    }
    if share["comment"]:
        p["comment"] = share["comment"]
    principals = [quote_principal(u) for u in acc["users"]] + \
                 [quote_principal(g, True) for g in acc["groups"]]
    if principals:
        p["valid users"] = " ".join(principals)

    vfs = []
    if state["samba"]["macos"] or smb["time_machine"]:
        vfs += ["catia", "fruit", "streams_xattr"]
    if smb["windows_acls"]:
        vfs.append("acl_xattr")
        p["map acl inherit"] = "yes"
        p["store dos attributes"] = "yes"
    if smb["shadow_copies"]:
        module, params = shadow_params(share["path"], warnings, name)
        if module:
            vfs.append(module)
            p.update(params)
    if smb["recycle"]:
        vfs.append("recycle")
        p.update({
            "recycle:repository": ".recycle/%U",
            "recycle:keeptree": "yes",
            "recycle:versions": "yes",
            "recycle:touch_mtime": "yes",
            "recycle:directory_mode": "0700",
            "recycle:exclude": RECYCLE_EXCLUDE,
            "recycle:exclude_dir": ".recycle,tmp,temp",
        })
    if smb["time_machine"]:
        p["fruit:time machine"] = "yes"
        if smb["time_machine_max_size"]:
            p["fruit:time machine max size"] = smb["time_machine_max_size"]
    if vfs:
        p["vfs objects"] = " ".join(vfs)
    return p


def render_homes(state):
    h = state["samba"]["homes"]
    p = {
        "comment": "Home Directories",
        "browseable": "yes" if h["browseable"] else "no",
        "read only": "no",
        "valid users": "%S",
        "create mask": "0700",
        "directory mask": "0700",
    }
    if state["samba"]["macos"]:
        p["vfs objects"] = "catia fruit streams_xattr"
    return p


def render(state, warnings):
    shares = {}
    for name, share in state["shares"].items():
        if share["smb"]["enabled"]:
            shares[name] = render_share(name, share, state, warnings)
    if state["samba"]["homes"]["enabled"]:
        shares["homes"] = render_homes(state)
    return render_global(state), shares


def to_conf_text(global_params, shares):
    out = ["[global]"] + [f"\t{k} = {v}" for k, v in global_params.items()]
    for name, params in shares.items():
        out += ["", f"[{name}]"] + [f"\t{k} = {v}" for k, v in params.items()]
    return "\n".join(out) + "\n"


def validate(global_params, shares):
    """Run testparm on the rendered config before it touches the registry."""
    if not have("testparm"):
        raise HelperError("testparm not found - is samba-common-bin installed?")
    fd, tmp = tempfile.mkstemp(prefix="sharehub-smb-", suffix=".conf")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(to_conf_text(global_params, shares))
        proc = run(["testparm", "-s", "--suppress-prompt", tmp], check=False)
    finally:
        os.unlink(tmp)
    problems = [line for line in proc.stderr.splitlines()
                if re.search(r"unknown parameter|invalid|error|ignoring", line, re.I)
                and "rlimit_max" not in line]
    if proc.returncode != 0 or problems:
        raise HelperError("Samba rejected the configuration:\n" + "\n".join(problems or [proc.stderr.strip()]))


# --------------------------------------------------------------------------- apply

def _owned():
    raw = read_text(OWNED_FILE)
    return set(json.loads(raw)) if raw else set()


def _set_owned(names):
    atomic_write(OWNED_FILE, json.dumps(sorted(names)) + "\n", 0o600)


def _sync_section(section, desired, current, managed_keys=None):
    """setparm/delparm so that `section` in the registry matches `desired`."""
    changed = False
    for key, value in current.items():
        owned = managed_keys is None or key in managed_keys or key.startswith(MANAGED_GLOBAL_PREFIXES)
        if owned and key not in desired:
            run(["net", "conf", "delparm", "--", section, key], check=False)
            changed = True
    for key, value in desired.items():
        if current.get(key) != value:
            run(["net", "conf", "setparm", "--", section, key, value])
            changed = True
    return changed


def apply(state, warnings):
    if not have("net"):
        if state["shares"] and any(s["smb"]["enabled"] for s in state["shares"].values()):
            raise HelperError("Samba is not installed (apt install samba).")
        return
    if not registry_enabled():
        raise HelperError("The Samba registry is not enabled. Enable it from the Overview page first.")
    global_params, shares = render(state, warnings)
    validate(global_params, shares)

    current = registry()
    current_lower = {k.lower(): k for k in current}
    changed = _sync_section("global", global_params, current.get("global", {}), MANAGED_GLOBAL)

    owned = _owned()
    for name, params in shares.items():
        existing = current_lower.get(name.lower())
        if existing is None:
            run(["net", "conf", "addshare", name, params.get("path", "/tmp")]
                if name != "homes" else ["net", "conf", "setparm", "homes", "comment", params["comment"]])
            existing = name
            cur = parse_conf(run(["net", "conf", "showshare", name]).stdout).get(name, {})
        else:
            cur = current[existing]
        changed |= _sync_section(existing, params, cur)
    desired_lower = {n.lower() for n in shares}
    for name in owned:
        existing = current_lower.get(name.lower())
        if existing and name.lower() not in desired_lower:
            run(["net", "conf", "delshare", existing], check=False)
            changed = True
    _set_owned(set(shares))
    if changed:
        reload()


def unmanaged_shares(state):
    """Registry shares ShareHub does not own (e.g. created by another tool)."""
    owned = {n.lower() for n in _owned()} | {n.lower() for n in state["shares"]} | {"homes"}
    return sorted(n for n in registry() if n.lower() not in owned and n.lower() != "global")


# --------------------------------------------------------------------------- recycle cleanup

def recycle_cleanup(state):
    """Delete recycle-bin files older than each share's retention. Run daily by a timer."""
    removed = 0
    for name, share in state["shares"].items():
        smb = share["smb"]
        if not (smb["enabled"] and smb["recycle"] and smb["recycle_days"]):
            continue
        root = os.path.join(share["path"], ".recycle")
        if not os.path.isdir(root):
            continue
        proc = run(["find", root, "-xdev", "-type", "f", "-mtime", f"+{smb['recycle_days']}",
                    "-print", "-delete"], check=False)
        removed += len(proc.stdout.splitlines())
        run(["find", root, "-xdev", "-mindepth", "2", "-type", "d", "-empty", "-delete"], check=False)
    return removed
