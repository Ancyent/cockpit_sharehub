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


@pytest.fixture(autouse=True)
def nfs_available(monkeypatch):
    """Tests must not depend on whether they run in a container."""
    from sharehub import health as health_mod
    from sharehub import util
    monkeypatch.setattr(util, "nfs_server_support", lambda: (True, None))
    monkeypatch.setattr(health_mod, "nfs_server_support", lambda: (True, None))
    monkeypatch.setattr(health_mod, "container_type", lambda: "")


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


# --------------------------------------------------------------------------- quotas

from sharehub import quota  # noqa: E402


@pytest.mark.parametrize("text,value", [("1M", 1024 ** 2), ("500G", 500 * 1024 ** 3), ("1.5T", int(1.5 * 1024 ** 4))])
def test_quota_sizes(text, value):
    assert quota.to_bytes(text) == value


@pytest.mark.parametrize("text", ["", "abc", "10X", "100K", "-5G"])
def test_quota_bad_sizes(text):
    with pytest.raises(HelperError):
        quota.to_bytes(text)


def test_share_quota_normalised(share_dir):
    share = make_share(share_dir, quota={"limit": " 500 g "})
    assert share["quota"]["limit"] == "500G"
    with pytest.raises(HelperError):
        make_share(share_dir, quota={"limit": "lots"})


def test_parse_xfs_report():
    text = "#0                  0          0          0     00 [--------]\n#20000           1024          0     102400     00 [--------]\n"
    assert quota.parse_xfs_report(text, 20000) == (1024 * 1024, 102400 * 1024)
    assert quota.parse_xfs_report(text, 20001) is None


def test_parse_repquota():
    text = ("*** Report for project quotas on device /dev/loop0\n"
            "Project         used    soft    hard  grace    used  soft  hard  grace\n"
            "----------------------------------------------------------------------\n"
            "#0        --      20       0       0              2     0     0\n"
            "#20000    +-   10240       0   10240  7days      3     0     0\n")
    assert quota.parse_repquota(text, 20000) == (10240 * 1024, 10240 * 1024)


def test_parse_btrfs_qgroup():
    text = ("Qgroupid    Referenced    Exclusive  Max referenced  Max exclusive   Path\n"
            "--------    ----------    ---------  --------------  -------------   ----\n"
            "0/5              16384        16384            none           none   <toplevel>\n"
            "0/256         52412416     52412416        52428800           none   projects\n")
    assert quota.parse_btrfs_qgroup(text, "256") == (52412416, 52428800)
    assert quota.parse_btrfs_qgroup(text, "5") == (16384, 0)


@pytest.mark.parametrize("method,expected", [
    ("zfs", [["zfs", "set", "refquota=1073741824", "tank/data"]]),
    ("xfs", [["xfs_quota", "-x", "-c", "project -s -p /srv/d 20000", "/srv"],
             ["xfs_quota", "-x", "-c", "limit -p bhard=1048576k 20000", "/srv"]]),
    ("ext4", [["chattr", "-R", "-p", "20000", "/srv/d"],
              ["find", "/srv/d", "-xdev", "-type", "d", "-exec", "chattr", "+P", "{}", "+"],
              ["setquota", "-P", "20000", "0", "1048576", "0", "0", "/srv"]]),
    ("btrfs", [["btrfs", "quota", "enable", "/srv"], ["btrfs", "qgroup", "limit", "1073741824", "/srv/d"]]),
])
def test_quota_commands(monkeypatch, method, expected):
    calls = []
    monkeypatch.setattr(quota, "run", lambda cmd, **kw: calls.append(cmd))
    info = {"path": "/srv/d", "mountpoint": "/srv", "method": method, "dataset": "tank/data"}
    quota._set(info, 1024 ** 3, 20000)
    assert calls == expected


# --------------------------------------------------------------------------- components dashboard

from sharehub import health  # noqa: E402


