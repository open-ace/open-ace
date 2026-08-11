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


def test_quarantine_probe_runs_weekly_on_saturday_at_0318_singapore_time():
    # The weekly subprocess probe of ci/legacy-issue-quarantine.json nodeids must
    # match the agreed cadence (Sat 03:18 SGT) so a recovered/behavior-changed
    # quarantine entry is caught and cannot drift silently.
    assert _schedules("quarantine-probe.yml") == [
        {"cron": "18 3 * * 6", "timezone": "Asia/Singapore"}
    ]


def test_ci_health_metrics_runs_daily_at_0618_singapore_time():
    assert _schedules("ci-health-metrics.yml") == [
        {"cron": "18 6 * * *", "timezone": "Asia/Singapore"}
    ]


def test_ci_health_metrics_is_read_only_and_never_runs_on_pull_requests():
    workflow = PROJECT_ROOT / ".github" / "workflows" / "ci-health-metrics.yml"
    data = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert set(data["on"]) == {"workflow_dispatch", "schedule"}
    assert data["permissions"] == {"actions": "read", "contents": "read"}
    upload = next(
        step
        for step in data["jobs"]["collect"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == "90"

    ci_workflow = yaml.load(
        (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert "ci-health-metrics" not in str(ci_workflow["jobs"]["pr-gate"].get("needs", []))


def test_nightly_suite_metrics_have_independent_90_day_artifact():
    workflow = yaml.load(
        (PROJECT_ROOT / ".github" / "workflows" / "extended-tests.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    job = workflow["jobs"]["nightly-python"]
    run_step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run full deterministic and collection gates"
    )
    assert run_step["env"]["OPENACE_CI_METRICS_FILE"].endswith(".jsonl")
    upload = next(
        step for step in job["steps"] if step.get("name") == "Upload nightly CI suite metrics"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == "90"
