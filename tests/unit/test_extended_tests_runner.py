"""Tests for scripts/run_extended_tests.py (Issue #1469).

Migrated from tests/issues/1469/test_extended_tests_runner.py. Batch 14 made
the issue selections drain-proof by deriving them from
tests/issues/legacy-directories.txt; the #2429 FINAL batch (17) retires the
whole lane instead: the ``issues`` category, the ``--issue``/``--issue-numbers``
selection, and the ci/legacy-issue-quarantine.json deselect machinery are
gone, and these tests now pin that retired contract.
"""

import subprocess
import sys

import pytest

from scripts import run_extended_tests

pytestmark = [pytest.mark.regression, pytest.mark.issue(1469)]


def test_critical_category_selects_pr_gate_targets():
    args = run_extended_tests.parse_args(["--category", "critical", "--dry-run"])

    cmd = run_extended_tests.build_pytest_command(args)

    assert "tests/e2e/browser/test_login.py" in cmd
    # The runner expands node selectors into a deterministic file manifest
    # before sharding, so the navigation module appears once in the command.
    assert "tests/e2e/browser/test_navigation.py" in cmd
    assert "-m" in cmd
    assert "not postgres" in cmd
    # The legacy quarantine deselect machinery is retired with the lane.
    assert "--deselect" not in cmd


def test_issues_category_is_retired():
    """#2429 final exodus: no category or flag may select tests/issues anymore."""
    with pytest.raises(SystemExit):
        run_extended_tests.parse_args(["--category", "issues", "--dry-run"])
    with pytest.raises(SystemExit):
        run_extended_tests.parse_args(["--category", "critical", "--issue", "517"])
    with pytest.raises(SystemExit):
        run_extended_tests.parse_args(["--category", "critical", "--issue-numbers", "517"])


def test_all_category_selects_only_the_e2e_tree():
    args = run_extended_tests.parse_args(["--category", "all", "--dry-run"])

    cmd = run_extended_tests.build_pytest_command(args)

    targets = [part for part in cmd if part.startswith("tests/")]
    assert targets, "the all category must still select a real test tree"
    assert all(target.startswith("tests/e2e/") for target in targets)
    assert not any(target.startswith("tests/issues") for target in targets)


def test_select_targets_skip_warns_missing_targets(capsys):
    args = run_extended_tests.parse_args(
        ["--category", "specific", "--target", "tests/e2e/no_such_dir", "--dry-run"]
    )

    with pytest.raises(FileNotFoundError, match="No selected test targets exist"):
        run_extended_tests.select_targets(args)

    assert "Skipping missing targets: tests/e2e/no_such_dir" in capsys.readouterr().out


def test_sharded_baseline_is_scaled(monkeypatch):
    monkeypatch.setattr(
        run_extended_tests,
        "load_baseline",
        lambda: {
            "layers": {"e2e_pytest": {"min_files": 400}},
            "tolerance": {"require_review_threshold": 10},
        },
    )

    assert run_extended_tests.check_baseline("e2e", file_count=100, split_total=4)
    assert not run_extended_tests.check_baseline("e2e", file_count=89, split_total=4)


def test_specific_category_requires_target():
    args = run_extended_tests.parse_args(["--category", "specific"])

    with pytest.raises(ValueError, match="requires at least one --target"):
        run_extended_tests.select_targets(args)


def test_split_uses_deterministic_file_shards():
    files = run_extended_tests.apply_split(["tests/e2e/browser"], split_total=2, split_group=1)

    assert files
    assert files == sorted(files)
    assert all(file.startswith("tests/e2e/browser/") for file in files)


def test_ensure_sqlite_schema_creates_isolated_test_database(tmp_path):
    run_extended_tests.ensure_sqlite_schema({"HOME": str(tmp_path)})

    db_path = tmp_path / ".open-ace" / "ace.db"
    assert run_extended_tests.sqlite_has_table(db_path, "tenants")
    assert run_extended_tests.sqlite_has_table(db_path, "users")


