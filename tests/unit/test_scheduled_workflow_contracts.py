"""Contracts for the repository's scheduled quality workflows."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _schedules(workflow_name):
    workflow = PROJECT_ROOT / ".github" / "workflows" / workflow_name
    data = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return data["on"]["schedule"]


def test_nightly_runs_at_0118_singapore_time():
    assert _schedules("extended-tests.yml") == [
        {"cron": "18 1 * * *", "timezone": "Asia/Singapore"}
    ]


def test_weekly_runs_on_saturday_at_0318_singapore_time():
    assert _schedules("weekly-quality.yml") == [
        {"cron": "18 3 * * 6", "timezone": "Asia/Singapore"}
    ]
