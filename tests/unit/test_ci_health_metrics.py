"""Contracts for CI runner JSONL and bounded Actions health metrics."""

import importlib.util
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = _load("openace_ci_metrics_runner", ROOT / "scripts" / "ci.py")
metrics = _load(
    "openace_github_actions_metrics", ROOT / "scripts" / "ci" / "github_actions_metrics.py"
)


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _suite_config(*commands):
    return {
        "suites": {
            "first": {
                "description": "first",
                "timeout_seconds": 30,
                "commands": list(commands),
            },
            "second": {
                "description": "second",
                "timeout_seconds": 30,
                "commands": [[ci.sys.executable, "-c", "raise SystemExit(0)"]],
            },
        }
    }


def test_runner_metrics_record_three_level_success_without_argv(tmp_path):
    path = tmp_path / "metrics.jsonl"
    recorder = ci.MetricsRecorder(str(path))

    ci.execute_suites(
        ["first"],
        _suite_config([ci.sys.executable, "-c", "value='secret-token-would-be-here'"]),
        action="run",
        metrics=recorder,
    )

    records = _records(path)
    types = [record["record_type"] for record in records]
    assert types == [
        "invocation_start",
        "suite_plan",
        "suite_start",
        "command_start",
        "command_terminal",
        "suite_terminal",
        "invocation_terminal",
    ]
    assert records[-1]["outcome"] == "success"
    assert "secret-token-would-be-here" not in path.read_text(encoding="utf-8")


def test_runner_metrics_marks_remaining_suite_not_started(tmp_path):
    path = tmp_path / "metrics.jsonl"
    recorder = ci.MetricsRecorder(str(path))

    with pytest.raises(ci.CIError, match="exit code 7"):
        ci.execute_suites(
            ["first", "second"],
            _suite_config([ci.sys.executable, "-c", "raise SystemExit(7)"]),
            action="run",
            metrics=recorder,
        )

    terminal = _records(path)[-1]
    assert terminal["suite_outcomes"] == {
        "first": "failure",
        "second": "not_started_due_to_previous_failure",
    }


def test_unknown_suite_still_has_start_and_terminal_records(tmp_path):
    path = tmp_path / "metrics.jsonl"
    recorder = ci.MetricsRecorder(str(path))

    with pytest.raises(ci.CIError, match="Unknown suite"):
        ci.execute_suites(
            ["missing"],
            _suite_config([ci.sys.executable, "-c", "raise SystemExit(0)"]),
            action="run",
            metrics=recorder,
        )

    records = _records(path)
    suite_records = [record for record in records if record["record_type"].startswith("suite_")]
    assert [record["record_type"] for record in suite_records] == [
        "suite_plan",
        "suite_start",
        "suite_terminal",
    ]
    assert suite_records[-1]["outcome"] == "failure"


def test_metrics_write_error_does_not_hide_primary_failure(tmp_path):
    recorder = ci.MetricsRecorder(str(tmp_path / "metrics.jsonl"))
    recorder.error = OSError("disk full")

    with pytest.raises(ci.CIError) as exc_info:
        ci.execute_suites(
            ["first"],
            _suite_config([ci.sys.executable, "-c", "raise SystemExit(9)"]),
            action="run",
            metrics=recorder,
        )

    message = str(exc_info.value)
    assert message.index("exit code 9") < message.index("metrics_write_error")


def test_metrics_bad_initial_path_fails_closed(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ci.CIError, match="metrics_write_error"):
        ci.MetricsRecorder(str(directory))


def test_metrics_disabled_has_no_output(tmp_path):
    ci.execute_suites(
        ["first"],
        _suite_config([ci.sys.executable, "-c", "raise SystemExit(0)"]),
        action="run",
        metrics=ci.MetricsRecorder(None),
    )
    assert list(tmp_path.iterdir()) == []


