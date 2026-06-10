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
import threading
import time
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

# Pre-compiled patterns for completion keyword matching (avoids recompilation in loop)
_COMPLETION_PATTERNS = [
    re.compile(
        rf"(?:^|\s){re.escape(kw)}(?:[\s,，。.!！?？、;；:：\n]|$)",
        re.IGNORECASE,
    )
    for kw in COMPLETION_KEYWORDS
]

# Prefix added to all prompts to inform the agent it is running autonomously
AUTONOMOUS_CONTEXT = (
    "## 重要提示\n"
    "你正在无人值守的自动化工作流中运行。请遵守以下规则：\n"
    "1. 不要请求人类确认或等待权限批准，如果操作被阻止请跳过并继续\n"
    "2. 不要使用需要交互式确认的 gh CLI 命令（如 gh pr create）\n"
    "3. 直接执行文件修改、git 操作等，不要仅输出方案文本\n"
    "4. 遇到权限问题时跳过该步骤继续执行其他任务\n\n"
)

# Prefix for planning phase — restricts agent to read-only analysis only.
# Rule #3 overrides AUTONOMOUS_CONTEXT's "直接执行文件修改" to prevent
# the planning agent from writing code (see Issue #761).
PLANNING_CONTEXT = (
    "## 重要提示\n"
    "你正在自动化工作流的分析和方案设计阶段运行。请遵守以下规则：\n"
    "1. 不要请求人类确认或等待权限批准，如果操作被阻止请跳过并继续\n"
    "2. 不要使用需要交互式确认的 gh CLI 命令（如 gh pr create）\n"
    "3. 只进行分析、阅读代码、输出方案文本，不要修改任何文件或执行写操作\n"
    "4. 如果需要查看项目代码以制定方案，可以使用文件读取和搜索工具\n"
    "5. 不要执行 git commit、git push、文件写入、文件编辑等操作\n"
    "6. 直接输出结构化方案内容，不要添加引导文字(如'我来...'、'让我...'、"
    "'首先...'等)或结尾引导(如'下一步是否...'、'建议...'等)\n\n"
)

# Read-only tool sets for planning phase, keyed by CLI tool name.
# When empty (Codex/OpenClaw), rely on PLANNING_CONTEXT prompt +
# selective auto-approve filtering (Layer 1 + Layer 3).
PLANNING_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": [
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
        "Agent",
        "TaskRead",
        "TaskGet",
        "TaskList",
    ],
    "qwen-code-cli": [
        "read_file",
        "list_files",
        "search_files",
        "code_search",
        "web_search",
        "web_fetch",
    ],
    "codex": [],
    "openclaw": [],
}

# Maximum time (seconds) for a single planning agent call.
# Normal planning should complete in a few minutes; this caps runaway agents.
PLANNING_TIMEOUT = 600

# Minimum review text length (chars) to be considered substantive feedback.
# Below this threshold the review is treated as "no feedback" (e.g. empty
# output or trivially short responses).  The fallback message injected for
# empty reviews is ~60 chars, so it exceeds this threshold and correctly
# triggers a refinement round.
REVIEW_FEEDBACK_MIN_LENGTH = 50

# Minimum review text length (chars) for an approved review to be considered
# as having substantive improvement suggestions.  A brief approval like
# "审查结论：方案通过审查。" is ~12 chars — well below this threshold.
# Reviews exceeding this length after approval likely contain actionable
# suggestions worth incorporating via the refinement step.
REVIEW_SUGGESTIONS_MIN_LENGTH = 200

# Phase ordering — used by fork to determine the next phase after the fork point.
PHASE_ORDER = ["preparation", "planning", "development", "pr_review", "report", "merge"]

# Maps phases to their corresponding workflow status values
PHASE_STATUS_MAP = {
    "preparation": "preparing",
    "planning": "planning",
    "development": "developing",
    "pr_review": "pr_review",
    "report": "reporting",
    "merge": "merging",
}

# CI check polling configuration.
# After a PR is created or code is pushed, CI checks may still be pending.
# These control how long we wait before proceeding with the review.
CI_POLL_INTERVAL = 30  # seconds between polls
CI_POLL_MAX_WAIT = 300  # maximum seconds to wait (5 minutes)

# Maximum character length for previous review feedback included in
# the review prompt.  Reviews longer than this are truncated with a
# notice so the reviewer knows content was omitted.
PREV_REVIEW_MAX_LENGTH = 3000

# Agent intro/closing text patterns for _clean_agent_text().
# These match common Chinese agent narration phrases that should not
# appear in GitHub comments.
_AGENT_INTRO_PATTERNS = [
    re.compile(r"^我来[^\n]{0,30}[。！]"),
    re.compile(r"^让我[^\n]{0,30}[。！]"),
    re.compile(r"^首先[让我]*[^\n]{0,30}[。！]"),
    re.compile(r"^现在[我来让]*[^\n]{0,30}[。！]"),
    re.compile(r"^好的[，,][^\n]{0,30}[。！]"),
    re.compile(r"^方案[已完]*[^\n]{0,20}[。，！]"),
    re.compile(r"^探索完成[^\n]*"),
    re.compile(r"^分析完成[^\n]*"),
]
_AGENT_CLOSING_PATTERNS = [
    re.compile(r"下一步是否需要"),
    re.compile(r"是否需要开始"),
    re.compile(r"按照[^\n]*流程"),
    re.compile(r"按照[^\n]*工作流"),
    re.compile(r"建议[：:]\s*$"),
]

# 429 rate limit retry configuration.
# Rate limit typically resolves within 30 minutes.
API_RETRY_TOTAL_TIMEOUT = 1800  # max total retry duration (seconds)
API_RETRY_INITIAL_DELAY = 30  # first retry delay (seconds)
API_RETRY_MAX_DELAY = 300  # max single retry delay (seconds)

# Test failure retry configuration.
MAX_TEST_RETRIES = 2  # max retries when test agent itself fails
MAX_DEV_RETRIES_ON_TEST_FAIL = 2  # max dev round retries for unfixable test failures


def _next_phase(current_phase: str) -> str:
    """Return the phase that follows current_phase."""
    try:
        idx = PHASE_ORDER.index(current_phase)
    except ValueError:
        return "planning"
    if idx + 1 < len(PHASE_ORDER):
        return PHASE_ORDER[idx + 1]
    return "merge"


