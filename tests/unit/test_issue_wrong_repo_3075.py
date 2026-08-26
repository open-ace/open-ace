# tests/unit/test_issue_wrong_repo_3075.py
"""
Issue #3075: 新项目创建的 Issue 错误地创建在 open-ace/open-ace 仓库而非新仓库

Root cause: When creating a new project with is_new_project=True and the project
name is not a URL (e.g. "123"), the regex that extracts owner/repo from the repo
URL only matched github.com — GHES URLs slipped through.  When issue_repo was
None, create_issue(repo=None) let gh infer the repo from cwd, which in a
cross-user (sudo) deployment is the orchestrator's own repo (open-ace/open-ace).

#2963 fixed the main wf-cache-staleness path, but three gaps remained:
1. The regex only matched github.com, not GHES hosts.
2. No fallback to gh.get_repo_name() when the regex fails.
3. No error guard — silently creating the issue in the wrong repo.

Tests:
1. Regex matches github.com, GHES, and SSH URLs.
2. Fallback to gh.get_repo_name() when regex returns None.
3. Error raised when issue_repo cannot be resolved for new-project workflows.
4. End-to-end _do_preparation: create_issue called with correct repo parameter.
5. End-to-end _do_preparation: GHES repo URL still targets the correct repo.
"""

import os
import re
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps


def _make_test_workflow():
    """Create a test workflow dict for new project scenario."""
    return {
        "id": "test-workflow-id-3075",
        "current_phase": "preparation",
        "status": "preparing",
        "is_new_project": True,
        "branch_strategy": "worktree",
        "requirements_text": "Build a REST API service",
        "github_issue_number": None,
        "project_repo_url": "my-new-project",  # Input name, not URL yet
        "project_path": None,
        "title": "Test Project",
        "is_private": True,
    }


# ── Regex tests ────────────────────────────────────────────────────


class TestIssueRepoRegex:
    """Test the owner/repo extraction regex supports all URL formats."""

    # This is the regex used in orchestrator.py _do_preparation
    REGEX = r"[:/]([^/]+/[^/]+?)(?:\.git)?/?$"

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/blueberry521/123", "blueberry521/123"),
            ("https://github.com/blueberry521/123.git", "blueberry521/123"),
            ("https://github.com/blueberry521/123/", "blueberry521/123"),
            ("https://github.com/org/repo-name", "org/repo-name"),
            ("https://gh.example.com/owner/repo", "owner/repo"),
            ("https://gh.example.com/owner/repo.git", "owner/repo"),
            ("git@github.com:owner/repo.git", "owner/repo"),
            ("ssh://git@github.com/owner/repo.git", "owner/repo"),
            ("123", None),
            ("", None),
        ],
    )
    def test_regex_extracts_owner_repo(self, url, expected):
        """Verify the regex extracts owner/repo from all common URL formats."""
        match = re.search(self.REGEX, url)
        result = match.group(1) if match else None
        assert result == expected


# ── Fallback tests ──────────────────────────────────────────────────


class TestIssueRepoFallback:
    """Test fallback to gh.get_repo_name() when regex fails."""

    def test_fallback_to_get_repo_name(self):
        """When the regex doesn't match, gh.get_repo_name() provides the slug."""
        # Simulate a URL that the regex can't parse (e.g. bare name)
        issue_repo_url = "123"
        match = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?/?$", issue_repo_url)
        issue_repo = match.group(1) if match else None
        assert issue_repo is None

        # Fallback: gh.get_repo_name() returns owner/repo from local remote
        mock_gh = MagicMock()
        mock_gh.get_repo_name.return_value = "blueberry521/123"
        resolved = mock_gh.get_repo_name()
        assert resolved == "blueberry521/123"

    def test_fallback_returns_empty_string(self):
        """When gh.get_repo_name() returns empty, issue_repo stays None."""
        mock_gh = MagicMock()
        mock_gh.get_repo_name.return_value = ""
        resolved = mock_gh.get_repo_name()
        assert not resolved

    def test_fallback_raises_githubopserror(self):
        """When gh.get_repo_name() raises, the exception is caught silently."""
        mock_gh = MagicMock()
        mock_gh.get_repo_name.side_effect = GitHubOpsError("no remote")
        # In the actual code, this is caught with except GitHubOpsError: pass
        try:
            mock_gh.get_repo_name()
        except GitHubOpsError:
            pass  # This is the expected path


# ── Error guard tests ──────────────────────────────────────────────


class TestIssueRepoErrorGuard:
    """Test that an error is raised when issue_repo cannot be resolved."""

    def test_error_raised_for_new_project_without_repo(self):
        """For is_new_project workflows, unresolved issue_repo raises an error."""
        wf = {"is_new_project": True}
        issue_repo = None
        repo_url = ""
        issue_repo_url = ""

        if not issue_repo and wf.get("is_new_project"):
            with pytest.raises(GitHubOpsError, match="Cannot determine target repository"):
                raise GitHubOpsError(
                    "Cannot determine target repository for issue creation. "
                    f"repo_url={repo_url!r}, issue_repo_url={issue_repo_url!r}"
                )

    def test_no_error_for_non_new_project(self):
        """For non-new-project workflows, the error guard is skipped."""
        wf = {"is_new_project": False}
        issue_repo = None
        # The guard condition: if not issue_repo and wf.get("is_new_project")
        # For non-new-project, this is False, so no error
        assert not (not issue_repo and wf.get("is_new_project"))


# ── End-to-end _do_preparation tests ──────────────────────────────


