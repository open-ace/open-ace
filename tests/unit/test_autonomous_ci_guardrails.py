"""Regression tests for autonomous CI/development guardrails."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


@pytest.mark.parametrize(
    ("return_code", "error_code", "stderr", "expected"),
    [
        (
            126,
            "",
            "mutating git commands are reserved for the Open ACE orchestrator",
            "command guard rejected",
        ),
        (1, "", "fatal: detected dubious ownership", "safe-directory validation failed"),
        (
            67,
            "",
            "openace-run-as: isolated agent must differ from project owner",
            "rejected by safety checks",
        ),
        (
            68,
            "",
            "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed",
            "Protected .git entry changed",
        ),
        (23, "", "provider-key=must-not-leak", "exit code 23"),
        (
            64,
            "",
            "ordinary child usage error",
            "exit code 64",
        ),
        (
            1,
            "",
            "API Error: model not found",
            "exit code 1",
        ),
        (
            0,
            "",
            "",
            "Failed to detect Claude sidebar session JSONL",
        ),
    ],
)
def test_sidebar_start_failure_is_actionable_without_raw_stderr(
    return_code, error_code, stderr, expected
):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    message = AutonomousAgentRunner._classify_sidebar_start_failure(return_code, error_code, stderr)

    assert expected in message
    assert "must-not-leak" not in message
    assert "ordinary child usage error" not in message
    assert "model not found" not in message


def test_actions_job_log_prefers_rest_api_without_run_cache():
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps("/tmp/repo")
    gh._repo_slug = "open-ace/open-ace"
    gh._repo_host = "github.com"
    gh._owner_repo = "open-ace/open-ace"
    gh._owner_repo_resolved = True
    calls = []

    def fake_run(args, check=True, repo_scoped=True):
        calls.append((args, repo_scoped))
        return MagicMock(
            returncode=0,
            stdout="mypy................................Failed\napp/x.py:2: error: broken\n",
            stderr="",
        )

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "lint",
                "link": "https://github.com/open-ace/open-ace/actions/runs/123/job/456",
            }
        )

    assert "app/x.py" in excerpt
    assert calls == [(["api", "repos/open-ace/open-ace/actions/jobs/456/logs"], False)]


def test_actions_job_log_uses_ghes_hostname_without_run_cache():
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps("/tmp/repo")
    gh._repo_slug = "team/project"
    gh._repo_host = "gh.example.com"
    gh._owner_repo = "team/project"
    gh._owner_repo_resolved = True
    calls = []

    def fake_run(args, check=True, repo_scoped=True):
        calls.append((args, repo_scoped))
        return MagicMock(returncode=0, stdout="pytest failed\n1 failed\n", stderr="")

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "test",
                "link": "https://gh.example.com/team/project/actions/runs/12/job/34",
            }
        )

    assert "failed" in excerpt
    assert calls == [
        (
            ["api", "--hostname", "gh.example.com", "repos/team/project/actions/jobs/34/logs"],
            False,
        )
    ]


def test_runtime_contract_separates_host_from_project(tmp_path):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\n', encoding="utf-8"
    )
    contract = AutonomousOrchestrator._project_runtime_contract(str(tmp_path))

    assert ">=3.10" in contract
    assert "不能通过全仓改写" in contract


def test_scope_guard_blocks_repository_wide_rewrite(monkeypatch):
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 3)
    reason = orchestrator_module.AutonomousOrchestrator._scope_violation(
        ["app/a.py", "app/b.py", "app/c.py", "app/d.py"]
    )
    assert "4 files changed" in reason
    assert "limit 3" in reason


def test_cumulative_scope_guard_catches_multi_round_growth(monkeypatch):
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 3)
    orch = orchestrator_module.AutonomousOrchestrator.__new__(
        orchestrator_module.AutonomousOrchestrator
    )
    gh = MagicMock()
    gh.get_changed_files.side_effect = [
        ["app/a.py", "app/b.py"],
        ["app/a.py", "app/b.py", "app/c.py", "app/d.py"],
    ]

    reason = orch._validate_autonomous_change_scope(
        gh, {"base_commit_sha": "base"}, "round-base", "head"
    )

    assert "Cumulative branch" in reason
    assert gh.get_changed_files.call_args_list[1].args == ("base", "head")


def test_cumulative_scope_guard_derives_immutable_base_when_main_moved(monkeypatch):
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 3)
    orch = orchestrator_module.AutonomousOrchestrator.__new__(
        orchestrator_module.AutonomousOrchestrator
    )
    orch._update_workflow = MagicMock()
    gh = MagicMock()
    gh._run_git.return_value = MagicMock(returncode=0, stdout="branch-point\n")
    gh.get_changed_files.side_effect = [
        ["app/a.py"],
        ["app/a.py", "app/b.py"],
    ]

    reason = orch._validate_autonomous_change_scope(gh, {}, "round-base", "head")

    assert reason == ""
    gh._run_git.assert_called_once_with(["merge-base", "head", "origin/main"], check=False)
    assert gh.get_changed_files.call_args_list[1].args == ("branch-point", "head")
    orch._update_workflow.assert_called_once_with({"base_commit_sha": "branch-point"})


def test_cumulative_scope_guard_fails_closed_when_base_cannot_be_derived():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._update_workflow = MagicMock()
    gh = MagicMock()
    gh._run_git.return_value = MagicMock(returncode=1, stdout="")

    reason = orch._validate_autonomous_change_scope(gh, {}, "round-base", "head")

    assert "missing immutable base commit" in reason
    gh.get_changed_files.assert_not_called()
    orch._update_workflow.assert_not_called()


def test_ci_repair_scope_rejection_prevents_push():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    gh = MagicMock()
    gh.get_current_commit.return_value = "head"
    result = AutonomousOrchestrator._detect_and_push_ci_repair_changes(
        gh,
        "base",
        1,
        "auto-dev/test",
        42,
        scope_validator=lambda before, after: "scope rejected",
    )

    assert result == ("head", True, "scope rejected")
    gh.git_push.assert_not_called()
    gh.reset_hard_to.assert_called_once_with("head")


def test_ci_repair_scope_rejection_preserves_prior_unpushed_head():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    gh = MagicMock()
    gh.get_current_commit.side_effect = ["local-B", "rejected-C"]
    gh.has_uncommitted_changes.return_value = True

    result = AutonomousOrchestrator._detect_and_push_ci_repair_changes(
        gh,
        "remote-A",
        2,
        "auto-dev/test",
        42,
        scope_validator=lambda before, after: "scope rejected",
    )

    assert result == ("rejected-C", True, "scope rejected")
    gh.reset_hard_to.assert_called_once_with("local-B")
    gh.git_push.assert_not_called()


def test_runtime_gate_blocks_incompatible_host_without_provisioner(tmp_path):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=99.0"\n', encoding="utf-8"
    )
    with patch("app.modules.workspace.autonomous.orchestrator.shutil.which", return_value=None):
        reason = AutonomousOrchestrator._runtime_environment_gate(str(tmp_path))

    assert "Environment mismatch" in reason
    assert "compatibility rewrites are blocked" in reason


def test_runtime_selection_uses_compatible_service_python_with_cross_user_repo(tmp_path):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\n', encoding="utf-8"
    )
    gh = MagicMock(system_account="repo-owner")

    command, reason = AutonomousOrchestrator._select_project_python_runtime(str(tmp_path), gh)

    assert command == [sys.executable]
    assert reason == ""


def test_agent_environment_binds_python_and_git_guards(monkeypatch, tmp_path):
    import json

    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))
    monkeypatch.setenv("SKIP", "all-hooks")

    adapter = MagicMock()
    adapter.get_env_vars.return_value = {}
    env = agent_runner.AutonomousAgentRunner._build_agent_env(
        adapter,
        "claude-code",
        None,
        "session",
        "",
        ["/opt/python3.11/bin/python"],
    )

    assert json.loads(env["OPENACE_PYTHON_COMMAND"]) == ["/opt/python3.11/bin/python"]
    assert env["PATH"].split(":", 1)[0] == str(guard_dir)
    assert env["OPENACE_REAL_GIT"]
    assert env["GH_CONFIG_DIR"] == "/var/empty/openace-autonomous-gh"
    assert "GH_TOKEN" not in env
    assert "SKIP" not in env


def test_agent_guard_bin_falls_back_to_source_when_install_is_incomplete(monkeypatch, tmp_path):
    from pathlib import Path

    from app.modules.workspace.autonomous import agent_runner

    incomplete_install = tmp_path / "agent-bin"
    incomplete_install.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES[:-1]:
        (incomplete_install / name).write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(incomplete_install))

    resolved = agent_runner.AutonomousAgentRunner._resolve_agent_guard_bin()

    assert resolved == str(Path(agent_runner.__file__).with_name("agent_bin"))


def test_agent_guard_bin_canonicalizes_packaged_directory_symlink(monkeypatch, tmp_path):
    from app.modules.workspace.autonomous import agent_runner

    installed_guard_dir = tmp_path / "installed-agent-bin"
    installed_guard_dir.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = installed_guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    configured_link = tmp_path / "configured-agent-bin"
    configured_link.symlink_to(installed_guard_dir, target_is_directory=True)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(configured_link))

    resolved = agent_runner.AutonomousAgentRunner._resolve_agent_guard_bin()

    assert resolved == str(installed_guard_dir.resolve())


def test_cross_user_launch_rejects_resolved_source_fallback(monkeypatch, tmp_path):
    from app.modules.workspace.autonomous import agent_runner

    missing_install = tmp_path / "missing-agent-bin"
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(missing_install))
    source_fallback = agent_runner.AutonomousAgentRunner._resolve_agent_guard_bin()
    env = {"PATH": f"{source_fallback}:/usr/bin"}

    with (
        patch.object(agent_runner.AutonomousAgentRunner, "_is_cross_user", return_value=True),
        pytest.raises(RuntimeError, match="packaged agent command guards"),
    ):
        agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            ["/usr/bin/pre-commit"], "/private/repo", "openace-agent", env
        )


def test_cross_user_launch_preserves_nonsecret_guard_environment(monkeypatch, tmp_path):
    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))

    env = {
        "PATH": f"{guard_dir}:/usr/bin",
        "OPENACE_REAL_GIT": "/usr/bin/git",
        "OPENACE_PYTHON_COMMAND": '["/usr/bin/python3"]',
        "GH_CONFIG_DIR": "/var/empty/openace-autonomous-gh",
        "GIT_TERMINAL_PROMPT": "0",
    }
    with (
        patch.object(agent_runner.AutonomousAgentRunner, "_is_cross_user", return_value=True),
        patch.object(
            agent_runner.AutonomousAgentRunner,
            "_validate_cross_user_guard_bin",
        ),
    ):
        command, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            ["/usr/bin/claude"], "/private/repo", "repo-user", env
        )

    assert cwd is None
    assert "--isolated" in command
    assert "/usr/bin/env" in command
    assert f"PATH={guard_dir}:/usr/bin" in command
    assert "OPENACE_REAL_GIT=/usr/bin/git" in command
    expected_cache = str(agent_runner.AutonomousAgentRunner._resolve_home_dir("repo-user"))
    assert f"OPENACE_GIT_CACHE_ROOT={expected_cache}/.cache/pre-commit" in command


def test_terminal_result_closes_stream_json_stdin():
    from types import SimpleNamespace

    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession

    class FakeStdout:
        def __init__(self):
            self.lines = [json.dumps({"type": "result", "session_id": "cli-session"}).encode()]

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

    class FakeStdin:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = None
    runner._capture_cli_session_id = lambda *_args: "cli-session"
    runner._sync_sidebar_session_totals = lambda *_args, **_kwargs: None
    runner._resolve_sidebar_session = lambda *_args, **_kwargs: "cli-session"
    process = SimpleNamespace(stdout=FakeStdout(), stdin=FakeStdin(), returncode=None)
    session = _LocalSession(session_id="tracking-session", process=process)

    with patch(
        "app.modules.workspace.autonomous.agent_runner._extract_stream_usage",
        return_value=None,
    ):
        runner._read_stdout(session)

    assert session.completed.is_set()
    assert process.stdin.closed


def test_cross_user_launch_rejects_source_tree_guard_path():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    env = {"PATH": "/private/service/app/agent_bin:/usr/bin"}
    with patch.object(AutonomousAgentRunner, "_is_cross_user", return_value=True):
        with pytest.raises(RuntimeError, match="packaged agent command guards"):
            AutonomousAgentRunner._wrap_agent_cmd(
                ["/usr/bin/claude"], "/private/repo", "openace-agent", env
            )


def test_cross_user_guard_rejects_root_group_only_permissions(monkeypatch, tmp_path):
    import stat
    from types import SimpleNamespace

    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir(mode=0o750)
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o750)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))
    real_stat = agent_runner.os.stat

    def root_owned_group_only(path, *, follow_symlinks=True):
        result = real_stat(path, follow_symlinks=follow_symlinks)
        kind = stat.S_IFDIR if stat.S_ISDIR(result.st_mode) else stat.S_IFREG
        return SimpleNamespace(st_uid=0, st_mode=kind | 0o750)

    monkeypatch.setattr(agent_runner.os, "stat", root_owned_group_only)

    with pytest.raises(RuntimeError, match="Unsafe packaged agent guard directory"):
        agent_runner.AutonomousAgentRunner._validate_cross_user_guard_bin(
            {"PATH": f"{guard_dir}:/usr/bin"}
        )


def test_cross_user_guard_accepts_root_owned_world_executable_install(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir(mode=0o755)
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))
    real_stat = agent_runner.os.stat

    def root_owned(path, *, follow_symlinks=True):
        result = real_stat(path, follow_symlinks=follow_symlinks)
        return SimpleNamespace(st_uid=0, st_mode=result.st_mode)

    monkeypatch.setattr(agent_runner.os, "stat", root_owned)

    agent_runner.AutonomousAgentRunner._validate_cross_user_guard_bin(
        {"PATH": f"{guard_dir}:/usr/bin"}
    )


def test_local_agent_fails_closed_without_trusted_repo_snapshot():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-no-snapshot"
    orch._runner = MagicMock()
    orch._runner._uses_sidebar_session_source.return_value = False
    orch.repo = MagicMock()
    orch._resolve_session_line = MagicMock(return_value=("session", "", False))
    orch._resolve_effective_repo_context = MagicMock(
        return_value={"repo_path": "/private/repo", "project_path": "/private/repo"}
    )
    orch._resolve_system_account = MagicMock(return_value="repo-owner")
    orch._select_project_python_runtime = MagicMock(return_value=(["python3"], ""))
    orch._get_gh = MagicMock(return_value=None)
    orch._snapshot_repo_context = MagicMock(return_value=None)

    result = orch._run_agent(
        wf={"workspace_type": "local", "project_path": "/private/repo", "user_id": 1},
        project_path="/private/repo",
        workspace_type="local",
        cli_tool="claude-code",
        model="test",
        prompt="test",
        permission_mode="auto-edit",
        allowed_tools=[],
    )

    assert not result.success
    assert result.error_code == "repo_integrity_violation"
    orch._runner.run_agent_task.assert_not_called()


def test_isolated_agent_account_rejects_root(monkeypatch):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    monkeypatch.setenv("OPENACE_AUTONOMOUS_AGENT_ACCOUNT", "root")

    try:
        AutonomousOrchestrator._resolve_isolated_agent_account()
    except RuntimeError as error:
        assert "UID 0" in str(error)
    else:
        raise AssertionError("UID 0 autonomous agent account was accepted")


def test_isolated_agent_account_rejects_service_principal(monkeypatch):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    monkeypatch.setenv("OPENACE_AUTONOMOUS_AGENT_ACCOUNT", "openace")
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.orchestrator.pwd.getpwnam",
        lambda _name: MagicMock(pw_uid=501, pw_gid=20),
    )
    monkeypatch.setattr("app.modules.workspace.autonomous.orchestrator.os.getuid", lambda: 501)

    with pytest.raises(RuntimeError, match="must differ from the Open ACE service account"):
        AutonomousOrchestrator._resolve_isolated_agent_account()


def test_trusted_git_context_rejects_replaced_git_directory():
    from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError

    gh = GitHubOps("/tmp/trusted-repo")
    gh._trusted_git_dir = "/protected/git-dir"
    gh._trusted_git_identity = "1:10"
    gh._trusted_common_dir = "/protected/common-dir"
    gh._trusted_common_identity = "1:20"
    gh.get_path_identity = MagicMock(side_effect=["1:99"])

    with patch("app.modules.workspace.autonomous.github_ops.subprocess.run") as run:
        try:
            gh._run_git(["push", "origin", "HEAD"])
        except GitHubOpsError as error:
            assert "identity changed" in str(error)
        else:
            raise AssertionError("replaced protected Git directory was accepted")

    run.assert_not_called()


def test_agent_command_policy_denies_push_and_mutating_pr_commands():
    from app.modules.workspace.autonomous.agent_runner import _is_forbidden_autonomous_command

    assert _is_forbidden_autonomous_command("Bash", {"command": "git push origin HEAD"})
    assert _is_forbidden_autonomous_command(
        "Bash", {"command": "/usr/bin/git -C /repo push --force"}
    )
    assert _is_forbidden_autonomous_command("Bash", {"command": "gh pr merge 42"})
    assert _is_forbidden_autonomous_command("Bash", {"command": "/usr/bin/python3 -m pytest"})
    assert not _is_forbidden_autonomous_command("Bash", {"command": "git status"})


def test_isolated_git_guard_allows_pre_commit_check_attr(tmp_path):
    """pre-commit's large-file hook needs this read-only plumbing command."""
    import os
    import subprocess
    from pathlib import Path

    real_git = tmp_path / "real-git"
    real_git.write_text(
        f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    real_git.chmod(0o755)
    guard = (
        Path(__file__).parents[2]
        / "app"
        / "modules"
        / "workspace"
        / "autonomous"
        / "agent_bin"
        / "git"
    )
    result = subprocess.run(
        [str(guard), "check-attr", "filter", "-z", "--stdin"],
        input="",
        capture_output=True,
        text=True,
        env={**os.environ, "OPENACE_REAL_GIT": str(real_git)},
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "check-attr filter -z --stdin"


def _fake_real_git(tmp_path):
    real_git = tmp_path / "real-git"
    real_git.write_text(
        f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    real_git.chmod(0o755)
    return real_git


def _source_git_guard():
    from pathlib import Path

    return (
        Path(__file__).parents[2]
        / "app"
        / "modules"
        / "workspace"
        / "autonomous"
        / "agent_bin"
        / "git"
    )


def test_isolated_git_guard_allows_pre_commit_cache_init(tmp_path):
    """Global -c options must not hide cache-scoped git init from the guard."""
    import os
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    cache_root = tmp_path / "agent-home" / ".cache" / "pre-commit"
    cache_root.mkdir(parents=True)
    target = cache_root / "repo123"
    result = subprocess.run(
        [
            str(_source_git_guard()),
            "-c",
            "core.useBuiltinFSMonitor=false",
            "init",
            "--template=",
            str(target),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OPENACE_REAL_GIT": str(_fake_real_git(tmp_path)),
            "OPENACE_GIT_CACHE_ROOT": str(cache_root),
            # openace-run-as clears the environment and injects this trusted
            # ownership exception for the validated workflow worktree.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(project),
        },
        check=False,
    )

    assert result.returncode == 0
    assert "init --template=" in result.stdout
    assert str(target) in result.stdout


def test_isolated_git_guard_allows_mutation_inside_pre_commit_cache(tmp_path):
    import os
    import subprocess

    cache_repo = tmp_path / "agent-home" / ".cache" / "pre-commit" / "repo123"
    cache_repo.mkdir(parents=True)
    result = subprocess.run(
        [str(_source_git_guard()), "remote", "add", "origin", "https://example.invalid/hook"],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OPENACE_REAL_GIT": str(_fake_real_git(tmp_path)),
            "OPENACE_GIT_CACHE_ROOT": str(cache_repo.parent),
        },
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip().startswith("remote add origin")


def test_isolated_git_guard_denies_cache_escape(tmp_path):
    import os
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    cache_repo = tmp_path / "agent-home" / ".cache" / "pre-commit" / "repo123"
    cache_repo.mkdir(parents=True)
    env = {
        **os.environ,
        "OPENACE_REAL_GIT": str(_fake_real_git(tmp_path)),
        "OPENACE_GIT_CACHE_ROOT": str(cache_repo.parent),
    }

    outside_init = subprocess.run(
        [str(_source_git_guard()), "init", str(project / "nested")],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    redirected_commit = subprocess.run(
        [str(_source_git_guard()), "-C", str(project), "commit", "-m", "escape"],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    separate_git_dir = subprocess.run(
        [
            str(_source_git_guard()),
            "init",
            f"--separate-git-dir={project / 'metadata'}",
            str(cache_repo / "nested"),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    env_redirect = subprocess.run(
        [str(_source_git_guard()), "commit", "-m", "escape"],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env={**env, "GIT_DIR": str(project / ".git")},
        check=False,
    )
    cache_cwd_init_escape = subprocess.run(
        [str(_source_git_guard()), "init", str(project / "escaped")],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    object_directory_escape = subprocess.run(
        [str(_source_git_guard()), "hash-object", "-w", "--stdin"],
        cwd=cache_repo,
        input="outside cache",
        capture_output=True,
        text=True,
        env={**env, "GIT_OBJECT_DIRECTORY": str(project / "objects")},
        check=False,
    )
    unsafe_config_escape = subprocess.run(
        [
            str(_source_git_guard()),
            "-c",
            f"core.worktree={project}",
            "checkout",
            "HEAD",
        ],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    clone_escape = subprocess.run(
        [
            str(_source_git_guard()),
            "clone",
            "https://example.invalid/hook",
            str(project / "clone"),
        ],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    global_config_escape = subprocess.run(
        [str(_source_git_guard()), "config", "--global", "user.name", "escape"],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    environment_config_escape = subprocess.run(
        [str(_source_git_guard()), "checkout", "HEAD"],
        cwd=cache_repo,
        capture_output=True,
        text=True,
        env={**env, "GIT_CONFIG_PARAMETERS": f"'core.worktree'='{project}'"},
        check=False,
    )

    assert outside_init.returncode == 126
    assert redirected_commit.returncode == 126
    assert separate_git_dir.returncode == 126
    assert env_redirect.returncode == 126
    assert cache_cwd_init_escape.returncode == 126
    assert object_directory_escape.returncode == 126
    assert unsafe_config_escape.returncode == 126
    assert clone_escape.returncode == 126
    assert global_config_escape.returncode == 126
    assert environment_config_escape.returncode == 126


def test_runner_preserves_tool_result_for_test_evidence():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    event = AutonomousAgentRunner._parse_single_shot_line(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "7 passed in 0.4s",
                        "is_error": False,
                    }
                ]
            },
        },
        "claude-code",
    )

    assert event == {
        "type": "tool_result",
        "tool_use_id": "tool-1",
        "text": "7 passed in 0.4s",
        "exit_code": None,
        "is_error": False,
    }


def test_claude_embedded_tool_use_pairs_with_test_result():
    from types import SimpleNamespace

    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    lines = [
        {
            "type": "assistant",
            "message": {
                "id": "msg-test",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-test",
                        "name": "Bash",
                        "input": {"command": "python -m pytest tests/issues/1891 -q"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-test",
                        "content": "65 passed in 3.2s",
                        "is_error": False,
                    }
                ]
            },
        },
    ]

    class FakeStdout:
        def __init__(self):
            self.lines = [json.dumps(line).encode() for line in lines]

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = None
    process = SimpleNamespace(stdout=FakeStdout(), returncode=None)
    session = _LocalSession(session_id="test-session", process=process)

    runner._read_stdout(session)

    assert session.tool_calls[0]["tool"]["name"] == "Bash"
    assert _has_passing_test_tool_result(session.event_log, "python")


def test_codex_command_execution_normalizes_tool_evidence():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        AutonomousAgentRunner._parse_single_shot_line(
            {
                "type": "item.started",
                "item": {
                    "id": "item-1",
                    "type": "command_execution",
                    "command": "pytest -q",
                },
            },
            "codex",
        ),
        AutonomousAgentRunner._parse_single_shot_line(
            {
                "type": "item.completed",
                "item": {
                    "id": "item-1",
                    "type": "command_execution",
                    "command": "pytest -q",
                    "aggregated_output": "42 passed, 0 failed in 1.2s",
                    "exit_code": 0,
                },
            },
            "codex",
        ),
    ]

    assert _has_passing_test_tool_result(events, "python")


def test_zcode_result_normalizes_tool_evidence():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    collector = _ZcodeResultCollector()
    collector.on_output(
        "session",
        json.dumps(
            {
                "type": "tool.updated",
                "data": {
                    "kind": "started",
                    "id": "z-1",
                    "toolName": "run_shell_command",
                    "input": {"command": "go test ./..."},
                },
            }
        ),
        "stdout",
        False,
    )
    collector.on_output(
        "session",
        json.dumps(
            {
                "type": "tool.updated",
                "data": {
                    "kind": "result",
                    "id": "z-1",
                    "result": {"output": "ok example/pkg 0.2s", "exitCode": 0},
                },
            }
        ),
        "stdout",
        False,
    )

    assert _has_passing_test_tool_result(collector.event_log, "go")


def test_test_evidence_requires_every_distinct_command_to_pass():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_use_id": "full",
        },
        {
            "type": "tool_result",
            "tool_use_id": "full",
            "text": "1 failed, 41 passed",
            "exit_code": 1,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_one.py"},
            "tool_use_id": "one",
        },
        {
            "type": "tool_result",
            "tool_use_id": "one",
            "text": "1 passed",
            "exit_code": 0,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


def test_test_evidence_treats_head_and_tail_as_same_pytest_rerun():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python -m pytest tests/unit/test_insights_service.py "
                    "-v --tb=short 2>&1 | head -100"
                )
            },
            "tool_use_id": "truncated",
        },
        {
            "type": "tool_result",
            "tool_use_id": "truncated",
            "text": "================ test session starts ================",
            "exit_code": 0,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python -m pytest tests/unit/test_insights_service.py "
                    "-v --tb=short 2>&1 | tail -20"
                )
            },
            "tool_use_id": "rerun",
        },
        {
            "type": "tool_result",
            "tool_use_id": "rerun",
            "text": "49 passed in 0.47s",
            "exit_code": 0,
        },
    ]

    assert _has_passing_test_tool_result(events, "python")


