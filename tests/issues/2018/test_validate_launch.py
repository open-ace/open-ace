"""Unit tests for the ``openace-validate-launch`` helper (Issue #2018).

The helper is the security gate invoked by ``openace-run-as --isolated`` before
any root ACL grant: it pins the target account to the configured credentialless
``openace-agent`` and confines the project path to a registered workspace root
(no symlink components, no ``..``, no mount crossing). It is pure stdlib so the
decision logic is fully testable on macOS/Linux without root.

The bash wrapper stays the source of truth for ACL arithmetic + privilege drop;
this helper only answers "is this launch authorized". See
``scripts/openace-validate-launch``.
"""

import importlib.machinery
import importlib.util
import stat
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_HELPER = _ROOT / "scripts" / "openace-validate-launch"


def _load_helper():
    """Import the dash-cased, extension-less helper file as a module.

    ``spec_from_file_location`` cannot infer a loader without a ``.py`` suffix,
    so we hand it a ``SourceFileLoader`` explicitly.
    """
    loader = importlib.machinery.SourceFileLoader("openace_validate_launch", str(_HELPER))
    spec = importlib.util.spec_from_loader("openace_validate_launch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeStat:
    """Minimal stat result for dependency-injected checks."""

    def __init__(self, *, uid=0, gid=0, mode=0o640, dev=1, kind="reg"):
        kind_bits = {
            "reg": stat.S_IFREG,
            "dir": stat.S_IFDIR,
            "link": stat.S_IFLNK,
        }[kind]
        self.st_mode = kind_bits | (mode & 0o777)
        self.st_uid = uid
        self.st_gid = gid
        self.st_dev = dev
        self.st_ino = 1
        self.st_size = 0


def _write_conf(path: Path, account: str = "openace-agent", roots=("/home", "/workspace")):
    path.write_text(
        "# root-owned launcher constraints\n"
        f'OPENACE_AUTONOMOUS_AGENT_ACCOUNT="{account}"\n'
        f"ALLOWED_WORKSPACE_ROOTS={' '.join(roots)}\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def mod():
    return _load_helper()


@pytest.fixture
def conf(tmp_path):
    """A launcher conf with valid content; ownership/mode is faked per-test."""
    p = tmp_path / "agent-launcher.conf"
    _write_conf(p)
    return p


# ── load_conf ─────────────────────────────────────────────────────────────


class TestLoadConf:
    def test_loads_account_and_roots(self, mod, conf):
        account, roots = mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o640))
        assert account == "openace-agent"
        assert roots == ["/home", "/workspace"]

    def test_ignores_comments_and_blanks(self, mod, tmp_path):
        conf = tmp_path / "c.conf"
        conf.write_text(
            '\n# comment\nOPENACE_AUTONOMOUS_AGENT_ACCOUNT="openace-agent"\n'
            "ALLOWED_WORKSPACE_ROOTS=/home\n\n",
            encoding="utf-8",
        )
        account, roots = mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o640))
        assert account == "openace-agent"
        assert roots == ["/home"]

    def test_rejects_symlink_conf(self, mod, conf):
        with pytest.raises(mod.ConfError, match="symlink"):
            mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o640, kind="link"))

    def test_rejects_non_root_owned(self, mod, conf):
        with pytest.raises(mod.ConfError, match="root"):
            mod.load_conf(conf, _stat=lambda _p: _FakeStat(uid=1000, mode=0o640))

    def test_rejects_group_writable(self, mod, conf):
        with pytest.raises(mod.ConfError, match="writable"):
            mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o660))

    def test_rejects_other_writable(self, mod, conf):
        with pytest.raises(mod.ConfError, match="writable"):
            mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o646))

    def test_accepts_read_only_group_other_bits(self, mod, conf):
        # 0644 = rw-r--r-- : no group/other WRITE → acceptable.
        account, roots = mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o644))
        assert account == "openace-agent"

    def test_rejects_missing_keys(self, mod, tmp_path):
        conf = tmp_path / "c.conf"
        conf.write_text("OPENACE_AUTONOMOUS_AGENT_ACCOUNT=openace-agent\n", encoding="utf-8")
        with pytest.raises(mod.ConfError, match="missing"):
            mod.load_conf(conf, _stat=lambda _p: _FakeStat(mode=0o640))


