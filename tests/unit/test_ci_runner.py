"""Tests for the local/GitHub shared CI runner."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("openace_ci", ROOT / "scripts" / "ci.py")
assert SPEC and SPEC.loader
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)

CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _requires_python_spec() -> SpecifierSet:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return SpecifierSet(project["project"]["requires-python"])


def _min_supported_python() -> str:
    """Return the lowest supported interpreter as a 'major.minor' string."""
    lowers = [
        Version(s.version) for s in _requires_python_spec() if s.operator in (">=", "==", "~=")
    ]
    assert lowers, "requires-python must declare a lower bound"
    low = min(lowers)
    return f"{low.major}.{low.minor}"


def _test_matrix() -> dict[str, dict]:
    """Map 'major.minor' -> matrix include entry for the ci.yml `test` job."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    include = workflow["jobs"]["test"]["strategy"]["matrix"]["include"]
    return {str(entry["python-version"]): entry for entry in include}


def test_docs_only_change_selects_no_runtime_suite():
    assert ci.select_pr_suites(["docs/TEST_LAYERS.md", "README.md"]) == []


def test_backend_change_selects_production_python_suite():
    assert ci.select_pr_suites(["app/services/auth_service.py"]) == [
        "default-collection",
        "python-core",
        "python-min",
    ]


def test_min_supported_python_runs_the_full_unit_suite():
    """#2868: the OLDEST supported interpreter must run the FULL unit suite.

    A version-specific regression surfaces on the lowest interpreter first
    (e.g. datetime.fromisoformat rejecting a 'Z' suffix before 3.11). The old
    matrix ran only a hand-picked 7-file smoke on 3.10, so #2868's failing unit
    test (test_models_session) was not covered and the break sailed through the
    PR AND the post-merge main push. Assert the min lane runs `python-min`,
    which runs `pytest tests/unit/` (the WHOLE tree) + compileall.
    """
    matrix = _test_matrix()
    min_py = _min_supported_python()
    assert min_py in matrix, f"min supported python {min_py} missing from ci test matrix"
    assert matrix[min_py]["suite"] == "python-min", (
        f"min supported python {min_py} must run the python-min suite, "
        f"got {matrix[min_py].get('suite')!r}"
    )

    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    commands = suites["python-min"]["commands"]
    flat = [tuple(c) for c in commands]
    assert any(
        c[:3] == ("{python}", "-m", "compileall") for c in flat
    ), "python-min must compileall"
    pytest_cmd = next((c for c in flat if c[:3] == ("{python}", "-m", "pytest")), None)
    assert pytest_cmd is not None, "python-min must run pytest"
    # The WHOLE unit tree, not a hand-picked subset — that is the #2868 fix.
    assert (
        "tests/unit/" in pytest_cmd
    ), f"python-min must run the full tests/unit/, got {pytest_cmd}"


def test_python_min_timeout_budget_absorbs_runner_variance():
    """#3240: python-min's suite budget must absorb GitHub-hosted runner variance.

    Evidence: the SAME commit ran 183s and 652s on the 3.10 lane (PR #3205
    run 33410540459 first attempt vs same-commit rerun), and main run
    33384495716 was budget-killed at
    599s ("Command exceeded 599s") with ZERO test failures. The lane is a
    required check, so the kill randomly blocked merges. The 600s budget was
    shared by compileall + the whole pytest run. 1200s keeps ~1.8x headroom
    over the worst observed run without masking a real slowdown: the same
    unit tests also run under python-core's unchanged 600s on 3.11 (typical
    340-388s), which would trip first. Changing this pin requires consciously
    re-deriving the budget from fresh variance evidence, not silently
    trimming it back toward the variance cliff.
    """
    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    assert suites["python-min"]["timeout_seconds"] == 1200


def test_backend_change_runs_the_min_version_unit_lane():
    """End-to-end of the #2868 fix: an app/** change selects python-min, and the
    matrix runs python-min on the minimum supported interpreter -- so the full
    unit suite actually executes on py-min for source changes (on the PR and on
    the post-merge push to main)."""
    selected = ci.select_pr_suites(["app/models/session.py"])
    assert "python-min" in selected
    assert _test_matrix()[_min_supported_python()]["suite"] == "python-min"


def test_every_matrix_python_is_supported():
    """No matrix lane may target an interpreter outside requires-python."""
    spec = _requires_python_spec()
    for key in _test_matrix():
        assert Version(key) in spec, f"ci test matrix targets unsupported python {key}"


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


def _requirement_names(lines):
    return {
        canonicalize_name(Requirement(line).name)
        for raw_line in lines
        if (line := raw_line.strip()) and not line.startswith(("#", "-r "))
    }


def test_development_tools_stay_out_of_production_requirements():
    production = _requirement_names((ROOT / "requirements.txt").read_text().splitlines())
    ci_input = _requirement_names((ROOT / "requirements-ci.in").read_text().splitlines())

    assert ci_input
    assert production.isdisjoint(ci_input)


def test_ci_input_matches_packaging_dev_extra():
    ci_input = _requirement_names((ROOT / "requirements-ci.in").read_text().splitlines())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev_extra = _requirement_names(project["project"]["optional-dependencies"]["dev"])

    assert ci_input == dev_extra


def test_ci_policy_change_fails_safe_to_all_pr_suites():
    selected = ci.select_pr_suites(["ci/suites.json"])
    assert set(selected) == set(ci.load_config()["pr_suites"])
    assert "performance" not in selected


def test_compatibility_smoke_stays_bounded():
    command = ci.load_config()["suites"]["compatibility-smoke"]["commands"][1]
    targets = [part for part in command if part.startswith("tests/unit/")]

    assert "tests/unit/" not in command
    assert 3 <= len(targets) <= 10


def test_e2e_governance_change_selects_governance_suite():
    selected = ci.select_pr_suites(["scripts/e2e/comparator.py"])
    assert "e2e-governance" in selected


def test_extended_runner_change_selects_critical_and_governance_suites():
    selected = ci.select_pr_suites(["scripts/run_extended_tests.py"])
    assert "critical-e2e" in selected
    assert "e2e-governance" in selected


def test_test_change_keeps_default_collection_and_retires_issue_collection():
    """#2429 final exodus: tests/** changes still gate on default collection,
    and the legacy issue-collection suite is gone from the registry."""
    selected = ci.select_pr_suites(["tests/unit/test_example.py"])
    assert "default-collection" in selected
    assert "issue-collection" not in ci.load_config()["suites"]
    assert "issue-collection" not in ci.load_config()["pr_suites"]


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


def test_collection_file_count_covers_default_tree():
    assert ci.candidate_test_file_count("tests") >= 250


def test_legacy_issue_quarantine_tree_is_retired():
    """#2429 final exodus: the tests/issues quarantine no longer exists, so its
    per-issue collection floor (the long min_files chain this test used to
    re-assert) is retired with it. Regressions live in canonical layers and
    are collected by the default-collection gate."""
    assert not (ROOT / "tests" / "issues").exists(), (
        "tests/issues was retired with the #2429 final exodus; new tests belong "
        "in tests/unit, tests/integration, tests/e2e, or tests/performance"
    )


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