def test_test_evidence_accepts_later_passing_pytest_superset():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py tests/test_b.py -v"},
            "tool_use_id": "group-failed",
        },
        {
            "type": "tool_result",
            "tool_use_id": "group-failed",
            "text": "1 failed, 1 passed in 0.4s",
            "exit_code": 1,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python -m pytest tests/test_a.py tests/test_b.py tests/test_c.py "
                    "-v 2>&1 | tail -10"
                )
            },
            "tool_use_id": "final-matrix",
        },
        {
            "type": "tool_result",
            "tool_use_id": "final-matrix",
            "text": "3 passed in 0.8s",
            "exit_code": 0,
        },
    ]

    assert _has_passing_test_tool_result(events, "python")


def test_test_evidence_earlier_passing_superset_does_not_clear_later_failure():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py tests/test_b.py -q"},
            "tool_use_id": "matrix-passed",
        },
        {
            "type": "tool_result",
            "tool_use_id": "matrix-passed",
            "text": "2 passed in 0.4s",
            "exit_code": 0,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            "tool_use_id": "later-failure",
        },
        {
            "type": "tool_result",
            "tool_use_id": "later-failure",
            "text": "1 failed in 0.2s",
            "exit_code": 1,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


@pytest.mark.parametrize(
    "restricted_command",
    [
        "python -m pytest tests/test_a.py -q -k passing_case",
        "python -m pytest tests -q --ignore tests/test_a.py",
        "ONLY_FAST=1 python -m pytest tests/test_a.py -q",
    ],
)
def test_test_evidence_restricted_pass_does_not_cover_earlier_failure(
    restricted_command,
):
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            "tool_use_id": "file-failed",
        },
        {
            "type": "tool_result",
            "tool_use_id": "file-failed",
            "text": "1 failed, 1 passed in 0.4s",
            "exit_code": 1,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": restricted_command},
            "tool_use_id": "restricted-pass",
        },
        {
            "type": "tool_result",
            "tool_use_id": "restricted-pass",
            "text": "1 passed in 0.2s",
            "exit_code": 0,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


@pytest.mark.parametrize(
    ("failed_command", "passing_command"),
    [
        (
            "python3.10 -m pytest tests/test_a.py -q",
            "python3.12 -m pytest tests/test_a.py tests/test_b.py -q",
        ),
        (
            "pytest tests/test_a.py -q",
            "python -m pytest tests/test_a.py tests/test_b.py -q",
        ),
    ],
)
def test_test_evidence_different_pytest_context_does_not_cover_failure(
    failed_command, passing_command
):
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": failed_command},
            "tool_use_id": "failed-context",
        },
        {
            "type": "tool_result",
            "tool_use_id": "failed-context",
            "text": "1 failed, 1 passed in 0.4s",
            "exit_code": 1,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": passing_command},
            "tool_use_id": "passing-context",
        },
        {
            "type": "tool_result",
            "tool_use_id": "passing-context",
            "text": "3 passed in 0.3s",
            "exit_code": 0,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


@pytest.mark.parametrize(
    ("test_output", "test_exit_code", "expected"),
    [
        ("1 failed in 0.2s", 1, False),
        ("1 passed in 0.2s", 0, True),
    ],
)
def test_test_evidence_pairs_anonymous_results_with_all_tool_calls(
    test_output, test_exit_code, expected
):
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "test-summary.txt"},
        },
        {
            "type": "tool_result",
            "text": "Historical note: 1 passed",
            "exit_code": 0,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
        },
        {
            "type": "tool_result",
            "text": test_output,
            "exit_code": test_exit_code,
        },
    ]

    assert _has_passing_test_tool_result(events, "python") is expected


