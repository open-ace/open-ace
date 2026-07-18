"""Tests for pr_review_summary created before CI check (issue #1813).

When PR review passes but CI fails, the workflow enters the CI repair loop
and returns early. Previously the ``pr_review_summary`` milestone was created
AFTER the CI check — so when CI failed, the summary was never created, and
the frontend "PR Review Summary" button stayed permanently disabled even
after the workflow completed successfully.

The fix moves the ``pr_review_summary`` creation BEFORE the CI check.
"""


class TestPrReviewSummaryBeforeCiCheck:
    """``pr_review_summary`` must be created BEFORE the CI failure check so
    that a CI failure (which redirects to CI repair and returns) doesn't skip
    the summary milestone.

    Verified by source inspection: in ``_do_pr_review``, the
    ``pr_review_summary`` milestone creation must appear textually before the
    ``ci_failed_before_report`` milestone creation (and the ``return`` that
    follows it). A full integration test is impractical here because
    ``_do_pr_review`` requires extensive mocking of CI polling, agent runs,
    diff inspection, and JSON-serializable milestone fields."""

    def test_summary_creation_appears_before_ci_check_in_source(self):
        import inspect

        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        source = inspect.getsource(AutonomousOrchestrator._do_pr_review)
        summary_pos = source.find('"pr_review_summary"')
        ci_fail_pos = source.find('"ci_failed_before_report"')
        assert summary_pos != -1, "pr_review_summary milestone creation not found"
        assert ci_fail_pos != -1, "ci_failed_before_report milestone creation not found"
        assert summary_pos < ci_fail_pos, (
            "pr_review_summary must be created BEFORE ci_failed_before_report "
            "so CI failure doesn't skip the summary (#1813 regression)"
        )

    def test_summary_creation_not_inside_ci_failure_block(self):
        """The summary creation must NOT be inside the ``if ci_failures:``
        block that creates ci_failed_before_report — it must run
        unconditionally when review passes."""
        import inspect

        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        source = inspect.getsource(AutonomousOrchestrator._do_pr_review)
        summary_pos = source.find('"pr_review_summary"')
        # Find the "if ci_failures:" that immediately precedes
        # ci_failed_before_report (the one that returns to CI repair).
        ci_fail_milestone_pos = source.find('"ci_failed_before_report"')
        assert ci_fail_milestone_pos != -1
        # Search backwards from ci_failed_before_report to find its enclosing
        # "if ci_failures:" block.
        ci_block_start = source.rfind("if ci_failures:", 0, ci_fail_milestone_pos)
        assert ci_block_start != -1
        # The summary must appear BEFORE this CI-failure block.
        assert summary_pos < ci_block_start, (
            "pr_review_summary must be created before the `if ci_failures:` "
            "block that creates ci_failed_before_report"
        )

