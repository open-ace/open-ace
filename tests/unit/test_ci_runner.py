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
        "false-positive-scan",
        "python-core",
        "python-min",
    ]


def test_every_non_docs_change_selects_false_positive_scan():
    """#3186 Phase A: the scanner lane rides on every non-docs change.

    The docs promise "每个非文档改动都跑" is now literally what the selector
    implements — a tests-only change, a product-code change, a scanner-script
    change, and an empty-changes push all select it; only docs-only does not.
    """
    for changed in (
        ["tests/unit/test_example.py"],  # plain test change / new test
        ["app/services/auth_service.py"],  # product code
        ["scripts/scan_test_false_positives.py"],  # the scanner itself
        ["ci/false-positive-ledger.json"],  # ledger edit (also policy)
        [],  # empty change set
    ):
        assert "false-positive-scan" in ci.select_pr_suites(changed), changed
    assert "false-positive-scan" not in ci.select_pr_suites(["README.md"])


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
    assert any(c[:3] == ("{python}", "-m", "compileall") for c in flat), (
        "python-min must compileall"
    )
    pytest_cmd = next((c for c in flat if c[:3] == ("{python}", "-m", "pytest")), None)
    assert pytest_cmd is not None, "python-min must run pytest"
    # The WHOLE unit tree, not a hand-picked subset — that is the #2868 fix.
    assert "tests/unit/" in pytest_cmd, (
        f"python-min must run the full tests/unit/, got {pytest_cmd}"
    )


def test_python_min_timeout_budget_absorbs_runner_variance():
    """#3240: python-min's suite budget must absorb GitHub-hosted runner variance.

    Evidence: the SAME commit ran 183s and 652s on the 3.10 lane (PR #3205
    run 33410540459 first attempt vs same-commit rerun), and main run
    33384495716 was budget-killed at
    599s ("Command exceeded 599s") with ZERO test failures. The lane is a
    required check, so the kill randomly blocked merges. The 600s budget was
    shared by compileall + the whole pytest run. 1200s keeps ~1.8x headroom
    over the worst observed run without masking a real slowdown: per-test
    --timeout on both unit lanes (see test_unit_lanes_fail_fast_on_hung_tests)
    now catches genuine hangs far below the suite budget. Changing this pin
    requires consciously re-deriving the budget from fresh variance
    evidence, not silently trimming it back toward the variance cliff.
    """
    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    assert suites["python-min"]["timeout_seconds"] == 1200


def test_python_core_timeout_budget_absorbs_runner_variance():
    """#3280: python-core's suite budget must absorb GitHub-hosted runner variance.

    #3241 kept python-core at 600s as a "tripwire" that would trip before
    python-min's raised budget if the suite genuinely slowed down. The
    2026-09-01 evidence in #3280 shows that tripwire sits INSIDE the runner
    variance band, so it fires on healthy runs: the same main-branch suite
    passed in 535.69s (run 33497187190) while two other runs were killed at
    599s (33490406262, 33482741069) and the fast tail finishes in ~290-385s.
    A required check that dies on a 1.6x slow runner is a flake source, not
    a regression tripwire; the regression signal now comes from per-test
    --timeout instead (test_unit_lanes_fail_fast_on_hung_tests). 1200s matches
    python-min and keeps ~2.2x headroom over the slowest observed healthy
    run. Changing this pin requires re-deriving the budget from fresh
    variance evidence, not trimming it back toward the variance cliff.
    """
    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    assert suites["python-core"]["timeout_seconds"] == 1200


def _pytest_command(suite_name: str) -> list[str]:
    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    commands = [tuple(c) for c in suites[suite_name]["commands"]]
    pytest_cmd = next((c for c in commands if c[:3] == ("{python}", "-m", "pytest")), None)
    assert pytest_cmd is not None, f"{suite_name} must run pytest"
    return list(pytest_cmd)


def _has_flag_pair(command: list[str], pair: tuple[str, str]) -> bool:
    return any(command[i : i + 2] == list(pair) for i in range(len(command) - 1))


def test_unit_lanes_fail_fast_on_hung_tests():
    """#3280: unit lanes must bound each test instead of only the whole suite.

    pytest-timeout is installed but was never passed on these lanes, so a
    hung xdist worker (e.g. the gevent-at-collection deadlock shape #3280
    cites) burned the ENTIRE suite budget and died with a bare
    "Command exceeded 599s" plus undiagnosable "OSError: cannot send". A
    300s per-test bound with the thread method dumps every thread's stack
    and kills the hung worker loudly: 300s is ~6x the slowest single test
    on record (46.7s, full-suite --durations measurement on 2026-09-01),
    so it cannot falsely kill a healthy test, while a hang surfaces in
    bounded time with a stack trace instead of at the suite ceiling.
    --durations=20 prints the slowest tests on every run so a shrinking
    budget margin is visible in the log before it becomes a timeout.
    """
    for lane in ("python-core", "python-min"):
        command = _pytest_command(lane)
        for flag, value in (("--timeout", "300"), ("--timeout-method", "thread")):
            pair = (flag, value)
            assert _has_flag_pair(
                command, pair
            ), f"{lane} pytest command must pass {flag} {value} (got {command})"
        assert (
            "--durations" in command
        ), f"{lane} pytest command must pass --durations (got {command})"


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


