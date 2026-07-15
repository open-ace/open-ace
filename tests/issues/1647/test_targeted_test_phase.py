"""Tests for targeted autonomous test-phase guidance (Issue #1647)."""

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_workflow(project_path: str, **overrides):
    base = {
        "workflow_id": "wf-1647",
        "user_id": 1,
        "title": "issue-1647",
        "status": "developing",
        "requirements_text": "Adjust frontend switch styling",
        "requirements_issue_url": "",
        "project_path": project_path,
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-1647",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": project_path,
        "preferred_worktree_path": project_path,
        "github_issue_number": 1647,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data, milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = milestones or []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo.update_milestone.return_value = {}
        mock_repo.update_workflow_tokens.return_value = None
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()

    return orch, mock_repo


def _seed_repo_layout(repo_root: Path) -> None:
    (repo_root / "frontend").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests").mkdir(parents=True, exist_ok=True)
    (repo_root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo_root / "frontend" / "package.json").write_text('{"scripts":{"test":"vitest --run"}}')
    (repo_root / "tests" / "README.md").write_text("pytest tests/unit/\npytest tests/integration/\n")
    (repo_root / "pytest.ini").write_text("[pytest]\n")


def test_planning_source_mentions_validation_plan():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._do_planning)
    assert "验证计划" in source


def test_build_test_execution_context_prefers_frontend_scope(tmp_path):
    repo_root = tmp_path / "repo"
    _seed_repo_layout(repo_root)

    plan_text = (
        "## 技术方案\n更新前端样式。\n\n"
        "## 验证计划\n"
        "- 验证 form-switch 样式尺寸与暗色主题。\n"
        "- 运行前端测试，不需要跑后端全量 pytest。\n"
    )
    milestones = [{"milestone_type": "plan_finalized", "plan_content": plan_text}]
    wf = _make_workflow(str(repo_root))
    orch, _ = _make_orchestrator(wf, milestones=milestones)
    orch._gh.get_current_commit.return_value = "abc1234"
    orch._gh.get_commit_changed_files.return_value = ["frontend/src/styles/main.css"]

    context = orch._build_test_execution_context(wf, orch._gh)

    assert "方案中的验证计划" in context
    assert "frontend/src/styles/main.css" in context
    assert "前端相关验证" in context
    assert "不要先跑后端全树 pytest" in context
    assert "tests/README.md" in context
    assert "frontend/package.json" in context


def test_run_test_phase_prompt_includes_targeted_context(tmp_path):
    repo_root = tmp_path / "repo"
    _seed_repo_layout(repo_root)

    plan_text = (
        "## 技术方案\n更新前端样式。\n\n"
        "## 验证计划\n"
        "- 优先验证前端组件和样式行为。\n"
        "- 仅在出现跨层影响证据时扩大范围。\n"
    )
    milestones = [{"milestone_type": "plan_finalized", "plan_content": plan_text}]
    wf = _make_workflow(str(repo_root))
    orch, _ = _make_orchestrator(wf, milestones=milestones)
    orch._accumulate_tokens = MagicMock()
    orch._run_agent = MagicMock(
        return_value=AgentTaskResult(
            success=True,
            session_id="sess-test",
            response_text="643 passed in 13.52s",
            visible_response_text="643 passed in 13.52s",
            error="",
        )
    )
    orch._gh.get_current_commit.return_value = "abc1234"
    orch._gh.get_commit_changed_files.return_value = ["frontend/src/styles/main.css"]

    orch._run_test_phase(wf, 1, orch._gh)

    prompt = orch._run_agent.call_args.kwargs["prompt"]
    assert "本轮定向验证上下文" in prompt
    assert "方案中的验证计划" in prompt
    assert "frontend/src/styles/main.css" in prompt
    assert "不要从裸 `python -m pytest`" in prompt
    assert "frontend/package.json" in prompt