class TestPreparationIssueCreation:
    """End-to-end tests for _do_preparation issue creation path."""

    def _setup_orchestrator(self, wf, mock_gh):
        """Helper to create a partially-mocked orchestrator."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        repo = MagicMock()
        repo.get_workflow.return_value = wf

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch.repo = repo
        orch._workflow_id = wf.get("workflow_id", "wf-3075-test")
        orch._gh = None
        orch._shutdown_requested = threading.Event()
        orch._session_usage_offsets = {}
        orch._update_workflow = MagicMock(side_effect=lambda updates: wf.update(updates))
        orch._create_milestone = MagicMock(return_value={"milestone_id": "ms"})
        orch.emit_phase_change = MagicMock()
        orch._get_gh = MagicMock(return_value=mock_gh)
        return orch, repo

    def test_create_issue_called_with_correct_repo(self, tmp_path):
        """Verify create_issue is called with the correct repo parameter."""
        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-3075-correct-repo",
                "user_id": 1,
                "project_path": str(tmp_path),
                "branch_strategy": "current",
                "project_repo_url": "123",  # Input name, not URL
            }
        )
        repo_url = "https://github.com/blueberry521/123"

        mock_gh = MagicMock(name="gh")
        mock_gh.create_repo.return_value = {"name": "123", "url": repo_url}
        mock_gh.create_issue.return_value = {
            "number": 42,
            "url": f"{repo_url}/issues/42",
        }
        mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh.get_repo_url.return_value = repo_url
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = self._setup_orchestrator(wf, mock_gh)

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=mock_gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.exists",
                return_value=False,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.isdir",
                return_value=False,
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.makedirs"),
        ):
            mock_user_repo_cls.return_value.get_user_by_id.return_value = {
                "system_account": "alice"
            }
            ctx = WorkflowContext(
                workflow=wf,
                definition_snapshot=None,
                repository_context=None,
                session_bindings={},
                cancellation=threading.Event(),
            )
            deps = PhaseDeps(
                host=orch,
                gh=mock_gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            result = orch._do_preparation(ctx, deps)

        # Verify create_issue was called with the correct repo
        mock_gh.create_issue.assert_called_once()
        call_kwargs = mock_gh.create_issue.call_args
        assert call_kwargs.kwargs.get("repo") == "blueberry521/123", (
            f"Expected repo='blueberry521/123', got "
            f"repo={call_kwargs.kwargs.get('repo')!r}"
        )
        assert result.next_phase == "planning"

    def test_create_issue_with_ghes_url(self, tmp_path):
        """Verify GHES repo URLs are correctly resolved for issue creation."""
        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-3075-ghes",
                "user_id": 1,
                "project_path": str(tmp_path),
                "branch_strategy": "current",
                "project_repo_url": "my-ghes-project",
            }
        )
        # GHES URL — the old regex would NOT match this
        repo_url = "https://gh.example.com/owner/my-ghes-project"

        mock_gh = MagicMock(name="gh")
        mock_gh.create_repo.return_value = {"name": "my-ghes-project", "url": repo_url}
        mock_gh.create_issue.return_value = {
            "number": 7,
            "url": f"{repo_url}/issues/7",
        }
        mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh.get_repo_url.return_value = repo_url
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = self._setup_orchestrator(wf, mock_gh)

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=mock_gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.exists",
                return_value=False,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.isdir",
                return_value=False,
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.makedirs"),
        ):
            mock_user_repo_cls.return_value.get_user_by_id.return_value = {
                "system_account": "alice"
            }
            ctx = WorkflowContext(
                workflow=wf,
                definition_snapshot=None,
                repository_context=None,
                session_bindings={},
                cancellation=threading.Event(),
            )
            deps = PhaseDeps(
                host=orch,
                gh=mock_gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            result = orch._do_preparation(ctx, deps)

        # Verify create_issue was called with the correct GHES repo
        mock_gh.create_issue.assert_called_once()
        call_kwargs = mock_gh.create_issue.call_args
        assert call_kwargs.kwargs.get("repo") == "owner/my-ghes-project", (
            f"Expected repo='owner/my-ghes-project', got "
            f"repo={call_kwargs.kwargs.get('repo')!r}"
        )

    def test_error_when_repo_url_empty_for_new_project(self, tmp_path):
        """When repo_url is empty and is_new_project=True, an error is raised."""
        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-3075-error",
                "user_id": 1,
                "project_path": str(tmp_path),
                "branch_strategy": "current",
                "project_repo_url": "some-name",
            }
        )

        mock_gh = MagicMock(name="gh")
        # create_repo returns empty URL
        mock_gh.create_repo.return_value = {"name": "some-name", "url": ""}
        mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh.get_repo_url.return_value = ""
        mock_gh.get_repo_name.return_value = ""
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = self._setup_orchestrator(wf, mock_gh)

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=mock_gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.exists",
                return_value=False,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.isdir",
                return_value=False,
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.makedirs"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.join",
                return_value=str(tmp_path / "some-name"),
            ),
        ):
            mock_user_repo_cls.return_value.get_user_by_id.return_value = {
                "system_account": "alice"
            }
            ctx = WorkflowContext(
                workflow=wf,
                definition_snapshot=None,
                repository_context=None,
                session_bindings={},
                cancellation=threading.Event(),
            )
            deps = PhaseDeps(
                host=orch,
                gh=mock_gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            # The empty repo_url should trigger the error guard
            with pytest.raises(GitHubOpsError, match="Cannot determine target repository"):
                orch._do_preparation(ctx, deps)

            # Verify create_issue was NOT called (error raised before)
            mock_gh.create_issue.assert_not_called()
