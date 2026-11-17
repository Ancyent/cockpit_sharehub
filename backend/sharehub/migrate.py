# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Import existing configuration: Samba registry shares (e.g. from 45Drives File Sharing),
NFS exports, and the earlier cockpit-smbftp prototype."""
import json
import os
import re
import shlex

from . import ftp as ftpmod
from . import samba
from .state import DEFAULT_NFS_CLIENT, DEFAULT_SHARE, SHARE_NAME_RE, _merge, find_share
from .util import (atomic_write, have, is_true, mountpoints, octal_unescape, read_text,
                   remove_file, run)

EXPORT_FILES = ["/etc/exports.d/cockpit-file-sharing.exports", "/etc/exports"]
SMBFTP_STATE = "/etc/cockpit-smbftp/state.json"
SMBFTP_PROFTPD = "/etc/proftpd/conf.d/cockpit-smbftp.conf"
SMBFTP_TAG = "x-cockpit-smbftp"

KNOWN_PARAMS = {
    "path", "comment", "read only", "writeable", "writable", "write ok", "guest ok", "public",
    "browseable", "browsable", "valid users", "vfs objects", "fruit:time machine",
    "fruit:time machine max size", "map acl inherit", "store dos attributes",
}
KNOWN_PREFIXES = ("recycle:", "shadow:")


def split_words(text):
    """Whitespace split honouring double quotes, without treating backslash as an escape
    (Samba names such as DOMAIN\\user must survive)."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


