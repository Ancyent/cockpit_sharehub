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


# --------------------------------------------------------------------------- 0.14: features

def test_quota_detect_in_container_points_to_the_host(monkeypatch, tmp_path):
    from sharehub import quota
    monkeypatch.setattr(quota, "_mount", lambda p: {"target": "/share", "fstype": "ext4", "options": ["rw"], "source": "/dev/sda1"})
    monkeypatch.setattr(quota, "container_type", lambda: "lxc")
    monkeypatch.setattr("sharehub.util.container_type", lambda: "lxc")
    info = quota.detect(str(tmp_path))
    assert info["kind"] == "impossible" and not info["supported"]
    assert "pct set" in info["reason"] and "host" in info["reason"]


def test_quota_detect_ext4_asks_for_setup_before_packages(monkeypatch, tmp_path):
    from sharehub import quota
    monkeypatch.setattr(quota, "_mount", lambda p: {"target": "/", "fstype": "ext4", "options": ["rw"], "source": "/dev/sda1"})
    monkeypatch.setattr(quota, "container_type", lambda: "")
    monkeypatch.setattr(quota, "have", lambda tool: False)
    info = quota.detect(str(tmp_path))
    assert info["kind"] == "needs_setup" and "project" in info["reason"]


def test_quota_detect_zfs_in_container_without_dev_zfs(monkeypatch, tmp_path):
    from sharehub import quota
    monkeypatch.setattr(quota, "_mount", lambda p: {"target": "/share", "fstype": "zfs", "options": [], "source": "rpool/data/subvol-101-disk-1"})
    monkeypatch.setattr(quota, "container_type", lambda: "lxc")
    monkeypatch.setattr(quota, "zfs_usable", lambda: False)
    assert quota.detect(str(tmp_path))["kind"] == "impossible"


def test_snapshot_detect_kinds(monkeypatch, tmp_path):
    from sharehub import snapshots
    monkeypatch.setattr(snapshots, "container_type", lambda: "")
    monkeypatch.setattr(snapshots, "fs_type", lambda p: "ext4")
    assert snapshots.detect(str(tmp_path))["kind"] == "impossible"
    monkeypatch.setattr(snapshots, "fs_type", lambda p: "btrfs")
    assert snapshots.detect(str(tmp_path))["kind"] == "needs_setup"   # a folder, not a subvolume
    monkeypatch.setattr(snapshots, "fs_type", lambda p: "zfs")
    monkeypatch.setattr(snapshots, "have", lambda tool: False)
    info = snapshots.detect(str(tmp_path))
    assert info["kind"] == "needs_install" and info["install"] == "zfsutils-linux"


def test_fs_evaluation_groups_shares_and_container_rule(monkeypatch, share_dir):
    from sharehub import health
    st = statemod.empty_state()
    for n in ("a", "b"):
        st["shares"][n] = make_share(share_dir)
    fake = {"fstype": "ext4", "kind": "impossible", "reason": "nope", "install": None}
    monkeypatch.setattr(health.snapshots, "detect", lambda p: dict(fake))
    ev = health.fs_evaluation(st, "snapshots")
    assert len(ev["checks"]) == 1 and "2 shares: a, b" in ev["checks"][0]["label"]
    assert ev["kind"] == "needs_setup"          # a share on ZFS/Btrfs would still work
    monkeypatch.setattr(health, "container_type", lambda: "lxc")
    assert health.fs_evaluation(st, "snapshots")["kind"] == "impossible"


def test_feature_switches(share_dir):
    from sharehub import features
    st = statemod.empty_state()
    st["shares"]["web"] = make_share(share_dir, ftp={"enabled": True, "read_only": False})
    with pytest.raises(HelperError, match="still used by: web"):
        features.apply_choice(st, {"ftp": False}, [])
    features.apply_choice(st, {"nfs": False, "mdns": False}, [])
    assert st["features"]["nfs"] is False and st["samba"]["mdns"] is False and st["features"]["configured"]
    share = make_share(share_dir, nfs={"enabled": True, "clients": [{"host": "10.0.0.0/24"}]})
    with pytest.raises(HelperError, match="NFS server is turned off"):
        features.check_share(st, "x", share)
    with pytest.raises(HelperError):
        features.apply_choice(st, {"bogus": True}, [])


def test_ui_state_mapping():
    from sharehub.features import ui_state
    base = {"status": "ok", "installable": [], "packages": [{"satisfied": True}], "setup_needed": False}
    assert ui_state(base, False) == "off"
    assert ui_state(dict(base, status="unsupported"), True) == "impossible"
    assert ui_state(dict(base, installable=["quota"], packages=[{"satisfied": False}]), True) == "needs_install"
    assert ui_state(dict(base, setup_needed=True), True) == "needs_setup"
    assert ui_state(base, True) == "ready"


def test_existing_state_keeps_every_feature_on():
    st = statemod.from_dict({"shares": {}, "samba": {"workgroup": "X"}})
    assert all(st["features"][k] for k in ("nfs", "ftp", "ad", "recycle", "quota", "snapshots", "versions"))
    assert st["features"]["configured"] is False


