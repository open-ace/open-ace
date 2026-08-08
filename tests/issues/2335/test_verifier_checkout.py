"""S5 (#2335): verifier runs on a dedicated merged-main checkout.

The verifier must NOT spawn in the workflow's dev worktree — it gets a
throwaway checkout of ``main`` at ``verification_merge_sha``. On any
checkout/spawn failure it returns empty verdicts (aggregates to
``indeterminate``/pause, never a false ``confirmed``).
"""

from __future__ import annotations

import os
import stat
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous import orchestrator as orch_mod
from app.modules.workspace.autonomous.github_ops import OPENACE_RM_WRAPPER, GitHubOps


def _make_orchestrator():
    """Build an AutonomousOrchestrator with stubbed dependencies."""
    wf = {
        "workflow_id": "wf-1",
        "cli_tool": "claude-code",
        "model": "test-model-x",
        "worktree_path": "/dev/worktree",
        "project_path": "/dev/repo",
        "branch_strategy": "worktree",
        "workspace_type": "local",
    }
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager"),
        patch("app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"),
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = dict(wf)
        mock_repo_cls.return_value = mock_repo
        orch = orch_mod.AutonomousOrchestrator("wf-1")
        orch.repo = mock_repo
    return orch


def test_checkout_merged_main_creates_temp_worktree_at_merge_sha(tmp_path):
    """``_checkout_merged_main`` runs ``git worktree add --detach <tmp> <sha>``."""
    orch = _make_orchestrator()
    gh = MagicMock()
    captured_cmds = []

    def fake_run_git(args, **_kw):
        captured_cmds.append(list(args))
        # Simulate git creating the worktree dir.
        if len(args) >= 2 and args[0] == "worktree" and args[1] == "add":
            os.makedirs(args[-2], exist_ok=True)
        return MagicMock(stdout="", stderr="", returncode=0)

    gh._run_git = fake_run_git
    gh.ensure_commit_available.return_value = True
    gh.repo_path = "/dev/repo"

    gh.create_verification_worktree_dir.return_value = str(tmp_path)
    with patch.object(orch_mod.AutonomousOrchestrator, "_get_gh", return_value=gh):
        path = orch._checkout_merged_main("abc123")

    assert path == str(tmp_path)
    gh.ensure_commit_available.assert_called_once_with("abc123")
    gh.create_verification_worktree_dir.assert_called_once_with("/dev/repo")
    # The first worktree-add command should target the merge sha in detached mode.
    add_cmds = [c for c in captured_cmds if c[:2] == ["worktree", "add"]]
    assert add_cmds, "expected a git worktree add"
    cmd = add_cmds[0]
    assert "--detach" in cmd or "--no-checkout" not in cmd
    assert cmd[-1] == "abc123"  # base ref is the merge sha
    assert cmd[-2] == str(tmp_path)


def test_checkout_merged_main_returns_none_on_failure(tmp_path):
    """A git failure during checkout returns None (fail-safe, no half-created state)."""
    orch = _make_orchestrator()
    gh = MagicMock()

    def fake_run_git(args, **_kw):
        if args[:2] in (["worktree", "add"], ["worktree", "remove"]):
            raise RuntimeError("boom")
        return MagicMock(stdout="", stderr="", returncode=0)

    gh._run_git = fake_run_git
    gh.ensure_commit_available.return_value = True
    gh.repo_path = "/dev/repo"

    gh.create_verification_worktree_dir.return_value = str(tmp_path)
    with patch.object(orch_mod.AutonomousOrchestrator, "_get_gh", return_value=gh):
        path = orch._checkout_merged_main("abc123")

    assert path is None
    gh.remove_verification_worktree_dir.assert_called_once_with(str(tmp_path), "/dev/repo")


def test_checkout_merged_main_does_not_create_worktree_when_commit_is_unavailable():
    """A failed exact-SHA fetch stops before allocating or mutating a worktree."""
    orch = _make_orchestrator()
    gh = MagicMock()
    gh.ensure_commit_available.return_value = False

    with patch.object(orch_mod.AutonomousOrchestrator, "_get_gh", return_value=gh):
        path = orch._checkout_merged_main("abc123")

    assert path is None
    gh.ensure_commit_available.assert_called_once_with("abc123")
    gh.create_verification_worktree_dir.assert_not_called()
    gh._run_git.assert_not_called()


