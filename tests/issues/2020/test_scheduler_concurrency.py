"""Scheduler concurrency cap is conf-driven (Issue #2020 Phase A).

Acceptance #4: "单机可按配置运行 N 个 Agent". The scheduler's
``MAX_CONCURRENT_WORKFLOWS`` becomes configurable via the same
agent-launcher.conf the launcher reads, defaulting to the historical 3.
"""

from __future__ import annotations

from app.modules.workspace.autonomous import task_isolation
from app.services import autonomous_scheduler


def test_default_concurrency_is_three(monkeypatch, tmp_path):
    monkeypatch.setattr(
        autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(tmp_path / "missing.conf")
    )
    assert autonomous_scheduler.get_max_concurrent_workflows() == 3


def test_concurrency_reads_from_conf(monkeypatch, tmp_path):
    conf = tmp_path / "agent-launcher.conf"
    conf.write_text(
        "agent_account=openace-agent\nagent_max_concurrent_workflows=5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(conf))
    assert autonomous_scheduler.get_max_concurrent_workflows() == 5


def test_concurrency_floors_at_one(monkeypatch, tmp_path):
    conf = tmp_path / "agent-launcher.conf"
    conf.write_text("agent_max_concurrent_workflows=0\n", encoding="utf-8")
    monkeypatch.setattr(autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(conf))
    assert autonomous_scheduler.get_max_concurrent_workflows() >= 1


def test_constant_unchanged_as_default():
    # Backward compat: the module constant is still 3 and still importable.
    assert autonomous_scheduler.MAX_CONCURRENT_WORKFLOWS == 3


def test_get_max_concurrent_workflows_uses_task_isolation_policy(monkeypatch, tmp_path):
    # The scheduler delegates to the shared policy reader so the launcher and
    # the scheduler can never disagree on the cap.
    monkeypatch.setattr(
        autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(tmp_path / "missing.conf")
    )
    policy = task_isolation.read_agent_task_policy(str(tmp_path / "missing.conf"))
    assert autonomous_scheduler.get_max_concurrent_workflows() == policy.max_concurrent_workflows
