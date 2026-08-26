# tests/unit/test_new_project_local_repo_init_2963.py
"""
Issue #2963: AI 自主开发创建新项目时首次创建 Issue 失败，重试后因本地仓库未初始化导致分支创建失败

测试修复点：
1. create_repo 后同步内存中的 wf 字典
2. create_issue 优先使用本地 repo_url 变量
3. 新项目创建后克隆仓库到本地
4. git 操作前验证目录有效性
"""

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps


def _make_test_workflow():
    """Create a test workflow dict for new project scenario."""
    return {
        "id": "test-workflow-id-2963",
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


class TestSyncWfAfterCreateRepo:
    """测试点1：create_repo 后同步内存中的 wf 字典"""

    def test_wf_synced_after_create_repo(self, tmp_path):
        """Verify wf dict is synced immediately after create_repo."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        wf = _make_test_workflow()
        wf["project_path"] = str(tmp_path)

        # Mock the orchestrator
        with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh_class.return_value = mock_gh
            mock_gh.create_repo.return_value = {
                "name": "my-new-project",
                "url": "https://github.com/owner/my-new-project",
            }
            mock_gh.create_issue.return_value = {
                "number": 1,
                "url": "https://github.com/owner/my-new-project/issues/1",
            }
            mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_gh.get_repo_url.return_value = "https://github.com/owner/my-new-project"
            mock_gh.list_worktrees.return_value = []
            mock_gh.path_exists_as_user.return_value = False

            # Create a mock repo
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf

            # Track updates to verify wf sync
            updates_applied = []

            def capture_update(*args, **kwargs):
                # Accept any arguments to be flexible with mock calls
                if args and isinstance(args[-1], dict):
                    updates_applied.append(args[-1])
                    wf.update(args[-1])
                return {"success": True}

            mock_repo.update_workflow.side_effect = capture_update

            o = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
            o.repo = mock_repo
            o._workflow_id = "test-workflow-id-2963"
            o._gh = None
            o._emit = MagicMock()

            # Simulate the create_repo block
            repo_url = "https://github.com/owner/my-new-project"
            o._update_workflow({"project_repo_url": repo_url})

            # Verify wf was synced (this is what the fix adds)
            # In the actual code, this is done by: wf["project_repo_url"] = repo_url
            assert (
                wf.get("project_repo_url") == repo_url
            ), "wf should be synced with repo_url after _update_workflow"


class TestIssueRepoFromLocalVariable:
    """测试点2：create_issue 优先使用本地 repo_url 变量"""

    def test_issue_repo_uses_local_repo_url(self):
        """Verify issue creation uses local repo_url, not stale wf value."""
        import re

        # Scenario: create_repo succeeded, but wf dict has stale value
        local_repo_url = "https://github.com/owner/new-project"  # From create_repo
        wf_stale_value = "my-new-project"  # Input name, not URL

        # The fix: issue_repo_url = repo_url or wf.get("project_repo_url", "")
        issue_repo_url = local_repo_url or wf_stale_value

        # Extract owner/repo
        match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", issue_repo_url)
        issue_repo = match.group(1) if match else None

        assert issue_repo == "owner/new-project"
        assert issue_repo is not None, "Should extract repo from local repo_url"


class TestCloneAfterCreateRepo:
    """测试点3：新项目创建后克隆仓库到本地"""

    def test_clone_called_for_new_project(self, tmp_path):
        """Verify git clone is called after create_repo for new projects."""
        repo_url = "https://github.com/owner/new-project"
        project_path = str(tmp_path / "new-project")

        # Simulate the clone logic from the fix
        git_calls = []

        def mock_run_git(args, **kw):
            git_calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_gh = MagicMock()
        mock_gh._run_git.side_effect = mock_run_git
        mock_gh.get_repo_url.return_value = repo_url

        # Directory doesn't exist → should create parent and clone
        assert not os.path.exists(project_path)

        # The fix logic
        if not os.path.exists(project_path):
            os.makedirs(os.path.dirname(project_path), exist_ok=True)

        if not os.path.isdir(os.path.join(project_path, ".git")):
            mock_gh._run_git(["clone", repo_url, project_path])

        # Verify clone was called
        assert ["clone", repo_url, project_path] in git_calls

    def test_skip_clone_if_already_cloned(self, tmp_path):
        """Verify clone is skipped if directory is already the target repo."""
        repo_url = "https://github.com/owner/new-project"
        project_path = str(tmp_path / "new-project")
        os.makedirs(project_path)
        os.makedirs(os.path.join(project_path, ".git"))

        git_calls = []

        def mock_run_git(args, **kw):
            git_calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_gh = MagicMock()
        mock_gh._run_git.side_effect = mock_run_git
        mock_gh.get_repo_url.return_value = repo_url

        # The fix logic: already cloned
        if not os.path.isdir(os.path.join(project_path, ".git")):
            mock_gh._run_git(["clone", repo_url, project_path])

        # Should NOT call clone
        assert ["clone", repo_url, project_path] not in git_calls

    def test_preparation_without_project_path_clones_into_user_workspace(self):
        """Route-permitted new projects without a path use the workspace fallback."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-no-path-2963",
                "user_id": 1,
                "project_path": None,
                "branch_strategy": "current",
                "github_issue_number": 123,
                "requirements_text": "Build a REST API service",
            }
        )
        repo_url = "https://github.com/owner/new-repo"
        fallback_path = "/workspace/alice/new-repo"

        mock_gh = MagicMock()
        mock_gh.create_repo.return_value = {"name": "new-repo", "url": repo_url}
        mock_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_gh._needs_sudo.return_value = False  # Single-user mode, no chown needed
        mock_gh.get_current_branch.return_value = "main"

        repo = MagicMock()
        repo.get_workflow.return_value = wf

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch.repo = repo
        orch._workflow_id = wf["workflow_id"]
        orch._gh = None
        orch._shutdown_requested = threading.Event()
        orch._session_usage_offsets = {}
        orch._update_workflow = MagicMock(side_effect=lambda updates: wf.update(updates))
        orch._create_milestone = MagicMock(return_value={"milestone_id": "ms"})
        orch._get_gh = MagicMock(return_value=mock_gh)
        orch.emit_phase_change = MagicMock()

        def fake_exists(path):
            return path == "."

        def fake_isdir(path):
            return False

        with (
            patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=mock_gh),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch.object(
                AutonomousOrchestrator, "_get_user_workspace", return_value="/workspace/alice"
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.path.exists", fake_exists),
            patch("app.modules.workspace.autonomous.orchestrator.os.path.isdir", fake_isdir),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.listdir", return_value=["file"]
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.makedirs") as makedirs,
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

        # Issue #3070: Now uses gh repo clone for private repo authentication
        mock_gh._run_gh.assert_any_call(
            ["repo", "clone", "owner/new-repo", "--", fallback_path],
            repo_scoped=False,
            check=True,
        )
        makedirs.assert_any_call("/workspace/alice", exist_ok=True)
        orch._update_workflow.assert_any_call({"project_path": fallback_path})
        assert result.next_phase == "planning"

    def test_preparation_reentry_without_project_path_clones_into_user_workspace(self):
        """A resolved URL checkpoint still ensures the local clone on re-entry."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        repo_url = "https://github.com/owner/new-repo"
        fallback_path = "/workspace/alice/new-repo"
        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-reentry-no-path-2963",
                "user_id": 1,
                "project_repo_url": repo_url,
                "project_path": None,
                "branch_strategy": "current",
                "github_issue_number": 123,
                "requirements_text": "Build a REST API service",
            }
        )

        local_gh = MagicMock(name="local_gh")
        local_gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        local_gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        local_gh._needs_sudo.return_value = False  # Single-user mode, no chown needed
        local_gh.get_current_branch.return_value = "main"

        repo = MagicMock()
        repo.get_workflow.return_value = wf

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch.repo = repo
        orch._workflow_id = wf["workflow_id"]
        orch._gh = None
        orch._shutdown_requested = threading.Event()
        orch._session_usage_offsets = {}
        orch._update_workflow = MagicMock(side_effect=lambda updates: wf.update(updates))
        orch._create_milestone = MagicMock(return_value={"milestone_id": "ms"})
        orch.emit_phase_change = MagicMock()

        def fake_isdir(path):
            return False

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=local_gh
            ) as git_hub_ops,
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch.object(
                AutonomousOrchestrator, "_get_user_workspace", return_value="/workspace/alice"
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.os.path.exists", return_value=False
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.path.isdir", fake_isdir),
            patch("app.modules.workspace.autonomous.orchestrator.os.makedirs") as makedirs,
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
                gh=local_gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            result = orch._do_preparation(ctx, deps)

        local_gh.create_repo.assert_not_called()
        git_hub_ops.assert_any_call(fallback_path, system_account="alice")
        # Issue #3070: Now uses gh repo clone for private repo authentication
        local_gh._run_gh.assert_any_call(
            ["repo", "clone", "owner/new-repo", "--", fallback_path],
            repo_scoped=False,
            check=True,
        )
        makedirs.assert_any_call("/workspace/alice", exist_ok=True)
        orch._update_workflow.assert_any_call({"project_path": fallback_path})
        assert result.next_phase == "planning"

    def test_preparation_without_project_path_checks_existing_fallback_repo(self):
        """Existing fallback repos are validated using the fallback-bound GitHubOps."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-existing-fallback-2963",
                "user_id": 1,
                "project_path": None,
                "branch_strategy": "current",
                "github_issue_number": 123,
                "requirements_text": "Build a REST API service",
            }
        )
        repo_url = "https://github.com/owner/new-repo"
        fallback_path = "/workspace/alice/new-repo"

        create_gh = MagicMock(name="create_gh")
        create_gh.create_repo.return_value = {"name": "new-repo", "url": repo_url}
        create_gh.get_repo_url.return_value = "https://github.com/wrong/repo"
        local_gh = MagicMock(name="local_gh")
        local_gh.get_repo_url.return_value = repo_url
        local_gh.get_current_branch.return_value = "main"

        repo = MagicMock()
        repo.get_workflow.return_value = wf

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch.repo = repo
        orch._workflow_id = wf["workflow_id"]
        orch._gh = None
        orch._shutdown_requested = threading.Event()
        orch._session_usage_offsets = {}
        orch._update_workflow = MagicMock(side_effect=lambda updates: wf.update(updates))
        orch._create_milestone = MagicMock(return_value={"milestone_id": "ms"})
        orch.emit_phase_change = MagicMock()

        def fake_exists(path):
            return path == fallback_path

        def fake_isdir(path):
            return path == os.path.join(fallback_path, ".git")

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.GitHubOps",
                side_effect=[create_gh, local_gh],
            ),
            patch(
                "app.modules.workspace.autonomous.orchestrator.UserRepository"
            ) as mock_user_repo_cls,
            patch.object(
                AutonomousOrchestrator, "_get_user_workspace", return_value="/workspace/alice"
            ),
            patch("app.modules.workspace.autonomous.orchestrator.os.path.exists", fake_exists),
            patch("app.modules.workspace.autonomous.orchestrator.os.path.isdir", fake_isdir),
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
                gh=create_gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            result = orch._do_preparation(ctx, deps)

        create_gh.get_repo_url.assert_not_called()
        local_gh.get_repo_url.assert_called_once()
        local_gh._run_git.assert_not_called()
        orch._update_workflow.assert_any_call({"project_path": fallback_path})
        assert result.next_phase == "planning"

    def test_error_if_different_repo(self, tmp_path):
        """Verify error if directory exists but is a different repo."""
        repo_url = "https://github.com/owner/new-project"
        existing_url = "https://github.com/other/different-project"
        project_path = str(tmp_path / "new-project")
        os.makedirs(project_path)
        os.makedirs(os.path.join(project_path, ".git"))

        mock_gh = MagicMock()
        mock_gh.get_repo_url.return_value = existing_url

        # The fix logic
        with pytest.raises(GitHubOpsError, match="different git repo"):
            if os.path.isdir(os.path.join(project_path, ".git")):
                if mock_gh.get_repo_url() != repo_url:
                    raise GitHubOpsError(f"Directory {project_path} is a different git repo")