def test_collection_suite_records_command_terminal(monkeypatch, tmp_path):
    path = tmp_path / "metrics.jsonl"
    recorder = ci.MetricsRecorder(str(path))
    monkeypatch.setattr(
        ci.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "12 tests collected\n", ""),
    )
    monkeypatch.setattr(
        ci,
        "load_json",
        lambda path: {"layers": {"default": {"min_tests": 1, "min_files": 1}}},
    )
    monkeypatch.setattr(ci, "candidate_test_file_count", lambda target: 2)

    ci.run_suite(
        "collection",
        {
            "suites": {
                "collection": {
                    "description": "collection",
                    "timeout_seconds": 10,
                    "collection_target": "tests",
                    "baseline_layer": "default",
                }
            }
        },
        metrics=recorder,
        invocation_id="invocation",
    )
    recorder.close()

    assert [record["record_type"] for record in _records(path)] == [
        "suite_start",
        "command_start",
        "command_terminal",
        "suite_terminal",
    ]


@pytest.mark.parametrize(
    ("raw", "expected", "clamped"),
    [(-2, 0, True), (-1, 0, True), (0, 0, False), (3, 3, False)],
)
def test_timestamp_skew_contract(raw, expected, clamped):
    start = datetime(2026, 8, 11, tzinfo=timezone.utc)
    end = start + timedelta(seconds=raw)
    measured = metrics.measured_delta(start.isoformat(), end.isoformat(), "test")
    assert measured["seconds"] == expected
    assert measured["timestamp_skew_clamped"] is clamped


def test_timestamp_skew_below_tolerance_is_invalid():
    with pytest.raises(metrics.MetricsError, match="timestamp order"):
        metrics.measured_delta("2026-08-11T00:00:03Z", "2026-08-11T00:00:00Z", "test")


def test_nearest_rank_and_minimum_sample_contract():
    values = list(range(1, 26))
    assert metrics.nearest_rank(values, 0.95) == 24
    assert metrics.summarize_values(values[:19], 20)["p95_status"] == "insufficient_data"
    assert metrics.summarize_values(values, 20)["p95_seconds"] == 24
    assert (
        metrics.summarize_values(values, 20, cohort_samples=1)["p95_status"] == "insufficient_data"
    )


def test_contract_hash_uses_absent_sentinel():
    run = {"id": 1, "head_sha": "head"}
    cohort = {
        "id": "push",
        "event": "push",
        "contract_paths": ["new.yml", "present.yml"],
    }
    digest, contract = metrics.contract_hash(
        run, cohort, {"head": {"present.yml": "blob"}}, version=1
    )
    assert len(digest) == 64
    assert contract["entries"] == [
        {"path": "new.yml", "blob": "ABSENT"},
        {"path": "present.yml", "blob": "blob"},
    ]


def test_pr_contract_groups_by_merge_blobs_and_selected_suites_not_raw_shas():
    cohort = {"id": "pr", "event": "pull_request", "contract_paths": ["ci/suites.json"]}
    first = {
        "id": 1,
        "_runtime_merge_sha": "merge-one",
        "_runtime_head_sha": "head-one",
        "_execution_base_sha": "base-one",
        "_selection_base_sha": "selection-one",
        "_selected_suites": ("legacy-pr",),
    }
    second = {
        **first,
        "id": 2,
        "_runtime_merge_sha": "merge-two",
        "_runtime_head_sha": "head-two",
        "_execution_base_sha": "base-two",
        "_selection_base_sha": "selection-two",
    }
    trees = {
        "merge-one": {"ci/suites.json": "same-blob"},
        "merge-two": {"ci/suites.json": "same-blob"},
    }

    first_digest, first_contract = metrics.contract_hash(first, cohort, trees, version=1)
    second_digest, second_contract = metrics.contract_hash(second, cohort, trees, version=1)

    assert first_digest == second_digest
    assert first_contract["checkout_sha"] != second_contract["checkout_sha"]


def test_pr_contract_selected_suite_set_changes_digest():
    cohort = {"id": "pr", "event": "pull_request", "contract_paths": []}
    run = {"id": 1, "_runtime_merge_sha": "merge", "_selected_suites": ("legacy-pr",)}
    first, _ = metrics.contract_hash(run, cohort, {"merge": {}}, version=1)
    run["_selected_suites"] = ("python-core",)
    second, _ = metrics.contract_hash(run, cohort, {"merge": {}}, version=1)
    assert first != second


