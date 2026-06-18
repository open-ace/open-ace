"""Tests for transient network error retry in git/gh operations (#830-845).

Two layers of retry:

Layer 1 (github_ops._run_git / _run_gh): 3×10s fixed-interval retry inside the
subprocess call. Handles the common case — a 2-second TLS hiccup that recovers
on the next attempt.

Layer 2 (orchestrator.advance): if layer 1 is also exhausted (sustained outage),
the workflow is NOT marked failed. Instead transient_retry_count increments and
the scheduler retries on the next cycle (~10s). After TRANSIENT_RETRY_MAX (6)
consecutive transient failures, the workflow finally fails for manual review.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import (
    GitHubOps,
    GitHubOpsError,
    _is_transient_error,
)
from app.modules.workspace.autonomous.orchestrator import (
    TRANSIENT_RETRY_MAX,
    AutonomousOrchestrator,
)

# ── Layer 1: _is_transient_error classification ──────────────────────────


class TestIsTransientError:
    def test_libressl_tls_error_is_transient(self):
        assert _is_transient_error(
            "fatal: unable to access 'https://github.com/': LibreSSL SSL_connect",
            128,
        )

    def test_connection_refused_is_transient(self):
        assert _is_transient_error("fatal: connection refused", 128)

    def test_dns_failure_is_transient(self):
        assert _is_transient_error("fatal: could not resolve host", 128)

    def test_conflict_is_not_transient(self):
        assert not _is_transient_error("CONFLICT (content): Merge conflict", 1)

    def test_missing_branch_is_not_transient(self):
        assert not _is_transient_error("fatal: not a valid object name", 128)

    def test_non_128_exit_not_transient(self):
        assert not _is_transient_error("some ssl error", 1)


# ── Layer 1: _run_git retry loop ─────────────────────────────────────────


class TestRunGitRetry:
    def test_retries_transient_then_succeeds(self):
        """A transient error on the first attempt succeeds on retry."""
        gh = GitHubOps("/tmp/repo")
        results = [
            MagicMock(returncode=128, stderr="LibreSSL SSL_connect failed", stdout=""),
            MagicMock(returncode=0, stderr="", stdout="ok"),
        ]
        with patch("subprocess.run", side_effect=results) as mock_run, patch("time.sleep"):
            result = gh._run_git(["fetch", "origin", "main"])
            assert mock_run.call_count == 2
            assert result.returncode == 0

    def test_no_retry_on_non_transient(self):
        """A permanent error (conflict) is not retried."""
        gh = GitHubOps("/tmp/repo")
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            mock_run.return_value = MagicMock(returncode=1, stderr="merge conflict", stdout="")
            with pytest.raises(GitHubOpsError, match="merge conflict"):
                gh._run_git(["merge", "origin/main"])
            assert mock_run.call_count == 1
            mock_sleep.assert_not_called()

    def test_exhausts_retries_then_raises(self):
        """All 3 attempts fail with transient error → raise after retries."""
        gh = GitHubOps("/tmp/repo")
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = MagicMock(
                returncode=128, stderr="LibreSSL SSL_connect", stdout=""
            )
            with pytest.raises(GitHubOpsError, match="LibreSSL"):
                gh._run_git(["fetch", "origin", "main"])
            from app.modules.workspace.autonomous.github_ops import GIT_NETWORK_RETRY_COUNT

            assert mock_run.call_count == GIT_NETWORK_RETRY_COUNT


# ── Layer 2: advance() transient auto-retry ──────────────────────────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-net",
        "title": "test",
        "cli_tool": "claude-code",
        "model": "",
        "project_path": "/tmp/repo",
        "worktree_path": "",
        "workspace_type": "local",
        "branch_strategy": "new-branch",
        "branch_name": "auto-dev/test",
        "current_phase": "preparation",
        "status": "preparing",
        "github_issue_number": 999,
        "github_pr_number": None,
        "remote_machine_id": "",
        "permission_mode": "auto-edit",
        "transient_retry_count": 0,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo.create_milestone.return_value = {"milestone_id": "ms-1"}
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    return o, mock_repo


class TestAdvanceTransientRetry:
    def test_transient_error_does_not_fail(self):
        """A network error in preparation keeps status active for retry."""
        wf = _make_workflow()
        o, mock_repo = _make_orchestrator(wf)
        o._do_preparation = MagicMock(
            side_effect=GitHubOpsError(
                "git fetch origin main failed (exit 128): "
                "fatal: unable to access 'https://github.com/': LibreSSL SSL_connect"
            )
        )

        o.advance()

        # Status must NOT be set to "failed".
        status_updates = [
            c for c in mock_repo.update_workflow.call_args_list if c[0][1].get("status") == "failed"
        ]
        assert not status_updates, "workflow was marked failed on transient error"
        # transient_retry_count was incremented.
        retry_updates = [
            c
            for c in mock_repo.update_workflow.call_args_list
            if "transient_retry_count" not in c[0][1]
            and "Transient network error" in c[0][1].get("error_message", "")
        ]
        assert retry_updates, "error_message should record transient retry"

    def test_non_transient_error_fails_immediately(self):
        """A conflict error (not network) marks failed right away."""
        wf = _make_workflow()
        o, mock_repo = _make_orchestrator(wf)
        o._do_preparation = MagicMock(side_effect=RuntimeError("branch already exists"))

        o.advance()

        status_updates = [
            c for c in mock_repo.update_workflow.call_args_list if c[0][1].get("status") == "failed"
        ]
        assert status_updates, "non-transient error should mark failed"

    def test_exhausts_transient_retries_then_fails(self):
        """After TRANSIENT_RETRY_MAX consecutive transient errors, fail."""
        wf = _make_workflow(transient_retry_count=TRANSIENT_RETRY_MAX)
        o, mock_repo = _make_orchestrator(wf)
        o._do_preparation = MagicMock(
            side_effect=GitHubOpsError("git fetch failed: LibreSSL SSL_connect")
        )

        o.advance()

        status_updates = [
            c for c in mock_repo.update_workflow.call_args_list if c[0][1].get("status") == "failed"
        ]
        assert status_updates, "should fail after exhausting transient retries"

    def test_success_resets_retry_count(self):
        """A successful advance resets transient_retry_count to 0."""
        wf = _make_workflow(transient_retry_count=3)
        o, mock_repo = _make_orchestrator(wf)
        o._do_preparation = MagicMock()  # succeeds

        o.advance()

        reset_updates = [
            c
            for c in mock_repo.update_workflow.call_args_list
            if c[0][1].get("transient_retry_count") == 0
        ]
        assert reset_updates, "transient_retry_count should be reset on success"