@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "type": "tool_use",
                "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            },
            {
                "type": "tool_use",
                "tool_name": "Read",
                "tool_input": {"file_path": "old-results.txt"},
            },
            {"type": "tool_result", "text": "Historical: 1 passed", "exit_code": 0},
        ],
        [
            {"type": "tool_result", "text": "Historical: 1 passed", "exit_code": 0},
            {
                "type": "tool_use",
                "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            },
        ],
    ],
)
def test_test_evidence_rejects_incomplete_or_out_of_order_anonymous_events(events):
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    assert not _has_passing_test_tool_result(events, "python")


@pytest.mark.parametrize("tool_id", ["named-run", None])
def test_test_evidence_new_invocation_invalidates_stale_pass(tool_id):
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    first_use = {
        "type": "tool_use",
        "tool_name": "Bash",
        "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
    }
    first_result = {
        "type": "tool_result",
        "text": "1 passed in 0.2s",
        "exit_code": 0,
    }
    second_use = {
        "type": "tool_use",
        "tool_name": "Bash",
        "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
    }
    if tool_id is not None:
        first_use["tool_use_id"] = tool_id
        first_result["tool_use_id"] = tool_id
        second_use["tool_use_id"] = tool_id

    assert not _has_passing_test_tool_result([first_use, first_result, second_use], "python")