def test_bounded_sample_reports_policy_truncation_without_requiring_next_page():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    runs = [
        {
            "id": index,
            "event": "pull_request",
            "status": "completed",
            "created_at": (now - timedelta(minutes=index)).isoformat(),
        }
        for index in range(1, 26)
    ]
    selected = metrics.validate_bounded_runs(
        {"id": "pr", "event": "pull_request"},
        {"total_count": 1645, "workflow_runs": runs},
        now - timedelta(days=30),
        25,
    )
    assert len(selected) == 25


def test_request_budget_keeps_repository_reserve():
    budget = metrics.RequestBudget(maximum=700, reserve=250)
    budget.note_rate_limit(1000)
    budget.preflight(700)
    with pytest.raises(metrics.MetricsError, match="estimate"):
        budget.preflight(701)
    budget.actual = 700
    with pytest.raises(metrics.MetricsError, match="exhausted"):
        budget.guard()


@pytest.mark.parametrize(
    "url",
    [
        "https://productionresultssa13.blob.core.windows.net/actions/log.txt?sig=redacted",
        "https://pipelines.actions.githubusercontent.com/log.txt?sig=redacted",
    ],
)
def test_job_log_redirect_allowlist(url):
    metrics.validate_log_redirect(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://productionresultssa13.blob.core.windows.net/log.txt",
        "https://blob.core.windows.net.evil.example/log.txt",
        "https://user@productionresultssa13.blob.core.windows.net/log.txt",
    ],
)
def test_job_log_redirect_rejects_unsafe_target(url):
    with pytest.raises(metrics.MetricsError, match="not allowlisted"):
        metrics.validate_log_redirect(url)


class AttemptClient:
    def __init__(self, responses):
        self.responses = responses

    def get(self, path, params=None):
        key = metrics.canonical_request(path, params)
        return self.responses[key], {}


class RuntimeClient(AttemptClient):
    def __init__(self, responses, logs):
        super().__init__(responses)
        self.logs = logs
        self.calls = []

    def get(self, path, params=None):
        key = metrics.canonical_request(path, params)
        self.calls.append(key)
        return self.responses[key], {}

    def get_text(self, path):
        self.calls.append(path)
        return self.logs[path], {}


def _attempt_meta(number, conclusion, created, started, updated):
    return {
        "run_attempt": number,
        "conclusion": conclusion,
        "created_at": created,
        "run_started_at": started,
        "updated_at": updated,
    }


def _job(job_id, conclusion, created, started, completed):
    return {
        "id": job_id,
        "name": "test (3.11)",
        "conclusion": conclusion,
        "labels": ["ubuntu-24.04"],
        "created_at": created,
        "started_at": started,
        "completed_at": completed,
    }


def _runtime_fixture(*, payload_link=True, bad_parents=False):
    repo = "open-ace/open-ace"
    run_id = 42
    head = "2" * 40
    runtime_base = "1" * 40
    current_base = "9" * 40
    merge = "3" * 40
    select_job = _job(
        77,
        "success",
        "2026-08-11T00:00:00Z",
        "2026-08-11T00:00:01Z",
        "2026-08-11T00:00:02Z",
    )
    select_job["name"] = "Select suites"
    responses = {
        f"/repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=100": {
            "total_count": 1,
            "jobs": [select_job],
        },
        f"/repos/{repo}/git/commits/{merge}": {
            "sha": merge,
            "parents": [
                {"sha": runtime_base},
                {"sha": "8" * 40 if bad_parents else head},
            ],
        },
        f"/repos/{repo}/actions/runs/{run_id}/attempts/1": _attempt_meta(
            1,
            "success",
            "2026-08-11T00:00:00Z",
            "2026-08-11T00:00:00Z",
            "2026-08-11T00:00:03Z",
        ),
    }
    pr = {
        "number": 2492,
        "head": {"sha": head},
        "base": {"sha": current_base},
    }
    logs = {
        f"/repos/{repo}/actions/jobs/77/logs": (
            f"git fetch origin +{merge}:refs/remotes/pull/2492/merge\n"
            f"BASE_SHA: {runtime_base}\n"
            "Selected suites: legacy-pr, python-core\n"
        )
    }
    run = {
        "id": run_id,
        "run_attempt": 1,
        "created_at": "2026-08-11T00:00:00Z",
        "head_sha": head,
        "pull_requests": [pr] if payload_link else [],
    }
    return repo, run, responses, logs, runtime_base, head