def test_prepare_test_home_preserves_existing_playwright_browser_cache(tmp_path):
    browsers_path = run_extended_tests.default_playwright_browsers_path(tmp_path)
    browsers_path.mkdir(parents=True)
    env = {"HOME": str(tmp_path)}

    test_home = run_extended_tests.prepare_test_home(env, isolated_home=True)
    try:
        assert env["HOME"] != str(tmp_path)
        assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers_path)
    finally:
        assert test_home is not None
        test_home.cleanup()


def test_prepare_test_home_preserves_explicit_playwright_browser_path(tmp_path):
    browsers_path = tmp_path / "shared-playwright-browsers"
    env = {
        "HOME": str(tmp_path),
        "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path),
    }

    test_home = run_extended_tests.prepare_test_home(env, isolated_home=True)
    try:
        assert env["HOME"] != str(tmp_path)
        assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers_path)
    finally:
        assert test_home is not None
        test_home.cleanup()


def test_isolated_base_url_uses_loopback_and_an_available_port():
    base_url = run_extended_tests.isolated_base_url("http://localhost:19888")

    assert base_url.startswith("http://127.0.0.1:")
    host_port = base_url.removeprefix("http://")
    host, port = host_port.rsplit(":", 1)
    assert not run_extended_tests.can_connect(host, int(port))


def test_configure_server_address_matches_test_url(tmp_path):
    config_dir = tmp_path / ".open-ace"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"database": {"type": "sqlite"}}')

    run_extended_tests.configure_server_address({"HOME": str(tmp_path)}, "http://127.0.0.1:23456")

    config = __import__("json").loads((config_dir / "config.json").read_text())
    assert config["server"] == {"web_host": "127.0.0.1", "web_port": 23456}


def test_frontend_build_check_fails_fast_when_dist_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_extended_tests, "PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="Frontend build is missing"):
        run_extended_tests.ensure_frontend_built(True)


def test_e2e_attempts_enable_attempt_plugin():
    args = run_extended_tests.parse_args(
        [
            "--category",
            "critical",
            "--dry-run",
            "--e2e-attempts",
            "test-results/full-e2e-attempts.jsonl",
        ]
    )

    cmd = run_extended_tests.build_pytest_command(args)

    assert "-p" in cmd
    assert "scripts.e2e.pytest_attempts" in cmd
    assert "--e2e-attempts=test-results/full-e2e-attempts.jsonl" in cmd


