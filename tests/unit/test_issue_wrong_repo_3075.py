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
1. Regex matches github.com, GHES, and SSH URLs (drives the product's own
   `_ISSUE_REPO_SLUG_RE`, not a copy).
2. Fallback to gh.get_repo_name() when the regex fails (drives the real
   _do_preparation fallback, end to end).
3. Unresolvable issue_repo raises the guard error (drives the real guard).
4. End-to-end _do_preparation: create_issue called with correct repo parameter.
5. End-to-end _do_preparation: GHES repo URL still targets the correct repo.

#3296 converted the mock-self fallback tests into real _do_preparation drivers
and removed the ErrorGuard replays (tests that re-implemented the guard
condition and raised on their own; both arms are covered by the real drivers
above).
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import _ISSUE_REPO_SLUG_RE
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps


def _setup_orchestrator(wf, mock_gh):
    """Create a partially-mocked orchestrator driving the real _do_preparation."""
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
    """The product's owner/repo extraction regex supports all URL formats.

    Drives the real ``_ISSUE_REPO_SLUG_RE`` used by ``_do_preparation`` for
    issue targeting (imported, not copied — #3296: an inline copy of the
    pattern was immune to product drift).
    """

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
            # A host-only URL carries no owner/repo tail: the slug stays None
            # and _do_preparation falls back to gh.get_repo_name().
            ("https://example.com", None),
            ("123", None),
            ("", None),
        ],
    )
    def test_regex_extracts_owner_repo(self, url, expected):
        """Verify the regex extracts owner/repo from all common URL formats."""
        match = _ISSUE_REPO_SLUG_RE.search(url)
        result = match.group(1) if match else None
        assert result == expected


# ── Fallback tests ──────────────────────────────────────────────────


class TestIssueRepoFallback:
    """Fallback to gh.get_repo_name() when the regex fails.

    Both tests drive the real ``_do_preparation`` on a re-entry workflow whose
    checkpointed ``project_repo_url`` is a host-only URL: it passes the
    resolved-URL re-entry gate but yields no slug from the product regex, so
    the fallback at orchestrator.py (_do_preparation) must fire. (#3296: the
    previous tests asserted a mock's return value against itself.)
    """

    def _make_reentry_workflow(self, tmp_path):
        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-3075-fallback",
                "user_id": 1,
                "project_repo_url": "https://example.com",  # Resolved URL, no slug
                "project_path": str(tmp_path),  # Real (empty) directory
                "branch_strategy": "current",
                "github_issue_number": None,  # Force the issue-creation block
            }
        )
        return wf

    def _make_boundary_gh(self):
        gh = MagicMock(name="gh")
        gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._needs_sudo.return_value = False
        gh.get_current_branch.return_value = "main"
        gh.get_repo_url.return_value = "https://example.com"
        return gh

    def _run_preparation(self, wf, gh):
        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
        ):
            mock_user_repo_cls.return_value.get_user_by_id.return_value = {
                "system_account": "alice"
            }
            orch, repo = _setup_orchestrator(wf, gh)
            ctx = WorkflowContext(
                workflow=wf,
                definition_snapshot=None,
                repository_context=None,
                session_bindings={},
                cancellation=threading.Event(),
            )
            deps = PhaseDeps(
                host=orch,
                gh=gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )
            result = orch._do_preparation(ctx, deps)
        return orch, result

    def test_fallback_to_get_repo_name(self, tmp_path):
        """When the regex doesn't match, gh.get_repo_name() provides the slug."""
        wf = self._make_reentry_workflow(tmp_path)
        gh = self._make_boundary_gh()
        gh.get_repo_name.return_value = "acme/resolved"
        gh.create_issue.return_value = {
            "number": 9,
            "url": "https://example.com/issues/9",
        }

        orch, result = self._run_preparation(wf, gh)

        # The real fallback resolved the repo from the local remote and the
        # issue was created in that repo (not the cwd-inferred one).
        gh.get_repo_name.assert_called()
        gh.create_issue.assert_called_once()
        assert gh.create_issue.call_args.kwargs.get("repo") == "acme/resolved"
        assert result.next_phase == "planning"

    def test_fallback_returns_empty_string(self, tmp_path):
        """An empty get_repo_name() answer hits the unresolved-repo guard."""
        wf = self._make_reentry_workflow(tmp_path)
        gh = self._make_boundary_gh()
        gh.get_repo_name.return_value = ""

        with pytest.raises(GitHubOpsError, match="Cannot determine target repository"):
            self._run_preparation(wf, gh)

        # The fallback DID fire (unlike the empty-repo_url guard test) but
        # its empty answer must not fall through to a cwd-inferred repo.
        gh.get_repo_name.assert_called()
        gh.create_issue.assert_not_called()

    def test_owner_repo_resolution_returns_none_without_remote(self, tmp_path):
        """No-origin resolution: GitHubOps catches the REAL git failure.

        On a git-init'd repo without an origin remote, get_repo_url() raises
        GitHubOpsError from the real `git remote get-url origin` failure;
        _resolve_owner_repo() catches it and returns None (the brand-new
        project pre-create_repo path), caching the negative result so the
        second call does not re-run git. Previously this test replayed a
        mock against itself (#3186 batch 5).
        """
        import subprocess as _sp

        from app.modules.workspace.autonomous.github_ops import GitHubOps

        repo = tmp_path / "repo-no-origin"
        repo.mkdir()
        _sp.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        ops = GitHubOps(str(repo))

        with pytest.raises(GitHubOpsError):
            ops.get_repo_url()

        assert ops._owner_repo is None and ops._owner_repo_resolved is False
        assert ops._resolve_owner_repo() is None
        assert ops._owner_repo_resolved is True
        # Cached negative resolution: no second git invocation needed.
        assert ops._resolve_owner_repo() is None


