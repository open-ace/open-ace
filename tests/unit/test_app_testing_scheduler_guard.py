"""TESTING 模式必须禁启后台服务（class-2 幽灵暂停事故，2026-08-15）。

背景：server 以 SCHEDULER_MODE=scheduler 启动时，agent 子进程继承该变量；
agent 在 worktree 内跑 pytest 时，fixture 的 create_app({"TESTING": True})
会在测试进程内启动真实 AutonomousScheduler，对真实 DB 执行孤儿清理/推进，
把 server 正在驱动的工作流 kill+paused。守卫：TESTING 下硬禁。
"""

from unittest.mock import patch

import pytest

from app import create_app

pytestmark = [pytest.mark.regression, pytest.mark.issue(2680)]


def test_testing_mode_skips_background_services():
    with (
        patch.dict("os.environ", {"SCHEDULER_MODE": "scheduler"}),
        patch("app.start_background_services") as mock_sbs,
    ):
        create_app({"TESTING": True})

    mock_sbs.assert_not_called()


def test_scheduler_mode_still_starts_services_when_not_testing():
    with (
        patch.dict("os.environ", {"SCHEDULER_MODE": "scheduler"}),
        patch("app.start_background_services") as mock_sbs,
    ):
        create_app({"TESTING": False})

    mock_sbs.assert_called_once()