# ---------------------------------------------------------------------------
# Known-debt ledger contract (#3186 Phase A)
# ---------------------------------------------------------------------------

KNOWN_DEBT_FROZEN_SEED = (
    (
        "erroneous_skip",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_batch_session_termination",
        1,
    ),
    (
        "no_assertion",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_batch_session_termination",
        1,
    ),
    (
        "erroneous_skip",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_deregister_with_active_session",
        1,
    ),
    (
        "no_assertion",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_deregister_with_active_session",
        1,
    ),
    (
        "erroneous_skip",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_full_deregistration_flow",
        1,
    ),
    (
        "no_assertion",
        "tests/e2e/remote/test_deregister_e2e.py",
        "TestDeregisterE2E.test_full_deregistration_flow",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestErrorHandlingIntegration.test_degraded_status_on_partial_failure",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestErrorHandlingIntegration.test_idempotent_collection",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestPerformanceIntegration.test_100_users_performance",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestPerformanceIntegration.test_large_file_handling",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestPermission700Collection.test_message_count_correct",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestPermission700Collection.test_two_users_permission_700_collected",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestPermission700Collection.test_user_id_mapping_correct",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestSecurityIntegration.test_symlink_attack_blocked",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_fetch_wrapper_integration_2543.py",
        "TestSecurityIntegration.test_web_service_cannot_read_other_users",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_coverage_data_in_result",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_coverage_data_in_result",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_multi_user_session_collection",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_multi_user_session_collection",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_session_data_persistence",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_session_data_persistence",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_single_user_session_collection",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_single_user_session_collection",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_tenant_attribution",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_tenant_attribution",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_user_id_resolution",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_multiuser_qwen_collection_2735.py",
        "TestMultiUserQwenCollection.test_user_id_resolution",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_agent_sessions_user_id_filled",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_agent_sessions_user_id_filled",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_daily_messages_user_id_filled",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_daily_messages_user_id_filled",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_tenant_isolation_in_aggregation",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_tenant_isolation_in_aggregation",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_tenant_summary_includes_qwen_data",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestTenantAttribution.test_tenant_summary_includes_qwen_data",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_by_username",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_by_username",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_returns_correct_id",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_returns_correct_id",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_returns_none_for_unknown",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_qwen_user_attribution_2735.py",
        "TestUserIdResolution.test_resolve_user_id_returns_none_for_unknown",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_system_user_sync_2735.py",
        "TestSystemUserSync.test_sync_failure_logging",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_system_user_sync_2735.py",
        "TestSystemUserSync.test_sync_failure_logging",
        1,
    ),
    (
        "erroneous_skip",
        "tests/integration/test_system_user_sync_2735.py",
        "TestSystemUserSync.test_sync_system_users_creates_users",
        1,
    ),
    (
        "no_assertion",
        "tests/integration/test_system_user_sync_2735.py",
        "TestSystemUserSync.test_sync_system_users_creates_users",
        1,
    ),
    (
        "erroneous_skip",
        "tests/performance/test_qwen_performance_2735.py",
        "TestPerformanceWithDatabase.test_100_session_files_with_db_performance",
        1,
    ),
    (
        "no_assertion",
        "tests/performance/test_qwen_performance_2735.py",
        "TestPerformanceWithDatabase.test_100_session_files_with_db_performance",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_agent_transport.py",
        "test_shutdown_on_an_already_dead_process_does_not_raise",
        1,
    ),
    ("no_assertion", "tests/unit/test_analytics_routes.py", "TestParseDateRange.test_route", 1),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestAuditLogging.test_audit_log_created",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestAuditLogging.test_username_sanitized",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestFileSizeLimits.test_large_file_rejected",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestIntegration.test_degraded_status_on_partial_failure",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestIntegration.test_multi_user_collection_with_permission_700",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestParameterValidation.test_exact_match_valid_params",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestUserIdentityMapping.test_no_match_returns_none",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestUserIdentityMapping.test_resolve_user_id_by_system_account",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_fetch_wrapper_2543.py",
        "TestUserIdentityMapping.test_resolve_user_id_by_username",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_issue_wrong_repo_3075.py",
        "TestIssueRepoFallback.test_fallback_raises_githubopserror",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_new_project_local_repo_init_2963.py",
        "TestValidateProjectPath.test_pass_if_valid_git_repository",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_gh_wrapper.py",
        "test_admin_merge_is_config_gated",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_gh_wrapper.py",
        "test_current_github_ops_gh_shapes_are_allowed",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_gh_wrapper.py",
        "test_dangerous_gh_shapes_are_denied",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_gh_wrapper.py",
        "test_version_and_help_are_only_standalone_passthrough",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_current_github_ops_git_shapes_are_allowed",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_forbidden_global_options_and_configs_are_denied",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_git_commands_require_hardening_globals",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_mutating_branches_are_limited_to_workflow_branches",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_relative_path_operands_cannot_escape_worktree",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_show_tree_paths_allow_real_filenames_but_not_escapes",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_openace_git_wrapper.py",
        "test_version_and_help_are_only_standalone_passthrough",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_client.py",
        "test_delete_sandbox_treats_404_as_success",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_policy.py",
        "test_a_cni_tier_accepts_any_public_proxy_host",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_policy.py",
        "test_an_allowlisted_proxy_url_passes",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_provider.py",
        "test_destroy_attribution_never_raises",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_wiring.py",
        "test_sweep_survives_a_provider_failure_on_one_row",
        1,
    ),
    (
        "no_assertion",
        "tests/unit/test_opensandbox_workspace.py",
        "test_apply_deleting_a_missing_path_is_not_an_error",
        1,
    ),
)