# --------------------------------------------------------------------------- 0.14: versions at scale

def test_browse_pages_and_filters(tmp_path):
    from sharehub import snapshots
    (tmp_path / "zdir").mkdir()
    (tmp_path / ".sharehub-snapshots").mkdir()
    for i in range(25):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    page = snapshots.browse(str(tmp_path), "", offset=0, limit=10)
    assert page["total"] == 26 and page["entries"][0]["name"] == "zdir"   # folders first, snapshot dir hidden
    assert [e["name"] for e in snapshots.browse(str(tmp_path), "", offset=20, limit=10)["entries"]] == \
        [f"f{i:02d}.txt" for i in range(19, 25)]
    assert snapshots.browse(str(tmp_path), "", query="F1")["total"] == 10


def test_browse_stops_at_the_deadline(tmp_path):
    from sharehub import snapshots
    for i in range(5):
        (tmp_path / f"f{i}").write_text("x")
    ticks = iter(range(0, 1000, 6))
    page = snapshots.browse(str(tmp_path), "", clock=lambda: next(ticks))
    assert page["truncated"] is True and page["total"] < 5


def test_search_limit_and_marks_copies(tmp_path, monkeypatch):
    from sharehub import snapshots
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "Copy #2 of report.txt").write_text("x")
    (tmp_path / "a" / "report.txt").write_text("x")
    res = snapshots.search(str(tmp_path), "", "report", hide=())
    assert {r["path"] for r in res["results"]} == {"a/report.txt", "a/b/Copy #2 of report.txt"}
    copy = next(r for r in res["results"] if r.get("copy"))
    assert copy["copy"] == 2 and copy["original"] == "report.txt"
    monkeypatch.setattr(snapshots, "SEARCH_LIMIT", 1)
    assert snapshots.search(str(tmp_path), "", "report", hide=())["stopped"] == "limit"
    with pytest.raises(HelperError):
        snapshots.search(str(tmp_path), "", "r")


def test_file_history_keeps_only_distinct_versions(tmp_path, monkeypatch):
    import datetime
    from sharehub import snapshots
    share = tmp_path / "share"
    share.mkdir()
    (share / "doc.txt").write_text("v3!")
    os.utime(share / "doc.txt", (3000, 3000))
    roots = {}
    # newest first: same as the current file, then v2 twice (unchanged), then v1
    for i, (content, mtime) in enumerate([("v3!", 3000), ("v2", 2000), ("v2", 2000), ("v1", 1000)]):
        root = tmp_path / f"snap{i}"
        root.mkdir()
        (root / "doc.txt").write_text(content)
        os.utime(root / "doc.txt", (mtime, mtime))
        roots[f"s{i}"] = str(root)
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(snapshots, "list_snapshots", lambda p: [{"name": n, "time": t} for n in roots])
    monkeypatch.setattr(snapshots, "snapshot_root", lambda p, n: roots[n])
    versions = snapshots.file_history(str(share), "doc.txt")["versions"]
    assert [v["snapshot"] for v in versions] == [None, "s1", "s3"]


def test_copy_tree_keeps_metadata_and_links(tmp_path):
    from sharehub import jobs
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("hello")
    os.chmod(src / "sub" / "a.txt", 0o640)
    os.utime(src / "sub" / "a.txt", (1234567, 1234567))
    os.symlink("sub/a.txt", src / "link")

    class P:
        files = 0

        def tick(self, files=0, nbytes=0, force=False):
            P.files += files
    jobs.copy_tree(str(src), str(tmp_path / "dst"), P())
    dst = tmp_path / "dst"
    assert (dst / "sub" / "a.txt").read_text() == "hello"
    assert oct(os.stat(dst / "sub" / "a.txt").st_mode & 0o777) == "0o640"
    assert int(os.stat(dst / "sub" / "a.txt").st_mtime) == 1234567
    assert os.readlink(dst / "link") == "sub/a.txt" and P.files == 2


def test_restore_recycle_tree_never_overwrites(tmp_path):
    from sharehub import jobs
    share = tmp_path
    rec = share / ".recycle" / "maria" / "plans"
    rec.mkdir(parents=True)
    (rec / "budget.xlsx").write_text("old")
    (rec / "Copy #1 of notes.txt").write_text("n")
    (share / "plans").mkdir()
    (share / "plans" / "budget.xlsx").write_text("current")

    class P:
        def tick(self, files=0, nbytes=0, force=False):
            pass
    assert jobs.restore_recycle_tree(str(share), "maria/plans", P()) == 2
    names = sorted(os.listdir(share / "plans"))
    assert "notes.txt" in names and "budget.xlsx" in names and any(n.startswith("budget (restored") for n in names)
    assert (share / "plans" / "budget.xlsx").read_text() == "current"
    assert not (share / ".recycle" / "maria" / "plans").exists()


# --------------------------------------------------------------------------- 0.14.1: recycle folder