def test_cross_user_verifier_directory_is_created_by_repository_owner():
    """Cross-user allocation must not leave a service-owned 0700 target."""
    gh = GitHubOps("/home/alice/repo", system_account="alice")
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch(
            "app.modules.workspace.autonomous.github_ops.subprocess.run",
            return_value=completed,
        ) as run,
        patch("app.modules.workspace.autonomous.github_ops.uuid.uuid4") as make_uuid,
    ):
        make_uuid.return_value.hex = "a" * 32
        path = gh.create_verification_worktree_dir("/home/alice/repo")

    expected_root = os.path.join(os.path.realpath("/home/alice/repo"), ".worktrees")
    assert path == os.path.join(expected_root, "verify-" + "a" * 32)
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["sudo", "-u", "alice", "mkdir", "-p", "--", expected_root],
        ["sudo", "-u", "alice", "mkdir", "-m", "700", "--", path],
    ]


def test_same_user_verifier_directory_is_private_and_owner_writable(tmp_path):
    """The real allocator creates a traversable/writable 0700 dir for its owner."""
    gh = GitHubOps(str(tmp_path))
    path = gh.create_verification_worktree_dir(str(tmp_path))
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o700
        assert os.access(path, os.X_OK | os.W_OK)
    finally:
        gh.remove_verification_worktree_dir(path, str(tmp_path))


def test_cross_user_verifier_cleanup_uses_owner_safe_wrapper():
    """A service user never tries to rmtree a repository-owner 0700 directory."""
    gh = GitHubOps("/home/alice/repo", system_account="alice")
    project = os.path.realpath("/home/alice/repo")
    path = os.path.join(project, ".worktrees", "verify-abc")
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch(
            "app.modules.workspace.autonomous.github_ops.subprocess.run",
            return_value=completed,
        ) as run,
        patch("app.modules.workspace.autonomous.github_ops.shutil.rmtree") as rmtree,
    ):
        gh.remove_verification_worktree_dir(path, project)

    cmd = run.call_args.args[0]
    assert cmd[:3] == ["sudo", OPENACE_RM_WRAPPER, "alice"]
    assert cmd[3:] == [path, "-r", "-f"]
    rmtree.assert_not_called()


def test_run_verification_agent_uses_merged_main_checkout(tmp_path):
    """``_run_verification_agent`` spawns the agent with project_path = the temp checkout."""
    orch = _make_orchestrator()
    snapshot = MagicMock()
    snapshot.to_canonical.return_value = {}

    checkout_path = str(tmp_path / "merged")
    os.makedirs(checkout_path)
    spawn_calls = []

    with patch.object(
        orch_mod.AutonomousOrchestrator, "_checkout_merged_main", return_value=checkout_path
    ) as mock_co:
        with patch.object(
            orch_mod.AutonomousOrchestrator, "_remove_verification_worktree"
        ) as mock_rm:

            def fake_run_agent(wf, **kwargs):
                spawn_calls.append(kwargs)
                return MagicMock()

            with patch.object(
                orch_mod.AutonomousOrchestrator, "_run_agent", side_effect=fake_run_agent
            ):
                with patch.object(
                    orch_mod.AutonomousOrchestrator,
                    "_parse_verifier_output",
                    return_value={"verdicts": [], "snapshot": None},
                ):
                    orch._run_verification_agent(
                        snapshot=snapshot,
                        merge_sha="deadbeef",
                        base_sha="base",
                        issue_number=42,
                        pr_number=99,
                    )

    # The agent was spawned with the temp checkout as project_path (not the dev worktree).
    assert spawn_calls, "verifier should have spawned an agent"
    assert spawn_calls[0]["project_path"] == checkout_path
    assert spawn_calls[0]["project_path"] != "/dev/worktree"
    mock_co.assert_called_once_with("deadbeef")
    # Cleanup happened.
    mock_rm.assert_called_once()


def test_run_verification_agent_cleans_up_checkout_on_exception():
    """Even when the spawn raises, the temp worktree is removed (try/finally)."""
    orch = _make_orchestrator()
    snapshot = MagicMock()
    snapshot.to_canonical.return_value = {}

    with patch.object(
        orch_mod.AutonomousOrchestrator, "_checkout_merged_main", return_value="/tmp/merged"
    ):
        with patch.object(
            orch_mod.AutonomousOrchestrator, "_remove_verification_worktree"
        ) as mock_rm:
            with patch.object(
                orch_mod.AutonomousOrchestrator,
                "_run_agent",
                side_effect=RuntimeError("spawn failed"),
            ):
                result = orch._run_verification_agent(
                    snapshot=snapshot,
                    merge_sha="deadbeef",
                    base_sha="base",
                    issue_number=42,
                    pr_number=99,
                )

    # Fail-safe: empty verdicts -> indeterminate, never confirmed.
    assert result["verdicts"] == []
    assert result["snapshot"] is None
    assert result["infra_error"] == "verification agent spawn failed"
    mock_rm.assert_called_once()