# ── End-to-end _do_preparation tests ──────────────────────────────


class TestPreparationIssueCreation:
    """End-to-end tests for _do_preparation issue creation path."""

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
        mock_gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh._needs_sudo.return_value = False  # Single-user mode, no chown needed
        mock_gh.get_repo_url.return_value = repo_url
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = _setup_orchestrator(wf, mock_gh)
        # Plain MagicMock (no wf-mutating side_effect): the post-run wf sync
        # assertions below can then only be satisfied by the product's own
        # in-memory sync lines in _do_preparation (#2963 测试点1, #3296).
        orch._update_workflow = MagicMock()

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
        assert (
            call_kwargs.kwargs.get("repo") == "blueberry521/123"
        ), f"Expected repo='blueberry521/123', got repo={call_kwargs.kwargs.get('repo')!r}"
        assert result.next_phase == "planning"
        # The in-memory wf must be synced with the resolved URL and the local
        # project path right after create_repo/clone (product sync lines), so
        # later phases and re-entries never observe the stale input name.
        assert wf["project_repo_url"] == repo_url
        assert wf["project_path"] == str(tmp_path)
        orch._update_workflow.assert_any_call({"project_repo_url": repo_url})
        orch._update_workflow.assert_any_call({"project_path": str(tmp_path)})

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
        mock_gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh._needs_sudo.return_value = False  # Single-user mode, no chown needed
        mock_gh.get_repo_url.return_value = repo_url
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = _setup_orchestrator(wf, mock_gh)

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

            orch._do_preparation(ctx, deps)

        # Verify create_issue was called with the correct GHES repo
        mock_gh.create_issue.assert_called_once()
        call_kwargs = mock_gh.create_issue.call_args
        assert (
            call_kwargs.kwargs.get("repo") == "owner/my-ghes-project"
        ), f"Expected repo='owner/my-ghes-project', got repo={call_kwargs.kwargs.get('repo')!r}"

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
        mock_gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh._needs_sudo.return_value = False  # Single-user mode, no chown needed
        mock_gh.get_repo_url.return_value = ""
        mock_gh.get_repo_name.return_value = ""
        mock_gh.get_current_branch.return_value = "main"

        orch, repo = _setup_orchestrator(wf, mock_gh)

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


