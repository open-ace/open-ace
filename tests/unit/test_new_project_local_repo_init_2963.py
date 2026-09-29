# tests/unit/test_new_project_local_repo_init_2963.py
"""
Issue #2963: AI 自主开发创建新项目时首次创建 Issue 失败，重试后因本地仓库未初始化导致分支创建失败

测试点（均为驱动真实 _do_preparation 的 driver）：
1. create_repo 后同步内存中的 wf 字典 — 由 3075 文件的
   test_create_issue_called_with_correct_repo 以纯 MagicMock _update_workflow
   断言产品自身同步行（#3296 B7）。
2. create_issue 优先使用本地 repo_url 变量 — 由 3075 文件同名真 driver 覆盖。
3. 新项目创建后克隆仓库到本地 — 本文件 TestCloneAfterCreateRepo 真驱动
   （gh repo clone 主臂 + git clone 回退臂 + different-repo/not-empty 两条 raise）。
4. git 操作前验证目录有效性 — 产品侧该无条件验证已被 60045a88 移除
   （"remove unconditional validation to fix existing tests"），重放它的
   TestValidateProjectPath 已删除（#3296 B1，60045a88 证据）。

#3296 移除了本文件残余的自演式测试（mock 自测/正则重放/已删产品逻辑重放），
逐项处置见 issue #3296 处置表。
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


def _setup_orchestrator(wf, mock_gh):
    """Create a partially-mocked orchestrator driving the real _do_preparation."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    repo = MagicMock()
    repo.get_workflow.return_value = wf

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = repo
    orch._workflow_id = wf.get("workflow_id", "wf-2963-test")
    orch._gh = None
    orch._shutdown_requested = threading.Event()
    orch._session_usage_offsets = {}
    orch._update_workflow = MagicMock(side_effect=lambda updates: wf.update(updates))
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms"})
    orch.emit_phase_change = MagicMock()
    orch._get_gh = MagicMock(return_value=mock_gh)
    return orch, repo


class TestCloneAfterCreateRepo:
    """测试点3：新项目创建后克隆仓库到本地（真实 _do_preparation driver）"""

    def test_git_clone_fallback_for_non_github_host(self, tmp_path):
        """Non-github.com repo URLs clone via plain git (the fallback arm).

        Drives the real _do_preparation clone decision on a re-entry workflow:
        the clone regex only matches github.com, so a self-hosted URL must take
        the ``gh._run_git(["clone", ...])`` arm. (The ``gh repo clone`` arm for
        github.com URLs is covered by the without-project-path drivers below.
        #3296 B3: previously this was an inline replay of the clone logic.)
        """
        repo_url = "https://git.example.com/owner/new-repo"
        project_path = str(tmp_path)  # Real (empty) directory

        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-git-clone-fallback-2963",
                "user_id": 1,
                "project_repo_url": repo_url,  # Resolved URL: re-entry, no create_repo
                "project_path": project_path,
                "branch_strategy": "current",
                "github_issue_number": 123,  # Issue already exists; focus on clone
            }
        )

        gh = MagicMock(name="gh")
        gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._needs_sudo.return_value = False
        gh.get_current_branch.return_value = "main"

        orch, repo = _setup_orchestrator(wf, gh)

        with (
            patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=gh),
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
                gh=gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            result = orch._do_preparation(ctx, deps)

        gh.create_repo.assert_not_called()  # Re-entry: URL already checkpointed
        gh._run_git.assert_any_call(["clone", repo_url, project_path])
        # The gh-repo-clone arm must NOT fire for a non-github.com host.
        assert not any(
            c.args and c.args[0][:2] == ["repo", "clone"] for c in gh._run_gh.call_args_list
        )
        orch._update_workflow.assert_any_call({"project_path": project_path})
        assert result.next_phase == "planning"

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
        # Issue #3155: Create project_path directory before gh repo clone
        makedirs.assert_any_call(fallback_path, exist_ok=True)
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
        # Issue #3155: Create project_path directory before gh repo clone
        makedirs.assert_any_call(fallback_path, exist_ok=True)
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

    def test_error_if_directory_is_a_different_repo(self, tmp_path):
        """An existing .git directory pointing at another repo raises.

        Drives the real raise in _do_preparation's clone decision block: the
        target directory already has a .git whose origin URL differs from the
        workflow's resolved repo_url. (#3296 B5: previously an inline replay;
        this raise path had no real coverage.)
        """
        repo_url = "https://github.com/owner/new-project"
        project_path = tmp_path / "new-project"
        (project_path / ".git").mkdir(parents=True)  # Real .git directory

        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-different-repo-2963",
                "user_id": 1,
                "project_repo_url": repo_url,
                "project_path": str(project_path),
                "branch_strategy": "current",
                "github_issue_number": None,
            }
        )

        gh = MagicMock(name="gh")
        gh.get_repo_url.return_value = "https://github.com/other/different-project"
        gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._needs_sudo.return_value = False

        orch, repo = _setup_orchestrator(wf, gh)

        with (
            patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=gh),
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
                gh=gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            with pytest.raises(GitHubOpsError, match="different git repo"):
                orch._do_preparation(ctx, deps)

        gh.create_issue.assert_not_called()
        self._assert_repo_setup_failed(orch, "different git repo")

    def test_error_if_directory_exists_but_not_empty(self, tmp_path):
        """A non-git, non-empty target directory raises instead of clobbered.

        Drives the other raise in the clone decision block: the target exists,
        has no .git, and contains user files — the preparation must abort
        rather than clone over it. (#3296 B6: this raise path had no real
        coverage at all.)
        """
        repo_url = "https://github.com/owner/new-project"
        project_path = tmp_path / "new-project"
        project_path.mkdir()
        (project_path / "stale.txt").write_text("user data")

        wf = _make_test_workflow()
        wf.update(
            {
                "workflow_id": "wf-not-empty-2963",
                "user_id": 1,
                "project_repo_url": repo_url,
                "project_path": str(project_path),
                "branch_strategy": "current",
                "github_issue_number": None,
            }
        )

        gh = MagicMock(name="gh")
        gh.get_repo_url.return_value = repo_url
        gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._run_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        gh._needs_sudo.return_value = False

        orch, repo = _setup_orchestrator(wf, gh)

        with (
            patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=gh),
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
                gh=gh,
                git_workspace=MagicMock(),
                evidence=MagicMock(),
                sandbox=MagicMock(),
                repo=repo,
                agent_runner=MagicMock(),
            )

            with pytest.raises(GitHubOpsError, match="exists but is not empty"):
                orch._do_preparation(ctx, deps)

        gh.create_issue.assert_not_called()
        self._assert_repo_setup_failed(orch, "not empty")
        # The user's file must be untouched.
        assert (project_path / "stale.txt").read_text() == "user data"

    @staticmethod
    def _assert_repo_setup_failed(orch, needle):
        """The failed repo_setup milestone must carry the raise's message."""
        failed_messages = [
            c.kwargs.get("error_message", "")
            for c in orch._create_milestone.call_args_list
            if c.kwargs.get("milestone_type") == "repo_setup" and c.kwargs.get("status") == "failed"
        ]
        assert failed_messages, "expected a failed repo_setup milestone"
        assert needle in failed_messages[0]
