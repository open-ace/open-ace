"""End-to-end integration tests for ``openace-run-as --isolated`` (Issue #2018).

These exercise the REAL installed validator + conf + the real ``openace-agent``
account through the full wrapper gate, on a Linux host running as root with
``setfacl``. The wrapper hardcodes its paths when euid==0 (env overrides are
ignored), so this suite tests the actual provisioning installed by
``install.sh`` / the ``Dockerfile`` rather than a faked environment.

Acceptance coverage that unit tests can only model:

  * legit launch under a registered root is authorized (exit 0, child runs);
  * a non-configured account is rejected (exit 67, no ACL granted);
  * a path outside the allowlist is rejected (exit 64);
  * a symlink component in the path is rejected (exit 64).

Everything else (account pin shape, path confinement, mount crossing, conf
tampering) is covered by ``test_validate_launch.py``; the gate exit-code/audit
mapping by ``test_run_as_gate.py``. The ACL grant/rollback logic itself is
unchanged pre-existing code.

These SKIP unless the host is a provisioned Linux-root lane. Run with::

    sudo pytest tests/issues/2018/test_run_as_integration.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _ROOT / "scripts" / "openace-run-as.sh"
_INSTALLED_VALIDATOR = Path("/usr/local/libexec/openace-validate-launch")
_INSTALLED_CONF = Path("/etc/openace/agent-launcher.conf")
_AUDIT_LOG = Path("/var/log/openace/run-as-audit.log")


def _linux_root() -> bool:
    return sys.platform.startswith("linux") and os.geteuid() == 0


def _has_setfacl() -> bool:
    return shutil.which("setfacl") is not None


def _has_agent() -> bool:
    return subprocess.run(["id", "openace-agent"], capture_output=True).returncode == 0


def _conf_allows_home() -> bool:
    try:
        return "/home" in _INSTALLED_CONF.read_text(encoding="utf-8")
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (
        _linux_root()
        and _has_setfacl()
        and _has_agent()
        and _INSTALLED_VALIDATOR.exists()
        and _INSTALLED_CONF.exists()
        and _conf_allows_home()
    ),
    reason="requires Linux + root + setfacl + openace-agent + installed validator/conf (conf allows /home)",
)


@pytest.fixture
def isolated_root(tmp_path):
    """A root-owned project dir UNDER /home (inside the allowlist)."""
    base = Path("/home") / f"oa-2018-{os.getpid()}-{tmp_path.name}"
    repo = base / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    try:
        yield repo
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _run(account, project_dir):
    """Run the wrapper as root. It hardcodes validator/conf/audit paths (env
    overrides ignored when euid==0), so this hits the real install."""
    env = dict(os.environ)
    for key in (
        "OPENACE_RUN_AS",
        "OPENACE_VALIDATE_LAUNCH",
        "OPENACE_LAUNCHER_CONF",
        "OPENACE_AUDIT_LOG",
    ):
        env.pop(key, None)
    return subprocess.run(
        [
            "bash",
            str(_WRAPPER),
            "--isolated",
            account,
            str(project_dir),
            "/usr/bin/test",
            "-d",
            ".",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _audit_text():
    if not _AUDIT_LOG.exists():
        return ""
    return _AUDIT_LOG.read_text(encoding="utf-8", errors="replace")


def test_legit_launch_under_home_is_authorized(isolated_root):
    result = _run("openace-agent", isolated_root)
    assert result.returncode == 0, result.stderr
    text = _audit_text()
    assert f"path={isolated_root}" in text
    assert "result=accept" in text


def test_non_configured_account_rejected(isolated_root):
    # 'nobody' is a non-configured, valid-shape, non-admin account on every Linux.
    result = _run("nobody", isolated_root)
    assert result.returncode == 67
    text = _audit_text()
    assert f"path={isolated_root}" in text
    assert "reject_account" in text


def test_path_outside_allowlist_rejected():
    result = _run("openace-agent", "/etc")
    assert result.returncode == 64
    assert "path=/etc result=reject_path" in _audit_text()


def test_symlink_component_rejected(isolated_root):
    link = isolated_root.parent / "evil-link"
    try:
        os.symlink("/etc", link)
        result = _run("openace-agent", str(link))
        assert result.returncode == 64
        assert f"path={link} result=reject_path" in _audit_text()
    finally:
        link.unlink(missing_ok=True)
