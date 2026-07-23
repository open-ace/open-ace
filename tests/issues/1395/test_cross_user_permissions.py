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

import base64
import os
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

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
        mock_gh.get_current_branch.return_value = "auto-dev/wf-1395"
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

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_existing_branch_uses_add_worktree_not_create(self, mock_gh_cls):
        # A prior failed run left the branch behind (worktree gone, branch
        # survives). Blindly calling create_worktree (which uses -b) would
        # fail "a branch named '...' already exists". The prep path must
        # detect the surviving branch and attach via add_worktree (no -b).
        from subprocess import CompletedProcess

        mock_gh = self._setup_gh(mock_gh_cls, list_worktrees=[])
        mock_gh.path_exists_as_user.return_value = False
        # show-ref --verify for refs/heads/<branch> → found (rc=0).
        found = CompletedProcess(args=[], returncode=0)
        mock_gh._run_git.side_effect = lambda *a, **k: found
        mock_gh.add_worktree.return_value = {"worktree_path": "/home/rhuang/auto-dev-wf-1395"}

        wf = _make_workflow()
        orch, _repo = _make_orchestrator(wf)

        orch._do_preparation(wf)

        mock_gh.add_worktree.assert_called_once()
        mock_gh.create_worktree.assert_not_called()


# ── _ensure_project_dir (agent_runner mkdir) ────────────────────────────


class TestEnsureProjectDir:
    def test_cross_user_directory_probe_uses_isolated_wrapper(self, monkeypatch, tmp_path):
        # The isolated wrapper grants the ACL before checking the worktree.
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

        assert captured["cmd"] == [
            "sudo",
            "-n",
            "-u",
            "root",
            agent_runner._OPENACE_RUN_AS,
            "--isolated",
            "rhuang",
            target,
            "/usr/bin/test",
            "-d",
            ".",
        ]
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

    def test_cross_user_existing_dir_still_uses_wrapper(self, monkeypatch, tmp_path):
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
        assert captured["cmd"][:6] == [
            "sudo",
            "-n",
            "-u",
            "root",
            agent_runner._OPENACE_RUN_AS,
            "--isolated",
        ]

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


def test_isolated_wrapper_scopes_git_safe_directory_to_worktree():
    from pathlib import Path

    wrapper = Path("scripts/openace-run-as.sh").read_text(encoding="utf-8")

    assert '"GIT_CONFIG_COUNT=1"' in wrapper
    assert '"GIT_CONFIG_KEY_0=safe.directory"' in wrapper
    assert '"GIT_CONFIG_VALUE_0=$project_dir"' in wrapper
    assert "git config --global" not in wrapper


def test_isolated_wrapper_can_handle_signals_while_waiting_for_agent():
    from pathlib import Path

    wrapper = Path("scripts/openace-run-as.sh").read_text(encoding="utf-8")

    assert "trap cleanup_isolated EXIT" in wrapper
    assert "trap 'exit 129' HUP" in wrapper
    assert "trap 'exit 130' INT" in wrapper
    assert "trap 'exit 143' TERM" in wrapper
    assert '"GIT_CONFIG_VALUE_0=$project_dir" "$@" <&0 9>&- &' in wrapper
    assert "agent_child_pid=$!" in wrapper
    assert 'wait "$agent_child_pid"' in wrapper


