# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Render the desired state into Samba, NFS, ProFTPD and discovery services.

Each module validates before it writes and rolls back its own files on failure. If any
module fails, the previous state is re-applied so the system is never left half-changed."""
from . import ftp, nfs, quota, samba, services
from . import state as statemod
from .util import HelperError

MODULES = (quota.apply, samba.apply, nfs.apply, ftp.apply, services.apply_discovery)


def apply(state, warnings):
    for module in MODULES:
        module(state, warnings)


def commit(new_state, label):
    """Apply new_state; on success persist it (with history), on failure restore the old one."""
    previous = statemod.load()
    warnings = []
    try:
        apply(new_state, warnings)
    except HelperError:
        try:
            apply(previous, [])
        except Exception:  # noqa: BLE001 - best effort, the original error matters more
            pass
        raise
    statemod.save(new_state, label)
    return warnings
