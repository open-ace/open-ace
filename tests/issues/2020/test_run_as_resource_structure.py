"""Structural guards for launcher resource enforcement (Issue #2020 Phase A).

Locks that the launcher wires the configured limits into the task cgroup
(memory.max / pids.max / cpu.max) where cgroup v2 is available, falls back to
``prlimit`` on the child process tree where it is not, and fails closed when
cgroup enforcement is forced (``on``) but unavailable. Behavioral proof of
actual OOM/pid/CPU enforcement lives in the Linux-root integration suite.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _ROOT / "scripts" / "openace-run-as.sh"


def _src() -> str:
    return _WRAPPER.read_text(encoding="utf-8")


def test_launcher_writes_cgroup_limits():
    src = _src()
    assert "memory.max" in src
    assert "pids.max" in src
    assert "cpu.max" in src


def test_launcher_has_prlimit_fallback():
    # When cgroup v2 is unavailable (stock container), RLIMIT_AS/NPROC/CPU on
    # the child process tree is the portable floor.
    src = _src()
    assert "prlimit" in src
    assert "--as=" in src or "--rss=" in src or "ulimit -v" in src


def test_launcher_reads_resource_keys_from_conf():
    src = _src()
    assert "agent_task_memory_max_bytes" in src
    assert "agent_task_pids_max" in src
    assert "agent_task_cpu_max" in src


def test_launcher_fail_closed_when_cgroup_forced_but_unavailable():
    # cgroup_enabled=on means "require cgroup"; if it cannot be created the
    # agent must not launch unsandboxed.
    src = _src()
    assert "agent_task_cgroup_enabled" in src
    # a fail-closed exit exists in the cgroup-unavailable branch when forced on
    assert "OPENACE_CGROUP_REQUIRED" in src or 'cgroup_enabled" = "on"' in src


def test_launcher_delegates_cgroup_controllers():
    # cgroup v2 requires the parent to delegate controllers to children via
    # cgroup.subtree_control. Without this, task subgroups have no memory.max,
    # pids.max, or cpu.max files and all resource writes silently fail.
    src = _src()
    assert "cgroup.subtree_control" in src
    assert "+memory +pids +cpu" in src
