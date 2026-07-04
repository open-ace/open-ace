# mypy: disable-error-code="assignment,arg-type,union-attr,return-value,no-any-return"
"""
Open ACE - Autonomous Orchestrator

State machine that drives a single autonomous development workflow
through its phases: preparation -> planning -> development ->
pr_review -> report -> wait -> (loop or merge).
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.modules.workspace.autonomous.agent_runner import (
    DEFAULT_TASK_TIMEOUT,
    AutonomousAgentRunner,
)
from app.modules.workspace.autonomous.artifact_text import (
    clean_agent_text,
    pick_best_artifact_text,
    sanitize_artifact_text,
)
from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter
from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.progress_report_i18n import (
    build_progress_payload,
    render_progress_report,
)
from app.repositories.autonomous_repo import DEFAULT_CONTENT_LANGUAGE, AutonomousWorkflowRepository
from app.repositories.database import Database
from app.repositories.user_repo import UserRepository

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

# Substrings that identify a comment as authored by the automation itself.
# Shared by preparation (issue ingestion) and wait (new-requirement polling)
# so status reports / PR links the bot posts back to the issue are never
# re-ingested as requirements (#1244).
BOT_AUTHOR_KEYWORDS = ["open-ace-bot", "autonomous", "bot"]


def _is_bot_comment(comment: dict) -> bool:
    """Return True if a comment looks like it was authored by the automation."""
    author = comment.get("author", {}) or {}
    login = (author.get("login") if isinstance(author, dict) else author) or ""
    return any(kw in login.lower() for kw in BOT_AUTHOR_KEYWORDS)


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
    # zcode uses its own built-in tool set (Read/Write/Edit/Bash/WebFetch/...),
    # exposed via the ZCode Protocol; no MCP-subtool allowlist is needed.
    "zcode": [],
}

# Development-phase tools: planning read-only set + Write/Edit/Bash so the agent
# can implement, run tests, and commit. Bash is allowed wholesale (test / git /
# build commands vary by language and can't be enumerated). This bounds where
# commits land (worktree + feature branch); bash itself is NOT sandboxed —
# cd /, rm -rf, sudo, network egress are all reachable from the worktree cwd,
# same trust model as any dev agent. plan phases stay read-only via
# PLANNING_ALLOWED_TOOLS above. See #996.
AUTONOMOUS_DEV_ALLOWED_TOOLS: dict[str, list[str]] = {
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
        "Write",
        "Edit",
        "Bash",
    ],
    "qwen-code-cli": [
        "read_file",
        "list_files",
        "search_files",
        "code_search",
        "web_search",
        "web_fetch",
        "write_file",
        "edit_file",
        "execute_command",
    ],
    "codex": [],
    "openclaw": [],
    "zcode": [],
}

# Maximum time (seconds) for a single planning agent call.
# ZCode+GLM planning involves multiple model round-trips and subagent spawns
# that can take 10-15 min for complex issues. 600s was too tight — planning
# succeeded only ~30% of the time. 1800s gives the agent enough headroom
# while still capping runaway agents.
PLANNING_TIMEOUT = 1800


def _zcode_planning_mode(wf: dict) -> str:
    """Return the ZCode --mode for planning-phase calls.

    ZCode planning must stay read-only (plan mode, #761). For all other CLI
    tools, pass through the workflow's permission_mode unchanged. For ZCode,
    force "plan" so the agent can't write files or run commands during
    planning — allowed_tools is [] for both planning and dev (zcode uses its
    own built-in toolset), so the mode is the only reliable read-only signal.
    """
    if wf.get("cli_tool") in ("zcode", "zcode-code"):
        return "plan"
    return wf.get("permission_mode", "auto-edit")


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

# Transient network error auto-retry (layer 2). When advance() catches a
# GitHubOpsError that looks like a network issue (not a code/conflict error),
# the workflow stays in its current active status and the scheduler retries
# on the next cycle (~10s). After this many consecutive transient failures,
# the workflow is marked failed for manual intervention.
TRANSIENT_RETRY_MAX = 6

# Keywords identifying transient network errors at the orchestrator level
# (the error_message stored by advance's except block). Mirrors the
# _TRANSIENT_ERROR_KEYWORDS in github_ops.py but checks the wrapped message.
_TRANSIENT_ORCHESTRATOR_KEYWORDS = [
    "libressl",
    "openssl",
    "ssl",
    "tls",
    "connection reset",
    "connection refused",
    "connection timed out",
    "timed out",
    "could not resolve host",
    "network is unreachable",
    "unable to access",
    "rpc failed",
    "early eof",
]

# GitHub rejects comment bodies longer than 65536 chars. Agent output (plan /
# review / fix / test) can exceed that, so very long comments are capped with
# a notice pointing to the timeline full-text view (#988).
GITHUB_COMMENT_MAX_CHARS = 65000

# Each phase agent appends a one-line `TL;DR: ...` summary to its output for the
# timeline milestone card (#993). build_language_instruction() is appended to
# every agent prompt; _extract_tldr pulls the line back out for the tldr column.
#
# AI-authored content (plan / review / tldr / PR-review summaries) is generated
# in the workflow's ``content_language`` — the persisted source of truth that
# does NOT switch per viewer. The language-neutral ``TL;DR:`` marker is kept in
# every language so _extract_tldr (regex on ``TL;DR:``) works regardless of
# content_language.
_LANGUAGE_DIRECTIVES = {
    "en": "\n\nWrite all content in English.",
    "zh": "\n\n请用简体中文输出所有内容。",
    "ja": "\n\nすべての内容を日本語で出力してください。",
    "ko": "\n\n모든 내용을 한국어로 출력해 주세요.",
}
_TLDR_FORMAT_INSTRUCTIONS = {
    "en": (
        "\n\n## Output format\n"
        "On the last line of your output, give a one-sentence summary of this "
        "output in this format (shown on the timeline card; concise, plain "
        "text, no markdown):\n"
        "TL;DR: <summary of no more than 80 characters>\n"
    ),
    "zh": (
        "\n\n## 输出格式要求\n"
        "请在输出的最后单独一行，用以下格式给出本次输出的一句话总结"
        "（将显示在 timeline 卡片上，务必简洁、纯文本、不要 markdown）：\n"
        "TL;DR: <不超过 80 字的总结>\n"
    ),
    "ja": (
        "\n\n## 出力形式\n"
        "出力の最後の行に、以下の書式で今回の出力の一文要約を記述してください"
        "（タイムラインカードに表示されます。簡潔に、プレーンテキストで、マークダウンなし）：\n"
        "TL;DR: <80文字以内の要約>\n"
    ),
    "ko": (
        "\n\n## 출력 형식\n"
        "출력의 마지막 줄에 다음 형식으로 이번 출력의 한 문장 요약을 작성해 주세요"
        "（타임라인 카드에 표시됩니다. 간결하게, 일반 텍스트로, 마크다운 없이):\n"
        "TL;DR: <80자 이내의 요약>\n"
    ),
}

# Backward-compatible alias: the Chinese TL;DR format instruction. Kept so
# existing imports/tests continue to work; new code uses build_language_instruction.
TLDR_INSTRUCTION = _TLDR_FORMAT_INSTRUCTIONS["zh"]


def build_language_instruction(content_language: Optional[str]) -> str:
    """Build the per-content_language directive + TL;DR format instruction.

    Returned string is appended to every agent prompt so AI-authored content is
    generated in the workflow's ``content_language``. Unknown/missing languages
    fall back to English. The language-neutral ``TL;DR:`` marker is always
    present so ``_extract_tldr`` works across all languages.
    """
    lang = content_language if content_language in _LANGUAGE_DIRECTIVES else "en"
    return _LANGUAGE_DIRECTIVES[lang] + _TLDR_FORMAT_INSTRUCTIONS[lang]


_TLDR_RE = re.compile(r"TL;DR:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# Approval marker the PR reviewer is asked to state when there are no major
# issues, per content_language. Used in BOTH the review prompt and approval
# detection — the agent writes its review in content_language, so a zh-only
# marker would miss en/ja/ko approvals. The structured verdict written to
# metadata.review_verdict removes progress_reported's dependency on this
# substring entirely. (Plan-review approval "方案通过审查" is still zh-locked
# and out of scope for the progress_reported i18n work.)
_REVIEW_APPROVAL_PHRASES = {
    "en": "Code review passed",
    "zh": "代码审查通过",
    "ja": "コードレビュー合格",
    "ko": "코드 리뷰 통과",
}


def _review_approval_phrase(content_language: Optional[str]) -> str:
    """Approval marker for PR review in the given content language."""
    return _REVIEW_APPROVAL_PHRASES.get(
        content_language, _REVIEW_APPROVAL_PHRASES[DEFAULT_CONTENT_LANGUAGE]
    )


def _parse_metadata(raw) -> dict:
    """Parse a milestone metadata JSON string/dict into a dict (empty on failure)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _merge_milestone_metadata(milestone, updates: dict) -> str:
    """Merge ``updates`` into a milestone's metadata, returning the JSON string.

    ``milestone`` may be a milestone dict (read from the repo) or a raw metadata
    JSON string. Existing keys are preserved; ``updates`` keys overwrite.
    """
    raw = milestone.get("metadata") if isinstance(milestone, dict) else milestone
    merged = _parse_metadata(raw)
    merged.update(updates)
    return json.dumps(merged, ensure_ascii=False)


# Localized "comment truncated" notice appended when a GitHub comment body
# exceeds the length cap. Rendered in the workflow's content_language.
_GITHUB_TRUNCATION_NOTICES = {
    "en": (
        "\n\n---\n\n"
        "> ⚠️ Content exceeded the GitHub comment length limit and was truncated. "
        "See the workflow timeline milestone card for the full text (plan/review/report).\n"
    ),
    "zh": (
        "\n\n---\n\n"
        "> ⚠️ 内容超出 GitHub 评论长度上限，已截断显示。"
        "完整内容请查看 workflow timeline 的里程碑卡片（方案/评审/报告全文）。\n"
    ),
    "ja": (
        "\n\n---\n\n"
        "> ⚠️ GitHub コメントの文字数上限を超えたため切り詰めました。"
        "全文はワークフロー タイムラインのマイルストーンカード（計画/レビュー/レポート）で確認してください。\n"
    ),
    "ko": (
        "\n\n---\n\n"
        "> ⚠️ GitHub 댓글 길이 제한을 초과하여 잘렸습니다. "
        "전체 내용은 워크플로 타임라인의 마일스톤 카드(계획/리뷰/보고서)에서 확인하세요.\n"
    ),
}


def _github_truncation_notice(content_language: Optional[str]) -> str:
    return _GITHUB_TRUNCATION_NOTICES.get(
        content_language, _GITHUB_TRUNCATION_NOTICES[DEFAULT_CONTENT_LANGUAGE]
    )


REVIEW_SESSION_MILESTONE_TYPES = {"plan_reviewed", "pr_reviewed"}

# Session lines that span multiple milestones via --resume. Each maps to a
# workflow column holding the real CLI session id once the line is established.
#   main:   plan_created → plan_refined → plan_finalized → dev_started →
#           pr_updated → pr_review_summary
#   review: plan_reviewed → pr_reviewed
#   test:   tests_run (reused across dev rounds)
SESSION_LINE_FIELDS = {
    "main": "main_session_id",
    "review": "review_session_id",
    "test": "test_session_id",
}

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

# Transient API error retry configuration (429 rate limit, 5xx overload, etc.).
# These typically resolve within 30 minutes.
API_RETRY_TOTAL_TIMEOUT = 1800  # max total retry duration (seconds)
API_RETRY_INITIAL_DELAY = 30  # first retry delay (seconds)
API_RETRY_MAX_DELAY = 300  # max single retry delay (seconds)

# Transient API error signatures in an agent response/error body. Used to decide
# retry (with backoff) and, after retries are exhausted, to flag the result as a
# failure so the error text isn't stored as plan/review content (#1001).
# Patterns are kept specific (status codes + unambiguous phrases) to avoid
# false-positive retries on legitimate plans that merely discuss error handling.
_TRANSIENT_API_ERROR_RE = re.compile(
    # "API Error: 429" / "API Error: 5xx". Only 429 among 4xx is transient;
    # 400/401/403/404/422 are permanent client errors and must NOT trigger retry.
    r"api\s*error:?\s*(?:429|5\d{2})"
    r"|(?:429|quota\s+exceeded|rate[\s-]?limit|too\s+many\s+requests)"
    r"|overloaded"  # "The service may be temporarily overloaded"
    r"|bad\s+gateway|service\s+unavailable|gateway\s+timeout|internal\s+server\s+error",
    re.IGNORECASE,
)

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
            on_pid_registered=self._on_pid_registered,
            on_pid_cleared=self._on_pid_cleared,
        )
        self._gh: Optional[GitHubOps] = None

    @property
    def workflow(self) -> Optional[dict]:
        return self.repo.get_workflow(self._workflow_id)

    def _get_gh(self) -> GitHubOps:
        """Lazily initialize GitHubOps.

        Prefers ``worktree_path`` (the agent's working repo once preparation
        has run) but falls back to ``project_path`` when the worktree dir does
        not yet exist. This matters on rerun: a previous failure may have
        removed the worktree dir while the stale ``worktree_path`` is still in
        the DB, so binding GitHubOps to it makes every ``git -C <worktree>``
        fail with ENOENT (Issue #1395 rerun regression). Preparation creates
        the worktree; until it does, the main repo is the only valid repo_path.
        """
        wf = self.workflow
        if not self._gh and wf:
            worktree_path = wf.get("worktree_path", "")
            project_path = wf.get("project_path", "")
            # Resolve system_account for multi-user permission isolation (Issue #1395)
            system_account = None
            user_id = wf.get("user_id")
            if user_id:
                user_repo = UserRepository()
                user = user_repo.get_user_by_id(user_id)
                if user:
                    system_account = user.get("system_account")
            # Prefer the worktree once it exists; otherwise the main repo.
            # path_exists_as_user cross-user-checks under a 700 home (the
            # service user can't stat it directly), so it's correct both for
            # same-user dev runs and the multi-user deployment.
            chosen = project_path
            if worktree_path:
                probe = GitHubOps(project_path, system_account=system_account)
                if probe.path_exists_as_user(worktree_path, dir_only=True):
                    chosen = worktree_path
            self._gh = GitHubOps(chosen, system_account=system_account)
        return self._gh

    def _ensure_worktree(self, wf: dict) -> str:
        """Guarantee the worktree dir + branch exist before a phase runs.

        Retrying/resuming a ``worktree``-strategy workflow after its dir was
        cleaned up (e.g. a previous failure removed it, or the machine
        rebooted) used to silently launch the agent against an empty path and
        fail with a JSONL-detection error (#814). Every downstream phase now
        calls this at entry so the environment self-heals:

        - normalizes a stale ``worktree_path`` still containing ``..`` so the
          DB and the on-disk dir agree;
        - recreates the worktree dir (reusing the branch if it still exists)
          when it is gone.

        Returns the canonical worktree path. For non-worktree strategies, or
        when ``worktree_path`` is intentionally empty (merge cleanup / conflict
        resolution clears it), this is a no-op returning ``project_path``.
        """
        strategy = wf.get("branch_strategy", "new-branch")
        project_path = wf.get("project_path", "")
        worktree_path = wf.get("worktree_path", "")

        # An empty worktree_path is NOT the "dir gone, recreate it" case — it
        # is set deliberately by merge cleanup (_resolve_merge_conflicts /
        # _do_merge) when the worktree is removed to free the main repo for
        # conflict resolution. Treating it as missing would fall back to
        # project_path as canonical and try `git worktree add <main_repo>`,
        # which fails and turns a retried merge into a hard failure. Only a
        # non-empty path whose dir is gone represents external loss (#814).
        if strategy != "worktree" or not project_path or not worktree_path:
            return worktree_path or project_path

        canonical = os.path.realpath(worktree_path)
        # Resolve system_account up front so the validity check below can use
        # it: os.path.isfile() stats as the service user and raises
        # PermissionError under a user-private parent (700 home, Issue #1395).
        # Get system_account for multi-user permission isolation (Issue #1395)
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")
        main_gh = GitHubOps(project_path, system_account=system_account)
        # Valid worktree: a .git FILE inside means git set it up (a plain
        # clone has a .git directory instead). If the stored path was
        # unnormalized (legacy ".."), persist the canonical form so JSONL
        # session detection matches Claude's encoding. file_only keeps the
        # original os.path.isfile() semantics (Issue #1395 review).
        if worktree_path and main_gh.path_exists_as_user(
            os.path.join(canonical, ".git"), file_only=True
        ):
            if canonical != worktree_path:
                self._update_workflow({"worktree_path": canonical})
            return canonical

        # Worktree missing — recreate from the main repo.
        branch_name = wf.get("branch_name") or f"auto-dev/{self._workflow_id[:8]}"
        try:
            main_gh._run_git(["fetch", "origin", "main"])
            # Does the branch still exist locally or on origin?
            branch_check = main_gh._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                check=False,
            )
            remote_check = main_gh._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch_name}"],
                check=False,
            )
            if branch_check.returncode == 0 or remote_check.returncode == 0:
                # Branch survives (local or remote) — attach a worktree to it
                # without recreating the branch. For a remote-only branch, git
                # auto-creates a local tracking branch in this step.
                main_gh._run_git(["worktree", "add", canonical, branch_name])
            else:
                # Neither worktree nor branch — start fresh from origin/main.
                main_gh._run_git(["worktree", "add", "-b", branch_name, canonical, "origin/main"])
        except GitHubOpsError as e:
            logger.error("Failed to recreate worktree at %s: %s", canonical, e)
            raise

        self._update_workflow({"worktree_path": canonical, "branch_name": branch_name})
        self._create_milestone(
            phase=wf.get("current_phase", "preparation"),
            milestone_type="worktree_restored",
            status="completed",
            title=f"Worktree restored at {os.path.basename(canonical)}",
        )
        logger.info("Restored worktree at %s on branch %s", canonical, branch_name)
        # Reset cached gh so it picks up the restored worktree path.
        self._gh = None
        return canonical

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
        return clean_agent_text(text)

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
    def _must_run_full_review_rounds(wf: dict) -> bool:
        """Whether planning/PR review must consume the configured round cap."""
        value = wf.get("require_full_review_rounds", False)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

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

    @staticmethod
    def _extract_tldr(response: str) -> str:
        """Extract the ``TL;DR: ...`` one-liner the agent was asked to append.

        Returns "" when absent so callers can fall back to ``result_summary``.
        """
        if not response:
            return ""
        match = _TLDR_RE.search(response)
        return match.group(1).strip()[:200] if match else ""

    @staticmethod
    def _artifact_visible_text(result: Optional[AgentTaskResult]) -> str:
        """Return all user-visible assistant turns for a task result."""
        if not result:
            return ""
        return (getattr(result, "visible_response_text", "") or result.response_text or "").strip()

    @classmethod
    def _sanitize_artifact_text(cls, text: str) -> str:
        return sanitize_artifact_text(text)

    @classmethod
    def _artifact_text(cls, result: Optional[AgentTaskResult]) -> str:
        """Return the milestone/comment artifact text for a task result."""
        if not result:
            return ""
        return pick_best_artifact_text(
            (result.response_text or "").strip(),
            cls._artifact_visible_text(result),
        )

    @classmethod
    def _artifact_tldr(cls, result: Optional[AgentTaskResult]) -> str:
        """Prefer structured/extracted TL;DR over raw string slicing."""
        if not result:
            return ""
        structured = getattr(result, "structured_tags", {}) or {}
        if structured.get("tldr"):
            return structured["tldr"][:200]
        return cls._extract_tldr(cls._artifact_visible_text(result) or result.response_text or "")

    @staticmethod
    def _artifact_status_tag(result: Optional[AgentTaskResult], key: str) -> str:
        """Read structured status tags extracted by the runner."""
        if not result:
            return ""
        structured = getattr(result, "structured_tags", {}) or {}
        value = structured.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _build_dev_result_summary(
        artifact_text: str, diff_stats: dict, commit_sha: str, success: bool
    ) -> str:
        """Prefer agent summary, but synthesize a concise fallback when it is noisy/empty."""
        cleaned = artifact_text.strip()
        if cleaned:
            return cleaned[:300]

        status = "Development completed" if success else "Development failed"
        parts = [status]
        if commit_sha:
            parts.append(f"commit {commit_sha[:8]}")
        if diff_stats:
            parts.append(
                f"{diff_stats.get('files', 0)} files changed "
                f"(+{diff_stats.get('additions', 0)}/-{diff_stats.get('deletions', 0)})"
            )
        return "; ".join(parts)

    def _post_github_comment(
        self,
        gh: GitHubOps,
        number: int,
        body: str,
        *,
        is_pr: bool = False,
        context: str = "",
        content_language: Optional[str] = None,
    ) -> None:
        """Post a comment to a GitHub issue or PR.

        Guards two failure modes the raw ``gh.add_*_comment`` calls used to
        swallow silently:

        - **Length**: GitHub rejects comment bodies longer than 65536 chars.
          Verbose agent output (plan / review / fix / test) can exceed that, so
          bodies over ``GITHUB_COMMENT_MAX_CHARS`` are capped with a notice
          pointing readers to the timeline full-text view (#988) for the rest.
        - **Errors**: a failed post is logged at WARNING (with ``context``) and
          swallowed, so a missing comment is diagnosable without aborting the
          workflow phase. The body survives in the DB / timeline regardless.
        """
        if len(body) > GITHUB_COMMENT_MAX_CHARS:
            notice = _github_truncation_notice(
                content_language or (self.workflow or {}).get("content_language")
            )
            body = body[: GITHUB_COMMENT_MAX_CHARS - len(notice)] + notice
        try:
            if is_pr:
                gh.add_pr_comment(number, body)
            else:
                gh.add_issue_comment(number, body)
        except Exception as e:  # log + continue; never abort the phase over a comment
            logger.warning(
                "Failed to post GitHub %s comment #%s%s: %s",
                "PR" if is_pr else "issue",
                number,
                f" ({context})" if context else "",
                e,
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

    def _accumulate_tokens(self, _result: AgentTaskResult):
        """Refresh workflow totals from the sessions linked to milestones."""
        self.repo.refresh_workflow_usage_from_sessions(self._workflow_id)

    def _on_pid_registered(self, session_id: str, pid: int):
        """Persist agent subprocess PID to database for reliable cancel/pause."""
        try:
            self.repo.update_workflow(
                self._workflow_id,
                {
                    "agent_pid": pid,
                    "agent_session_id": session_id,
                },
            )
            logger.info(
                "Registered agent PID %d for workflow %s (session %s)",
                pid,
                self._workflow_id[:8],
                session_id[:8],
            )
        except Exception as e:
            logger.warning("Failed to persist agent PID: %s", e)

    def _on_pid_cleared(self, session_id: str):
        """Clear agent subprocess PID from database after process exits."""
        try:
            with self._session_lock:
                if self._current_session_id == session_id:
                    self.repo.update_workflow(
                        self._workflow_id,
                        {
                            "agent_pid": None,
                            "agent_session_id": "",
                        },
                    )
                    logger.debug(
                        "Cleared agent PID for workflow %s",
                        self._workflow_id[:8],
                    )
        except Exception as e:
            logger.warning("Failed to clear agent PID: %s", e)

    def _on_agent_activity(self, session_id: str, activity: dict):
        """Forward agent activity to the SSE event stream and refresh usage."""
        self.emitter.emit(
            self._workflow_id,
            "agent_activity",
            {
                "session_id": session_id,
                **activity,
            },
        )
        activity_type = activity.get("type")
        if activity_type == "session_resolved":
            try:
                self._link_session_to_current_milestone(session_id)
                self.repo.refresh_workflow_usage_from_sessions(self._workflow_id)
            except Exception:
                logger.warning("Failed to resolve workflow session in real-time", exc_info=True)
        elif activity_type == "usage":
            try:
                # Write the running cumulative usage to the in-progress milestone
                # so the workflow total climbs during a long task instead of
                # freezing until the call returns. phase_* is overwritten per
                # event (session totals are cumulative within the call) and
                # finalized by _write_phase_usage at _run_agent return.
                self._write_realtime_phase_usage(activity)
                self.repo.refresh_workflow_usage_from_sessions(self._workflow_id)
            except Exception:
                logger.warning("Failed to refresh workflow tokens in real-time", exc_info=True)

    def _resolve_session_line(self, wf: dict, session_line: str):
        """Resolve (tracking_session_id, resume_session_id, resume) for a line.

        main/review/test: the workflow stores a stable tracking session id for
        the line. For Claude, the actual CLI/sidebar resume id is read from the
        tracking session row's authoritative ``cli_session_id`` column (with a
        context-JSON fallback for rows written before the column existed). For
        other tools, the tracking id itself is the resume target.

        Strict 3-session topology: if a Claude line's mapping is missing (partial
        write, old data, manual cleanup), we do NOT disguise the tracking id as a
        resume id — that would resume a non-existent Claude session and pin the
        bad id with no self-heal. Instead we return resume=False so ``_run_local``
        re-probes the real CLI id and rebinds it to this same line (still exactly
        3 sessions, never a 4th).

        Reads from the wf dict first, then falls back to a fresh DB query.
        The DB fallback is critical: _run_agent updates the session line in the
        DB, but the in-memory wf dict may be stale if it was read before the
        update (e.g. _do_planning reads wf once at entry, then planning's
        _run_agent writes to DB; finalize needs the updated value).
        """
        field = SESSION_LINE_FIELDS.get(session_line)
        if not field:
            return str(uuid.uuid4()), None, False
        existing = ((wf or {}).get(field) or "").strip()
        # Fallback to DB if the in-memory wf dict doesn't have the session id.
        # This handles the case where a prior _run_agent in the same phase
        # updated the DB but the in-memory wf dict wasn't refreshed.
        if not existing:
            fresh = self.workflow or {}
            existing = (fresh.get(field) or "").strip()
        if existing:
            resume_target = existing
            if (wf or {}).get("cli_tool") == "claude-code" and getattr(
                self._runner, "session_manager", None
            ) is not None:
                try:
                    session_row = self._runner.session_manager.get_session(existing) or {}
                    # get_session returns an AgentSession (column on the object)
                    # or {} when absent; tests/legacy rows may supply a dict with
                    # context.cli_session_id. Authoritative column first.
                    cli_session_id = ""
                    if session_row:
                        cli_session_id = (getattr(session_row, "cli_session_id", "") or "").strip()
                        if not cli_session_id:
                            ctx = getattr(session_row, "context", None)
                            if not isinstance(ctx, dict):
                                ctx = (
                                    session_row.get("context", {})
                                    if isinstance(session_row, dict)
                                    else {}
                                )
                            cli_session_id = (ctx.get("cli_session_id") or "").strip()
                    if cli_session_id:
                        resume_target = cli_session_id
                    else:
                        # Mapping lost: keep this SAME session line stable and
                        # re-probe/rebind the real CLI id onto it on the next
                        # run, rather than creating a new wrapper line or faking
                        # a resume with the tracking id.
                        logger.warning(
                            "Claude session line %s has no cli_session_id mapping "
                            "(tracking=%s); starting fresh on the same line",
                            session_line,
                            existing[:8],
                        )
                        return existing, None, False
                except Exception:
                    logger.warning("Failed to resolve Claude resume target", exc_info=True)
                    return existing, None, False
            return existing, resume_target, True
        return str(uuid.uuid4()), None, False

    @staticmethod
    def _review_is_approved(review_text: str, approval_text: str) -> bool:
        """Whether the review text explicitly approves the artifact."""
        return bool(review_text and approval_text in review_text)

    @staticmethod
    def _derive_review_passed(review_milestones: list, content_language) -> bool:
        """Whether any PR review round approved the PR.

        Prefers the structured ``metadata.review_verdict`` written at review time
        (language-independent) so progress_reported no longer depends on a
        Chinese substring. Falls back to scanning ``review_content`` for the
        language-aware approval phrase (and the legacy zh marker) when none of
        the milestones carry a structured verdict — i.e. for workflows whose
        reviews predate this field.
        """
        phrase = _review_approval_phrase(content_language)
        have_structured = False
        for ms in review_milestones:
            verdict = _parse_metadata(ms.get("metadata")).get("review_verdict") or {}
            if isinstance(verdict, dict) and "passed" in verdict:
                have_structured = True
                if verdict.get("passed"):
                    return True
        if have_structured:
            return False
        for ms in review_milestones:
            text = ms.get("review_content") or ""
            if phrase in text or "代码审查通过" in text:
                return True
        return False

    def _link_session_to_current_milestone(self, session_id: str):
        """Write session_id to the latest in_progress milestone immediately."""
        try:
            milestones = self.repo.list_milestones(self._workflow_id, status="in_progress")
            if milestones:
                ms = milestones[-1]  # most recent
                field_name = (
                    "review_session_id"
                    if ms.get("milestone_type") in REVIEW_SESSION_MILESTONE_TYPES
                    else "session_id"
                )
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        field_name: session_id,
                    },
                )
        except Exception:
            logger.warning("Failed to link session to milestone", exc_info=True)

    @staticmethod
    def _is_transient_api_error(response: str) -> bool:
        """Detect transient API errors (429 rate limit, 5xx/overload) in a
        response or error body. Used to trigger retry with backoff and, after
        retries are exhausted, to flag the result as a failure (#1001).
        """
        if not response:
            return False
        return bool(_TRANSIENT_API_ERROR_RE.search(response))

    def _run_agent(
        self, wf: dict = None, *, session_line: str = "fresh", milestone_id: str = "", **kwargs
    ) -> AgentTaskResult:
        """Run an agent task with session-line tracking and transient-API-error retry.

        Args:
            wf: Optional pre-fetched workflow dict to avoid extra DB queries.
                If not provided, falls back to self.workflow (DB query).
            session_line: Which session line this call belongs to — "main",
                "review", "test" (resumed across milestones via --resume), or
                "fresh" (a brand-new one-off session).
            milestone_id: Milestone to attribute this call's phase usage to.
        """
        workflow_data = wf or self.workflow
        # Resolve the session line: resume an established session or start new.
        session_id, resume_session_id, resume = self._resolve_session_line(
            workflow_data, session_line
        )
        kwargs["session_id"] = session_id
        kwargs["resume"] = resume
        kwargs["resume_session_id"] = resume_session_id
        if milestone_id:
            kwargs["milestone_id"] = milestone_id
        tracking_session_id = session_id
        if "user_id" not in kwargs and workflow_data:
            kwargs["user_id"] = workflow_data.get("user_id")

        # Get system_account for multi-user permission isolation (Issue #1395)
        # When set, CLI tools will be run via `sudo -u <system_account>`
        if "system_account" not in kwargs and workflow_data:
            user_id = workflow_data.get("user_id")
            if user_id:
                user_repo = UserRepository()
                user = user_repo.get_user_by_id(user_id)
                if user:
                    kwargs["system_account"] = user.get("system_account")

        with self._session_lock:
            self._current_session_id = tracking_session_id

        should_prelink_tracking_session = not self._runner._uses_sidebar_session_source(
            kwargs.get("cli_tool", ""),
            kwargs.get("workspace_type", "local"),
        )
        if should_prelink_tracking_session:
            self._link_session_to_current_milestone(tracking_session_id)

        # Inject per-workflow timeout if specified
        if "timeout" not in kwargs:
            task_timeout = (workflow_data or {}).get("task_timeout")
            if task_timeout:
                kwargs["timeout"] = int(task_timeout)

        # Append the per-content_language directive + TL;DR format instruction
        # so every phase agent outputs a one-line summary (#993) AND authors its
        # content in the workflow's content_language. AI content is generated in
        # content_language (source of truth) and rendered verbatim per viewer.
        if kwargs.get("prompt"):
            content_language = (workflow_data or {}).get("content_language")
            kwargs["prompt"] = kwargs["prompt"] + build_language_instruction(content_language)

        result = self._runner.run_agent_task(**kwargs)
        if result.session_id:
            # Link the milestone card to the REAL claude session id (not the
            # per-call wrapper uuid), so all milestones sharing a session line
            # (e.g. plan_created/plan_refined/dev on the "main" line) show the
            # SAME id and the card's "view session" points to the right transcript.
            # Falls back to the wrapper uuid when the real id isn't resolved yet.
            link_session_id = result.source_session_id or result.session_id
            self._link_session_to_current_milestone(link_session_id)
            # Persist the stable tracking id for this line. Fresh lines write
            # their first tracking id here; established lines keep reusing the
            # same wrapper row across milestones so timeline/session identity
            # does not drift with every resume attempt.
            field = SESSION_LINE_FIELDS.get(session_line)
            if field:
                self._update_workflow({field: result.session_id})
                # Keep the in-memory wf dict in sync so the next _resolve_session_line
                # call in the same phase (e.g. planning → finalize) sees the updated
                # line identity and resumes it instead of rotating wrappers.
                wf[field] = result.session_id

        # Transient API error retry (429 / 5xx / overload) — exponential
        # backoff, max 30 minutes total. Interruptible sleep (cancel check
        # every 5s) so the orchestrator can be paused/stopped during a wait.
        _CANCEL_POLL_INTERVAL = 5  # seconds between cancel checks
        retry_start = time.monotonic()
        delay = API_RETRY_INITIAL_DELAY
        while (time.monotonic() - retry_start) < API_RETRY_TOTAL_TIMEOUT:
            # Re-check workflow status each iteration: a failure/cancellation
            # set on the row (by a concurrent path or a prior failure in this
            # advance()) must abort retries, otherwise we keep spawning agent
            # subprocesses on a dead workflow for the full 30-min window. #1029
            _status = (self.workflow or {}).get("status")
            if _status in ("failed", "cancelled"):
                logger.info("API error retry aborted (workflow status=%s)", _status)
                self._synthesize_transient_failure(result)
                self._write_phase_usage(milestone_id, result)
                with self._session_lock:
                    self._current_session_id = result.session_id
                return result

            response_text = result.response_text or ""
            error_text = result.error or ""
            if not (
                self._is_transient_api_error(response_text)
                or self._is_transient_api_error(error_text)
            ):
                break  # Not a transient API error, no retry needed

            elapsed = int(time.monotonic() - retry_start)
            logger.warning(
                "Transient API error detected, retrying in %ds (elapsed: %ds / %ds): %s",
                delay,
                elapsed,
                API_RETRY_TOTAL_TIMEOUT,
                (response_text or error_text)[:160],
            )
            self._emit(
                "api_error_retry",
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
                    logger.info("API error retry cancelled (cancel requested)")
                    self._synthesize_transient_failure(result)
                    self._write_phase_usage(milestone_id, result)
                    with self._session_lock:
                        self._current_session_id = result.session_id
                    return result

            delay = min(delay * 2, API_RETRY_MAX_DELAY)

            # Rotate tracking id only; resume_session_id stays so the line holds.
            kwargs["session_id"] = str(uuid.uuid4())
            with self._session_lock:
                self._current_session_id = kwargs["session_id"]
            self._link_session_to_current_milestone(kwargs["session_id"])
            result = self._runner.run_agent_task(**kwargs)
            if result.session_id:
                self._link_session_to_current_milestone(result.session_id)

        # A transient-error body (e.g. a 529 "overloaded" returned as
        # assistant_text with no tokens generated) must not be handed back as a
        # success — callers would store it as plan/review content. The tokens==0
        # gate avoids flagging a legitimate plan that merely mentions these
        # phrases. #1001. Centralized in a helper so the retry loop's early-exit
        # paths (status failed/cancelled, cancel-requested) apply it too. #1036.
        self._synthesize_transient_failure(result)

        # Attribute this call's own usage to its milestone (increment, not cumulative).
        self._write_phase_usage(milestone_id, result)

        with self._session_lock:
            self._current_session_id = result.tracking_session_id or result.session_id
        return result

    def _synthesize_transient_failure(self, result: AgentTaskResult) -> None:
        """Synthesize a failure if the result body is an unresolved transient
        API error (e.g. a 529 "overloaded" body returned as assistant_text with
        no tokens generated).

        Prevents callers from storing the error body as plan/review content.
        The ``tokens==0`` gate avoids flagging a legitimate plan that merely
        mentions these phrases. Centralized here so the retry loop's post-loop
        path AND its early-exit paths (status failed/cancelled, cancel-requested)
        all apply it consistently. #1001, #1036.
        """
        if not (result.success and self._is_transient_api_error(result.response_text or "")):
            return
        if (result.total_tokens or 0) != 0:
            return
        err_snippet = (result.response_text or "")[:200]
        logger.warning(
            "API error response unresolved after retries, marking failed: %s",
            err_snippet,
        )
        result.success = False
        result.error = (
            result.error or f"Transient API error not resolved after retries: {err_snippet}"
        )
        result.response_text = ""  # don't let callers store the error body
        result.visible_response_text = ""
        result.structured_tags = {}

    def _write_phase_usage(self, milestone_id: str, result: AgentTaskResult) -> None:
        """Write this call's token/request increment to its milestone."""
        if not milestone_id:
            return
        try:
            self.repo.update_milestone(
                milestone_id,
                {
                    "phase_total_tokens": result.total_tokens or 0,
                    "phase_input_tokens": result.total_input_tokens or 0,
                    "phase_output_tokens": result.total_output_tokens or 0,
                    "phase_request_count": result.request_count or 0,
                },
            )
        except Exception:
            logger.warning("Failed to write phase usage to milestone", exc_info=True)

    def _write_realtime_phase_usage(self, activity: dict) -> None:
        """Write the current call's running cumulative usage to the in-progress milestone.

        Driven by live usage events so the workflow total climbs during a long
        task instead of freezing until the call returns. Overwrites phase_* each
        event (session totals are cumulative within the call); the final value is
        re-written by _write_phase_usage at _run_agent return. request_count is
        written per event too (it is incremented before the usage event fires),
        so total_requests climbs in lockstep with total_tokens instead of only
        jumping once the call returns.
        """
        try:
            milestones = self.repo.list_milestones(self._workflow_id, status="in_progress")
            if not milestones:
                return
            ms_id = milestones[-1].get("milestone_id", "")
            if not ms_id:
                return
            self.repo.update_milestone(
                ms_id,
                {
                    "phase_total_tokens": int(activity.get("total_tokens", 0) or 0),
                    "phase_input_tokens": int(activity.get("total_input_tokens", 0) or 0),
                    "phase_output_tokens": int(activity.get("total_output_tokens", 0) or 0),
                    "phase_request_count": int(activity.get("request_count", 0) or 0),
                },
            )
        except Exception:
            logger.warning("Failed to write real-time phase usage", exc_info=True)

    def pause_current_task(self):
        """Pause the currently running agent task using SIGSTOP.

        The process is frozen in place and can be resumed later.
        Unlike cancel, this does NOT clear _current_session_id so
        resume can find the session again.

        Note: we intentionally do NOT set _cancel_requested here.
        Since SIGSTOP freezes the process, _run_local's
        session.completed.wait() will block until SIGCONT resumes
        the process and it finishes. The scheduler won't re-poll
        this workflow because its status is set to "paused" in the
        database, which advance() checks at entry.
        """
        with self._session_lock:
            session_id = self._current_session_id
        if session_id:
            logger.info(
                "Pausing current agent task session=%s",
                session_id[:8],
            )
            try:
                self._runner.pause_session(session_id)
            except Exception as e:
                logger.warning("Failed to pause session %s: %s", session_id[:8], e)

    def resume_current_task(self):
        """Resume a paused agent task using SIGCONT."""
        with self._session_lock:
            session_id = self._current_session_id
        if session_id:
            logger.info(
                "Resuming paused agent task session=%s",
                session_id[:8],
            )
            try:
                self._runner.resume_session(session_id)
            except Exception as e:
                logger.warning("Failed to resume session %s: %s", session_id[:8], e)

    def cancel_current_task(self):
        """Cancel the currently running agent task (e.g. on stop).

        Terminates the subprocess with SIGTERM/SIGKILL.
        """
        self._cancel_requested.set()  # signal API-error retry loop to stop
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

        if wf.get("status") in ("paused", "failed", "cancelled"):
            return

        phase = wf.get("current_phase", "preparation")
        logger.info("Advancing workflow %s phase=%s", self._workflow_id[:8], phase)

        try:
            # Self-heal the worktree before any downstream phase runs. A
            # retried/resumed workflow may find its worktree dir gone (cleaned
            # up after a prior failure), which previously launched the agent
            # against an empty path (#814). preparation creates it, so it's
            # skipped here.
            if phase != "preparation":
                self._ensure_worktree(wf)
                # Re-read so downstream phases see the healed worktree_path /
                # branch_name in wf rather than the pre-heal snapshot.
                wf = self.workflow
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
            # Success — reset the transient retry counter so the next network
            # blip starts fresh.
            if wf.get("transient_retry_count", 0):
                self._update_workflow({"transient_retry_count": 0, "error_message": ""})
        except Exception as e:
            err_str = str(e).lower()
            is_transient = isinstance(e, GitHubOpsError) and any(
                kw in err_str for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS
            )
            if is_transient:
                # Layer 2 auto-retry: don't mark failed. Increment the
                # transient retry counter and let the scheduler retry on the
                # next cycle (~10s). This handles sustained network outages
                # that outlast the layer-1 in-call retry (3×10s in _run_git).
                transient_count = int(wf.get("transient_retry_count", 0) or 0) + 1
                if transient_count <= TRANSIENT_RETRY_MAX:
                    logger.warning(
                        "Transient error in %s for workflow %s (attempt %d/%d): %s — "
                        "will retry on next scheduler cycle",
                        phase,
                        self._workflow_id[:8],
                        transient_count,
                        TRANSIENT_RETRY_MAX,
                        e,
                    )
                    self._update_workflow(
                        {
                            "transient_retry_count": transient_count,
                            "error_message": f"Transient network error (retry {transient_count}/{TRANSIENT_RETRY_MAX}): {e}",
                        }
                    )
                    return
                logger.error(
                    "Transient error retry exhausted for workflow %s after %d attempts",
                    self._workflow_id[:8],
                    transient_count - 1,
                )
            # Non-transient error, or transient retries exhausted → fail.
            logger.error("Orchestrator error in %s: %s", phase, e, exc_info=True)
            self._update_workflow(
                {
                    "status": "failed",
                    "error_message": str(e),
                    "transient_retry_count": 0,
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
                # normpath collapses the ".." so the stored path and the worktree
                # dir on disk agree; an unnormalized path later breaks JSONL
                # session detection (#814).
                wt_path = os.path.normpath(f"{project_path}/../{branch_name.replace('/', '-')}")
                # A prior fork attempt may have created the branch then failed
                # before/after registering the worktree; cleanup only removes
                # the worktree, not the branch. Probe for a surviving branch
                # (local or remote) and attach via add_worktree (no -b) if it
                # exists, otherwise create both fresh. Same pattern as the
                # main prep path and _ensure_worktree (#814).
                _fork_branch_exists = (
                    gh._run_git(
                        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                        check=False,
                    ).returncode
                    == 0
                )
                _fork_remote_exists = (
                    gh._run_git(
                        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch_name}"],
                        check=False,
                    ).returncode
                    == 0
                )
                if _fork_branch_exists or _fork_remote_exists:
                    gh.add_worktree(path=wt_path, branch=branch_name)
                else:
                    gh.create_worktree(path=wt_path, branch=branch_name, base=base_ref)
                self._update_workflow(
                    {
                        "worktree_path": wt_path,
                        "branch_name": branch_name,
                        "branch_strategy": "worktree",
                    }
                )
                # Worktree now exists; drop the cached gh (bound to the main
                # repo) so the next _get_gh() rebinds to the worktree path.
                self._gh = None
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
                # Get system_account for multi-user permission isolation (Issue #1395)
                system_account = None
                user_id = wf.get("user_id")
                if user_id:
                    user_repo = UserRepository()
                    user = user_repo.get_user_by_id(user_id)
                    if user:
                        system_account = user.get("system_account")
                gh = GitHubOps(project_path or ".", system_account=system_account)
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
                self._gh = GitHubOps(project_path, system_account=system_account)
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
                # Persist the created issue number to the workflow so downstream
                # phases (comment posting, timeline header badge, PR body "Closes
                # #N") can resolve it. Mirrors the parsed-issue branch above.
                # Without this, wf.github_issue_number stays NULL and every
                # `wf.get("github_issue_number")` gate silently no-ops (#1194).
                self._update_workflow({"github_issue_number": issue_number})
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
            # Read issue content (body + all comments so planning sees the
            # full discussion, not just the top-level description)
            try:
                issue_data = gh.get_issue(issue_number)
                requirements_text = issue_data.get("body", "")
                comments = issue_data.get("comments", []) or []
                if comments:
                    # Skip automation-authored comments (status reports / PR
                    # links the bot itself posts) so a rerun or fork on an
                    # existing issue doesn't re-ingest them as requirements.
                    comments = [c for c in comments if not _is_bot_comment(c)]
                if comments:
                    # Format comments in chronological order. The gh CLI
                    # returns author as {"login": "..."} and timestamps in
                    # camelCase ("createdAt"), matching list_issue_comments.
                    formatted = []
                    for c in comments:
                        author = c.get("author", {}) or {}
                        author_name = (
                            author.get("login") if isinstance(author, dict) else author
                        ) or "unknown"
                        created = c.get("createdAt", "") or c.get("created_at", "")
                        stamp = f" ({created})" if created else ""
                        formatted.append(f"### 评论 by @{author_name}{stamp}\n{c.get('body', '')}")
                    requirements_text += "\n\n---\n\n## Issue 评论（补充信息）\n\n" + "\n\n".join(
                        formatted
                    )
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_linked",
                    status="completed",
                    title=f"Linked to issue #{issue_number}",
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
                    # normpath collapses ".." so DB/JSONL encoding matches the
                    # real worktree dir (#814).
                    worktree_path = os.path.normpath(
                        f"{project_path}/../{branch_name.replace('/', '-')}"
                    )

                    # Clean up prunable worktrees (directory lost but registered)
                    # This happens when worktree dir was deleted externally (Issue #1442)
                    gh._run_git(["worktree", "prune"])

                    # Check and clean up residual worktree (Issue #1442).
                    # Path.exists()/shutil.rmtree() run as the service user and
                    # hit [Errno 13] under a user-private parent (700 home),
                    # so we cross-check via git's own worktree registry (already
                    # routed through the sudo wrapper in _run_git) instead of
                    # Python filesystem calls. Bare residual dirs (registered
                    # then pruned, but the directory survived) are detected via
                    # path_exists_as_user; removal of those falls back to git
                    # and, on failure, is left in place with a warning rather
                    # than force-deleting as the service user.
                    if any(wt.get("path") == worktree_path for wt in gh.list_worktrees()):
                        try:
                            gh.remove_worktree(worktree_path)
                            logger.info("Removed residual worktree at %s", worktree_path)
                        except GitHubOpsError as e:
                            logger.warning(
                                "Could not remove residual worktree %s: %s", worktree_path, e
                            )
                    elif gh.path_exists_as_user(worktree_path, dir_only=True):
                        # Directory present but not git-registered (e.g. pruned).
                        # git worktree remove will reject it; we cannot safely
                        # rmtree as the service user, so leave it and let
                        # create_worktree surface a clear error if it blocks.
                        logger.warning(
                            "Residual non-worktree directory at %s is not git-registered; "
                            "leaving in place (no rm privilege as service user)",
                            worktree_path,
                        )

                    # A prior run may have created the branch then failed before
                    # (or after) registering the worktree; the cleanup above only
                    # removes the worktree registration, not the branch. Blindly
                    # calling create_worktree (which uses ``-b``) would then fail
                    # with "a branch named '<branch>' already exists". If the
                    # branch survives (local or remote), attach a worktree to it
                    # via add_worktree (no ``-b``); otherwise create both fresh.
                    # Mirrors the recovery logic in _ensure_worktree.
                    branch_exists = (
                        gh._run_git(
                            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                            check=False,
                        ).returncode
                        == 0
                    )
                    remote_exists = (
                        gh._run_git(
                            [
                                "show-ref",
                                "--verify",
                                "--quiet",
                                f"refs/remotes/origin/{branch_name}",
                            ],
                            check=False,
                        ).returncode
                        == 0
                    )
                    if branch_exists or remote_exists:
                        wt_data = gh.add_worktree(path=worktree_path, branch=branch_name)
                    else:
                        wt_data = gh.create_worktree(
                            path=worktree_path,
                            branch=branch_name,
                            base="origin/main",
                        )
                    self._update_workflow({"worktree_path": wt_data.get("worktree_path", "")})
                    # The worktree now exists; drop the cached gh (bound to the
                    # main repo during preparation) so the next _get_gh() rebinds
                    # to the worktree path — the agent's actual working repo.
                    self._gh = None
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
        force_full_rounds = self._must_run_full_review_rounds(wf)
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
            permission_mode=_zcode_planning_mode(wf),
            allowed_tools=planning_allowed,
            timeout=planning_timeout,
            session_line="main",
            milestone_id=ms.get("milestone_id", ""),
        )

        # Clear user feedback after it has been injected into the prompt
        if wf.get("user_feedback", "").strip():
            self._update_workflow({"user_feedback": ""})

        self._accumulate_tokens(result)

        # Store plan
        plan_text = self._artifact_text(result)
        self.repo.update_milestone(
            ms.get("milestone_id", ""),
            {
                "status": "completed" if result.success else "failed",
                "plan_content": plan_text,
                "result_summary": plan_text[:200],
                "tldr": self._artifact_tldr(result),
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
                        "partial_plan": (self._artifact_visible_text(result) or plan_text)[:500],
                    },
                )
            else:
                self._update_workflow(
                    {"status": "failed", "error_message": f"Planning failed: {result.error}"}
                )
            return

        # Post plan as issue comment
        if issue_number:
            self._post_github_comment(
                gh,
                issue_number,
                f"## 📋 Implementation Plan (Round {round_num})\n\n{plan_text}",
                context="plan",
            )

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
            permission_mode=_zcode_planning_mode(wf),
            allowed_tools=planning_allowed,
            timeout=planning_timeout,
            session_line="review",
            milestone_id=review_ms.get("milestone_id", ""),
        )

        self._accumulate_tokens(review_result)

        review_text = self._artifact_text(review_result)

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
                "tldr": self._artifact_tldr(review_result),
                "review_session_id": review_result.session_id,
            },
        )

        # Post review as issue comment
        if issue_number:
            self._post_github_comment(
                gh,
                issue_number,
                f"## 🔍 Plan Review (Round {round_num})\n\n{review_text}",
                context="plan-review",
            )

        # Step 3: Check if all rounds are done
        # max_plan_rounds is the cap for plan review rounds. In the default
        # mode we continue only when the review has substantive feedback; in
        # force-full mode we continue until the cap even after approval.
        self._update_workflow({"current_round": round_num})

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        # Default mode may stop early when the review passes. When
        # require_full_review_rounds is enabled, planning keeps going until the
        # configured cap so the workflow always consumes exactly N review
        # rounds. The cap is still strict: rounds run 1..max, never max+1.
        needs_refinement = round_num < max_rounds and (force_full_rounds or review_has_feedback)

        if not needs_refinement:
            # Planning complete. Gather the latest plan and the last review.
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

            # Act on the LAST review's feedback with one final plan_refined (its
            # own milestone), so the timeline shows review(N) -> plan_refined ->
            # plan_finalized and the Nth review is never wasted. Skipped only
            # when the last review purely approved (nothing to integrate).
            if final_plan and (review_has_feedback or self._should_refine_plan(last_review)):
                refine_ms = self._create_milestone(
                    phase="planning",
                    dev_round=dev_round,
                    round_number=round_num,
                    milestone_type="plan_refined",
                    status="in_progress",
                    title=f"Plan refine (final, round {round_num})",
                )
                refine_prompt = (
                    PLANNING_CONTEXT + "请根据以下审查意见完善实现方案，输出最终的完整方案。"
                    "直接输出方案，不要输出思考过程或引导文字。\n\n"
                    f"## 当前方案\n{final_plan}\n\n"
                    f"## 审查意见\n{last_review}\n\n"
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
                    permission_mode=_zcode_planning_mode(wf),
                    allowed_tools=planning_allowed,
                    timeout=PLANNING_TIMEOUT,
                    session_line="main",
                    milestone_id=refine_ms.get("milestone_id", ""),
                )
                self._accumulate_tokens(refine_result)
                if refine_result.success:
                    final_plan = self._artifact_text(refine_result) or final_plan
                self.repo.update_milestone(
                    refine_ms.get("milestone_id", ""),
                    {
                        "status": "completed" if refine_result.success else "failed",
                        "plan_content": final_plan,
                        "result_summary": final_plan[:200],
                        "tldr": self._extract_tldr(final_plan),
                        "error_message": refine_result.error or "",
                    },
                )
                if not refine_result.success:
                    # The final refine is what guarantees the Nth review's
                    # feedback is acted on. If it failed (timeout / model /
                    # runner), do NOT silently proceed with the pre-refine plan —
                    # block for retry, mirroring the plan-agent failure path.
                    # Otherwise the last review would be dropped quietly (#1200).
                    if "timed out" in (refine_result.error or ""):
                        self._update_workflow(
                            {
                                "status": "planning_timeout",
                                "error_message": (
                                    f"Plan refine timed out after {PLANNING_TIMEOUT}s. "
                                    "You can extend the timeout and retry."
                                ),
                            }
                        )
                        self._emit(
                            "planning_timeout",
                            {
                                "timeout": PLANNING_TIMEOUT,
                                "tokens_used": refine_result.total_tokens,
                                "partial_plan": final_plan[:500],
                            },
                        )
                    else:
                        self._update_workflow(
                            {
                                "status": "failed",
                                "error_message": f"Plan refine failed: {refine_result.error}",
                            }
                        )
                    return

            final_plan = self._sanitize_artifact_text(final_plan)

            # Record the authoritative final plan.
            plan_final_ms = self._create_milestone(
                phase="planning",
                dev_round=dev_round,
                round_number=round_num,
                milestone_type="plan_finalized",
                status="in_progress",
                title="Plan Finalized",
            )
            self.repo.update_milestone(
                plan_final_ms.get("milestone_id", ""),
                {
                    "status": "completed",
                    "plan_content": final_plan,
                    "result_summary": final_plan[:200],
                    "tldr": self._extract_tldr(final_plan),
                },
            )

            issue_number = wf.get("github_issue_number")
            if issue_number and final_plan:
                final_comment = (
                    f"## 📋 Final Implementation Plan\n\n"
                    f"Plan review completed after {round_num} round(s).\n\n"
                    f"{final_plan}"
                )
                self._post_github_comment(gh, issue_number, final_comment, context="final-plan")

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
        skip_retries = wf.get("skip_retries", 0)

        # ── Development phase (skipped on test-only/skip retry) ──
        if test_retries > 0 or skip_retries > 0:
            logger.info(
                "Test/skip retry (test=%d, skip=%d) for dev round %d, skipping development phase",
                test_retries,
                skip_retries,
                dev_round,
            )
        else:
            self._run_development_agent(wf, dev_round, gh)
            # Post development completion comment — but only if dev succeeded.
            # _run_development_agent sets status="failed" on failure; without
            # this guard, a "✅ Completed" comment is posted with a stale
            # commit that isn't the agent's work (#525).
            wf = self.workflow or {}
            if wf.get("status") != "failed":
                self._post_dev_completion_comment(wf, dev_round, gh)

        # ── Test phase (always runs) ──
        self._run_test_phase(wf, dev_round, gh)

    def _run_development_agent(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Run the development agent, verify code changes, and return.

        On failure, updates workflow status to 'failed' and returns.
        Caller should check workflow status if needed.
        """
        # Get the finalized plan — prefer the explicit plan_finalized milestone
        # (authoritative final plan), then fall back to the latest plan_content.
        milestones = self.repo.list_milestones(self._workflow_id, phase="planning")
        final_plan = ""
        for ms in reversed(milestones):
            if ms.get("milestone_type") == "plan_finalized" and ms.get("plan_content"):
                final_plan = ms["plan_content"]
                break
        if not final_plan:
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
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="main",
            timeout=int(wf.get("task_timeout") or DEFAULT_TASK_TIMEOUT),
            milestone_id=ms.get("milestone_id", ""),
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
            diff_stats = (
                gh.get_commit_diff_stats(commit_sha)
                if commit_sha
                else gh.get_diff_stats("HEAD~1", "HEAD")
            )
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
                        diff_stats = (
                            gh.get_commit_diff_stats(commit_sha)
                            if commit_sha
                            else gh.get_diff_stats("HEAD~1", "HEAD")
                        )
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

            if branch_has_changes_vs_base and commit_sha != commit_before:
                # The branch has commits vs origin/main AND the HEAD advanced
                # during this session (commit_sha differs from commit_before).
                # This covers resume scenarios where the agent committed in a
                # prior session on the same branch.
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
            elif branch_has_changes_vs_base and commit_sha == commit_before:
                # Branch has pre-existing divergence (e.g. it was created
                # behind main), but the agent produced NO new commit this
                # session. The diff is NOT the agent's work — treat as failure.
                logger.warning(
                    "Branch has %d commits vs origin/main but HEAD unchanged "
                    "this session (commit_sha == commit_before) — not agent work",
                    base_diff_stats.get("commits", 0),
                )
                logger.warning("Agent reported success but no new commits detected (SHA unchanged)")
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "session_id": result.session_id,
                        "result_summary": self._artifact_text(result)[:300],
                        "tldr": self._artifact_tldr(result),
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

        # Salvage logic (issue #723): the dev agent may report failure
        # (success=False) even though it produced real work this session — most
        # commonly a subprocess timeout where claude-code finished and committed
        # but never emitted a closing `result` event, or an API error that
        # landed after the commit. When ``sha_changed`` proves the agent
        # committed code, treat the round as completed instead of discarding
        # hours of work. Only the no-commit case (handled at the ``not
        # sha_changed`` branch above) is a true failure.
        #
        # Note (scope): ``sha_changed`` is set both by an explicit agent commit
        # AND by the auto-commit of uncommitted changes above. So a failed agent
        # that left half-finished edits is also salvaged and forwarded to the
        # test phase. This is intentional: the test phase is the gate that
        # catches broken/half-finished code, so salvaging lets the workflow
        # progress to that gate rather than dying on a late subprocess error.
        salvaged = (not result.success) and bool(sha_changed)
        if salvaged:
            logger.warning(
                "Dev agent reported failure (%s) but produced a new commit %s "
                "this session — salvaging development and proceeding to tests",
                result.error,
                (commit_sha or "")[:8],
            )

        dev_result_summary = self._build_dev_result_summary(
            self._artifact_text(result),
            diff_stats,
            commit_sha,
            result.success or salvaged,
        )

        milestone_error = ""
        if salvaged:
            milestone_error = f"Salvaged after agent error: {result.error or ''}".strip()
        elif result.error:
            milestone_error = result.error

        self.repo.update_milestone(
            ms.get("milestone_id", ""),
            {
                "status": "completed" if (result.success or salvaged) else "failed",
                "session_id": result.session_id,
                "result_summary": dev_result_summary,
                "tldr": self._artifact_tldr(result),
                "commit_shas": json.dumps([commit_sha] if commit_sha else []),
                "diff_stats": json.dumps(diff_stats),
                "error_message": milestone_error,
            },
        )

        if not result.success and not salvaged:
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

        self._post_github_comment(gh, issue_number, msg, context="dev-progress")

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
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="test",
            milestone_id=test_ms.get("milestone_id", ""),
        )

        self._accumulate_tokens(test_result)

        test_summary = self._artifact_text(test_result)
        test_visible_text = self._artifact_visible_text(test_result)
        self.repo.update_milestone(
            test_ms.get("milestone_id", ""),
            {
                "status": "completed" if test_result.success else "failed",
                "session_id": test_result.session_id,
                "result_summary": test_summary,
                "tldr": self._artifact_tldr(test_result),
            },
        )

        # Detect if tests were actually skipped (agent couldn't run them)
        test_response_text = test_visible_text or test_summary
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
        test_status_tag = self._artifact_status_tag(test_result, "test_status").lower()
        tests_actually_skipped = (
            test_status_tag == "skipped"
            or any(m in test_response_text for m in _skipped_markers)
            or (test_result.success and has_skip_keyword)
            or (test_result.success and not has_test_result)
        )

        # Post test results to issue
        issue_number = wf.get("github_issue_number")
        if issue_number:
            if tests_actually_skipped:
                status_line = "⚠️ Tests were not actually run — see details below"
            elif test_result.success:
                status_line = "✅ All tests passed"
            else:
                status_line = "❌ Tests failed"
            test_comment = (
                f"## 🧪 Test Results (Dev Round {dev_round})\n\n{status_line}\n\n{test_summary}"
            )
            self._post_github_comment(gh, issue_number, test_comment, context="test-results")

        # Treat skipped tests as failure — tests must actually run.
        # Allow 1 retry in case of transient environment issues.
        if tests_actually_skipped:
            # Correct the milestone status: it was optimistically set to
            # "completed" above based on session success alone, but tests
            # were not actually executed. Without this correction, the
            # timeline shows "completed" while the comment says "skipped".
            self.repo.update_milestone(
                test_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "error_message": "Tests were not actually run (skipped by agent)",
                },
            )
            skip_retries = wf.get("skip_retries", 0) + 1
            if skip_retries <= 1:
                logger.warning(
                    "Tests were skipped (not actually run) for dev round %d, retry %d/1",
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
        test_response = self._artifact_visible_text(test_result) or self._artifact_text(test_result)
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
        self._update_workflow({"test_retries": 0, "dev_retries_on_test_fail": 0, "skip_retries": 0})

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
            branch = wf.get("branch_name", "")
            status_msg = (
                f"## 🎯 All Checks Passed (Dev Round {dev_round})\n\n"
                f"- **Status**: Development + tests completed successfully\n"
                f"- **Branch**: `{branch}`\n"
                f"- **Next**: Creating PR and running code review\n"
            )
            self._post_github_comment(gh, issue_number, status_msg, context="dev-complete")

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
        force_full_rounds = self._must_run_full_review_rounds(wf)
        dev_round = wf.get("dev_round", 1)
        branch_name = wf.get("branch_name", "")
        gh = self._get_gh()
        # Language-aware approval marker for PR review (matches what the agent,
        # writing in content_language, is asked to state).
        approval_phrase = _review_approval_phrase(wf.get("content_language"))

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
                self._post_github_comment(gh, issue_number, no_change_msg, context="no-changes")
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
                pr_body = f"Autonomous development for dev round {dev_round}.\n\nRequirements: {wf.get('requirements_text', '')}"
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
                except Exception:
                    ci_checks_post = []
                ci_fails_post = [c for c in ci_checks_post if c.get("bucket") == "fail"]
                if ci_fails_post:
                    ci_summary = "\n".join(
                        f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_fails_post
                    )
                    self._post_github_comment(
                        gh,
                        pr_number,
                        "## ⚠️ CI 检查状态\n\n"
                        f"以下 CI 检查未通过：\n{ci_summary}\n\n"
                        "将在后续代码审查轮次中分析这些失败是否由本 PR 引入。",
                        is_pr=True,
                        context="ci-fails",
                    )

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
            f"## 代码变更\n{self._smart_truncate_diff(diff_text)}\n\n"
        )

        # For rounds > 1, the previous round's review is already in this review
        # session's resumed history (--resume). Ask the reviewer to revisit it
        # and confirm whether each point was addressed.
        if round_num > 1:
            review_prompt += (
                "## 上一轮审查\n"
                "请回顾你上一轮的审查意见（在本会话历史中），逐条确认是否已落实："
                "已落实（说明如何修改）/ 未落实（说明原因）/ 不适用（说明理由）。\n\n"
            )

        review_prompt += (
            "请检查：\n"
            "1. 代码质量和可读性\n"
            "2. 潜在 bug 和安全问题\n"
            "3. 测试覆盖率\n"
            "4. 性能影响\n"
            "5. 与需求的对齐程度\n"
            "6. 上一轮审查意见的落实情况(如有)\n\n"
            f"如果没有重大问题，请在审查结论中明确写出批准标记：{approval_phrase}。\n\n"
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
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="review",
            milestone_id=review_ms.get("milestone_id", ""),
        )

        self._accumulate_tokens(review_result)

        review_text = self._artifact_text(review_result)
        # Detect approval using the language-aware marker, then persist a
        # structured verdict so progress_reported doesn't re-scan review text.
        # The legacy zh marker is accepted too, for workflows whose content
        # language predates this field (mirrors _derive_review_passed).
        review_passed = (
            self._review_is_approved(review_text, approval_phrase) or "代码审查通过" in review_text
        )
        review_metadata = _merge_milestone_metadata(
            self.repo.get_milestone(review_ms.get("milestone_id", "")),
            {"review_verdict": {"passed": review_passed, "round": round_num}},
        )
        self.repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {
                "status": "completed" if review_result.success else "failed",
                "review_content": review_text,
                "review_session_id": review_result.session_id,
                "tldr": self._artifact_tldr(review_result),
                "metadata": review_metadata,
            },
        )

        # Post review as PR comment
        if pr_number:
            self._post_github_comment(
                gh,
                pr_number,
                f"## 🔍 Code Review (Round {round_num})\n\n{review_text}",
                is_pr=True,
                context="code-review",
            )

        # Check if all rounds done
        self._update_workflow({"current_round": round_num})

        # Every review with findings gets a fix — including the cap round — so
        # the last review's feedback is never silently dropped. Total reviews are
        # capped at max_pr_review_rounds (matches the "PR 审查最大轮次" label):
        # after the cap-round fix we go straight to summary/report instead of
        # scheduling another review. In the default mode, an approved review can
        # also end PR review early; with require_full_review_rounds enabled, only
        # the cap ends the loop. There is never an (N+1)-th review.
        at_cap = round_num >= max_rounds
        if not review_passed:
            self._apply_pr_review_fix(
                wf, gh, review_text, round_num, dev_round, ci_failures, pr_number
            )

        if (review_passed and not force_full_rounds) or at_cap:
            # All PR review rounds completed — summarize via the main session,
            # then move to report. The main session resumes with the development
            # history (incl. fixes) and is given the last review round's feedback
            # (review runs on the review session, so it must be injected), then
            # asked whether all review points were addressed and the PR is ready.
            last_pr_review = ""
            pr_milestones = self.repo.list_milestones(self._workflow_id, phase="pr_review")
            for ms in reversed(pr_milestones):
                if ms.get("milestone_type") == "pr_reviewed" and ms.get("review_content"):
                    last_pr_review = ms["review_content"]
                    break

            summary_ms = self._create_milestone(
                phase="pr_review",
                dev_round=dev_round,
                round_number=round_num,
                milestone_type="pr_review_summary",
                status="in_progress",
                title="PR Review Summary",
            )

            summary_prompt = (
                AUTONOMOUS_CONTEXT + "代码审查已全部完成。请根据最后一轮审查意见，"
                "并结合本会话历史中开发环节的修复记录，"
                "输出一份 PR 评审总结，明确：\n"
                "1. 最后一轮审查意见是否已全部落实\n"
                "2. 是否还有遗留问题需要处理\n"
                "3. 当前 PR 是否可以合并\n\n"
                f"## 最后一轮审查意见\n{self._clean_agent_text(last_pr_review)}\n\n"
                "如果审查意见已全部落实、无遗留问题，请明确说明'可以合并'。"
                "直接输出总结，不要添加引导文字。"
            )
            summary_result = self._run_agent(
                wf=wf,
                workflow_id=self._workflow_id,
                cli_tool=wf.get("cli_tool", "claude-code"),
                model=wf.get("model", ""),
                project_path=wf.get("worktree_path") or wf.get("project_path", ""),
                prompt=summary_prompt,
                workspace_type=wf.get("workspace_type", "local"),
                remote_machine_id=wf.get("remote_machine_id"),
                permission_mode=wf.get("permission_mode", "auto-edit"),
                allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(
                    wf.get("cli_tool", "claude-code"), []
                ),
                session_line="main",
                milestone_id=summary_ms.get("milestone_id", ""),
            )
            self._accumulate_tokens(summary_result)
            summary_text = self._artifact_text(summary_result)
            self.repo.update_milestone(
                summary_ms.get("milestone_id", ""),
                {
                    "status": "completed" if summary_result.success else "failed",
                    "review_content": summary_text,
                    "result_summary": summary_text[:200],
                    "tldr": self._artifact_tldr(summary_result),
                },
            )

            if pr_number and summary_text:
                self._post_github_comment(
                    gh,
                    pr_number,
                    f"## ✅ PR Review Summary\n\n{summary_text}",
                    is_pr=True,
                    context="review-summary",
                )

            # Move to report
            self._update_workflow(
                {
                    "current_phase": "report",
                    "status": "reporting",
                }
            )
            self._emit("phase_change", {"phase": "report"})
        # Under cap, the scheduler re-enters _do_pr_review for the next review
        # round. In the default mode this path means "not approved and the fix
        # above already ran"; with force-full enabled it also covers "approved
        # early, but keep reviewing until the configured cap".

    def _apply_pr_review_fix(
        self,
        wf: dict,
        gh,
        review_text: str,
        round_num: int,
        dev_round: int,
        ci_failures: list,
        pr_number,
    ) -> None:
        """Apply one round of code-review fixes (pr_updated milestone).

        Runs for every non-passing review — including the cap round — so the
        last review's feedback is never silently dropped (#1200 review).
        """
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

        commit_before = ""
        try:
            commit_before = gh.get_current_commit()
        except Exception:
            pass

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
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="main",
            milestone_id=fix_ms.get("milestone_id", ""),
        )

        self._accumulate_tokens(fix_result)

        # Clear user feedback after it has been injected into the prompt
        if wf.get("user_feedback", "").strip():
            self._update_workflow({"user_feedback": ""})

        # Agent may fail to commit (bash blocked / forgot) — salvage uncommitted
        # changes the way dev does, else the fix never reaches the PR (#960
        # symptom). A no-op fix is genuinely empty, not a failed dev round.
        commit_sha = ""
        diff_stats = {}
        try:
            commit_sha = gh.get_current_commit()
        except Exception:
            pass
        sha_changed = commit_before and commit_sha and commit_before != commit_sha
        if not sha_changed:
            try:
                if gh.has_uncommitted_changes():
                    gh.git_add_all()
                    gh.git_commit(
                        f"auto: review fixes (round {round_num})",
                        no_verify=True,
                    )
                    commit_sha = gh.get_current_commit()
                    sha_changed = True
            except Exception as e:
                logger.warning("Fix auto-commit failed: %s", e)
        if sha_changed:
            try:
                gh.git_push()
            except Exception as e:
                # push failure leaves the fix local; the next pr_review re-reads
                # the old PR state — log so it's diagnosable.
                logger.warning("Fix git_push failed (round %d): %s", round_num, e)
        try:
            diff_stats = gh.get_commit_diff_stats(commit_sha) if commit_sha else {}
        except Exception:
            pass

        self.repo.update_milestone(
            fix_ms.get("milestone_id", ""),
            {
                "status": "completed" if fix_result.success else "failed",
                "session_id": fix_result.session_id,
                "commit_shas": json.dumps([commit_sha] if commit_sha else []),
                "diff_stats": json.dumps(diff_stats),
                "result_summary": self._artifact_text(fix_result)[:200],
                "tldr": self._artifact_tldr(fix_result),
            },
        )

        if pr_number:
            # Extract fix summary from agent response in full; GitHub comments
            # render long content fine (capped by _post_github_comment if huge).
            fix_summary = self._artifact_text(fix_result)
            comment = (
                f"## ✅ Addressed Review Feedback (Round {round_num})\n\n"
                f"### Changes Made\n{fix_summary}\n\n"
            )
            if commit_sha:
                comment += f"- Commit: `{commit_sha[:8]}`\n"
            # Note pre-existing CI failures if fix agent identified them.
            if self._artifact_status_tag(
                fix_result, "ci_status"
            ).lower() == "pre-existing" or self._is_pre_existing_ci_failure(
                self._artifact_visible_text(fix_result)
            ):
                comment += "\n> ⚠️ 部分 CI 检查失败，" "但经分析为预先存在的问题，非本 PR 引入。\n"
            self._post_github_comment(gh, pr_number, comment, is_pr=True, context="fix")

    # ── Phase: Report ───────────────────────────────────────────────

    def _do_report(self, wf: dict):
        """Generate progress report and update issue."""
        dev_round = wf.get("dev_round", 1)
        gh = self._get_gh()
        issue_number = wf.get("github_issue_number")
        pr_number = wf.get("github_pr_number")
        all_milestones = self.repo.list_milestones(self._workflow_id, dev_round=dev_round)

        # 1. Plan summary (from finalized plan — prefer plan_finalized milestone)
        plan_summary = ""
        for ms in reversed(all_milestones):
            if (
                ms.get("plan_content")
                and ms.get("phase") == "planning"
                and ms.get("milestone_type") == "plan_finalized"
            ):
                plan_summary = self._clean_agent_text(ms["plan_content"])
                break
        if not plan_summary:
            for ms in reversed(all_milestones):
                if ms.get("plan_content") and ms.get("phase") == "planning":
                    plan_summary = self._clean_agent_text(ms["plan_content"])
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
                test_summary = self._clean_agent_text(ms["result_summary"])
                break

        # 4. Code review rounds + structured approval verdict
        pr_review_milestones = [
            ms for ms in all_milestones if ms.get("milestone_type") == "pr_reviewed"
        ]
        review_rounds = sum(1 for ms in pr_review_milestones if ms.get("phase") == "pr_review")
        review_passed = self._derive_review_passed(pr_review_milestones, wf.get("content_language"))

        # Build the structured report payload — the single source of truth. The
        # one-line summary and full report are NOT persisted as localized prose;
        # the frontend renders them from ``metadata.report`` in the viewer's UI
        # language. The GitHub issue comment is rendered here in the workflow's
        # content_language (the issue audience expects the workflow's language).
        content_language = wf.get("content_language")
        payload = build_progress_payload(
            dev_round=dev_round,
            plan_summary=plan_summary,
            diff_stats=diff_stats,
            test_summary=test_summary,
            review_rounds=review_rounds,
            review_passed=review_passed,
            pr_number=pr_number,
            branch=wf.get("branch_name", ""),
            total_tokens=wf.get("total_tokens", 0),
            total_requests=wf.get("total_requests", 0),
        )
        report_metadata = json.dumps({"report": payload}, ensure_ascii=False)
        report_markdown = render_progress_report(payload, content_language)

        self._create_milestone(
            phase="report",
            dev_round=dev_round,
            milestone_type="progress_reported",
            status="completed",
            title=f"Progress report for round {dev_round}",
            metadata=report_metadata,
        )

        # Post report to issue
        if issue_number:
            self._post_github_comment(gh, issue_number, report_markdown, context="progress-report")

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

        If auto_merge is enabled and PR exists, skip waiting and proceed to merge.
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

        # Auto merge check for batch workflows
        auto_merge = wf.get("auto_merge", True)
        github_pr_number = wf.get("github_pr_number")
        if auto_merge and github_pr_number:
            # PR exists and auto_merge enabled - skip waiting, go directly to merge
            logger.info(
                "Auto merge enabled for workflow %s, proceeding to merge phase",
                self._workflow_id[:8],
            )
            self._update_workflow(
                {
                    "current_phase": "merge",
                    "status": "merging",
                }
            )
            self._emit(
                "phase_change",
                {"phase": "merge", "auto_merge": True},
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
        user_comments = [c for c in comments if not _is_bot_comment(c)]

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
        """Merge PR and clean up. Resolves merge conflicts automatically.

        Merge is retried across scheduler cycles instead of blocking on CI:
        if CI is still running we return (staying in 'merging') and the
        scheduler retries in ~10s. This avoids hogging a workflow thread for
        the full CI duration (10+ min for Python 3.9) and naturally adapts
        to variable CI times without --admin bypass or long polls.
        """
        gh = self._get_gh()
        pr_number = wf.get("github_pr_number")
        branch_name = wf.get("branch_name", "")

        if pr_number:
            # If CI is still running, defer this merge to the next scheduler
            # cycle instead of blocking (synchronous poll) or failing. The
            # scheduler re-enters _do_merge every ~10s.
            try:
                checks = gh.get_pr_checks(pr_number)
            except Exception:
                checks = []
            pending = [c for c in checks if c.get("bucket") == "pending"]
            if pending:
                logger.info(
                    "PR #%s: %d CI checks pending, deferring merge to next cycle",
                    pr_number,
                    len(pending),
                )
                return

            try:
                gh.merge_pr(pr_number, strategy="merge")
                self._create_milestone(
                    phase="merge",
                    milestone_type="merged",
                    status="completed",
                    title=f"PR #{pr_number} merged",
                )
            except GitHubOpsError as e:
                err_msg = str(e)
                if "base branch policy prohibits" in err_msg:
                    # CI reports done but GitHub hasn't reconciled yet, or a
                    # late check started. Check whether CI actually failed —
                    # if so, this is a real failure, not a transient deferral.
                    failed = [c for c in checks if c.get("bucket") == "fail"]
                    if failed:
                        failed_names = ", ".join(c.get("name", "?") for c in failed)
                        raise GitHubOpsError(
                            f"PR #{pr_number} CI failed ({failed_names}), cannot merge"
                        )
                    # No failures, just policy lag — defer to next cycle.
                    logger.info(
                        "PR #%s: policy prohibits (CI not failed), deferring merge", pr_number
                    )
                    return
                try:
                    # Merge conflict — resolve locally and retry
                    logger.info("PR #%s not mergeable, resolving conflicts", pr_number)
                    self._resolve_merge_conflicts(gh, branch_name, pr_number)
                    # Conflicts resolved + pushed, but NOT merged yet — the push
                    # triggered a fresh CI run. Return here (staying in 'merging')
                    # so _do_merge's CI-pending deferral handles the wait on the
                    # next cycle. Falling through to cleanup would delete the
                    # branch before the PR is merged (#1112 P1).
                    return
                except Exception as resolve_err:
                    self._create_milestone(
                        phase="merge",
                        milestone_type="merged",
                        status="failed",
                        title="PR merge failed",
                        error_message=f"Merge conflict resolution failed: {resolve_err}",
                    )
                    raise

        # Clean up branch/worktree. Re-read wf because _resolve_merge_conflicts
        # may have cleared worktree_path (removed the original worktree to free
        # the branch for the temp merge worktree); using the stale snapshot
        # would retry the removal and fail, skipping branch deletion (#1107).
        wf = self.workflow
        branch_name = wf.get("branch_name", "")
        worktree_path = wf.get("worktree_path", "")
        project_path = wf.get("project_path", "")
        # Get system_account for multi-user permission isolation (Issue #1395)
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")
        try:
            if worktree_path:
                # Must use main repo's gh to remove worktree
                # (can't remove a worktree from within itself)
                main_gh = GitHubOps(project_path, system_account=system_account)
                main_gh.remove_worktree(worktree_path)
                self._update_workflow({"worktree_path": ""})
                # Reinitialize gh to point at main repo for branch deletion
                self._gh = GitHubOps(project_path, system_account=system_account)
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
        """Resolve merge conflicts in an isolated worktree, push, and merge the PR.

        Previously this checked out the PR branch directly in the main repo,
        which polluted the shared working tree (``index.lock`` races with
        concurrent workflows, ``reset --hard`` clobbered in-flight resolution
        on scheduler re-entry). Now a throwaway worktree is created for the
        branch, all merge/resolve/push happens inside it, and it is removed in
        a ``finally`` — the main repo's index and HEAD are never touched.
        """
        wf = self.workflow
        project_path = wf.get("project_path", "")
        worktree_path = wf.get("worktree_path", "")
        # Get system_account for multi-user permission isolation (Issue #1395)
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")

        # Git forbids checking out the same branch in two worktrees, so the
        # workflow's own worktree (if still present) must be removed first to
        # free the branch for the temp worktree below.
        main_gh = GitHubOps(project_path, system_account=system_account)
        if worktree_path:
            try:
                main_gh.remove_worktree(worktree_path)
            except GitHubOpsError as e:
                logger.warning("Could not remove existing worktree %s: %s", worktree_path, e)
            self._update_workflow({"worktree_path": ""})
            # The caller's gh still points at the now-deleted worktree dir as
            # its cwd. Rebind it (and the cached self._gh) to the main repo so
            # the later merge_pr / _do_merge cleanup don't run subprocess with
            # a gone cwd (#1107 review).
            gh = GitHubOps(project_path, system_account=system_account)
            self._gh = gh

        # Create an isolated worktree for the existing PR branch. Use the main
        # repo's gh so the worktree is registered against the real .git.
        temp_wt_path = os.path.normpath(f"{project_path}/../merge-{self._workflow_id[:8]}")
        main_gh.add_worktree(temp_wt_path, branch_name)
        logger.info("Created temporary merge worktree at %s", temp_wt_path)

        # All subsequent git ops run inside the temp worktree.
        wt_gh = GitHubOps(temp_wt_path, system_account=system_account)
        try:
            # Fetch latest main and merge into the branch.
            wt_gh._run_git(["fetch", "origin", "main"])
            merge_result = wt_gh._run_git(["merge", "origin/main"], check=False)
            # git writes conflict summaries to STDOUT (not stderr), so we must
            # check both streams. Checking only stderr left stderr empty on a
            # real conflict and the code misclassified it as a "non-conflict"
            # failure, abandoning merge without ever invoking the AI resolver.
            combined_output = f"{merge_result.stdout}\n{merge_result.stderr}"
            if merge_result.returncode != 0:
                if "CONFLICT" not in combined_output:
                    raise GitHubOpsError(
                        f"git merge failed (non-conflict): {merge_result.stderr.strip()}"
                    )

                # Ask AI agent to resolve conflicts inside the temp worktree.
                conflict_prompt = (
                    AUTONOMOUS_CONTEXT
                    + "当前分支与 main 存在合并冲突。请解决所有冲突文件中的冲突标记，"
                    "保留两边的有效修改。\n\n"
                    "步骤：\n"
                    "1. 查看所有冲突文件：git diff --name-only --diff-filter=U\n"
                    "2. 逐个解决冲突标记（<<<<<<, ======, >>>>>>）\n"
                    "3. git add 所有解决后的文件\n"
                    "4. 运行测试验证冲突解决没有破坏功能（不能跳过）：\n"
                    "   - python -m pytest 或 python3 -m pytest\n"
                    "   - 如果有测试失败，分析原因并修复，然后重新测试\n"
                    "   - 特别注意：main 上的改动可能修改了函数签名/SQL/接口，\n"
                    "     冲突文件相关的测试也需要同步更新\n"
                    "   - 重复直到所有测试通过\n"
                    "5. 测试全部通过后，git commit 完成合并\n\n"
                    "## 总结报告（必须）\n"
                    "在回复末尾简要总结：\n"
                    "- 解决了哪些文件的冲突\n"
                    "- 是否执行了测试，测试结果如何（如 42 passed, 0 failed）\n"
                    "- 如果跳过了测试，说明原因\n"
                    "- 这个总结会显示在工作流的 timeline 中，供用户查看"
                )

                wf = self.workflow
                # Track this as its own milestone so conflict-resolution usage is
                # captured in phase_* (and thus workflow totals = SUM(phase_*)).
                conflict_ms = self._create_milestone(
                    phase="merge",
                    dev_round=wf.get("dev_round", 1),
                    milestone_type="conflicts_resolved",
                    status="in_progress",
                    title=f"Resolving merge conflicts (PR #{pr_number})",
                )
                result = self._run_agent(
                    wf=wf,
                    workflow_id=self._workflow_id,
                    cli_tool=wf.get("cli_tool", "claude-code"),
                    model=wf.get("model", ""),
                    project_path=temp_wt_path,
                    prompt=conflict_prompt,
                    workspace_type=wf.get("workspace_type", "local"),
                    remote_machine_id=wf.get("remote_machine_id"),
                    permission_mode=wf.get("permission_mode", "auto-edit"),
                    allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(
                        wf.get("cli_tool", "claude-code"), []
                    ),
                    session_line="fresh",
                    milestone_id=conflict_ms.get("milestone_id", ""),
                )
                self._accumulate_tokens(result)
                response_text = self._artifact_text(result)
                self.repo.update_milestone(
                    conflict_ms.get("milestone_id", ""),
                    {
                        "status": "completed" if result.success else "failed",
                        "session_id": result.session_id,
                        "error_message": result.error or "",
                        "result_summary": response_text,
                        "tldr": self._artifact_tldr(result),
                    },
                )

                if not result.success:
                    raise RuntimeError(f"Conflict resolution failed: {result.error}")

            # Push the resolved branch. The new merge commit triggers a fresh
            # CI run, so we do NOT merge here — _do_merge will retry on the
            # next scheduler cycle once CI passes (it checks for pending CI
            # at the top and defers until checks are green).
            wt_gh.git_push(branch=branch_name)
            self._create_milestone(
                phase="merge",
                milestone_type="conflicts_pushed",
                status="completed",
                title=f"PR #{pr_number} conflicts resolved, waiting for CI to merge",
            )
        finally:
            # Always tear down the temp worktree, even on failure, so it does
            # not leak and block future runs. Use the main repo's gh because
            # a worktree cannot remove itself.
            try:
                main_gh.remove_worktree(temp_wt_path)
                logger.info("Removed temporary merge worktree at %s", temp_wt_path)
            except GitHubOpsError as e:
                logger.warning("Failed to remove temp worktree %s: %s", temp_wt_path, e)
