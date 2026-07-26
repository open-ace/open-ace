"""Structural safety assertions for the launcher (Issue #2020 Phase A).

These are content-level guards that run on every platform (no root/setfacl).
They lock the two hardest-won behavioral guarantees of the isolation rewrite
so a future edit cannot silently regress them:

  * the launcher no longer uses UID-wide ``pkill -KILL -u`` as a cleanup
    mechanism (Issue #2020 acceptance: "不再把 UID-wide pkill 作为正常生命周期边界");
  * the per-task lock/registry/kill paths are keyed off the attempt
    (``task_id``), and a task-scoped kill (cgroup or process group) exists.

The full behavioral proof lives in the Linux-root integration suite.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _ROOT / "scripts" / "openace-run-as.sh"


def _src() -> str:
    return _WRAPPER.read_text(encoding="utf-8")


def test_launcher_does_not_use_uid_wide_pkill():
    src = _src()
    assert "pkill -KILL -u" not in src, (
        "UID-wide pkill must be removed (Issue #2020): it reaps unrelated "
        "concurrent attempts on the same Agent UID."
    )
    # No pkill-by-user at all in cleanup; pkill itself may still appear in the
    # availability check, but never targeted at -u <uid> for cleanup.
    assert "pkill -KILL -u " not in src


def test_launcher_keys_registries_off_isolation_key():
    src = _src()
    # flock lock + ACL/signature registries must use the per-attempt key, not
    # the bare target_uid.
    assert "isolation_key" in src
    assert "openace-agent-${isolation_key}" in src or "${isolation_key}" in src
    # Legacy uid-keyed fallback still exists for callers without --task-id.
    assert "uid-${target_uid}" in src


def test_launcher_has_task_scoped_kill():
    src = _src()
    # cgroup v2 kill is the precise task-scoped mechanism; the recorded child
    # is also signaled directly as defense-in-depth. The child is NOT placed in
    # its own session (no setsid) so the orchestrator's os.killpg(<sudo_pid>)
    # still reaches the agent tree.
    assert "cgroup.kill" in src
    assert 'kill -KILL "${agent_child_pid}"' in src
    assert "setsid" not in src


def test_launcher_sets_per_task_home_tmp_xdg():
    src = _src()
    assert "task_home" in src and "task_tmp" in src
    assert "XDG_CACHE_HOME" in src
    assert "XDG_CONFIG_HOME" in src
    assert "XDG_DATA_HOME" in src


def test_launcher_creates_task_runtime_tree():
    src = _src()
    assert "/run/openace-agent-tasks" in src or "${OPENACE_AGENT_TASK_ROOT" in src
    assert "mkdir -p" in src