class TestIssue3199ExistingProject:
    """Test Issue #3199: existing project without project_repo_url should resolve from local git remote."""

    def _make_existing_project_workflow(self, tmp_path):
        """Create a test workflow dict for existing project scenario."""
        return {
            "id": "test-workflow-id-3199",
            "workflow_id": "wf-3199-test",
            "current_phase": "preparation",
            "status": "preparing",
            "is_new_project": False,  # Key: existing project, not new
            "branch_strategy": "new-branch",
            "requirements_text": "Add a README file",
            "github_issue_number": None,
            "project_repo_url": "",  # Empty: user didn't specify a repo URL
            "project_path": str(tmp_path),
            "title": "Test Existing Project",
        }

    def test_existing_project_resolves_repo_from_local_remote(self, tmp_path):
        """Verify existing project resolves owner/repo from local git remote.

        Issue #3199: User selected an existing Git project directory that has a
        GitHub origin remote. The issue_repo should be resolved from the local
        git remote, not from project_repo_url (which is empty for existing projects).
        """
        wf = self._make_existing_project_workflow(tmp_path)
        wf.update(
            {
                "user_id": 1,
            }
        )

        mock_gh = MagicMock(name="gh")
        # get_repo_name() returns the owner/repo from local git remote
        mock_gh.get_repo_name.return_value = "user/existing-project"
        mock_gh.create_issue.return_value = {
            "number": 123,
            "url": "https://github.com/user/existing-project/issues/123",
        }
        mock_gh.get_current_branch.return_value = "main"
        mock_gh.list_worktrees.return_value = []

        # Mock _run_git with a function that handles different commands
        def mock_run_git(args, check=True, **kwargs):
            stdout = ""
            rc = 0
            if "fetch" in args:
                stdout = ""
                rc = 0
            elif "show-ref" in args:
                # Return success for refs/remotes/origin/main
                if "origin/main" in args:
                    stdout = "refs/remotes/origin/main"
                    rc = 0
                else:
                    rc = 1
            elif "rev-parse" in args:
                stdout = "abc123"  # Commit SHA
            elif "branch" in args and "-a" in args:
                stdout = "  main\n  remotes/origin/main"
            return MagicMock(returncode=rc, stdout=stdout, stderr="")

        mock_gh._run_git.side_effect = mock_run_git

        orch, repo = _setup_orchestrator(wf, mock_gh)

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=mock_gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
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

        # Verify get_repo_name was called to resolve owner/repo from local remote
        mock_gh.get_repo_name.assert_called()

        # Verify create_issue was called with the correct repo
        mock_gh.create_issue.assert_called_once()
        call_kwargs = mock_gh.create_issue.call_args
        assert (
            call_kwargs.kwargs.get("repo") == "user/existing-project"
        ), f"Expected repo='user/existing-project', got repo={call_kwargs.kwargs.get('repo')!r}"
        assert result.next_phase == "planning"

    def test_existing_project_no_remote_raises_clear_error(self, tmp_path):
        """Verify clear error when existing project has no GitHub remote configured.

        Issue #3199: When the user selects a project directory that has no GitHub
        origin remote, the error message should guide the user to configure one.
        """
        wf = self._make_existing_project_workflow(tmp_path)
        wf.update(
            {
                "user_id": 1,
            }
        )

        mock_gh = MagicMock(name="gh")
        # get_repo_name() returns empty string (no remote configured)
        mock_gh.get_repo_name.return_value = ""
        mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh.get_current_branch.return_value = "main"
        mock_gh.list_worktrees.return_value = []

        orch, repo = _setup_orchestrator(wf, mock_gh)

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                return_value=mock_gh,
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
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

            # Should raise clear error about missing GitHub remote
            with pytest.raises(GitHubOpsError, match="Cannot determine target repository"):
                orch._do_preparation(ctx, deps)

            # Verify create_issue was NOT called
            mock_gh.create_issue.assert_not_called()
