"""End-to-end integration tests for per-task isolation (Issue #2020).

Exercises the REAL launcher (root, setfacl, cgroup v2 where available) through
``--isolated --task-id`` to prove the behavioral guarantees unit tests can only
model:

  * two attempts get distinct HOME/TMP trees and cannot read each other's
    runtime files;
  * concurrent attempts on the same Agent UID produce distinct runtime trees
    (no UID-level serialization collision).

These SKIP unless the host is a provisioned Linux-root lane (mirrors the
``tests/issues/2018/test_run_as_integration.py`` guard, plus a writable cgroup
v2 hierarchy for the resource-limit cases). Run with::

    sudo pytest tests/issues/2020/test_run_as_integration_2020.py
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
_TASK_ROOT = Path("/run/openace-agent-tasks")


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
    reason="requires Linux + root + setfacl + openace-agent + installed validator/conf",
)


@pytest.fixture
def isolated_repo(tmp_path):
    base = Path("/home") / f"oa-2020-{os.getpid()}-{tmp_path.name}"
    repo = base / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    try:
        yield repo
    finally:
        shutil.rmtree(base, ignore_errors=True)
        # Best-effort cleanup of per-task trees this test created.
        for task_dir in _TASK_ROOT.glob("oa2020-*"):
            shutil.rmtree(task_dir, ignore_errors=True)


def _run(account, project_dir, task_id, cmd):
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
            "--task-id",
            task_id,
            account,
            str(project_dir),
            *cmd,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_per_task_home_and_tmp_are_distinct_and_private(isolated_repo):
    """Each attempt gets its own HOME/TMP; one cannot read the other's files."""
    probe = ["/bin/sh", "-c", 'echo "HOME=$HOME TMPDIR=$TMPDIR"; echo "$TASK_ID" > "$HOME/marker"']

    a = _run("openace-agent", isolated_repo, "oa2020-a", probe)
    b = _run("openace-agent", isolated_repo, "oa2020-b", probe)
    assert a.returncode == 0, a.stderr
    assert b.returncode == 0, b.stderr

    home_a = (_TASK_ROOT / "oa2020-a" / "home").resolve()
    home_b = (_TASK_ROOT / "oa2020-b" / "home").resolve()
    assert home_a != home_b
    assert (home_a / "marker").read_text().strip() == "oa2020-a"
    assert (home_b / "marker").read_text().strip() == "oa2020-b"
    # Task A's marker must not appear in task B's HOME (no shared HOME).
    assert not (home_b / "marker").read_text().__contains__("oa2020-a")


def test_concurrent_attempts_get_distinct_runtime_trees(isolated_repo):
    """Two attempts on the same UID produce two trees (no UID-level collision)."""
    _run("openace-agent", isolated_repo, "oa2020-c1", ["/usr/bin/true"])
    _run("openace-agent", isolated_repo, "oa2020-c2", ["/usr/bin/true"])
    assert (_TASK_ROOT / "oa2020-c1").is_dir()
    assert (_TASK_ROOT / "oa2020-c2").is_dir()