def test_background_child_does_not_inherit_acl_lock(tmp_path):
    """A SIGKILLed wrapper must not strand fd 9 in its live child."""
    if not shutil.which("flock"):
        pytest.skip("flock is required for the launcher lock regression")

    lock_path = tmp_path / "agent.lock"
    child_pid_path = tmp_path / "child.pid"
    wrapper = subprocess.Popen(
        [
            "bash",
            "-c",
            (
                'exec 9>"$1"; flock -x 9; '
                'sleep 30 9>&- & child=$!; printf "%s" "$child" > "$2"; wait "$child"'
            ),
            "openace-lock-test",
            str(lock_path),
            str(child_pid_path),
        ]
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if child_pid_path.exists() and child_pid_path.read_text().strip():
                child_pid = int(child_pid_path.read_text().strip())
                break
            time.sleep(0.05)
        assert child_pid > 0

        wrapper.kill()
        wrapper.wait(timeout=5)
        acquired = subprocess.run(
            ["flock", "-w", "1", str(lock_path), "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert acquired.returncode == 0
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_isolated_wrapper_normalizes_recovered_acls_before_git_baseline():
    """Launcher ACL recovery precedes exact signature/ACL verification.

    The durable registry carries the exact ACL snapshot, cleanup runs before
    recovery, and the next baseline is published before any new ACL grant.
    """
    from pathlib import Path

    wrapper = Path("scripts/openace-run-as.sh").read_text(encoding="utf-8")

    signature_registry = 'signature_registry="/run/openace-agent-git-signature-${target_uid}"'
    recovery = '"$previous_git_acl_snapshot"; then'
    current_baseline = 'git_entry_before="$(git_entry_signature "$project_dir")"'
    current_acl_baseline = 'git_acl_before="$(git_entry_acl_snapshot "$project_dir")"'
    first_acl_grant = 'setfacl -m "u:${target_user}:x" "$parent"'
    final_verification = (
        'if ! verify_and_restore_git_entry "$project_dir" "$git_entry_before" "$git_acl_before";'
    )

    assert signature_registry in wrapper
    assert 'previous_git_acl_snapshot="$(sed -n \'3p\' "$signature_registry")"' in wrapper
    assert wrapper.index("cleanup_isolated\n") < wrapper.index(recovery)
    assert wrapper.index(recovery) < wrapper.index(current_baseline)
    assert wrapper.index(current_baseline) < wrapper.index(first_acl_grant)
    assert wrapper.index(current_acl_baseline) < wrapper.index(first_acl_grant)
    assert wrapper.index("printf '%s\\n%s\\n%s\\n' \\") < wrapper.index(first_acl_grant)
    assert wrapper.index("cleanup_isolated\n", wrapper.index('wait "$agent_child_pid"')) < (
        wrapper.index(final_verification)
    )
    assert wrapper.index(final_verification) < wrapper.index(
        'rm -f "$signature_registry"', wrapper.index(final_verification)
    )


def test_git_entry_acl_restore_preserves_exact_integrity(tmp_path):
    """Only launcher-shaped mask churn is restored; real changes fail."""
    if sys.platform != "linux":
        pytest.skip("GNU stat signature regression is Linux-specific")
    if not shutil.which("setfacl"):
        pytest.skip("setfacl is required for the ACL mask regression")

    from pathlib import Path
    from textwrap import dedent

    wrapper = Path("scripts/openace-run-as.sh").read_text(encoding="utf-8")
    function_start = wrapper.index("    normalize_group_class_signature() {")
    function_end = function_start
    for _ in range(6):
        function_end = wrapper.index("\n    }\n", function_end + 1) + len("\n    }")
    signature_functions = dedent(wrapper[function_start:function_end])
    project = tmp_path / "project"
    project.mkdir()
    git_entry = project / ".git"
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")

    def shell_value(expression: str) -> str:
        result = subprocess.run(
            ["bash", "-c", f"{signature_functions}\n{expression}", "signature", project],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def signature() -> str:
        return shell_value('git_entry_signature "$1"')

    def acl_snapshot() -> str:
        return shell_value('git_entry_acl_snapshot "$1"')

    def verify(expected_signature: str, expected_acl: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "bash",
                "-c",
                f'{signature_functions}\nverify_and_restore_git_entry "$1" "$2" "$3"',
                "verify",
                project,
                expected_signature,
                expected_acl,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    # Extended ACL: launcher mask churn is restored to the exact baseline.
    subprocess.run(
        ["setfacl", "-m", f"u:{os.getuid()}:rwx,m::rw", str(git_entry)],
        check=True,
    )
    git_entry.chmod(0o664)
    baseline = signature()
    baseline_acl = acl_snapshot()
    git_entry.chmod(0o674)
    assert signature() != baseline
    assert verify(baseline, baseline_acl).returncode == 0
    assert signature() == baseline
    assert acl_snapshot() == baseline_acl

    # Owner/other permissions, content, inode, and non-mask ACL changes fail.
    git_entry.chmod(0o675)
    assert verify(baseline, baseline_acl).returncode != 0
    git_entry.chmod(0o664)
    git_entry.write_text("gitdir: /tampered/metadata\n", encoding="utf-8")
    assert verify(baseline, baseline_acl).returncode != 0
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    subprocess.run(["setfacl", "-m", "g::rwx", str(git_entry)], check=True)
    assert verify(baseline, baseline_acl).returncode != 0
    subprocess.run(
        ["setfacl", "-n", "--set-file=-", str(git_entry)],
        input=base64.b64decode(baseline_acl),
        check=True,
    )
    git_entry.unlink()
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    git_entry.chmod(0o664)
    assert verify(baseline, baseline_acl).returncode != 0

    # Plain ACL -> launcher mask-only ACL is restored exactly, while a plain
    # Unix group-mode change (group::, not mask::) is rejected.
    git_entry.unlink()
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    subprocess.run(["setfacl", "-b", str(git_entry)], check=True)
    git_entry.chmod(0o600)
    plain_baseline = signature()
    plain_acl = acl_snapshot()
    subprocess.run(["setfacl", "-m", f"u:{os.getuid()}:r", str(git_entry)], check=True)
    subprocess.run(["setfacl", "-x", f"u:{os.getuid()}", str(git_entry)], check=True)
    assert verify(plain_baseline, plain_acl).returncode == 0
    assert signature() == plain_baseline
    assert acl_snapshot() == plain_acl

    git_entry.chmod(0o640)
    assert verify(plain_baseline, plain_acl).returncode != 0

    # Legacy two-line registries get one restricted group-class-compatible
    # recovery, then the caller immediately writes the exact ACL-aware format.
    subprocess.run(
        ["setfacl", "-n", "--set-file=-", str(git_entry)],
        input="user::rw-\ngroup::---\nother::---\n",
        text=True,
        check=True,
    )
    git_entry.chmod(0o674)
    legacy_expected = signature().replace(":674:", ":664:")
    assert verify(legacy_expected, "").returncode != 0
    subprocess.run(
        ["setfacl", "-m", f"u:{os.getuid()}:rwx,m::rwx", str(git_entry)],
        check=True,
    )
    assert verify(legacy_expected, "").returncode == 0


def test_background_wait_keeps_runner_stdin_until_eof():
    import subprocess

    result = subprocess.run(
        [
            "bash",
            "-c",
            '{ IFS= read -r line; printf "received=%s" "$line"; } <&0 & ' 'child=$!; wait "$child"',
        ],
        input="hello-from-runner\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "received=hello-from-runner"


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

        # Wrapper invocation: sudo -n -u root <wrapper> --isolated <user> <dir> <cmd...>
        assert cmd[:7] == [
            "sudo",
            "-n",
            "-u",
            "root",
            agent_runner._OPENACE_RUN_AS,
            "--isolated",
            "rhuang",
        ]
        assert cmd[7] == project  # project dir passed to wrapper
        assert cmd[8:] == base_cmd  # original command appended verbatim
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
        assert cmd[8:] == base_cmd
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

    # ── Regression (PR #1467 review comment 2) ────────────────────────────
    # _build_agent_env encodes user_id into the proxy token; the quota/
    # accounting layer reads user_id back out of the token. If user_id is None
    # it falls back to 0, which silently bills usage to a nonexistent account
    # and bypasses per-user quotas. The single-shot path (codex/openclaw) used
    # to pass user_id=None even though _run_local had the real id in hand.
    # These tests pin the wiring: real user_id → generate_proxy_token(user_id=…).

    def test_real_user_id_passed_to_token(self, monkeypatch):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        fake_proxy = self._setup_proxy(monkeypatch)
        adapter = MagicMock()
        adapter.get_env_vars.return_value = {"OPENAI_API_KEY": "tok"}

        AutonomousAgentRunner._build_agent_env(
            adapter, "codex", user_id=42, session_id="s", model=""
        )

        _args, kwargs = fake_proxy.generate_proxy_token.call_args
        assert kwargs["user_id"] == 42  # not 0

    def test_none_user_id_defaults_to_zero(self, monkeypatch):
        # Sanity: the explicit fallback still produces user_id=0 (so callers
        # that genuinely have no user — e.g. ad-hoc dev runs — don't crash).
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        fake_proxy = self._setup_proxy(monkeypatch)
        adapter = MagicMock()
        adapter.get_env_vars.return_value = {"OPENAI_API_KEY": "tok"}

        AutonomousAgentRunner._build_agent_env(
            adapter, "codex", user_id=None, session_id="s", model=""
        )

        _args, kwargs = fake_proxy.generate_proxy_token.call_args
        assert kwargs["user_id"] == 0


# ── _run_single_shot user_id propagation (PR #1467 comment 2) ────────────


class TestSingleShotUserIdPropagation:
    """_run_local must forward user_id to _run_single_shot, which must forward
    it to _build_agent_env. Verified by patching _build_agent_env on the
    instance and asserting the kwargs it received carry the real user_id
    (not 0)."""

    def _make_runner(self):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        return AutonomousAgentRunner.__new__(AutonomousAgentRunner)

    def test_single_shot_passes_real_user_id_to_env(self, monkeypatch):
        import sys
        import types

        runner = self._make_runner()
        captured = {}

        def fake_build_env(adapter, cli_tool, user_id, session_id, model):
            captured["user_id"] = user_id
            return {"PATH": "/bin"}

        # _build_agent_env is a staticmethod; replace via the class.
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"
            "._build_agent_env",
            staticmethod(fake_build_env),
        )
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"
            "._wrap_agent_cmd",
            staticmethod(lambda cmd, project_path, system_account: (cmd, None)),
        )

        adapter = MagicMock()
        adapter.get_executable_name.return_value = "codex"
        adapter.build_single_shot_args.return_value = ["codex", "--prompt", "x"]
        # _run_single_shot lazily does `from cli_adapters import get_adapter`.
        # Inject a stub module so the import resolves without the real
        # remote-agent package on sys.path.
        fake_mod = types.ModuleType("cli_adapters")
        fake_mod.get_adapter = lambda name: adapter
        monkeypatch.setitem(sys.modules, "cli_adapters", fake_mod)
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.agent_runner.shutil.which",
            lambda exe: "/usr/local/bin/codex",
        )

        import app.modules.workspace.autonomous.agent_runner as mod

        completed = MagicMock(stdout="", stderr="", returncode=0)
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: completed)

        runner._run_single_shot(
            session_id="s",
            cli_tool="codex",
            model="",
            project_path="/tmp/repo",
            prompt="do work",
            timeout=5,
            workflow_id="wf",
            user_id=77,
        )

        assert captured["user_id"] == 77  # real user, not 0


# ── _get_gh() rerun path fallback (Issue #1395 rerun regression) ─────────


class TestGetGhRerunFallback:
    """On rerun, a worktree-strategy workflow may carry a stale
    ``worktree_path`` in the DB whose dir was removed by a prior failure's
    cleanup. ``_get_gh()`` previously bound ``GitHubOps`` to that stale path
    unconditionally (``worktree_path or project_path``), making every
    ``git -C <stale>`` fail with ENOENT during preparation. It must fall back
    to ``project_path`` when the worktree dir is gone, and rebind to the
    worktree once preparation recreates it."""

    def _make_orchestrator(self, wf_data):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf_data
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo
            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()
            return orch

    def _wf(self, **overrides):
        base = {
            "workflow_id": "wf-1395",
            "user_id": 1,
            "project_path": "/home/rhuang/open-ace",
            "worktree_path": "/home/rhuang/auto-dev-dead",
            "branch_strategy": "worktree",
            "branch_name": "auto-dev/dead",
        }
        base.update(overrides)
        return base

    def test_falls_back_to_project_when_worktree_dir_missing(self, monkeypatch):
        # Stale worktree_path in DB, dir gone → _get_gh must use project_path.
        orch = self._make_orchestrator(self._wf())

        # path_exists_as_user → False (worktree dir doesn't exist)
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.GitHubOps.path_exists_as_user",
            lambda self, path, **kw: False,
        )

        gh = orch._get_gh()
        assert gh.repo_path == "/home/rhuang/open-ace"  # project_path, not stale

    def test_uses_worktree_when_dir_exists(self, monkeypatch):
        # Worktree dir present → _get_gh binds to worktree_path.
        orch = self._make_orchestrator(self._wf())

        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.GitHubOps.path_exists_as_user",
            lambda self, path, **kw: True,
        )

        gh = orch._get_gh()
        assert gh.repo_path == "/home/rhuang/auto-dev-dead"  # worktree_path

    def test_no_worktree_path_uses_project_path(self, monkeypatch):
        # Empty worktree_path (e.g. merge cleanup cleared it) → project_path.
        orch = self._make_orchestrator(self._wf(worktree_path=""))

        called = []

        def _probe(self, path, **kw):
            called.append(path)
            return False

        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.GitHubOps.path_exists_as_user",
            _probe,
        )

        gh = orch._get_gh()
        assert gh.repo_path == "/home/rhuang/open-ace"
        # Must NOT probe an empty worktree path.
        assert called == []

    def test_cached_gh_not_rebuilt_until_reset(self, monkeypatch):
        # After the first _get_gh() caches a gh bound to project_path, a
        # second call returns the same instance even if the worktree later
        # appears — unless self._gh is reset (preparation does this after
        # creating the worktree).
        orch = self._make_orchestrator(self._wf())
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.GitHubOps.path_exists_as_user",
            lambda self, path, **kw: False,
        )

        first = orch._get_gh()
        second = orch._get_gh()
        assert first is second  # cached

        # Simulate preparation finishing: reset cache, worktree now exists.
        orch._gh = None
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.github_ops.GitHubOps.path_exists_as_user",
            lambda self, path, **kw: True,
        )
        third = orch._get_gh()
        assert third.repo_path == "/home/rhuang/auto-dev-dead"  # rebound
        assert third is not first