def parse_exports(text):
    """[(path, [client dict])] from exports(5) text."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            tokens = split_words(line)
        except ValueError:
            continue
        if not tokens:
            continue
        path = octal_unescape(tokens[0])
        clients = []
        for tok in tokens[1:]:
            m = re.match(r"^([^()]*)(?:\((.*)\))?$", tok)
            if not m:
                continue
            host = m.group(1) or "*"
            opts = (m.group(2) or "").split(",")
            c = dict(DEFAULT_NFS_CLIENT, host=host)
            c["rw"] = "rw" in opts
            c["sync"] = "async" not in opts
            c["squash"] = ("no_root_squash" if "no_root_squash" in opts else
                           "all_squash" if "all_squash" in opts else "root_squash")
            for o in opts:
                if o.startswith("anonuid="):
                    c["anonuid"] = o.split("=", 1)[1]
                if o.startswith("anongid="):
                    c["anongid"] = o.split("=", 1)[1]
            clients.append(c)
        out.append((os.path.normpath(path), clients))
    return out


def share_from_registry(params):
    share = _merge(DEFAULT_SHARE, {})
    share["path"] = params.get("path", "")
    share["comment"] = params.get("comment", "")
    ro = is_true(params.get("read only"), True)
    for key in ("writeable", "writable", "write ok"):
        if key in params:
            ro = not is_true(params[key])
    share["access"]["read_only"] = ro
    users, groups = [], []
    for token in split_words(params.get("valid users", "").replace(",", " ")):
        if token and token[0] in "@+&":
            groups.append(token.lstrip("@+&"))
        elif token:
            users.append(token)
    share["access"]["users"], share["access"]["groups"] = users, groups
    smb = share["smb"]
    smb["guest_ok"] = is_true(params.get("guest ok", params.get("public")), False)
    smb["browseable"] = is_true(params.get("browseable", params.get("browsable")), True)
    vfs = params.get("vfs objects", "").split()
    smb["recycle"] = "recycle" in vfs
    smb["shadow_copies"] = "shadow_copy2" in vfs or "snapper" in vfs
    smb["windows_acls"] = "acl_xattr" in vfs
    smb["time_machine"] = is_true(params.get("fruit:time machine"))
    smb["time_machine_max_size"] = params.get("fruit:time machine max size", "")
    dropped = sorted(k for k in params if k not in KNOWN_PARAMS and not k.startswith(KNOWN_PREFIXES))
    return share, dropped


def _safe_name(base, taken):
    name = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip("._-")[:60] or "share"
    candidate, i = name, 2
    while candidate.lower() in taken or not SHARE_NAME_RE.match(candidate):
        candidate = f"{name}_{i}"
        i += 1
    return candidate


def detect(state):
    found = {"shares": [], "nfs_only": [], "smbftp": False, "file_sharing_installed": False}
    reg = samba.registry() if samba.registry_enabled() else {}
    exports = []
    for path in EXPORT_FILES:
        exports += [(p, c, path) for p, c in parse_exports(read_text(path))]
    smbftp = json.loads(read_text(SMBFTP_STATE) or "{}")
    found["smbftp"] = bool(smbftp) or os.path.exists(SMBFTP_PROFTPD)
    for name, params in reg.items():
        if name.lower() in ("global", "homes") or find_share(state, name):
            continue
        share, dropped = share_from_registry(params)
        nfs_clients = [c for p, cl, _ in exports if p == os.path.normpath(share["path"]) for c in cl]
        found["shares"].append({
            "name": name, "path": share["path"], "dropped_params": dropped,
            "nfs_clients": len(nfs_clients),
            "ftp": name.lower() in (smbftp.get("ftp") or {}),
        })
    reg_paths = {os.path.normpath(p.get("path", "")) for n, p in reg.items() if n.lower() != "global"}
    state_paths = {os.path.normpath(s["path"]) for s in state["shares"].values()}
    for path, clients, source in exports:
        if path not in reg_paths and path not in state_paths:
            found["nfs_only"].append({"path": path, "clients": len(clients), "source": source})
    if have("dpkg-query"):
        proc = run(["dpkg-query", "-W", "-f=${Status}", "cockpit-file-sharing"], check=False)
        found["file_sharing_installed"] = "install ok installed" in proc.stdout
    return found


def run_import(req, state, warnings):
    """Import the selected items into state. The caller applies the new state."""
    selected = set(req.get("shares") or [])
    selected_nfs = set(req.get("nfs_paths") or [])
    reg = samba.registry()
    exports = []
    for path in EXPORT_FILES:
        exports += [(p, c, path) for p, c in parse_exports(read_text(path))]
    smbftp = json.loads(read_text(SMBFTP_STATE) or "{}")
    imported = []
    moved_exports = set()
    for name, params in reg.items():
        if name not in selected or find_share(state, name):
            continue
        share, dropped = share_from_registry(params)
        if not os.path.isdir(share["path"]):
            warnings.append(f"{name}: path {share['path']} does not exist - skipped.")
            continue
        clients = [c for p, cl, _ in exports if p == os.path.normpath(share["path"]) for c in cl]
        if clients:
            share["nfs"] = {"enabled": True, "clients": clients}
            moved_exports.add(os.path.normpath(share["path"]))
        ftp = (smbftp.get("ftp") or {}).get(name.lower())
        if ftp is not None:
            share["ftp"] = {"enabled": True, "read_only": bool(ftp.get("read_only"))}
        if dropped:
            warnings.append(f"{name}: parameters not managed by ShareHub will be removed: {', '.join(dropped)}")
        state["shares"][name] = share
        imported.append(name)
    taken = {n.lower() for n in state["shares"]}
    for path, clients, source in exports:
        if path not in selected_nfs or any(os.path.normpath(s["path"]) == path for s in state["shares"].values()):
            continue
        if not os.path.isdir(path):
            warnings.append(f"NFS export {path} does not exist - skipped.")
            continue
        name = _safe_name(os.path.basename(path) or "nfs", taken)
        taken.add(name.lower())
        share = _merge(DEFAULT_SHARE, {})
        share["path"] = path
        share["smb"]["enabled"] = False
        share["nfs"] = {"enabled": True, "clients": clients}
        state["shares"][name] = share
        imported.append(name)
        moved_exports.add(path)
    settings = smbftp.get("settings") or {}
    if settings:
        for key in ("tls_cert", "tls_key", "passive_ports"):
            if settings.get(key):
                state["ftp"][key] = settings[key]
        state["ftp"]["tls_required"] = bool(settings.get("ftp_tls_required"))
        state["ftp"]["homes"] = bool(settings.get("homes_ftp"))
        state["samba"]["homes"]["enabled"] = bool(settings.get("homes_smb"))
    return imported, sorted(moved_exports)


def cleanup_after_import(moved_exports, warnings):
    """Remove artifacts of the previous tools that would conflict with ShareHub."""
    removed = []
    # NFS exports now live in /etc/exports.d/sharehub.exports
    path = EXPORT_FILES[0]
    if os.path.exists(path):
        os.replace(path, path + ".sharehub-imported")
        removed.append(path)
    # Entries now served from sharehub.exports are commented out in /etc/exports
    text = read_text("/etc/exports")
    if text and moved_exports:
        lines = []
        for line in text.splitlines():
            body = line.split("#", 1)[0].strip()
            words = split_words(body) if body else []
            if words and os.path.normpath(octal_unescape(words[0])) in moved_exports:
                lines.append("# moved to ShareHub: " + line)
                removed.append(f"/etc/exports: {words[0]}")
            else:
                lines.append(line)
        atomic_write("/etc/exports", "\n".join(lines) + "\n")
    # cockpit-smbftp prototype: its ProFTPD include and bind mounts
    if remove_file(SMBFTP_PROFTPD):
        removed.append(SMBFTP_PROFTPD)
    text = read_text(ftpmod.FSTAB) or ""
    kept = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and SMBFTP_TAG in parts[3].split(","):
            target = octal_unescape(parts[1])
            if target in mountpoints() and run(["umount", target], check=False).returncode != 0:
                run(["umount", "-l", target], check=False)
            removed.append(f"fstab: {target}")
            continue
        kept.append(line)
    if len(kept) != len(text.splitlines()):
        atomic_write(ftpmod.FSTAB, "\n".join(kept) + "\n")
    if os.path.exists(SMBFTP_STATE):
        os.replace(SMBFTP_STATE, SMBFTP_STATE + ".sharehub-imported")
        removed.append(SMBFTP_STATE)
    if have("exportfs"):
        run(["exportfs", "-ra"], check=False)
    return removed