def test_pr_runtime_contract_uses_merge_parent_not_current_payload_base():
    repo, run, responses, logs, runtime_base, head = _runtime_fixture()
    client = RuntimeClient(responses, logs)
    cache = {}

    metrics.resolve_pr_runtime(client, repo, run, 100, cache)

    assert run["_execution_base_sha"] == runtime_base
    assert run["_runtime_head_sha"] == head
    assert run["_selected_suites"] == ("legacy-pr", "python-core")
    assert run["_payload_link_state"] == "base_mismatch"
    assert run["_pr_number_source"] == "runtime_merge_ref"
    attempts = metrics.collect_attempts(client, repo, run, 100, cache)
    assert len(attempts) == 1
    jobs_path = f"/repos/{repo}/actions/runs/{run['id']}/attempts/1/jobs?per_page=100"
    assert client.calls.count(jobs_path) == 1


def test_pr_runtime_accepts_empty_dynamic_payload():
    repo, run, responses, logs, runtime_base, head = _runtime_fixture(payload_link=False)
    client = RuntimeClient(responses, logs)

    metrics.resolve_pr_runtime(client, repo, run, 100, {})

    assert run["_execution_base_sha"] == runtime_base
    assert run["_runtime_head_sha"] == head
    assert run["_payload_link_state"] == "empty"


def test_pr_runtime_treats_payload_number_conflict_as_diagnostic_only():
    repo, run, responses, logs, runtime_base, head = _runtime_fixture()
    run["pull_requests"][0]["number"] = 9999

    metrics.resolve_pr_runtime(RuntimeClient(responses, logs), repo, run, 100, {})

    assert run["_execution_base_sha"] == runtime_base
    assert run["_runtime_head_sha"] == head
    assert run["_pr_number"] == 2492
    assert run["_payload_link_state"] == "number_mismatch"


def test_pr_runtime_allows_selection_and_execution_base_to_diverge():
    repo, run, responses, logs, runtime_base, _ = _runtime_fixture()
    selection_base = "7" * 40
    logs[next(iter(logs))] = logs[next(iter(logs))].replace(runtime_base, selection_base)

    metrics.resolve_pr_runtime(RuntimeClient(responses, logs), repo, run, 100, {})

    assert run["_execution_base_sha"] == runtime_base
    assert run["_selection_base_sha"] == selection_base
    assert run["_base_alignment"] == "diverged"


def test_pr_runtime_rejects_merge_parent_mismatch():
    repo, run, responses, logs, _, _ = _runtime_fixture(bad_parents=True)
    with pytest.raises(metrics.MetricsError, match="parents do not match"):
        metrics.resolve_pr_runtime(RuntimeClient(responses, logs), repo, run, 100, {})


def test_pr_runtime_rejects_ambiguous_log_evidence():
    repo, run, responses, logs, _, _ = _runtime_fixture()
    logs[next(iter(logs))] += f"git fetch origin +{'4' * 40}:refs/remotes/pull/2492/merge\n"
    with pytest.raises(metrics.MetricsError, match="log evidence is ambiguous"):
        metrics.resolve_pr_runtime(RuntimeClient(responses, logs), repo, run, 100, {})