# ── confine_path ──────────────────────────────────────────────────────────


class TestConfinePath:
    def test_accepts_path_beneath_root(self, mod, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        ok, _ = mod.confine_path(str(repo), [str(tmp_path)])
        assert ok

    def test_accepts_matching_second_root(self, mod, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        ok, _ = mod.confine_path(str(repo), ["/nonexistent-root", str(tmp_path)])
        assert ok

    def test_rejects_path_equal_to_root(self, mod, tmp_path):
        # Granting ACLs on the root itself (/home) would be catastrophic; the
        # project dir must be STRICTLY beneath a registered root.
        ok, reason = mod.confine_path(str(tmp_path), [str(tmp_path)])
        assert not ok
        assert "root" in reason

    def test_rejects_relative_path(self, mod):
        ok, reason = mod.confine_path("relative/repo", ["/home"])
        assert not ok
        assert "relative" in reason or "absolute" in reason

    def test_rejects_dotdot_component(self, mod, tmp_path):
        target = str(tmp_path) + "/sub/../../etc"
        ok, reason = mod.confine_path(target, [str(tmp_path)])
        assert not ok
        assert "dotdot" in reason or ".." in reason

    def test_rejects_symlink_component_in_chain(self, mod, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        target = str(link / "repo")
        ok, reason = mod.confine_path(target, [str(tmp_path)])
        assert not ok
        assert "symlink" in reason

    def test_rejects_leaf_symlink(self, mod, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        ok, reason = mod.confine_path(str(link), [str(tmp_path)])
        assert not ok
        assert "symlink" in reason

    def test_rejects_path_outside_allowlist(self, mod):
        for outside in ("/etc", "/tmp/whatever", "/root", "/var/lib/postgres"):
            ok, _ = mod.confine_path(outside + "/x", ["/home", "/workspace"])
            assert not ok, outside

    def test_rejects_mount_crossing(self, mod):
        # The leaf path sits on a different device than the matched root
        # (bind-mount / mount escape). Inject st_dev via the lstat hook.
        def fake_lstat(p):
            return _FakeStat(dev=99 if p == "/home/alice/repo" else 1, kind="dir", mode=0o755)

        ok, reason = mod.confine_path("/home/alice/repo", ["/home"], _lstat=fake_lstat)
        assert not ok
        assert "mount" in reason

    def test_accepts_same_device(self, mod):
        def fake_lstat(_p):
            return _FakeStat(dev=7, kind="dir", mode=0o755)

        ok, _ = mod.confine_path("/home/alice/repo", ["/home"], _lstat=fake_lstat)
        assert ok


# ── validate (composition) ────────────────────────────────────────────────


class TestValidate:
    def _stat_ok(self, _p):
        return _FakeStat(mode=0o640)

    def test_accepts_valid_account_and_path(self, mod, conf, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_conf(conf, account="openace-agent", roots=(str(tmp_path),))
        ok, _ = mod.validate("openace-agent", str(repo), str(conf), _conf_stat=self._stat_ok)
        assert ok

    def test_rejects_wrong_account(self, mod, conf, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_conf(conf, account="openace-agent", roots=(str(tmp_path),))
        ok, reason = mod.validate("nobody", str(repo), str(conf), _conf_stat=self._stat_ok)
        assert not ok
        assert "account" in reason

    def test_rejects_valid_account_but_path_outside(self, mod, conf, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_conf(conf, account="openace-agent", roots=(str(tmp_path),))
        ok, reason = mod.validate(
            "openace-agent", "/etc/passwd", str(conf), _conf_stat=self._stat_ok
        )
        assert not ok
        assert "path" in reason or "allowlist" in reason

    def test_rejects_when_conf_not_root_owned(self, mod, conf, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _write_conf(conf, account="openace-agent", roots=(str(tmp_path),))
        ok, reason = mod.validate(
            "openace-agent",
            str(repo),
            str(conf),
            _conf_stat=lambda _p: _FakeStat(uid=1000, mode=0o640),
        )
        assert not ok
        assert "conf" in reason