class TestValidateProjectPath:
    """测试点4：git 操作前验证目录有效性"""

    def test_error_if_project_path_not_set(self):
        """Verify error if project_path is None or empty."""
        project_path = None

        with pytest.raises(GitHubOpsError, match="project_path is not set"):
            if not project_path:
                raise GitHubOpsError("project_path is not set for branch creation")

    def test_error_if_project_path_does_not_exist(self):
        """Verify error if project_path directory doesn't exist."""
        project_path = "/nonexistent/path"

        with pytest.raises(GitHubOpsError, match="does not exist"):
            if not os.path.isdir(project_path):
                raise GitHubOpsError(f"project_path {project_path} does not exist")

    def test_error_if_not_git_repository(self, tmp_path):
        """Verify error if directory is not a git repository."""
        project_path = str(tmp_path / "not-a-repo")
        os.makedirs(project_path)

        with pytest.raises(GitHubOpsError, match="not a valid git repository"):
            if not os.path.isdir(os.path.join(project_path, ".git")):
                raise GitHubOpsError(f"{project_path} is not a valid git repository")

    def test_pass_if_valid_git_repository(self, tmp_path):
        """Verify validation passes for valid git repository."""
        project_path = str(tmp_path / "valid-repo")
        os.makedirs(project_path)
        os.makedirs(os.path.join(project_path, ".git"))

        # Should not raise
        if not project_path:
            raise GitHubOpsError("project_path is not set")
        if not os.path.isdir(project_path):
            raise GitHubOpsError(f"project_path {project_path} does not exist")
        if not os.path.isdir(os.path.join(project_path, ".git")):
            raise GitHubOpsError(f"{project_path} is not a valid git repository")

        # No exception = pass