def test_attempts_keep_first_failure_and_retry_recovery_separate():
    repo = "open-ace/open-ace"
    run_id = 31458130891
    base = f"/repos/{repo}/actions/runs/{run_id}/attempts"
    responses = {
        f"{base}/1": _attempt_meta(
            1, "failure", "2026-08-11T04:19:47Z", "2026-08-11T04:19:47Z", "2026-08-11T04:28:19Z"
        ),
        f"{base}/1/jobs?per_page=100": {
            "total_count": 3,
            "jobs": [
                _job(
                    1,
                    "failure",
                    "2026-08-11T04:20:08Z",
                    "2026-08-11T04:20:09Z",
                    "2026-08-11T04:28:11Z",
                ),
                {
                    **_job(
                        3,
                        "success",
                        "2026-08-11T04:19:50Z",
                        "2026-08-11T04:19:51Z",
                        "2026-08-11T04:21:54Z",
                    ),
                    "name": "lint",
                },
                {
                    **_job(
                        4,
                        "skipped",
                        "2026-08-11T04:20:01Z",
                        "2026-08-11T04:20:01Z",
                        "2026-08-11T04:20:01Z",
                    ),
                    "name": "Dependency security audit",
                },
            ],
        },
        f"{base}/2": _attempt_meta(
            2, "success", "2026-08-11T04:29:30Z", "2026-08-11T04:29:29Z", "2026-08-11T04:34:40Z"
        ),
        f"{base}/2/jobs?per_page=100": {
            "total_count": 3,
            "jobs": [
                _job(
                    2,
                    "success",
                    "2026-08-11T04:29:31Z",
                    "2026-08-11T04:29:32Z",
                    "2026-08-11T04:34:06Z",
                ),
                {
                    **_job(
                        5,
                        "success",
                        "2026-08-11T04:29:31Z",
                        "2026-08-11T04:19:51Z",
                        "2026-08-11T04:21:54Z",
                    ),
                    "name": "lint",
                },
                {
                    **_job(
                        6,
                        "skipped",
                        "2026-08-11T04:29:31Z",
                        "2026-08-11T04:29:31Z",
                        "2026-08-11T04:20:01Z",
                    ),
                    "name": "Dependency security audit",
                },
            ],
        },
    }
    run = {
        "id": run_id,
        "run_attempt": 2,
        "created_at": "2026-08-11T04:19:47Z",
        "head_sha": "head",
    }
    attempts = metrics.collect_attempts(AttemptClient(responses), repo, run, 100)
    normalized = metrics.normalize_run(run, "contract", {}, attempts)
    aggregate = metrics.aggregate_contract([normalized], min_samples=20)

    assert attempts[1]["queue"]["seconds"] == 0
    assert attempts[1]["queue"]["timestamp_skew_clamped"] is True
    assert normalized["recovered_on_retry"] is True
    assert aggregate["first_conclusions"] == {"failure": 1}
    assert aggregate["eventual_conclusions"] == {"success": 1}
    assert aggregate["retry_recovery_count"] == 1
    assert aggregate["inherited_job_snapshot_count"] == 2
    assert aggregate["workflow_queue"]["count"] == 2
    assert aggregate["job_queue"]["count"] == 4
    assert attempts[1]["jobs"][1]["inherited_from_job_id"] == 3
    assert attempts[1]["jobs"][2]["inherited_from_job_id"] == 4


def test_inherited_job_without_canonical_origin_is_invalid():
    repo = "open-ace/open-ace"
    run_id = 2
    base = f"/repos/{repo}/actions/runs/{run_id}/attempts"
    responses = {
        f"{base}/1": _attempt_meta(
            1,
            "failure",
            "2026-08-11T00:00:00Z",
            "2026-08-11T00:00:00Z",
            "2026-08-11T00:01:00Z",
        ),
        f"{base}/1/jobs?per_page=100": {"total_count": 0, "jobs": []},
        f"{base}/2": _attempt_meta(
            2,
            "success",
            "2026-08-11T00:02:00Z",
            "2026-08-11T00:02:00Z",
            "2026-08-11T00:03:00Z",
        ),
        f"{base}/2/jobs?per_page=100": {
            "total_count": 1,
            "jobs": [
                {
                    **_job(
                        9,
                        "success",
                        "2026-08-11T00:02:00Z",
                        "2026-08-11T00:00:10Z",
                        "2026-08-11T00:00:20Z",
                    ),
                    "name": "lint",
                }
            ],
        },
    }
    with pytest.raises(metrics.MetricsError, match="canonical timing origin"):
        metrics.collect_attempts(
            AttemptClient(responses),
            repo,
            {"id": run_id, "run_attempt": 2},
            100,
        )


def test_attempt_jobs_must_be_complete():
    repo = "open-ace/open-ace"
    run_id = 1
    responses = {
        f"/repos/{repo}/actions/runs/{run_id}/attempts/1": _attempt_meta(
            1, "success", "2026-08-11T00:00:00Z", "2026-08-11T00:00:00Z", "2026-08-11T00:01:00Z"
        ),
        f"/repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=100": {
            "total_count": 2,
            "jobs": [],
        },
    }
    with pytest.raises(metrics.MetricsError, match="jobs incomplete"):
        metrics.collect_attempts(
            AttemptClient(responses), repo, {"id": run_id, "run_attempt": 1}, 100
        )