class _Proc:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def test_package_info_parsing(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[0] == "dpkg-query":
            return _Proc("samba|installed|2:4.19.5\nwsdd-server|not-installed|\n")
        return _Proc("samba:\n  Installed: 2:4.19.5\n  Candidate: 2:4.19.5\nwsdd-server:\n  Installed: (none)\n"
                     "  Candidate: 2:0.7.1-5\nsnapper:\n  Installed: (none)\n  Candidate: (none)\n")
    monkeypatch.setattr(health, "run", fake_run)
    monkeypatch.setattr(health, "have", lambda b: True)
    info = health.package_info(["samba", "wsdd-server", "snapper"])
    assert info["samba"] == {"installed": "2:4.19.5", "candidate": "2:4.19.5"}
    assert info["wsdd-server"] == {"installed": None, "candidate": "2:0.7.1-5"}
    assert info["snapper"] == {"installed": None, "candidate": None}


def test_report_flags_missing_package_in_use(monkeypatch, share_dir):
    st = statemod.empty_state()
    st["shares"]["Data"] = make_share(share_dir, nfs={"enabled": True, "clients": [{"host": "*"}]})
    installed = {"samba", "samba-common-bin", "avahi-daemon", "wsdd-server"}
    monkeypatch.setattr(health, "package_info", lambda names: {
        n: {"installed": "1.0" if n in installed else None, "candidate": "1.0"} for n in names})
    monkeypatch.setattr(health, "_service", lambda u: {"unit": u, "installed": None, "active": None, "enabled": None})
    monkeypatch.setattr(health, "_checks", lambda fid, state: [])
    feats = {f["id"]: f for f in health.report(st)["features"]}
    assert feats["nfs"]["status"] == "attention" and feats["nfs"]["installable"] == ["nfs-kernel-server"]
    assert feats["smb"]["status"] == "ok"
    assert feats["ad"]["status"] == "absent"
    assert feats["versions"]["installable"] == ["sanoid"]


def test_install_allow_list():
    with pytest.raises(HelperError, match="cannot be installed"):
        health.install({"packages": ["netcat-openbsd"]})
    with pytest.raises(HelperError):
        health.install({"packages": []})


# --------------------------------------------------------------------------- containers and unavailable packages

def test_nfs_refused_in_container(monkeypatch, share_dir):
    from sharehub import util
    monkeypatch.setattr(util, "nfs_server_support", lambda: (False, "This system runs in a container (lxc)."))
    with pytest.raises(HelperError, match="container"):
        make_share(share_dir, nfs={"enabled": True, "clients": [{"host": "*"}]})


def test_container_detection(monkeypatch):
    monkeypatch.undo()   # use the real detection code, not the autouse stub
    from sharehub import util
    monkeypatch.setattr(util, "have", lambda b: False)
    monkeypatch.setattr(util, "read_text", lambda p: "lxc\n" if p == "/run/systemd/container" else None)
    monkeypatch.setattr(util.os.path, "ismount", lambda p: False)
    assert util.container_type() == "lxc"
    ok, reason = util.nfs_server_support()
    assert not ok and "lxc" in reason


def test_report_unavailable_and_unsupported(monkeypatch):
    st = statemod.empty_state()   # WS-Discovery is on by default but no share uses SMB yet
    monkeypatch.setattr(health, "package_info", lambda names: {
        n: {"installed": "1.0" if n in ("samba", "samba-common-bin", "nfs-kernel-server") else None,
            "candidate": None if n in ("wsdd-server", "wsdd2") else "1.0"} for n in names})
    monkeypatch.setattr(health, "_service", lambda u: {"unit": u, "installed": True, "active": "inactive", "enabled": None})
    monkeypatch.setattr(health, "_checks", lambda fid, state: [])
    monkeypatch.setattr(health, "nfs_server_support", lambda: (False, "container"))
    feats = {f["id"]: f for f in health.report(st)["features"]}
    assert feats["wsdd"]["status"] == "unavailable" and feats["wsdd"]["installable"] == []
    assert "wsdd2" in feats["wsdd"]["reason"]
    assert feats["nfs"]["status"] == "unsupported" and feats["nfs"]["installable"] == []


def test_verdicts_and_text_report(monkeypatch):
    st = statemod.empty_state()
    monkeypatch.setattr(health, "package_info", lambda names: {
        n: {"installed": "1.0" if n in ("samba", "samba-common-bin") else None,
            "candidate": None if n in ("wsdd-server", "wsdd2") else "1.0"} for n in names})
    monkeypatch.setattr(health, "_service", lambda u: {"unit": u, "installed": True, "active": "active", "enabled": "enabled"})
    monkeypatch.setattr(health, "_checks", lambda fid, state: [])
    monkeypatch.setattr(health, "nfs_server_support", lambda: (False, "This system runs in a container (lxc)."))
    monkeypatch.setattr(health, "system_info", lambda: {"os": "Debian GNU/Linux 13 (trixie)", "container": "lxc",
                                                        "virtualization": "lxc", "samba": "4.22", "kernel": "6.8", "os_id": "debian", "os_version": "13"})
    feats = {f["id"]: f for f in health.report(st)["features"]}
    assert feats["smb"]["availability"]["verdict"] == "yes"
    assert feats["nfs"]["availability"]["verdict"] == "no"
    assert feats["wsdd"]["availability"]["verdict"] == "no"
    assert feats["ftp"]["availability"]["verdict"] == "install"
    text = health.text_report(st)
    assert "Debian GNU/Linux 13 (trixie) (lxc container)" in text
    assert "[ -- ] NFS server" in text and "apt install proftpd-core" in text


# --------------------------------------------------------------------------- versioning

import datetime as _dt  # noqa: E402

from sharehub import snapshots  # noqa: E402


def _t(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def test_retention_hourly_daily():
    now = _t(2026, 11, 17, 12, 0)
    times = [now - _dt.timedelta(minutes=15 * i) for i in range(0, 4 * 24 * 5)]   # 5 days every 15 min
    keep = snapshots.keep_set(times, {"keep_frequent": 4, "keep_hourly": 6, "keep_daily": 3,
                                      "keep_weekly": 0, "keep_monthly": 0}, now)
    assert now in keep                                   # newest always kept
    assert len([t for t in keep if t > now - _dt.timedelta(hours=1)]) == 4   # 4 most recent quarter-hours
    hours = {(t.day, t.hour) for t in keep}
    assert len(hours) >= 6
    days = {t.day for t in keep}
    assert days == {17, 16, 15}                          # 3 daily versions
    assert len(keep) < len(times)


def test_retention_keeps_newest_with_zero_policy():
    now = _t(2026, 1, 1, 0, 0)
    assert snapshots.keep_set([now, now - _dt.timedelta(hours=1)], {}, now) == {now}


def test_snapshot_names_roundtrip():
    t = _t(2026, 11, 17, 21, 0, 5)
    name = snapshots.PREFIX + t.strftime(snapshots.TIME_FORMAT)
    assert name == "sharehub_2026.11.17-21.00.05"
    assert snapshots._parse(name) == t
    assert snapshots._parse("autosnap_2026") is None


@pytest.mark.parametrize("rel,expected", [("a/b.txt", "a/b.txt"), ("/a/../b", "b"), ("../../etc/passwd", "etc/passwd"), ("", "")])
def test_safe_rel(rel, expected):
    assert snapshots._safe_rel(rel) == expected


def test_recycle_trim_and_restore(tmp_path):
    share = tmp_path / "share"
    rec = share / ".recycle" / "maria" / "docs"
    rec.mkdir(parents=True)
    for i, name in enumerate(["r.txt", "Copy #1 of r.txt", "Copy #2 of r.txt", "Copy #3 of r.txt"]):
        f = rec / name
        f.write_text(str(i))
        os.utime(f, (1000 + i, 1000 + i))
    assert snapshots.trim_recycle_versions(str(share), 2) == 2
    left = sorted(p.name for p in rec.iterdir())
    assert left == ["Copy #2 of r.txt", "Copy #3 of r.txt"]
    restored = snapshots.restore_from_recycle(str(share), "maria/docs/Copy #3 of r.txt")
    assert restored == "docs/r.txt" and (share / "docs" / "r.txt").read_text() == "3"
    restored2 = snapshots.restore_from_recycle(str(share), "maria/docs/Copy #2 of r.txt")
    assert restored2.startswith("docs/r (restored ") and restored2.endswith(").txt")


def test_samba_params_for_versioning(monkeypatch, share_dir):
    monkeypatch.setattr(snapshots, "detect", lambda p: {"method": "btrfs", "supported": True})
    st = statemod.empty_state()
    share = make_share(share_dir, smb={"enabled": True, "recycle": True, "recycle_protect": True})
    share["versioning"]["snapshots"] = True
    p = samba.render_share("Data", share, st, [])
    assert p["shadow:snapdir"] == ".sharehub-snapshots"
    assert p["shadow:format"] == "sharehub_%Y.%m.%d-%H.%M.%S"
    assert p["veto files"] == "/.sharehub-snapshots/.recycle/"
    assert "shadow_copy2" in p["vfs objects"]


def test_existing_users_with_dots_or_upper_case_are_managed(monkeypatch):
    from sharehub import users
    import pwd as _pwd
    entry = _pwd.struct_passwd(("Andrei.P", "x", 1001, 1001, "", "/home/Andrei.P", "/bin/bash"))
    monkeypatch.setattr(users.pwd, "getpwnam", lambda n: entry if n == "Andrei.P" else (_ for _ in ()).throw(KeyError(n)))
    calls = []
    monkeypatch.setattr(users, "run", lambda cmd, **kw: calls.append((cmd, kw.get("inp"))))
    monkeypatch.setattr(users, "have", lambda _: True)
    assert users.set_password({"name": "Andrei.P", "password": "longenough", "smb": True}) == {"name": "Andrei.P"}
    assert calls[0] == (["chpasswd"], "Andrei.P:longenough\n")
    for bad in ("-rf", "a:b", "a b", "x,y", ""):
        with pytest.raises(users.HelperError):
            users.regular_user(bad)
    # new users keep the strict rule
    with pytest.raises(users.HelperError):
        users.create_user({"name": "Andrei.P", "password": "longenough"})
