"""#2699: lazy dependency preparation for agent worktrees.

The reverted node_modules shim (#2694/#2698) left the cross-user EACCES trap
in place: a clean worktree has no ``node_modules`` (gitignored), Node
resolution walks up into the project owner's main clone — deps load (reads
work) but vite's cache write into the owner-owned ``node_modules/.vite-temp``
is denied. To the agent that reads like a permissions bug, not missing deps,
so it retries around permissions instead of installing.

These tests lock the two replacement layers:

1. Proactive rule in the test-phase strategy prompt (check node_modules +
   lockfile-detected install before running frontend commands).
2. Failure-signature translation: the prior round's report hitting the
   missing-deps signature injects an explicit "install deps first" directive
   into (a) the fresh-retry prompt and (b) the #2590 dev-repair feedback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    _DEPENDENCY_PREP_RULE,
    _dependency_failure_directive,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_PY = PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "orchestrator.py"

pytestmark = [pytest.mark.issue(2699), pytest.mark.regression]


class TestDependencyFailureDirective:
    def test_matches_vite_temp_eacces(self):
        out = (
            "Error: EACCES: permission denied, open "
            "'/home/qlfan/repo/frontend/node_modules/.vite-temp/config.json'"
        )
        directive = _dependency_failure_directive(out)
        assert directive, "EACCES on node_modules/.vite-temp must trigger the directive"
        assert "npm ci" in directive
        assert "pnpm install --frozen-lockfile" in directive

    def test_matches_cannot_find_module_vitest(self):
        out = "node:internal/modules/cjs/loader: Error: Cannot find module 'vitest'"
        assert _dependency_failure_directive(out)

    def test_matches_windows_style_path(self):
        out = r"Error: EACCES: permission denied 'C:\repo\frontend\node_modules\.vite-temp\x'"
        assert _dependency_failure_directive(out)

    def test_normal_pytest_failure_no_directive(self):
        out = "FAILED tests/test_app.py::test_login - AssertionError: expected 200"
        assert _dependency_failure_directive(out) == ""

    def test_empty_output_no_directive(self):
        assert _dependency_failure_directive("") == ""
        assert _dependency_failure_directive(None) == ""  # type: ignore[arg-type]

    def test_directive_is_actionable_not_permission_chasing(self):
        """The directive must redirect the agent away from permission
        workarounds (sudo/chmod) toward installing deps in the worktree."""
        directive = _dependency_failure_directive(
            "EACCES: permission denied, open '/r/frontend/node_modules/.vite-temp/c'"
        )
        assert "worktree" in directive
        assert "重新执行" in directive


class TestProactiveRule:
    def test_rule_covers_all_four_lockfiles(self):
        for cmd in (
            "npm ci",
            "pnpm install --frozen-lockfile",
            "yarn install --frozen-lockfile",
            "bun install",
        ):
            assert cmd in _DEPENDENCY_PREP_RULE, cmd

    def test_rule_scopes_to_frontend_commands(self):
        """Pure-backend tasks must stay zero-cost — the rule must tell the
        agent it only applies before frontend/JS commands."""
        assert "前端" in _DEPENDENCY_PREP_RULE
        assert "node_modules" in _DEPENDENCY_PREP_RULE

    @pytest.mark.issue(2699)
    def test_rule_wired_into_test_prompt(self):
        """Drift lock: the test-phase strategy prompt must reference the rule
        constant (mirroring the #2635/#2674 source-grep locks)."""
        text = ORCHESTRATOR_PY.read_text()
        assert "_DEPENDENCY_PREP_RULE" in text, (
            "orchestrator no longer references _DEPENDENCY_PREP_RULE; the "
            "proactive dependency rule must stay wired into the test-phase "
            "strategy prompt (Issue #2699)"
        )

    def test_directive_wired_into_retry_and_repair_paths(self):
        """Drift lock: the signature translation must be consulted at both
        delivery sites — the fresh-retry prompt (prior milestone summary) and
        the #2590 dev-repair feedback."""
        text = ORCHESTRATOR_PY.read_text()
        assert text.count("_dependency_failure_directive") >= 3, (
            "expected _dependency_failure_directive at: definition + retry "
            "prompt site + dev-repair feedback site (Issue #2699)"
        )


class TestPriorSummaryDirective:
    def test_orchestrator_helper_reads_prior_tests_run_summary(self):
        """The retry-path helper must read the PRIOR tests_run milestone's
        result_summary (excluding the current milestone) — same milestone
        selection as _recompute_prior_test_milestone_verdict."""
        text = ORCHESTRATOR_PY.read_text()
        assert "_prior_dependency_failure_directive" in text
        # Exclusion of the current milestone is load-bearing: without it the
        # just-created in_progress tests_run milestone (empty summary) could
        # shadow the prior round's report.
        assert "!= current_milestone_id" in text or "!= current_milestone_id" in text