# ── run_agent_task session reactivation (proxy 401 on resumed line) ──────


class TestRunAgentTaskSessionReactivation:
    """A resumed session line (main/review/test) may carry a completed/error
    status from a prior run. The LLM proxy token validator requires
    agent_sessions.status in (active, paused), so run_agent_task must
    reactivate the row before any proxy-token-bearing env is built. Without
    this, the second call on a line 401s with "session not active"."""

    def _make_runner(self, monkeypatch):
        from app.modules.workspace.autonomous.agent_runner import (
            AgentTaskResult,
            AutonomousAgentRunner,
        )

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        runner.session_manager = MagicMock()

        # Stub _run_local so no real CLI launches; return a minimal success.
        def fake_run_local(self, **kwargs):
            return AgentTaskResult(session_id=kwargs.get("session_id", "s"), success=True)

        monkeypatch.setattr(
            "app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner._run_local",
            fake_run_local,
        )
        return runner

    def test_completed_session_reactivated_to_active(self, monkeypatch):
        runner = self._make_runner(monkeypatch)
        # Session exists, status=completed (prior run finished).
        runner.session_manager.get_session.return_value = MagicMock(status="completed")

        runner.run_agent_task(
            workflow_id="wf",
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="x",
            session_id="sess-completed",
        )

        # The FIRST update_session_fields call is the reactivation (active +
        # clear stale timestamps); a later post-run call writes the terminal
        # status. Assert the first.
        calls = runner.session_manager.update_session_fields.call_args_list
        assert calls[0] == (
            ("sess-completed", {"status": "active", "completed_at": None, "paused_at": None}),
            {},
        )

    def test_error_session_reactivated_to_active(self, monkeypatch):
        runner = self._make_runner(monkeypatch)
        runner.session_manager.get_session.return_value = MagicMock(status="error")

        runner.run_agent_task(
            workflow_id="wf",
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="x",
            session_id="sess-error",
        )

        calls = runner.session_manager.update_session_fields.call_args_list
        assert calls[0] == (
            ("sess-error", {"status": "active", "completed_at": None, "paused_at": None}),
            {},
        )

    def test_active_session_no_reactivation_call(self, monkeypatch):
        runner = self._make_runner(monkeypatch)
        runner.session_manager.get_session.return_value = MagicMock(status="active")

        runner.run_agent_task(
            workflow_id="wf",
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="x",
            session_id="sess-active",
        )

        # No reactivation; only the post-run terminal-status write happens.
        calls = runner.session_manager.update_session_fields.call_args_list
        assert all(c.args[1] != {"status": "active"} for c in calls)

    def test_paused_session_no_reactivation_call(self, monkeypatch):
        runner = self._make_runner(monkeypatch)
        runner.session_manager.get_session.return_value = MagicMock(status="paused")

        runner.run_agent_task(
            workflow_id="wf",
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="x",
            session_id="sess-paused",
        )

        calls = runner.session_manager.update_session_fields.call_args_list
        assert all(c.args[1] != {"status": "active"} for c in calls)

    def test_missing_session_no_reactivation(self, monkeypatch):
        runner = self._make_runner(monkeypatch)
        # Session row absent (e.g. late-creating tools) — must not crash.
        runner.session_manager.get_session.return_value = None

        runner.run_agent_task(
            workflow_id="wf",
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="x",
            session_id="sess-missing",
        )

        # Only the post-run write (if any) happens; no reactivation.
        calls = runner.session_manager.update_session_fields.call_args_list
        assert all(c.args[1] != {"status": "active"} for c in calls)