def test_run_verification_agent_returns_empty_when_checkout_fails():
    """When the merged-main checkout cannot be created, no spawn is attempted."""
    orch = _make_orchestrator()
    snapshot = MagicMock()
    snapshot.to_canonical.return_value = {}

    with patch.object(orch_mod.AutonomousOrchestrator, "_checkout_merged_main", return_value=None):
        with patch.object(orch_mod.AutonomousOrchestrator, "_run_agent") as mock_spawn:
            result = orch._run_verification_agent(
                snapshot=snapshot,
                merge_sha="deadbeef",
                base_sha="base",
                issue_number=42,
                pr_number=99,
            )

    assert result["verdicts"] == []
    assert result["snapshot"] is None
    assert result["infra_error"] == "merged-main checkout failed"
    mock_spawn.assert_not_called()


def test_run_verification_agent_signals_unsuccessful_agent_result():
    orch = _make_orchestrator()
    snapshot = MagicMock()
    snapshot.to_canonical.return_value = {}
    failed_result = MagicMock(success=False, error="secret provider detail", error_code="timeout")

    with patch.object(
        orch_mod.AutonomousOrchestrator, "_checkout_merged_main", return_value="/tmp/merged"
    ):
        with patch.object(orch_mod.AutonomousOrchestrator, "_remove_verification_worktree"):
            with patch.object(
                orch_mod.AutonomousOrchestrator, "_run_agent", return_value=failed_result
            ):
                result = orch._run_verification_agent(
                    snapshot=snapshot,
                    merge_sha="deadbeef",
                    base_sha="base",
                    issue_number=42,
                    pr_number=99,
                )

    assert result["infra_error"] == "verification agent failed (timeout)"
    assert "secret provider detail" not in result["infra_error"]


def test_parse_valid_empty_verdict_response_is_not_an_infra_error():
    orch = _make_orchestrator()
    result = MagicMock()
    with patch.object(
        orch_mod.AutonomousOrchestrator,
        "_artifact_text",
        return_value='```json\n{"verdicts": [], "snapshot": null}\n```',
    ):
        parsed = orch._parse_verifier_output(result)

    assert parsed == {"verdicts": [], "snapshot": None}


def test_parse_malformed_verdict_shape_is_explicit_infra_error():
    orch = _make_orchestrator()
    result = MagicMock()
    with patch.object(
        orch_mod.AutonomousOrchestrator,
        "_artifact_text",
        return_value='```json\n{"verdicts": ["not-an-object"], "snapshot": null}\n```',
    ):
        parsed = orch._parse_verifier_output(result)

    assert parsed["infra_error"] == "verification agent verdicts were malformed"


def test_verified_by_records_model_version():
    """``verified_by`` in the report includes the verifier model/version."""
    # The report-builder path: we test via the phase handler's common_patch,
    # but verified_by is also surfaced by _run_verification_agent's output so
    # the phase can stamp it. Verify the agent records the workflow model.
    orch = _make_orchestrator()
    snapshot = MagicMock()
    snapshot.to_canonical.return_value = {}

    with patch.object(
        orch_mod.AutonomousOrchestrator, "_checkout_merged_main", return_value="/tmp/merged"
    ):
        with patch.object(orch_mod.AutonomousOrchestrator, "_remove_verification_worktree"):
            with patch.object(
                orch_mod.AutonomousOrchestrator, "_run_agent", return_value=MagicMock()
            ):
                with patch.object(
                    orch_mod.AutonomousOrchestrator,
                    "_parse_verifier_output",
                    return_value={"verdicts": [], "snapshot": None},
                ):
                    result = orch._run_verification_agent(
                        snapshot=snapshot,
                        merge_sha="deadbeef",
                        base_sha="base",
                        issue_number=42,
                        pr_number=99,
                    )

    # The verifier output carries a verified_by field naming the model + runner version.
    assert "verified_by" in result
    assert "test-model-x" in result["verified_by"]
