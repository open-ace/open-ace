# mypy: disable-error-code="assignment,arg-type,union-attr,return-value,no-any-return"
"""
Open ACE - Autonomous Orchestrator

State machine that drives a single autonomous development workflow
through its phases: preparation -> planning -> development ->
pr_review -> report -> wait -> (loop or merge).
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter
from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.repositories.database import Database

logger = logging.getLogger(__name__)

# Completion keywords to detect in issue comments
COMPLETION_KEYWORDS = [
    "开发完成",
    "无新需求",
    "项目结束",
    "all done",
    "no more requirements",
    "project finished",
    "开发完毕",
    "全部完成",
    "workflow complete",
]

# Prefix added to all prompts to inform the agent it is running autonomously
AUTONOMOUS_CONTEXT = (
    "## 重要提示\n"
    "你正在无人值守的自动化工作流中运行。请遵守以下规则：\n"
    "1. 不要请求人类确认或等待权限批准，如果操作被阻止请跳过并继续\n"
    "2. 不要使用需要交互式确认的 gh CLI 命令（如 gh pr create）\n"
    "3. 直接输出你的分析和方案，不要尝试执行需要权限的操作\n"
    "4. 如果需要修改文件，直接输出修改方案即可\n\n"
)


class AutonomousOrchestrator:
    """Drives a single autonomous workflow through its phases."""

    def __init__(self, workflow_id: str, db: Database = None):
        self.db = db or Database()
        self.repo = AutonomousWorkflowRepository(self.db)
        self.emitter = AutonomousEventEmitter.instance()
        self._workflow_id = workflow_id
        self._runner = AutonomousAgentRunner()
        self._gh: Optional[GitHubOps] = None

    @property
    def workflow(self) -> Optional[dict]:
        return self.repo.get_workflow(self._workflow_id)

    def _get_gh(self) -> GitHubOps:
        """Lazily initialize GitHubOps."""
        wf = self.workflow
        if not self._gh and wf:
            project_path = wf.get("worktree_path") or wf.get("project_path", "")
            self._gh = GitHubOps(project_path)
        return self._gh

    def _emit(self, event_type: str, data: dict):
        """Emit a timeline event."""
        self.emitter.emit(self._workflow_id, event_type, data)
        self.repo.create_event(
            {
                "workflow_id": self._workflow_id,
                "event_type": event_type,
                "event_data": json.dumps(data, ensure_ascii=False),
            }
        )

    def _create_milestone(self, **kwargs) -> dict:
        """Create a milestone and emit event."""
        kwargs.setdefault("workflow_id", self._workflow_id)
        ms = self.repo.create_milestone(kwargs)
        self._emit(
            "milestone_created",
            {
                "milestone_id": ms.get("milestone_id", ""),
                "milestone_type": kwargs.get("milestone_type", ""),
                "title": kwargs.get("title", ""),
            },
        )
        return ms

    def _update_workflow(self, updates: dict):
        """Update workflow and emit event."""
        self.repo.update_workflow(self._workflow_id, updates)
        self._emit("workflow_updated", updates)

    def _accumulate_tokens(self, result: AgentTaskResult):
        """Add agent task tokens to workflow totals."""
        self.repo.update_workflow_tokens(
            self._workflow_id,
            {
                "total_tokens": result.total_tokens,
                "total_input_tokens": result.total_input_tokens,
                "total_output_tokens": result.total_output_tokens,
                "total_requests": 1,
            },
        )

    def advance(self):
        """Advance the workflow one step. Called by the scheduler."""
        wf = self.workflow
        if not wf:
            logger.error("Workflow %s not found", self._workflow_id)
            return

        if wf.get("status") == "paused":
            return

        phase = wf.get("current_phase", "preparation")
        logger.info("Advancing workflow %s phase=%s", self._workflow_id[:8], phase)

        try:
            if phase == "preparation":
                self._do_preparation(wf)
            elif phase == "planning":
                self._do_planning(wf)
            elif phase == "development":
                self._do_development(wf)
            elif phase == "pr_review":
                self._do_pr_review(wf)
            elif phase == "report":
                self._do_report(wf)
            elif phase == "wait":
                self._do_wait(wf)
            elif phase == "merge":
                self._do_merge(wf)
        except Exception as e:
            logger.error("Orchestrator error in %s: %s", phase, e, exc_info=True)
            self._update_workflow(
                {
                    "status": "failed",
                    "error_message": str(e),
                }
            )
            self._emit("error", {"phase": phase, "error": str(e)})

    # ── Phase: Preparation ────────────────────────────────────────

    def _do_preparation(self, wf: dict):
        """Set up project, create/read issue, create branch."""
        project_path = wf.get("project_path", "")

        # New project: create GitHub repo
        if wf.get("is_new_project"):
            try:
                gh = GitHubOps(project_path or ".")
                repo_data = gh.create_repo(
                    name=wf.get("project_repo_url", f"auto-project-{uuid.uuid4().hex[:8]}"),
                    private=wf.get("is_private", True),
                    description=wf.get("title", ""),
                )
                repo_url = repo_data.get("url", "")
                self._create_milestone(
                    phase="preparation",
                    milestone_type="repo_setup",
                    status="completed",
                    title="Repository created",
                    result_summary=f"Created repo: {repo_url}",
                )
                self._update_workflow({"project_repo_url": repo_url})
                project_path = project_path or "."
                self._gh = GitHubOps(project_path)
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="preparation",
                    milestone_type="repo_setup",
                    status="failed",
                    title="Repo creation failed",
                    error_message=str(e),
                )
                raise

        gh = self._get_gh()

        # Create or read issue
        requirements_text = wf.get("requirements_text", "")
        issue_url = wf.get("requirements_issue_url", "")
        issue_number = wf.get("github_issue_number")

        if not issue_number and issue_url:
            # Parse issue number from URL
            parts = issue_url.rstrip("/").split("/")
            try:
                issue_number = int(parts[-1])
            except (ValueError, IndexError):
                pass

            # Persist parsed issue number to workflow and record milestone
            if issue_number:
                self._update_workflow({"github_issue_number": issue_number})
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_linked",
                    status="completed",
                    title=f"Linked to issue #{issue_number}",
                    github_issue_number=issue_number,
                    result_summary=issue_url,
                )

        if not issue_number and requirements_text:
            # Create issue from text
            try:
                issue_data = gh.create_issue(
                    title=wf.get("title") or f"Autonomous Dev: {requirements_text[:60]}",
                    body=requirements_text,
                )
                issue_number = issue_data.get("number")
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_created",
                    status="completed",
                    title=f"Issue #{issue_number} created",
                    github_issue_number=issue_number,
                    result_summary=issue_data.get("url", ""),
                )
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_created",
                    status="failed",
                    title="Issue creation failed",
                    error_message=str(e),
                )
                raise
        elif issue_number and not requirements_text:
            # Read issue content
            try:
                issue_data = gh.get_issue(issue_number)
                requirements_text = issue_data.get("body", "")
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_created",
                    status="completed",
                    title=f"Read issue #{issue_number}",
                    github_issue_number=issue_number,
                    result_summary=issue_data.get("title", ""),
                )
                self._update_workflow({"requirements_text": requirements_text})
            except GitHubOpsError as e:
                logger.warning("Failed to read issue #%s: %s", issue_number, e)

        # Create branch
        strategy = wf.get("branch_strategy", "new-branch")
        branch_name = wf.get("branch_name", "")

        if strategy == "new-branch" or strategy == "worktree":
            if not branch_name:
                branch_name = f"auto-dev/{self._workflow_id[:8]}"
            try:
                # Ensure we branch from latest origin/main
                gh._run_git(["fetch", "origin", "main"])

                if strategy == "worktree":
                    wt_data = gh.create_worktree(
                        path=f"{project_path}/../{branch_name.replace('/', '-')}",
                        branch=branch_name,
                    )
                    self._update_workflow({"worktree_path": wt_data.get("worktree_path", "")})
                else:
                    gh.create_branch(branch_name, base="origin/main")
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="completed",
                    title=f"Branch '{branch_name}' created",
                )
                self._update_workflow({"branch_name": branch_name})
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="failed",
                    title="Branch creation failed",
                    error_message=str(e),
                )
                raise

        # Transition to planning
        self._update_workflow(
            {
                "current_phase": "planning",
                "status": "planning",
                "current_round": 0,
            }
        )
        self._emit("phase_change", {"phase": "planning"})

    # ── Phase: Planning ────────────────────────────────────────────

    def _do_planning(self, wf: dict):
        """Execute one planning + review round."""
        wf = self.workflow  # Re-read for latest state
        round_num = wf.get("current_round", 0) + 1
        max_rounds = wf.get("max_plan_rounds", 3)
        dev_round = wf.get("dev_round", 1)
        issue_number = wf.get("github_issue_number")
        gh = self._get_gh()

        requirements = wf.get("requirements_text", "")
        existing_plan = ""

        # Get existing plan from previous round if refining
        if round_num > 1:
            milestones = self.repo.list_milestones(self._workflow_id, phase="planning")
            for ms in milestones:
                if ms.get("milestone_type") in ("plan_refined", "plan_created") and ms.get(
                    "plan_content"
                ):
                    existing_plan = ms["plan_content"]

        # Step 1: Create or refine plan
        if existing_plan and round_num > 1:
            # Get latest review
            review_text = ""
            for ms in reversed(milestones):
                if ms.get("milestone_type") == "plan_reviewed" and ms.get("review_content"):
                    review_text = ms["review_content"]
                    break

            prompt = (
                AUTONOMOUS_CONTEXT + f"你是一个高级开发工程师。请根据以下审查意见完善实现方案。\n\n"
                f"## 原始需求\n{requirements}\n\n"
                f"## 审查意见\n{review_text}\n\n"
                f"## 原方案\n{existing_plan}\n\n"
                f"请输出完善后的完整实现方案。"
            )
            milestone_type = "plan_refined"
        else:
            prompt = (
                AUTONOMOUS_CONTEXT + f"你是一个高级开发工程师。请为以下需求制定详细的实现方案。\n\n"
                f"## 需求\n{requirements}\n\n"
                f"## 项目路径\n{wf.get('project_path', '')}\n\n"
                f"请用 plan mode 创建方案，包含：\n"
                f"1. 需求分析和拆分\n"
                f"2. 技术方案和架构设计\n"
                f"3. 实现步骤（按优先级排序）\n"
                f"4. 测试策略\n"
                f"5. 潜在风险和缓解措施"
            )
            milestone_type = "plan_created"

        ms = self._create_milestone(
            phase="planning",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type=milestone_type,
            status="in_progress",
            title=f"Plan round {round_num}: {milestone_type.replace('_', ' ')}",
        )

        result = self._runner.run_agent_task(
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
        )

        self._accumulate_tokens(result)

        # Store plan
        plan_text = result.response_text or ""
        self.repo.update_milestone(
            ms.get("milestone_id", ""),
            {
                "status": "completed" if result.success else "failed",
                "plan_content": plan_text,
                "result_summary": plan_text[:200],
                "session_id": result.session_id,
                "error_message": result.error or "",
            },
        )

        if not result.success:
            self._update_workflow(
                {"status": "failed", "error_message": f"Planning failed: {result.error}"}
            )
            return

        # Post plan as issue comment
        if issue_number:
            try:
                gh.add_issue_comment(
                    issue_number, f"## 📋 Implementation Plan (Round {round_num})\n\n{plan_text}"
                )
            except GitHubOpsError:
                pass

        # Step 2: Review plan
        review_prompt = (
            AUTONOMOUS_CONTEXT + f"你是一位资深技术评审专家。请严格审查以下实现方案，指出：\n"
            f"1. 遗漏的需求\n"
            f"2. 架构风险\n"
            f"3. 实现难度估计\n"
            f"4. 改进建议\n\n"
            f"## 方案\n{plan_text}\n\n"
            f"## 需求\n{requirements}\n\n"
            f"如果方案没有重大问题，请明确说明'方案通过审查'。"
        )

        review_ms = self._create_milestone(
            phase="planning",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type="plan_reviewed",
            status="in_progress",
            title=f"Plan review round {round_num}",
        )

        review_result = self._runner.run_agent_task(
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=review_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
        )

        self._accumulate_tokens(review_result)

        review_text = review_result.response_text or ""
        self.repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {
                "status": "completed" if review_result.success else "failed",
                "review_content": review_text,
                "result_summary": review_text[:200],
                "review_session_id": review_result.session_id,
            },
        )

        # Post review as issue comment
        if issue_number:
            try:
                gh.add_issue_comment(
                    issue_number, f"## 🔍 Plan Review (Round {round_num})\n\n{review_text}"
                )
            except GitHubOpsError:
                pass

        # Step 3: Check if all rounds are done
        self._update_workflow({"current_round": round_num})

        if round_num >= max_rounds:
            # All plan review rounds completed — post final plan to issue
            final_plan = ""
            all_milestones = self.repo.list_milestones(self._workflow_id, phase="planning")
            for ms in reversed(all_milestones):
                if ms.get("plan_content"):
                    final_plan = ms["plan_content"]
                    break

            issue_number = wf.get("github_issue_number")
            if issue_number and final_plan:
                try:
                    final_comment = (
                        f"## 📋 Final Implementation Plan\n\n"
                        f"Plan review completed after {max_rounds} round(s).\n\n"
                        f"{final_plan}"
                    )
                    gh.add_issue_comment(issue_number, final_comment)
                except Exception:
                    pass

            # Plan finalized, move to development
            self._update_workflow(
                {
                    "current_phase": "development",
                    "status": "developing",
                }
            )
            self._emit("phase_change", {"phase": "development"})
        else:
            # Continue to next planning round
            self._emit("round_end", {"round": round_num, "approved": False})

    # ── Phase: Development ────────────────────────────────────────

    def _do_development(self, wf: dict):
        """Execute development based on finalized plan."""
        dev_round = wf.get("dev_round", 1)
        gh = self._get_gh()

        # Get the finalized plan
        milestones = self.repo.list_milestones(self._workflow_id, phase="planning")
        final_plan = ""
        for ms in reversed(milestones):
            if ms.get("plan_content"):
                final_plan = ms["plan_content"]
                break

        if not final_plan:
            final_plan = wf.get("requirements_text", "No plan available")

        # Development
        ms = self._create_milestone(
            phase="development",
            dev_round=dev_round,
            milestone_type="dev_started",
            status="in_progress",
            title=f"Development round {dev_round}",
        )

        dev_prompt = (
            AUTONOMOUS_CONTEXT + f"根据以下已审定的实现方案进行完整开发。\n\n"
            f"## 实现方案\n{final_plan}\n\n"
            f"## 要求\n"
            f"1. 严格按照方案实现所有功能\n"
            f"2. 编写单元测试和集成测试\n"
            f"3. 运行所有测试确保通过\n"
            f"4. 确保不破坏现有功能\n"
            f"5. 遵循项目现有的代码风格和约定\n"
            f"6. 所有修改完成后，提交 git commit"
        )

        result = self._runner.run_agent_task(
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=dev_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
        )

        self._accumulate_tokens(result)

        # Get diff stats
        diff_stats = {}
        commit_sha = ""
        try:
            commit_sha = gh.get_current_commit()
            diff_stats = gh.get_diff_stats("HEAD~1", "HEAD")
        except Exception:
            pass

        self.repo.update_milestone(
            ms.get("milestone_id", ""),
            {
                "status": "completed" if result.success else "failed",
                "session_id": result.session_id,
                "result_summary": result.response_text[:300] if result.response_text else "",
                "commit_shas": json.dumps([commit_sha] if commit_sha else []),
                "diff_stats": json.dumps(diff_stats),
                "error_message": result.error or "",
            },
        )

        if not result.success:
            self._update_workflow(
                {"status": "failed", "error_message": f"Development failed: {result.error}"}
            )
            return

        # Run tests
        test_ms = self._create_milestone(
            phase="development",
            dev_round=dev_round,
            milestone_type="tests_run",
            status="in_progress",
            title=f"Running tests round {dev_round}",
        )

        test_prompt = (
            AUTONOMOUS_CONTEXT
            + "运行项目的完整测试套件并报告结果。如果有失败，修复问题并重新测试。"
            "确保所有测试通过后再结束。"
        )

        test_result = self._runner.run_agent_task(
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=test_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
        )

        self._accumulate_tokens(test_result)

        self.repo.update_milestone(
            test_ms.get("milestone_id", ""),
            {
                "status": "completed" if test_result.success else "failed",
                "session_id": test_result.session_id,
                "result_summary": (
                    test_result.response_text[:300] if test_result.response_text else ""
                ),
            },
        )

        if not test_result.success:
            self._update_workflow(
                {"status": "failed", "error_message": f"Tests failed: {test_result.error}"}
            )
            return

        # Dev completed milestone
        self._create_milestone(
            phase="development",
            dev_round=dev_round,
            milestone_type="dev_completed",
            status="completed",
            title=f"Development round {dev_round} completed",
        )

        # Post development status to issue
        issue_number = wf.get("github_issue_number")
        if issue_number:
            try:
                branch = wf.get("branch_name", "")
                status_msg = (
                    f"## ✅ Development Round {dev_round} Completed\n\n"
                    f"- **Status**: Development finished, tests passed\n"
                    f"- **Branch**: `{branch}`\n"
                    f"- **Next**: Creating PR and running code review\n\n"
                    f"Progressing to PR review phase..."
                )
                gh.add_issue_comment(issue_number, status_msg)
            except Exception:
                pass

        # Move to PR review
        self._update_workflow(
            {
                "current_phase": "pr_review",
                "status": "pr_review",
                "current_round": 0,
            }
        )
        self._emit("phase_change", {"phase": "pr_review"})

    # ── Phase: PR Review ────────────────────────────────────────────

    def _do_pr_review(self, wf: dict):
        """Create PR and handle code review rounds."""
        wf = self.workflow
        round_num = wf.get("current_round", 0) + 1
        max_rounds = wf.get("max_pr_review_rounds", 5)
        dev_round = wf.get("dev_round", 1)
        branch_name = wf.get("branch_name", "")
        gh = self._get_gh()

        # Check if branch has any changes vs main
        has_changes = False
        try:
            diff_stats = gh.get_diff_stats("main", branch_name)
            has_changes = diff_stats.get("commits", 0) > 0
        except Exception:
            pass

        if not has_changes:
            # No code changes produced — skip PR, post to issue, and mark completed
            issue_number = wf.get("github_issue_number")
            no_change_msg = (
                f"## ℹ️ No Changes Detected\n\n"
                f"Agent completed dev round {dev_round} without producing code changes. "
                f"Skipping PR creation."
            )
            if issue_number:
                try:
                    gh.add_issue_comment(issue_number, no_change_msg)
                except GitHubOpsError:
                    pass
            self._create_milestone(
                phase="pr_review",
                dev_round=dev_round,
                milestone_type="no_changes",
                status="completed",
                title="No code changes produced",
                result_summary="Agent did not produce any code changes. Skipping PR creation.",
            )
            self._update_workflow(
                {
                    "status": "completed",
                    "current_phase": "completed",
                    "error_message": "",
                }
            )
            self._emit("phase_change", {"phase": "completed"})
            return

        # Ensure branch is pushed to remote before PR creation
        try:
            gh.git_push(branch=branch_name)
        except Exception as e:
            logger.warning("Failed to push branch %s: %s", branch_name, e)

        # Create PR on first round
        if round_num == 1:
            try:
                # Build PR body with issue linkage
                pr_body = f"Autonomous development for dev round {dev_round}.\n\nRequirements: {wf.get('requirements_text', '')[:500]}"
                issue_number = wf.get("github_issue_number")
                if issue_number:
                    pr_body += f"\n\nCloses #{issue_number}"

                pr_data = gh.create_pr(
                    title=f"[Auto] Dev round {dev_round}: {wf.get('title', 'Autonomous development')}",
                    body=pr_body,
                    head=branch_name,
                    base="main",
                )
                pr_number = pr_data.get("number")
                pr_url = pr_data.get("url", "")
                self._create_milestone(
                    phase="pr_review",
                    dev_round=dev_round,
                    milestone_type="pr_created",
                    status="completed",
                    title=f"PR #{pr_number} created",
                    github_pr_number=pr_number,
                    result_summary=pr_url,
                )
                self._update_workflow(
                    {
                        "github_pr_number": pr_number,
                        "github_pr_url": pr_url,
                    }
                )
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="pr_review",
                    milestone_type="pr_created",
                    status="failed",
                    title="PR creation failed",
                    error_message=str(e),
                )
                raise

        pr_number = wf.get("github_pr_number")
        if not pr_number:
            pr_number = self.workflow.get("github_pr_number")

        # Code review
        review_ms = self._create_milestone(
            phase="pr_review",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type="pr_reviewed",
            status="in_progress",
            title=f"PR review round {round_num}",
        )

        # Get diff for review
        diff_text = ""
        try:
            diff_text = gh.get_diff("main", branch_name)
        except Exception:
            pass

        review_prompt = (
            AUTONOMOUS_CONTEXT + f"你是一位资深代码审查专家。请审查以下 PR 的代码变更。\n\n"
            f"## 需求\n{wf.get('requirements_text', '')[:500]}\n\n"
            f"## 代码变更\n{diff_text[:8000]}\n\n"
            f"请检查：\n"
            f"1. 代码质量和可读性\n"
            f"2. 潜在 bug 和安全问题\n"
            f"3. 测试覆盖率\n"
            f"4. 性能影响\n"
            f"5. 与需求的对齐程度\n\n"
            f"如果没有重大问题，请明确说明'代码审查通过'。"
        )

        review_result = self._runner.run_agent_task(
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=review_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
        )

        self._accumulate_tokens(review_result)

        review_text = review_result.response_text or ""
        self.repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {
                "status": "completed" if review_result.success else "failed",
                "review_content": review_text,
                "review_session_id": review_result.session_id,
            },
        )

        # Post review as PR comment
        if pr_number:
            try:
                gh.add_pr_comment(
                    pr_number, f"## 🔍 Code Review (Round {round_num})\n\n{review_text}"
                )
            except GitHubOpsError:
                pass

        # Check if all rounds done
        self._update_workflow({"current_round": round_num})

        if round_num >= max_rounds:
            # All PR review rounds completed — move to report
            self._update_workflow(
                {
                    "current_phase": "report",
                    "status": "reporting",
                }
            )
            self._emit("phase_change", {"phase": "report"})
        else:
            # Fix issues and continue to next round
            fix_ms = self._create_milestone(
                phase="pr_review",
                dev_round=dev_round,
                round_number=round_num,
                milestone_type="pr_updated",
                status="in_progress",
                title=f"PR fixes round {round_num}",
            )

            fix_prompt = (
                AUTONOMOUS_CONTEXT + f"根据以下代码审查意见修改代码：\n\n{review_text}\n\n"
                f"修改后提交 git commit 并推送。"
            )

            fix_result = self._runner.run_agent_task(
                workflow_id=self._workflow_id,
                cli_tool=wf.get("cli_tool", "claude-code"),
                model=wf.get("model", ""),
                project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                prompt=fix_prompt,
                workspace_type=wf.get("workspace_type", "local"),
                remote_machine_id=wf.get("remote_machine_id"),
            )

            self._accumulate_tokens(fix_result)

            commit_sha = ""
            try:
                gh.git_push()
                commit_sha = gh.get_current_commit()
            except Exception:
                pass

            self.repo.update_milestone(
                fix_ms.get("milestone_id", ""),
                {
                    "status": "completed" if fix_result.success else "failed",
                    "session_id": fix_result.session_id,
                    "commit_shas": json.dumps([commit_sha] if commit_sha else []),
                },
            )

            if pr_number:
                try:
                    gh.add_pr_comment(
                        pr_number, f"✅ Addressed review feedback (round {round_num})"
                    )
                except GitHubOpsError:
                    pass

    # ── Phase: Report ───────────────────────────────────────────────

    def _do_report(self, wf: dict):
        """Generate progress report and update issue."""
        dev_round = wf.get("dev_round", 1)
        gh = self._get_gh()
        issue_number = wf.get("github_issue_number")
        pr_number = wf.get("github_pr_number")

        # Collect milestone summaries
        milestones = self.repo.list_milestones(self._workflow_id, dev_round=dev_round)
        summary_parts = []
        for ms in milestones:
            if ms.get("status") == "completed" and ms.get("result_summary"):
                summary_parts.append(f"- {ms.get('title', '')}: {ms['result_summary'][:100]}")

        # Get diff stats
        diff_stats = {}
        try:
            branch = wf.get("branch_name", "")
            diff_stats = gh.get_diff_stats("main", branch)
        except Exception:
            pass

        report = (
            f"## 📊 Dev Round {dev_round} Progress Report\n\n"
            f"### Completed\n" + "\n".join(summary_parts[:20]) + "\n\n"
            f"### Stats\n"
            f"- Tokens: {wf.get('total_tokens', 0):,}\n"
            f"- Requests: {wf.get('total_requests', 0)}\n"
        )
        if diff_stats:
            report += f"- Changes: +{diff_stats.get('additions', 0)}/-{diff_stats.get('deletions', 0)} ({diff_stats.get('files', 0)} files)\n"
        if pr_number:
            report += f"- PR: #{pr_number}\n"

        self._create_milestone(
            phase="report",
            dev_round=dev_round,
            milestone_type="progress_reported",
            status="completed",
            title=f"Progress report for round {dev_round}",
            result_summary=report[:500],
        )

        # Post report to issue
        if issue_number:
            try:
                gh.add_issue_comment(issue_number, report)
            except GitHubOpsError:
                pass

        # Mark round completed
        self._create_milestone(
            phase="report",
            dev_round=dev_round,
            milestone_type="round_completed",
            status="completed",
            title=f"Dev round {dev_round} completed",
        )

        # Record wait phase start time for comment filtering
        self._create_milestone(
            phase="report",
            dev_round=dev_round,
            milestone_type="wait_started",
            status="completed",
            title="Wait phase starting",
            metadata=json.dumps({"wait_started_at": datetime.now(timezone.utc).isoformat()}),
        )

        # Move to wait phase
        self._update_workflow(
            {
                "current_phase": "wait",
                "status": "waiting",
            }
        )
        self._emit("phase_change", {"phase": "wait"})

    # ── Phase: Wait ─────────────────────────────────────────────────

    def _do_wait(self, wf: dict):
        """Poll for new requirements or completion signal."""
        issue_number = wf.get("github_issue_number")
        gh = self._get_gh()

        if not issue_number:
            return  # No issue to check

        # Get the time when wait phase started (set by _do_report)
        # This ensures only user comments AFTER the report are considered
        wait_start = ""
        milestones = self.repo.list_milestones(self._workflow_id)
        for ms in reversed(milestones):
            if ms.get("milestone_type") == "wait_started" and ms.get("metadata"):
                try:
                    meta = json.loads(ms["metadata"])
                    wait_start = meta.get("wait_started_at", "")
                except (json.JSONDecodeError, TypeError):
                    pass
                break

        try:
            comments = gh.list_issue_comments(
                issue_number, since=wait_start if wait_start else None
            )
        except GitHubOpsError:
            return

        if not comments:
            return  # No new comments

        # Filter out bot's own comments (comments authored by the automation)
        bot_author_keywords = ["open-ace-bot", "autonomous", "bot"]
        user_comments = [
            c
            for c in comments
            if not any(
                kw in (c.get("author", {}).get("login", "") or "").lower()
                for kw in bot_author_keywords
            )
        ]

        # Check for completion signals — match whole words/lines only
        for comment in reversed(user_comments):
            body = comment.get("body", "")
            for keyword in COMPLETION_KEYWORDS:
                # Match as standalone phrase: at line start or preceded by whitespace,
                # followed by whitespace, punctuation, or end of string
                pattern = re.compile(
                    rf"(?:^|\s){re.escape(keyword)}(?:[\s,，。.!！?？、;；:：\n]|$)",
                    re.IGNORECASE,
                )
                if pattern.search(body):
                    self._update_workflow(
                        {
                            "current_phase": "merge",
                            "status": "merging",
                        }
                    )
                    self._emit("phase_change", {"phase": "merge"})
                    return

        # No user comments left after filtering
        if not user_comments:
            return

        # New requirements detected
        new_req_comment = user_comments[-1]  # Latest user comment
        new_requirements = new_req_comment.get("body", "")
        # Use correct field name from gh CLI (camelCase)
        comment_time = new_req_comment.get("createdAt", "")

        self._create_milestone(
            phase="wait",
            milestone_type="requirement_received",
            status="completed",
            title="New requirements detected",
            result_summary=new_requirements[:200],
            metadata=json.dumps({"last_comment_time": comment_time}),
        )

        # Start new dev round
        self._update_workflow(
            {
                "current_phase": "planning",
                "status": "planning",
                "current_round": 0,
                "dev_round": wf.get("dev_round", 1) + 1,
                "requirements_text": new_requirements,
            }
        )
        self._emit("phase_change", {"phase": "planning", "dev_round": wf.get("dev_round", 1) + 1})

    # ── Phase: Merge ────────────────────────────────────────────────

    def _do_merge(self, wf: dict):
        """Merge PR and clean up. Resolves merge conflicts automatically."""
        gh = self._get_gh()
        pr_number = wf.get("github_pr_number")
        branch_name = wf.get("branch_name", "")

        if pr_number:
            try:
                gh.merge_pr(pr_number, strategy="merge")
                self._create_milestone(
                    phase="merge",
                    milestone_type="merged",
                    status="completed",
                    title=f"PR #{pr_number} merged",
                )
            except GitHubOpsError:
                # Merge conflict — resolve locally and retry
                logger.info("PR #%s not mergeable, resolving conflicts", pr_number)
                try:
                    self._resolve_merge_conflicts(gh, branch_name, pr_number)
                except Exception as e:
                    self._create_milestone(
                        phase="merge",
                        milestone_type="merged",
                        status="failed",
                        title="PR merge failed",
                        error_message=f"Merge conflict resolution failed: {e}",
                    )
                    raise

        # Clean up branch/worktree
        branch_name = wf.get("branch_name", "")
        worktree_path = wf.get("worktree_path", "")
        try:
            if worktree_path:
                gh.remove_worktree(worktree_path)
            if branch_name:
                gh.delete_branch(branch_name)
            self._create_milestone(
                phase="merge",
                milestone_type="cleaned_up",
                status="completed",
                title="Branch/worktree cleaned up",
            )
        except GitHubOpsError as e:
            logger.warning("Cleanup failed: %s", e)

        # Mark workflow completed
        self._update_workflow(
            {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._emit("phase_change", {"phase": "completed"})

    def _resolve_merge_conflicts(self, gh: GitHubOps, branch_name: str, pr_number: int):
        """Resolve merge conflicts locally, push, and merge the PR."""
        # Remove worktree if it's blocking checkout (must do before any git ops
        # on the main repo, since removing worktree deletes its directory)
        wf = self.workflow
        worktree_path = wf.get("worktree_path", "")
        project_path = wf.get("project_path", "")
        if worktree_path:
            try:
                # Use a separate GitHubOps for the main repo to remove worktree
                main_gh = GitHubOps(project_path)
                main_gh.remove_worktree(worktree_path)
            except Exception:
                pass
            self._update_workflow({"worktree_path": ""})
            # Reinitialize gh to point at project_path (worktree dir is gone)
            self._gh = GitHubOps(project_path)
            gh = self._gh

        # Clean up any leftover git state (conflicts, uncommitted changes)
        gh._run_git(["reset", "--hard", "HEAD"])
        gh._run_git(["clean", "-fd"])
        # Fetch latest main and checkout our branch
        gh._run_git(["fetch", "origin", "main"])
        gh._run_git(["checkout", branch_name])
        try:
            gh._run_git(["merge", "origin/main"])
        except GitHubOpsError:
            # There are conflicts — use AI agent to resolve them
            gh._run_git(["merge", "--abort"])  # Clean state first
            merge_result = gh._run_git(["merge", "origin/main"], check=False)
            if merge_result.returncode != 0 and "CONFLICT" not in merge_result.stderr:
                raise GitHubOpsError(
                    f"git merge failed (non-conflict): {merge_result.stderr.strip()}"
                )

            # Ask AI agent to resolve conflicts
            conflict_prompt = (
                AUTONOMOUS_CONTEXT
                + "当前分支与 main 存在合并冲突。请解决所有冲突文件中的冲突标记，"
                "保留两边的有效修改。解决完成后执行 git add 并 git commit。\n\n"
                "步骤：\n"
                "1. 查看所有冲突文件：git diff --name-only --diff-filter=U\n"
                "2. 逐个解决冲突标记（<<<<<<, ======, >>>>>>）\n"
                "3. git add 所有解决后的文件\n"
                "4. git commit 完成合并"
            )

            wf = self.workflow
            result = self._runner.run_agent_task(
                workflow_id=self._workflow_id,
                cli_tool=wf.get("cli_tool", "claude-code"),
                model=wf.get("model", ""),
                project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                prompt=conflict_prompt,
                workspace_type=wf.get("workspace_type", "local"),
                remote_machine_id=wf.get("remote_machine_id"),
            )

            if not result.success:
                raise RuntimeError(f"Conflict resolution failed: {result.error}")

        # Push the merged branch and retry PR merge
        gh.git_push(branch=branch_name)
        gh.merge_pr(pr_number, strategy="merge")

        self._create_milestone(
            phase="merge",
            milestone_type="merged",
            status="completed",
            title=f"PR #{pr_number} merged (conflicts resolved)",
        )
