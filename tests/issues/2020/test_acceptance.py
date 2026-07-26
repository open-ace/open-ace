"""Issue #2020 Phase A acceptance tests.

The six required test names from the issue. The macOS-runnable slice here
exercises the policy/wiring/classification layer with mocks; the OS-enforced
behavior (actual cgroup OOM, prlimit, concurrent real processes) is covered by
the Linux-root integration suite in ``test_run_as_integration_2020.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _REPO_ROOT / "scripts" / "openace-run-as.sh"
_REMOTE = _REPO_ROOT / "remote-agent"
if str(_REMOTE) not in sys.path:
    sys.path.insert(0, str(_REMOTE))


class _FakeAdapter:
    def get_env_vars(self, proxy_url, proxy_token):
        return {"ANTHROPIC_API_KEY": proxy_token, "ANTHROPIC_BASE_URL": proxy_url}

    def get_settings_path(self):
        return None


class _FakeProxy:
    def generate_proxy_token(self, **kwargs):
        return "proxy-token-xyz"


def _patch_env_deps(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GH_TOKEN"):
        monkeypatch.setenv(key, "raw")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("OPENACE_ALLOW_RAW_KEY_FALLBACK", raising=False)
    monkeypatch.setattr(
        "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
        lambda: _FakeProxy(),
    )

    def fake(*a, **k):
        if a and a[-1] == "web_port":
            return 5000
        return "http://test"

    monkeypatch.setattr("app.utils.config.get_config_value", fake)


def _build_env(monkeypatch, task_id):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _patch_env_deps(monkeypatch)
    monkeypatch.setenv("OPENACE_AGENT_TASK_ROOT", "/run/openace-agent-tasks")
    return AutonomousAgentRunner._build_agent_env(
        _FakeAdapter(), "claude-code", None, f"sess-{task_id}", "m", task_id=task_id
    )


# ── 1. distinct HOME/TMP/XDG per task ──────────────────────────────────────


def test_tasks_use_distinct_home_tmp_and_xdg_dirs(monkeypatch):
    a = _build_env(monkeypatch, "task-a")
    b = _build_env(monkeypatch, "task-b")
    for key in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert a[key] != b[key], f"{key} shared between tasks"
    assert "/run/openace-agent-tasks/task-a/" in a["HOME"]
    assert "/run/openace-agent-tasks/task-b/" in b["HOME"]


# ── 2. cleanup one task does not kill another ──────────────────────────────


def test_cleanup_one_task_does_not_kill_another(monkeypatch):
    # (a) The launcher has no UID-wide kill — cleanups are task-scoped.
    src = _WRAPPER.read_text(encoding="utf-8")
    assert "pkill -KILL -u" not in src
    # (b) Python stop_session targets ONLY the stopped session's process group;
    # a concurrent session with a different pgid is never signaled.
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession

    runner = AutonomousAgentRunner()
    alive = MagicMock(returncode=None, pid=22222)
    stopped = MagicMock(returncode=None, pid=11111)
    runner._local_sessions["s-alive"] = _LocalSession(session_id="s-alive", process=alive)
    runner._local_sessions["s-stop"] = _LocalSession(session_id="s-stop", process=stopped)

    killed_pgids: list[int] = []
    with (
        patch("os.getpgid", side_effect=lambda pid: pid),
        patch("os.killpg", side_effect=lambda pgid, sig: killed_pgids.append((pgid, sig))),
    ):
        runner.stop_session("s-stop")

    targeted = [pgid for pgid, _ in killed_pgids]
    assert 11111 in targeted  # the stopped session's group
    assert 22222 not in targeted  # the other task's group untouched


# ── 3. local concurrency matches scheduler limit ───────────────────────────


def test_local_concurrency_matches_scheduler_limit(monkeypatch, tmp_path):
    from app.services import autonomous_scheduler

    conf = tmp_path / "agent-launcher.conf"
    conf.write_text("agent_max_concurrent_workflows=4\n", encoding="utf-8")
    monkeypatch.setattr(autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(conf))
    assert autonomous_scheduler.get_max_concurrent_workflows() == 4


# ── 4. memory + pid limit return a structured error ────────────────────────


def test_memory_and_pid_limit_return_structured_error(tmp_path):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner as R
    from app.modules.workspace.autonomous.task_isolation import read_agent_task_policy

    conf = tmp_path / "agent-launcher.conf"
    conf.write_text(
        "agent_task_memory_max_bytes=2147483648\nagent_task_pids_max=512\n",
        encoding="utf-8",
    )
    policy = read_agent_task_policy(str(conf))
    assert policy.memory_max_bytes == 2147483648
    assert policy.pids_max == 512

    # A task killed by the kernel over its cgroup/rlimit (exit 137) surfaces a
    # structured, machine-readable error code — not an opaque non-zero exit.
    code, _msg = R._classify_isolated_exit_code(137, "")
    assert code == "task_resource_limit_exceeded"


# ── 5. launcher kill reconciles orphan task ────────────────────────────────


def test_launcher_kill_reconciles_orphan_task():
    from app.services import autonomous_scheduler

    repo = MagicMock()
    repo.get_workflows_with_active_pid.return_value = [
        {"workflow_id": "wf-orphan", "agent_pid": 99999, "status": "running"}
    ]
    killed: list[tuple[int, int]] = []
    with (
        patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
            return_value=repo,
        ),
        patch("app.repositories.database.Database", return_value=MagicMock()),
        patch("os.kill", return_value=None),
        patch("os.getpgid", side_effect=lambda pid: pid),
        patch("os.killpg", side_effect=lambda pgid, sig: killed.append((pgid, sig))),
        patch("app.services.autonomous_scheduler.time.sleep"),
    ):
        autonomous_scheduler._cleanup_orphan_processes()

    # The orphan's process group is terminated (SIGTERM then SIGKILL)…
    sigs = [sig for _, sig in killed]
    assert signal_SIGTERM() in sigs and signal_SIGKILL() in sigs
    # …and the workflow is reset to a safe paused state.
    updated = repo.update_workflow.call_args[0][1]
    assert updated["status"] == "paused"
    assert updated["agent_pid"] is None


def signal_SIGTERM() -> int:
    import signal

    return signal.SIGTERM


def signal_SIGKILL() -> int:
    import signal

    return signal.SIGKILL


# ── 6. provider rejects required policy when unsupported ───────────────────


def test_provider_rejects_required_policy_when_unsupported():
    # Phase A placeholder (full provider contract is #2022): when cgroup
    # enforcement is forced on but unavailable, the launcher fail-closes
    # (exit 66) and the runner classifies it as a structured policy error.
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner as R

    src = _WRAPPER.read_text(encoding="utf-8")
    assert "OPENACE_CGROUP_REQUIRED" in src  # launcher emits the sentinel
    code, _msg = R._classify_isolated_exit_code(
        66, "OPENACE_CGROUP_REQUIRED: cgroup enforcement forced but unavailable"
    )
    assert code == "task_resource_policy_unavailable"
