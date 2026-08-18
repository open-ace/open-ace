import subprocess
import sys

import pytest

from scripts import run_extended_tests


def test_critical_category_selects_pr_gate_targets():
    args = run_extended_tests.parse_args(["--category", "critical", "--dry-run"])

    cmd = run_extended_tests.build_pytest_command(args)

    assert "tests/e2e/browser/test_login.py" in cmd
    # The runner expands node selectors into a deterministic file manifest
    # before sharding, so the navigation module appears once in the command.
    assert "tests/e2e/browser/test_navigation.py" in cmd
    assert "-m" in cmd
    assert "not postgres" in cmd


def test_issue_numbers_select_specific_issue_directories():
    args = run_extended_tests.parse_args(
        ["--category", "issues", "--issue", "716", "--issue-numbers", "740,762", "--dry-run"]
    )

    assert run_extended_tests.select_targets(args) == [
        "tests/issues/716",
        "tests/issues/740",
        "tests/issues/762",
    ]


def test_targeted_issue_run_does_not_use_full_suite_baseline(monkeypatch):
    args = run_extended_tests.parse_args(["--category", "issues", "--issue", "1469", "--dry-run"])
    monkeypatch.setattr(
        run_extended_tests,
        "check_baseline",
        lambda *args, **kwargs: pytest.fail("full-suite baseline must not be checked"),
    )

    command = run_extended_tests.build_pytest_command(args)

    assert any(path.startswith("tests/issues/1469/") for path in command)


def test_sharded_baseline_is_scaled(monkeypatch):
    monkeypatch.setattr(
        run_extended_tests,
        "load_baseline",
        lambda: {
            "layers": {"issues": {"min_files": 400}},
            "tolerance": {"require_review_threshold": 10},
        },
    )

    assert run_extended_tests.check_baseline("issues", file_count=100, split_total=4)
    assert not run_extended_tests.check_baseline("issues", file_count=89, split_total=4)


def test_specific_category_requires_target():
    args = run_extended_tests.parse_args(["--category", "specific"])

    with pytest.raises(ValueError, match="requires at least one --target"):
        run_extended_tests.select_targets(args)


def test_split_uses_deterministic_file_shards():
    files = run_extended_tests.apply_split(["tests/e2e/browser"], split_total=2, split_group=1)

    assert files
    assert files == sorted(files)
    assert all(file.startswith("tests/e2e/browser/") for file in files)


def test_invalid_issue_number_is_rejected():
    args = run_extended_tests.parse_args(["--category", "issues", "--issue", "../716"])

    with pytest.raises(ValueError, match="Invalid issue number"):
        run_extended_tests.select_targets(args)


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


def _write_q(tmp_path, obj):
    p = tmp_path / "q.json"
    import json

    p.write_text(json.dumps(obj))
    return p


def test_quarantine_loader_missing_file_fails_closed(tmp_path):
    with pytest.raises(SystemExit):
        run_extended_tests._quarantine_nodeids(path=tmp_path / "missing.json")


def test_quarantine_loader_corrupt_fails_closed(tmp_path):
    p = tmp_path / "q.json"
    p.write_text("{not json")
    with pytest.raises(SystemExit):
        run_extended_tests._quarantine_nodeids(path=p)


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
    assert payload["job_conclusion"] == "failure"
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


def test_quarantine_loader_bad_schema_fails_closed(tmp_path):
    p = _write_q(tmp_path, {"version": 1, "schema": "wrong", "entries": []})
    with pytest.raises(SystemExit):
        run_extended_tests._quarantine_nodeids(path=p)


def test_quarantine_loader_expired_entry_fails_closed(tmp_path):
    p = _write_q(
        tmp_path,
        {
            "version": 1,
            "schema": "openace-legacy-issue-quarantine",
            "entries": [
                {
                    "nodeid": "tests/issues/604/t.py::a",
                    "reason": "r",
                    "owner": "o",
                    "tracking_issue": "t",
                    "exit_condition": "e",
                    "expires_on": "2020-01-01",
                }
            ],
        },
    )
    with pytest.raises(SystemExit):
        run_extended_tests._quarantine_nodeids(path=p)