def test_test_evidence_older_named_result_cannot_resolve_newer_invocation():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            "tool_use_id": "older",
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_a.py -q"},
            "tool_use_id": "newer",
        },
        {
            "type": "tool_result",
            "tool_use_id": "older",
            "text": "1 passed in 0.2s",
            "exit_code": 0,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


def test_test_evidence_targeted_pass_does_not_cover_failed_full_suite():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    events = [
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest -q"},
            "tool_use_id": "full-suite",
        },
        {
            "type": "tool_result",
            "tool_use_id": "full-suite",
            "text": "1 failed, 200 passed",
            "exit_code": 1,
        },
        {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_one.py -q"},
            "tool_use_id": "targeted",
        },
        {
            "type": "tool_result",
            "tool_use_id": "targeted",
            "text": "1 passed",
            "exit_code": 0,
        },
    ]

    assert not _has_passing_test_tool_result(events, "python")


def test_test_evidence_accepts_common_framework_success_summaries():
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    cases = [
        ("python -m unittest", "Ran 5 tests in 0.1s\n\nOK", "python"),
        ("go test ./...", "ok example/pkg 0.2s", "go"),
        (
            "mvn test",
            "Tests run: 5, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS",
            "java",
        ),
        ("mvn test", "[INFO] BUILD SUCCESS", "java"),
        ("./gradlew test", "BUILD SUCCESSFUL in 12s", "java"),
        ("cargo test", "test result: ok. 5 passed; 0 failed", "rust"),
    ]
    for index, (command, output, framework) in enumerate(cases):
        tool_id = f"tool-{index}"
        events = [
            {
                "type": "tool_use",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_use_id": tool_id,
            },
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "text": output,
                "exit_code": 0,
            },
        ]
        assert _has_passing_test_tool_result(events, framework), command


