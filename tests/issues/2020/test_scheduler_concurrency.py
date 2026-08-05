"""Scheduler concurrency cap is conf-driven (Issue #2020 Phase A).

Acceptance #4: "单机可按配置运行 N 个 Agent". The scheduler's
``MAX_CONCURRENT_WORKFLOWS`` becomes configurable via the same
agent-launcher.conf the launcher reads, defaulting to 10 (#2295 raised it from 3;
the per-user cap is enforced separately via tenant max_sessions_per_user).
"""

from __future__ import annotations

from app.modules.workspace.autonomous import task_isolation
from app.services import autonomous_scheduler


def test_default_concurrency_is_ten(monkeypatch, tmp_path):
    monkeypatch.setattr(
        autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(tmp_path / "missing.conf")
    )
    assert autonomous_scheduler.get_max_concurrent_workflows() == 10


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


def test_constant_is_ten():
    # The module constant is 10 (#2295) and still importable.
    assert autonomous_scheduler.MAX_CONCURRENT_WORKFLOWS == 10


def test_get_max_concurrent_workflows_uses_task_isolation_policy(monkeypatch, tmp_path):
    # The scheduler delegates to the shared policy reader so the launcher and
    # the scheduler can never disagree on the cap.
    monkeypatch.setattr(
        autonomous_scheduler, "_AGENT_LAUNCHER_CONF", str(tmp_path / "missing.conf")
    )
    policy = task_isolation.read_agent_task_policy(str(tmp_path / "missing.conf"))
    assert autonomous_scheduler.get_max_concurrent_workflows() == policy.max_concurrent_workflows
