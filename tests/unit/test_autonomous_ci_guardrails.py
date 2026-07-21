"""Regression tests for autonomous CI/development guardrails."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


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
