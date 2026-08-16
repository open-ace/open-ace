"""Scheduler auto-resume of usage-window (GLM 5h) upstream quota pauses (#2709)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.autonomous_scheduler import AutonomousScheduler


UTC8 = timezone(timedelta(hours=8))

PAUSED_WINDOW = {
    "workflow_id": "wf-window-1",
    "status": "paused",
    "current_phase": "pr_review",
    # paused_at 是 naive UTC（_pause_for_upstream_quota 以 now(utc) 写入）；
    # reset ts 已过 + 暂停已久 → 应恢复。
    "paused_at": "2026-08-15 12:00:00",
    "error_message": (
        "Upstream provider quota exhausted: provider usage window exhausted; "
        "limit resets at 2026-08-15 20:59:28 +0800; auto-resume scheduled"
    ),
}


def _scheduler() -> AutonomousScheduler:
    return AutonomousScheduler.__new__(AutonomousScheduler)


def _repo(*workflows) -> MagicMock:
    repo = MagicMock()
    repo.get_paused_workflows.return_value = list(workflows)
    return repo


def test_resumes_after_stated_reset_time():
    repo = _repo(PAUSED_WINDOW)

    with patch("app.routes.autonomous._emit_event_safe") as emit:
        _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_called_once()
    args = repo.update_workflow.call_args.args
    assert args[0] == "wf-window-1"
    assert args[1] == {
        "status": "pr_review",
        "paused_at": None,
        "error_message": "",
    }
    emit.assert_called_once_with("wf-window-1", "status_change", {"status": "pr_review"})


def test_does_not_resume_before_reset_time():
    future_local = (datetime.now(timezone.utc) + timedelta(hours=2)).astimezone(UTC8)
    wf = dict(
        PAUSED_WINDOW,
        error_message=PAUSED_WINDOW["error_message"].replace(
            "2026-08-15 20:59:28", future_local.strftime("%Y-%m-%d %H:%M:%S")
        ),
    )
    repo = _repo(wf)

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()


def test_not_resumed_within_min_pause_age():
    # 5 分钟下限：刚暂停就"到期"说明解析与实际不符（时区假设错误等），
    # 等待而不是以调度周期频率热循环 resume→429→pause。
    fresh = dict(
        PAUSED_WINDOW,
        paused_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    repo = _repo(fresh)

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()


def test_hard_quota_pause_without_marker_is_left_for_operator():
    repo = _repo(
        {
            "workflow_id": "wf-hard-1",
            "status": "paused",
            "current_phase": "development",
            "paused_at": "2026-08-15 12:00:00",
            "error_message": (
                "Upstream provider quota exhausted: the configured model provider "
                "rejected requests; resume after provider allocation is restored"
            ),
        }
    )

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()


def test_unparseable_timestamp_is_left_for_operator():
    repo = _repo(
        {
            "workflow_id": "wf-bad-1",
            "status": "paused",
            "current_phase": "development",
            "paused_at": "2026-08-15 12:00:00",
            "error_message": (
                "Upstream provider quota exhausted: provider usage window exhausted; "
                "limit resets at not-a-time +0800; auto-resume scheduled"
            ),
        }
    )

    _scheduler()._auto_resume_upstream_window_paused(repo)

    repo.update_workflow.assert_not_called()
