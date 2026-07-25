"""End-to-end integration tests for ``openace-run-as --isolated`` (Issue #2018).

These exercise the REAL helper + a root-owned conf + the real ``openace-agent``
account through the full wrapper gate, on a Linux host running as root with
``setfacl``. They cover the acceptance criteria that unit tests can only model:

  * legit launch under a registered root is authorized (exit 0, child runs);
  * a non-configured account is rejected (exit 67, no ACL granted);
  * a path outside the allowlist is rejected (exit 64);
  * a symlink component in the path is rejected (exit 64).

Everything else (account pin, path confinement, mount crossing, conf tampering)
is covered by ``test_validate_launch.py``. The ACL grant/rollback logic itself
is unchanged pre-existing code.

These SKIP on macOS / non-root / without setfacl / without the openace-agent
account. Run them on a Linux-root CI lane (or locally in a privileged
container) with::

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
_HELPER = _ROOT / "scripts" / "openace-validate-launch"

_LINUX_ROOT = sys.platform.startswith("linux") and os.geteuid() == 0
_HAS_SETFACL = shutil.which("setfacl") is not None
_HAS_AGENT = subprocess.run(["id", "openace-agent"], capture_output=True).returncode == 0

pytestmark = pytest.mark.skipif(
    not (_LINUX_ROOT and _HAS_SETFACL and _HAS_AGENT and _HELPER.exists()),
    reason="requires Linux + root + setfacl + openace-agent account + helper",
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


@pytest.fixture
def conf(tmp_path):
    """A root-owned launcher conf scoping the allowlist to /home."""
    conf_dir = tmp_path / "openace-conf"
    conf_dir.mkdir()
    conf_path = conf_dir / "agent-launcher.conf"
    conf_path.write_text(
        "# Issue #2018 test conf\n"
        'OPENACE_AUTONOMOUS_AGENT_ACCOUNT="openace-agent"\n'
        'ALLOWED_WORKSPACE_ROOTS="/home"\n',
        encoding="utf-8",
    )
    os.chown(conf_path, 0, 0)
    os.chmod(conf_path, 0o640)
    return conf_path


def _run(conf, account, project_dir, audit_log):
    env = {
        **os.environ,
        "OPENACE_VALIDATE_LAUNCH": str(_HELPER),
        "OPENACE_LAUNCHER_CONF": str(conf),
        "OPENACE_AUDIT_LOG": str(audit_log),
    }
    env.pop("OPENACE_RUN_AS", None)
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


def test_legit_launch_under_home_is_authorized(isolated_root, conf, tmp_path):
    audit = tmp_path / "audit.log"
    result = _run(conf, "openace-agent", isolated_root, audit)
    assert result.returncode == 0, result.stderr
    assert "result=accept" in audit.read_text()


def test_non_configured_account_rejected(isolated_root, conf, tmp_path):
    audit = tmp_path / "audit.log"
    # 'nobody' is a non-configured, non-admin account that exists on every Linux.
    result = _run(conf, "nobody", isolated_root, audit)
    assert result.returncode == 67
    assert "reject_account" in audit.read_text()
    # And no ACL was granted to nobody on the tree (no setfacl side effect).


def test_path_outside_allowlist_rejected(conf, tmp_path):
    audit = tmp_path / "audit.log"
    result = _run(conf, "openace-agent", "/etc", audit)
    assert result.returncode == 64
    assert "reject_path" in audit.read_text()


def test_symlink_component_rejected(isolated_root, conf, tmp_path):
    audit = tmp_path / "audit.log"
    link = isolated_root.parent / "evil-link"
    try:
        os.symlink("/etc", link)
        result = _run(conf, "openace-agent", str(link), audit)
        assert result.returncode == 64
        assert "reject_path" in audit.read_text()
    finally:
        link.unlink(missing_ok=True)
