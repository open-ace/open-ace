import pytest

from scripts import run_extended_tests


def test_critical_category_selects_pr_gate_targets():
    args = run_extended_tests.parse_args(["--category", "critical", "--dry-run"])

    cmd = run_extended_tests.build_pytest_command(args)

    assert "tests/e2e/regression/test_login.py" in cmd
    assert "tests/e2e/regression/test_navigation.py" in cmd
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


def test_specific_category_requires_target():
    args = run_extended_tests.parse_args(["--category", "specific"])

    with pytest.raises(ValueError, match="requires at least one --target"):
        run_extended_tests.select_targets(args)


def test_split_uses_deterministic_file_shards():
    files = run_extended_tests.apply_split(["tests/e2e/regression"], split_total=2, split_group=1)

    assert files
    assert files == sorted(files)
    assert all(file.startswith("tests/e2e/regression/") for file in files)


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


def test_frontend_build_check_fails_fast_when_dist_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_extended_tests, "PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="Frontend build is missing"):
        run_extended_tests.ensure_frontend_built("critical")
