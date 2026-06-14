"""Tests for autonomous agent bash permission + fix commit fallback (Issue #996).

Verifies:
  - Non-plan phases pass `AUTONOMOUS_DEV_ALLOWED_TOOLS` (incl. ``Bash``) so the
    agent can run tests / git / build; plan phases stay read-only.
  - The fix phase salvages uncommitted changes (mirroring dev) when the agent
    didn't commit, so review fixes actually reach the PR.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import (
    AUTONOMOUS_DEV_ALLOWED_TOOLS,
    PLANNING_ALLOWED_TOOLS,
    AutonomousOrchestrator,
)

# ── helpers (mirror tests/issues/987/) ───────────────────────────────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "test-wf-996",
        "title": "Test 996",
        "requirements_text": "REQ",
        "branch_name": "feature-x",
        "github_issue_number": 100,
        "github_pr_number": 42,
        "current_round": 0,  # round_num = 1
        "max_pr_review_rounds": 2,  # round(1) < max(2) -> fix branch
        "dev_round": 1,
        "cli_tool": "claude-code",
        "model": "",
        "worktree_path": "/tmp/wf996",
        "project_path": "/tmp/wf996",
        "workspace_type": "local",
        "remote_machine_id": "",
        "permission_mode": "auto-edit",
    }
    base.update(overrides)
    return base


def _make_agent_result(text="done\nTL;DR: fixed the bug"):
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=10,
        total_input_tokens=5,
        total_output_tokens=5,
        success=True,
        error=None,
    )


def _make_gh(commit_sha="abc1234", uncommitted=True):
    gh = MagicMock()
    gh.get_diff_stats.return_value = {"commits": 1, "additions": 5, "deletions": 1, "files": 1}
    gh.get_commit_diff_stats.return_value = {
        "commits": 1,
        "additions": 5,
        "deletions": 1,
        "files": 1,
    }
    gh.git_push.return_value = None
    gh.get_pr_diff.return_value = "FAKE_DIFF"
    gh.add_pr_comment.return_value = {}
    gh.add_issue_comment.return_value = {}
    gh.get_current_commit.return_value = commit_sha  # same SHA before/after -> triggers fallback
    gh.has_uncommitted_changes.return_value = uncommitted
    gh.git_add_all.return_value = None
    gh.git_commit.return_value = {"sha": commit_sha}
    return gh


def _make_orchestrator(wf):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        orch = AutonomousOrchestrator(wf["workflow_id"])
        orch.repo = mock_repo
    orch.emitter = MagicMock()
    orch._get_gh = MagicMock()
    orch._poll_ci_status = MagicMock(return_value=[])
    orch._smart_truncate_diff = MagicMock(return_value="DIFF_TEXT")
    orch._clean_agent_text = MagicMock(side_effect=lambda x: x or "")
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-x"})
    orch._update_workflow = MagicMock()
    orch._accumulate_tokens = MagicMock()
    orch._emit = MagicMock()
    return orch


# ── allowed_tools constants ──────────────────────────────────────────────


class TestAllowedToolSets:
    def test_dev_set_includes_bash_write_edit(self):
        tools = AUTONOMOUS_DEV_ALLOWED_TOOLS["claude-code"]
        assert "Bash" in tools
        assert "Write" in tools
        assert "Edit" in tools
        # still has the read-only planning tools
        assert "Read" in tools and "Grep" in tools

    def test_plan_set_excludes_bash_and_writes(self):
        tools = PLANNING_ALLOWED_TOOLS["claude-code"]
        assert "Bash" not in tools
        assert "Write" not in tools
        assert "Edit" not in tools


# ── fix phase: passes Bash + salvages uncommitted changes ────────────────


class TestFixPhasePermissionsAndFallback:
    def test_fix_run_agent_passes_bash_allowed_tools(self):
        wf = _make_workflow()  # round 1 < max 2 -> fix branch
        orch = _make_orchestrator(wf)
        orch._get_gh.return_value = _make_gh()
        orch.repo.list_milestones.return_value = []
        orch._run_agent = MagicMock(return_value=_make_agent_result())

        orch._do_pr_review(wf)

        # every _run_agent call in the fix branch carries the dev tool set
        for call in orch._run_agent.call_args_list:
            allowed = call.kwargs.get("allowed_tools")
            assert allowed is not None, "fix/review _run_agent must pass allowed_tools"
            assert "Bash" in allowed

    def test_fix_salvages_uncommitted_when_agent_did_not_commit(self):
        wf = _make_workflow()
        orch = _make_orchestrator(wf)
        gh = _make_gh(commit_sha="same123", uncommitted=True)
        orch._get_gh.return_value = gh
        orch.repo.list_milestones.return_value = []
        orch._run_agent = MagicMock(return_value=_make_agent_result())

        orch._do_pr_review(wf)

        # agent didn't commit (SHA unchanged) but left uncommitted changes ->
        # orchestrator salvages via git_add_all + git_commit, then pushes
        gh.has_uncommitted_changes.assert_called()
        gh.git_add_all.assert_called_once()
        gh.git_commit.assert_called_once()
        assert gh.git_commit.call_args.kwargs.get("no_verify") is True
        gh.git_push.assert_called()  # pushed after salvage

    def test_fix_no_salvage_when_no_uncommitted_changes(self):
        wf = _make_workflow()
        orch = _make_orchestrator(wf)
        gh = _make_gh(commit_sha="same123", uncommitted=False)
        orch._get_gh.return_value = gh
        orch.repo.list_milestones.return_value = []
        orch._run_agent = MagicMock(return_value=_make_agent_result())

        orch._do_pr_review(wf)

        # SHA unchanged + nothing uncommitted -> no auto-commit, no push
        gh.git_add_all.assert_not_called()
        gh.git_commit.assert_not_called()
