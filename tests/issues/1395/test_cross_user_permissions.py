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


# ── _wrap_agent_cmd / _is_cross_user (agent launch) ─────────────────────


class TestWrapAgentCmd:
    """Cross-user agent launch must route through the run-as wrapper (which
    chdir's as root then drops to system_account), not the old sudo -u prefix
    that left Popen chdir'ing as the service user and failing under a private
    home. Same-user keeps the command verbatim with cwd=project (Issue #1395)."""

    def test_cross_user_wraps_with_run_as(self, monkeypatch):
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        base_cmd = ["/usr/local/bin/claude", "--print"]
        project = "/home/rhuang/auto-dev-0b463e77"

        cmd, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(base_cmd, project, "rhuang")

        # Wrapper invocation: sudo -n -u root <wrapper> <user> <dir> <cmd...>
        assert cmd[:6] == [
            "sudo",
            "-n",
            "-u",
            "root",
            agent_runner._OPENACE_RUN_AS,
            "rhuang",
        ]
        assert cmd[6] == project  # project dir passed to wrapper
        assert cmd[7:] == base_cmd  # original command appended verbatim
        # cwd must be None — the wrapper chdir's internally; a non-None cwd
        # would make Popen chdir as the service user and re-trigger [Errno 13].
        assert cwd is None

    def test_same_user_keeps_cmd_verbatim(self, monkeypatch):
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        base_cmd = ["/usr/local/bin/claude", "--print"]
        project = str(_tmp := __import__("pathlib").Path("/srv/proj"))

        cmd, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            base_cmd, project, "openace"  # same as service user
        )

        assert cmd == base_cmd  # no wrapper
        assert cwd == project  # Popen chdir's directly (same user has access)

    def test_no_system_account_keeps_cmd(self):
        from app.modules.workspace.autonomous import agent_runner

        base_cmd = ["claude"]
        cmd, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(base_cmd, "/srv/proj", None)
        assert cmd == base_cmd
        assert cwd == "/srv/proj"

    def test_cross_user_preserves_complex_cmd(self, monkeypatch):
        # Multi-element command (e.g. node engine for zcode) passes through intact.
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        base_cmd = ["node", "/path/to/zcode.cjs", "app-server", "--cwd", "/home/rhuang/p"]
        cmd, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            base_cmd, "/home/rhuang/p", "rhuang"
        )
        assert cmd[7:] == base_cmd
        assert cwd is None


class TestIsCrossUser:
    def test_none_account_is_not_cross_user(self):
        from app.modules.workspace.autonomous import agent_runner

        assert agent_runner.AutonomousAgentRunner._is_cross_user(None) is False

    def test_same_user_is_not_cross_user(self, monkeypatch):
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        assert agent_runner.AutonomousAgentRunner._is_cross_user("openace") is False

    def test_different_user_is_cross_user(self, monkeypatch):
        from app.modules.workspace.autonomous import agent_runner

        monkeypatch.setattr(agent_runner.pwd, "getpwuid", lambda _uid: MagicMock(pw_name="openace"))
        assert agent_runner.AutonomousAgentRunner._is_cross_user("rhuang") is True


# ── _build_agent_env (LLM proxy auth) ────────────────────────────────────


class TestBuildAgentEnv:
    """Local autonomous agents must authenticate through the Open ACE LLM proxy
    with a signed proxy token — never the raw API key (security: key stays only
    in the DB). _build_agent_env mints the token and injects adapter-specific
    env vars (ANTHROPIC_API_KEY/OPENAI_API_KEY = proxy_token, *_BASE_URL =
    proxy URL)."""

    def _setup_proxy(self, monkeypatch):
        """Stub get_api_key_proxy_service to return a fake proxy service."""
        from app.modules.workspace.autonomous import agent_runner

        fake_proxy = MagicMock()
        fake_proxy.generate_proxy_token.return_value = "signedtoken.payload"

        def fake_get_service():
            return fake_proxy

        # _build_agent_env imports the factory inside the function, so patch at
        # the module where it's defined.
        monkeypatch.setattr(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
            fake_get_service,
        )
        return fake_proxy

    def test_claude_code_gets_anthropic_proxy_env(self, monkeypatch):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        self._setup_proxy(monkeypatch)
        adapter = MagicMock()
        adapter.get_env_vars.return_value = {
            "ANTHROPIC_API_KEY": "signedtoken.payload",
            "ANTHROPIC_BASE_URL": "http://localhost:5000/api/remote/llm-proxy",
        }

        env = AutonomousAgentRunner._build_agent_env(
            adapter, "claude-code", user_id=5, session_id="sess-1", model="glm-5"
        )

        assert env["ANTHROPIC_API_KEY"] == "signedtoken.payload"
        assert "/api/remote/llm-proxy" in env["ANTHROPIC_BASE_URL"]
        assert env["OPENACE_PROXY_TOKEN"] == "signedtoken.payload"
        assert env["OPENACE_MODEL"] == "glm-5"
        adapter.get_env_vars.assert_called_once()

    def test_qwen_code_gets_openai_proxy_env(self, monkeypatch):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        fake_proxy = self._setup_proxy(monkeypatch)
        adapter = MagicMock()
        adapter.get_env_vars.return_value = {
            "OPENAI_API_KEY": "signedtoken.payload",
            "OPENAI_BASE_URL": "http://localhost:5000/api/remote/llm-proxy",
        }

        env = AutonomousAgentRunner._build_agent_env(
            adapter, "qwen-code-cli", user_id=5, session_id="sess-2", model=""
        )

        assert env["OPENAI_API_KEY"] == "signedtoken.payload"
        # generate_proxy_token called with provider=openai for qwen
        _args, kwargs = fake_proxy.generate_proxy_token.call_args
        assert kwargs["provider"] == "openai"

    def test_proxy_token_is_signed_not_raw_key(self, monkeypatch):
        # The injected API_KEY must be the proxy token, never a raw DB key.
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        fake_proxy = self._setup_proxy(monkeypatch)
        fake_proxy.generate_proxy_token.return_value = "tok.abc123"
        adapter = MagicMock()
        adapter.get_env_vars.return_value = {"ANTHROPIC_API_KEY": "tok.abc123"}

        env = AutonomousAgentRunner._build_agent_env(
            adapter, "claude-code", user_id=1, session_id="s", model=""
        )

        assert env["ANTHROPIC_API_KEY"] == "tok.abc123"
        assert "." in env["ANTHROPIC_API_KEY"]  # signed token format

    def test_fallback_on_proxy_failure(self, monkeypatch):
        # If proxy setup throws, fall back to dict(os.environ) (dev box with
        # env-injected keys keeps working).
        from app.modules.workspace.autonomous import agent_runner

        def fake_get_service():
            raise RuntimeError("DB unavailable")

        monkeypatch.setattr(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
            fake_get_service,
        )
        adapter = MagicMock()
        env = agent_runner.AutonomousAgentRunner._build_agent_env(
            adapter, "claude-code", user_id=None, session_id="s", model=""
        )
        # adapter.get_env_vars never called (setup failed before that)
        adapter.get_env_vars.assert_not_called()
        # env is a plain dict copy of os.environ
        assert isinstance(env, dict)