def test_remote_messages_preserve_structured_test_evidence():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result
    from app.modules.workspace.session_manager import SessionMessage

    messages = [
        SessionMessage(
            role="assistant",
            content="Running tests",
            content_blocks=[
                {"type": "text", "text": "Running tests"},
                {
                    "type": "tool_use",
                    "id": "remote-test-1",
                    "name": "Bash",
                    "input": {"command": "python -m pytest tests/unit"},
                },
            ],
        ),
        SessionMessage(
            role="tool",
            content="18 passed in 1.5s",
            metadata={"tool_use_id": "remote-test-1", "exit_code": 0},
        ),
    ]

    events, tool_calls = AutonomousAgentRunner._normalize_remote_messages(messages)

    assert tool_calls[0]["tool"]["name"] == "Bash"
    assert _has_passing_test_tool_result(events, "python")


def test_remote_tool_result_content_block_survives_without_assistant_text():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    messages = [
        {
            "role": "assistant",
            "content": "",
            "content_blocks": [
                {
                    "type": "tool_result",
                    "tool_use_id": "remote-test-2",
                    "content": "5 passed",
                    "exit_code": 0,
                }
            ],
        }
    ]

    events, _ = AutonomousAgentRunner._normalize_remote_messages(messages)

    assert events == [
        {
            "type": "tool_result",
            "tool_use_id": "remote-test-2",
            "text": "5 passed",
            "exit_code": 0,
            "is_error": False,
        }
    ]