KNOWN_DEBT_FINDING_TOTAL = 77  # total findings across all seed entries
KNOWN_DEBT_LEDGER_SIZE = 77


def _ledger_counts() -> dict[tuple[str, str, str], int]:
    import json

    data = json.loads((ROOT / "ci" / "false-positive-ledger.json").read_text())
    return {
        (e["pattern"], e["file"], e.get("function") or "<module>"): int(e.get("count", 1))
        for e in data["entries"]
    }


def test_known_debt_ledger_is_frozen_seed_and_only_shrinks():
    """#3186: the known-debt ledger starts EXACTLY as the frozen seed and only shrinks.

    Four properties, all load-bearing (the seed tuples carry counts):
    1. identities ⊆ seed identities — substitution is programmatically red:
       "fix A, introduce B, hand-edit the ledger to add B" fails here because
       B is not in the frozen seed. Enrollment is impossible via --prune-ledger
       (removal-only by design).
    2. per-identity count <= seed count — raising an existing entry's count
       (e.g. 1 -> 2 to absorb a second hit in an already-indebted function)
       is red without a visible seed edit.
    3. sum(counts) == KNOWN_DEBT_FINDING_TOTAL — the EXACT total pin is the
       only guard against re-enrolling debt that was already paid down but
       still sits in the frozen seed (⊆ passes, total red). Do NOT relax to
       <=. A legitimate paydown (Phase B) prunes the ledger AND lowers this
       pin in the same PR — that visible pin edit is the audit trail.
    4. every ledger identity still matches a live finding — a stale entry
       means debt moved/renamed without pruning; the CI scanner job fails on
       stale entries too, this keeps the unit lane honest as well.
    """
    counts = _ledger_counts()
    seed_counts = {(p, f, fn): c for p, f, fn, c in KNOWN_DEBT_FROZEN_SEED}
    ledger_ids = set(counts)
    assert ledger_ids <= set(seed_counts), (
        "ledger contains identities outside the frozen seed (substitution or "
        f"enrollment attempt): {sorted(ledger_ids - set(seed_counts))}"
    )
    raised = {
        identity: (counts[identity], seed_counts[identity])
        for identity in ledger_ids
        if counts[identity] > seed_counts[identity]
    }
    assert not raised, f"ledger counts exceed the frozen seed (enrollment attempt): {raised}"
    total = sum(counts.values())
    assert total == KNOWN_DEBT_FINDING_TOTAL, (
        f"known-debt finding total {total} != pin {KNOWN_DEBT_FINDING_TOTAL}; "
        "legitimate shrink: prune the ledger AND lower this pin in the same PR; "
        "growth is never allowed (fix the finding instead)"
    )

    spec = importlib.util.spec_from_file_location(
        "scan_fp", ROOT / "scripts" / "scan_test_false_positives.py"
    )
    assert spec and spec.loader
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    live = scanner._current_counts(scanner.scan_tests(ROOT / "tests"), ROOT)
    assert all(counts[identity] <= live.get(identity, 0) for identity in ledger_ids), (
        f"stale ledger entries (debt moved/fixed): {sorted(ledger_ids - set(live))}"
    )


