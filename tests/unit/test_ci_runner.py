"""Tests for the local/GitHub shared CI runner."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("openace_ci", ROOT / "scripts" / "ci.py")
assert SPEC and SPEC.loader
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def test_docs_only_change_selects_no_runtime_suite():
    assert ci.select_pr_suites(["docs/TEST_LAYERS.md", "README.md"]) == []


def test_backend_change_selects_production_python_suite():
    assert ci.select_pr_suites(["app/services/auth_service.py"]) == [
        "default-collection",
        "issue-collection",
        "legacy-pr",
        "python-core",
    ]


def test_frontend_change_selects_frontend_and_critical_e2e():
    selected = ci.select_pr_suites(["frontend/src/App.tsx"])
    assert "python-core" in selected
    assert "frontend" in selected
    assert "critical-e2e" in selected


def test_database_change_selects_postgres():
    selected = ci.select_pr_suites(["app/repositories/database.py"])
    assert "postgres" in selected


def test_dependency_change_selects_audit_and_compatibility():
    selected = ci.select_pr_suites(["frontend/package-lock.json"])
    assert "dependency-audit" in selected
    assert "compatibility-smoke" in selected


def test_python_lock_change_selects_package_and_dependency_suites():
    selected = ci.select_pr_suites(["requirements-ci.lock"])
    assert "package" in selected
    assert "dependency-audit" in selected
    assert "compatibility-smoke" in selected


def test_ci_input_change_selects_package_and_dependency_suites():
    selected = ci.select_pr_suites(["requirements-ci.in"])
    assert "package" in selected
    assert "dependency-audit" in selected
    assert "compatibility-smoke" in selected


def test_ci_only_tools_stay_out_of_production_requirements():
    production = (ROOT / "requirements.txt").read_text().lower().splitlines()
    declared = {
        line.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].strip()
        for line in production
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert declared.isdisjoint({"bandit", "build", "pip-audit", "playwright"})


def test_ci_policy_change_fails_safe_to_all_pr_suites():
    selected = ci.select_pr_suites(["ci/suites.json"])
    assert set(selected) == set(ci.load_config()["pr_suites"])
    assert "performance" not in selected


def test_test_change_selects_issue_collection():
    selected = ci.select_pr_suites(["tests/unit/test_example.py"])
    assert "default-collection" in selected
    assert "issue-collection" in selected


def test_isolated_environment_drops_developer_database_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://developer-database")
    monkeypatch.setenv("DB_HOST", "developer-host")

    env = ci.isolated_environment(str(tmp_path))

    assert env["HOME"] == str(tmp_path)
    assert env["TZ"] == "UTC"
    assert env["PYTHONHASHSEED"] == "0"
    assert "DATABASE_URL" not in env
    assert "DB_HOST" not in env


def test_collection_count_uses_final_pytest_summary():
    output = "100 tests collected\nwarning\n4417 tests collected in 2.1s\n"
    assert ci.collection_count(output) == 4417


def test_collection_file_count_honors_default_quarantine():
    assert ci.candidate_test_file_count("tests") >= 250
    assert ci.candidate_test_file_count("tests/issues") >= 430


def test_missing_push_base_fails_safe_to_policy_change():
    assert ci.changed_files("0000000000000000000000000000000000000000") == ["ci/suites.json"]


def test_changed_files_includes_committed_and_local_worktree_changes(monkeypatch):
    run = Mock()
    run.side_effect = [
        subprocess.CompletedProcess([], 0, "app/service.py\n", ""),
        subprocess.CompletedProcess([], 0, "tests/unit/test_local.py\n", ""),
        subprocess.CompletedProcess([], 0, "requirements-ci.lock\n", ""),
    ]
    monkeypatch.setattr(ci.subprocess, "run", run)

    assert ci.changed_files("base-sha") == [
        "app/service.py",
        "requirements-ci.lock",
        "tests/unit/test_local.py",
    ]
    assert run.call_args_list == [
        call(
            ["git", "diff", "--name-only", "base-sha...HEAD"],
            cwd=ci.PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        ),
        call(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=ci.PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        ),
        call(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ci.PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        ),
    ]
