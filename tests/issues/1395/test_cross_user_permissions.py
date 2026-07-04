"""Tests for cross-user filesystem permission fixes (Issue #1395).

The open-ace service runs as the ``openace`` user, but autonomous-development
workflows target a repo owned by another user (``system_account``), often
under a 0700 home directory. The ``_run_git``/``_run_gh`` sudo wrapper already
routes git/gh subprocesses through ``sudo -u <system_account>``, but several
*Python-native* filesystem calls slipped through and stat'd as the service
user, raising ``[Errno 13] Permission denied``.

Covers the four fixes:

  1. ``GitHubOps.path_exists_as_user`` — routes existence checks through
     ``sudo -u <account> test`` when cross-user, else falls back to ``Path``.
  2. ``_do_preparation`` residual-worktree cleanup uses ``list_worktrees`` +
     ``remove_worktree`` (both already sudo-wrapped) instead of
     ``Path.exists()`` / ``shutil.rmtree``.
  3. ``_ensure_worktree`` validity check uses ``path_exists_as_user`` instead
     of ``os.path.isfile``.
  4. ``_run_local_agent_task`` mkdir uses ``sudo -u <account> mkdir -p`` when
     cross-user.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps

# ── GitHubOps.path_exists_as_user ────────────────────────────────────────


class TestPathExistsAsUser:
    def test_same_user_falls_back_to_path(self, monkeypatch, tmp_path):
        # When the service already runs as system_account (NoNewPrivileges
        # forbids sudo), path_exists_as_user must use Path directly.
        gh = GitHubOps(str(tmp_path), system_account="openace")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )

        target = tmp_path / "marker"
        target.mkdir()
        assert gh.path_exists_as_user(str(target), dir_only=True) is True

    def test_same_user_dir_only_false_for_file(self, monkeypatch, tmp_path):
        gh = GitHubOps(str(tmp_path), system_account="openace")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        target = tmp_path / "file.txt"
        target.write_text("x")
        # dir_only requires a directory; a file must return False.
        assert gh.path_exists_as_user(str(target), dir_only=True) is False
        # Without dir_only the file exists.
        assert gh.path_exists_as_user(str(target)) is True

    def test_cross_user_runs_sudo_test(self, monkeypatch, tmp_path):
        # Service runs as 'openace' but repo owner is 'rhuang' → must sudo.
        gh = GitHubOps(str(tmp_path), system_account="rhuang")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        target_path = "/home/rhuang/auto-dev-abc12345"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return MagicMock(returncode=0)  # test -e → 0 means exists

        monkeypatch.setattr("app.modules.workspace.autonomous.github_ops.subprocess.run", fake_run)
        result = gh.path_exists_as_user(target_path)

        assert result is True
        assert captured["cmd"] == ["sudo", "-u", "rhuang", "test", "-e", target_path]

    def test_cross_user_dir_only_uses_d_flag(self, monkeypatch, tmp_path):
        gh = GitHubOps(str(tmp_path), system_account="rhuang")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr("app.modules.workspace.autonomous.github_ops.subprocess.run", fake_run)
        gh.path_exists_as_user("/home/rhuang/x", dir_only=True)

        assert captured["cmd"][4] == "-d"

    def test_cross_user_file_only_uses_f_flag(self, monkeypatch, tmp_path):
        # file_only → test -f, distinguishes a worktree's .git FILE from a
        # normal repo's .git DIRECTORY (Issue #1395 review).
        gh = GitHubOps(str(tmp_path), system_account="rhuang")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr("app.modules.workspace.autonomous.github_ops.subprocess.run", fake_run)
        gh.path_exists_as_user("/home/rhuang/repo/.git", file_only=True)

        assert captured["cmd"][4] == "-f"

    def test_same_user_file_only_false_for_directory(self, monkeypatch, tmp_path):
        # A directory must NOT pass the file_only check — this is the core
        # regression guard: a plain clone's .git directory must not be mistaken
        # for a valid worktree's .git file.
        gh = GitHubOps(str(tmp_path), system_account="openace")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        # tmp_path itself is a directory.
        assert gh.path_exists_as_user(str(tmp_path), file_only=True) is False
        # A real file passes.
        f = tmp_path / "gitfile"
        f.write_text("gitdir: ../.git/worktrees/x")
        assert gh.path_exists_as_user(str(f), file_only=True) is True

    def test_cross_user_sudo_failure_returns_false(self, monkeypatch, tmp_path):
        # test exits non-zero → path does not exist.
        gh = GitHubOps(str(tmp_path), system_account="rhuang")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.subprocess.run",
            lambda cmd, **_kw: MagicMock(returncode=1),
        )
        assert gh.path_exists_as_user("/home/rhuang/missing") is False

    def test_no_system_account_uses_path(self, tmp_path):
        # system_account=None → never sudo, always Path.
        gh = GitHubOps(str(tmp_path), system_account=None)
        assert gh._needs_sudo() is False
        present = tmp_path / "there"
        present.mkdir()
        assert gh.path_exists_as_user(str(present)) is True
        assert gh.path_exists_as_user(str(tmp_path / "absent")) is False

    def test_subprocess_exception_returns_false(self, monkeypatch, tmp_path):
        # TimeoutExpired / FileNotFoundError → treat as "does not exist".
        gh = GitHubOps(str(tmp_path), system_account="rhuang")
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
            lambda _uid: MagicMock(pw_name="openace"),
        )
        import subprocess as _sp

        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.subprocess.run",
            lambda cmd, **_kw: (_ for _ in ()).throw(_sp.TimeoutExpired(cmd, 10)),
        )
        assert gh.path_exists_as_user("/home/rhuang/x") is False


# ── _do_preparation residual worktree cleanup ───────────────────────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1395",
        "title": "gh issue 1395",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "project_path": "/home/rhuang/open-ace",
        "user_id": 5,
        "current_phase": "preparation",
        "status": "preparing",
        "github_issue_number": 1395,
        "requirements_text": "fix cross-user permissions",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-new",
            "workflow_id": wf["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
        o.emitter = MagicMock()
    return o, mock_repo


class TestPreparationWorktreeCleanup:
    """Residual-worktree cleanup must route through git (already sudo-wrapped),
    not Python Path.exists()/shutil.rmtree() which stat as the service user."""

    def _setup_gh(self, mock_gh_cls, list_worktrees):
        mock_gh = MagicMock()
        mock_gh.create_issue.return_value = {"number": 1395, "url": "x"}
        mock_gh.list_worktrees.return_value = list_worktrees
        mock_gh.create_worktree.return_value = {"worktree_path": "/home/rhuang/auto-dev-wf-1395"}
        mock_gh_cls.return_value = mock_gh
        return mock_gh

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_registered_residual_removed_via_git(self, mock_gh_cls):
        # Residual worktree is still git-registered → remove_worktree (sudo).
        wt_path = "/home/rhuang/auto-dev-wf-1395"
        mock_gh = self._setup_gh(
            mock_gh_cls,
            list_worktrees=[{"path": wt_path, "branch": "auto-dev/wf-1395"}],
        )
        wf = _make_workflow()
        orch, _repo = _make_orchestrator(wf)

        orch._do_preparation(wf)

        mock_gh.remove_worktree.assert_called_once_with(wt_path)
        # shutil.rmtree must NOT have been invoked on the orchestrator: the
        # service user cannot delete a path under a 0700 home anyway.
        mock_gh.create_worktree.assert_called_once()
        mock_gh.path_exists_as_user.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_non_registered_residual_dir_left_in_place(self, mock_gh_cls):
        # Directory present but pruned (not git-registered): no rm privilege →
        # leave it and warn, do not rmtree as the service user.
        mock_gh = self._setup_gh(mock_gh_cls, list_worktrees=[])
        mock_gh.path_exists_as_user.return_value = True  # dir still on disk
        wf = _make_workflow()
        orch, _repo = _make_orchestrator(wf)

        orch._do_preparation(wf)

        mock_gh.remove_worktree.assert_not_called()
        mock_gh.path_exists_as_user.assert_called()
        # create_worktree still attempted (surfaces a clear error if blocked).
        mock_gh.create_worktree.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_clean_path_creates_worktree_directly(self, mock_gh_cls):
        # No residual at all → straight to create_worktree.
        mock_gh = self._setup_gh(mock_gh_cls, list_worktrees=[])
        mock_gh.path_exists_as_user.return_value = False
        wf = _make_workflow()
        orch, _repo = _make_orchestrator(wf)

        orch._do_preparation(wf)

        mock_gh.remove_worktree.assert_not_called()
        mock_gh.create_worktree.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_remove_failure_does_not_raise(self, mock_gh_cls):
        # If git worktree remove fails, log a warning and continue — must
        # NOT fall back to shutil.rmtree (would PermissionError as service user).
        from app.modules.workspace.autonomous.github_ops import GitHubOpsError

        wt_path = "/home/rhuang/auto-dev-wf-1395"
        mock_gh = self._setup_gh(
            mock_gh_cls,
            list_worktrees=[{"path": wt_path, "branch": "auto-dev/wf-1395"}],
        )
        mock_gh.remove_worktree.side_effect = GitHubOpsError("locked")
        wf = _make_workflow()
        orch, _repo = _make_orchestrator(wf)

        # Must not raise — preparation continues to create_worktree.
        orch._do_preparation(wf)
        mock_gh.create_worktree.assert_called_once()


# ── _ensure_project_dir (agent_runner mkdir) ────────────────────────────


class TestEnsureProjectDir:
    def test_cross_user_mkdir_uses_sudo(self, monkeypatch, tmp_path):
        # project_path under a user-private dir → mkdir goes through sudo.
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return MagicMock(returncode=0)

        monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
        target = str(tmp_path / "deep" / "newdir")

        agent_runner.AutonomousAgentRunner._ensure_project_dir(target, "rhuang")

        assert captured["cmd"] == ["sudo", "-u", "rhuang", "mkdir", "-p", target]
        assert captured["kwargs"]["timeout"] == 30

    def test_same_user_mkdir_uses_path(self, monkeypatch, tmp_path):
        # Same user → plain Path.mkdir, no sudo (NoNewPrivileges safe).
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        sudo_calls = []
        monkeypatch.setattr(
            agent_runner.subprocess,
            "run",
            lambda cmd, **_kw: sudo_calls.append(cmd) or MagicMock(returncode=0),
        )
        target = tmp_path / "plain"
        agent_runner.AutonomousAgentRunner._ensure_project_dir(str(target), "openace")

        assert sudo_calls == []  # no sudo
        assert target.is_dir()  # Path.mkdir actually created it

    def test_no_system_account_uses_path(self, tmp_path):
        from app.modules.workspace.autonomous import agent_runner

        target = tmp_path / "plain"
        agent_runner.AutonomousAgentRunner._ensure_project_dir(str(target), None)
        assert target.is_dir()

    def test_cross_user_existing_dir_still_sudo_mkdir_p(self, monkeypatch, tmp_path):
        # mkdir -p is idempotent; even if the dir exists, sudo mkdir is fine.
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        target = tmp_path / "exists"
        target.mkdir()
        captured = {}

        def fake_run(cmd, **_kw):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
        agent_runner.AutonomousAgentRunner._ensure_project_dir(str(target), "rhuang")
        assert captured["cmd"][:3] == ["sudo", "-u", "rhuang"]

    def test_cross_user_mkdir_failure_raises(self, monkeypatch, tmp_path):
        # sudo/mkdir failure must raise (fail-fast) instead of being swallowed,
        # mirroring Path.mkdir's semantics (Issue #1395 review).
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))

        def fake_run(cmd, **_kw):
            return MagicMock(
                returncode=1, stderr="mkdir: cannot create directory: Permission denied"
            )

        monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

        import pytest

        with pytest.raises(PermissionError, match="Permission denied"):
            agent_runner.AutonomousAgentRunner._ensure_project_dir("/home/rhuang/no-perm", "rhuang")
