# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors
"""Unit tests for the pure parts of the backend: validation and rendering.

Run with:  python3 -m pytest tests/
They do not touch the system (no Samba, NFS or ProFTPD needed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sharehub import ftp, migrate, nfs, samba  # noqa: E402
from sharehub import state as statemod  # noqa: E402
from sharehub.util import HelperError  # noqa: E402

FTP_ROOT = "/srv/ftp-shares"


@pytest.fixture
def share_dir(tmp_path):
    d = tmp_path / "data share"
    d.mkdir()
    return str(d)


def make_share(path, **overrides):
    data = {"path": path, "access": {"users": ["root"], "groups": ["root"], "read_only": False}}
    for key, value in overrides.items():
        data[key] = value
    return statemod.normalize_share(data, FTP_ROOT)


# --------------------------------------------------------------------------- validation

def test_share_defaults(share_dir):
    share = make_share(share_dir)
    assert share["smb"]["enabled"] is True
    assert share["nfs"]["enabled"] is False
    assert share["ftp"]["enabled"] is False
    assert share["access"]["users"] == ["root"]


@pytest.mark.parametrize("name", ["", "a b", "-x", "homes", "global", "x" * 65, "a/b"])
def test_bad_share_names(name):
    with pytest.raises(HelperError):
        statemod.validate_share_name(name)


@pytest.mark.parametrize("path", ["relative", "/", "/etc", "/nonexistent/dir", "/tmp/with\"quote"])
def test_bad_paths(path):
    with pytest.raises(HelperError):
        statemod.validate_path(path, FTP_ROOT)


def test_unknown_user_rejected(share_dir):
    with pytest.raises(HelperError, match="Unknown user"):
        statemod.normalize_share({"path": share_dir, "access": {"users": ["no-such-user-xyz"]}}, FTP_ROOT)


def test_no_protocol_rejected(share_dir):
    with pytest.raises(HelperError, match="at least one protocol"):
        make_share(share_dir, smb={"enabled": False})


@pytest.mark.parametrize("host,ok", [
    ("10.0.0.0/24", True), ("192.168.1.10", True), ("fd00::/64", True), ("*", True),
    ("*.lan", True), ("nas.example.com", True), ("@trusted", True),
    ("10.0.0.0/33", False), ("bad host", False), ("", False), ("a(b)", False),
])
def test_nfs_hosts(host, ok):
    if ok:
        assert statemod.validate_nfs_host(host) == host
    else:
        with pytest.raises(HelperError):
            statemod.validate_nfs_host(host)


def test_nfs_needs_clients(share_dir):
    with pytest.raises(HelperError, match="NFS client"):
        make_share(share_dir, nfs={"enabled": True, "clients": []})


def test_settings_validation():
    s = statemod.normalize_settings("samba", {"workgroup": "corp", "min_protocol": "SMB3_11"})
    assert s["workgroup"] == "CORP"
    with pytest.raises(HelperError):
        statemod.normalize_settings("samba", {"encryption": "maybe"})
    with pytest.raises(HelperError):
        statemod.normalize_settings("nfs", {"v3": False, "v4": False})
    with pytest.raises(HelperError):
        statemod.normalize_settings("ftp", {"passive_ports": "80-90"})


def test_state_roundtrip_drops_unknown_keys():
    st = statemod.from_dict({"samba": {"workgroup": "X", "bogus": 1}, "shares": {
        "a": {"path": "/x", "smb": {"recycle": True, "junk": 2}}}})
    assert "bogus" not in st["samba"]
    assert "junk" not in st["shares"]["a"]["smb"]
    assert st["shares"]["a"]["smb"]["recycle"] is True
    assert st["ftp"]["homes"] is False


# --------------------------------------------------------------------------- samba rendering

def test_samba_share_render(share_dir):
    st = statemod.empty_state()
    share = make_share(share_dir, smb={"enabled": True, "recycle": True, "windows_acls": True,
                                        "time_machine": True, "time_machine_max_size": "1T"})
    p = samba.render_share("Data", share, st, [])
    assert p["valid users"] == "root @root"
    assert p["vfs objects"] == "catia fruit streams_xattr acl_xattr recycle"
    assert p["fruit:time machine"] == "yes"
    assert p["fruit:time machine max size"] == "1T"
    assert p["recycle:repository"] == ".recycle/%U"


def test_samba_principals_with_spaces_are_quoted():
    assert samba.quote_principal("Domain Users", group=True) == '"@Domain Users"'
    assert samba.quote_principal("alice") == "alice"


def test_samba_global_macos_and_security():
    st = statemod.empty_state()
    st["samba"].update({"netbios": False, "encryption": "required", "macos": True})
    g = samba.render_global(st)
    assert g["disable netbios"] == "yes" and g["smb ports"] == "445"
    assert g["server smb encrypt"] == "required"
    assert g["fruit:metadata"] == "stream"
    assert "security" not in g


def test_samba_global_ad():
    st = statemod.empty_state()
    st["ad"].update({"enabled": True, "realm": "corp.example.com", "workgroup": "CORP"})
    g = samba.render_global(st)
    assert g["security"] == "ADS"
    assert g["realm"] == "CORP.EXAMPLE.COM"
    assert g["idmap config CORP : backend"] == "rid"
    assert g["workgroup"] == "CORP"


def test_samba_homes():
    st = statemod.empty_state()
    st["samba"]["homes"]["enabled"] = True
    _, shares = samba.render(st, [])
    assert shares["homes"]["valid users"] == "%S"


# --------------------------------------------------------------------------- nfs / ftp rendering

def test_nfs_exports(share_dir):
    st = statemod.empty_state()
    st["shares"]["Data"] = make_share(share_dir, nfs={"enabled": True, "clients": [
        {"host": "10.0.0.0/24", "rw": True, "squash": "all_squash", "sync": False, "anonuid": 1000, "anongid": 1000}]})
    text = nfs.render_exports(st)
    assert f'"{share_dir}" 10.0.0.0/24(rw,async,no_subtree_check,all_squash,anonuid=1000,anongid=1000)' in text


def test_nfs_read_only_share_forces_ro(share_dir):
    st = statemod.empty_state()
    share = make_share(share_dir, nfs={"enabled": True, "clients": [{"host": "*", "rw": True}]})
    share["access"]["read_only"] = True
    st["shares"]["Data"] = share
    assert "*(ro," in nfs.render_exports(st)


def test_nfs_conf_fixed_ports():
    st = statemod.empty_state()
    assert "port=20048" in nfs.render_conf(st)
    st["nfs"]["v3"] = False
    assert "mountd" not in nfs.render_conf(st)


def test_ftp_render(share_dir):
    st = statemod.empty_state()
    share = make_share(share_dir, ftp={"enabled": True, "read_only": True})
    st["ftp"]["homes"] = True
    st["ftp"]["tls_required"] = True
    text = ftp.render(st, {"Data": share}, {})
    assert "DefaultRoot /srv/ftp-shares" in text
    assert "AllowUser root" in text and "AllowGroup root" in text
    assert "<Limit WRITE SITE_CHMOD>" in text
    assert "TLSRequired on" in text
    assert "DefaultChdir /home/%u" in text


def test_ftp_render_nothing():
    assert ftp.render(statemod.empty_state(), {}, {}) is None


# --------------------------------------------------------------------------- migration parsing

def test_parse_exports():
    text = '"/srv/my data" 10.0.0.0/24(rw,sync,no_root_squash) host2(ro)\n# comment\n/srv/b *(rw,all_squash,anonuid=99)\n'
    parsed = migrate.parse_exports(text)
    assert parsed[0][0] == "/srv/my data"
    assert parsed[0][1][0]["squash"] == "no_root_squash" and parsed[0][1][0]["rw"]
    assert parsed[0][1][1]["rw"] is False
    assert parsed[1][1][0]["anonuid"] == "99"


def test_share_from_registry():
    share, dropped = migrate.share_from_registry({
        "path": "/srv/x", "writeable": "yes", "valid users": 'alice @staff "@CORP\\Domain Users"',
        "vfs objects": "fruit streams_xattr recycle", "hide dot files": "yes"})
    assert share["access"]["read_only"] is False
    assert share["access"]["users"] == ["alice"]
    assert share["access"]["groups"] == ["staff", "CORP\\Domain Users"]
    assert share["smb"]["recycle"] is True
    assert dropped == ["hide dot files"]