def test_every_selectable_pr_suite_has_a_workflow_consumer():
    """#3186 Phase A req 4: no selectable suite may be a workflow orphan.

    For every suite the selector can pick (pr_suites), the ci.yml `changes`
    job must expose its output key, and some job must actually execute the
    suite, via either arm:
      (i) selection-gated: a job/step `if` references outputs.<key> and the
          job runs `ci.py run <suite>` (matrix entries resolve to their
          `ci.py run ${{ matrix.suite }}` job); or
      (ii) always-run: a step runs `ci.py run <suite>` with no `if` gating on
          that suite's output (e.g. `package` inside `build`).
    A registered-but-never-executed suite (the pre-#3186 false-positive-scan
    situation) fails here.
    """
    import json

    pr_suites = json.loads((ROOT / "ci" / "suites.json").read_text())["pr_suites"]
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())

    changes_outputs = set(workflow["jobs"]["changes"]["outputs"])
    # Every suite name's snake_case key must be exposed by the changes job.
    for suite in pr_suites:
        key = suite.replace("-", "_")
        assert key in changes_outputs, f"suite {suite}: changes job does not expose {key}"

    def job_text(job: dict) -> str:
        import json as _json

        return _json.dumps(job)

    import re as _re

    for suite in sorted(pr_suites):
        key = suite.replace("-", "_")
        executed = False
        for job in workflow["jobs"].values():
            text = job_text(job)
            # Word-boundary match so a suite that prefixes another cannot
            # cross-match ("python-min" vs a hypothetical "python-minimal").
            runs_suite = _re.search(rf"ci\.py run {_re.escape(suite)}\b", text) is not None
            in_matrix = "${{ matrix.suite }}" in text and any(
                inc.get("suite") == suite
                for inc in job.get("strategy", {}).get("matrix", {}).get("include", [])
            )
            if not runs_suite and not in_matrix:
                continue
            gated = f"outputs.{key}" in text
            step_gated = any(f"outputs.{key}" in str(s.get("if", "")) for s in job.get("steps", []))
            job_gated = f"outputs.{key}" in str(job.get("if", ""))
            if gated or job_gated or step_gated:
                executed = True  # arm (i): selection-gated execution
            elif not in_matrix:
                executed = True  # arm (ii): always-run execution (e.g. package in build)
            # A matrix lane counts ONLY when the output key gates the job
            # somewhere (job- or step-level `if`) — an ungated matrix lane is
            # fail-closed: not counted here.
        assert executed, (
            f"suite {suite} is selectable but no ci.yml job executes it through "
            "either the selection-gated or always-run arm (orphan suite)"
        )


def test_false_positive_scan_lane_is_gate_consumed():
    """#3186 Phase A req 3: the scanner lane's result must feed PR Gate.

    Executing is not enough — an advisory job would pass the consumer test
    above. The job that runs `ci.py run false-positive-scan` must be in
    pr-gate's `needs` AND its result variable must appear in the validation
    snippet, so a red scanner blocks merge.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    scan_job = next(
        name
        for name, job in workflow["jobs"].items()
        if "ci.py run false-positive-scan" in str(job)
    )
    gate = workflow["jobs"]["pr-gate"]
    assert scan_job in gate["needs"], (
        f"job {scan_job} executes the scanner but is absent from pr-gate needs"
    )
    validate_step = next(s for s in gate["steps"] if "Validate required" in str(s.get("name", "")))
    snippet = str(validate_step["run"])
    assert "SCAN" in validate_step["env"], "pr-gate must map the scanner result to SCAN"
    assert '"SCAN"' in snippet, "the pr-gate validation dict must consume SCAN"


def test_workflow_suite_references_resolve():
    """Every `ci.py run <name>` and changes output key in ci.yml is a real suite."""
    import json
    import re

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    run_texts = [
        str(step.get("run", ""))
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]
    for run_text in run_texts:
        for name in re.findall(r"ci\.py run ([a-z][a-z0-9-]*[a-z0-9])", run_text):
            assert name in suites, f"ci.yml runs unknown suite {name!r}"
    for key in workflow["jobs"]["changes"]["outputs"]:
        if key == "selected":
            continue
        assert key.replace("_", "-") in suites, f"changes output {key!r} has no suite"


def test_false_positive_scan_suite_command_shape():
    """The scanner suite must scan the whole tests/ tree against the ledger.

    Narrowing the scan via suites.json (--exclude-dirs/--pattern/another
    target) is the only remaining way to hide new debt without touching the
    ledger; this pin makes any such narrowing a visible contract change.
    """
    import json

    suites = json.loads((ROOT / "ci" / "suites.json").read_text())["suites"]
    assert suites["false-positive-scan"]["commands"] == [
        [
            "{python}",
            "scripts/scan_test_false_positives.py",
            "tests",
            "--ledger",
            "ci/false-positive-ledger.json",
        ]
    ], suites["false-positive-scan"]["commands"]