class AutonomousOrchestrator:
    """Drives a single autonomous workflow through its phases."""

    def __init__(self, workflow_id: str, db: Database = None):
        self.db = db or Database()
        self.repo = AutonomousWorkflowRepository(self.db)
        self.emitter = AutonomousEventEmitter.instance()
        self._workflow_id = workflow_id
        self._current_session_id: Optional[str] = None
        self._session_lock = threading.Lock()
        self._cancel_requested = threading.Event()  # in-memory cancel signal

        # Wire session_manager so agent sessions are persisted to DB
        from app.modules.workspace.session_manager import SessionManager

        session_manager = SessionManager()
        self._runner = AutonomousAgentRunner(
            session_manager=session_manager,
            activity_callback=self._on_agent_activity,
        )
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

    def _is_fork_workflow(self, wf: dict) -> bool:
        """Check if this workflow was created by a fork operation."""
        return bool(wf.get("parent_workflow_id"))

    def _get_user_feedback_prompt(self, wf: dict) -> str:
        """Return a prompt section with user feedback, or empty string."""
        feedback = wf.get("user_feedback", "")
        if not feedback or not feedback.strip():
            return ""
        return (
            "\n\n## ⚠️ 用户反馈/指令\n"
            "用户提供了以下反馈，请在工作中充分考虑并优先执行：\n"
            f"{feedback}\n\n"
        )

    @staticmethod
    def _clean_agent_text(text: str) -> str:
        """Strip agent narration/intro/closing text, keep only structured content.

        Performs three cleaning passes:
        1. Strip everything before the first markdown heading (# Title).
        2. Strip leading lines matching common agent intro patterns
           (e.g. "我来为这个需求制定详细的实现方案。").
        3. Strip trailing lines matching common agent closing patterns
           (e.g. "下一步是否需要开始实施...").
        """
        if not text:
            return text

        # Pass 1: strip before first markdown heading
        match = re.search(r"^#{1,6}\s", text, re.MULTILINE)
        if match:
            text = text[match.start() :]

        # Pass 2: strip leading agent intro lines, skipping headings/empty lines.
        # Scan up to the first _INTRO_SCAN_LIMIT non-empty, non-heading lines.
        # Only strip the FIRST contiguous intro block — once we see a heading
        # after intro lines, stop so that legitimate sub-headings between
        # intro lines are not deleted.
        _INTRO_SCAN_LIMIT = 5
        lines = text.split("\n")
        intro_end = 0  # line index after the last contiguous intro line
        non_heading_count = 0
        seen_intro = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            is_heading = bool(re.match(r"^#{1,6}\s", stripped))
            if is_heading:
                if seen_intro:
                    break  # stop — don't strip across sub-headings
                continue  # skip initial headings before any intro
            non_heading_count += 1
            if non_heading_count > _INTRO_SCAN_LIMIT:
                break  # past scan limit, stop
            is_intro = any(p.search(stripped) for p in _AGENT_INTRO_PATTERNS)
            if is_intro:
                intro_end = i + 1
                seen_intro = True
            else:
                break  # non-intro content found, stop
        if intro_end > 0:
            lines = lines[intro_end:]
            text = "\n".join(lines)

        # Pass 3: strip trailing agent closing text.
        # Walk forward to find the first closing pattern match, then strip
        # everything from that line to the end (including subsequent non-matching
        # lines like numbered lists that are part of the closing block).
        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and any(p.search(stripped) for p in _AGENT_CLOSING_PATTERNS):
                # Strip empty lines before the closing text as well
                while i > 0 and not lines[i - 1].strip():
                    i -= 1
                text = "\n".join(lines[:i])
                break

        return text.strip()

    # Backward-compatible alias for existing tests (Issue #906, #910)
    @staticmethod
    def _clean_plan_output(text: str) -> str:
        return AutonomousOrchestrator._clean_agent_text(text)

    @staticmethod
    def _should_show_review_warning(round_num: int, max_rounds: int, last_review: str) -> bool:
        """Whether to append a "feedback not yet addressed" warning.

        True when the planning loop exhausted all rounds without the
        reviewer approving the plan (no "方案通过审查" in the last review).
        """
        return bool(last_review and round_num >= max_rounds and "方案通过审查" not in last_review)

    @staticmethod
    def _should_refine_plan(last_review: str) -> bool:
        """Whether the approved review contains substantive suggestions.

        True when the review approved the plan ("方案通过审查" present)
        *and* the review text is long enough to contain actionable
        improvement suggestions (exceeds REVIEW_SUGGESTIONS_MIN_LENGTH).
        """
        if not last_review:
            return False
        review_approved = "方案通过审查" in last_review
        return review_approved and len(last_review.strip()) > REVIEW_SUGGESTIONS_MIN_LENGTH

    @staticmethod
    def _smart_truncate_diff(
        diff_text: str, max_chars: int = 32000, per_file_lines: int = 200
    ) -> str:
        """Smart diff truncation that preserves all file headers.

        Keeps ``diff --git a/...`` lines intact and truncates each file's
        content to *per_file_lines* lines.  If the total still exceeds
        *max_chars* an explanatory note is appended.
        """
        if not diff_text or len(diff_text) <= max_chars:
            return diff_text

        import re

        # Split into per-file chunks (each starts with a diff --git line)
        chunks = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
        result_parts: list[str] = []
        total = 0

        for chunk in chunks:
            if not chunk:
                continue
            lines = chunk.split("\n")
            header = lines[0] if lines else ""
            body = "\n".join(lines[1 : per_file_lines + 1])

            part = header + "\n" + body
            if total + len(part) > max_chars:
                # Add truncation note and stop
                result_parts.append(
                    f"\n... [Truncated: {len(chunks)} files total, "
                    f"showing {len(result_parts)} with {per_file_lines} lines each]\n"
                )
                break
            result_parts.append(part)
            total += len(part)

        return "\n".join(result_parts)

    @staticmethod
    def _is_pre_existing_ci_failure(response: str) -> bool:
        """Whether the agent identified CI failures as pre-existing.

        Checks for (in order of priority):
          1. Structured ``CI_STATUS: pre-existing`` tag
          2. Legacy Chinese keyword ``预先存在``
          3. Legacy English ``pre-existing`` / ``pre existing``
        """
        if not response:
            return False
        return bool(
            re.search(r"CI_STATUS:\s*pre-existing", response)
            or "预先存在" in response
            or re.search(r"pre[\s-]?existing", response, re.IGNORECASE)
        )

    def _find_existing_milestone(
        self, phase: str, milestone_type: str, dev_round: int = None, round_number: int = None
    ) -> Optional[dict]:
        """Check if a milestone of this type already exists (idempotency guard)."""
        existing = self.repo.list_milestones(self._workflow_id, phase=phase, status="in_progress")
        # Also check completed ones
        completed = self.repo.list_milestones(self._workflow_id, phase=phase, status="completed")
        candidates = existing + completed

        for ms in candidates:
            if ms.get("milestone_type") != milestone_type:
                continue
            if dev_round is not None and ms.get("dev_round") != dev_round:
                continue
            if round_number is not None and ms.get("round_number") != round_number:
                continue
            return ms
        return None

    def _create_milestone(self, **kwargs) -> dict:
        """Create a milestone and emit event. Idempotent — returns existing if found."""
        kwargs.setdefault("workflow_id", self._workflow_id)

        # Idempotency guard: skip creation if matching milestone already exists
        existing = self._find_existing_milestone(
            phase=kwargs.get("phase", ""),
            milestone_type=kwargs.get("milestone_type", ""),
            dev_round=kwargs.get("dev_round"),
            round_number=kwargs.get("round_number"),
        )
        if existing:
            logger.info(
                "Milestone already exists (id=%s, type=%s), skipping creation",
                existing.get("milestone_id", "")[:8],
                kwargs.get("milestone_type", ""),
            )
            return existing

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

    def _poll_ci_status(self, gh: GitHubOps, pr_number: int) -> list:
        """Poll CI checks until all are non-pending or timeout is reached.

        Returns the final list of CI check dicts (may still contain pending
        items if the timeout was reached).
        """
        deadline = time.monotonic() + CI_POLL_MAX_WAIT
        while True:
            try:
                checks = gh.get_pr_checks(pr_number)
            except Exception:
                logger.warning("CI check query failed for PR #%s, will retry...", pr_number)
                if time.monotonic() >= deadline:
                    return []
                time.sleep(CI_POLL_INTERVAL)
                continue
            if not checks:
                # No checks configured or parse failure — nothing to wait for.
                return checks
            pending = [c for c in checks if c.get("bucket") == "pending"]
            if not pending:
                return checks
            if time.monotonic() >= deadline:
                logger.warning(
                    "CI polling timed out after %ds for PR #%s (%d checks still pending)",
                    CI_POLL_MAX_WAIT,
                    pr_number,
                    len(pending),
                )
                return checks
            logger.info(
                "CI still running for PR #%s (%d pending), waiting %ds...",
                pr_number,
                len(pending),
                CI_POLL_INTERVAL,
            )
            time.sleep(CI_POLL_INTERVAL)

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
            },
        )
        # Recalculate request count from actual session_messages
        self.repo.recalculate_workflow_requests(self._workflow_id)

    def _on_agent_activity(self, session_id: str, activity: dict):
        """Forward agent activity to the SSE event stream and update tokens."""
        self.emitter.emit(
            self._workflow_id,
            "agent_activity",
            {
                "session_id": session_id,
                **activity,
            },
        )
        # Real-time token update (only on usage events to avoid DB thrashing)
        if activity.get("type") == "usage":
            try:
                self.repo.update_workflow_tokens(
                    self._workflow_id,
                    {
                        "total_tokens": activity.get("total_tokens", 0),
                        "total_input_tokens": activity.get("total_input_tokens", 0),
                        "total_output_tokens": activity.get("total_output_tokens", 0),
                    },
                )
            except Exception:
                logger.warning("Failed to update workflow tokens in real-time", exc_info=True)

    def _link_session_to_current_milestone(self, session_id: str):
        """Write session_id to the latest in_progress milestone immediately."""
        try:
            milestones = self.repo.list_milestones(self._workflow_id, status="in_progress")
            if milestones:
                ms = milestones[-1]  # most recent
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        "session_id": session_id,
                    },
                )
        except Exception:
            logger.warning("Failed to link session to milestone", exc_info=True)

    @staticmethod
    def _is_rate_limited(response: str) -> bool:
        """Detect 429 rate limit errors in agent response or error text."""
        if not response:
            return False
        response_lower = response.lower()
        return any(
            p in response_lower
            for p in ["429", "quota exceeded", "rate limit", "too many requests"]
        )

    def _run_agent(self, wf: dict = None, **kwargs) -> AgentTaskResult:
        """Run an agent task with session tracking and 429 retry support.

        Args:
            wf: Optional pre-fetched workflow dict to avoid extra DB queries.
                If not provided, falls back to self.workflow (DB query).
        """
        # Pre-generate session_id so cancel_current_task() can access it
        # before run_agent_task() returns
        if "session_id" not in kwargs:
            kwargs["session_id"] = str(uuid.uuid4())

        with self._session_lock:
            self._current_session_id = kwargs["session_id"]

        # Immediately link session to in_progress milestone so frontend
        # can show session details while the agent is still running
        self._link_session_to_current_milestone(kwargs["session_id"])

        # Inject per-workflow timeout if specified
        if "timeout" not in kwargs:
            workflow_data = wf or self.workflow
            task_timeout = (workflow_data or {}).get("task_timeout")
            if task_timeout:
                kwargs["timeout"] = int(task_timeout)

        result = self._runner.run_agent_task(**kwargs)

        # 429 rate limit retry — exponential backoff, max 30 minutes total.
        # Use interruptible sleep (check cancel signal every 5s) so the
        # orchestrator can be paused/stopped during a retry wait.
        _CANCEL_POLL_INTERVAL = 5  # seconds between cancel checks
        retry_start = time.monotonic()
        delay = API_RETRY_INITIAL_DELAY
        while (time.monotonic() - retry_start) < API_RETRY_TOTAL_TIMEOUT:
            response_text = result.response_text or ""
            error_text = result.error or ""
            if not (self._is_rate_limited(response_text) or self._is_rate_limited(error_text)):
                break  # Not a 429 error, no retry needed

            elapsed = int(time.monotonic() - retry_start)
            logger.warning(
                "API rate limit (429) detected, retrying in %ds (elapsed: %ds / %ds)",
                delay,
                elapsed,
                API_RETRY_TOTAL_TIMEOUT,
            )
            self._emit(
                "rate_limit_retry",
                {
                    "delay": delay,
                    "elapsed": elapsed,
                    "total_timeout": API_RETRY_TOTAL_TIMEOUT,
                },
            )

            # Interruptible sleep: check for cancellation every 5s.
            # Use in-memory flag instead of DB query to avoid overhead.
            slept = 0
            self._cancel_requested.clear()
            while slept < delay:
                time.sleep(min(_CANCEL_POLL_INTERVAL, delay - slept))
                slept += _CANCEL_POLL_INTERVAL
                if self._cancel_requested.is_set():
                    logger.info("429 retry cancelled (cancel requested)")
                    with self._session_lock:
                        self._current_session_id = result.session_id
                    return result

            delay = min(delay * 2, API_RETRY_MAX_DELAY)

            # Generate new session_id for retry
            kwargs["session_id"] = str(uuid.uuid4())
            with self._session_lock:
                self._current_session_id = kwargs["session_id"]
            self._link_session_to_current_milestone(kwargs["session_id"])
            result = self._runner.run_agent_task(**kwargs)

        with self._session_lock:
            self._current_session_id = result.session_id
        return result

    def cancel_current_task(self):
        """Cancel the currently running agent task (e.g. on pause/stop)."""
        self._cancel_requested.set()  # signal 429 retry loop to stop
        with self._session_lock:
            session_id = self._current_session_id
        if session_id:
            logger.info(
                "Cancelling current agent task session=%s",
                session_id[:8],
            )
            try:
                self._runner.stop_session(session_id)
            except Exception as e:
                logger.warning("Failed to stop session %s: %s", session_id[:8], e)
            with self._session_lock:
                self._current_session_id = None

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
        """Set up project, create/read issue, create branch.

        For fork workflows (parent_workflow_id set):
        - Skip repo/issue creation (already exist from parent)
        - Force worktree strategy for parallel execution
        - Jump to the next phase after the fork milestone's phase
        """
        project_path = wf.get("project_path", "")

        # --- Fork workflow fast path ---
        if self._is_fork_workflow(wf):
            gh = self._get_gh()
            branch_name = wf.get("branch_name", "")
            if not branch_name:
                branch_name = f"fork/{self._workflow_id[:8]}"

            # Determine the base commit from the fork milestone
            fork_milestone_id = wf.get("fork_milestone_id", "")
            fork_ms = self.repo.get_milestone(fork_milestone_id) if fork_milestone_id else None
            base_ref = "origin/main"
            if fork_ms:
                commit_shas = fork_ms.get("commit_shas", "")
                if commit_shas:
                    try:
                        shas = json.loads(commit_shas)
                        if shas:
                            base_ref = shas[-1]
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Force worktree for parallel execution
            try:
                gh._run_git(["fetch", "origin", "main"])
                wt_path = f"{project_path}/../{branch_name.replace('/', '-')}"
                gh.create_worktree(path=wt_path, branch=branch_name, base=base_ref)
                self._update_workflow(
                    {
                        "worktree_path": wt_path,
                        "branch_name": branch_name,
                        "branch_strategy": "worktree",
                    }
                )
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="completed",
                    title=f"Fork branch '{branch_name}' created (worktree)",
                )
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="failed",
                    title="Fork branch creation failed",
                    error_message=str(e),
                )
                raise

            # Jump to the next phase after the fork point's phase
            fork_phase = fork_ms.get("phase", "planning") if fork_ms else "planning"
            next_phase = _next_phase(fork_phase)
            self._update_workflow(
                {
                    "current_phase": next_phase,
                    "status": PHASE_STATUS_MAP.get(next_phase, "planning"),
                    "current_round": 0,
                }
            )
            self._emit("phase_change", {"phase": next_phase, "fork": True})
            return

        # --- Normal workflow path ---

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
                        base="origin/main",
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
                PLANNING_CONTEXT + f"你是一个高级开发工程师。请根据以下审查意见完善实现方案。\n\n"
                f"## 原始需求\n{requirements}\n\n"
                f"## 审查意见\n{review_text}\n\n"
                f"## 原方案\n{existing_plan}\n\n"
                f"请输出完善后的完整实现方案。\n\n"
                f"重要约束：只输出高层设计方案和实现步骤描述，"
                f"不要输出完整的代码实现。具体代码将在后续开发阶段编写。"
            )
            prompt += self._get_user_feedback_prompt(wf)
            milestone_type = "plan_refined"
        else:
            prompt = (
                PLANNING_CONTEXT + f"你是一个高级开发工程师。请为以下需求制定详细的实现方案。\n\n"
                f"## 需求\n{requirements}\n\n"
                f"## 项目路径\n{wf.get('project_path', '')}\n\n"
                f"请用 plan mode 创建方案，包含：\n"
                f"1. 需求分析和拆分\n"
                f"2. 技术方案和架构设计\n"
                f"3. 实现步骤（按优先级排序）\n"
                f"4. 测试策略\n"
                f"5. 潜在风险和缓解措施\n\n"
                f"重要约束：只输出高层设计方案和实现步骤描述，"
                f"不要输出完整的代码实现。具体代码将在后续开发阶段编写。"
            )
            prompt += self._get_user_feedback_prompt(wf)
            milestone_type = "plan_created"

        ms = self._create_milestone(
            phase="planning",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type=milestone_type,
            status="in_progress",
            title=f"Plan round {round_num}: {milestone_type.replace('_', ' ')}",
        )

        # Planning phase: restrict to read-only tools + capped timeout
        planning_allowed = PLANNING_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), [])
        # Base planning timeout + any user extension (via extend-planning-timeout API)
        extension = int(wf.get("planning_timeout_extension", 0) or 0)
        planning_timeout = PLANNING_TIMEOUT + extension

        result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
            allowed_tools=planning_allowed,
            timeout=planning_timeout,
        )

        # Clear user feedback after it has been injected into the prompt
        if wf.get("user_feedback", "").strip():
            self._update_workflow({"user_feedback": ""})

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
            if "timed out" in (result.error or ""):
                # Timeout → allow user to extend instead of hard failure
                self._update_workflow(
                    {
                        "status": "planning_timeout",
                        "error_message": (
                            f"Planning timed out after {planning_timeout}s. "
                            "You can extend the timeout and retry."
                        ),
                    }
                )
                self._emit(
                    "planning_timeout",
                    {
                        "timeout": planning_timeout,
                        "tokens_used": result.total_tokens,
                        "partial_plan": (result.response_text or "")[:500],
                    },
                )
            else:
                self._update_workflow(
                    {"status": "failed", "error_message": f"Planning failed: {result.error}"}
                )
            return

        # Post plan as issue comment
        if issue_number:
            try:
                gh.add_issue_comment(
                    issue_number,
                    f"## 📋 Implementation Plan (Round {round_num})\n\n{self._clean_agent_text(plan_text)}",
                )
            except GitHubOpsError:
                pass

        # Step 2: Review plan
        review_prompt = (
            PLANNING_CONTEXT + f"你是一位资深技术评审专家。请严格审查以下实现方案，指出：\n"
            f"1. 遗漏的需求\n"
            f"2. 架构风险\n"
            f"3. 实现难度估计\n"
            f"4. 改进建议\n\n"
            f"## 方案\n{plan_text}\n\n"
            f"## 需求\n{requirements}\n\n"
            f"如果方案没有重大问题，请明确说明'方案通过审查'。\n\n"
            f"重要：直接输出审查结果，不要添加引导文字(如'我来审查...'、'让我...'等)"
            f"或结尾引导(如'下一步是否...'等)。"
        )

        review_ms = self._create_milestone(
            phase="planning",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type="plan_reviewed",
            status="in_progress",
            title=f"Plan review round {round_num}",
        )

        review_result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=review_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
            allowed_tools=planning_allowed,
            timeout=planning_timeout,
        )

        self._accumulate_tokens(review_result)

        review_text = review_result.response_text or ""

        # Detect empty review output (agent produced no meaningful content)
        if not review_text.strip():
            logger.warning(
                "Plan review round %d produced empty output (session=%s, success=%s)",
                round_num,
                review_result.session_id,
                review_result.success,
            )
            # Use a fallback message so the planning loop can still decide
            # whether to refine, rather than treating empty as "approved"
            review_text = "Review agent produced no output. Plan should be reviewed manually."

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
                    issue_number,
                    f"## 🔍 Plan Review (Round {round_num})\n\n{self._clean_agent_text(review_text)}",
                )
            except GitHubOpsError:
                pass

        # Step 3: Check if all rounds are done
        # max_plan_rounds means the max number of Plan→Review→Refine cycles.
        # After the initial Plan + Review (round 1), we need at least one
        # refinement round if the review had substantive feedback.
        # So the total rounds = initial plan + up to max_plan_rounds refinements.
        self._update_workflow({"current_round": round_num})

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        needs_refinement = review_has_feedback and round_num <= max_rounds

        if not needs_refinement:
            # Planning complete — post final plan to issue.
            # Use the latest refined plan if available, otherwise the original.
            final_plan = ""
            last_review = ""
            all_milestones = self.repo.list_milestones(self._workflow_id, phase="planning")
            for ms in reversed(all_milestones):
                if ms.get("plan_content") and not final_plan:
                    final_plan = ms["plan_content"]
                if (
                    ms.get("milestone_type") == "plan_reviewed"
                    and ms.get("review_content")
                    and not last_review
                ):
                    last_review = ms["review_content"]
                if final_plan and last_review:
                    break

            # If the review approved the plan but contained improvement
            # suggestions, run a one-time refinement step to incorporate
            # them into the final plan.
            if self._should_refine_plan(last_review) and final_plan:
                refine_prompt = (
                    PLANNING_CONTEXT + "以下实现方案已通过审查，但审查专家给出了一些改进建议。\n"
                    "请根据建议优化方案。直接输出优化后的完整方案，"
                    "不要输出思考过程或其他引导文字。\n\n"
                    f"## 已通过的方案\n{final_plan}\n\n"
                    f"## 审查建议\n{last_review}\n\n"
                    "重要约束：只输出高层设计方案和实现步骤描述，"
                    "不要输出完整的代码实现。具体代码将在后续开发阶段编写。"
                )
                planning_allowed = PLANNING_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), [])
                refine_result = self._run_agent(
                    wf=wf,
                    workflow_id=self._workflow_id,
                    cli_tool=wf.get("cli_tool", "claude-code"),
                    model=wf.get("model", ""),
                    project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                    prompt=refine_prompt,
                    workspace_type=wf.get("workspace_type", "local"),
                    remote_machine_id=wf.get("remote_machine_id"),
                    permission_mode=wf.get("permission_mode", "auto-edit"),
                    allowed_tools=planning_allowed,
                    timeout=PLANNING_TIMEOUT,
                )
                self._accumulate_tokens(refine_result)
                if refine_result.success and refine_result.response_text:
                    final_plan = refine_result.response_text
                    # Record refinement as a milestone for crash recovery.
                    self._create_milestone(
                        phase="planning",
                        milestone_type="plan_refined",
                        title="Plan Refined (Review Suggestions Incorporated)",
                        plan_content=final_plan,
                        round_number=round_num,
                    )

            # Clean up agent intro text (e.g. "我来分析代码库...")
            final_plan = self._clean_agent_text(final_plan)

            issue_number = wf.get("github_issue_number")
            if issue_number and final_plan:
                try:
                    final_comment = (
                        f"## 📋 Final Implementation Plan\n\n"
                        f"Plan review completed after {round_num} round(s).\n\n"
                        f"{final_plan}"
                    )
                    if self._should_show_review_warning(round_num, max_rounds, last_review):
                        final_comment += (
                            f"\n\n---\n\n"
                            f"## ⚠️ Note: Last review had feedback not yet addressed\n\n"
                            f"{last_review[:2000]}"
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
        """Execute development based on finalized plan.

        When ``test_retries > 0``, the dev phase was already completed and
        only the test step needs to be re-run (e.g. the test agent itself
        timed out or hit an API error on the previous attempt).
        """
        dev_round = wf.get("dev_round", 1)
        gh = self._get_gh()
        test_retries = wf.get("test_retries", 0)

        # ── Development phase (skipped on test-only retry) ──
        if test_retries > 0:
            logger.info(
                "Test-only retry %d for dev round %d, skipping development phase",
                test_retries,
                dev_round,
            )
        else:
            self._run_development_agent(wf, dev_round, gh)
            # Post development completion comment (before tests)
            self._post_dev_completion_comment(wf, dev_round, gh)

        # ── Test phase (always runs) ──
        self._run_test_phase(wf, dev_round, gh)

    def _run_development_agent(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Run the development agent, verify code changes, and return.

        On failure, updates workflow status to 'failed' and returns.
        Caller should check workflow status if needed.
        """
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

        # Capture commit SHA before agent runs to verify code changes later
        commit_before = ""
        try:
            commit_before = gh.get_current_commit()
        except Exception:
            pass

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
        dev_prompt += self._get_user_feedback_prompt(wf)

        result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=dev_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
        )

        # Clear user feedback after it has been injected into the prompt
        if wf.get("user_feedback", "").strip():
            self._update_workflow({"user_feedback": ""})

        self._accumulate_tokens(result)

        # Get diff stats and commit SHA independently so one failure
        # does not discard the other
        diff_stats = {}
        commit_sha = ""
        try:
            commit_sha = gh.get_current_commit()
        except Exception:
            pass
        try:
            diff_stats = gh.get_diff_stats("HEAD~1", "HEAD")
        except Exception:
            pass

        # Verify agent actually produced code changes (Issue #776 Bug 2)
        sha_changed = commit_before and commit_sha and commit_before != commit_sha
        has_uncommitted = False

        if not sha_changed:
            # SHA unchanged (or unavailable) — check for uncommitted changes
            has_uncommitted = False
            try:
                has_uncommitted = gh.has_uncommitted_changes()
            except Exception:
                pass

            if has_uncommitted:
                logger.info(
                    "Agent left uncommitted changes, auto-committing (success=%s)",
                    result.success,
                )
                try:
                    gh.git_add_all()
                    gh.git_commit(
                        f"auto: development changes (round {dev_round})",
                        no_verify=True,
                    )
                    commit_sha = gh.get_current_commit()
                    sha_changed = True
                    try:
                        diff_stats = gh.get_diff_stats("HEAD~1", "HEAD")
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("Auto-commit failed: %s", e)
                    has_uncommitted = False

        if not sha_changed and not has_uncommitted:
            branch_has_changes_vs_base = False
            branch_name = wf.get("branch_name", "")
            base_diff_stats = {}
            try:
                if branch_name:
                    base_diff_stats = gh.get_diff_stats("origin/main", branch_name)
                    branch_has_changes_vs_base = base_diff_stats.get("commits", 0) > 0
            except Exception:
                pass

            if branch_has_changes_vs_base:
                logger.info(
                    "No new commit this session, but branch '%s' has %d commits vs origin/main",
                    branch_name,
                    base_diff_stats.get("commits", 0),
                )
                diff_stats = base_diff_stats
                if not commit_sha:
                    try:
                        commit_sha = gh.get_current_commit()
                    except Exception:
                        pass
            else:
                logger.warning("Agent reported success but no new commits detected (SHA unchanged)")
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "session_id": result.session_id,
                        "result_summary": (
                            result.response_text[:300] if result.response_text else ""
                        ),
                        "error_message": "Agent produced no code changes (commit SHA unchanged)",
                    },
                )
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": "Development failed: agent produced no code changes",
                    }
                )
                return

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

    def _post_dev_completion_comment(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Post development completion comment with file change stats to issue.

        Posted BEFORE tests run, so the logical order on the issue is:
        Plan → Dev Completed (this) → Test Results → PR Review.
        """
        issue_number = wf.get("github_issue_number")
        if not issue_number:
            return

        # Collect diff stats (branch vs main)
        branch = wf.get("branch_name", "")
        diff_stats = {}
        try:
            if branch:
                diff_stats = gh.get_diff_stats("main", branch)
        except Exception:
            pass

        commit_sha = ""
        try:
            commit_sha = gh.get_current_commit()
        except Exception:
            pass

        msg = f"## ✅ Development Round {dev_round} Completed\n\n"
        if commit_sha:
            msg += f"- **Commit**: `{commit_sha[:8]}`\n"
        if branch:
            msg += f"- **Branch**: `{branch}`\n"
        if diff_stats:
            msg += (
                f"- **Changes**: {diff_stats.get('files', 0)} files "
                f"(+{diff_stats.get('additions', 0)}/-{diff_stats.get('deletions', 0)})\n"
            )
        msg += "\nProgressing to test phase..."

        try:
            gh.add_issue_comment(issue_number, msg)
        except Exception:
            pass

    def _run_test_phase(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Run tests, post results to issue, handle retries.

        On unrecoverable failure, updates workflow status to 'failed' and returns.
        On success, transitions workflow to pr_review phase.
        """
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
            "确保所有测试通过后再结束。\n\n"
            "## 重要：测试执行策略\n"
            "测试是必须执行的步骤，不能跳过。请按以下顺序尝试：\n"
            "1. 首先尝试 `python -m pytest` 或 `python3 -m pytest`（项目自带 pytest 依赖）\n"
            "2. 如果 pytest 不可用，尝试 `python -m unittest discover -s tests`\n"
            "3. 对于前端项目，尝试 `npm test` 或 `npx vitest run`\n"
            "4. 如果所有测试框架都不可用，至少执行以下验证：\n"
            '   - 用 `python -c "import <模块>"` 验证关键模块能正常导入\n'
            "   - 用 `python -m py_compile <文件>` 验证修改的文件没有语法错误\n"
            "   - 手动验证核心功能逻辑\n"
            "5. 如果测试确实无法运行，在回复末尾单独一行输出 `TEST_STATUS: skipped`\n"
        )

        test_result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=test_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
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

        # Detect if tests were actually skipped (agent couldn't run them)
        test_response_text = test_result.response_text or ""
        _skipped_markers = ["TEST_STATUS: skipped", "测试被跳过", "跳过测试"]
        _skipped_keywords = [
            "pytest 未安装",
            "pytest was not installed",
            "命令被阻止",
            "commands were blocked",
            "pip install failed",
            "pip install 被阻止",
            "权限批准",
            "所有测试框架都不可用",
            "no test framework available",
            "could not run tests",
            "unable to execute tests",
            "test framework not found",
        ]
        has_skip_keyword = any(kw in test_response_text for kw in _skipped_keywords)
        # Negative detection: if response contains no test-result keywords at
        # all (passed, failed, error, test, assertion, PASSED, FAILED), the
        # agent likely never ran tests.
        _test_result_keywords = [
            "passed",
            "failed",
            "PASSED",
            "FAILED",
            "assertion",
            "AssertionError",
            "error",
            "test",
        ]
        has_test_result = any(kw in test_response_text for kw in _test_result_keywords)
        tests_actually_skipped = (
            any(m in test_response_text for m in _skipped_markers)
            or (test_result.success and has_skip_keyword)
            or (test_result.success and not has_test_result)
        )

        # Post test results to issue
        issue_number = wf.get("github_issue_number")
        if issue_number:
            try:
                test_summary = self._clean_agent_text(test_response_text)[:800]
                if tests_actually_skipped:
                    status_line = "⚠️ Tests were not actually run — see details below"
                elif test_result.success:
                    status_line = "✅ All tests passed"
                else:
                    status_line = "❌ Tests failed"
                test_comment = (
                    f"## 🧪 Test Results (Dev Round {dev_round})\n\n"
                    f"{status_line}\n\n"
                    f"{test_summary}"
                )
                gh.add_issue_comment(issue_number, test_comment)
            except Exception:
                pass

        # Treat skipped tests as failure — tests must actually run.
        # Allow 1 retry in case of transient environment issues.
        if tests_actually_skipped:
            skip_retries = wf.get("skip_retries", 0) + 1
            if skip_retries <= 1:
                logger.warning(
                    "Tests were skipped (not actually run) for dev round %d, " "retry %d/1",
                    dev_round,
                    skip_retries,
                )
                self._update_workflow({"skip_retries": skip_retries})
                return  # Scheduler will re-call _run_test_phase
            logger.warning("Tests were skipped after retry for dev round %d", dev_round)
            self._update_workflow(
                {
                    "status": "failed",
                    "error_message": (
                        "Tests were not actually run — agent could not execute "
                        "any test framework. This may indicate a permission or "
                        "environment issue."
                    ),
                }
            )
            return

        # Handle test failure with retry logic
        if not test_result.success:
            # Situation A: test agent itself failed (timeout, API error, etc.)
            test_retries = wf.get("test_retries", 0) + 1
            if test_retries <= MAX_TEST_RETRIES:
                logger.warning(
                    "Test agent failed (round %d), retry %d/%d: %s",
                    dev_round,
                    test_retries,
                    MAX_TEST_RETRIES,
                    test_result.error,
                )
                self._update_workflow({"test_retries": test_retries})
                return  # Scheduler will re-call _do_development (skips dev)
            else:
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": (
                            f"Test agent failed after {MAX_TEST_RETRIES} retries: "
                            f"{test_result.error}"
                        ),
                    }
                )
                return

        # Situation B: test agent succeeded but reported unfixable failures
        test_response = test_result.response_text or ""
        _unfixable_marker = "[UNFIXABLE]"
        has_unfixable = _unfixable_marker in test_response
        if not has_unfixable:
            # Fallback: check legacy keywords for backward compatibility
            _legacy_unfixable = [
                "无法修复",
                "不可修复",
                "cannot fix",
                "unable to fix",
            ]
            has_unfixable = any(kw in test_response.lower() for kw in _legacy_unfixable)

        if has_unfixable:
            dev_retries = wf.get("dev_retries_on_test_fail", 0) + 1
            if dev_retries <= MAX_DEV_RETRIES_ON_TEST_FAIL:
                logger.warning(
                    "Tests have unfixable failures, starting dev round %d (retry %d/%d)",
                    dev_round + 1,
                    dev_retries,
                    MAX_DEV_RETRIES_ON_TEST_FAIL,
                )
                self._update_workflow(
                    {
                        "dev_round": dev_round + 1,
                        "dev_retries_on_test_fail": dev_retries,
                    }
                )
                return
            else:
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": (
                            f"Tests have unfixable failures after "
                            f"{MAX_DEV_RETRIES_ON_TEST_FAIL} dev retries"
                        ),
                    }
                )
                return

        # Tests passed — clear retry counters
        self._update_workflow({"test_retries": 0, "dev_retries_on_test_fail": 0})

        # Dev completed milestone
        self._create_milestone(
            phase="development",
            dev_round=dev_round,
            milestone_type="dev_completed",
            status="completed",
            title=f"Development round {dev_round} completed",
        )

        # Post test-passed status to issue
        if issue_number:
            try:
                branch = wf.get("branch_name", "")
                status_msg = (
                    f"## 🎯 All Checks Passed (Dev Round {dev_round})\n\n"
                    f"- **Status**: Development + tests completed successfully\n"
                    f"- **Branch**: `{branch}`\n"
                    f"- **Next**: Creating PR and running code review\n"
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

            # Check CI status after PR creation — poll until finished or timeout
            if pr_number:
                try:
                    ci_checks_post = self._poll_ci_status(gh, pr_number)
                    ci_fails_post = [c for c in ci_checks_post if c.get("bucket") == "fail"]
                    if ci_fails_post:
                        ci_summary = "\n".join(
                            f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_fails_post
                        )
                        gh.add_pr_comment(
                            pr_number,
                            "## ⚠️ CI 检查状态\n\n"
                            f"以下 CI 检查未通过：\n{ci_summary}\n\n"
                            "将在后续代码审查轮次中分析这些失败是否由本 PR 引入。",
                        )
                except Exception:
                    pass

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

        # Check CI status for the PR — poll until checks finish or timeout
        ci_checks: list = []
        ci_failures: list = []
        if pr_number:
            try:
                ci_checks = self._poll_ci_status(gh, pr_number)
                ci_failures = [c for c in ci_checks if c.get("bucket") == "fail"]
            except Exception:
                pass

        review_prompt = (
            AUTONOMOUS_CONTEXT + f"你是一位资深代码审查专家。请审查以下 PR 的代码变更。\n\n"
            f"## 需求\n{wf.get('requirements_text', '')[:500]}\n\n"
            f"## 代码变更\n{self._smart_truncate_diff(diff_text)}\n\n"
        )

        # Include previous review feedback for rounds > 1
        if round_num > 1:
            prev_review_milestones = self.repo.list_milestones(self._workflow_id, phase="pr_review")
            last_review_text = ""
            for ms in reversed(prev_review_milestones):
                if ms.get("milestone_type") == "pr_reviewed" and ms.get("review_content"):
                    last_review_text = ms["review_content"]
                    break
            if last_review_text:
                cleaned_review = self._clean_agent_text(last_review_text)
                truncated = cleaned_review[:PREV_REVIEW_MAX_LENGTH]
                truncation_notice = (
                    "\n> ⚠️ 以上审查意见已截断至 3000 字符，部分内容可能被省略。\n"
                    if len(cleaned_review) > PREV_REVIEW_MAX_LENGTH
                    else ""
                )
                review_prompt += (
                    f"## 上一轮审查意见（Round {round_num - 1}）\n\n"
                    f"{truncated}\n"
                    f"{truncation_notice}\n"
                    "**请逐条确认上一轮审查意见是否已被落实：**\n"
                    "- 已落实：说明如何修改\n"
                    "- 未落实：说明原因\n"
                    "- 不适用：说明理由\n\n"
                )

        review_prompt += (
            "请检查：\n"
            "1. 代码质量和可读性\n"
            "2. 潜在 bug 和安全问题\n"
            "3. 测试覆盖率\n"
            "4. 性能影响\n"
            "5. 与需求的对齐程度\n"
            "6. 上一轮审查意见的落实情况(如有)\n\n"
            "如果没有重大问题，请明确说明'代码审查通过'。\n\n"
            "重要：直接输出审查结果，不要添加引导文字(如'我来审查...'、'让我...'等)"
            "或结尾引导(如'下一步是否...'等)。"
        )

        # Include CI failures in review prompt if any
        if ci_failures:
            ci_summary = "\n".join(
                f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_failures
            )
            review_prompt += (
                f"\n\n## ⚠️ CI 检查失败\n\n以下 CI 检查未通过：\n{ci_summary}\n\n"
                "请在审查时分析这些 CI 失败是否由本 PR 的代码变更引入。\n"
                "如果是预先存在的问题，在审查结论中明确说明。"
            )

        review_result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=review_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
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
                    pr_number,
                    f"## 🔍 Code Review (Round {round_num})\n\n{self._clean_agent_text(review_text)}",
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
                AUTONOMOUS_CONTEXT
                + f"根据以下代码审查意见修改代码：\n\n{self._clean_agent_text(review_text)}\n\n"
                "重要要求：\n"
                "1. 修改完成后，运行项目测试确保所有测试通过\n"
                "2. 如果测试失败，分析失败原因：\n"
                "   - 如果是本 PR 引入的问题，修复后重新运行测试\n"
                "   - 如果是预先存在的问题（与本 PR 修改的文件无关），在回复末尾"
                "单独一行输出 `CI_STATUS: pre-existing`\n"
                "3. 确认测试通过后提交 git commit 并推送\n"
            )
            if ci_failures:
                ci_summary = "\n".join(
                    f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_failures
                )
                fix_prompt += (
                    f"\n\n## 当前 CI 失败的检查\n{ci_summary}\n"
                    "请分析这些 CI 失败是否由本 PR 的代码变更引入，并尝试修复。\n"
                    "如果确认是预先存在的问题，在回复末尾单独一行输出 `CI_STATUS: pre-existing`。"
                )

            fix_result = self._run_agent(
                wf=wf,
                workflow_id=self._workflow_id,
                cli_tool=wf.get("cli_tool", "claude-code"),
                model=wf.get("model", ""),
                project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                prompt=fix_prompt,
                workspace_type=wf.get("workspace_type", "local"),
                remote_machine_id=wf.get("remote_machine_id"),
                permission_mode=wf.get("permission_mode", "auto-edit"),
            )

            self._accumulate_tokens(fix_result)

            # Clear user feedback after it has been injected into the prompt
            if wf.get("user_feedback", "").strip():
                self._update_workflow({"user_feedback": ""})

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
                    # Extract fix summary from agent response (no hard truncation —
                    # _clean_agent_text already strips noise; only cap at 5000 chars
                    # to avoid excessively long comments, breaking at paragraph boundary).
                    fix_summary = self._clean_agent_text(fix_result.response_text or "")
                    if len(fix_summary) > 5000:
                        # Truncate at last paragraph break within limit
                        truncated = fix_summary[:5000]
                        last_break = max(truncated.rfind("\n\n"), truncated.rfind("\n##"), 0)
                        if last_break > 1000:
                            fix_summary = truncated[:last_break].rstrip() + "\n\n..."
                        else:
                            fix_summary = truncated.rstrip() + "..."
                    comment = (
                        f"## ✅ Addressed Review Feedback (Round {round_num})\n\n"
                        f"### Changes Made\n{fix_summary}\n\n"
                    )
                    if commit_sha:
                        comment += f"- Commit: `{commit_sha[:8]}`\n"
                    # Note pre-existing CI failures if fix agent identified them.
                    if self._is_pre_existing_ci_failure(fix_result.response_text or ""):
                        comment += (
                            "\n> ⚠️ 部分 CI 检查失败，" "但经分析为预先存在的问题，非本 PR 引入。\n"
                        )
                    gh.add_pr_comment(pr_number, comment)
                except GitHubOpsError:
                    pass

    # ── Phase: Report ───────────────────────────────────────────────

    def _do_report(self, wf: dict):
        """Generate progress report and update issue."""
        dev_round = wf.get("dev_round", 1)
        gh = self._get_gh()
        issue_number = wf.get("github_issue_number")
        pr_number = wf.get("github_pr_number")
        all_milestones = self.repo.list_milestones(self._workflow_id, dev_round=dev_round)

        # 1. Plan summary (from finalized plan)
        plan_summary = ""
        for ms in reversed(all_milestones):
            if ms.get("plan_content") and ms.get("phase") == "planning":
                plan_summary = self._clean_agent_text(ms["plan_content"])[:300]
                break

        # 2. Diff stats
        diff_stats = {}
        try:
            branch = wf.get("branch_name", "")
            diff_stats = gh.get_diff_stats("main", branch)
        except Exception:
            pass

        # 3. Test result summary (from test milestone)
        test_summary = ""
        for ms in all_milestones:
            if ms.get("milestone_type") == "tests_run" and ms.get("result_summary"):
                test_summary = self._clean_agent_text(ms["result_summary"])[:200]
                break

        # 4. Code review rounds
        review_rounds = sum(
            1
            for ms in all_milestones
            if ms.get("milestone_type") == "pr_reviewed" and ms.get("phase") == "pr_review"
        )
        review_passed = any(
            "代码审查通过" in (ms.get("review_content") or "")
            for ms in all_milestones
            if ms.get("milestone_type") == "pr_reviewed"
        )

        # Build report
        report = f"## 📊 Dev Round {dev_round} Summary\n\n"

        if plan_summary:
            report += f"### 📋 Plan\n{plan_summary}\n\n"

        # Changes section
        report += "### 📝 Changes\n"
        branch = wf.get("branch_name", "")
        if diff_stats:
            report += (
                f"- Files: {diff_stats.get('files', 0)} changed "
                f"(+{diff_stats.get('additions', 0)}/-{diff_stats.get('deletions', 0)})\n"
            )
        if branch:
            report += f"- Branch: `{branch}`\n"
        report += "\n"

        if test_summary:
            report += f"### 🧪 Tests\n{test_summary}\n\n"

        if review_rounds > 0:
            report += "### 🔍 Code Review\n"
            report += f"- Rounds: {review_rounds}\n"
            report += f"- Final status: {'✅ Passed' if review_passed else '⚠️ Issues found'}\n\n"

        if pr_number:
            report += f"### 🔗 PR\n- PR #{pr_number}\n\n"

        report += (
            "### 📈 Resources\n"
            f"- Tokens: {wf.get('total_tokens', 0):,}\n"
            f"- API Requests: {wf.get('total_requests', 0)}\n"
        )

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
        """Poll for new requirements or completion signal.

        If user_feedback is stored on the workflow (from cancel-with-feedback),
        resume immediately from the cancelled milestone's phase.
        """
        # Check for stored user feedback (from cancel-with-feedback)
        user_feedback = wf.get("user_feedback", "")
        if user_feedback and user_feedback.strip():
            # User provided feedback via cancel — resume from the cancelled phase
            # Find the most recent non-cancelled, non-wait milestone to determine phase
            cancelled_phase = "development"  # default fallback
            milestones = self.repo.list_milestones(self._workflow_id)
            for ms in reversed(milestones):
                status = ms.get("status", "")
                mtype = ms.get("milestone_type", "")
                if status == "completed" and mtype not in (
                    "wait_started",
                    "requirement_received",
                    "branch_created",
                    "repo_setup",
                    "issue_created",
                ):
                    cancelled_phase = ms.get("phase", "development")
                    break

            new_dev_round = wf.get("dev_round", 1) + 1
            self._update_workflow(
                {
                    "current_phase": cancelled_phase,
                    "status": PHASE_STATUS_MAP.get(cancelled_phase, "developing"),
                    "dev_round": new_dev_round,
                    "current_round": 0,
                }
            )
            self._emit(
                "phase_change",
                {"phase": cancelled_phase, "dev_round": new_dev_round, "resumed": True},
            )
            return

        # Original behavior: poll GitHub issue comments
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
            for pattern in _COMPLETION_PATTERNS:
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
        project_path = wf.get("project_path", "")
        try:
            if worktree_path:
                # Must use main repo's gh to remove worktree
                # (can't remove a worktree from within itself)
                main_gh = GitHubOps(project_path)
                main_gh.remove_worktree(worktree_path)
                self._update_workflow({"worktree_path": ""})
                # Reinitialize gh to point at main repo for branch deletion
                self._gh = GitHubOps(project_path)
                gh = self._gh
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
            result = self._run_agent(
                wf=wf,
                workflow_id=self._workflow_id,
                cli_tool=wf.get("cli_tool", "claude-code"),
                model=wf.get("model", ""),
                project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                prompt=conflict_prompt,
                workspace_type=wf.get("workspace_type", "local"),
                remote_machine_id=wf.get("remote_machine_id"),
                permission_mode=wf.get("permission_mode", "auto-edit"),
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