def test_remote_codex_exec_command_counts_as_test_evidence():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.orchestrator import _has_passing_test_tool_result

    messages = [
        {
            "role": "assistant",
            "content_blocks": [
                {
                    "type": "tool_use",
                    "id": "codex-test-1",
                    "name": "exec_command",
                    "input": {"cmd": "python -m pytest -q"},
                }
            ],
        },
        {
            "role": "tool",
            "content": "7 passed in 0.6s",
            "metadata": {"tool_call_id": "codex-test-1", "exit_code": 0},
        },
    ]

    events, _ = AutonomousAgentRunner._normalize_remote_messages(messages)

    assert _has_passing_test_tool_result(events, "python")


def test_fresh_ci_repair_prompt_keeps_requirements_plan_and_diff():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-1892"
    orch.repo = MagicMock()
    orch.repo.get_workflow.return_value = {"github_issue_number": 1892}
    orch._get_latest_final_plan = MagicMock(return_value="APPROVED PLAN")
    orch._get_ci_repair_prompt = MagicMock(return_value="\nCI EXCERPT\n")
    orch._get_user_feedback_prompt = MagicMock(return_value="")
    orch._build_prior_repair_failures_prompt = MagicMock(return_value="")
    gh = MagicMock()
    gh.get_pr_diff.return_value = "diff --git a/a.py b/a.py"

    prompt = orch._build_merge_ci_repair_agent_prompt(
        {"github_issue_number": 1892, "requirements_text": "ORIGINAL REQUIREMENT"},
        1927,
        [{"name": "lint", "bucket": "fail", "state": "failure"}],
        gh=gh,
    )

    assert "ORIGINAL REQUIREMENT" in prompt
    assert "APPROVED PLAN" in prompt
    assert "diff --git" in prompt
    assert "CI EXCERPT" in prompt
    assert "pre-commit run --all-files" in prompt
    assert "编排器已先把当前 main 合并进 PR 分支" in prompt
    assert "SKIP=bandit,no-commit-to-branch" in prompt
    assert "直到 exit 0" in prompt


