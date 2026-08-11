"""Contracts for Extended Tests workflow-dispatch lane selection."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "extended-tests.yml"
EXECUTION_JOBS = ("full-e2e", "issue-tests", "manual-extended")
CATEGORY_PATTERN = re.compile(r"inputs\.category\s*==\s*'([^']+)'")


def _workflow():
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _dispatch_categories(job):
    return set(CATEGORY_PATTERN.findall(job["if"]))


def _normalized_condition(job):
    return " ".join(job["if"].split())


def _upload_artifact_name(job):
    upload_steps = [
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(upload_steps) == 1
    return upload_steps[0]["with"]["name"]


def test_dispatch_categories_are_exhaustive_and_unambiguous():
    workflow = _workflow()
    options = set(workflow["on"]["workflow_dispatch"]["inputs"]["category"]["options"])
    jobs = workflow["jobs"]
    categories_by_job = {
        job_name: _dispatch_categories(jobs[job_name]) for job_name in EXECUTION_JOBS
    }

    selected_jobs = {
        category: {
            job_name for job_name, categories in categories_by_job.items() if category in categories
        }
        for category in options
    }
    expected = {
        "critical": {"manual-extended"},
        "regression": {"manual-extended"},
        "e2e": {"full-e2e"},
        "issues": {"issue-tests"},
        "all": {"full-e2e", "issue-tests"},
        "specific": {"manual-extended"},
    }

    assert set().union(*categories_by_job.values()) == options
    assert selected_jobs == expected
    assert all(len(selected_jobs[category]) == 1 for category in options - {"all"})
    assert "!=" not in jobs["manual-extended"]["if"]


def test_dispatch_lane_predicates_have_exact_boolean_semantics():
    jobs = _workflow()["jobs"]
    expected = {
        "full-e2e": (
            "github.event_name == 'schedule' || "
            "(github.event_name == 'workflow_dispatch' && "
            "(inputs.category == 'e2e' || inputs.category == 'all')) || "
            "(github.event_name == 'pull_request' && "
            "contains(github.event.pull_request.labels.*.name, 'run-full-e2e'))"
        ),
        "issue-tests": (
            "github.event_name == 'schedule' || "
            "(github.event_name == 'workflow_dispatch' && "
            "(inputs.category == 'issues' || inputs.category == 'all')) || "
            "(github.event_name == 'pull_request' && "
            "contains(github.event.pull_request.labels.*.name, 'run-issue-tests'))"
        ),
        "manual-extended": (
            "github.event_name == 'workflow_dispatch' && "
            "(inputs.category == 'critical' || inputs.category == 'regression' || "
            "inputs.category == 'specific')"
        ),
    }

    assert {
        job_name: _normalized_condition(jobs[job_name]) for job_name in EXECUTION_JOBS
    } == expected


def test_issue_dispatch_keeps_fail_closed_baseline_comparator():
    jobs = _workflow()["jobs"]
    comparator = jobs["legacy-issue-baseline"]
    condition = comparator["if"]

    assert comparator["needs"] == ["issue-tests"]
    assert _dispatch_categories(comparator) == {"issues", "all"}
    assert "always()" in condition
    assert "needs.issue-tests.result != 'skipped'" in condition

    nightly = jobs["nightly-summary"]
    assert "legacy-issue-baseline" in nightly["needs"]
    publish_step = next(
        step for step in nightly["steps"] if step["name"] == "Publish nightly result"
    )
    assert publish_step["env"]["LEGACY_RESULT"] == "${{ needs.legacy-issue-baseline.result }}"
    assert 'if [ "$PYTHON_RESULT" != success ]' in publish_step["run"]
    assert '[ "$LEGACY_RESULT" != success ]' in publish_step["run"]


@pytest.mark.parametrize("failed_lane", [None, "python", "e2e", "legacy"])
def test_nightly_gate_returns_nonzero_for_every_failed_lane(tmp_path, failed_lane):
    jobs = _workflow()["jobs"]
    publish_step = next(
        step
        for step in jobs["nightly-summary"]["steps"]
        if step["name"] == "Publish nightly result"
    )
    results = {"python": "success", "e2e": "success", "legacy": "success"}
    if failed_lane:
        results[failed_lane] = "failure"
    summary_path = tmp_path / "summary.md"
    env = {
        **os.environ,
        "PYTHON_RESULT": results["python"],
        "E2E_RESULT": results["e2e"],
        "LEGACY_RESULT": results["legacy"],
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }

    completed = subprocess.run(
        ["bash", "-c", publish_step["run"]],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == (1 if failed_lane else 0)
    assert "Nightly quality gate" in summary_path.read_text(encoding="utf-8")


def test_execution_lane_names_and_artifacts_are_unique():
    jobs = _workflow()["jobs"]
    expected = {
        "full-e2e": ("Full E2E", "full-e2e"),
        "issue-tests": ("Issue Tests (${{ matrix.group }}/4)", "issue-tests-${{ matrix.group }}"),
        "manual-extended": ("Manual Extended Tests", "manual-extended-tests"),
    }

    observed = {
        job_name: (jobs[job_name]["name"], _upload_artifact_name(jobs[job_name]))
        for job_name in EXECUTION_JOBS
    }
    assert observed == expected
    assert len({display_name for display_name, _ in observed.values()}) == len(observed)
    assert len({artifact_name for _, artifact_name in observed.values()}) == len(observed)