def test_recycle_root_is_open_to_every_user(tmp_path, monkeypatch):
    from sharehub import samba
    share = make_share(str(tmp_path))
    rec = tmp_path / ".recycle"
    rec.mkdir(mode=0o700)          # as vfs_recycle creates it for the first user
    os.chmod(rec, 0o2700)
    owners = []
    monkeypatch.setattr(samba.os, "lchown", lambda p, u, g: owners.append((p, u, g)))
    real_lstat = os.lstat
    monkeypatch.setattr(samba.os, "lstat", lambda p: real_lstat(p))
    warnings = []
    share["smb"]["recycle_protect"] = True
    samba.prepare_recycle(share, warnings)
    assert warnings == []
    assert oct(os.stat(rec).st_mode & 0o7777) == "0o1733"
    share["smb"]["recycle_protect"] = False
    samba.prepare_recycle(share, warnings)
    assert oct(os.stat(rec).st_mode & 0o7777) == "0o1777"
    if os.geteuid() != 0:
        assert owners and owners[0][1:] == (0, 0)


def test_recycle_root_problem_and_missing_folder(tmp_path):
    from sharehub import samba
    share = make_share(str(tmp_path))
    assert samba.recycle_root_problem(share) is None          # not created yet: fine
    rec = tmp_path / ".recycle"
    rec.mkdir(mode=0o700)
    problem = samba.recycle_root_problem(share)
    assert problem and "other users cannot create" in problem
    samba.prepare_recycle(share, [])
    if os.geteuid() == 0:
        assert samba.recycle_root_problem(share) is None
    samba.prepare_recycle(dict(share, path=str(tmp_path / "x")), w := [])
    assert w   # missing share folder reported, not raised


def test_recycle_exclude_dir_uses_pipes():
    from sharehub import samba
    assert "," not in samba.RECYCLE_EXCLUDE_DIR and samba.RECYCLE_EXCLUDE_DIR.split("|") == [".recycle", "tmp", "temp"]


# --------------------------------------------------------------------------- 0.15: Cockpit Files from backports

def test_backports_suite_and_detection(tmp_path):
    from sharehub import backports
    deb = backports.os_release('ID=debian\nVERSION_CODENAME=trixie\n')
    ubu = backports.os_release('ID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=noble\n')
    assert backports.suite(deb) == "trixie-backports" and backports.suite(ubu) == "noble-backports"
    assert backports.suite(backports.os_release("ID=fedora\nVERSION_CODENAME=\n")) is None
    assert backports.distro(ubu) == "ubuntu"
    lst = tmp_path / "a.list"
    lst.write_text("# deb http://deb.debian.org/debian trixie-backports main\ndeb http://deb.debian.org/debian trixie main\n")
    assert not backports.enabled("trixie-backports", [str(lst)])
    lst.write_text("deb http://deb.debian.org/debian trixie-backports main\n")
    assert backports.enabled("trixie-backports", [str(lst)])
    src = tmp_path / "ubuntu.sources"
    src.write_text("## <name>-backports - comment\nTypes: deb\nSuites: noble noble-updates noble-backports\n")
    assert backports.enabled("noble-backports", [str(src)])
    assert not backports.enabled("noble-backports-x", [str(src)])


def test_backports_preview_parses_apt(monkeypatch):
    from sharehub import backports
    out = ("Inst libnss-systemd (255.4 Ubuntu:24.04/noble-updates [amd64])\n"
           "Inst cockpit-bridge [314-1] (362-1~bpo24.04.1 Ubuntu:24.04/noble-backports [all])\n"
           "Conf cockpit-bridge (362-1~bpo24.04.1 Ubuntu:24.04/noble-backports [all])\n")
    monkeypatch.setattr(backports, "suite", lambda rel=None: "noble-backports")
    monkeypatch.setattr(backports, "run", lambda cmd, **kw: type("P", (), {"stdout": out, "stderr": "", "returncode": 0})())
    assert backports.preview(["cockpit-files"]) == [
        {"name": "libnss-systemd", "from": None, "to": "255.4"},
        {"name": "cockpit-bridge", "from": "314-1", "to": "362-1~bpo24.04.1"}]


def test_files_feature_is_opt_in_and_installable_from_backports(monkeypatch):
    from sharehub import health
    st = statemod.empty_state()
    assert st["features"]["files"] is False
    monkeypatch.setattr(health, "package_info", lambda names: {n: {"installed": "1", "candidate": "1"} for n in names}
                        | {"cockpit-files": {"installed": None, "candidate": None}})
    monkeypatch.setattr(health.backports, "suite", lambda rel=None: "trixie-backports")
    monkeypatch.setattr(health.backports, "enabled", lambda name=None, files=None: False)
    f = next(x for x in health.report(st, light=True)["features"] if x["id"] == "files")
    assert f["installable"] == ["cockpit-files"] and f["from_backports"] == "trixie-backports"
    assert f["ui"] == "off" and "enables it" in f["reason"]
    monkeypatch.setattr(health.backports, "enabled", lambda name=None, files=None: True)
    f = next(x for x in health.report(st, light=True)["features"] if x["id"] == "files")
    assert f["status"] == "unavailable"     # backports on, still no package: really not available