def test_pre_commit_detection_requires_log_evidence():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    assert AutonomousOrchestrator._ci_failure_uses_pre_commit(
        [
            {
                "name": "lint",
                "failure_excerpt": "hook id: end-of-file-fixer\nfiles were modified by this hook",
            }
        ]
    )
    assert not AutonomousOrchestrator._ci_failure_uses_pre_commit(
        [{"name": "lint", "failure_excerpt": "ruff: F401 unused import"}]
    )


def test_pre_commit_convergence_reruns_modified_hooks_as_isolated_agent():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._resolve_isolated_agent_account = MagicMock(return_value="openace-agent")
    orch._resolve_system_account = MagicMock(return_value="repo-owner")
    orch._select_project_python_runtime = MagicMock(return_value=(["python3"], ""))
    gh = MagicMock()
    gh.path_exists_as_user.return_value = True
    failed_checks = [
        {
            "name": "lint",
            "failure_excerpt": "pre-commit hook(s) made changes",
        }
    ]
    modified = MagicMock(
        returncode=1,
        stdout="files were modified by this hook\nFixing docker-compose.yml",
        stderr="",
    )
    clean = MagicMock(returncode=0, stdout="All checks passed", stderr="")

    with (
        patch(
            "app.modules.workspace.autonomous.orchestrator.shutil.which",
            side_effect=["/usr/local/bin/pre-commit", "/usr/bin/git"],
        ),
        patch.object(
            AutonomousAgentRunner,
            "_wrap_agent_cmd",
            return_value=(["isolated-wrapper", "pre-commit"], None),
        ) as wrap,
        patch.object(
            AutonomousAgentRunner,
            "_resolve_agent_guard_bin",
            return_value="/usr/local/libexec/openace-agent-bin",
        ),
        patch(
            "app.modules.workspace.autonomous.orchestrator.subprocess.run",
            side_effect=[modified, clean],
        ) as run,
    ):
        attempted, error = orch._converge_pre_commit_fixes(
            {
                "workspace_type": "local",
                "worktree_path": "/private/repo",
            },
            gh,
            failed_checks,
        )

    assert attempted
    assert error == ""
    assert run.call_count == 2
    wrap.assert_called_once()
    assert wrap.call_args.args[:3] == (
        ["/usr/local/bin/pre-commit", "run", "--all-files"],
        "/private/repo",
        "openace-agent",
    )
    assert wrap.call_args.args[3]["PATH"].split(":", 1)[0] == "/usr/local/libexec/openace-agent-bin"
    assert wrap.call_args.args[3]["SKIP"] == "bandit,no-commit-to-branch"
    assert run.call_args.kwargs["cwd"] is None
    assert run.call_args.kwargs["env"] is None