def test_selection_json_uses_exact_targets_and_skips_standalone_for_pytest(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        __import__("json").dumps(
            {
                "normal": ["tests/e2e/browser/test_login.py::test_login_page_loads"],
                "advisory": ["standalone::tests/e2e/e2e_autonomous_models_error_playwright.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    args = run_extended_tests.parse_args(
        ["--category", "e2e", "--selection-json", str(selection), "--dry-run"]
    )

    assert run_extended_tests.resolved_targets(args) == [
        "tests/e2e/browser/test_login.py::test_login_page_loads",
        "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py",
    ]
    cmd = run_extended_tests.build_pytest_command(args)

    assert "tests/e2e/browser/test_login.py::test_login_page_loads" in cmd
    assert "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py" not in cmd


def test_selection_json_shards_nodeids_and_standalone_entries_together(tmp_path):
    selection = tmp_path / "selection.json"
    selected = [
        "tests/e2e/browser/test_login.py::test_login_page_loads",
        "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py",
        "tests/e2e/browser/test_navigation.py::test_menu_navigation",
    ]
    selection.write_text(
        __import__("json").dumps({"normal": selected, "advisory": []}) + "\n",
        encoding="utf-8",
    )
    first = run_extended_tests.parse_args(
        [
            "--category",
            "e2e",
            "--selection-json",
            str(selection),
            "--split-total",
            "2",
            "--split-group",
            "1",
        ]
    )
    second = run_extended_tests.parse_args(
        [
            "--category",
            "e2e",
            "--selection-json",
            str(selection),
            "--split-total",
            "2",
            "--split-group",
            "2",
        ]
    )

    first_targets = run_extended_tests.resolved_targets(first)
    second_targets = run_extended_tests.resolved_targets(second)
    assert set(first_targets).isdisjoint(second_targets)
    assert set(first_targets) | set(second_targets) == set(selected)
    assert any(target.startswith("standalone::") for target in first_targets + second_targets)


def test_execution_needs_server_uses_selected_item_capabilities(tmp_path, monkeypatch):
    selection = tmp_path / "selection.json"
    selection.write_text(
        __import__("json").dumps(
            {
                "normal": ["standalone::tests/e2e/custom_standalone.py"],
                "advisory": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    args = run_extended_tests.parse_args(
        ["--category", "e2e", "--selection-json", str(selection), "--dry-run"]
    )
    monkeypatch.setattr(
        run_extended_tests,
        "inventory_entries_by_path",
        lambda: {"tests/e2e/custom_standalone.py": {"capabilities": ["browser"]}},
    )

    assert (
        run_extended_tests.execution_needs_server(
            args, ["standalone::tests/e2e/custom_standalone.py"]
        )
        is False
    )


def test_execution_needs_server_falls_back_to_category_for_unknown_targets(tmp_path, monkeypatch):
    selection = tmp_path / "selection.json"
    selection.write_text(
        __import__("json").dumps(
            {
                "normal": ["tests/e2e/browser/test_login.py::test_login_page_loads"],
                "advisory": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    args = run_extended_tests.parse_args(
        ["--category", "e2e", "--selection-json", str(selection), "--dry-run"]
    )
    monkeypatch.setattr(
        run_extended_tests,
        "inventory_entries_by_path",
        lambda: {"tests/e2e/unrelated.py": {"capabilities": ["browser"]}},
    )

    assert (
        run_extended_tests.execution_needs_server(
            args, ["tests/e2e/browser/test_login.py::test_login_page_loads"]
        )
        is True
    )


def test_cli_entrypoint_writes_envelope_with_selection_json(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        __import__("json").dumps(
            {"normal": ["tests/e2e/browser/test_login.py::test_login_page_loads"], "advisory": []}
        )
        + "\n",
        encoding="utf-8",
    )
    envelope = tmp_path / "envelope.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_extended_tests.py",
            "--category",
            "e2e",
            "--selection-json",
            str(selection),
            "--dry-run",
            "--envelope-json",
            str(envelope),
        ],
        cwd=run_extended_tests.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    assert payload["selected_targets"] == ["tests/e2e/browser/test_login.py::test_login_page_loads"]
    assert payload["pytest_command"][0] == sys.executable


def test_write_run_envelope_summarizes_attempts(tmp_path, monkeypatch):
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "\n".join(
            [
                '{"nodeid":"tests/e2e/browser/test_login.py::test_ok","attempt":1,"phase":"call","outcome":"passed","duration_seconds":1.25}',
                '{"nodeid":"tests/e2e/browser/test_navigation.py::test_fail","attempt":1,"phase":"call","outcome":"failed","duration_seconds":2.5,"exception_class":"AssertionError","message":"boom"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    envelope = tmp_path / "envelope.json"
    args = run_extended_tests.parse_args(
        [
            "--category",
            "e2e",
            "--junitxml",
            "test-results/full-e2e.xml",
            "--e2e-attempts",
            str(attempts),
            "--envelope-json",
            str(envelope),
        ]
    )
    monkeypatch.setattr(run_extended_tests, "_current_head_sha", lambda: "deadbeef")
    monkeypatch.setattr(run_extended_tests, "_current_contract_key", lambda: "contract-v1")
    monkeypatch.setattr(run_extended_tests, "is_healthy", lambda _base_url: True)

    run_extended_tests._write_run_envelope(
        str(envelope),
        args=args,
        env={"HOME": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": "/pw"},
        cmd=["pytest", "tests/e2e/browser/test_login.py"],
        selected_targets=["tests/e2e/browser/test_login.py"],
        needs_server=True,
        server_handle=None,
        return_code=1,
        started_at="2026-08-18T01:00:00Z",
        completed_at="2026-08-18T01:02:30Z",
        standalone_outcomes=None,
    )

    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    assert payload["schema_name"] == "openace-e2e-run-envelope"
    assert payload["commit_sha"] == "deadbeef"
    assert payload["contract_key"] == "contract-v1"
    assert payload["duration_minutes"] == 2.5
    assert payload["job_conclusion"] == "success"
    assert payload["return_code"] == 1
    assert payload["error"] is None
    assert payload["artifacts"]["attempts_jsonl"] == str(attempts)
    assert payload["server"]["readiness_achieved"] is True
    assert [item["nodeid"] for item in payload["outcomes"]] == [
        "tests/e2e/browser/test_login.py::test_ok",
        "tests/e2e/browser/test_navigation.py::test_fail",
    ]
    failed = payload["outcomes"][1]
    assert failed["category"] == "assertion_failure"
    assert failed["final_outcome"] == "fail"
    assert failed["fingerprint"]
    assert failed["duration_seconds"] == 2.5
    assert failed["total_duration_seconds"] == 2.5


def test_summarize_attempts_handles_missing_exception_class():
    outcomes = run_extended_tests._summarize_attempt_records(
        [
            {
                "nodeid": "tests/e2e/ui/test_work_page_loads.py::test_work_page_loads",
                "attempt": 1,
                "phase": "call",
                "outcome": "failed",
                "duration_seconds": 2.5,
                "exception_class": None,
                "message": "work layout was missing",
            }
        ],
        {"readiness_achieved": True, "exit": {"abnormal": False}},
    )

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["nodeid"] == "tests/e2e/ui/test_work_page_loads.py::test_work_page_loads"
    assert outcome["final_outcome"] == "fail"
    assert outcome["category"] == "test_body_exception"
    assert outcome["fingerprint"]
    assert outcome["exception_class"] is None
    assert outcome["message"] == "work layout was missing"


def test_setup_phase_failure_is_not_summarized_as_pass(tmp_path, monkeypatch):
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "\n".join(
            [
                '{"nodeid":"tests/e2e/browser/test_login.py::test_setup_failure","attempt":1,"phase":"setup","outcome":"failed","duration_seconds":3.0,"exception_class":"RuntimeError","message":"boom"}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_setup_failure","attempt":1,"phase":"teardown","outcome":"passed","duration_seconds":1.0}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    envelope = tmp_path / "envelope.json"
    args = run_extended_tests.parse_args(
        ["--category", "e2e", "--e2e-attempts", str(attempts), "--envelope-json", str(envelope)]
    )
    monkeypatch.setattr(run_extended_tests, "_current_head_sha", lambda: "deadbeef")
    monkeypatch.setattr(run_extended_tests, "_current_contract_key", lambda: "contract-v1")
    monkeypatch.setattr(run_extended_tests, "is_healthy", lambda _base_url: True)

    run_extended_tests._write_run_envelope(
        str(envelope),
        args=args,
        env={"HOME": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": "/pw"},
        cmd=["pytest", "tests/e2e/browser/test_login.py"],
        selected_targets=["tests/e2e/browser/test_login.py"],
        needs_server=True,
        server_handle=None,
        return_code=1,
        started_at="2026-08-18T01:00:00Z",
        completed_at="2026-08-18T01:00:05Z",
        standalone_outcomes=None,
    )

    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    outcome = payload["outcomes"][0]
    assert outcome["final_outcome"] == "fail"
    assert outcome["category"] == "setup_error"
    assert outcome["duration_seconds"] == 4.0


def test_malformed_attempt_line_is_skipped(tmp_path):
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text('{"nodeid":"a","attempt":1}\n{"nodeid":"bad"\n', encoding="utf-8")

    records = run_extended_tests._load_attempt_records(str(attempts))

    assert records == [{"nodeid": "a", "attempt": 1}]


def test_rerun_duration_budget_uses_max_attempt_not_sum(tmp_path, monkeypatch):
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "\n".join(
            [
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":1,"phase":"setup","outcome":"passed","duration_seconds":5.0}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":1,"phase":"call","outcome":"failed","duration_seconds":120.0,"exception_class":"AssertionError","message":"boom"}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":1,"phase":"teardown","outcome":"passed","duration_seconds":5.0}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":2,"phase":"setup","outcome":"passed","duration_seconds":5.0}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":2,"phase":"call","outcome":"passed","duration_seconds":120.0}',
                '{"nodeid":"tests/e2e/browser/test_login.py::test_rerun","attempt":2,"phase":"teardown","outcome":"passed","duration_seconds":5.0}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    envelope = tmp_path / "envelope.json"
    args = run_extended_tests.parse_args(
        ["--category", "e2e", "--e2e-attempts", str(attempts), "--envelope-json", str(envelope)]
    )
    monkeypatch.setattr(run_extended_tests, "_current_head_sha", lambda: "deadbeef")
    monkeypatch.setattr(run_extended_tests, "_current_contract_key", lambda: "contract-v1")
    monkeypatch.setattr(run_extended_tests, "is_healthy", lambda _base_url: True)

    run_extended_tests._write_run_envelope(
        str(envelope),
        args=args,
        env={"HOME": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": "/pw"},
        cmd=["pytest", "tests/e2e/browser/test_login.py"],
        selected_targets=["tests/e2e/browser/test_login.py"],
        needs_server=True,
        server_handle=None,
        return_code=0,
        started_at="2026-08-18T01:00:00Z",
        completed_at="2026-08-18T01:04:20Z",
        standalone_outcomes=None,
    )

    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    outcome = payload["outcomes"][0]
    assert outcome["final_outcome"] == "pass"
    assert outcome["duration_seconds"] == 130.0
    assert outcome["total_duration_seconds"] == 260.0


def test_write_run_envelope_includes_standalone_outcomes(tmp_path, monkeypatch):
    envelope = tmp_path / "envelope.json"
    args = run_extended_tests.parse_args(["--category", "e2e", "--envelope-json", str(envelope)])
    monkeypatch.setattr(run_extended_tests, "_current_head_sha", lambda: "deadbeef")
    monkeypatch.setattr(run_extended_tests, "_current_contract_key", lambda: "contract-v1")
    monkeypatch.setattr(run_extended_tests, "is_healthy", lambda _base_url: True)

    run_extended_tests._write_run_envelope(
        str(envelope),
        args=args,
        env={"HOME": str(tmp_path), "PLAYWRIGHT_BROWSERS_PATH": "/pw"},
        cmd=["pytest", "tests/e2e/browser/test_login.py"],
        selected_targets=["standalone::tests/e2e/e2e_autonomous_models_error_playwright.py"],
        needs_server=False,
        server_handle=None,
        return_code=0,
        started_at="2026-08-18T01:00:00Z",
        completed_at="2026-08-18T01:00:05Z",
        standalone_outcomes=[
            {
                "nodeid": "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py",
                "attempts": 1,
                "first_attempt_outcome": "pass",
                "final_outcome": "pass",
                "duration_seconds": 4.0,
            }
        ],
    )

    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    assert payload["outcomes"][0]["nodeid"] == (
        "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py"
    )
    assert payload["server"]["readiness_achieved"] is None


def test_standalone_targets_retry_and_preserve_attempt_evidence(monkeypatch):
    responses = iter(
        [
            subprocess.CompletedProcess([], returncode=1),
            subprocess.CompletedProcess([], returncode=0),
        ]
    )
    monkeypatch.setattr(
        run_extended_tests.subprocess, "run", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr(run_extended_tests.time, "sleep", lambda _seconds: None)

    outcomes = run_extended_tests._run_standalone_targets(
        ["standalone::tests/e2e/e2e_autonomous_models_error_playwright.py"],
        env={},
        timeout_seconds=10,
        reruns=1,
    )

    assert outcomes == [
        {
            "nodeid": "standalone::tests/e2e/e2e_autonomous_models_error_playwright.py",
            "attempts": 2,
            "first_attempt_outcome": "fail",
            "final_outcome": "pass",
            "duration_seconds": 0.0,
            "total_duration_seconds": 0.0,
            "attempt_durations_seconds": {1: 0.0, 2: 0.0},
        }
    ]


def test_dry_run_envelope_reports_success(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        __import__("json").dumps(
            {"normal": ["tests/e2e/browser/test_login.py::test_login_page_loads"], "advisory": []}
        )
        + "\n",
        encoding="utf-8",
    )
    envelope = tmp_path / "dry-run-envelope.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_extended_tests.py",
            "--category",
            "e2e",
            "--selection-json",
            str(selection),
            "--dry-run",
            "--envelope-json",
            str(envelope),
        ],
        cwd=run_extended_tests.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = __import__("json").loads(envelope.read_text(encoding="utf-8"))
    assert payload["return_code"] == 0
    assert payload["job_conclusion"] == "success"
