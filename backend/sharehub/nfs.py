# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""NFS exports (/etc/exports.d) and server settings (/etc/nfs.conf.d)."""
import os

from .util import HelperError, atomic_write, have, read_text, remove_file, run, systemctl

EXPORTS_FILE = "/etc/exports.d/sharehub.exports"
NFS_CONF_FILE = "/etc/nfs.conf.d/sharehub.conf"
HEADER = "# Managed by ShareHub - do not edit by hand, changes are overwritten.\n"


def client_options(client, read_only):
    opts = ["rw" if client["rw"] and not read_only else "ro",
            "sync" if client["sync"] else "async",
            "no_subtree_check",
            client["squash"]]
    if client["anonuid"] != "":
        opts.append(f"anonuid={client['anonuid']}")
    if client["anongid"] != "":
        opts.append(f"anongid={client['anongid']}")
    return ",".join(opts)


def render_exports(state):
    lines = []
    for name in sorted(state["shares"]):
        share = state["shares"][name]
        nfs = share["nfs"]
        if not nfs["enabled"]:
            continue
        clients = " ".join(f"{c['host']}({client_options(c, share['access']['read_only'])})"
                           for c in nfs["clients"])
        path = f'"{share["path"]}"' if " " in share["path"] else share["path"]
        lines.append(f"# share: {name}\n{path} {clients}")
    return (HEADER + "\n".join(lines) + "\n") if lines else None


def render_conf(state):
    n = state["nfs"]
    text = (HEADER + "[nfsd]\n"
            f"threads={n['threads']}\n"
            f"vers3={'y' if n['v3'] else 'n'}\n"
            f"vers4={'y' if n['v4'] else 'n'}\n")
    if n["v3"]:
        # Fixed ports so NFSv3 works through a firewall
        text += ("\n[mountd]\nport=20048\n"
                 "\n[statd]\nport=32765\noutgoing-port=32766\n"
                 "\n[lockd]\nport=32768\nudp-port=32768\n")
    return text


def _exportfs(warnings):
    proc = run(["exportfs", "-ra"], check=False)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def apply(state, warnings):
    exports = render_exports(state)
    conf = render_conf(state)
    if exports and not have("exportfs"):
        raise HelperError("NFS server is not installed (apt install nfs-kernel-server).")
    if not have("exportfs"):
        return

    old_exports = read_text(EXPORTS_FILE)
    old_conf = read_text(NFS_CONF_FILE)
    if exports == old_exports and conf == old_conf:
        return

    if exports is None:
        remove_file(EXPORTS_FILE)
    else:
        os.makedirs(os.path.dirname(EXPORTS_FILE), exist_ok=True)
        atomic_write(EXPORTS_FILE, exports)
    ok, detail = _exportfs(warnings)
    if not ok and exports is not None:
        if old_exports is None:
            remove_file(EXPORTS_FILE)
        else:
            atomic_write(EXPORTS_FILE, old_exports)
        _exportfs(warnings)
        raise HelperError("exportfs rejected the NFS exports, changes rolled back:\n" + detail)
    if not ok:
        warnings.append("exportfs: " + detail)

    if conf != old_conf:
        os.makedirs(os.path.dirname(NFS_CONF_FILE), exist_ok=True)
        atomic_write(NFS_CONF_FILE, conf)
        if exports:
            systemctl("restart", "nfs-server", warnings=warnings)
    if exports:
        systemctl("enable", "--now", "nfs-server", warnings=warnings)


def connected_clients():
    """NFSv4 clients known to the kernel (from /proc/fs/nfsd/clients)."""
    base = "/proc/fs/nfsd/clients"
    out = []
    if not os.path.isdir(base):
        return out
    for entry in os.listdir(base):
        info = read_text(os.path.join(base, entry, "info")) or ""
        data = {}
        for line in info.splitlines():
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip().strip('"')
        out.append({"address": data.get("address", ""), "name": data.get("name", ""),
                    "minor_version": data.get("minor version", "")})
    return out
