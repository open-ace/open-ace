"""Tests for git_push transient error handling (Issue #1814).

This module tests the fix for two Medium-severity findings from PR #1742 review:

1. Pre-PR push wraps GitHubOpsError in plain RuntimeError, defeating Layer-2
   transient retry (reliability issue).

2. Review-fix push swallows exception, marks milestone completed, and posts
   misleading 'Addressed Review Feedback' comment referencing local-only SHA
   (correctness bug).

3. CI repair push captures exception but doesn't propagate, preventing Layer-2
   retry for transient errors.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(**overrides):
    """Create a minimal workflow dict for testing."""
    base = {
        "workflow_id": "test-wf-uuid",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "pending",
        "requirements_text": "Build a simple feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/test-project",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/test",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "preferred_worktree_path": "",
        "github_issue_number": 1234,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "transient_retry_count": 0,
    }
    base.update(overrides)
    return base


def _make_agent_result(success=True, text="Done", tokens=100, error=None):
    """Create a mock agent result."""
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=tokens,
        total_input_tokens=tokens // 2,
        total_output_tokens=tokens // 2,
        success=success,
        error=error,
    )


class TestIsTransientGitError:
    """Tests for the _is_transient_git_error helper function."""

    def test_ssl_error_is_transient(self):
        """SSL errors should be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: SSL certificate problem")
        assert _is_transient_git_error(e) is True

    def test_connection_reset_is_transient(self):
        """Connection reset errors should be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: Connection reset by peer")
        assert _is_transient_git_error(e) is True

    def test_timeout_is_transient(self):
        """Timeout errors should be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: Connection timed out")
        assert _is_transient_git_error(e) is True

    def test_rpc_failed_is_transient(self):
        """RPC failed errors should be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: RPC failed; HTTP 429")
        assert _is_transient_git_error(e) is True

    def test_auth_error_is_not_transient(self):
        """Authentication errors should NOT be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: Permission denied (publickey)")
        assert _is_transient_git_error(e) is False

    def test_runtime_error_is_not_transient(self):
        """Non-GitHubOpsError exceptions should NOT be identified as transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = RuntimeError("git push failed: SSL error")
        assert _is_transient_git_error(e) is False


class TestCiRepairPushTransientRetry:
    """Tests for CI repair push transient error handling (Issue #1814, Finding 3)."""

    def test_transient_push_propagates_for_retry(self):
        """Transient CI repair push failure should propagate for Layer-2 retry."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        # Test the static method directly
        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "sha-new"
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_push.side_effect = GitHubOpsError("git push failed: Connection timed out")

        with pytest.raises(GitHubOpsError) as exc_info:
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh=mock_gh,
                commit_before="sha-old",
                attempt=1,
                branch_name="auto-dev/test",
                pr_number=42,
            )

        assert "timed out" in str(exc_info.value).lower()

    def test_non_transient_push_returns_push_error(self):
        """Non-transient CI repair push failure should return push_error."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "sha-new"
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_push.side_effect = GitHubOpsError("git push failed: Permission denied")

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh=mock_gh,
                commit_before="sha-old",
                attempt=1,
                branch_name="auto-dev/test",
                pr_number=42,
            )
        )

        assert sha_changed is True
        assert "Permission denied" in push_error

    def test_success_returns_empty_push_error(self):
        """Successful CI repair push should return empty push_error."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "sha-new"
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_push.return_value = None  # Success

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh=mock_gh,
                commit_before="sha-old",
                attempt=1,
                branch_name="auto-dev/test",
                pr_number=42,
            )
        )

        assert sha_changed is True
        assert push_error == ""

    def test_non_transient_push_with_ssl_keyword_propagates(self):
        """Push with SSL keyword should be treated as transient and propagate."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "sha-new"
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_push.side_effect = GitHubOpsError("git push failed: SSL certificate problem")

        with pytest.raises(GitHubOpsError):
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh=mock_gh,
                commit_before="sha-old",
                attempt=1,
                branch_name="auto-dev/test",
                pr_number=42,
            )

    def test_network_unreachable_is_transient(self):
        """Network unreachable should be treated as transient."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "sha-new"
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_push.side_effect = GitHubOpsError("git push failed: Network is unreachable")

        with pytest.raises(GitHubOpsError):
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh=mock_gh,
                commit_before="sha-old",
                attempt=1,
                branch_name="auto-dev/test",
                pr_number=42,
            )


class TestCiRepairPushExistingTests:
    """Tests to verify existing test behavior is preserved."""

    def test_pushes_unpushed_commit_even_without_new_changes(self):
        """Existing test from test_ci_repair_fingerprint.py should still pass."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        gh = MagicMock()
        gh.get_current_commit.return_value = "sha-B"  # local has unpushed commit
        gh.has_uncommitted_changes.return_value = False
        gh.git_push.return_value = None

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh,
                commit_before="sha-A",  # PR remote head (behind local)
                attempt=1,
                branch_name="auto-dev/x",
                pr_number=1812,
            )
        )
        assert sha_changed is True
        assert commit_sha == "sha-B"
        assert push_error == ""
        gh.git_push.assert_called_once_with(branch="auto-dev/x", force_with_lease=True)

    def test_no_changes_when_remote_equals_local(self):
        """Existing test from test_ci_repair_fingerprint.py should still pass."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        gh = MagicMock()
        gh.get_current_commit.return_value = "sha-A"  # same as remote
        gh.has_uncommitted_changes.return_value = False

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh, "sha-A", 1, "auto-dev/x", 1812
            )
        )
        assert sha_changed is False
        assert push_error == ""
        gh.git_push.assert_not_called()

    def test_auto_commits_uncommitted_changes(self):
        """Existing test from test_ci_repair_fingerprint.py should still pass."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        gh = MagicMock()
        gh.get_current_commit.return_value = "sha-A"  # initial: same as remote
        gh.has_uncommitted_changes.return_value = True
        # After auto-commit, HEAD advances
        gh.git_commit.side_effect = lambda *a, **kw: setattr(
            gh, "get_current_commit", MagicMock(return_value="sha-C")
        )

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh, "sha-A", 1, "auto-dev/x", 1812
            )
        )
        assert sha_changed is True
        gh.git_add_all.assert_called_once()
        gh.git_push.assert_called_once()

    def test_push_error_captured_not_raised(self):
        """Existing test: non-transient push error should be captured in push_error."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        gh = MagicMock()
        gh.get_current_commit.return_value = "sha-B"
        gh.has_uncommitted_changes.return_value = False
        # Non-transient error (no transient keywords)
        gh.git_push.side_effect = Exception("permission denied")

        commit_sha, sha_changed, push_error = (
            AutonomousOrchestrator._detect_and_push_ci_repair_changes(
                gh, "sha-A", 1, "auto-dev/x", 1812
            )
        )
        assert sha_changed is True
        assert "permission denied" in push_error


class TestTransientKeywordsCoverage:
    """Tests to ensure all transient keywords are covered."""

    def test_libressl_is_transient(self):
        """LibreSSL errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: LibreSSL SSL_read")
        assert _is_transient_git_error(e) is True

    def test_tls_is_transient(self):
        """TLS errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: TLS connection error")
        assert _is_transient_git_error(e) is True

    def test_connection_refused_is_transient(self):
        """Connection refused errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: Connection refused")
        assert _is_transient_git_error(e) is True

    def test_could_not_resolve_host_is_transient(self):
        """DNS resolution errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: Could not resolve host github.com")
        assert _is_transient_git_error(e) is True

    def test_unable_to_access_is_transient(self):
        """Unable to access errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: unable to access")
        assert _is_transient_git_error(e) is True

    def test_early_eof_is_transient(self):
        """Early EOF errors should be transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: early EOF")
        assert _is_transient_git_error(e) is True

    def test_force_with_lease_stale_info_is_transient(self):
        """--force-with-lease stale-info rejection is a recoverable race."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError(
            "git push origin auto-dev/3c5aefb9 --force-with-lease failed (exit 1): "
            "To https://github.com/open-ace/open-ace "
            "! [rejected] auto-dev/3c5aefb9 -> auto-dev/3c5aefb9 (stale info) "
            "错误：无法推送一些引用"
        )
        assert _is_transient_git_error(e) is True

    def test_fetch_first_is_transient(self):
        """git 'fetch first' rejection is transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: ! [rejected] main -> main (fetch first)")
        assert _is_transient_git_error(e) is True

    def test_non_fast_forward_is_transient(self):
        """non-fast-forward rejection is transient."""
        from app.modules.workspace.autonomous.orchestrator import _is_transient_git_error

        e = GitHubOpsError("git push failed: ! [rejected] (non-fast-forward)")
        assert _is_transient_git_error(e) is True