def test_pre_commit_convergence_rejects_repository_owner_account():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._resolve_isolated_agent_account = MagicMock(return_value="repo-owner")
    orch._resolve_system_account = MagicMock(return_value="repo-owner")
    gh = MagicMock()
    gh.path_exists_as_user.return_value = True

    with (
        patch(
            "app.modules.workspace.autonomous.orchestrator.shutil.which",
            side_effect=["/usr/local/bin/pre-commit", "/usr/bin/git"],
        ),
        patch("app.modules.workspace.autonomous.orchestrator.subprocess.run") as run,
        pytest.raises(RuntimeError, match="repository owner"),
    ):
        orch._converge_pre_commit_fixes(
            {"workspace_type": "local", "worktree_path": "/private/repo", "user_id": 7},
            gh,
            [{"failure_excerpt": "pre-commit hook(s) made changes"}],
        )

    run.assert_not_called()


def test_failed_pr_sync_skips_branch_that_already_contains_main():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._ancestor_check = MagicMock(return_value=True)
    orch._resolve_merge_conflicts = MagicMock()
    orch._ensure_pr_head_local = MagicMock(return_value=True)
    gh = MagicMock()
    gh.resolve_commit.return_value = "main-head"

    synced = orch._sync_failed_pr_with_main(gh, "auto-dev/test", 42, "pr-head")

    assert not synced
    gh._run_git.assert_called_once_with(["fetch", "origin", "main"])
    orch._ancestor_check.assert_called_once_with(gh, "main-head", "pr-head")
    orch._resolve_merge_conflicts.assert_not_called()


def test_failed_pr_sync_pushes_main_before_ai_repair():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._ancestor_check = MagicMock(return_value=False)
    orch._resolve_merge_conflicts = MagicMock()
    orch._ensure_pr_head_local = MagicMock(return_value=True)
    gh = MagicMock()
    gh.resolve_commit.return_value = "main-head"

    synced = orch._sync_failed_pr_with_main(gh, "auto-dev/test", 42, "pr-head")

    assert synced
    orch._resolve_merge_conflicts.assert_called_once_with(gh, "auto-dev/test", 42)


def test_failed_pr_sync_happens_before_ai_attempt_limit():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-sync-before-limit"
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "pr-head"
    orch._get_gh = MagicMock(return_value=gh)
    orch._sync_failed_pr_with_main = MagicMock(return_value=True)
    orch._create_milestone = MagicMock()
    orch._update_workflow = MagicMock()

    orch._start_ci_repair_round(
        {
            "dev_round": 1,
            "ci_repair_attempts": 3,
            "branch_name": "auto-dev/test",
        },
        42,
        [{"name": "lint", "bucket": "fail"}],
    )

    orch._sync_failed_pr_with_main.assert_called_once_with(gh, "auto-dev/test", 42, "pr-head")
    orch._create_milestone.assert_not_called()
    orch._update_workflow.assert_not_called()


def test_nonstandard_report_with_real_test_evidence_does_not_retry():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-1897"
    orch.repo = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-test"})
    orch._build_test_execution_context = MagicMock(return_value="targeted")
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._emit = MagicMock()
    orch._update_workflow = MagicMock()
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(
            success=True,
            session_id="test-session",
            response_text="12个测试全部通过",
            visible_response_text="12个测试全部通过",
            tool_calls=[
                {
                    "tool": {
                        "name": "Bash",
                        "input": {"command": "python -m pytest tests/unit"},
                        "id": "tool-test-1",
                    }
                }
            ],
            event_log=[
                {
                    "type": "tool_use",
                    "tool_name": "Bash",
                    "tool_input": {"command": "python -m pytest tests/unit"},
                    "tool_use_id": "tool-test-1",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-test-1",
                    "text": "12 passed in 1.2s",
                    "is_error": False,
                },
            ],
        )
    )
    wf = {
        "workflow_id": "wf-1897",
        "project_path": "/tmp/repo",
        "worktree_path": "/tmp/repo",
        "cli_tool": "claude-code",
        "dev_round": 1,
        "branch_name": "auto-dev/wf-1897",
    }

    orch._run_test_phase(wf, 1, MagicMock())

    updates = [call.args[0] for call in orch._update_workflow.call_args_list]
    assert not any("format_retries" in update for update in updates)
    assert any(update.get("status") == "pr_review" for update in updates)


def test_test_tool_call_without_result_is_inconclusive():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-inconclusive"
    orch.repo = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-test"})
    orch._build_test_execution_context = MagicMock(return_value="targeted")
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._emit = MagicMock()
    orch._update_workflow = MagicMock()
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(
            success=True,
            response_text="I invoked the test command.",
            tool_calls=[{"tool": {"name": "Bash", "input": {"command": "pytest || true"}}}],
        )
    )
    wf = {
        "project_path": "/tmp/repo-without-pyproject",
        "cli_tool": "claude-code",
        "test_retries": 0,
    }

    orch._run_test_phase(wf, 1, MagicMock())

    updates = [call.args[0] for call in orch._update_workflow.call_args_list]
    assert any(update.get("test_retries") == 1 for update in updates)
    assert not any(update.get("status") == "pr_review" for update in updates)


def test_model_pass_summary_without_tool_result_is_inconclusive():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-model-summary"
    orch.repo = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-test"})
    orch._build_test_execution_context = MagicMock(return_value="targeted")
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._emit = MagicMock()
    orch._update_workflow = MagicMock()
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(success=True, response_text="12个测试全部通过")
    )

    orch._run_test_phase(
        {"project_path": "/tmp/no-project", "cli_tool": "claude-code"}, 1, MagicMock()
    )

    updates = [call.args[0] for call in orch._update_workflow.call_args_list]
    assert any(update.get("test_retries") == 1 for update in updates)
    assert not any(update.get("status") == "pr_review" for update in updates)
