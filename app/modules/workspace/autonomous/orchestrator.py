# mypy: disable-error-code="assignment,arg-type,union-attr,return-value,no-any-return"
"""
Open ACE - Autonomous Orchestrator

State machine that drives a single autonomous development workflow
through its phases: preparation -> planning -> development ->
pr_review -> report -> wait -> (loop or merge).
"""

import grp
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import only under TYPE_CHECKING to avoid a runtime cycle: the git_workspace
    # module imports orchestrator at module top, so a runtime import here would
    # recurse. The string annotation below resolves via this name under mypy.
    from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService

from app.modules.workspace.autonomous.agent_runner import (
    DEFAULT_TASK_TIMEOUT,
    AutonomousAgentRunner,
)
from app.modules.workspace.autonomous.artifact_text import (
    clean_agent_text,
    pick_best_artifact_text,
    sanitize_artifact_text,
)
from app.modules.workspace.autonomous.command_evidence.recorder import emit_command_evidence
from app.modules.workspace.autonomous.command_evidence.scope import (  # noqa: E402,F401; Re-imported for the legacy heuristic (_has_passing_test_tool_result) and; any in-module callers; the structured verdict (test_verdict) imports them; directly from scope to avoid a circular import (#2046 Phase B).
    _normalize_test_command,
    _pytest_scope_covers,
    _pytest_test_scope,
    _PytestScope,
)
from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict

# Re-exported here for backward compatibility: tests and other modules import
# these symbols from ``orchestrator`` (they lived here before T11 moved them to
# constants.py to break the circular import with phases/*.py). The
# ``noqa: F401`` keeps the unused-in-orchestrator names exported even though
# the migrated phase bodies no longer reference them here.
from app.modules.workspace.autonomous.constants import (  # noqa: F401
    _REVIEW_APPROVAL_PHRASES,
    _TRANSIENT_ORCHESTRATOR_KEYWORDS,
    AUTONOMOUS_CONTEXT,
    AUTONOMOUS_DEV_ALLOWED_TOOLS,
    MERGE_POLICY_PAUSE_REASON_PREFIX,
    READ_ONLY_REVIEW_UNSUPPORTED_TOOLS,
    REVIEW_ALLOWED_TOOLS,
    _extract_pr_number_from_error,
    _is_transient_git_error,
    _merge_milestone_metadata,
    _parse_metadata,
    _review_approval_phrase,
    _zcode_planning_mode,
)
from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.evidence_service import EvidenceService
from app.modules.workspace.autonomous.github_ops import _FAILURE_LINE_RE, GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps
from app.modules.workspace.autonomous.phases import resolve_phase_handler
from app.modules.workspace.autonomous.progress_report_i18n import (
    build_progress_payload,
    render_progress_report,
)
from app.repositories.autonomous_repo import DEFAULT_CONTENT_LANGUAGE, AutonomousWorkflowRepository
from app.repositories.database import Database
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

UPSTREAM_QUOTA_PAUSE_REASON_PREFIX = "Upstream provider quota exhausted:"


class WorkflowPaused(RuntimeError):
    """Control-flow signal for a persisted workflow pause."""


class UpstreamQuotaPaused(WorkflowPaused):
    """Control-flow signal used after persisting a hard-quota pause."""


class _ReconcileFailed(Exception):
    """Internal signal that worktree transition reconcile must fail closed.

    Raised from the reconcile helper body when git registry/disk state is
    ambiguous or ownership cannot be proven. The caller catches it and writes
    ``recovery_failed`` + a usable message (#2050).
    """


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

# ── Framework inference for test detection (Phase 1, P0) ────────────────


def _infer_test_framework(project_path: str, cli_tool: str) -> str:
    """Infer test framework type from project structure and CLI tool.

    Returns: "python", "javascript", "go", "mixed", or "unknown"
    """
    if not project_path:
        return "unknown"

    import os

    # Check for JavaScript/Node project files
    js_markers = ["package.json", "jest.config.js", "jest.config.ts", "vitest.config.ts"]
    # Check for Python project files
    py_markers = ["requirements.txt", "setup.py", "pyproject.toml", "pytest.ini", "tox.ini"]
    # Check for Go project files
    go_markers = ["go.mod", "go.sum"]
    # Check for Rust project files
    rust_markers = ["Cargo.toml"]
    # Check for Java project files
    java_markers = ["pom.xml", "build.gradle", "build.gradle.kts"]

    detected_frameworks = []

    # Scan for marker files (limit to 3 levels deep to avoid slow scans)
    for root, dirs, files in os.walk(project_path):
        # Limit depth
        depth = root[len(project_path) :].count(os.sep)
        if depth > 2:
            dirs[:] = []  # Don't recurse further
            continue

        # Skip common non-source directories
        dirs[:] = [
            d
            for d in dirs
            if d not in ("node_modules", "venv", ".venv", "__pycache__", ".git", "dist", "build")
        ]

        for f in files:
            if f in js_markers:
                detected_frameworks.append("javascript")
            elif f in py_markers:
                detected_frameworks.append("python")
            elif f in go_markers:
                detected_frameworks.append("go")
            elif f in rust_markers:
                detected_frameworks.append("rust")
            elif f in java_markers:
                detected_frameworks.append("java")

    # Deduplicate and determine result
    unique_frameworks = list(set(detected_frameworks))

    if len(unique_frameworks) == 1:
        return unique_frameworks[0]
    elif len(unique_frameworks) > 1:
        return "mixed"
    else:
        # Fallback: cannot infer from files, use "unknown" (disable keyword fallback)
        return "unknown"


def _has_strict_keyword_result(test_response_text: str, has_hallucination_desc: bool) -> bool:
    """Strict keyword detection with multiple conditions (Phase 1, P0).

    Requires stronger evidence than single keyword match:
    - Condition A: dual keywords (passed + PASSED, etc.)
    - Condition B: keyword + timestamp ("in X.XXs")
    - Condition C: keyword + file count ("X tests", "X files")
    - Condition D: keyword + error details (AssertionError, expected)

    Returns False if hallucination detected.
    """
    if has_hallucination_desc:
        return False

    import re

    # Condition A: Dual keyword combination (Issue #1538: extended to multilingual)
    dual_keywords = [
        # English
        ("passed", "PASSED"),
        ("failed", "FAILED"),
        ("passed", "failures"),
        ("failed", "failures"),
        # Chinese
        ("通过", "成功"),
        ("失败", "错误"),
        # Japanese
        ("通過", "成功"),
        ("失敗", "エラー"),
        # Korean
        ("통과", "성공"),
        ("실패", "오류"),
    ]
    has_dual = any(
        kw1 in test_response_text.lower() and kw2 in test_response_text
        for kw1, kw2 in dual_keywords
    )

    # Condition B: Keyword + timestamp pattern (Issue #1538: multilingual timestamps)
    timestamp_patterns = [
        r"in\s+[\d.]+s",  # English: "in 2.5s"
        r"用时\s*[\d.]+\s*秒",  # Chinese: "用时2.5秒"
        r"(所要時間|時間)\s*[\d.]+\s*秒",  # Japanese
        r"소요\s*시간\s*[\d.]+\s*초",  # Korean
    ]
    has_timestamp_keyword = bool(
        (
            "passed" in test_response_text.lower()
            or "通过" in test_response_text
            or "通過" in test_response_text
            or "통과" in test_response_text
        )
        and any(re.search(p, test_response_text) for p in timestamp_patterns)
    ) or bool(
        ("completed" in test_response_text.lower() or "完成" in test_response_text)
        and any(re.search(p, test_response_text) for p in timestamp_patterns)
    )

    # Condition C: Keyword + file/test count
    count_pattern = r"\d+\s+(tests?|files?|specs?|个|件|개)"
    has_count_keyword = bool(
        (
            "passed" in test_response_text.lower()
            or "通过" in test_response_text
            or "通過" in test_response_text
            or "통과" in test_response_text
        )
        and re.search(count_pattern, test_response_text)
    ) or bool(
        (
            "failed" in test_response_text.lower()
            or "失败" in test_response_text
            or "失敗" in test_response_text
            or "실패" in test_response_text
        )
        and re.search(count_pattern, test_response_text)
    )

    # Condition D: Keyword + error details
    has_error_details = (
        "failed" in test_response_text.lower()
        or "失败" in test_response_text
        or "失敗" in test_response_text
        or "실패" in test_response_text
    ) and (
        "AssertionError" in test_response_text
        or "expected" in test_response_text.lower()
        or "Traceback" in test_response_text
    )

    return has_dual or has_timestamp_keyword or has_count_keyword or has_error_details


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


def _has_test_tool_call(tool_calls: list, framework_type: str) -> bool:
    """Check if tool_calls contains a test framework execution command.

    This detects actual test execution by examining tool call commands,
    complementing pytest output detection. Used to prevent false-negative
    "Tests were not actually run" judgments when agent runs tests but
    output is not captured in visible text (Issue #1532).
    """
    if not tool_calls:
        return False

    test_commands = {
        "python": ["pytest", "python -m pytest", "-m pytest", "unittest", "tox", "nox"],
        "javascript": ["jest", "npm test", "npm run test", "yarn test", "vitest", "mocha"],
        "go": ["go test", "gotestsum"],
        "rust": ["cargo test", "cargo t"],
        "java": ["mvn test", "gradle test", "./gradlew test"],
    }
    # P1: generic_patterns includes unittest for unknown frameworks
    generic_patterns = ["pytest", "unittest", "jest", "go test", "cargo test", "npm test"]
    patterns_to_check = test_commands.get(framework_type, generic_patterns)
    # Note: -v (verbose) is NOT excluded, only --help/--version/-h
    exclude_flags = ["--help", "--version", "-h"]

    for tc in tool_calls:
        tool_name = tc.get("tool", {}).get("name", "") if isinstance(tc.get("tool"), dict) else ""
        # P0: Ensure tool_input is dict (handle None case)
        tool_input = (
            (tc.get("tool", {}).get("input", {}) or {}) if isinstance(tc.get("tool"), dict) else {}
        )

        # P2: Check non-Bash test tools first (pytest, run_tests, test)
        if tool_name in ("pytest", "run_tests", "test"):
            return True

        # Then check Bash/run_shell_command for test commands
        if tool_name in ("Bash", "Shell", "run_shell_command", "exec_command"):
            cmd = tool_input.get("command") or tool_input.get("cmd") or ""
            if not cmd or any(flag in cmd for flag in exclude_flags):
                continue
            for pattern in patterns_to_check:
                if pattern in cmd:
                    return True

    return False


def _has_passing_test_tool_result(event_log: list, framework_type: str) -> bool:
    """Require conclusive success for every distinct test command in this run.

    A later successful retry can supersede an earlier failure only when it
    reruns the same normalized command or a safely comparable pytest superset.
    Every new invocation resets its command to pending, so stale success from
    before a code change cannot satisfy an interrupted rerun.

    #2046 Phase B — NON-AUTHORITATIVE FALLBACK: the gate now drives
    PASSED/FAILED from ``compute_run_verdict`` over ``TestExecutionEvidence``.
    This heuristic is retained only as the INCONCLUSIVE/NOT_RUN fallback (when
    ``structured_verdict`` deferred). Do not extend it as a source of truth;
    new frameworks belong in ``test_evidence.parse_test_evidence``.
    """
    _CommandInfo = tuple[str, _PytestScope | None]
    _Invocation = tuple[_CommandInfo, int]

    test_tools: dict[str, _Invocation] = {}
    states: dict[str, bool] = {}
    scopes: dict[str, _PytestScope | None] = {}
    state_orders: dict[str, int] = {}
    latest_invocation_orders: dict[str, int] = {}
    expected_commands: set[str] = set()
    anonymous_pending: _Invocation | None = None
    anonymous_has_pending = False
    anonymous_evidence_ambiguous = False

    def _command_info(event: dict) -> _CommandInfo | None:
        as_tool_call = {
            "tool": {
                "name": event.get("tool_name", ""),
                "input": event.get("tool_input", {}),
            }
        }
        if not _has_test_tool_call([as_tool_call], framework_type):
            return None
        tool_input = event.get("tool_input") or {}
        command = ""
        if isinstance(tool_input, dict):
            command = str(
                tool_input.get("command") or tool_input.get("cmd") or tool_input.get("args") or ""
            )
        command_key = _normalize_test_command(command) or f"tool:{event.get('tool_name', '')}"
        scope = _pytest_test_scope(command) if framework_type == "python" else None
        return command_key, scope

    def _record_result(invocation: _Invocation, event: dict, event_order: int) -> None:
        command_info, invocation_order = invocation
        command_key, scope = command_info
        if latest_invocation_orders.get(command_key) != invocation_order:
            # A newer invocation of this command is pending or has completed;
            # an out-of-order result from an older call must not replace it.
            return
        text = str(event.get("text") or "")
        exit_code = event.get("exit_code")
        explicit_error = bool(event.get("is_error")) or (
            isinstance(exit_code, int) and exit_code != 0
        )
        # Positive-number failure summaries are failures; zero-valued summaries
        # ("42 passed, 0 failed", Maven "Failures: 0") are not.
        has_failure = any(
            re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            for pattern in (
                r"\b[1-9]\d*\s+failed\b",
                r"(?:^|\n)FAILED(?:\s|$)",
                r"\btest result:\s*FAILED\b",
                r"\bFailures?:\s*[1-9]\d*\b",
                r"\bErrors?:\s*[1-9]\d*\b",
                r"\bBUILD FAILURE\b",
            )
        )
        has_pass = any(
            re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            for pattern in (
                r"\b[1-9]\d*\s+passed\b",
                r"\btest result:\s*ok\b",
                r"^ok\s+\S+(?:\s+\S+)?$",  # Go test
                r"\bRan\s+[1-9]\d*\s+tests?\b[\s\S]*?^OK\s*$",  # unittest
                r"\bTests run:\s*[1-9]\d*\b[\s\S]*?\bFailures:\s*0\b[\s\S]*?\bErrors:\s*0\b",
                r"\bBUILD SUCCESS(?:FUL)?\b",  # Maven / Gradle test command
            )
        )
        states[command_key] = not explicit_error and not has_failure and has_pass
        scopes[command_key] = scope
        state_orders[command_key] = event_order

    for event_order, event in enumerate(event_log or []):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "tool_use":
            tool_id = str(event.get("tool_use_id") or "")
            command_info = _command_info(event)
            if command_info is not None:
                command_key, scope = command_info
                expected_commands.add(command_key)
                scopes[command_key] = scope
                states[command_key] = False
                state_orders[command_key] = event_order
                latest_invocation_orders[command_key] = event_order
                invocation = (command_info, event_order)
            else:
                invocation = None
            if tool_id:
                if invocation is not None:
                    test_tools[tool_id] = invocation
                continue
            if anonymous_has_pending:
                # Without IDs, overlapping calls cannot be paired safely.  Do
                # not trust any anonymous result in this event stream.
                anonymous_evidence_ambiguous = True
            anonymous_pending = invocation
            anonymous_has_pending = True
            continue
        if event_type != "tool_result":
            continue

        tool_id = str(event.get("tool_use_id") or "")
        if tool_id:
            invocation = test_tools.get(tool_id)
            if not invocation:
                continue
        else:
            if not anonymous_has_pending:
                continue
            invocation = anonymous_pending
            anonymous_pending = None
            anonymous_has_pending = False
            if anonymous_evidence_ambiguous or invocation is None:
                continue
        _record_result(invocation, event, event_order)

    if not expected_commands:
        return False

    passing_commands = [
        (command_key, scopes.get(command_key), state_orders.get(command_key, -1))
        for command_key, passed in states.items()
        if passed
    ]
    for command_key in expected_commands:
        if states.get(command_key) is True:
            continue
        earlier_scope = scopes.get(command_key)
        earlier_order = state_orders.get(command_key, -1)
        if framework_type != "python" or not any(
            passing_order > earlier_order and _pytest_scope_covers(passing_scope, earlier_scope)
            for _passing_key, passing_scope, passing_order in passing_commands
        ):
            return False
    return True


# Prefix added to all prompts to inform the agent it is running autonomously.
# Moved to constants.py (shared with phases/*.py to avoid a circular import);
# re-imported above as AUTONOMOUS_CONTEXT.

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

# Maximum time (seconds) for a single planning agent call.
# ZCode+GLM planning involves multiple model round-trips and subagent spawns
# that can take 10-15 min for complex issues. 600s was too tight — planning
# succeeded only ~30% of the time. 1800s gives the agent enough headroom
# while still capping runaway agents.
PLANNING_TIMEOUT = 1800


# _zcode_planning_mode moved to constants.py (shared with phases/pr_review.py);
# re-imported above.


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

# The only legitimate current_phase values a PhaseResult(completed) may carry in
# workflow_patch when next_phase="completed" (the terminal pseudo-phase). "merge"
# is the default terminal real phase (a completed workflow's current_phase stays
# "merge"); "completed" is pr_review's literal-terminal legacy path (it skips
# report/merge and writes the literal "completed"). Any other patch-carried
# current_phase would strand the workflow in a non-canonical phase — rejected by
# _commit_phase_result to mirror the top-level next_phase validation. (#2044 S2)
_COMPLETED_TERMINAL_PHASES = ("merge", "completed")

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

# _TRANSIENT_ORCHESTRATOR_KEYWORDS + _is_transient_git_error moved to
# constants.py (shared with phases/pr_review.py); re-imported above.


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


def build_language_instruction(content_language: str | None) -> str:
    """Build the per-content_language directive + TL;DR format instruction.

    Returned string is appended to every agent prompt so AI-authored content is
    generated in the workflow's ``content_language``. Unknown/missing languages
    fall back to English. The language-neutral ``TL;DR:`` marker is always
    present so ``_extract_tldr`` works across all languages.
    """
    lang = content_language if content_language in _LANGUAGE_DIRECTIVES else "en"
    return _LANGUAGE_DIRECTIVES[lang] + _TLDR_FORMAT_INSTRUCTIONS[lang]


_TLDR_RE = re.compile(r"TL;DR:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# _REVIEW_APPROVAL_PHRASES + _review_approval_phrase moved to constants.py
# (shared with phases/pr_review.py); re-imported above.

_REVIEW_RESULT_LINE_RE = re.compile(r"^REVIEW_RESULT\s*:\s*(\{.*\})$")


def _parse_review_result(review_text: str) -> dict | None:
    """Parse the final language-neutral PR review result, failing closed.

    Natural-language severity inference is intentionally avoided: punctuation,
    negation scope, and translated findings made it possible for a resolved P0
    clause to hide an unresolved P1 clause.  The reviewer must instead provide
    one single-line JSON result whose verdict agrees with its blocker list.
    """
    lines = (review_text or "").splitlines()
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return None

    # The result must be the final non-summary line.  A single TL;DR line may
    # follow because build_language_instruction() asks every phase for it.
    last_index = nonempty[-1]
    candidate_index = last_index
    if re.match(r"^TL;DR\s*:", lines[last_index].strip(), re.IGNORECASE):
        if len(nonempty) < 2:
            return None
        candidate_index = nonempty[-2]

    result_line_indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip().upper().startswith("REVIEW_RESULT")
    ]
    if result_line_indexes != [candidate_index]:
        return None

    # A result shown as a Markdown example is not authoritative.  Track fence
    # state before the candidate; an unclosed or enclosing fence fails closed.
    in_fence = False
    fence_marker = ""
    for line in lines[:candidate_index]:
        stripped = line.strip()
        marker = (
            "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else ""
        )
        if not marker:
            continue
        if not in_fence:
            in_fence = True
            fence_marker = marker
        elif marker == fence_marker:
            in_fence = False
            fence_marker = ""
    if in_fence:
        return None

    match = _REVIEW_RESULT_LINE_RE.fullmatch(lines[candidate_index].strip())
    if not match:
        return None
    try:
        result = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(result, dict):
        return None
    verdict = result.get("verdict")
    blockers = result.get("blocking_findings")
    if verdict not in {"APPROVE", "REQUEST_CHANGES"} or not isinstance(blockers, list):
        return None
    if not all(isinstance(item, str) and item.strip() for item in blockers):
        return None
    return {"verdict": verdict, "blocking_findings": blockers}


# _extract_pr_number_from_error / _parse_metadata / _merge_milestone_metadata
# moved to constants.py (shared with phases/pr_review.py); re-imported above.


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


def _github_truncation_notice(content_language: str | None) -> str:
    return _GITHUB_TRUNCATION_NOTICES.get(
        content_language, _GITHUB_TRUNCATION_NOTICES[DEFAULT_CONTENT_LANGUAGE]
    )


REVIEW_SESSION_MILESTONE_TYPES = {"plan_reviewed", "pr_reviewed"}

# Session lines that span multiple milestones via --resume. Each maps to a
# workflow column holding one stable tracking id; the provider/CLI resume id is
# stored on that agent_sessions row in ``cli_session_id``.
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
    # Claude may wrap the status later in the same short envelope:
    # ``API Error: Request rejected (429)``.
    r"|api\s*error[^\n]{0,80}\(\s*(?:429|5\d{2})\s*\)"
    # Bare "429" must have context that distinguishes a real API error from a
    # plan discussing HTTP status codes (e.g. "return 429" in a rate-limit
    # design). Require "status" or "error" nearby, not just the number alone.
    r"|status\s*(?:code)?\s*[:：]\s*429"
    r"|error\s*(?:code)?\s*[:：]\s*429"
    r"|429\s*too\s+many\s+requests"
    r"|quota\s+exceeded|rate[\s-]?limit(?:ed)?|too\s+many\s+requests"
    r"|overloaded"  # "The service may be temporarily overloaded"
    r"|bad\s+gateway|service\s+unavailable|gateway\s+timeout|internal\s+server\s+error",
    re.IGNORECASE,
)

# Hard provider quota failures are distinct from Bailian Coding Plan's
# ``usage allocated quota exceeded`` wording, which is a temporary allocation
# rate limit and must remain on the transient retry path.
_UPSTREAM_HARD_QUOTA_EXHAUSTED_RE = re.compile(
    r"platform\s+quota\s+exceeded" r"|upstream\s+(?:provider\s+)?quota\s+(?:exceeded|exhausted)",
    re.IGNORECASE,
)

# Context-window / input-length overflow signatures. A PERMANENT client
# error (NOT transient — must not trigger the transient-retry backoff loop).
# Used by CI repair to detect a resumed-session-too-large failure and switch
# to a fresh minimal-context session on the next in-round retry. Kept broad
# enough to catch provider-specific phrasings:
#   GLM:      "Range of input length should be [1, 202752]"
#   OpenAI:   "maximum context length" / "too many input tokens"
#   Anthropic:"prompt is too long" / "context window"
_CONTEXT_OVERFLOW_RE = re.compile(
    r"range\s+of\s+input\s+length"
    r"|maximum\s+context\s+length"
    r"|context[_ ]?length\s*.{0,12}exceed"
    r"|prompt\s+is\s+too\s+long"
    r"|input\s+length\s+should\s+be"
    r"|context[_ ]?window\s+.{0,12}exceed"
    r"|too\s+many\s+input\s+tokens",
    re.IGNORECASE,
)

# Test failure retry configuration.
MAX_TEST_RETRIES = 2  # max retries when test agent itself fails
MAX_DEV_RETRIES_ON_TEST_FAIL = 2  # max dev round retries for unfixable test failures
MAX_CI_REPAIR_ATTEMPTS = 3  # max automatic dev-round retries for merge-phase CI failures
MAX_PRE_COMMIT_CONVERGENCE_PASSES = 3
PRE_COMMIT_CONVERGENCE_TIMEOUT = 600
CI_PRE_COMMIT_SKIP = "bandit,no-commit-to-branch"
MAX_CI_DIAGNOSTICS_ATTEMPTS = 6  # bound scheduler polls when failed job logs stay unavailable
MAX_CLEANUP_ATTEMPTS = 10  # post-merge Git cleanup retries before giving up (#2043)


def _cleanup_backoff_time(attempts: int) -> str:
    """ISO timestamp for the next cleanup retry; exponential backoff capped at 1h (#2043)."""
    from datetime import timedelta

    delay = min(60 * (2 ** max(attempts - 1, 0)), 3600)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")


try:
    MAX_AUTONOMOUS_CHANGED_FILES = int(os.environ.get("AUTONOMOUS_MAX_CHANGED_FILES", "60"))
except ValueError:
    logger.warning("Invalid AUTONOMOUS_MAX_CHANGED_FILES; using default 60")
    MAX_AUTONOMOUS_CHANGED_FILES = 60

# Sentinel digest used by _ci_failure_fingerprint when get_check_failure_excerpt
# returns empty (old gh CLI / token / REST-API URL-format issues). Deliberately
# contains non-hex chars so it can never collide with a real sha256[:12].
# The give-up guard in _start_ci_repair_round checks for this sentinel to skip
# the "unchanged signature" misfire — a name-only fingerprint has no
# discriminative power, so firing the guard would wrongly kill workflows whose
# real failure did change (#1855, #1856). Shared constant so producer
# (_ci_failure_fingerprint) and consumer (_start_ci_repair_round) can't drift.
NO_EXCERPT_SENTINEL = "<no-excerpt>"


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
        self._current_session_id: str | None = None
        self._session_lock = threading.Lock()
        # Usage consumed by earlier transient-error attempts in the same
        # milestone. Live milestone totals apply this runtime offset when the
        # stable main/review/test session starts its next provider request.
        self._session_usage_offsets: dict[str, dict[str, int]] = {}
        self._cancel_requested = threading.Event()  # in-memory cancel signal
        # Set only by the application shutdown path. Unlike a user stop, this
        # interrupts the current attempt without changing the workflow's active
        # phase/status, so the next process can retry it automatically.
        self._shutdown_requested = threading.Event()

        # Wire session_manager so agent sessions are persisted to DB
        from app.modules.workspace.session_manager import SessionManager

        session_manager = SessionManager()
        self._runner = AutonomousAgentRunner(
            session_manager=session_manager,
            activity_callback=self._on_agent_activity,
            on_pid_registered=self._on_pid_registered,
            on_pid_cleared=self._on_pid_cleared,
            on_sandbox_created=self._on_sandbox_created,
        )
        self._gh: GitHubOps | None = None

    @property
    def _sandbox_provider(self):
        """The default SandboxProvider the runner spawns local tasks through (#2044 T13).

        ``AutonomousAgentRunner`` owns the per-task provider selection
        (``_select_sandbox_provider`` branches on workspace_type: local → the
        ``LegacyPosixProvider`` injected at construction, remote → a fresh
        ``RemoteMachineProvider``). Phase handlers that need a provider handle
        to introspect (capabilities, build a spec, etc.) receive this default
        via ``PhaseDeps.sandbox``. It is the same instance the runner uses for
        local spawns, so handler-built specs and the runner's spawn path agree
        on the declared capabilities. Per-task remote providers are NOT visible
        here on purpose — they are minted from ``remote_session_manager`` at
        run time and are not a stable orchestrator-level dependency.

        Tests that bypass ``__init__`` (``__new__`` + stubbed runner) get a
        ``LegacyPosixProvider`` fallback. This is a data-descriptor property,
        so a test cannot shadow it by stuffing ``self._sandbox_provider =
        ...`` into ``__dict__`` (assignment would invoke this getter's
        ``__set__`` and raise ``AttributeError`` — there is no setter). To
        override in a test, either (a) inject ``runner._sandbox_provider`` and
        let the getter read it, or (b) ``del type(o)._sandbox_provider`` to
        remove the property before assigning the instance attribute.
        """
        runner_provider = getattr(self._runner, "_sandbox_provider", None)
        if runner_provider is not None:
            return runner_provider
        from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider

        return LegacyPosixProvider()

    @property
    def _git_workspace(self) -> "GitWorkspaceService":
        """Lazily attach the git-workspace service (#2044 Phase B T5).

        Mirrors ``_evidence``: unit tests that build the orchestrator via
        ``__new__`` (bypassing ``__init__``) and stub individual methods still
        get a service instance on first wrapper call instead of an
        ``AttributeError``. A test that supplies its own ``_git_workspace`` mock
        takes precedence via ``__dict__``.
        """
        cached = self.__dict__.get("_git_workspace_instance")
        if cached is None:
            from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService

            cached = GitWorkspaceService(self)
            self.__dict__["_git_workspace_instance"] = cached
        return cached

    @property
    def _evidence(self) -> EvidenceService:
        """Lazily attach the verify-before-act evidence service (issue #2045).

        Some unit tests construct the orchestrator via ``__new__`` (bypassing
        ``__init__``) and stub individual methods; this property keeps those
        tests working without each needing to set ``self._evidence`` explicitly.
        """
        cached = self.__dict__.get("_evidence_instance")
        if cached is None:
            cached = EvidenceService()
            self.__dict__["_evidence_instance"] = cached
        return cached

    @property
    def workflow(self) -> dict | None:
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

        Issue #1573: Added branch verification after binding to worktree.
        If the actual branch doesn't match expected branch_name, log warning
        and attempt to checkout to the correct branch.
        """
        wf = self.workflow
        if not self._gh and wf:
            # Reconcile an interrupted transition before binding, so we never
            # bind GitHubOps to the main repo (HEAD=main) while a worktree
            # transition is mid-flight (#2050). recovery_failed also short-
            # circuits: nothing should run against git in that state.
            ts = wf.get("worktree_transition_state")
            if ts and ts != "recovery_failed":
                self._reconcile_worktree_transition(wf)
                wf = self.workflow
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
            path_check_result = "skipped"
            if worktree_path:
                probe = GitHubOps(project_path, system_account=system_account)
                path_check_passed = probe.path_exists_as_user(worktree_path, dir_only=True)
                path_check_result = "passed" if path_check_passed else "failed"
                if path_check_passed:
                    chosen = worktree_path
            self._gh = GitHubOps(chosen, system_account=system_account)
            # Issue #1736: Log binding decision for observability
            logger.info(
                "_get_gh binding: workflow=%s chosen=%s worktree=%s project=%s path_check=%s",
                self._workflow_id[:8],
                "worktree" if chosen == worktree_path else "main_repo",
                worktree_path,
                project_path,
                path_check_result,
            )

            # Issue #1573: Verify branch consistency after binding to worktree
            if chosen != project_path and worktree_path:  # Bound to worktree, not main repo
                expected_branch = wf.get("branch_name", "")
                if expected_branch:
                    try:
                        actual_branch = self._gh.get_current_branch()
                        if actual_branch != expected_branch:
                            logger.warning(
                                "_get_gh: Branch mismatch for workflow %s: expected=%s, actual=%s",
                                self._workflow_id[:8],
                                expected_branch,
                                actual_branch,
                            )
                            # Attempt to checkout to the correct branch
                            try:
                                self._gh.checkout(expected_branch)
                                logger.info(
                                    "_get_gh: Successfully checked out to branch %s",
                                    expected_branch,
                                )
                            except GitHubOpsError as e:
                                logger.error(
                                    "_get_gh: Failed to checkout to branch %s: %s",
                                    expected_branch,
                                    e,
                                )
                                # Clear cache so next call re-evaluates
                                self._gh = None
                    except GitHubOpsError as e:
                        logger.warning("_get_gh: Branch verification failed: %s", e)
        return self._gh

    @staticmethod
    def _resolve_system_account(wf: dict | None) -> str | None:
        """Resolve the workflow owner's system account, if any."""
        user_id = (wf or {}).get("user_id")
        if not user_id:
            return None
        user = UserRepository().get_user_by_id(user_id)
        return user.get("system_account") if user else None

    @staticmethod
    def _resolve_isolated_agent_account() -> str:
        """Return the credentialless OS principal used for local AI agents.

        The account is intentionally distinct from both the service principal
        and repository owner.  Installers provision it without sudo or GitHub
        credentials; ``openace-run-as --isolated`` grants only scoped worktree
        ACLs and a clean allow-listed environment.
        """
        configured = os.environ.get("OPENACE_AUTONOMOUS_AGENT_ACCOUNT", "").strip()
        if not configured:
            try:
                from app.utils.config import get_config_value

                configured = str(
                    get_config_value("autonomous", "agent_system_account", "openace-agent") or ""
                ).strip()
            except Exception:
                configured = "openace-agent"
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", configured):
            raise RuntimeError("Invalid autonomous.agent_system_account configuration")
        try:
            account = pwd.getpwnam(configured)
        except KeyError:
            # The root-owned launcher performs the authoritative account check;
            # keeping the name here lets installations surface its clear error
            # when provisioning was skipped.
            return configured
        if account.pw_uid == 0:
            raise RuntimeError("Autonomous agent account must never have UID 0")
        if account.pw_uid == os.getuid():
            raise RuntimeError(
                "Autonomous agent account must differ from the Open ACE service account"
            )
        group_ids = set(os.getgrouplist(configured, account.pw_gid))
        group_names = set()
        for group_id in group_ids:
            try:
                group_names.add(grp.getgrgid(group_id).gr_name)
            except KeyError:
                continue
        if 0 in group_ids or group_names.intersection({"root", "wheel", "sudo", "admin"}):
            raise RuntimeError("Autonomous agent account must not belong to an admin group")
        return configured

    def _resolve_effective_repo_context(self, wf: dict | None) -> dict[str, str]:
        """Resolve the authoritative repo path + branch for this workflow."""
        workflow = wf or self.workflow or {}
        strategy = (workflow.get("branch_strategy") or "new-branch").strip() or "new-branch"
        project_path = (workflow.get("project_path") or "").strip()
        worktree_path = (workflow.get("worktree_path") or "").strip()
        repo_path = project_path
        if strategy == "worktree" and worktree_path:
            repo_path = os.path.realpath(worktree_path)
        return {
            "strategy": strategy,
            "project_path": project_path,
            "worktree_path": worktree_path,
            "repo_path": repo_path,
            "expected_branch": (workflow.get("branch_name") or "").strip(),
        }

    def _build_repo_execution_contract(self, wf: dict | None) -> str:
        """Prompt contract that keeps all agent actions bound to one repo/branch."""
        ctx = self._resolve_effective_repo_context(wf)
        repo_path = ctx.get("repo_path", "")
        if not repo_path:
            return ""
        branch = ctx.get("expected_branch", "")
        lines = [
            "",
            "## 仓库执行约束",
            f"- 唯一允许操作的 Git 仓库路径：`{repo_path}`",
        ]
        if branch:
            lines.append(f"- 必须停留在分支：`{branch}`")
        lines.extend(
            [
                "- 禁止 `cd` 到其他 Git 仓库，禁止在其他仓库执行 `git`、测试、构建或提交命令",
                "- 如果需要运行 shell 命令，请始终以该路径作为当前目录",
            ]
        )
        return "\n".join(lines) + "\n"

    def _capture_repo_state(self, repo_path: str, system_account: str | None) -> dict[str, str]:
        """Capture the repo root, branch, and HEAD for post-run validation."""
        gh = GitHubOps(repo_path, system_account=system_account)
        top_level = gh._run_git(["rev-parse", "--show-toplevel"]).stdout.strip()
        branch = gh.get_current_branch()
        head = gh.get_current_commit()
        origin = gh._run_git(["remote", "get-url", "origin"], check=False).stdout.strip()
        git_dir = gh._run_git(["rev-parse", "--absolute-git-dir"]).stdout.strip()
        common_dir = gh._run_git(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        ).stdout.strip()
        git_identity = gh.get_path_identity(git_dir)
        common_identity = gh.get_path_identity(common_dir)
        if not isinstance(top_level, str) or not top_level.startswith(os.sep):
            raise RuntimeError(f"unsupported repo root probe: {top_level!r}")
        if not isinstance(branch, str) or not isinstance(head, str):
            raise RuntimeError("unsupported git state probe")
        return {
            "repo_path": os.path.realpath(repo_path),
            "top_level": os.path.realpath(top_level) if top_level else "",
            "branch": branch,
            "head": head,
            "origin": origin,
            "git_dir": os.path.realpath(git_dir),
            "common_dir": os.path.realpath(common_dir),
            "git_identity": git_identity,
            "common_identity": common_identity,
        }

    def _ancestor_check(self, gh: "GitHubOps", a: str, b: str) -> bool | None:
        """Return True if ``a`` is an ancestor of ``b``, False if not, None on
        a git error.

        ``git merge-base --is-ancestor`` exits 0 (yes), 1 (no), or 128+ (git
        error such as a missing object). The 1-vs-error distinction matters:
        a "no" is a definitive commit-graph answer we act on, a git error is
        an indeterminate probe that must fail closed (see
        ``_main_drift_is_benign_pull``).

        Issue #2045 Phase A: delegates to :class:`EvidenceService`, which returns
        a tri-state :class:`Evidence`. The bool|None return is preserved for
        existing callers (True=CONFIRMED, False=REJECTED, None=INDETERMINATE).
        """
        evidence = self._evidence.verify_branch_contains(gh, head=b, base=a)
        return evidence.verdict.to_bool_or_none()

    def _main_drift_is_benign_pull(
        self,
        repo_path: str,
        before: str,
        after: str,
        system_account: str | None,
    ) -> bool:
        """Return True if main HEAD moving ``before`` → ``after`` is a benign
        external ``git pull`` (rather than an agent escaping the worktree).

        Requires BOTH commit-graph conditions:
          1. ``before`` is an ancestor of ``after`` — main moved *forward*
             (rules out reset/rollback/history-rewrite).
          2. ``after`` is an ancestor of ``origin/main`` — the new HEAD is a
             remote-sourced commit (rules out a local, not-yet-pushed
             ``git commit``).

        What this detects, stated as commit-graph states rather than as claims
        about who ran which command: it blocks (a) a local un-pushed commit on
        main (after is not on origin/main) and (b) a non-fast-forward change
        (main did not move forward). It does NOT identify the operation's
        source: an agent running ``git pull`` / ``reset --hard origin/main`` /
        checking out a newer remote commit is graph-identical to an external
        pull (forward + on remote) and is likewise allowed. That boundary is
        inherent to a commit-graph check.

        Failure policy is fail-closed. Reaching this method already means main
        HEAD moved suspiciously during an agent run, so a probe that cannot
        produce a definitive answer (a git error on merge-base) defaults to
        "not proven benign" → block. ``fetch`` is best-effort (``check=False``):
        a network/auth failure does not short-circuit — we fall back to the
        existing origin/main ref, which a concurrent external pull has already
        updated, so the two conditions remain distinguishable.
        """
        if not before or not after or before == after:
            # Defensive: the sole caller (_validate_repo_context_after_run)
            # only reaches this method when before_main != after_main, so
            # before == after is currently unreachable. Kept so a future caller
            # gets the correct "no drift → benign" answer instead of a probe.
            return True
        gh = GitHubOps(repo_path, system_account=system_account)
        try:
            # Best-effort refresh of origin/main. A concurrent external pull
            # runs its own fetch and updates this ref, so even if this fetch
            # fails the local ref is fresh enough to tell pull from local commit.
            gh._run_git(["fetch", "origin", "main"], check=False)
            moved_forward = self._ancestor_check(gh, before, after)
            after_on_remote = self._ancestor_check(gh, after, "origin/main")
            # A git error on either probe → cannot prove benign → fail closed.
            if moved_forward is None or after_on_remote is None:
                logger.warning(
                    "Workflow %s: benign-pull probe indeterminate for main HEAD "
                    "%s..%s (git error); failing closed.",
                    self._workflow_id,
                    before[:8],
                    after[:8],
                )
                return False
            return moved_forward and after_on_remote
        except Exception as e:
            # Unexpected exception (not a merge-base exit code). main HEAD has
            # already moved suspiciously, so do not assume benign.
            logger.warning(
                "Workflow %s: benign-pull probe raised for main HEAD %s..%s: %s; failing closed.",
                self._workflow_id,
                before[:8],
                after[:8],
                e,
            )
            return False

    def _snapshot_repo_context(
        self, wf: dict | None, workspace_type: str, system_account: str | None
    ) -> dict | None:
        """Snapshot expected repo state before a local agent phase runs."""
        if workspace_type != "local":
            return None
        ctx = self._resolve_effective_repo_context(wf)
        repo_path = ctx.get("repo_path", "")
        if not repo_path:
            return None
        try:
            state = {
                "context": ctx,
                "effective": self._capture_repo_state(repo_path, system_account),
            }
            project_path = ctx.get("project_path", "")
            if (
                ctx.get("strategy") == "worktree"
                and project_path
                and os.path.realpath(project_path) != os.path.realpath(repo_path)
            ):
                state["main"] = self._capture_repo_state(project_path, system_account)
            return state
        except Exception as e:
            logger.warning("Skipping repo-state snapshot for workflow %s: %s", self._workflow_id, e)
            return None

    def _validate_repo_context_after_run(
        self, before_state: dict | None, system_account: str | None
    ) -> str:
        """Verify the agent stayed on the workflow's intended repo + branch."""
        if not before_state:
            return ""
        ctx = before_state.get("context", {}) or {}
        repo_path = ctx.get("repo_path", "")
        expected_branch = ctx.get("expected_branch", "")
        if not repo_path:
            return ""
        try:
            after_effective = self._capture_repo_state(repo_path, system_account)
        except Exception as e:
            return f"Failed to verify workflow repository state after agent run: {e}"

        expected_root = before_state.get("effective", {}).get("repo_path", "")
        actual_root = after_effective.get("top_level", "")
        if expected_root and actual_root and actual_root != expected_root:
            return (
                "Agent escaped the workflow repository: "
                f"expected repo root {expected_root}, actual {actual_root}"
            )
        if expected_branch and after_effective.get("branch") != expected_branch:
            return (
                "Agent changed the workflow branch unexpectedly: "
                f"expected {expected_branch}, actual {after_effective.get('branch', '')}"
            )
        before_origin = before_state.get("effective", {}).get("origin", "")
        after_origin = after_effective.get("origin", "")
        if before_origin != after_origin:
            return (
                "Agent changed the workflow repository origin unexpectedly: "
                f"expected {before_origin!r}, actual {after_origin!r}"
            )
        for metadata_key in ("git_dir", "common_dir"):
            expected_metadata = before_state.get("effective", {}).get(metadata_key, "")
            actual_metadata = after_effective.get(metadata_key, "")
            if expected_metadata != actual_metadata:
                return (
                    "Agent changed protected Git metadata unexpectedly: "
                    f"{metadata_key} expected {expected_metadata!r}, actual {actual_metadata!r}"
                )

        before_main = before_state.get("main")
        before_effective = before_state.get("effective", {})
        if before_main:
            try:
                after_main = self._capture_repo_state(ctx.get("project_path", ""), system_account)
            except Exception:
                after_main = {}
            if (
                before_main.get("head")
                and after_main.get("head")
                and before_main.get("head") != after_main.get("head")
                and before_main.get("branch") == after_main.get("branch")
                and before_effective.get("head") == after_effective.get("head")
            ):
                # main HEAD moved but the worktree did not. This is either an
                # agent operating on the main repo, or an external `git pull`
                # moving HEAD to a remote commit during the agent run. Allow
                # only when the move is a forward update to a remote-sourced
                # commit (a benign pull); a local escape commit (not pushed),
                # a reset/rollback, or a non-fast-forward rewrite is blocked.
                if self._main_drift_is_benign_pull(
                    ctx.get("project_path", ""),
                    before_main.get("head"),
                    after_main.get("head"),
                    system_account,
                ):
                    logger.info(
                        "Workflow %s: main repo HEAD moved %s..%s during agent run "
                        "(benign external pull); allowing.",
                        self._workflow_id,
                        before_main.get("head", "")[:8],
                        after_main.get("head", "")[:8],
                    )
                    return ""
                return (
                    "Detected commits on the main repository while the workflow worktree "
                    "HEAD did not move; the agent likely executed git commands outside "
                    "the workflow worktree."
                )
            after_main_branch = after_main.get("branch", "")
            if (
                before_main.get("head")
                and after_main.get("head")
                and before_main.get("head") != after_main.get("head")
                and before_main.get("branch") != after_main_branch
                and before_effective.get("head") == after_effective.get("head")
            ):
                # Main repo HEAD moved AND its checked-out branch changed
                # during the agent run. On a dev/single-user host this is the
                # common case: the developer checked out a feature branch to
                # work on a fix PR while a workflow was running against the
                # same repo, and the feature branch's local (unpushed) commits
                # would otherwise trip after_on_remote=False in the
                # benign-pull probe (#1831 false positive).
                #
                # Security tradeoff: this skip is ambiguous on same-user
                # deployments (macOS / dev installs without the
                # openace-run-as launcher). In same-user mode the agent has no
                # command guard and COULD theoretically `cd` to the main repo
                # and `git checkout <branch> && git commit`. The worktree-only
                # validation above (repo root / branch / origin / git_dir)
                # does NOT catch this — it only confirms the worktree was not
                # modified, not that the agent stayed inside the worktree.
                # Mitigations:
                #   - auto-dev/* branches are NOT skipped (see below), since
                #     those are workflow branches and a switch to one in the
                #     main repo is more likely an escape than developer work.
                #   - The branch change is logged at WARNING so operators on
                #     security-sensitive multi-user hosts can monitor it.
                #   - On multi-user Linux hosts with the launcher, the agent
                #     runs as a credentialless principal and CANNOT touch the
                #     main repo, so this tradeoff is dev-host-only.
                if after_main_branch.startswith("auto-dev/"):
                    # A switch to an auto-dev/* branch in the main repo is
                    # suspicious — workflow branches belong in worktrees, not
                    # the main checkout. Run the benign-pull probe; if it
                    # doesn't pass, block as a likely escape.
                    logger.warning(
                        "Workflow %s: main repo branch changed to %s (an auto-dev/* "
                        "workflow branch) during agent run; running drift check.",
                        self._workflow_id,
                        after_main_branch,
                    )
                    if self._main_drift_is_benign_pull(
                        ctx.get("project_path", ""),
                        before_main.get("head"),
                        after_main.get("head"),
                        system_account,
                    ):
                        return ""
                    return (
                        "Detected commits on the main repository while the workflow "
                        "worktree HEAD did not move; the main repo branch changed to "
                        f"{after_main_branch} (an auto-dev/* workflow branch), which "
                        "is not a developer action — the agent likely executed git "
                        "commands outside the workflow worktree."
                    )
                else:
                    logger.warning(
                        "Workflow %s: main repo branch changed %s..%s during agent "
                        "run (developer switched branches: %s -> %s); skipping "
                        "main-HEAD-drift check. NOTE: on same-user deployments this "
                        "skip cannot distinguish developer action from an agent "
                        "escape via branch switch — monitor this log on "
                        "security-sensitive hosts.",
                        self._workflow_id,
                        before_main.get("head", "")[:8],
                        after_main.get("head", "")[:8],
                        before_main.get("branch", ""),
                        after_main_branch,
                    )
                    return ""
        return ""

    def _ensure_worktree(self, wf: dict) -> str:
        return self._git_workspace.ensure_worktree(wf)

    def _fail_recovery_closed(
        self,
        wf: dict,
        canonical: str,
        decision: str,
        evidence_meta: dict,
        local_sha: str,
        expected_sha: str,
    ) -> None:
        return self._git_workspace.fail_recovery_closed(
            wf, canonical, decision, evidence_meta, local_sha, expected_sha
        )

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

    def _ci_failure_fingerprint(self, gh: GitHubOps, checks: list[dict]) -> str:
        """Fine-grained failure signature including the actual error lines.

        The coarse-grained approach (check name only) is too coarse for Actions
        jobs that bundle multiple tools (one ``lint`` job runs
        black/isort/ruff/mypy). When the agent fixes one sub-tool but another
        still fails, a name-only signature stays identical and the
        exhausted-unchanged guard wrongly gives up. This fingerprint appends a
        normalized hash of the per-check error excerpt so partial fixes change
        the signature and earn another repair attempt.
        """
        import hashlib

        parts = []
        for check in checks or []:
            if check.get("bucket") != "fail":
                continue
            name = str(check.get("name") or "").strip()
            if "failure_excerpt" in check:
                excerpt = str(check.get("failure_excerpt") or "")
            else:
                try:
                    excerpt = gh.get_check_failure_excerpt(check)
                except Exception:
                    excerpt = ""
            # Normalize: strip volatile bits (timestamps, absolute paths,
            # run/job ids) so the hash is stable across re-runs of the same
            # error but differs when the error set changes.
            normalized = self._normalize_failure_excerpt(excerpt)
            if normalized:
                digest = hashlib.sha256(normalized.encode()).hexdigest()[:12]
            else:
                # Excerpt unavailable (old gh CLI / token / URL-format issue).
                # Use a sentinel distinct from any real hash so the caller
                # knows the fingerprint is name-only and should NOT trigger the
                # "unchanged signature" give-up guard — that guard requires the
                # fingerprint to reflect actual error lines, and a name-only
                # fingerprint can't tell whether the agent's fix changed the
                # error set. Misfiring it would kill workflows (e.g. #1855)
                # whose real failure did change.
                digest = NO_EXCERPT_SENTINEL
            parts.append(f"{name}::{digest}")
        return "\n".join(sorted(parts))

    @staticmethod
    def _normalize_failure_excerpt(excerpt: str) -> str:
        """Reduce an excerpt to its error-bearing essence for fingerprinting.

        Keeps only lines that look like real errors (strips CI chrome, blank
        lines, hook banners) and normalizes file paths so the hash captures
        *which* errors remain, not their line numbers or local paths.
        """
        if not excerpt:
            return ""
        lines = []
        for raw in excerpt.splitlines():
            line = raw.strip()
            if not line:
                continue
            # Keep lines that carry an error marker; drop pre-commit banners
            # ("black....Passed", "mypy....Failed") which are too noisy.
            # Reuse the shared _FAILURE_LINE_RE from github_ops to avoid drift.
            if not _FAILURE_LINE_RE.search(line):
                continue
            # Normalize file paths: /abs/path/to/file.py:12:3 → file.py
            line = re.sub(
                r"(?<![\w/])[\w./-]+\.py(?=:\d+)", lambda m: m.group(0).split("/")[-1], line
            )
            # Drop line:col numbers (file.py:10:5 or file.py:99)
            line = re.sub(r":\d+(:\d+)?(?=:|\s|$)", "", line)
            lines.append(line)
        return "\n".join(lines)

    def _get_preferred_worktree_path(self, wf: dict) -> str:
        return self._git_workspace.get_preferred_worktree_path(wf)

    def _build_ci_repair_context(
        self, wf: dict, gh: GitHubOps, pr_number: int, failed_checks: list[dict]
    ) -> str:
        """Summarize failed CI checks and require evidence-driven local investigation."""
        lines = [
            f"PR #{pr_number} 在合并前检测到以下 CI 失败。",
            "请先根据失败日志复现问题，再修复；不能只跑“相关测试”就宣布已解决。",
            "如果本轮没有产生新的代码改动，就不能视为修复完成。",
            "请优先自行调查仓库中的 CI 定义与脚本来源，而不是依赖人工预先配置的规则。",
            "",
        ]
        for check in failed_checks or []:
            if check.get("bucket") != "fail":
                continue
            state = check.get("state") or "unknown"
            link = check.get("link") or ""
            name = check.get("name") or "unknown"
            lines.append(f"### {name}")
            lines.append(f"- 状态: {state}")
            if link:
                lines.append(f"- 链接: {link}")

            if "failure_excerpt" in check:
                excerpt = str(check.get("failure_excerpt") or "")
            else:
                try:
                    excerpt = gh.get_check_failure_excerpt(check)
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch CI failure excerpt for check '%s' in PR #%s: %s",
                        name,
                        pr_number,
                        exc,
                    )
                    excerpt = ""
            if excerpt:
                lines.append("- 失败摘录:")
                lines.append("```text")
                lines.append(excerpt)
                lines.append("```")
            lines.append("")
        lines.extend(
            [
                "调查要求：",
                "1. 先查看 `.github/workflows/`、`package.json`、`Makefile`、`tox.ini`、`pytest.ini`、`scripts/` 等，确认失败 check 在仓库中对应哪条本地命令或脚本。",
                "2. 优先用 CI 工作流里真实执行的命令在本地复现，而不是只挑选你认为“相关”的部分测试。",
                "3. 修复后重新运行对应命令，以及你新增/修改代码的相关测试。",
                "4. 只有在确实产生新代码提交后，才能宣布 CI 修复完成；如果没有代码改动，必须明确说明为什么 CI 会自动恢复。",
            ]
        )
        runtime_contract = self._project_runtime_contract(
            wf.get("worktree_path") or wf.get("project_path", ""), gh
        )
        if runtime_contract:
            lines.append(str(runtime_contract))
        return "\n".join(lines).strip()

    @staticmethod
    def _project_runtime_contract(project_path: str, gh: GitHubOps | None = None) -> str:
        """Describe the repository runtime without treating the service host
        interpreter as a compatibility target.

        Autonomous agents previously saw the Open ACE service's Python 3.9 and
        rewrote Python 3.10+ repositories across hundreds of files.  Surface
        the declared requirement and make the distinction explicit in every
        code-writing/test prompt.
        """
        if not project_path:
            return ""
        declared = ""
        pyproject = os.path.join(project_path, "pyproject.toml")
        pyproject_text = ""
        try:
            with open(pyproject, encoding="utf-8") as handle:
                pyproject_text = handle.read()
        except OSError:
            # Multi-user worktrees may be unreadable by the service account.
            # GitHubOps routes git through the workflow owner and can still
            # read the committed file without weakening filesystem isolation.
            if gh is not None:
                try:
                    result = gh._run_git(["show", "HEAD:pyproject.toml"], check=False)
                    if result.returncode == 0:
                        pyproject_text = result.stdout or ""
                except Exception:
                    pass
        match = re.search(
            r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']',
            pyproject_text,
            re.MULTILINE,
        )
        if match:
            declared = match.group(1).strip()
        if not declared:
            return ""
        host = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        return (
            "\n\n## 项目运行时契约\n"
            f"- 项目声明的 Python 版本：`{declared}`\n"
            f"- Open ACE 服务进程使用的 Python：`{host}`\n"
            "- 服务进程版本不代表目标项目的兼容范围。必须使用满足项目声明/CI matrix "
            "的解释器执行验证；如果当前解释器不满足要求，应切换解释器或明确报告环境不匹配，"
            "绝对不能通过全仓改写类型注解、添加 future import 等方式让项目迁就宿主版本。\n"
            "- 编排器会把 `python`、`python3`、`pytest` 绑定到已选择的兼容运行时；"
            "不要调用绝对路径或带版本后缀的 Python 来绕过该绑定。\n"
        )

    @staticmethod
    def _runtime_environment_gate(project_path: str, gh: GitHubOps | None = None) -> str:
        """Return a blocking error if a Python minimum cannot be satisfied.

        The gate deliberately recognizes a compatible interpreter already on
        PATH or ``uv`` (which can provision the declared interpreter). This
        makes environment mismatch a deterministic workflow decision instead
        of inviting repository-wide compatibility rewrites.
        """
        _, error = AutonomousOrchestrator._select_project_python_runtime(project_path, gh)
        return error

    @staticmethod
    def _select_project_python_runtime(
        project_path: str, gh: GitHubOps | None = None
    ) -> tuple[list[str], str]:
        """Select the command that autonomous Python/pytest shims must use."""
        contract = AutonomousOrchestrator._project_runtime_contract(project_path, gh)
        match = re.search(r"项目声明的 Python 版本：`([^`]+)`", contract)
        if not match:
            return [], ""
        minimum = re.search(r">=\s*(\d+)\.(\d+)", match.group(1))
        if not minimum:
            return [], ""
        required = (int(minimum.group(1)), int(minimum.group(2)))
        # Runtime commands are ultimately launched through the isolated
        # wrapper, not through the repository owner's sudo allowlist.  Probe
        # executable paths as the service account here.  Prefixing probes with
        # ``sudo -u <repo-owner>`` both violates the narrow sudo policy and can
        # make a compatible service interpreter look unavailable.  Preserve
        # the normal preference for an accessible repository virtualenv.
        candidates = [
            os.path.join(project_path, ".venv", "bin", "python"),
            os.path.join(project_path, "venv", "bin", "python"),
        ]
        if sys.version_info[:2] >= required:
            candidates.append(sys.executable)
        for minor in range(required[1], required[1] + 6):
            candidates.append(shutil.which(f"python{required[0]}.{minor}") or "")
        for executable in candidates:
            if not executable:
                continue
            try:
                probe_command = [
                    executable,
                    "-c",
                    "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
                ]
                detected = subprocess.run(
                    probe_command,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                major, found_minor = (int(part) for part in detected.stdout.strip().split(".")[:2])
                if detected.returncode == 0 and (major, found_minor) >= required:
                    return [executable], ""
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
        uv = shutil.which("uv")
        if uv:
            return [
                uv,
                "run",
                "--python",
                f"{required[0]}.{required[1]}",
                "python",
            ], ""
        return [], (
            f"Environment mismatch: project requires Python >= {required[0]}.{required[1]}, "
            f"service has {sys.version_info.major}.{sys.version_info.minor}, and no compatible "
            "interpreter or uv provisioner is available. Repository compatibility rewrites are blocked."
        )

    @staticmethod
    def _scope_violation(changed_files: list[str]) -> str:
        """Return a blocking reason when one autonomous round explodes in scope."""
        limit = MAX_AUTONOMOUS_CHANGED_FILES
        normalized = sorted({path for path in changed_files if path})
        if limit > 0 and len(normalized) > limit:
            sample = ", ".join(normalized[:8])
            return (
                f"Autonomous change scope exceeded: {len(normalized)} files changed "
                f"(limit {limit}). Sample: {sample}"
            )
        return ""

    def _validate_autonomous_change_scope(
        self,
        gh: GitHubOps,
        wf: dict,
        commit_before: str,
        commit_after: str,
    ) -> str:
        """Fail closed on both per-round and cumulative branch scope."""
        if not commit_before or not commit_after:
            return "Autonomous change scope could not be verified: missing commit boundary"
        ranges = [("current round", commit_before)]
        cumulative_base = (wf.get("base_commit_sha") or "").strip()
        if not cumulative_base:
            # Legacy workflows (or a batch whose initial rev-parse failed) can
            # have no persisted base. Never compare them with the *moving*
            # origin/main ref: unrelated commits merged after branch creation
            # would be counted as autonomous changes. The graph merge-base is
            # the immutable branch point and remains stable as main advances.
            try:
                merge_base_result = gh._run_git(
                    ["merge-base", commit_after, "origin/main"], check=False
                )
                cumulative_base = merge_base_result.stdout.strip()
                if merge_base_result.returncode != 0 or not cumulative_base:
                    raise GitHubOpsError("git merge-base returned no commit")
            except Exception as exc:
                return (
                    "Autonomous cumulative branch scope could not be verified: "
                    f"missing immutable base commit and merge-base derivation failed: {exc}"
                )
            # Backfill only the branch-point identifier, never usage/history.
            # Future rounds then remain independent of origin/main movement.
            self._update_workflow({"base_commit_sha": cumulative_base})
        if cumulative_base != commit_before:
            ranges.append(("cumulative branch", cumulative_base))
        for label, base in ranges:
            try:
                changed_files = gh.get_changed_files(base, commit_after)
            except Exception as exc:
                return f"Autonomous {label} scope could not be verified; refusing to push: {exc}"
            violation = self._scope_violation(changed_files)
            if violation:
                return f"{label.capitalize()} {violation}"
        return ""

    def _validate_pre_merge_change_scope(
        self,
        gh: GitHubOps,
        wf: dict,
        pr_head_sha: str,
    ) -> str:
        """Validate the effective PR delta against an immutable main snapshot.

        A conflict resolver can merge months of upstream main history into an
        old PR branch.  The workflow's original ``base_commit_sha`` must not be
        used as the pre-merge "current round" boundary after that merge: doing
        so counts every upstream file as an autonomous edit.  The graph merge
        base with the just-fetched main commit is the effective PR base both
        before and after an upstream merge.
        """
        if not pr_head_sha:
            return "Pre-merge change scope could not be verified: missing PR head"
        try:
            # The PR head SHA comes from the GitHub API and may not be present
            # in the local object DB (the worktree can be torn down between
            # rounds). Fetch the PR branch so merge-base has both objects.
            if not self._ensure_pr_head_local(gh, pr_head_sha, wf.get("branch_name", "")):
                return (
                    "Pre-merge change scope could not be verified: "
                    "PR head not present in local object DB"
                )
            gh._run_git(["fetch", "origin", "main"])
            fetched_main_head = gh.resolve_commit("FETCH_HEAD")
            merge_base_result = gh._run_git(
                ["merge-base", pr_head_sha, fetched_main_head], check=False
            )
            effective_base = merge_base_result.stdout.strip()
            if merge_base_result.returncode != 0 or not effective_base:
                raise GitHubOpsError("git merge-base returned no commit")
        except Exception as exc:
            return f"Pre-merge change scope could not derive effective PR base: {exc}"

        effective_wf = dict(wf)
        effective_wf["base_commit_sha"] = effective_base
        scope_error = self._validate_autonomous_change_scope(
            gh, effective_wf, effective_base, pr_head_sha
        )
        if not scope_error and (wf.get("base_commit_sha") or "").strip() != effective_base:
            # Once upstream main has been merged, subsequent CI-repair rounds
            # must use the same effective PR base rather than the stale branch
            # creation point.  This updates only scope metadata, never usage or
            # session history.
            self._update_workflow({"base_commit_sha": effective_base})
            # _do_merge can enter CI repair later in this same scheduler cycle.
            # Keep that in-memory snapshot aligned with the persisted value so
            # its scope validator cannot reuse the stale branch-creation base.
            wf["base_commit_sha"] = effective_base
        return scope_error

    def _get_ci_repair_prompt(self, wf: dict) -> str:
        """Return merge-phase CI repair context, or empty."""
        context = wf.get("ci_repair_context", "")
        if not context or not context.strip():
            return ""
        return (
            "\n\n## ⚠️ Merge 阶段 CI 修复上下文\n"
            "当前轮次是因为 PR 在合并阶段检测到 CI 失败而回流，请优先处理以下问题：\n"
            f"{context}\n\n"
        )

    def _collect_prior_ci_repair_failures(self) -> list[dict]:
        """Return prior failed ``ci_repair_applied`` milestones.

        Used to inject "what was already tried and failed" into a fresh
        CI repair prompt so the agent doesn't repeat the same fix.
        Context-overflow failures are excluded — the agent never produced
        output in those rounds, so there's nothing actionable to avoid.
        """
        out: list[dict] = []
        for ms in self.repo.list_milestones(self._workflow_id, phase="merge"):
            if ms.get("milestone_type") != "ci_repair_applied":
                continue
            if ms.get("status") != "failed":
                continue
            body = f"{ms.get('error_message') or ''}\n{ms.get('result_summary') or ''}"
            if _CONTEXT_OVERFLOW_RE.search(body):
                continue
            out.append(ms)
        return out

    def _build_prior_repair_failures_prompt(self) -> str:
        """Inject full text of prior CI repair failures.

        Returns empty on the first attempt (no history). Full error_message +
        result_summary are injected verbatim (not summarized): a fresh session
        starts with minimal context, so prior-failure text won't risk overflow.
        """
        prior = self._collect_prior_ci_repair_failures()
        if not prior:
            return ""
        lines = [
            "\n\n## ⚠️ 历史 CI 修复失败记录（请勿重复同样的修法）",
            "以下是此前已经尝试过但失败的修复。当前会话是全新开始的，",
            "请基于这些失败信息采用不同的修复思路，不要重复同样的改动。\n",
        ]
        for i, ms in enumerate(prior, 1):
            title = ms.get("title") or ""
            err = ms.get("error_message") or ""
            summary = ms.get("result_summary") or ""
            lines.append(f"### 失败记录 {i}: {title}")
            if err:
                lines.append(f"- 失败原因：\n```\n{err}\n```")
            if summary:
                lines.append(f"- 当时的修复摘要 / 报错：\n```\n{summary}\n```")
            lines.append("")
        return "\n".join(lines)

    def _build_merge_ci_repair_agent_prompt(
        self,
        wf: dict,
        pr_number: int,
        failed_checks: list[dict],
        *,
        gh: GitHubOps | None = None,
        include_prior_failures: bool = False,
    ) -> str:
        """Assemble the CI repair agent prompt.

        When ``include_prior_failures`` is set (used by the fresh-session
        retry after a context overflow), appends full text of prior failed
        CI repair attempts so the agent avoids repeating them.
        """
        issue_number = wf.get("github_issue_number") or self.workflow.get("github_issue_number")
        prompt = (
            AUTONOMOUS_CONTEXT
            + "当前任务是在已有 PR 上修复 merge 阶段失败的 CI，不是开始新一轮完整开发。\n\n"
        )
        if issue_number:
            prompt += (
                f"## 关联 Issue\n"
                f"本任务关联 GitHub Issue #{issue_number}。\n"
                f"修复时请确保修改继续满足 Issue #{issue_number} 的所有需求。\n\n"
            )
        requirements = str(wf.get("requirements_text") or "").strip()
        if requirements:
            prompt += f"## 原始需求（截断至 6000 字符）\n{requirements[:6000]}\n\n"
        final_plan = str(self._get_latest_final_plan(wf) or "").strip()
        if final_plan and final_plan != requirements:
            prompt += f"## 审定方案（截断至 6000 字符）\n{final_plan[:6000]}\n\n"
        if gh is not None:
            try:
                pr_diff = gh.get_pr_diff(pr_number)
            except Exception as exc:
                logger.warning("Failed to load PR #%s diff for repair context: %s", pr_number, exc)
                pr_diff = ""
            if pr_diff:
                prompt += (
                    f"## 当前 PR diff（截断至 12000 字符）\n```diff\n{pr_diff[:12000]}\n```\n\n"
                )
        prompt += (
            "## 重要约束\n"
            "1. 保持在当前工作分支上修复，不要创建新的 PR，不要切换到其他分支。\n"
            "2. 不要进入新的代码审查、进度汇报或等待流程；当前唯一目标是让现有 PR 的 CI 通过。\n"
            "3. 必须优先根据 CI 工作流、失败日志摘录和仓库脚本定位问题，复现 CI 真实执行的命令。\n"
            "4. 修复后必须重新运行 CI 的完整对应命令，而不是只运行单个 hook、单个文件或你认为相关的子集。\n"
            "5. 如果命令中的 formatter / pre-commit hook 自动修改了文件并以非零状态退出，这只是修复过程，"
            "不代表验证完成；必须保留这些修改并重复运行同一完整命令，直到 exit 0 或确认存在不可自动修复的错误。\n"
            "6. 编排器已先把当前 main 合并进 PR 分支，因此对 "
            "`SKIP=bandit,no-commit-to-branch pre-commit run --all-files` 必须原样运行并重复至 exit 0；"
            "Bandit 由 CI 独立检查。\n"
            "7. 不要执行 git add、git commit 或 git push；编排器会在范围校验通过后统一提交并推送。\n"
            "8. 结束时请明确说明：你复现了哪些完整命令、最终 exit code、修复了什么、还剩什么风险。\n"
        )
        # Runtime contract is already embedded in ci_repair_context; do not
        # duplicate it in the fresh-session prompt.
        prompt += self._get_ci_repair_prompt(wf)
        if failed_checks:
            failure_list = "\n".join(
                f"- **{check.get('name') or 'unknown'}**: {check.get('state') or 'unknown'}"
                for check in failed_checks
                if check.get("bucket") == "fail"
            )
            if failure_list:
                prompt += f"## 当前失败检查\n{failure_list}\n\n"
        prompt += self._get_user_feedback_prompt(wf)
        if include_prior_failures:
            prompt += self._build_prior_repair_failures_prompt()
        return prompt

    @staticmethod
    def _ci_failure_uses_pre_commit(failed_checks: list[dict]) -> bool:
        """Whether collected CI evidence identifies a pre-commit failure."""
        evidence = "\n".join(
            str(check.get("failure_excerpt") or "") for check in failed_checks
        ).lower()
        return any(
            marker in evidence
            for marker in (
                "pre-commit",
                "pre_commit",
                "hook id:",
                "files were modified by this hook",
            )
        )

    def _converge_pre_commit_fixes(
        self,
        wf: dict,
        gh: GitHubOps,
        failed_checks: list[dict],
    ) -> tuple[bool, str]:
        """Run the CI's full pre-commit command under the isolated agent account.

        Some hooks intentionally modify files and return 1 on their first pass.
        A repair agent can mistake that for completion and push a still-red
        branch. Re-run the full command until it is clean, while preserving the
        same credentialless OS boundary used for all autonomous repository code.

        Returns ``(attempted, remaining_error)``. A remaining error is reported
        in the repair summary but does not discard safe hook edits: the next CI
        run remains the authoritative check and can feed a subsequent repair.
        """
        if wf.get("workspace_type") == "remote" or not self._ci_failure_uses_pre_commit(
            failed_checks
        ):
            return False, ""

        project_path = wf.get("worktree_path") or wf.get("project_path", "")
        config_path = os.path.join(project_path, ".pre-commit-config.yaml")
        if not project_path or not gh.path_exists_as_user(config_path, file_only=True):
            return False, ""

        pre_commit = shutil.which("pre-commit")
        real_git = shutil.which("git")
        if not pre_commit or not real_git:
            logger.warning("Skipping isolated pre-commit convergence: executable unavailable")
            return False, ""

        project_system_account = self._resolve_system_account(wf)
        # Engage the credentialless isolated-agent principal only when the
        # privileged launcher is provisioned (Linux multi-user). On dev/macOS
        # installs without openace-run-as, fall back to the repository owner
        # account in same-user mode — mirroring _run_agent's downgrade.
        if AutonomousAgentRunner.is_isolated_launcher_available():
            isolated_account = self._resolve_isolated_agent_account()
            if project_system_account and isolated_account == project_system_account:
                raise RuntimeError(
                    "Autonomous validation account must differ from the repository owner account"
                )
        else:
            logger.warning(
                "Workflow %s: isolated agent launcher unavailable; running pre-commit "
                "convergence as the repository owner (%s) in same-user mode.",
                getattr(self, "_workflow_id", ""),
                project_system_account or "(unknown)",
            )
            isolated_account = project_system_account
        runtime_command, _ = self._select_project_python_runtime(project_path, gh)
        guard_bin = AutonomousAgentRunner._resolve_agent_guard_bin()
        env = {
            "PATH": guard_bin + os.pathsep + os.environ.get("PATH", ""),
            "OPENACE_REAL_GIT": real_git,
            "OPENACE_GIT_CACHE_ROOT": str(
                AutonomousAgentRunner._resolve_home_dir(isolated_account) / ".cache" / "pre-commit"
            ),
            "OPENACE_PYTHON_COMMAND": json.dumps(runtime_command or [sys.executable]),
            "GH_CONFIG_DIR": "/var/empty/openace-autonomous-gh",
            "GIT_TERMINAL_PROMPT": "0",
            "SKIP": CI_PRE_COMMIT_SKIP,
        }
        command, cwd = AutonomousAgentRunner._wrap_agent_cmd(
            [pre_commit, "run", "--all-files"],
            project_path,
            isolated_account,
            env,
        )
        last_output = ""
        passes_run = 0
        for pass_number in range(1, MAX_PRE_COMMIT_CONVERGENCE_PASSES + 1):
            passes_run = pass_number
            try:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    env=None if cwd is None else {**os.environ, **env},
                    capture_output=True,
                    text=True,
                    timeout=PRE_COMMIT_CONVERGENCE_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return True, f"isolated pre-commit validation could not run: {exc}"

            last_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            if result.returncode == 0:
                logger.info("Isolated pre-commit convergence passed on run %d", pass_number)
                return True, ""

            hooks_modified_files = any(
                marker in last_output.lower()
                for marker in (
                    "files were modified by this hook",
                    "fixing ",
                    "reformatted ",
                )
            )
            if not hooks_modified_files:
                break

        concise_output = last_output[-4000:] if last_output else "no output"
        return True, (
            "isolated `pre-commit run --all-files` did not reach exit 0 after "
            f"{passes_run} pass(es):\n{concise_output}"
        )

    @staticmethod
    def _detect_and_push_ci_repair_changes(
        gh: GitHubOps,
        commit_before: str,
        attempt: int,
        branch_name: str | None,
        pr_number: int,
        scope_validator=None,
    ) -> tuple[str, bool, str]:
        """Detect whether the CI repair agent produced changes and push them.

        Returns ``(commit_sha, sha_changed, push_error)``. Extracted so the
        "remote A, local B, agent no new commit → still push B" behavior is
        unit-testable without running the full ``_run_merge_ci_repair`` flow
        (#1838 review suggestion 1).
        """
        commit_sha = ""
        local_start_sha = ""
        try:
            commit_sha = gh.get_current_commit()
            local_start_sha = commit_sha
        except Exception:
            pass

        # Always commit working-tree edits before comparing/pushing. The local
        # HEAD may already be ahead of the remote PR after a transient push
        # failure; pre-commit convergence can then add fresh edits on top of
        # that commit. Skipping this check when HEAD differs would push the old
        # commit and silently strand the validated hook fixes in the worktree.
        try:
            if gh.has_uncommitted_changes():
                gh.git_add_all()
                gh.git_commit(
                    f"auto: ci repair (attempt {attempt})",
                    no_verify=True,
                )
                commit_sha = gh.get_current_commit()
        except Exception as e:
            message = f"CI repair auto-commit failed: {e}"
            logger.warning(message)
            sha_changed = bool(commit_before and commit_sha and commit_before != commit_sha)
            return commit_sha, sha_changed, message

        sha_changed = bool(commit_before and commit_sha and commit_before != commit_sha)

        push_error = ""
        if sha_changed:
            if scope_validator is not None:
                push_error = str(scope_validator(commit_before, commit_sha) or "")
                if push_error:
                    try:
                        gh.reset_hard_to(local_start_sha or commit_before)
                    except Exception as exc:
                        push_error += (
                            "; failed to discard the rejected local CI-repair commit: " f"{exc}"
                        )
                    return commit_sha, sha_changed, push_error
            try:
                gh.git_push(branch=branch_name, force_with_lease=True)
            except Exception as e:
                # Distinguish transient vs non-transient to enable Layer-2 retry
                # (Issue #1814).
                if _is_transient_git_error(e):
                    # Transient: propagate to trigger Layer-2 retry
                    logger.warning("Transient CI repair push failure for PR #%s: %s", pr_number, e)
                    raise
                else:
                    # Non-transient: capture error message for milestone
                    push_error = str(e)
                    logger.warning("CI repair git_push failed for PR #%s: %s", pr_number, e)

        return commit_sha, sha_changed, push_error

    def _run_merge_ci_repair(
        self, wf: dict, gh: GitHubOps, pr_number: int, failed_checks: list[dict]
    ) -> None:
        """Repair CI failures for an existing PR in-place during merge phase."""
        dev_round = int(wf.get("dev_round", 1) or 1)
        attempt = int(wf.get("ci_repair_attempts", 0) or 0)
        branch_name = wf.get("branch_name", "")
        project_path = wf.get("worktree_path") or wf.get("project_path", "")
        runtime_error = (
            ""
            if wf.get("workspace_type") == "remote"
            else self._runtime_environment_gate(project_path, gh)
        )
        if runtime_error:
            self._create_milestone(
                phase="merge",
                dev_round=dev_round,
                round_number=attempt,
                milestone_type="ci_repair_environment_mismatch",
                status="failed",
                title="CI repair environment is incompatible",
                error_message=runtime_error,
            )
            self._update_workflow({"status": "failed", "error_message": runtime_error})
            return

        repair_ms = self._create_milestone(
            phase="merge",
            dev_round=dev_round,
            round_number=attempt,
            milestone_type="ci_repair_applied",
            status="in_progress",
            title=f"CI repair attempt {attempt} for PR #{pr_number}",
        )

        repair_prompt = self._build_merge_ci_repair_agent_prompt(
            wf, pr_number, failed_checks, gh=gh, include_prior_failures=True
        )

        # Capture the PR's remote head SHA (not the local worktree HEAD) as the
        # baseline. If a prior repair round committed locally but didn't push
        # (or pushed after this capture), the local HEAD would already include
        # that commit, making commit_sha == commit_before and falsely reporting
        # "no code changes". Using the remote PR head as baseline ensures we
        # measure against what's actually on the PR branch on GitHub.
        commit_before = ""
        try:
            commit_before = gh.get_pr_head_sha(pr_number)
        except Exception as exc:
            # Fallback to local HEAD if the PR head lookup fails — better than
            # skipping the check entirely. Log so ops can see when the fix
            # degrades to the old (buggy) local-vs-local comparison.
            logger.warning(
                "get_pr_head_sha failed for PR #%s, falling back to local HEAD "
                "for commit_before: %s",
                pr_number,
                exc,
            )
            try:
                commit_before = gh.get_current_commit()
            except Exception:
                pass

        # CI repair is deliberately a one-shot session.  Resuming the main
        # plan/development conversation at merge time routinely exceeds the
        # provider context window and adds no useful signal: the exact PR diff
        # and CI excerpts are already present in the prompt.
        repair_result = self._run_agent(
            wf=wf,
            workflow_id=self._workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=repair_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="fresh",
            timeout=int(wf.get("task_timeout") or DEFAULT_TASK_TIMEOUT),
            milestone_id=repair_ms.get("milestone_id", ""),
        )

        if repair_result.error_code == "repo_integrity_violation":
            self._accumulate_tokens(repair_result)
            self._abort_on_repo_integrity_violation(
                repair_result, repair_ms.get("milestone_id", "")
            )
            return

        if self._is_context_overflow(repair_result):
            # A fresh prompt cannot be made smaller by replaying the same
            # request with added history. Discard any partial edits and fail
            # this bounded repair attempt without pushing uncertain code.
            self._accumulate_tokens(repair_result)
            try:
                gh.reset_hard_to_head()
            except Exception as exc:
                logger.warning("Failed to discard partial overflow edits: %s", exc)
            message = (
                "CI repair failed: context overflow - "
                f"{repair_result.error or repair_result.response_text}"
            )
            self.repo.update_milestone(
                repair_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "session_id": repair_result.session_id,
                    "error_message": message,
                },
            )
            self._update_workflow({"status": "failed", "error_message": message})
            return

        self._gh = None
        gh = self._get_gh()

        try:
            current_branch = gh.get_current_branch()
            if branch_name and current_branch != branch_name:
                message = (
                    f"CI repair changed workflow branch unexpectedly: expected {branch_name}, "
                    f"actual {current_branch}"
                )
                self.repo.update_milestone(
                    repair_ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "error_message": message,
                    },
                )
                self._update_workflow({"status": "failed", "error_message": message})
                return
        except Exception as e:
            logger.warning("Failed to verify branch after CI repair: %s", e)

        if wf.get("user_feedback", "").strip():
            self._update_workflow({"user_feedback": ""})

        pre_commit_attempted = False
        pre_commit_error = ""
        try:
            pre_commit_attempted, pre_commit_error = self._converge_pre_commit_fixes(
                wf, gh, failed_checks
            )
        except Exception as exc:
            # The isolated validation is defense-in-depth. Preserve the agent's
            # edits and let GitHub CI remain authoritative if the local wrapper
            # itself is unavailable or misconfigured.
            pre_commit_attempted = True
            pre_commit_error = f"isolated pre-commit validation failed to start: {exc}"
            logger.warning(pre_commit_error, exc_info=True)

        self._accumulate_tokens(repair_result)

        commit_sha, sha_changed, push_error = self._detect_and_push_ci_repair_changes(
            gh,
            commit_before,
            attempt,
            branch_name or None,
            pr_number,
            scope_validator=lambda before, after: self._validate_autonomous_change_scope(
                gh, wf, before, after
            ),
        )

        # Record the trusted head only after a successful commit+push (the
        # static helper has no `self`; this is the caller that owns state).
        if sha_changed and not push_error:
            self._record_trusted_head(gh, pushed=True, sha=commit_sha)

        try:
            diff_stats = gh.get_commit_diff_stats(commit_sha) if commit_sha else {}
        except Exception:
            pass

        salvaged = (not repair_result.success) and sha_changed and not push_error
        summary = self._build_dev_result_summary(
            self._artifact_text(repair_result),
            diff_stats,
            commit_sha,
            repair_result.success or salvaged,
        )
        if pre_commit_attempted:
            validation_summary = (
                pre_commit_error or "isolated `pre-commit run --all-files` converged with exit 0"
            )
            summary = f"{summary}\n\nValidation: {validation_summary}".strip()

        milestone_updates = {
            "status": "completed" if (repair_result.success or salvaged) else "failed",
            "session_id": repair_result.session_id,
            "commit_shas": json.dumps([commit_sha] if commit_sha else []),
            "diff_stats": json.dumps(diff_stats),
            "result_summary": summary,
            "tldr": self._artifact_tldr(repair_result),
            "error_message": "",
        }

        if push_error:
            message = f"CI repair failed to push branch '{branch_name}': {push_error}"
            milestone_updates["status"] = "failed"
            milestone_updates["error_message"] = message
            self.repo.update_milestone(repair_ms.get("milestone_id", ""), milestone_updates)
            self._update_workflow({"status": "failed", "error_message": message})
            return

        if not sha_changed:
            # Preserve the context-overflow signal in the error message so the
            # next round's _collect_prior_ci_repair_failures filters it out
            # (an overflow failure has no actionable signal — the agent never
            # produced output). Without this, a double-overflow round would
            # be misclassified as "no code changes" and wrongly injected into
            # the next fresh prompt. (#1816 review suggestion)
            if self._is_context_overflow(repair_result):
                message = f"CI repair failed: context overflow - {repair_result.error}"
            elif primary_error := self._primary_result_error(repair_result):
                message = f"CI repair agent failed before producing code changes: {primary_error}"
            else:
                message = "CI repair failed: agent produced no code changes"
            milestone_updates["status"] = "failed"
            milestone_updates["error_message"] = message
            self.repo.update_milestone(repair_ms.get("milestone_id", ""), milestone_updates)
            self._update_workflow({"status": "failed", "error_message": message})
            return

        if not repair_result.success and not salvaged:
            message = f"CI repair failed: {repair_result.error}"
            milestone_updates["status"] = "failed"
            milestone_updates["error_message"] = message
            self.repo.update_milestone(repair_ms.get("milestone_id", ""), milestone_updates)
            self._update_workflow({"status": "failed", "error_message": message})
            return

        self.repo.update_milestone(repair_ms.get("milestone_id", ""), milestone_updates)
        self._update_workflow({"current_phase": "merge", "status": "merging", "error_message": ""})

        repair_comment = (
            f"## 🔧 CI Repair Attempt {attempt}\n\n- PR: #{pr_number}\n- Branch: `{branch_name}`\n"
        )
        if commit_sha:
            repair_comment += f"- Commit: `{commit_sha[:8]}`\n"
        repair_comment += "\n已推送修复提交，等待 GitHub CI 重新运行。\n"
        if summary:
            repair_comment += f"\n### 修复摘要\n{summary}\n"
        self._post_github_comment(gh, pr_number, repair_comment, is_pr=True, context="ci-repair")

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
    def _get_pr_review_diff(gh: GitHubOps, pr_number: int | None, branch_name: str) -> str:
        """Return only the changes introduced by the workflow branch.

        A two-point ``git diff main branch`` includes unrelated changes that
        landed on ``main`` after a long-running workflow branched. Prefer
        GitHub's PR diff, whose semantics are based on the PR merge base. If
        the PR API is temporarily unavailable, reproduce those semantics
        locally by resolving the merge base explicitly.
        """
        if pr_number:
            try:
                pr_diff = gh.get_pr_diff(pr_number)
                if pr_diff:
                    return pr_diff
            except Exception as exc:
                logger.warning("Failed to load PR #%s diff for review: %s", pr_number, exc)

        if not branch_name:
            return ""

        try:
            merge_base = gh._run_git(["merge-base", "origin/main", branch_name]).stdout.strip()
            if not merge_base:
                return ""
            return gh.get_diff(merge_base, branch_name)
        except Exception as exc:
            logger.warning(
                "Failed to load merge-base diff for review branch %s: %s",
                branch_name,
                exc,
            )
            return ""

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
    def _artifact_visible_text(result: AgentTaskResult | None) -> str:
        """Return all user-visible assistant turns for a task result."""
        if not result:
            return ""
        return (getattr(result, "visible_response_text", "") or result.response_text or "").strip()

    @classmethod
    def _sanitize_artifact_text(cls, text: str) -> str:
        return sanitize_artifact_text(text)

    @classmethod
    def _artifact_text(cls, result: AgentTaskResult | None) -> str:
        """Return the milestone/comment artifact text for a task result."""
        if not result:
            return ""
        return pick_best_artifact_text(
            (result.response_text or "").strip(),
            cls._artifact_visible_text(result),
        )

    @classmethod
    def _artifact_tldr(cls, result: AgentTaskResult | None) -> str:
        """Prefer structured/extracted TL;DR over raw string slicing."""
        if not result:
            return ""
        structured = getattr(result, "structured_tags", {}) or {}
        if structured.get("tldr"):
            return structured["tldr"][:200]
        return cls._extract_tldr(cls._artifact_visible_text(result) or result.response_text or "")

    @staticmethod
    def _artifact_status_tag(result: AgentTaskResult | None, key: str) -> str:
        """Read structured status tags extracted by the runner."""
        if not result:
            return ""
        structured = getattr(result, "structured_tags", {}) or {}
        value = structured.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _primary_result_error(result: AgentTaskResult | None) -> str:
        """Return the first-class runner error that should win over heuristics."""
        if not result or result.success:
            return ""
        error = getattr(result, "error", None)
        return error.strip() if isinstance(error, str) else ""

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

    def _get_latest_final_plan(self, wf: dict) -> str:
        """Return the latest finalized plan, or fall back to requirements."""
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
        return final_plan or wf.get("requirements_text", "No plan available")

    @staticmethod
    def _extract_markdown_section(text: str, headings: tuple[str, ...]) -> str:
        """Extract a markdown or numbered-plan section by heading name."""
        if not text or not headings:
            return ""
        names = tuple(h.strip().lower() for h in headings if h and h.strip())
        if not names:
            return ""

        def _normalize_title(title: str) -> str:
            lowered = re.sub(r"\s+", " ", (title or "").strip().lower())
            return re.sub(r"\s*[:：(\[（【].*$", "", lowered).strip()

        def _matches_heading(title: str) -> bool:
            normalized = _normalize_title(title)
            return any(normalized == name.lower() for name in names)

        lines = text.splitlines()
        capture: list[str] = []
        active_mode = ""

        for idx, line in enumerate(lines):
            md_match = re.match(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", line)
            if md_match:
                title = md_match.group(1).strip()
                if capture and active_mode:
                    break
                if _matches_heading(title):
                    active_mode = "markdown"
                    continue

            num_match = re.match(r"^\s*(\d+)\.\s*(.+?)\s*$", line)
            if num_match:
                title = num_match.group(2).strip()
                if capture and active_mode == "numbered":
                    break
                if _matches_heading(title):
                    active_mode = "numbered"
                    suffix_match = re.match(
                        r"^\s*\d+\.\s*.+?(?:[:：]\s*(.*))?$",
                        line,
                    )
                    suffix = (suffix_match.group(1) if suffix_match else "") or ""
                    if suffix.strip():
                        capture.append(suffix.strip())
                    continue

            if capture or active_mode:
                if active_mode == "markdown" and re.match(r"^\s{0,3}#{1,6}\s+", line):
                    break
                capture.append(line)

        return "\n".join(capture).strip()

    @staticmethod
    def _analyze_changed_files(changed_files: list[str]) -> dict:
        """Classify changed files into validation-relevant buckets."""
        normalized = [path.strip() for path in changed_files if path and path.strip()]
        tags: set[str] = set()
        docs_only = bool(normalized)

        for path in normalized:
            lower = path.lower()
            is_doc = (
                lower.endswith((".md", ".rst", ".txt"))
                or "/docs/" in lower
                or lower.startswith("docs/")
            )
            docs_only = docs_only and is_doc

            if path.startswith("frontend/"):
                tags.add("frontend")
            if path.startswith(("app/", "backend/", "tests/unit/", "tests/integration/")):
                tags.add("backend")
            if path.startswith(
                (
                    "app/utils/",
                    "app/services/",
                    "app/repositories/",
                    "app/modules/",
                    "app/schemas/",
                )
            ):
                tags.add("shared-backend")
            if path.startswith(("app/routes/", "app/api/", "app/schemas/", "frontend/src/api/")):
                tags.add("contracts")
            if path.startswith("tests/issues/"):
                tags.add("issue-regression")
            if path.startswith("tests/e2e/") or "playwright" in lower:
                tags.add("e2e")
            if path.startswith(".github/workflows/") or path in (
                "package.json",
                "pyproject.toml",
                "requirements.txt",
                "requirements-dev.txt",
                "poetry.lock",
                "pnpm-lock.yaml",
                "package-lock.json",
                "yarn.lock",
                "Makefile",
                "tox.ini",
            ):
                tags.add("tooling")
            if (
                "alembic" in lower
                or "/migrations/" in lower
                or lower.startswith("migrations/")
                or "migration" in lower
            ):
                tags.add("migration")

        broaden_scope = bool(
            {"migration", "contracts", "tooling", "shared-backend"} & tags
            or ("frontend" in tags and "backend" in tags)
            or len({path.split("/", 1)[0] for path in normalized if "/" in path}) >= 3
        )

        return {
            "tags": sorted(tags),
            "docs_only": docs_only,
            "broaden_scope": broaden_scope,
        }

    @classmethod
    def _build_targeted_validation_scopes(
        cls, changed_files: list[str], framework_type: str
    ) -> list[tuple[str, str]]:
        """Derive validation scopes from concrete file changes."""
        analysis = cls._analyze_changed_files(changed_files)
        tags = set(analysis["tags"])
        scopes: list[tuple[str, str]] = []

        if analysis["docs_only"]:
            scopes.append(
                ("最小化验证", "当前改动看起来是文档/说明变更，优先做语法或引用一致性检查。")
            )
            return scopes

        if "frontend" in tags:
            scopes.append(
                ("前端相关验证", "改动涉及 frontend 目录，应优先覆盖前端组件、页面或样式行为。")
            )
        if "backend" in tags:
            scopes.append(("后端单元验证", "改动涉及 Python/服务端目录，应优先验证对应单元测试。"))
        if "shared-backend" in tags:
            scopes.append(
                (
                    "共享后端依赖验证",
                    "改动触及 app/utils、app/services、schema 等共享模块，应扩大到直接依赖方或相关调用链测试。",
                )
            )
        if "contracts" in tags:
            scopes.append(("接口/契约验证", "改动涉及路由、API 或前后端契约，需确认调用链兼容。"))
        if "migration" in tags:
            scopes.append(("数据迁移验证", "改动涉及 migration/schema，需要验证迁移和持久层行为。"))
        if "issue-regression" in tags:
            scopes.append(
                ("问题回归验证", "改动触达 issue/regression 测试目录，应复用对应回归用例。")
            )
        if "e2e" in tags:
            scopes.append(
                ("端到端/Smoke 验证", "改动触达 E2E 相关代码，应补充对应 smoke 或端到端验证。")
            )
        if "tooling" in tags:
            scopes.append(
                ("仓库脚本/CI 验证", "改动涉及工作流、依赖或构建脚本，应对照仓库真实命令验证。")
            )

        if not scopes:
            fallback = {
                "python": (
                    "后端定向验证",
                    "当前仓库以 Python 为主，先从最接近改动模块的 pytest 子集开始。",
                ),
                "javascript": (
                    "前端定向验证",
                    "当前仓库以 JavaScript 为主，先从 package.json 中的测试脚本开始。",
                ),
                "mixed": (
                    "混合仓库定向验证",
                    "当前仓库同时包含多种栈，先按改动所在子目录选择最小必要测试范围。",
                ),
            }
            scopes.append(
                fallback.get(
                    framework_type,
                    (
                        "定向验证",
                        "未识别明确改动类型，请先阅读仓库测试约定并选择最小必要验证范围。",
                    ),
                )
            )

        return scopes

    def _build_test_execution_context(self, wf: dict, gh: GitHubOps) -> str:
        """Build a targeted validation brief for the test phase."""
        project_path = wf.get("worktree_path") or wf.get("project_path", "")
        framework_type = _infer_test_framework(project_path, wf.get("cli_tool", ""))
        final_plan = self._get_latest_final_plan(wf)
        verification_plan = self._extract_markdown_section(
            final_plan,
            (
                "验证计划",
                "测试策略",
                "Validation Plan",
                "Validation Strategy",
                "Test Strategy",
                "Testing Strategy",
            ),
        )

        changed_files: list[str] = []
        try:
            commit_sha = gh.get_current_commit()
        except Exception:
            commit_sha = ""
        try:
            changed_files = (
                gh.get_commit_changed_files(commit_sha)
                if commit_sha
                else gh.get_changed_files("HEAD~1", "HEAD")
            )
        except Exception:
            changed_files = []

        analysis = self._analyze_changed_files(changed_files)
        scopes = self._build_targeted_validation_scopes(changed_files, framework_type)

        guardrail = (
            "除非下面的验证范围、仓库约定文件或实际失败证据明确要求，否则不要从裸 "
            "`python -m pytest`、`pytest tests/`、全仓库扫描式命令开始。"
        )
        if analysis["docs_only"]:
            guardrail = "当前更像文档/说明类改动，只做最小必要验证，不要升级为大范围回归。"
        elif "frontend" in analysis["tags"] and "backend" not in analysis["tags"]:
            guardrail = "当前改动主要在前端。先验证前端相关测试/构建；没有跨层证据前，不要先跑后端全树 pytest。"
        elif "backend" in analysis["tags"] and "frontend" not in analysis["tags"]:
            guardrail = "当前改动主要在后端。先验证最接近改动模块的后端测试；不要一上来跑整个仓库的所有测试。"
        if "shared-backend" in analysis["tags"]:
            guardrail += " 若改动触及共享后端模块，还应补上直接依赖方或相关调用链测试。"

        repo_guidance: list[str] = []
        if project_path:
            if os.path.exists(os.path.join(project_path, "tests", "README.md")):
                repo_guidance.append(
                    "仓库包含 `tests/README.md`，Python 测试范围请优先遵循其中的 unit/integration/e2e 分层。"
                )
            if os.path.exists(os.path.join(project_path, "frontend", "package.json")):
                repo_guidance.append(
                    "仓库包含 `frontend/package.json`，前端测试请优先使用其中已有脚本，不要临时发明命令。"
                )
            if os.path.exists(os.path.join(project_path, ".github", "workflows")):
                repo_guidance.append(
                    "如果改动涉及依赖、构建或工作流，请对照 `.github/workflows/` 中真实 job 的本地命令。"
                )
            if os.path.exists(os.path.join(project_path, "pytest.ini")):
                repo_guidance.append(
                    "仓库包含 `pytest.ini`，选择 pytest 命令前先确认默认收集范围和配置。"
                )

        changed_lines = (
            "\n".join(f"- `{path}`" for path in changed_files[:20])
            or "- 未能自动获取改动文件，请先自行确认本轮变更范围。"
        )
        if len(changed_files) > 20:
            changed_lines += f"\n- 其余 {len(changed_files) - 20} 个文件省略"

        scope_lines = "\n".join(f"- **{name}**：{reason}" for name, reason in scopes)
        repo_lines = (
            "\n".join(f"- {line}" for line in repo_guidance)
            or "- 未发现额外仓库约定文件，请先检查项目根目录中的测试/构建配置。"
        )

        context = [
            "## 本轮定向验证上下文",
            f"- 推断框架类型：`{framework_type}`",
            f"- 是否建议扩大测试范围：{'是' if analysis['broaden_scope'] else '否'}",
            "",
            "### 最终方案",
            final_plan[:4000],
            "",
        ]
        if verification_plan:
            context.extend(["### 方案中的验证计划", verification_plan[:2000], ""])
        context.extend(
            [
                "### 本轮改动文件",
                changed_lines,
                "",
                "### 建议优先验证的方面",
                scope_lines,
                "",
                "### 仓库测试约定",
                repo_lines,
                "",
                "### 范围护栏",
                guardrail,
            ]
        )
        return "\n".join(context).strip()

    def _post_github_comment(
        self,
        gh: GitHubOps,
        number: int,
        body: str,
        *,
        is_pr: bool = False,
        context: str = "",
        content_language: str | None = None,
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
    ) -> dict | None:
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

    # ── Phase A (#2044): unified phase/status commit entrypoint ──────────────
    #
    # The single authoritative path for phase/status transition. Phase handlers
    # migrated onto the PhaseResult contract return a structured outcome instead
    # of calling _update_workflow / _create_milestone inline; this method
    # validates and applies it. Crucially, a failed/paused/retrying phase can
    # no longer write the NEXT phase's state — only outcome="completed" advances
    # current_phase, which prevents the partial-commit hazard #2044 targets.
    #
    # Legacy _do_* methods that still return None keep their inline commits
    # (see _dispatch_phase / advance). This method is additive and does not
    # change existing behaviour until a phase opts in.

    def _commit_phase_result(self, result: PhaseResult) -> None:
        """Validate and persist a ``PhaseResult`` atomically-ish.

        Order matters: workflow patch + phase/status first, then milestones,
        then usage. Each DB call is individual (no transaction across repos),
        but routing every transition through here makes phase/status mutation a
        single auditable path — the property Phase A requires.
        """
        patch: dict[str, object] = dict(result.workflow_patch)

        if result.outcome == "completed":
            # Only a successful phase may advance current_phase. An unknown
            # next_phase is rejected rather than silently stored, so a buggy
            # handler cannot strand the workflow in a non-canonical phase.
            if result.next_phase is None:
                raise ValueError("PhaseResult(completed) requires next_phase")
            # "wait" is a pseudo-phase the orchestrator dispatches (advance() has
            # ``elif phase == "wait":`` routing to _do_wait) but it is NOT in
            # PHASE_ORDER because it is not part of the canonical linear sequence
            # — it is a park state report/wait transitions into. Admit it here
            # alongside "completed" (the terminal pseudo-phase) so _do_report's
            # legitimate next_phase="wait" return is not rejected as unknown.
            if result.next_phase not in PHASE_ORDER and result.next_phase not in (
                "completed",
                "wait",
            ):
                raise ValueError(
                    f"PhaseResult(completed) next_phase={result.next_phase!r} "
                    f"is not in PHASE_ORDER={PHASE_ORDER}"
                )
            # The "completed" pseudo-phase is a terminal workflow status, not a
            # real phase. A handler signals it by next_phase="completed" (the
            # sentinel mirrors how _do_merge writes status="completed" +
            # completed_at + emits phase_change{"phase":"completed"}).
            #
            # completed_at is written here, symmetric with paused_at on the
            # pause branch, so a migrated merge handler cannot silently drop
            # the completion timestamp (review feedback on #2065).
            #
            # phase_change events are NOT emitted by this entrypoint: the legacy
            # _do_* methods emit them inline with phase-specific payloads (e.g.
            # {"phase":"merge","auto_merge":True}), which the entrypoint cannot
            # reconstruct. A migrated handler must emit its own phase_change.
            #
            # The terminal current_phase value is phase-specific: merge completes
            # with current_phase="merge" (the workflow is already there and the
            # legacy merge kept it), while pr_review's no-changes/timing-issue
            # terminal path writes the literal "completed" (it skips report/
            # merge entirely). A handler signals the literal terminal
            # current_phase by including it in workflow_patch (the T3 guard
            # scans phases/*.py for direct _update_workflow({...}) calls and
            # patch[...] assignments, NOT for PhaseResult(workflow_patch=...)
            # construction, so this is the sanctioned escape hatch). When the
            # patch carries current_phase, honour it; otherwise default to
            # "merge" (the merge-phase terminal semantics).
            if result.next_phase == "completed":
                if "current_phase" in patch:
                    # Whitelist the patch-carried current_phase to known terminal
                    # values. Without this a buggy handler could strand the
                    # workflow in a non-canonical phase (e.g. current_phase=
                    # "development" + status="completed") by stuffing it into
                    # workflow_patch — the T3 AST guard cannot see it because
                    # PhaseResult.workflow_patch is the sanctioned escape hatch.
                    # Mirror the top-level next_phase rejection. (#2044 S2)
                    if patch["current_phase"] not in _COMPLETED_TERMINAL_PHASES:
                        raise ValueError(
                            f"PhaseResult(completed) workflow_patch.current_phase="
                            f"{patch['current_phase']!r} is not a valid terminal phase "
                            f"(allowed: {_COMPLETED_TERMINAL_PHASES})"
                        )
                    patch.setdefault("status", "completed")
                else:
                    patch["current_phase"] = "merge"
                    patch["status"] = "completed"
                patch["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            elif result.next_phase == "wait":
                # The wait pseudo-phase: park the workflow at current_phase="wait"
                # so advance() routes the next cycle to _do_wait. The handler
                # supplies next_status (typically "waiting"); default to it so a
                # bare next_phase="wait" still lands in a visible park state.
                patch["current_phase"] = "wait"
                patch["status"] = result.next_status or "waiting"
            else:
                patch["current_phase"] = result.next_phase
                patch["status"] = result.next_status or PHASE_STATUS_MAP.get(
                    result.next_phase, "planning"
                )
        elif result.outcome == "wait":
            # Wait parks the workflow without advancing the phase.
            patch["status"] = result.next_status or "waiting"
        elif result.outcome == "retry":
            # Retry leaves phase/status untouched; only the patch (if any)
            # applies — e.g. a transient_retry_count bump.
            pass
        elif result.outcome == "pause":
            patch["status"] = "paused"
            patch["paused_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        elif result.outcome == "failed":
            patch["status"] = "failed"
            if isinstance(result.structured_error, dict) and result.structured_error.get("message"):
                patch["error_message"] = str(result.structured_error["message"])
        else:  # pragma: no cover - defensive, outcome is a Literal
            raise ValueError(f"Unknown PhaseResult outcome: {result.outcome!r}")

        if patch:
            self._update_workflow(patch)

        # Milestones are committed after the workflow patch so an observer
        # reading the timeline sees the phase advance first. _create_milestone
        # is idempotent, so replaying a partially-applied result is safe.
        for ms_kwargs in result.milestone_events:
            self._create_milestone(**ms_kwargs)

        if result.usage_delta is not None:
            # Refresh workflow totals from linked sessions. The delta value is
            # currently unused — _accumulate_tokens ignores its argument and
            # recomputes from sessions — so passing it is a no-op today.
            #
            # TODO(#2044 Phase B): when an evidence service (#2046) produces a
            # real structured delta, either teach _accumulate_tokens to apply
            # it (fixing its unused _result param / the object-vs-AgentTaskResult
            # type smell) or route usage updates through a dedicated method.
            # review feedback on #2065.
            self._accumulate_tokens(result.usage_delta)  # type: ignore[arg-type]

    def _build_workflow_context(self, wf: dict) -> WorkflowContext:
        """Assemble the read-only snapshot a phase handler receives.

        Phases migrated onto the contract take this instead of the raw ``wf``
        dict, so they cannot accidentally read mutable workflow fields that a
        user may edit mid-run (model, permission_mode). The cancellation event
        is the orchestrator's own shutdown signal — phases observe it instead
        of the scheduler aborting them externally.

        ``repository_context`` snapshots the persisted git/worktree binding so
        migrated phases read branch/HEAD via ``ctx.repository_context`` rather
        than ad hoc (Phase B / #2044).
        """
        return WorkflowContext(
            workflow=wf,
            definition_snapshot=wf.get("definition_snapshot"),
            repository_context=self._build_repository_context(wf),
            session_bindings=self._session_usage_offsets,
            cancellation=self._shutdown_requested,
        )

    def _build_repository_context(self, wf: dict) -> dict:
        """Snapshot of the git/worktree binding a phase reads via ctx.

        Phase B (#2044): replaces the Phase-A ``repository_context=None`` TODO.
        Task 5 will move the underlying helpers into GitWorkspaceService and
        this becomes a thin delegate; for now it snapshots the persisted
        binding fields.
        """
        return {
            "branch_name": wf.get("branch_name"),
            "worktree_path": wf.get("worktree_path"),
            "expected_head_sha": wf.get("expected_head_sha"),
            "base_branch": wf.get("original_branch_name") or "main",
        }

    def _poll_ci_status(self, gh: GitHubOps, pr_number: int) -> list:
        """Poll CI checks until all are non-pending or timeout is reached.

        Returns the final list of CI check dicts (may still contain pending
        items if the timeout was reached).
        """
        deadline = time.monotonic() + CI_POLL_MAX_WAIT
        last_error = ""
        while True:
            if AutonomousOrchestrator._is_shutdown_requested(self):
                raise WorkflowPaused("Service shutdown interrupted CI polling")
            try:
                checks = gh.get_pr_checks(pr_number)
            except Exception as e:
                last_error = str(e)
                logger.warning("CI check query failed for PR #%s, will retry...", pr_number)
                if time.monotonic() >= deadline:
                    raise GitHubOpsError(
                        f"Failed to query CI checks for PR #{pr_number} within "
                        f"{CI_POLL_MAX_WAIT}s: {last_error}"
                    ) from e
                AutonomousOrchestrator._wait_for_ci_poll_or_shutdown(self)
                continue
            if not checks:
                # No checks configured — nothing to wait for.
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
            AutonomousOrchestrator._wait_for_ci_poll_or_shutdown(self)

    def _wait_for_ci_poll_or_shutdown(self) -> None:
        """Wait one CI poll interval while remaining interruptible by shutdown."""
        shutdown_event = getattr(self, "_shutdown_requested", None)
        if shutdown_event is None:
            time.sleep(CI_POLL_INTERVAL)
            return
        if shutdown_event.wait(CI_POLL_INTERVAL):
            raise WorkflowPaused("Service shutdown interrupted CI polling")

    def _sync_failed_pr_with_main(
        self,
        gh: GitHubOps,
        branch_name: str,
        pr_number: int,
        pr_head_sha: str,
    ) -> bool:
        """Merge current main into a stale PR before repair or final merge.

        GitHub's pull_request checks run against a synthetic merge commit. A
        stale local PR branch therefore cannot reproduce all-files checks
        faithfully. Reuse the trusted merge/conflict resolver to update and
        push the branch first; the resulting CI run becomes authoritative.

        The historical method name is retained because existing tests and
        integrations patch it, but final merge now uses the same synchronization
        gate before asking GitHub to merge.
        """
        contains_main = self._branch_contains_main(gh, pr_head_sha, branch_name)
        if contains_main is None:
            raise GitHubOpsError("Unable to verify whether the failed PR contains current main")
        if contains_main:
            return False
        logger.info(
            "PR #%s failed CI on a branch behind main; synchronizing main before AI repair",
            pr_number,
        )
        self._resolve_merge_conflicts(gh, branch_name, pr_number)
        return True

    def _branch_contains_main(
        self, gh: "GitHubOps", pr_head_sha: str, branch_name: str = ""
    ) -> bool | None:
        """Whether the PR branch already contains current main.

        Returns True if the PR head has main as an ancestor (nothing to merge),
        False if the branch is behind main, or None when the probe is
        indeterminate and the caller must fail closed.

        Issue #2045 Phase A: the pr-head existence check (``_ensure_pr_head_local``)
        and the ancestry probe (``_ancestor_check``) each delegate to
        :class:`EvidenceService`; this method composes them as before so the
        bool|None contract and the test-visible call structure are preserved.
        """
        # Ensure pr_head_sha is local first; an unverifiable head is indeterminate.
        # ``_ensure_pr_head_local`` already applies the workflow branch_name
        # fallback, so reuse it to keep the historical fetch-ref behavior.
        if not self._ensure_pr_head_local(gh, pr_head_sha, branch_name):
            return None
        gh._run_git(["fetch", "origin", "main"])
        main_head = gh.resolve_commit("FETCH_HEAD")
        return self._ancestor_check(gh, main_head, pr_head_sha)

    def _ensure_pr_head_local(
        self, gh: "GitHubOps", pr_head_sha: str, branch_name: str = ""
    ) -> bool:
        """Ensure ``pr_head_sha`` is present in the local object DB.

        ``get_pr_head_sha`` queries the GitHub API for the SHA without fetching
        the PR branch, so the object may be absent after the worktree is torn
        down (or when the head is not reachable from main). A subsequent
        ``git merge-base`` then fails with "no commit" and fails the workflow.
        Fetch the branch first so the merge-base probe has both objects.
        Returns False if the object still cannot be resolved after fetching.

        Issue #2045 Phase A: delegates to :class:`EvidenceService`.
        """
        ref = branch_name or self.workflow.get("branch_name", "")
        evidence = self._evidence.verify_commit_available(gh, pr_head_sha, ref)
        return evidence.verdict is Verdict.CONFIRMED

    def _record_trusted_head(
        self, gh: GitHubOps, *, pushed: bool = False, sha: str | None = None
    ) -> str | None:
        return self._git_workspace.record_trusted_head(gh, pushed=pushed, sha=sha)

    def _resolve_recovery_head(self, main_gh: GitHubOps, wf: dict) -> tuple[str | None, str, dict]:
        return self._git_workspace.resolve_recovery_head(main_gh, wf)

    def _start_ci_repair_round(self, wf: dict, pr_number: int, failed_checks: list[dict]) -> None:
        """Repair merge-phase CI failures in-place on the existing PR branch."""
        dev_round = int(wf.get("dev_round", 1) or 1)
        previous_attempts = int(wf.get("ci_repair_attempts", 0) or 0)
        next_attempt = previous_attempts + 1
        preferred_worktree_path = self._get_preferred_worktree_path(wf)
        gh = self._get_gh()
        current_head_sha = ""
        try:
            current_head_sha = gh.get_pr_head_sha(pr_number)
        except Exception as e:
            logger.warning(
                "Failed to resolve PR head SHA for CI repair on PR #%s: %s", pr_number, e
            )

        branch_name = (wf.get("branch_name") or "").strip()
        if (
            current_head_sha
            and branch_name
            and self._sync_failed_pr_with_main(gh, branch_name, pr_number, current_head_sha)
        ):
            # The push starts a new synthetic-merge CI run. Do not consume a
            # bounded AI repair attempt for stale-tree synchronization.
            return

        # After the non-AI main synchronization above, check the attempt limit
        # before fingerprint/log fetches (gh run view --log-failed per check).
        # Note: when BOTH "over MAX" and "signature unchanged" would match, this
        # reports "limit reached" (more accurate — the real stop reason is the
        # cap, not the unchanged signature). No downstream code depends on the
        # milestone title wording (verified via grep).
        failure_names = ", ".join(
            check.get("name") or "unknown"
            for check in failed_checks
            if check.get("bucket") == "fail"
        )

        if next_attempt > MAX_CI_REPAIR_ATTEMPTS:
            message = (
                f"PR #{pr_number} CI failed after {MAX_CI_REPAIR_ATTEMPTS} automatic repair rounds: "
                f"{failure_names}"
            )
            self._create_milestone(
                phase="merge",
                dev_round=dev_round,
                round_number=next_attempt,
                milestone_type="ci_repair_exhausted",
                status="failed",
                title="CI automatic repair limit reached",
                error_message=message,
            )
            self._update_workflow({"status": "failed", "error_message": message})
            return

        # Resolve log evidence before consuming an attempt.  A repair agent
        # that only knows "lint failed" can at best guess and historically
        # burned all three rounds fixing one superficial layer at a time.
        # Keep completed/cancelled jobs out of the actionable set and cache the
        # excerpts on the check dict so fingerprint/context construction does
        # not download each log twice.
        enriched_checks: list[dict] = []
        actionable_count = 0
        excerpt_count = 0
        for raw_check in failed_checks:
            check = dict(raw_check)
            if check.get("bucket") == "fail" and str(check.get("state") or "").lower() not in {
                "cancelled",
                "canceled",
            }:
                actionable_count += 1
                try:
                    check["failure_excerpt"] = gh.get_check_failure_excerpt(check)
                except Exception as exc:
                    logger.warning(
                        "Failed to collect CI diagnostics for PR #%s check '%s': %s",
                        pr_number,
                        check.get("name") or "unknown",
                        exc,
                    )
                    check["failure_excerpt"] = ""
                if check["failure_excerpt"]:
                    excerpt_count += 1
            enriched_checks.append(check)

        if actionable_count and excerpt_count < actionable_count:
            diagnostics_attempt = int(wf.get("ci_diagnostics_attempts", 0) or 0) + 1
            message = (
                f"PR #{pr_number} CI diagnostics incomplete "
                f"({excerpt_count}/{actionable_count} failed checks have logs; "
                f"poll {diagnostics_attempt}/{MAX_CI_DIAGNOSTICS_ATTEMPTS})"
            )
            pending_ms = self._create_milestone(
                phase="merge",
                dev_round=dev_round,
                round_number=previous_attempts,
                milestone_type="ci_diagnostics_pending",
                status="in_progress",
                title="Waiting for actionable CI failure logs",
                error_message=message,
            )
            if diagnostics_attempt >= MAX_CI_DIAGNOSTICS_ATTEMPTS:
                terminal = (
                    f"{message}. Automatic repair stopped because complete failure logs "
                    "could not be collected; verify GitHub token Actions-log permissions "
                    "and check provider URLs."
                )
                self.repo.update_milestone(
                    pending_ms.get("milestone_id", ""),
                    {"status": "failed", "error_message": terminal},
                )
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": terminal,
                        "ci_diagnostics_attempts": diagnostics_attempt,
                    }
                )
                return
            self._update_workflow(
                {
                    "current_phase": "merge",
                    "status": "merging",
                    "error_message": message,
                    "ci_diagnostics_attempts": diagnostics_attempt,
                }
            )
            return

        failed_checks = enriched_checks
        pending_ms = self._find_existing_milestone(
            phase="merge",
            milestone_type="ci_diagnostics_pending",
            dev_round=dev_round,
            round_number=previous_attempts,
        )
        if pending_ms and pending_ms.get("status") == "in_progress":
            self.repo.update_milestone(
                pending_ms.get("milestone_id", ""),
                {
                    "status": "completed",
                    "error_message": "",
                    "result_summary": (
                        f"Collected actionable logs for all {actionable_count} failed checks"
                    ),
                },
            )

        # Fine-grained fingerprint: check name + the specific error lines from
        # the CI log excerpt. A name-only signature is too coarse for Actions
        # jobs that bundle multiple tools (one ``lint`` job runs
        # black/isort/ruff/mypy) — fixing one sub-tool while another still
        # fails would leave the signature identical and wrongly give up.
        signature = self._ci_failure_fingerprint(gh, failed_checks)
        previous_signature = (wf.get("last_ci_failure_signature") or "").strip()
        previous_head_sha = (wf.get("last_ci_failure_head_sha") or "").strip()

        # The "signature unchanged → give up" guard only fires when the
        # fingerprint reflects actual CI error lines. When the excerpt was
        # unavailable (old gh CLI / token / URL-format issues), the fingerprint
        # degrades to a name-only "<no-excerpt>" sentinel that can't tell
        # whether the agent's fix changed the error set — applying the guard
        # here would misfire and kill workflows whose real failure did change
        # (#1855). Skip the guard in that case; the MAX_CI_REPAIR_ATTEMPTS
        # cap still bounds retries.
        fingerprint_is_meaningful = NO_EXCERPT_SENTINEL not in signature
        if (
            fingerprint_is_meaningful
            and previous_signature
            and signature
            and previous_signature == signature
            and previous_head_sha
            and current_head_sha
            and previous_head_sha != current_head_sha
        ):
            message = f"PR #{pr_number} CI 失败在自动修复后仍未变化: {failure_names or signature}"
            self._create_milestone(
                phase="merge",
                dev_round=dev_round,
                round_number=next_attempt,
                milestone_type="ci_repair_exhausted",
                status="failed",
                title="CI failures unchanged after automatic repair",
                error_message=message,
            )
            self._update_workflow({"status": "failed", "error_message": message})
            return

        context = self._build_ci_repair_context(wf, gh, pr_number, failed_checks)
        restore_worktree = bool(
            wf.get("branch_strategy") == "worktree"
            and not wf.get("worktree_path")
            and preferred_worktree_path
        )
        repair_wf = dict(wf)
        if restore_worktree:
            repair_wf["preferred_worktree_path"] = preferred_worktree_path
            repair_wf["worktree_path"] = preferred_worktree_path
            # Conflict resolution deliberately removes the workflow worktree
            # while its branch is attached to an isolated resolver worktree.
            # Restore the PR branch before consuming an attempt or announcing
            # that repair started. A transient fetch/worktree-add error can
            # then use the orchestrator's normal retry budget without burning
            # the bounded AI repair budget or leaving a false milestone.
            self._ensure_worktree(repair_wf)
            refreshed_wf = self.workflow
            if refreshed_wf and refreshed_wf.get("worktree_path"):
                repair_wf = refreshed_wf
            # _ensure_worktree can create/rebind the checkout. Discard the
            # main-repository GitHubOps handle so all repair commits and pushes
            # are pinned to the restored worktree.
            self._gh = None
            gh = self._get_gh()

        self._create_milestone(
            phase="merge",
            dev_round=dev_round,
            round_number=next_attempt,
            milestone_type="ci_repair_started",
            status="completed",
            title=f"CI failed for PR #{pr_number}, starting merge repair attempt {next_attempt}",
            result_summary=context[:200],
        )
        updates = {
            "current_phase": "merge",
            "status": "merging",
            "agent_pid": None,
            "agent_session_id": "",
            "error_message": "",
            "ci_repair_attempts": next_attempt,
            "ci_diagnostics_attempts": 0,
            "ci_repair_context": context,
            "last_ci_failure_signature": signature,
            "last_ci_failure_head_sha": current_head_sha,
        }
        if wf.get("branch_strategy") == "worktree" and preferred_worktree_path:
            updates["preferred_worktree_path"] = preferred_worktree_path
            updates["worktree_path"] = repair_wf.get("worktree_path") or preferred_worktree_path
        self._update_workflow(updates)
        repair_wf.update(updates)
        if not restore_worktree:
            repair_wf = self.workflow or repair_wf
        self._run_merge_ci_repair(repair_wf, gh, pr_number, failed_checks)

    def _update_workflow(self, updates: dict):
        """Update workflow and emit event."""
        self.repo.update_workflow(self._workflow_id, updates)
        self._emit("workflow_updated", updates)

    def _cleanup_worktree_and_branch(
        self,
        reason: str = "failed",
        *,
        remove_worktree: bool = True,
        remove_branch: bool = False,
    ) -> bool:
        return self._git_workspace.cleanup_worktree_and_branch(
            reason, remove_worktree=remove_worktree, remove_branch=remove_branch
        )

    def _perform_git_cleanup(self) -> tuple[str, str]:
        """Retry entry point for post-merge Git cleanup (#2043).

        Wraps :meth:`_cleanup_worktree_and_branch` and persists the
        ``cleanup_*`` tracking fields so delivery completion and resource
        convergence stay independent. Idempotent: a workflow whose worktree and
        branch are already gone reports ``completed``. Returns
        ``(status, error)`` where status is ``completed`` or ``failed``.
        """
        wf = self.workflow or {}
        attempts = int(wf.get("cleanup_attempts") or 0) + 1
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ok = self._cleanup_worktree_and_branch(
            reason="completed", remove_worktree=True, remove_branch=True
        )
        if ok:
            self._update_workflow(
                {
                    "cleanup_status": "completed",
                    "cleanup_attempts": attempts,
                    "cleanup_error": "",
                    "cleanup_updated_at": now_iso,
                    "cleanup_next_retry_at": "",
                }
            )
            # Record a cleaned_up milestone when a retry converges, so the audit
            # trail shows the eventual success (not just the prior cleanup_pending
            # failure). The immediate-success path in _do_merge records its own
            # cleaned_up milestone; this covers the retry-recovered case.
            if attempts > 1:
                self._create_milestone(
                    phase="merge",
                    milestone_type="cleaned_up",
                    status="completed",
                    title=f"Git cleanup recovered after {attempts} attempt(s)",
                )
            return "completed", ""
        # Transient/partial failure — schedule a backoff retry unless exhausted.
        error = wf.get("error_message") or "Git cleanup failed (worktree or branch)"
        status = "pending" if attempts < MAX_CLEANUP_ATTEMPTS else "failed"
        self._update_workflow(
            {
                "cleanup_status": status,
                "cleanup_attempts": attempts,
                "cleanup_error": error[:500],
                "cleanup_updated_at": now_iso,
                "cleanup_next_retry_at": _cleanup_backoff_time(attempts),
            }
        )
        return status, error

    def _mark_failed(self, error_message: str, *, phase: str | None = None) -> None:
        """Record a terminal workflow failure (status + error event) only.

        Sets ``status=failed`` and emits the error event. Worktree reclamation
        is NOT performed here: ``advance``'s post-phase convergence point (which
        runs after this AND after inline ``status=failed`` returns) is the
        SINGLE place terminal-failure worktree cleanup happens. Doing cleanup in
        both ``_mark_failed`` and the convergence point doubled the dirty-
        worktree retention note and re-ran the git-status check on every pass
        (review P2), so this method now records state only and lets the
        convergence point own the cleanup.

        #1112 timing dimension: the transient-retry path in ``advance`` returns
        before reaching here, so transient (retryable) failures keep their
        worktree for the next cycle; only terminal failures reach the
        convergence cleanup. The branch is kept (``keep_for_debug``).
        """
        self._update_workflow(
            {
                "status": "failed",
                "error_message": error_message,
                "transient_retry_count": 0,
            }
        )
        self._emit("error", {"phase": phase or "unknown", "error": error_message})

    def _accumulate_tokens(self, _result: AgentTaskResult):
        """Refresh workflow totals from the sessions linked to milestones."""
        self.repo.refresh_workflow_usage_from_sessions(self._workflow_id)

    def _persist_sandbox_attribution(self, result: AgentTaskResult) -> None:
        """Write the task's sandbox identity + final state to the workflow row.

        #2022 P5: records which SandboxProvider ran the task (provider/id/
        generation/state) so the workflow carries sandbox attribution for audit,
        the (deferred) UI, and P6 reconciliation. Best-effort: never raises to
        the caller (runs on the result path).
        """
        provider = getattr(result, "sandbox_provider", "") or ""
        sandbox_id = getattr(result, "sandbox_id", None)
        if not provider and not sandbox_id:
            return  # no sandbox ran (e.g. CLI not found before create)
        try:
            self.repo.update_workflow(
                self._workflow_id,
                {
                    "sandbox_provider": provider,
                    "sandbox_id": sandbox_id,
                    "sandbox_generation": getattr(result, "sandbox_generation", None),
                    "sandbox_state": getattr(result, "sandbox_state", "") or None,
                },
            )
        except Exception as e:  # pragma: no cover - best-effort attribution
            logger.warning(
                "Failed to persist sandbox attribution for %s: %s", self._workflow_id[:8], e
            )

    def _shadow_compare_evidence(
        self,
        *,
        test_result: AgentTaskResult,
        milestone_id: str,
        heuristic_passed: bool,
        structured_verdict: ExecutionVerdict | None = None,
    ) -> None:
        """Compare the heuristic test verdict against structured evidence (#2046).

        Two divergence layers are recorded as ``evidence_shadow_divergence``
        workflow events:

        - Command-level (Phase A): ``compare_verdicts`` over
          ``CommandExecutionEvidence`` rows — whether every recorded command
          passed. Catches missing evidence (a heuristic pass with no commands).
        - Run-level (Phase B): the structured ``ExecutionVerdict`` (from
          ``compute_run_verdict`` over ``TestExecutionEvidence``) vs the
          heuristic. This verdict now drives the gate; divergence here is the
          signal for whether the heuristic fallback (#2046 §4) can be retired.
        """
        try:
            import json

            from app.modules.workspace.autonomous.command_evidence.recorder import (
                compare_verdicts,
                get_command_evidence_recorder,
            )

            recorder = get_command_evidence_recorder()
            evidence_rows = []
            if not recorder.is_noop:
                recorder.flush(timeout=1.0)
                session_id = test_result.session_id or test_result.tracking_session_id or ""
                if session_id:
                    evidence_rows = recorder.repo.query_by_session(session_id)
            comparison = compare_verdicts(
                heuristic_passed=heuristic_passed, evidence_rows=evidence_rows
            )

            structured_value = (
                structured_verdict.value
                if structured_verdict is not None
                else comparison.get("structured_verdict")
            )
            # Run-level divergence: structured PASSED/FAILED disagrees with the
            # heuristic. NOT_RUN/INCONCLUSIVE means the structured layer deferred
            # — only a divergence if the heuristic nonetheless claimed a pass.
            structured_pass = structured_value == ExecutionVerdict.PASSED.value
            run_level_divergence = (
                structured_verdict is not None and structured_pass != heuristic_passed
            )

            if not comparison.get("divergence") and not run_level_divergence:
                return

            self.repo.create_event(
                {
                    "workflow_id": self._workflow_id,
                    "milestone_id": milestone_id,
                    "event_type": "evidence_shadow_divergence",
                    "event_data": json.dumps(
                        {
                            "heuristic_verdict": comparison.get("heuristic_verdict"),
                            "structured_command_verdict": comparison.get("structured_verdict"),
                            "structured_run_verdict": structured_value,
                            "reason": comparison.get("reason"),
                            "command_verdicts": comparison.get("command_verdicts", []),
                            "evidence_count": len(evidence_rows),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            logger.warning(
                "evidence shadow divergence for %s: heuristic=%s structured_run=%s (%s)",
                self._workflow_id,
                comparison.get("heuristic_verdict"),
                structured_value,
                comparison.get("reason", ""),
            )
        except Exception as e:
            logger.debug("evidence shadow comparison failed: %s", e)

    def _compute_structured_test_verdict(
        self,
        test_result: AgentTaskResult,
        framework_type: str,
        test_ms: dict,
    ) -> tuple[ExecutionVerdict, list, str]:
        """Parse command evidence into TestExecutionEvidence and compute the run verdict.

        #2046 Phase B authoritative path: reads the ``CommandExecutionEvidence``
        rows dual-written in Phase A, parses each test command's output into a
        ``TestExecutionEvidence`` row (persisted), and aggregates them via
        ``compute_run_verdict``. Returns ``(verdict, evidences, reason)``.

        Best-effort: any failure returns ``NOT_RUN`` with a reason so the gate
        falls back to the legacy heuristic — the structured layer never raises
        into the gate path.
        """
        try:
            from app.modules.workspace.autonomous.command_evidence.recorder import (
                get_command_evidence_recorder,
            )
            from app.modules.workspace.autonomous.command_evidence.test_evidence import (
                parse_test_evidence,
            )
            from app.modules.workspace.autonomous.command_evidence.test_verdict import (
                compute_run_verdict,
            )
            from app.repositories.command_evidence_repo import CommandExecutionEvidenceRepository
            from app.repositories.test_evidence_repo import TestExecutionEvidenceRepository

            recorder = get_command_evidence_recorder()
            if not recorder.is_noop:
                recorder.flush(timeout=1.0)

            session_id = test_result.session_id or test_result.tracking_session_id or ""
            if not session_id:
                return ExecutionVerdict.NOT_RUN, [], "no session id on test result"

            command_evidences = CommandExecutionEvidenceRepository().query_by_session(session_id)
            if not command_evidences:
                return ExecutionVerdict.NOT_RUN, [], "no command execution evidence"

            # Only parse commands that look like test invocations; an ``echo``
            # or ``ls`` must not be fed to the generic parser as if it were a
            # test run (#2046 — agent prose / non-test commands cannot judge).
            test_command_evidences = [
                ce
                for ce in command_evidences
                if _has_test_tool_call(
                    [
                        {
                            "tool": {
                                "name": ce.tool_name or "",
                                "input": {"command": ce.shell_command or ""},
                            }
                        }
                    ],
                    framework_type,
                )
            ]
            if not test_command_evidences:
                return ExecutionVerdict.NOT_RUN, [], "no test command evidence"

            test_repo = TestExecutionEvidenceRepository()
            test_evidences: list = []
            for command_evidence in test_command_evidences:
                parsed = parse_test_evidence(command_evidence, framework_hint=framework_type)
                test_repo.upsert(parsed)
                test_evidences.append(parsed)

            verdict = compute_run_verdict(test_evidences, framework_type)
            return verdict, test_evidences, f"parsed {len(test_evidences)} test command(s)"
        except Exception as e:
            logger.debug("structured test verdict computation failed: %s", e)
            return ExecutionVerdict.NOT_RUN, [], f"compute failed: {e}"

    def _emit_structured_test_fallback(
        self,
        verdict: ExecutionVerdict,
        reason: str,
        milestone_id: str,
    ) -> None:
        """Record that the structured verdict deferred to the heuristic.

        Emitted whenever ``compute_run_verdict`` returns NOT_RUN/INCONCLUSIVE
        and the gate falls back to ``_has_passing_test_tool_result`` (#2046 §4
        降级). Tracked so parser coverage gaps are visible and the fallback can
        be retired once shadow data proves the structured layer is sufficient.
        """
        try:
            import json

            self.repo.create_event(
                {
                    "workflow_id": self._workflow_id,
                    "milestone_id": milestone_id,
                    "event_type": "structured_test_fallback",
                    "event_data": json.dumps(
                        {
                            "structured_verdict": verdict.value,
                            "reason": reason,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            logger.info(
                "structured test verdict deferred to heuristic for %s: verdict=%s (%s)",
                self._workflow_id,
                verdict.value,
                reason,
            )
        except Exception as e:
            logger.debug("structured fallback event emit failed: %s", e)

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

    def _on_sandbox_created(
        self,
        session_id: str,
        sandbox_id: str,
        provider_name: str,
        remote_session_id: str | None,
        effective_policy: dict | None = None,
    ) -> None:
        """Persist mid-run sandbox identity so a crash leaves a reconcilable row (#2022 P6).

        Sandbox-lifecycle mirror of ``_on_pid_registered``: write
        ``sandbox_state='running'`` + sandbox_id/provider between exec and task
        completion. A crash here leaves an orphan the startup/periodic reconciler
        destroys by ``sandbox_remote_session_id`` (remote) or DB-resets (local).
        The remote id is written only when present so a local row never carries a
        stale/NULL remote id. ``effective_policy`` (#2020 Phase B) is the
        JSON-serializable resource/isolation snapshot built by the runner; when
        present it is persisted as ``sandbox_effective_policy`` so the workflow
        detail UI shows what was actually in effect for the run. Best-effort:
        never raises to the run path.
        """
        try:
            updates: dict[str, object] = {
                "sandbox_state": "running",
                "sandbox_id": sandbox_id,
                "sandbox_provider": provider_name,
            }
            if remote_session_id is not None:
                updates["sandbox_remote_session_id"] = remote_session_id
            if effective_policy is not None:
                updates["sandbox_effective_policy"] = json.dumps(effective_policy)
            self.repo.update_workflow(self._workflow_id, updates)
            logger.info(
                "Registered sandbox %s (provider=%s) for workflow %s%s",
                sandbox_id[:8],
                provider_name,
                self._workflow_id[:8],
                f" remote_session={remote_session_id[:8]}" if remote_session_id else "",
            )
        except Exception as e:
            logger.warning("Failed to persist mid-run sandbox state: %s", e)

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
        # A thinking-token event is a high-frequency cumulative estimate, not
        # a discrete activity or authoritative usage. Keep it out of SSE even
        # when a runner other than the local Claude path emits one.
        if activity.get("type") == "system" and activity.get("subtype") == "thinking_tokens":
            return

        emitted_activity = activity
        if activity.get("type") == "usage":
            offsets = getattr(self, "_session_usage_offsets", {})
            session_lock = getattr(self, "_session_lock", None)
            if session_lock is None:
                offset = dict(offsets.get(session_id, {}))
            else:
                with session_lock:
                    offset = dict(offsets.get(session_id, {}))
            emitted_activity = {
                **activity,
                "total_tokens": int(activity.get("total_tokens", 0) or 0)
                + int(offset.get("total_tokens", 0) or 0),
                "total_input_tokens": int(activity.get("total_input_tokens", 0) or 0)
                + int(offset.get("total_input_tokens", 0) or 0),
                "total_output_tokens": int(activity.get("total_output_tokens", 0) or 0)
                + int(offset.get("total_output_tokens", 0) or 0),
                "request_count": int(activity.get("request_count", 0) or 0)
                + int(offset.get("request_count", 0) or 0),
            }

        self.emitter.emit(
            self._workflow_id,
            "agent_activity",
            {
                "session_id": session_id,
                **emitted_activity,
            },
        )
        activity_type = emitted_activity.get("type")
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
                self._write_realtime_phase_usage(emitted_activity)
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
        """Whether a structured review result unambiguously approves the PR.

        ``approval_text`` stays in the signature for call-site compatibility,
        but localized prose is never authoritative for a new review. Missing or
        malformed structured output fails closed.
        """
        del approval_text
        result = _parse_review_result(review_text)
        return bool(result and result["verdict"] == "APPROVE" and not result["blocking_findings"])

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

    @classmethod
    def _should_retry_transient_api_failure(cls, result: AgentTaskResult) -> bool:
        """Return whether *result* represents a real transient API failure.

        Explicit runner errors are always eligible. Some Claude versions put
        an upstream error envelope in assistant text, but that fallback is
        safe only when the call produced no tokens. Scanning token-bearing
        plan/review text caused phrases such as "Rate Limit" to restart an
        otherwise successful phase (#1891).
        """
        if cls._is_upstream_hard_quota_exhausted(result):
            return False
        if cls._is_transient_api_error(result.error or ""):
            return True
        return (result.total_tokens or 0) == 0 and cls._is_transient_api_error(
            result.response_text or ""
        )

    @staticmethod
    def _is_upstream_hard_quota_exhausted(result: AgentTaskResult) -> bool:
        """Return whether a provider reports a non-transient hard quota.

        Error fields are authoritative. Assistant text is considered only for
        a zero-token envelope so prose discussing quota handling cannot pause
        a workflow. Bailian's allocated-quota rate limit is intentionally not
        part of the hard-quota pattern.
        """
        if _UPSTREAM_HARD_QUOTA_EXHAUSTED_RE.search(result.error or ""):
            return True
        return (result.total_tokens or 0) == 0 and bool(
            _UPSTREAM_HARD_QUOTA_EXHAUSTED_RE.search(result.response_text or "")
        )

    def _pause_for_upstream_quota(self, result: AgentTaskResult, milestone_id: str = "") -> None:
        """Persist a non-spinning hard-quota pause that an operator may resume."""
        message = (
            f"{UPSTREAM_QUOTA_PAUSE_REASON_PREFIX} "
            "the configured model provider rejected requests; resume after "
            "provider allocation is restored"
        )
        result.success = False
        result.error_code = "upstream_quota_exhausted"
        result.error = message
        if milestone_id:
            self.repo.update_milestone(
                milestone_id,
                {
                    "status": "failed",
                    "session_id": result.session_id,
                    "error_message": message,
                },
            )
        self._update_workflow(
            {
                "status": "paused",
                "error_message": message,
                "paused_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "agent_pid": None,
            }
        )
        self._emit(
            "upstream_quota_paused",
            {"reason": "upstream_quota_exhausted", "message": message},
        )

    @staticmethod
    def _is_context_overflow(result: AgentTaskResult) -> bool:
        """True if the agent call failed because the resumed session context
        exceeded the model's input-token limit (e.g. GLM ``400
        InvalidParameter: Range of input length``).

        Such failures are recoverable: the agent produced nothing because the
        very first API call was rejected, so retrying on a fresh
        minimal-context session (no ``--resume``) can succeed. This is a
        permanent client error (NOT transient), so it deliberately does not
        match ``_TRANSIENT_API_ERROR_RE`` and won't trigger the backoff loop.
        """
        body = f"{result.error or ''}\n{result.response_text or ''}"
        if not _CONTEXT_OVERFLOW_RE.search(body):
            return False
        if not result.success:
            return True

        # Some Claude-compatible providers emit a terminal ``API Error`` text
        # envelope but the CLI process still exits zero.  Treat that exact
        # provider envelope as failure while avoiding broad scans of otherwise
        # successful, token-bearing prose (which previously caused false
        # retries when an agent merely discussed rate limits).
        visible = (result.response_text or "").strip()
        return bool(re.match(r"^API Error:\s*400\b", visible, re.IGNORECASE))

    def _run_agent_with_context_recovery(
        self,
        wf: dict,
        *,
        session_line: str,
        milestone_id: str = "",
        **kwargs,
    ) -> AgentTaskResult:
        """Retry one overflowing stable line without creating a fourth session.

        The workflow's main/review/test tracking ids are permanent identities.
        When a provider transcript is too large, clear only that tracking row's
        provider mapping and run the same self-contained prompt without resume.
        The runner then rebinds the new provider transcript to the SAME tracking
        row. Usage from the rejected attempt is carried into the retry's final
        milestone write; the caller accounts the returned result as usual.
        """
        result = self._run_agent(
            wf=wf,
            session_line=session_line,
            milestone_id=milestone_id,
            **kwargs,
        )
        if not self._is_context_overflow(result):
            return result

        field = SESSION_LINE_FIELDS.get(session_line)
        if not field:
            return result

        prior_usage = {
            "total_tokens": int(result.total_tokens or 0),
            "total_input_tokens": int(result.total_input_tokens or 0),
            "total_output_tokens": int(result.total_output_tokens or 0),
            "request_count": int(result.request_count or 0),
        }
        # _run_agent may itself have consumed transient retries before its
        # final attempt overflowed. Those attempts live in its local
        # retry_usage and have already been persisted to phase_*; they are not
        # represented by the returned final AgentTaskResult. Carry forward the
        # milestone aggregate so the recovery write cannot erase them.
        if milestone_id:
            try:
                milestone = self.repo.get_milestone(milestone_id)
                if isinstance(milestone, dict):
                    for usage_key, phase_key in (
                        ("total_tokens", "phase_total_tokens"),
                        ("total_input_tokens", "phase_input_tokens"),
                        ("total_output_tokens", "phase_output_tokens"),
                        ("request_count", "phase_request_count"),
                    ):
                        prior_usage[usage_key] = max(
                            prior_usage[usage_key], int(milestone.get(phase_key, 0) or 0)
                        )
            except Exception:
                # The final result remains a safe lower bound. A repository
                # read outage must not make context recovery lose even that
                # attempt's usage.
                logger.warning(
                    "Failed to read aggregate phase usage before context recovery",
                    exc_info=True,
                )
        self._accumulate_tokens(result)
        logger.warning(
            "Session line %s exceeded provider context; rebinding it and retrying once",
            session_line,
        )
        tracking_session_id = ((wf or {}).get(field) or "").strip()
        if not tracking_session_id:
            tracking_session_id = ((self.workflow or {}).get(field) or "").strip()
        session_manager = getattr(self._runner, "session_manager", None)
        if tracking_session_id and session_manager is not None:
            try:
                session_row = session_manager.get_session(tracking_session_id) or {}
                context = getattr(session_row, "context", None)
                if not isinstance(context, dict):
                    context = (
                        session_row.get("context", {}) if isinstance(session_row, dict) else {}
                    )
                context = dict(context or {})
                context.pop("cli_session_id", None)
                if not session_manager.update_session_fields(
                    tracking_session_id,
                    {
                        "cli_session_id": "",
                        "context": context,
                        "status": "active",
                    },
                ):
                    raise RuntimeError("tracking session row was not updated")
            except Exception as exc:
                logger.error(
                    "Failed to clear provider context for session line %s",
                    session_line,
                    exc_info=True,
                )
                result.success = False
                result.error = f"Context recovery could not rebind {session_line} session: {exc}"
                return result

        self._update_workflow({"agent_session_id": "", "agent_pid": None})
        return self._run_agent(
            wf=wf,
            session_line=session_line,
            milestone_id=milestone_id,
            force_fresh=True,
            prior_usage=prior_usage,
            **kwargs,
        )

    def _run_agent(
        self,
        wf: dict = None,
        *,
        session_line: str = "fresh",
        milestone_id: str = "",
        force_fresh: bool = False,
        prior_usage: dict[str, int] | None = None,
        **kwargs,
    ) -> AgentTaskResult:
        """Run an agent task with session-line tracking and transient-API-error retry.

        Args:
            wf: Optional pre-fetched workflow dict to avoid extra DB queries.
                If not provided, falls back to self.workflow (DB query).
            session_line: Which session line this call belongs to — "main",
                "review", "test" (resumed across milestones via --resume), or
                "fresh" (a brand-new one-off session).
            milestone_id: Milestone to attribute this call's phase usage to.
            force_fresh: Start a new provider transcript while retaining a
                named line's stable tracking id.
            prior_usage: Usage from a preceding attempt that belongs to the
                same milestone and must be included in the final phase totals.
        """
        workflow_data = wf or self.workflow
        field = SESSION_LINE_FIELDS.get(session_line)

        # Resolve the session line: resume an established session or start new.
        # Context recovery forces a fresh provider transcript while retaining
        # the named line's stable tracking id.
        if force_fresh and field:
            session_id = ((workflow_data or {}).get(field) or "").strip()
            if not session_id:
                session_id = ((self.workflow or {}).get(field) or "").strip()
            session_id = session_id or str(uuid.uuid4())
            resume_session_id = None
            resume = False
        else:
            session_id, resume_session_id, resume = self._resolve_session_line(
                workflow_data, session_line
            )
        kwargs["session_id"] = session_id
        kwargs["resume"] = resume
        kwargs["resume_session_id"] = resume_session_id
        if milestone_id:
            kwargs["milestone_id"] = milestone_id
        tracking_session_id = session_id
        prior_usage = prior_usage or {}
        retry_usage = {
            "total_tokens": int(prior_usage.get("total_tokens", 0) or 0),
            "total_input_tokens": int(prior_usage.get("total_input_tokens", 0) or 0),
            "total_output_tokens": int(prior_usage.get("total_output_tokens", 0) or 0),
            "request_count": int(prior_usage.get("request_count", 0) or 0),
        }
        usage_session_ids = {tracking_session_id}
        if self._is_shutdown_requested():
            self._abort_agent_run_for_shutdown(
                milestone_id,
                AgentTaskResult(
                    session_id=tracking_session_id,
                    tracking_session_id=tracking_session_id,
                    success=False,
                ),
                retry_usage,
                usage_session_ids,
                tracking_session_id,
            )
        if "user_id" not in kwargs and workflow_data:
            kwargs["user_id"] = workflow_data.get("user_id")

        # Resolve the repository owner first.  GitHubOps continues to run as
        # that identity, while the agent itself is switched below to a
        # credentialless dedicated principal.
        if "system_account" not in kwargs and workflow_data:
            kwargs["system_account"] = self._resolve_system_account(workflow_data)
        project_system_account = kwargs.get("system_account")

        repo_context = self._resolve_effective_repo_context(workflow_data)
        effective_project_path = repo_context.get("repo_path") or kwargs.get("project_path", "")
        if effective_project_path:
            kwargs["project_path"] = effective_project_path
        if kwargs.get("workspace_type", "local") != "remote":
            runtime_command, runtime_error = self._select_project_python_runtime(
                effective_project_path, self._get_gh()
            )
            if runtime_command:
                kwargs["runtime_python_command"] = runtime_command
            elif runtime_error:
                logger.warning("Autonomous runtime binding unavailable: %s", runtime_error)
        repo_state_before = self._snapshot_repo_context(
            workflow_data,
            kwargs.get("workspace_type", "local"),
            project_system_account,
        )
        if (
            kwargs.get("workspace_type", "local") == "local"
            and effective_project_path
            and repo_state_before is None
        ):
            return AgentTaskResult(
                session_id=tracking_session_id,
                tracking_session_id=tracking_session_id,
                success=False,
                error_code="repo_integrity_violation",
                error="Cannot establish a trusted Git context before agent execution",
            )

        # Pin every privileged git operation to the gitdir resolved before the
        # agent starts.  A writable worktree root lets an editor replace the
        # .git directory entry even when the metadata itself is read-only; the
        # trusted context prevents that replacement from redirecting later
        # owner-credentialed commits or pushes.
        if repo_state_before:
            for state in (
                repo_state_before.get("effective"),
                repo_state_before.get("main"),
            ):
                if not isinstance(state, dict) or not state.get("git_dir"):
                    continue
                GitHubOps.register_trusted_git_context(
                    state.get("repo_path", ""),
                    state.get("git_dir", ""),
                    state.get("git_identity", ""),
                    state.get("common_dir", ""),
                    state.get("common_identity", ""),
                )
            current_gh = self._get_gh()
            effective_state = repo_state_before.get("effective") or {}
            if current_gh is not None and os.path.realpath(
                current_gh.repo_path
            ) == os.path.realpath(effective_state.get("repo_path", "")):
                current_gh.bind_trusted_git_context(
                    effective_state.get("git_dir", ""),
                    effective_state.get("git_identity", ""),
                    effective_state.get("common_dir", ""),
                    effective_state.get("common_identity", ""),
                )

        if kwargs.get("workspace_type", "local") == "local":
            origin = str(((repo_state_before or {}).get("effective") or {}).get("origin") or "")
            if re.match(r"^https?://[^/]*@", origin, re.IGNORECASE):
                return AgentTaskResult(
                    session_id=tracking_session_id,
                    tracking_session_id=tracking_session_id,
                    success=False,
                    error=(
                        "Autonomous agent isolation refused a repository whose origin URL "
                        "contains embedded credentials; configure a credential helper instead"
                    ),
                )
            # Engage the credentialless isolated-agent principal only when the
            # privileged launcher (openace-run-as --isolated) is actually
            # provisioned. The launcher is Linux-only (setfacl/getent/runuser)
            # and absent on dev/macOS installs where the service already runs as
            # the repo owner; in that same-user case isolation provides no
            # benefit and cannot run. Mirrors the Windows single-user downgrade.
            if AutonomousAgentRunner.is_isolated_launcher_available():
                isolated_account = self._resolve_isolated_agent_account()
                try:
                    service_account = pwd.getpwuid(os.getuid()).pw_name
                except (KeyError, OverflowError):
                    service_account = ""
                if isolated_account in {project_system_account, service_account}:
                    return AgentTaskResult(
                        session_id=tracking_session_id,
                        tracking_session_id=tracking_session_id,
                        success=False,
                        error=(
                            "Autonomous agent isolation is invalid: the credentialless agent "
                            "account must differ from the service and repository owner accounts"
                        ),
                    )
                kwargs["system_account"] = isolated_account
            else:
                logger.warning(
                    "Workflow %s: isolated agent launcher %s unavailable; running the "
                    "agent as the repository owner (%s) in same-user mode. This is expected "
                    "on dev/macOS installs where the service runs as the repo owner; "
                    "isolated agent mode requires the openace-run-as launcher "
                    "(Linux multi-user only).",
                    self._workflow_id,
                    AutonomousAgentRunner.isolated_launcher_path(),
                    project_system_account or "(unknown)",
                )

        with self._session_lock:
            self._current_session_id = tracking_session_id
            self._session_usage_offsets[tracking_session_id] = dict(retry_usage)

        # The tracking id is the stable UI identity for every session line and
        # is known before the blocking runner call starts. Link the exact
        # milestone now, including Claude/sidebar runs, so live activity can be
        # attributed from the first event instead of only after process exit.
        if milestone_id and tracking_session_id:
            milestone_session_field = (
                "review_session_id" if session_line == "review" else "session_id"
            )
            try:
                self.repo.update_milestone(
                    milestone_id,
                    {milestone_session_field: tracking_session_id},
                )
            except Exception:
                logger.warning("Failed to pre-link session to milestone", exc_info=True)

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
            kwargs["prompt"] = (
                kwargs["prompt"]
                + self._build_repo_execution_contract(workflow_data)
                + build_language_instruction(content_language)
            )

        if self._is_shutdown_requested():
            self._abort_agent_run_for_shutdown(
                milestone_id,
                AgentTaskResult(
                    session_id=tracking_session_id,
                    tracking_session_id=tracking_session_id,
                    success=False,
                ),
                retry_usage,
                usage_session_ids,
                tracking_session_id,
            )
        result = self._runner.run_agent_task(**kwargs)
        if result.session_id:
            # The runner is authoritative for the tracking id it actually
            # persisted (notably app-server adapters that create sessions
            # after dispatch). Adopt it once, then keep it stable for every
            # retry on this named line.
            resolved_tracking_session_id = result.tracking_session_id or result.session_id
            if resolved_tracking_session_id != tracking_session_id:
                with self._session_lock:
                    offset = self._session_usage_offsets.pop(tracking_session_id, retry_usage)
                    self._session_usage_offsets[resolved_tracking_session_id] = offset
                    self._current_session_id = resolved_tracking_session_id
                usage_session_ids.add(resolved_tracking_session_id)
                tracking_session_id = resolved_tracking_session_id

            # Workflow milestones must keep the stable session-line tracking id
            # (main/review/test), not the provider's real CLI id. The timeline
            # resolves the actual transcript session at read time via
            # agent_sessions.cli_session_id, which keeps DB identity stable
            # while still letting the UI open the real transcript.
            link_session_id = (
                tracking_session_id if field else (result.tracking_session_id or result.session_id)
            )
            self._link_session_to_current_milestone(link_session_id)
            # Persist the stable tracking id for this line. Fresh lines write
            # their first tracking id here; established lines keep reusing the
            # same wrapper row across milestones so timeline/session identity
            # does not drift with every resume attempt.
            if field:
                self._update_workflow({field: tracking_session_id})
                # Keep the in-memory wf dict in sync so the next _resolve_session_line
                # call in the same phase (e.g. planning → finalize) sees the updated
                # line identity and resumes it instead of rotating wrappers.
                if workflow_data is not None:
                    workflow_data[field] = tracking_session_id

        if self._is_shutdown_requested():
            self._abort_agent_run_for_shutdown(
                milestone_id,
                result,
                retry_usage,
                usage_session_ids,
                tracking_session_id,
            )

        # Transient API error retry (429 / 5xx / overload) — exponential
        # backoff, max 30 minutes total. Interruptible sleep (cancel check
        # every 5s) so the orchestrator can be paused/stopped during a wait.
        _CANCEL_POLL_INTERVAL = 5  # seconds between cancel checks
        retry_start = time.monotonic()
        delay = API_RETRY_INITIAL_DELAY

        def abort_paused_retry() -> None:
            """Finalize retry bookkeeping before unwinding a manual pause."""
            logger.info("API error retry aborted (workflow status=paused)")
            if milestone_id:
                self.repo.update_milestone(
                    milestone_id,
                    {
                        "status": "cancelled",
                        "session_id": result.session_id,
                        "error_message": "Workflow paused during API error retry",
                    },
                )
            self._write_phase_usage(milestone_id, result, retry_usage)
            self._clear_session_usage_offsets(usage_session_ids)
            with self._session_lock:
                self._current_session_id = (
                    tracking_session_id
                    if field
                    else (result.tracking_session_id or result.session_id or tracking_session_id)
                )
            raise WorkflowPaused("Workflow paused during API error retry")

        while (time.monotonic() - retry_start) < API_RETRY_TOTAL_TIMEOUT:
            if self._is_shutdown_requested():
                self._abort_agent_run_for_shutdown(
                    milestone_id,
                    result,
                    retry_usage,
                    usage_session_ids,
                    tracking_session_id,
                )
            # Re-check workflow status each iteration: a failure/cancellation
            # set on the row (by a concurrent path or a prior failure in this
            # advance()) must abort retries, otherwise we keep spawning agent
            # subprocesses on a dead workflow for the full 30-min window. #1029
            _status = (self.workflow or {}).get("status")
            if _status == "paused":
                abort_paused_retry()
            if _status in ("failed", "cancelled"):
                logger.info("API error retry aborted (workflow status=%s)", _status)
                self._synthesize_transient_failure(result)
                self._write_phase_usage(milestone_id, result, retry_usage)
                self._clear_session_usage_offsets(usage_session_ids)
                with self._session_lock:
                    self._current_session_id = (
                        tracking_session_id
                        if field
                        else (
                            result.tracking_session_id or result.session_id or tracking_session_id
                        )
                    )
                return result

            if not self._should_retry_transient_api_failure(result):
                break  # Not a transient API error, no retry needed

            elapsed = int(time.monotonic() - retry_start)
            retry_source = (
                "result.error"
                if self._is_transient_api_error(result.error or "")
                else "zero-token response"
            )
            logger.warning(
                "Transient API retry in %ds (elapsed=%ds/%ds, source=%s)",
                delay,
                elapsed,
                API_RETRY_TOTAL_TIMEOUT,
                retry_source,
            )
            self._emit(
                "api_error_retry",
                {
                    "delay": delay,
                    "elapsed": elapsed,
                    "total_timeout": API_RETRY_TOTAL_TIMEOUT,
                    "source": retry_source,
                },
            )

            # Interruptible sleep: check cancellation and manual pause every
            # five seconds so a long backoff cannot wake up and launch another
            # agent after the workflow row was paused.
            slept = 0
            self._cancel_requested.clear()
            # Shutdown may race exactly between the loop-top check and the
            # clear above.  The shutdown event is monotonic and authoritative;
            # re-check it directly so clearing the shared cancel event cannot
            # consume SIGTERM and let a new retry dispatch.
            if self._is_shutdown_requested():
                self._abort_agent_run_for_shutdown(
                    milestone_id,
                    result,
                    retry_usage,
                    usage_session_ids,
                    tracking_session_id,
                )
            while slept < delay:
                time.sleep(min(_CANCEL_POLL_INTERVAL, delay - slept))
                slept += _CANCEL_POLL_INTERVAL
                if (self.workflow or {}).get("status") == "paused":
                    abort_paused_retry()
                if self._is_shutdown_requested():
                    self._abort_agent_run_for_shutdown(
                        milestone_id,
                        result,
                        retry_usage,
                        usage_session_ids,
                        tracking_session_id,
                    )
                if self._cancel_requested.is_set():
                    logger.info("API error retry cancelled (cancel requested)")
                    self._synthesize_transient_failure(result)
                    self._write_phase_usage(milestone_id, result, retry_usage)
                    self._clear_session_usage_offsets(usage_session_ids)
                    with self._session_lock:
                        self._current_session_id = (
                            tracking_session_id
                            if field
                            else (
                                result.tracking_session_id
                                or result.session_id
                                or tracking_session_id
                            )
                        )
                    return result

            delay = min(delay * 2, API_RETRY_MAX_DELAY)

            # AgentTaskResult's runner contract is usage for one
            # run_agent_task invocation: Claude reports per-request values and
            # cumulative-result adapters difference turns before building the
            # result. Any provider that replays pre-resume history must
            # normalize that in its adapter; the orchestrator sums only these
            # per-invocation deltas.
            for key in retry_usage:
                retry_usage[key] += int(getattr(result, key, 0) or 0)

            # Strict main/review/test topology: retries reuse the line's stable
            # tracking id and, when possible, the provider transcript created
            # by the preceding attempt. One-off "fresh" calls may rotate.
            retry_session_id = tracking_session_id if field else str(uuid.uuid4())
            kwargs["session_id"] = retry_session_id
            if field:
                resume_target = result.source_session_id or kwargs.get("resume_session_id")
                if not resume_target and not self._runner._uses_sidebar_session_source(
                    kwargs.get("cli_tool", ""), kwargs.get("workspace_type", "local")
                ):
                    resume_target = tracking_session_id
                if resume_target:
                    kwargs["resume"] = True
                    kwargs["resume_session_id"] = resume_target
            usage_session_ids.add(retry_session_id)
            with self._session_lock:
                self._current_session_id = retry_session_id
                self._session_usage_offsets[retry_session_id] = dict(retry_usage)
            self._link_session_to_current_milestone(
                tracking_session_id if field else retry_session_id
            )
            result = self._runner.run_agent_task(**kwargs)
            if result.session_id:
                self._link_session_to_current_milestone(
                    tracking_session_id
                    if field
                    else (result.tracking_session_id or result.session_id)
                )

        # Session-resume failure recovery: if the agent failed because it
        # couldn't resume a stale or inaccessible session (EPERM on macOS
        # ``com.apple.provenance`` xattr, or "No conversation found" for a
        # deleted/mismatched session id), clear the stale cli_session_id
        # mapping and retry once with a fresh session on the same tracking
        # line. This prevents permanent workflow failure from
        # transient/environmental session-access issues (#2035).
        if (
            not result.success
            and field
            and result.error_code in ("resume_session_not_found", "resume_session_failed")
            and not self._is_shutdown_requested()
            and (self.workflow or {}).get("status") not in ("failed", "cancelled", "paused")
        ):
            logger.warning(
                "Session resume failed (error_code=%s) on line %s; clearing "
                "cli_session_id mapping and retrying with a fresh session",
                result.error_code,
                session_line,
            )
            self._emit(
                "session_resume_recovery",
                {
                    "error_code": result.error_code,
                    "session_line": session_line,
                },
            )
            # Clear the stale cli_session_id so _resolve_session_line on the
            # next advance() does not pick up the same broken resume target.
            try:
                if (
                    getattr(self._runner, "session_manager", None) is not None
                    and tracking_session_id
                ):
                    self._runner.session_manager.update_session_fields(
                        tracking_session_id, {"cli_session_id": ""}
                    )
            except Exception:
                logger.warning(
                    "Failed to clear cli_session_id for %s",
                    tracking_session_id[:8],
                    exc_info=True,
                )
            # Accumulate usage from the failed attempt so tokens aren't lost.
            for key in retry_usage:
                retry_usage[key] += int(getattr(result, key, 0) or 0)
            # Refresh session bookkeeping to match the retry-loop pattern so a
            # concurrent reader does not observe a stale _current_session_id
            # during the recovery retry, and _session_usage_offsets carries the
            # accumulated totals for _write_phase_usage (#2035 follow-up).
            kwargs["session_id"] = tracking_session_id
            kwargs["resume"] = False
            kwargs["resume_session_id"] = None
            usage_session_ids.add(tracking_session_id)
            with self._session_lock:
                self._current_session_id = tracking_session_id
                self._session_usage_offsets[tracking_session_id] = dict(retry_usage)
            result = self._runner.run_agent_task(**kwargs)
            if result.session_id:
                self._link_session_to_current_milestone(tracking_session_id)

        # A transient-error body (e.g. a 529 "overloaded" returned as
        # assistant_text with no tokens generated) must not be handed back as a
        # success — callers would store it as plan/review content. The tokens==0
        # gate avoids flagging a legitimate plan that merely mentions these
        # phrases. #1001. Centralized in a helper so the retry loop's early-exit
        # paths (status failed/cancelled, cancel-requested) apply it too. #1036.
        self._synthesize_transient_failure(result)
        repo_validation_error = self._validate_repo_context_after_run(
            repo_state_before, project_system_account
        )
        if repo_validation_error:
            logger.error("Workflow repo validation failed: %s", repo_validation_error)
            result.success = False
            result.error_code = "repo_integrity_violation"
            result.error = repo_validation_error
        # Apply repository validation to the result before observing shutdown.
        # Otherwise SIGTERM racing with this validation window could downgrade
        # a newly detected integrity violation into a retryable cancellation.
        if self._is_shutdown_requested():
            self._abort_agent_run_for_shutdown(
                milestone_id,
                result,
                retry_usage,
                usage_session_ids,
                tracking_session_id,
            )
        upstream_hard_quota_exhausted = self._is_upstream_hard_quota_exhausted(result)

        # Attribute this call's own usage to its milestone (increment, not cumulative).
        self._write_phase_usage(milestone_id, result, retry_usage)
        self._clear_session_usage_offsets(usage_session_ids)

        with self._session_lock:
            self._current_session_id = (
                tracking_session_id
                if field
                else (result.tracking_session_id or result.session_id or tracking_session_id)
            )
        if upstream_hard_quota_exhausted:
            self._pause_for_upstream_quota(result, milestone_id)
            raise UpstreamQuotaPaused(result.error)
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
        if not (result.success and self._should_retry_transient_api_failure(result)):
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

    def _abort_on_repo_integrity_violation(
        self, result: AgentTaskResult, milestone_id: str = ""
    ) -> bool:
        """Fail before any owner-credentialed salvage or remote mutation."""
        if result.error_code != "repo_integrity_violation":
            return False
        message = result.error or "Protected Git metadata changed during agent execution"
        if milestone_id:
            self.repo.update_milestone(
                milestone_id,
                {
                    "status": "failed",
                    "session_id": result.session_id,
                    "error_message": message,
                },
            )
        self._update_workflow({"status": "failed", "error_message": message})
        self._emit("workflow_failed", {"error": message, "reason": "repo_integrity_violation"})
        return True

    def _write_phase_usage(
        self,
        milestone_id: str,
        result: AgentTaskResult,
        prior_usage: dict[str, int] | None = None,
    ) -> None:
        """Write this call's token/request increment to its milestone."""
        if not milestone_id:
            return
        prior_usage = prior_usage or {}
        try:
            self.repo.update_milestone(
                milestone_id,
                {
                    "phase_total_tokens": int(prior_usage.get("total_tokens", 0) or 0)
                    + int(result.total_tokens or 0),
                    "phase_input_tokens": int(prior_usage.get("total_input_tokens", 0) or 0)
                    + int(result.total_input_tokens or 0),
                    "phase_output_tokens": int(prior_usage.get("total_output_tokens", 0) or 0)
                    + int(result.total_output_tokens or 0),
                    "phase_request_count": int(prior_usage.get("request_count", 0) or 0)
                    + int(result.request_count or 0),
                },
            )
        except Exception:
            logger.warning("Failed to write phase usage to milestone", exc_info=True)

    def _clear_session_usage_offsets(self, session_ids: set[str]) -> None:
        """Discard runtime-only retry usage state after an agent call ends."""
        with self._session_lock:
            offsets = getattr(self, "_session_usage_offsets", {})
            for session_id in session_ids:
                offsets.pop(session_id, None)

    def _is_shutdown_requested(self) -> bool:
        """Support lightweight test/legacy instances constructed without ``__init__``."""
        event = getattr(self, "_shutdown_requested", None)
        return bool(event and event.is_set())

    def _abort_agent_run_for_shutdown(
        self,
        milestone_id: str,
        result: AgentTaskResult,
        retry_usage: dict[str, int],
        usage_session_ids: set[str],
        tracking_session_id: str,
    ) -> None:
        """Persist an interrupted attempt and unwind without failing its workflow."""
        if result.error_code == "repo_integrity_violation":
            # Shutdown must never downgrade an already-detected security
            # violation into a retryable cancellation. The protected .git
            # entry may have been replaced, so a new process must not adopt
            # that state as its trusted baseline.
            self._persist_shutdown_usage(
                milestone_id,
                result,
                retry_usage,
                usage_session_ids,
                tracking_session_id,
            )
            self._abort_on_repo_integrity_violation(result, milestone_id)
            raise WorkflowPaused("Service shutdown observed a repository integrity violation")

        self._cancel_milestone_for_shutdown(milestone_id)
        self._persist_shutdown_usage(
            milestone_id,
            result,
            retry_usage,
            usage_session_ids,
            tracking_session_id,
        )
        raise WorkflowPaused("Service shutdown interrupted the current attempt")

    def _persist_shutdown_usage(
        self,
        milestone_id: str,
        result: AgentTaskResult,
        retry_usage: dict[str, int],
        usage_session_ids: set[str],
        tracking_session_id: str,
    ) -> None:
        """Finalize usage/session bookkeeping for either shutdown outcome."""
        self._write_phase_usage(milestone_id, result, retry_usage)
        try:
            self.repo.refresh_workflow_usage_from_sessions(self._workflow_id)
        except Exception:
            logger.warning("Failed to refresh workflow usage during shutdown", exc_info=True)
        self._clear_session_usage_offsets(usage_session_ids)
        with self._session_lock:
            self._current_session_id = tracking_session_id

    def _cancel_milestone_for_shutdown(self, milestone_id: str) -> None:
        """Best-effort milestone cancellation that cannot fail the workflow."""
        if not milestone_id:
            return
        try:
            self.repo.update_milestone(
                milestone_id,
                {
                    "status": "cancelled",
                    "error_message": (
                        "Service shutdown interrupted this attempt; it will retry automatically"
                    ),
                },
            )
        except Exception:
            # The process is intentionally winding down. A late DB failure
            # must not escape as a generic orchestrator error and mark the
            # still-active workflow failed; the replacement process can
            # reconcile the stale in-progress milestone on retry.
            logger.warning("Failed to cancel milestone during shutdown", exc_info=True)

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

        **Phase 1 Enhancement (P0)**: Persist retry state before pause
        to ensure counts survive pause/resume cycle.
        """
        # ── Phase 1: Persist retry state before pause ─────────────────────
        wf = self.workflow
        if wf and wf.get("status") in ("developing", "planning"):
            # Read current retry counts
            test_retries = wf.get("test_retries", 0)
            skip_retries = wf.get("skip_retries", 0)
            dev_retries = wf.get("dev_retries_on_test_fail", 0)

            # Persist retry state + status + paused_at (原子写入)
            try:
                self._update_workflow(
                    {
                        "test_retries": test_retries,
                        "skip_retries": skip_retries,
                        "dev_retries_on_test_fail": dev_retries,
                        "status": "paused",
                        "paused_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                logger.info(
                    "Persisted retry state before pause: test=%d, skip=%d, dev=%d",
                    test_retries,
                    skip_retries,
                    dev_retries,
                )
            except Exception as e:
                # 写入失败仍允许 pause（避免阻塞用户操作）
                logger.warning("Failed to persist retry state before pause: %s", e)

        # ── Pause the agent process ───────────────────────────────────────
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

    def prepare_for_shutdown(self):
        """Interrupt the current attempt so a replacement process can retry it."""
        self._shutdown_requested.set()
        self._cancel_requested.set()
        with self._session_lock:
            session_id = self._current_session_id
        if session_id:
            logger.info(
                "Stopping agent task for service shutdown session=%s",
                session_id[:8],
            )
            try:
                self._runner.stop_session(session_id)
            except Exception as e:
                logger.warning(
                    "Failed to stop shutdown session %s: %s",
                    session_id[:8],
                    e,
                )

    def _dispatch_phase(self, phase: str, wf: dict, handler) -> PhaseResult | None:
        """Run a phase handler and return its structured result, if any.

        Phase B (#2044): builds ``WorkflowContext`` + ``PhaseDeps`` and invokes
        the handler as ``handler(ctx, deps)``. Migrated handlers return a
        ``PhaseResult`` (committed via ``_commit_phase_result``); thin phases are
        wrapped by ``advance()`` in a legacy adapter and return ``None`` until
        they migrate (T6-T9).
        """
        logger.debug("Dispatching phase=%s for workflow %s", phase, self._workflow_id[:8])
        ctx = self._build_workflow_context(wf)
        deps = self._build_phase_deps()
        return handler(ctx, deps)

    def _build_phase_deps(self) -> PhaseDeps:
        """Assemble the service bundle injected into a phase handler.

        ``git_workspace`` (T5) and ``sandbox`` (T13) are wired via lazy
        properties so a handler that needs them always gets a live instance.
        ``gh`` is bound here too: ``__init__`` leaves ``self._gh = None`` and
        the scheduler builds a fresh orchestrator each tick, so the registry
        production path (advance → resolve_phase_handler → _dispatch_phase →
        here) would otherwise hand a migrated handler (phases/*.handle) a
        ``None`` gh. The thin test-compat shims (_do_development/_do_pr_review)
        used to bind their own ``self._gh`` first, masking the gap, but the
        registry handlers read ``deps.gh`` directly. ``_get_gh`` is the same
        lazy binder those shims call; it's safe whenever ``self.workflow`` is
        truthy (it is during advance) and falls back to ``project_path`` when
        the worktree doesn't exist yet, so it's correct for preparation too
        (#2044 Phase B review P1-a).
        """
        if self._gh is None and self.repo is not None:
            self._gh = self._get_gh()
        return PhaseDeps(
            host=self,
            gh=self._gh,
            git_workspace=self._git_workspace,
            evidence=self._evidence,
            sandbox=self._sandbox_provider,
            repo=self.repo,
            agent_runner=self._runner,
        )

    # --- PhaseHost implementation (#2044 Phase B) ---
    def emit_phase_change(self, payload: dict) -> None:
        # _emit(event_type, data) is the orchestrator's emitter (L1897); real
        # phase_change emission is self._emit("phase_change", {...}) (e.g. L6100).
        self._emit("phase_change", payload)

    def emit_status_change(self, payload: dict) -> None:
        # Domain event distinct from phase_change; the merge handler emits this
        # on the policy-pause branch (legacy inline ``_emit("status_change", ...)``).
        self._emit("status_change", payload)

    def session_offsets(self):
        return self._session_usage_offsets

    def cancellation(self):
        return self._shutdown_requested

    def create_milestone_idempotent(self, **kwargs):
        # Returns the milestone dict — _create_milestone is idempotent (returns
        # the existing record if found). Phase A declared this ``-> None`` but
        # the pr_review handler (#2044 Phase B T11) reads the returned
        # milestone_id for agent-run correlation, so the return value is now
        # surfaced (the merge handler ignores it, so this is backward-
        # compatible).
        return self._create_milestone(**kwargs)

    # --- PhaseHost merge-phase helpers (#2044 Phase B T10) ---
    # Thin public aliases over the orchestrator-private underscore helpers so
    # the migrated merge handler (phases/merge.py) can call them via
    # ``deps.host.<name>`` without a concrete orchestrator reference. The
    # helpers themselves stay underscore-prefixed (their existing internal
    # callers are unchanged); these aliases are the PhaseHost surface only.
    def validate_pre_merge_change_scope(self, gh, wf, pr_head_sha):
        return self._validate_pre_merge_change_scope(gh, wf, pr_head_sha)

    def sync_failed_pr_with_main(self, gh, branch_name, pr_number, pr_head_sha):
        return self._sync_failed_pr_with_main(gh, branch_name, pr_number, pr_head_sha)

    def branch_contains_main(self, gh, pr_head_sha, branch_name=""):
        return self._branch_contains_main(gh, pr_head_sha, branch_name)

    def start_ci_repair_round(self, wf, pr_number, failed_checks):
        return self._start_ci_repair_round(wf, pr_number, failed_checks)

    def perform_git_cleanup(self):
        return self._perform_git_cleanup()

    def resolve_merge_conflicts(self, gh, branch_name, pr_number):
        return self._resolve_merge_conflicts(gh, branch_name, pr_number)

    # --- PhaseHost pr_review-phase helpers (#2044 Phase B T11) ---
    # Thin public aliases over the orchestrator-private underscore helpers so
    # the migrated pr_review handler (phases/pr_review.py) can call them via
    # ``deps.host.<name>`` without a concrete orchestrator reference. Same
    # pattern as the merge helpers above. See phase_host.py docstring.
    def get_workflow_field(self, field: str):
        wf = self.workflow or {}
        return wf.get(field)

    def refresh_workflow_snapshot(self) -> dict:
        return self.workflow or {}

    def post_github_comment(self, gh, number, body, *, is_pr=False, context="") -> None:
        return self._post_github_comment(gh, number, body, is_pr=is_pr, context=context)

    def must_run_full_review_rounds(self, wf: dict) -> bool:
        return self._must_run_full_review_rounds(wf)

    def get_pr_review_diff(self, gh, pr_number, branch_name) -> str:
        return self._get_pr_review_diff(gh, pr_number, branch_name)

    def smart_truncate_diff(self, diff_text: str) -> str:
        return self._smart_truncate_diff(diff_text)

    def clean_agent_text(self, text: str) -> str:
        return self._clean_agent_text(text)

    def poll_ci_status(self, gh, pr_number) -> list:
        return self._poll_ci_status(gh, pr_number)

    def run_agent_with_context_recovery(self, **kwargs):
        return self._run_agent_with_context_recovery(**kwargs)

    def accumulate_tokens(self, result) -> None:
        return self._accumulate_tokens(result)

    def abort_on_repo_integrity_violation(self, result, milestone_id: str) -> bool:
        return self._abort_on_repo_integrity_violation(result, milestone_id)

    def is_context_overflow(self, result) -> bool:
        return self._is_context_overflow(result)

    def artifact_text(self, result) -> str:
        return self._artifact_text(result)

    def artifact_tldr(self, result) -> str:
        return self._artifact_tldr(result)

    def review_is_approved(self, review_text: str, approval_phrase: str) -> bool:
        return self._review_is_approved(review_text, approval_phrase)

    def validate_autonomous_change_scope(self, gh, wf, base_sha, head_sha) -> str:
        return self._validate_autonomous_change_scope(gh, wf, base_sha, head_sha)

    def apply_pr_review_fix(
        self, wf, gh, review_text, round_num, dev_round, ci_failures, pr_number
    ) -> bool:
        return self._apply_pr_review_fix(
            wf, gh, review_text, round_num, dev_round, ci_failures, pr_number
        )

    def cancel_milestone_for_shutdown(self, milestone_id: str) -> None:
        return self._cancel_milestone_for_shutdown(milestone_id)

    # --- PhaseHost development-phase helpers (#2044 Phase B T12) ---
    # Thin public aliases over the orchestrator-private sub-methods so the
    # migrated development handler (phases/development.py) can call them via
    # ``deps.host.<name>`` without a concrete orchestrator reference. Same
    # pattern as the merge/pr_review helpers above. The sub-methods themselves
    # stay underscore-prefixed (their existing internal callers and the
    # direct-call/static-source tests under tests/issues/1140,1897,1647,1520,
    # 1277,1574,1547,1829 + tests/unit/test_autonomous_ci_guardrails.py are
    # unchanged); these aliases are the PhaseHost surface only.
    def run_development_agent(self, wf, dev_round, gh) -> None:
        return self._run_development_agent(wf, dev_round, gh)

    def post_dev_completion_comment(self, wf, dev_round, gh) -> None:
        return self._post_dev_completion_comment(wf, dev_round, gh)

    def run_test_phase(self, wf, dev_round, gh) -> None:
        return self._run_test_phase(wf, dev_round, gh)

    @property
    def workflow_id(self) -> str:
        # PhaseHost protocol declares ``workflow_id``; the internal field is the
        # underscore-prefixed ``self._workflow_id``. Expose it read-only so the
        # narrow interface is satisfied without migrating every internal reader.
        return self._workflow_id

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
            # Reconcile an interrupted merge-conflict worktree transition BEFORE
            # _ensure_worktree / _get_gh, so a SIGKILLed transition never lets a
            # later phase fall back to the main checkout (#2050). Order is fixed:
            # reconcile -> ensure_worktree -> bind GitHubOps -> phase execute.
            if wf.get("worktree_transition_state"):
                self._reconcile_worktree_transition(wf)
                wf = self.workflow  # reconcile may have changed worktree_path
                if wf.get("status") == "failed":
                    # Reconcile failed closed; do not run a phase.
                    return
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

            def _legacy(method):
                """Adapter: a not-yet-migrated _do_*(self, wf) is invoked with its
                original (wf) signature; the (ctx, deps) from _dispatch_phase are
                ignored until it migrates onto the contract (T6-T9).
                """

                def wrapped(ctx, deps):
                    return method(ctx.workflow)

                return wrapped

            migrated = resolve_phase_handler(phase)
            if migrated is not None:
                handler = migrated
            elif phase == "preparation":
                # Phase B #2044 T8: migrated onto the (ctx, deps) contract —
                # no longer wrapped by _legacy (it returns a PhaseResult and is
                # committed via _commit_phase_result).
                handler = self._do_preparation
            elif phase == "planning":
                # Phase B #2044 T9: migrated onto the (ctx, deps) contract —
                # no longer wrapped by _legacy (it returns a PhaseResult and is
                # committed via _commit_phase_result).
                handler = self._do_planning
            elif phase == "development":
                # Phase B #2044 T12: migrated to phases/development.py and
                # registered in PHASE_HANDLERS; the registry branch above
                # resolves it. This branch is retained only as a defensive
                # fallback in case the registry is queried before the import
                # side-effect registers the handler (it should never fire in
                # practice).
                handler = _legacy(self._do_development)
            elif phase == "pr_review":
                # Phase B #2044 T11: migrated to phases/pr_review.py and
                # registered in PHASE_HANDLERS; the registry branch above
                # resolves it. This branch is retained only as a defensive
                # fallback in case the registry is queried before the import
                # side-effect registers the handler (it should never fire).
                handler = _legacy(self._do_pr_review)
            elif phase == "report":
                # Phase B #2044 T6: migrated onto the (ctx, deps) contract —
                # no longer wrapped by _legacy (it returns a PhaseResult and is
                # committed via _commit_phase_result).
                handler = self._do_report
            elif phase == "wait":
                # Phase B #2044 T7: migrated onto the (ctx, deps) contract —
                # no longer wrapped by _legacy (it returns a PhaseResult and is
                # committed via _commit_phase_result).
                handler = self._do_wait
            elif phase == "merge":
                # Phase B #2044 T10: migrated to phases/merge.py and registered
                # in PHASE_HANDLERS; the registry branch above resolves it. This
                # branch is retained only as a defensive fallback in case the
                # registry is queried before the import side-effect registers
                # the handler (it should never fire in practice).
                handler = _legacy(self._do_merge)
            else:
                handler = None

            result = self._dispatch_phase(phase, wf, handler) if handler is not None else None
            # Phase A (#2044): a phase migrated onto the PhaseResult contract
            # returns a structured outcome, which is committed through the
            # single authoritative entrypoint. Legacy _do_* methods return None
            # and have already committed inline — leave them be. Either way the
            # transient-retry reset below applies only on a clean return.
            if isinstance(result, PhaseResult):
                self._commit_phase_result(result)
                # A pause result persists a user-facing pause reason in
                # error_message (e.g. merge-policy rejection). Skip the
                # transient-retry reset on a pause — its ``error_message=""
                # clear would wipe that reason (#2044 Phase B T10). Legacy
                # merge short-circuited this by raising WorkflowPaused after
                # the inline status=paused write; the migrated handler returns
                # PhaseResult.pause instead, so the reset must be gated here.
                if result.outcome == "pause":
                    return
            # Success/clean advance — reset the transient retry counter so the
            # next network blip starts fresh. Skip on ``failed``:
            # _commit_phase_result just wrote status=failed + error_message,
            # and this reset's ``error_message=""`` would wipe the failure
            # reason, leaving an undiagnosable terminal status. ``result is
            # None`` is the legacy inline-commit path which still resets.
            # (#2044 Phase B review P2.)
            if (result is None or result.outcome != "failed") and wf.get(
                "transient_retry_count", 0
            ):
                self._update_workflow({"transient_retry_count": 0, "error_message": ""})
        except WorkflowPaused as e:
            logger.info(
                "Workflow %s stopped advancing because it is paused: %s",
                self._workflow_id[:8],
                e,
            )
            return
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
            # _mark_failed records the terminal failure and reclaims the
            # worktree for THIS path (advance's exception handler). The
            # transient path above returned already, so this only runs for
            # terminal failures — the worktree survives retries, not terminal
            # failures (#1112).
            logger.error("Orchestrator error in %s: %s", phase, e, exc_info=True)
            self._mark_failed(str(e), phase=phase)
        # Convergence point for terminal-failure worktree reclamation (Issue
        # #1831 finding #1). After the phase runs, if the workflow is now
        # terminally failed — whether via _mark_failed just above (an unhandled
        # exception) OR via a phase that recorded status=failed inline and
        # returned normally (which bypasses _mark_failed) — reclaim the worktree
        # dir. Many phase-internal failure paths set status=failed and return;
        # without this check each would leak the worktree. Idempotent: on the
        # exception path _mark_failed already cleared worktree_path, so the
        # ``worktree_path`` guard makes this a no-op there and it only acts on
        # the inline-failure paths. Best-effort + branch kept (#1112).
        wf = self.workflow
        if wf and wf.get("status") == "failed" and wf.get("worktree_path"):
            self._cleanup_worktree_and_branch(
                reason="failed", remove_worktree=True, remove_branch=False
            )

    # ── Phase: Preparation ────────────────────────────────────────

    def _do_preparation(self, ctx: WorkflowContext, deps: PhaseDeps) -> PhaseResult:
        """Set up project, create/read issue, create branch.

        For fork workflows (parent_workflow_id set):
        - Skip repo/issue creation (already exist from parent)
        - Force worktree strategy for parallel execution
        - Jump to the next phase after the fork milestone's phase

        Phase B #2044 T8: migrated onto the PhaseResult contract. Same
        decisions as the legacy inline-commit method, only the recording
        mechanism changes: the phase/status transition (preparation → planning,
        or preparation → fork's next phase) travels on the returned
        ``PhaseResult``; bookkeeping workflow-field writes (worktree_path,
        branch_name, github_pr_number, repo_url, requirements_text, etc.) and
        ``current_round`` travel in ``workflow_patch``; the branch/repo/issue
        milestones travel in ``milestone_events``; the phase_change event is
        emitted through ``deps.host`` so the commit entrypoint stays the sole
        phase/status authority. The four forbidden fields (current_phase/status/
        completed_at/paused_at) are no longer written inline by this handler.

        Failure paths (GitHubOpsError on repo/issue/branch creation) keep their
        legacy semantics: they record the failed milestone inline then re-raise
        so ``advance()``'s exception handler runs ``_mark_failed`` (writing
        status=failed + error_message + the ``error`` event). Returning
        PhaseResult.failed here instead would skip that handler and drop the
        ``error`` event — a behaviour change. Re-raising preserves it exactly.

        Irreversible external-resource ids (``project_repo_url`` after
        create_repo, ``github_issue_number`` after create_issue) and their
        milestones are immediate-checkpointed via ``_update_workflow`` /
        ``_create_milestone`` right after each creation succeeds, NOT deferred
        into ``workflow_patch`` / ``milestone_events``. A later raise (e.g.
        branch/worktree creation) must not lose them, or re-entry would call
        create_repo/create_issue again — minting a duplicate repo / issue. The
        #2044 invariant allows immediate bookkeeping-field writes (only
        phase/status must travel through PhaseResult), so this is the correct
        split. Branch/worktree ids DO stay deferred — their creation is
        idempotent on re-entry (the fork fast-path / _ensure_worktree probes for
        a surviving branch). (#2044 Phase B review P1-b.)

        Until T10-T12 extract phases into phases/*.py, gh/repo stay on self;
        deps here carries the host (emit_phase_change) for this thin phase.
        """
        wf = ctx.workflow
        project_path = wf.get("project_path", "")
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")

        # Bookkeeping workflow-field writes collected here (non-forbidden fields
        # only); the phase/status transition is supplied by the PhaseResult.
        workflow_patch: dict[str, object] = {}
        # Milestones accumulated for unified commit by _commit_phase_result.
        milestone_events: list[dict] = []

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
                workflow_patch.update(
                    {
                        "worktree_path": wt_path,
                        "branch_name": branch_name,
                        "branch_strategy": "worktree",
                    }
                )
                # Worktree now exists; drop the cached gh (bound to the main
                # repo) so the next _get_gh() rebinds to the worktree path.
                self._gh = None
                milestone_events.append(
                    {
                        "phase": "preparation",
                        "milestone_type": "branch_created",
                        "status": "completed",
                        "title": f"Fork branch '{branch_name}' created (worktree)",
                    }
                )
            except GitHubOpsError as e:
                # Failure path preserves legacy semantics: record the failed
                # milestone inline then re-raise so advance()'s exception
                # handler runs _mark_failed. See method docstring.
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
            workflow_patch["current_round"] = 0
            # Emit the phase_change event through the host (the commit entrypoint
            # does NOT emit phase_change — Phase A contract — so the handler emits
            # its own). Payload is identical to the legacy inline _emit call.
            deps.host.emit_phase_change({"phase": next_phase, "fork": True})
            # Advance via PhaseResult rather than an inline current_phase/status
            # write. Same decision the legacy method made.
            return PhaseResult.completed(
                next_phase=next_phase,
                next_status=PHASE_STATUS_MAP.get(next_phase, "planning"),
                workflow_patch=workflow_patch,
                milestone_events=milestone_events,
            )

        # --- Normal workflow path ---

        # New project: create GitHub repo. The block is gated on the
        # repo_setup milestone so re-entry is idempotent — is_new_project stays
        # True across re-entry, but the immediate-checkpointed repo_setup
        # milestone (written below after create_repo succeeds) tells a later
        # run the repo was already created. Without this gate a transient
        # raise after create_repo would re-enter and call create_repo again,
        # this time with the resolved URL as the name (project_repo_url was
        # checkpointed) — failing or minting a duplicate. Mirrors the
        # issue-side github_issue_number gate below. (#2044 Phase B P1-b.)
        if wf.get("is_new_project"):
            if self._find_existing_milestone("preparation", "repo_setup"):
                # Repo already created on a prior (partially-failed) run — skip
                # create_repo (idempotent re-entry). The repo_setup milestone
                # was immediate-checkpointed, so it survives the raise that
                # ended the prior run; project_repo_url is also persisted.
                logger.info(
                    "Workflow %s: repo_setup milestone exists; skipping create_repo",
                    self._workflow_id[:8],
                )
            else:
                try:
                    gh = GitHubOps(project_path or ".", system_account=system_account)
                    repo_data = gh.create_repo(
                        name=wf.get("project_repo_url", f"auto-project-{uuid.uuid4().hex[:8]}"),
                        private=wf.get("is_private", True),
                        description=wf.get("title", ""),
                    )
                    repo_url = repo_data.get("url", "")
                    project_path = project_path or "."
                    self._gh = GitHubOps(project_path, system_account=system_account)
                    # The repo URL is an irreversible external-resource id: a
                    # later raise (e.g. branch creation) must not lose it, or
                    # re-entry would call create_repo again and mint a NEW
                    # repo. #2044 allows immediate bookkeeping-field writes
                    # (only phase/status must travel through PhaseResult), so
                    # checkpoint the id + its milestone here rather than
                    # deferring into workflow_patch / milestone_events. The
                    # completed repo_setup milestone ALSO doubles as the
                    # re-entry gate above. (#2044 Phase B review P1-b.)
                    self._update_workflow({"project_repo_url": repo_url})
                    self._create_milestone(
                        phase="preparation",
                        milestone_type="repo_setup",
                        status="completed",
                        title="Repository created",
                        result_summary=f"Created repo: {repo_url}",
                    )
                except GitHubOpsError as e:
                    # Failure path preserves legacy semantics: record the failed
                    # milestone inline then re-raise so advance()'s exception
                    # handler runs _mark_failed. See method docstring.
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
                workflow_patch["github_issue_number"] = issue_number
                milestone_events.append(
                    {
                        "phase": "preparation",
                        "milestone_type": "issue_linked",
                        "status": "completed",
                        "title": f"Linked to issue #{issue_number}",
                        "github_issue_number": issue_number,
                        "result_summary": issue_url,
                    }
                )

        if not issue_number and requirements_text:
            # Create issue from text
            try:
                issue_data = gh.create_issue(
                    title=wf.get("title") or f"Autonomous Dev: {requirements_text[:60]}",
                    body=requirements_text,
                )
                issue_number = issue_data.get("number")
                # The issue number is an irreversible external-resource id: a
                # later raise (e.g. branch creation) must not lose it, or
                # re-entry would call create_issue again and open a DUPLICATE
                # issue. #2044 allows immediate bookkeeping-field writes (only
                # phase/status must travel through PhaseResult), so checkpoint
                # the number + its milestone here rather than deferring into
                # workflow_patch / milestone_events. (#2044 Phase B review P1-b.)
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
                # Failure path preserves legacy semantics: record the failed
                # milestone inline then re-raise so advance()'s exception
                # handler runs _mark_failed. See method docstring.
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
                milestone_events.append(
                    {
                        "phase": "preparation",
                        "milestone_type": "issue_linked",
                        "status": "completed",
                        "title": f"Linked to issue #{issue_number}",
                        "github_issue_number": issue_number,
                        "result_summary": issue_data.get("title", ""),
                    }
                )
                workflow_patch["requirements_text"] = requirements_text
            except GitHubOpsError as e:
                # Failure path preserves legacy semantics: record the failed
                # milestone inline then re-raise so advance()'s exception
                # handler runs _mark_failed. See method docstring.
                self._create_milestone(
                    phase="preparation",
                    milestone_type="issue_linked",
                    status="failed",
                    title=f"Failed to read issue #{issue_number}",
                    github_issue_number=issue_number,
                    error_message=str(e),
                )
                raise

        # Create branch
        strategy = wf.get("branch_strategy", "new-branch")
        # Use pre-generated branch_name if available (Issue #1573)
        branch_name = wf.get("branch_name", "")
        if not branch_name:
            branch_name = f"auto-dev/{self._workflow_id[:12]}"

        if strategy == "new-branch" or strategy == "worktree":
            try:
                # Ensure we branch from latest origin/main
                gh._run_git(["fetch", "origin", "main"])

                if strategy == "worktree":
                    # Use pre-generated worktree_path if available (Issue #1573)
                    # Format: {project_path}/.worktrees/{workflow_id}
                    # Fallback to legacy format for backwards compatibility
                    pre_generated_worktree_path = wf.get("worktree_path", "") or wf.get(
                        "preferred_worktree_path", ""
                    )
                    if pre_generated_worktree_path and pre_generated_worktree_path.startswith(
                        project_path
                    ):
                        worktree_path = pre_generated_worktree_path
                    else:
                        # Legacy format for backwards compatibility
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
                        # Use locked base_commit_sha for batch workflows (Issue #1552)
                        base_commit_sha = wf.get("base_commit_sha")
                        base_ref = base_commit_sha if base_commit_sha else "origin/main"
                        # Resolve the symbolic ref before branch creation and
                        # persist that immutable SHA. Scope checks must not use
                        # a later, moving origin/main value.
                        resolved_base = gh._run_git(["rev-parse", base_ref]).stdout.strip()
                        if not resolved_base:
                            raise GitHubOpsError(f"Unable to resolve branch base {base_ref}")
                        if not base_commit_sha:
                            workflow_patch["base_commit_sha"] = resolved_base
                        wt_data = gh.create_worktree(
                            path=worktree_path,
                            branch=branch_name,
                            base=resolved_base,
                        )

                    # Issue #1573: Verify worktree was created on correct branch
                    actual_worktree_path = wt_data.get("worktree_path", "")
                    if actual_worktree_path:
                        try:
                            wt_gh = GitHubOps(actual_worktree_path, system_account=system_account)
                            actual_branch = wt_gh.get_current_branch()
                            if actual_branch != branch_name:
                                logger.error(
                                    "Worktree created on wrong branch for workflow %s: expected=%s, actual=%s",
                                    self._workflow_id[:8],
                                    branch_name,
                                    actual_branch,
                                )
                                raise GitHubOpsError(
                                    f"Worktree created on wrong branch: expected {branch_name}, actual {actual_branch}"
                                )
                            logger.info(
                                "Verified worktree %s is on correct branch %s",
                                actual_worktree_path,
                                branch_name,
                            )
                        except GitHubOpsError:
                            raise
                        except Exception as e:
                            logger.warning("Failed to verify worktree branch: %s", e)

                    # Collect worktree_path/branch_name into workflow_patch (the
                    # legacy method wrote them as a single _update_workflow call;
                    # they are non-forbidden fields so they travel in the patch
                    # applied by _commit_phase_result).
                    workflow_patch.update(
                        {
                            "worktree_path": actual_worktree_path,
                            "preferred_worktree_path": actual_worktree_path,
                            "branch_name": branch_name,
                        }
                    )
                    # The worktree now exists; drop the cached gh (bound to the
                    # main repo during preparation) so the next _get_gh() rebinds
                    # to the worktree path — the agent's actual working repo.
                    self._gh = None
                else:
                    # Use locked base_commit_sha for batch workflows (Issue #1552)
                    base_commit_sha = wf.get("base_commit_sha")
                    base_ref = base_commit_sha if base_commit_sha else "origin/main"
                    resolved_base = gh._run_git(["rev-parse", base_ref]).stdout.strip()
                    if not resolved_base:
                        raise GitHubOpsError(f"Unable to resolve branch base {base_ref}")
                    gh.create_branch(branch_name, base=resolved_base)
                    workflow_patch["branch_name"] = branch_name
                    if not base_commit_sha:
                        workflow_patch["base_commit_sha"] = resolved_base

                milestone_events.append(
                    {
                        "phase": "preparation",
                        "milestone_type": "branch_created",
                        "status": "completed",
                        "title": f"Branch '{branch_name}' created",
                    }
                )
            except GitHubOpsError as e:
                # Failure path preserves legacy semantics: record the failed
                # milestone inline then re-raise so advance()'s exception
                # handler runs _mark_failed. See method docstring.
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="failed",
                    title="Branch creation failed",
                    error_message=str(e),
                )
                raise
        elif strategy == "current":
            try:
                current_branch = gh.get_current_branch()
                workflow_patch["branch_name"] = current_branch
            except GitHubOpsError as e:
                # Failure path preserves legacy semantics: record the failed
                # milestone inline then re-raise so advance()'s exception
                # handler runs _mark_failed. See method docstring.
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="failed",
                    title="Current branch detection failed",
                    error_message=str(e),
                )
                raise

        # Transition to planning — advance via PhaseResult rather than an inline
        # current_phase/status write. current_round is a non-forbidden workflow
        # field and travels in workflow_patch. Same decision the legacy method
        # made.
        workflow_patch["current_round"] = 0
        # Emit the phase_change event through the host (the commit entrypoint
        # does NOT emit phase_change — Phase A contract — so the handler emits
        # its own). Payload is identical to the legacy inline _emit call.
        deps.host.emit_phase_change({"phase": "planning"})
        return PhaseResult.completed(
            next_phase="planning",
            next_status="planning",
            workflow_patch=workflow_patch,
            milestone_events=milestone_events,
        )

    # ── Phase: Planning ────────────────────────────────────────────

    def _do_planning(self, ctx: WorkflowContext, deps: PhaseDeps) -> PhaseResult:
        """Execute one planning + review round.

        Phase B #2044 T9: migrated onto the PhaseResult contract. Same
        decisions as the legacy inline-commit method, only the recording
        mechanism changes: the phase/status transition (planning → development)
        travels on the returned ``PhaseResult``; bookkeeping workflow-field
        writes (current_round, user_feedback) travel in ``workflow_patch``; the
        plan-finalization phase_change event is emitted through ``deps.host`` so
        the commit entrypoint stays the sole phase/status authority. The four
        forbidden fields (current_phase/status/completed_at/paused_at) are no
        longer written inline by this handler on the success path.

        This phase's milestones are NOT collected into ``milestone_events`` for
        unified commit, unlike _do_preparation. Each plan/review/refine
        milestone is created inline as status=in_progress, the agent runs, then
        ``repo.update_milestone`` flips it to completed/failed with the agent
        output — and the next round reads prior milestones via
        ``repo.list_milestones`` to refine. Deferring creation to the end would
        break that create→run→update→re-read interleaving. So the in-progress
        anchors stay inline (they are durable work records, not terminal
        markers); only the terminal phase/status transition uses PhaseResult.

        Failure paths (plan/refine timeout or agent failure) keep their legacy
        semantics: they were normal control-flow returns (not raises) that wrote
        status inline and returned None, bypassing advance()'s _mark_failed. The
        migration preserves that exactly — a timeout returns
        ``PhaseResult(outcome="wait", next_status="planning_timeout", ...)`` (the
        wait outcome parks the workflow without advancing the phase; constructed
        directly because the wait() factory hardcodes next_status="waiting"),
        and a hard failure returns ``PhaseResult.failed(structured_error=
        {"message": ...})`` so _commit_phase_result writes status=failed +
        error_message. Both also keep their inline ``_emit("planning_timeout",
        ...)`` because it is a domain event (not a phase_change) and PhaseHost
        only proxies phase_change. The non-phase_change round_end emit on the
        continue-round branch likewise stays inline.

        Until T10-T12 extract phases into phases/*.py, gh/repo/agent stay on
        self; deps here carries the host (emit_phase_change) for this phase.
        """
        wf = ctx.workflow
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
                PLANNING_CONTEXT + "你是一个高级开发工程师。请根据以下审查意见完善实现方案。\n\n"
            )
            if issue_number:
                prompt += (
                    f"## 关联 Issue\n"
                    f"本任务关联 GitHub Issue #{issue_number}。\n"
                    f"完善后的方案需满足 Issue #{issue_number} 的所有需求。\n\n"
                )
            prompt += (
                f"## 原始需求\n{requirements}\n\n"
                f"## 审查意见\n{review_text}\n\n"
                f"## 原方案\n{existing_plan}\n\n"
                f"请输出完善后的完整实现方案，并使用 `## 验证计划` 作为独立标题，保留/完善其中的验证内容，"
                f"明确本次改动完成后必须验证哪些方面。\n\n"
                f"重要约束：只输出高层设计方案和实现步骤描述，"
                f"不要输出完整的代码实现。具体代码将在后续开发阶段编写。"
            )
            prompt += self._get_user_feedback_prompt(wf)
            milestone_type = "plan_refined"
        else:
            repo_context = self._resolve_effective_repo_context(wf)
            prompt = (
                PLANNING_CONTEXT + "你是一个高级开发工程师。请为以下需求制定详细的实现方案。\n\n"
            )
            if issue_number:
                prompt += (
                    f"## 关联 Issue\n"
                    f"本任务关联 GitHub Issue #{issue_number}。\n"
                    f"方案中请明确引用此 Issue 编号 #{issue_number}。\n\n"
                )
            prompt += (
                f"## 需求\n{requirements}\n\n"
                f"## 项目路径\n{repo_context.get('repo_path', '')}\n\n"
                f"请用 plan mode 创建方案，包含：\n"
                f"1. 需求分析和拆分\n"
                f"2. 技术方案和架构设计\n"
                f"3. 实现步骤（按优先级排序）\n"
                f"4. 测试策略\n"
                f"5. 潜在风险和缓解措施\n"
                f"6. 必须包含 `## 验证计划` 独立标题：按影响面列出必须验证的方面、建议测试范围、何时需要扩大到更广测试\n\n"
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

        # Bookkeeping workflow-field writes collected here (non-forbidden fields
        # only); the phase/status transition is supplied by the PhaseResult.
        workflow_patch: dict[str, object] = {}

        # Clear user feedback after it has been injected into the prompt
        if wf.get("user_feedback", "").strip():
            workflow_patch["user_feedback"] = ""

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
                # Timeout → allow user to extend instead of hard failure.
                # ``wait`` parks the workflow without advancing the phase; the
                # custom planning_timeout status travels on next_status (the
                # wait outcome writes ``next_status or "waiting"``), and the
                # error_message travels in workflow_patch (non-forbidden). The
                # inline planning_timeout event stays — it is a domain event,
                # not a phase_change, so PhaseHost does not proxy it.
                error_message = (
                    f"Planning timed out after {planning_timeout}s. "
                    "You can extend the timeout and retry."
                )
                self._emit(
                    "planning_timeout",
                    {
                        "timeout": planning_timeout,
                        "tokens_used": result.total_tokens,
                        "partial_plan": (self._artifact_visible_text(result) or plan_text)[:500],
                    },
                )
                # The wait() factory hardcodes next_status="waiting", but
                # planning_timeout is a distinct park status the user can extend
                # via the planning-timeout API. Construct directly so the custom
                # status rides on outcome="wait" (which parks the phase) while
                # next_status overrides the parked status — exactly what the
                # _commit_phase_result wait branch already supports
                # (patch["status"] = result.next_status or "waiting").
                return PhaseResult(
                    outcome="wait",
                    next_phase=None,
                    next_status="planning_timeout",
                    workflow_patch={**workflow_patch, "error_message": error_message},
                )
            else:
                # Hard failure: PhaseResult.failed writes status=failed +
                # error_message (from structured_error.message) in the commit
                # entrypoint. Same decision as the legacy inline write.
                return PhaseResult.failed(
                    structured_error={"message": f"Planning failed: {result.error}"},
                    workflow_patch=workflow_patch,
                )

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
            PLANNING_CONTEXT + "你是一位资深技术评审专家。请严格审查以下实现方案，指出：\n"
            "1. 遗漏的需求\n"
            "2. 架构风险\n"
            "3. 实现难度估计\n"
            "4. 改进建议\n"
            "5. 验证计划是否足够覆盖方案中的行为变化、风险点和受影响模块\n\n"
        )
        if issue_number:
            review_prompt += (
                f"## 关联 Issue\n"
                f"本任务关联 GitHub Issue #{issue_number}。\n"
                f"审查时请确保方案满足 Issue #{issue_number} 的所有需求。\n\n"
            )
        review_prompt += (
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
        workflow_patch["current_round"] = round_num

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
                )
                if issue_number:
                    refine_prompt += (
                        f"## 关联 Issue\n"
                        f"本任务关联 GitHub Issue #{issue_number}。\n"
                        f"完善后的方案需满足 Issue #{issue_number} 的所有需求。\n\n"
                    )
                refine_prompt += (
                    f"## 当前方案\n{final_plan}\n\n"
                    f"## 审查意见\n{last_review}\n\n"
                    "请保留并完善方案中的 `## 验证计划` 章节，明确本次实现完成后必须验证哪些方面，"
                    "以及什么情况下需要扩大测试范围。\n\n"
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
                        # Same mapping as the plan-agent timeout branch above:
                        # outcome="wait" parks the phase; the wait() factory
                        # hardcodes "waiting" so construct directly to carry the
                        # custom planning_timeout park status.
                        error_message = (
                            f"Plan refine timed out after {PLANNING_TIMEOUT}s. "
                            "You can extend the timeout and retry."
                        )
                        self._emit(
                            "planning_timeout",
                            {
                                "timeout": PLANNING_TIMEOUT,
                                "tokens_used": refine_result.total_tokens,
                                "partial_plan": final_plan[:500],
                            },
                        )
                        return PhaseResult(
                            outcome="wait",
                            next_phase=None,
                            next_status="planning_timeout",
                            workflow_patch={**workflow_patch, "error_message": error_message},
                        )
                    else:
                        return PhaseResult.failed(
                            structured_error={
                                "message": f"Plan refine failed: {refine_result.error}"
                            },
                            workflow_patch=workflow_patch,
                        )

            final_plan = self._sanitize_artifact_text(final_plan)

            # Record the authoritative final plan.
            plan_final_ms = self._create_milestone(
                phase="planning",
                dev_round=dev_round,
                round_number=round_num,
                milestone_type="plan_finalized",
                status="in_progress",
                title="Plan Finalized",
                # Finalization is a zero-cost summary marker, not another LLM
                # call. Link it to the stable main session so the timeline can
                # still open the conversation while its own usage remains 0.
                session_id=(wf.get("main_session_id") or "").strip(),
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

            # Plan finalized, move to development — advance via PhaseResult
            # rather than an inline current_phase/status write. Same decision
            # the legacy method made.
            #
            # Emit the phase_change event through the host (the commit
            # entrypoint does NOT emit phase_change — Phase A contract — so the
            # handler emits its own). Payload is identical to the legacy inline
            # _emit call.
            deps.host.emit_phase_change({"phase": "development"})
            return PhaseResult.completed(
                next_phase="development",
                next_status="developing",
                workflow_patch=workflow_patch,
            )
        else:
            # Continue to next planning round. round_end is a domain event (not
            # a phase_change) so it stays inline. Phase/status are unchanged —
            # the workflow stays in planning for the next round, so a ``retry``
            # outcome carries only the current_round bump (and any user_feedback
            # clear) without touching the forbidden fields.
            self._emit("round_end", {"round": round_num, "approved": False})
            return PhaseResult.retry(workflow_patch=workflow_patch)

    # ── Phase: Development ────────────────────────────────────────

    def _do_development(self, wf: dict):
        """Test-compat shim (#2044 Phase B T12).

        The development phase lives in ``phases/development.py`` (registered in
        ``PHASE_HANDLERS``); advance()'s production path resolves it via the
        registry, NOT through this method. This thin wrapper exists only so the
        many ``o._do_development(wf)`` direct callers in tests/ (and any
        ``patch('AutonomousOrchestrator._do_development')`` sites that influence
        a direct call rather than advance()) keep working with zero per-test
        edits. It builds the (ctx, deps) bundle, delegates to
        ``phases.development.handle``, and commits the returned PhaseResult
        through the single authoritative entrypoint — same behaviour as
        advance()'s dispatch.

        ACCEPTED DEBT (#2044 T14): kept rather than removed. Migrating the
        ~60+ direct-caller tests to invoke the registry handler (or advance())
        is out of scope for #2044 — these are test helpers, production
        advance() uses the registry. Remove in a follow-up that retires the
        direct-call test pattern wholesale.

        Behaviour note: the development sub-methods (``_run_development_agent``
        / ``_post_dev_completion_comment`` / ``_run_test_phase``) stay on the
        orchestrator and continue to commit forbidden fields inline
        (status=failed on dev/test failure, status=pr_review +
        current_phase=pr_review on test-phase success) exactly as the legacy
        ``_do_development`` did. The migrated handler is a thin top-level
        orchestrator that calls them via host aliases and returns a PhaseResult
        mirroring the committed transition; the re-write by
        ``_commit_phase_result`` is idempotent (values match).
        """
        from app.modules.workspace.autonomous import phases as _phases

        # Legacy ``_do_development`` called ``gh = self._get_gh()`` first (the
        # lazy initializer populates ``self._gh``); many direct-call tests stub
        # ``orch._get_gh.return_value`` rather than ``orch._gh``. Resolve the gh
        # binding through the same lazy path AND assign it to ``self._gh`` so
        # ``deps.gh`` (which reads ``self._gh`` via _build_phase_deps) sees the
        # same mock the legacy method did.
        self._gh = self._get_gh()
        ctx = self._build_workflow_context(wf)
        deps = self._build_phase_deps()
        result = _phases.development.handle(ctx, deps)
        if result is not None:
            self._commit_phase_result(result)

    def _run_development_agent(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Run the development agent, verify code changes, and return.

        On failure, updates workflow status to 'failed' and returns.
        Caller should check workflow status if needed.

        Issue #1573: Added branch verification at the start of development phase.
        """
        # Get Issue number for prompt context
        issue_number = wf.get("github_issue_number") or self.workflow.get("github_issue_number")
        project_path = wf.get("worktree_path") or wf.get("project_path", "")
        runtime_error = (
            ""
            if wf.get("workspace_type") == "remote"
            else self._runtime_environment_gate(project_path, gh)
        )
        if runtime_error:
            self._update_workflow({"status": "failed", "error_message": runtime_error})
            return
        # Issue #1573: Verify we're on the correct branch before development
        expected_branch = wf.get("branch_name", "")
        workflow_prefix = f"auto-dev/{self._workflow_id[:8]}"
        try:
            actual_branch = gh.get_current_branch()
            if expected_branch and actual_branch != expected_branch:
                logger.warning(
                    "Development phase branch mismatch for workflow %s: expected=%s, actual=%s",
                    self._workflow_id[:8],
                    expected_branch,
                    actual_branch,
                )
                # If expected_branch starts with auto-dev/, try to switch
                if expected_branch.startswith("auto-dev/"):
                    try:
                        gh.checkout(expected_branch)
                        logger.info(
                            "Successfully switched to expected branch %s",
                            expected_branch,
                        )
                        actual_branch = gh.get_current_branch()
                    except GitHubOpsError as e:
                        logger.error(
                            "Failed to checkout to expected branch %s: %s",
                            expected_branch,
                            e,
                        )
                        self._create_milestone(
                            phase="development",
                            dev_round=dev_round,
                            milestone_type="branch_mismatch",
                            status="failed",
                            title=f"Branch mismatch: expected {expected_branch}, actual {actual_branch}",
                            error_message=f"Cannot checkout to expected branch: {e}",
                        )
                        self._update_workflow(
                            {
                                "status": "failed",
                                "error_message": f"Branch mismatch: cannot checkout to {expected_branch}",
                            }
                        )
                        return
                else:
                    # No auto-dev prefix, check if we're at least on workflow-specific branch
                    if not actual_branch.startswith(workflow_prefix):
                        logger.error(
                            "Not on workflow-specific branch for workflow %s: %s",
                            self._workflow_id[:8],
                            actual_branch,
                        )
                        self._create_milestone(
                            phase="development",
                            dev_round=dev_round,
                            milestone_type="branch_mismatch",
                            status="failed",
                            title=f"Not on workflow-specific branch: {actual_branch}",
                            error_message=f"Expected branch starting with {workflow_prefix}",
                        )
                        self._update_workflow(
                            {
                                "status": "failed",
                                "error_message": f"Not on workflow-specific branch: {actual_branch}",
                            }
                        )
                        return
        except GitHubOpsError as e:
            logger.warning("Branch verification failed: %s", e)

        final_plan = self._get_latest_final_plan(wf)

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

        dev_prompt = AUTONOMOUS_CONTEXT + "根据以下已审定的实现方案进行完整开发。\n\n"
        if issue_number:
            dev_prompt += (
                f"## 关联 Issue\n"
                f"本任务关联 GitHub Issue #{issue_number}。\n"
                f"所有修改都必须满足 Issue #{issue_number}；提交由编排器统一完成。\n\n"
            )
        dev_prompt += (
            f"## 实现方案\n{final_plan}\n\n"
            f"## 要求\n"
            f"1. 严格按照方案实现所有功能\n"
            f"2. 编写单元测试和集成测试\n"
            f"3. 运行所有测试确保通过\n"
            f"4. 确保不破坏现有功能\n"
            f"5. 遵循项目现有的代码风格和约定\n"
            f"6. 保留修改在工作区，不要执行 git add、git commit 或 git push"
        )
        dev_prompt += self._project_runtime_contract(
            wf.get("worktree_path") or wf.get("project_path", ""), gh
        )
        dev_prompt += self._get_ci_repair_prompt(wf)
        dev_prompt += self._get_user_feedback_prompt(wf)

        result = self._run_agent_with_context_recovery(
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

        if result.error_code == "repo_integrity_violation":
            self._accumulate_tokens(result)
            self._abort_on_repo_integrity_violation(result, ms.get("milestone_id", ""))
            return

        # P2 修复（Issue #1611）：开发完成后重置 gh 缓存，重新绑定到正确路径
        self._gh = None
        gh = self._get_gh()

        # 严格校验：自主开发不能悄悄切到其他分支
        try:
            current_branch = gh.get_current_branch()
            expected_branch = wf.get("branch_name", "")
            if expected_branch and current_branch != expected_branch:
                logger.error(
                    "Development changed workflow branch unexpectedly: expected=%s actual=%s",
                    expected_branch,
                    current_branch,
                )
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "error_message": (
                            f"Workflow branch changed unexpectedly: expected {expected_branch}, "
                            f"actual {current_branch}"
                        ),
                    },
                )
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": (
                            f"Workflow branch changed unexpectedly: expected {expected_branch}, "
                            f"actual {current_branch}"
                        ),
                    }
                )
                return
        except Exception as e:
            logger.warning("Failed to verify branch after development: %s", e)

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
        primary_error = self._primary_result_error(result)

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
                    self._record_trusted_head(gh, sha=commit_sha)
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
                if primary_error:
                    logger.warning(
                        "Agent failed before producing a new commit; preserving primary error: %s",
                        primary_error,
                    )
                else:
                    logger.warning(
                        "Agent reported success but no new commits detected (SHA unchanged)"
                    )
                milestone_error = (
                    primary_error
                    if primary_error
                    else "Agent produced no code changes (commit SHA unchanged)"
                )
                workflow_error = (
                    f"Development failed: {primary_error}"
                    if primary_error
                    else "Development failed: agent produced no code changes"
                )
                self.repo.update_milestone(
                    ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "session_id": result.session_id,
                        "result_summary": self._artifact_text(result)[:300],
                        "tldr": self._artifact_tldr(result),
                        "error_message": milestone_error,
                    },
                )
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": workflow_error,
                    }
                )
                return

        # Block accidental repository-wide rewrites before they can proceed to
        # tests/PR creation.  The threshold is intentionally configurable for
        # explicitly planned migrations, but the default catches the 80-170
        # file scope explosions observed in security issue workflows.
        scope_error = ""
        if sha_changed:
            scope_error = self._validate_autonomous_change_scope(gh, wf, commit_before, commit_sha)
        if scope_error:
            logger.error(scope_error)
            self.repo.update_milestone(
                ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "session_id": result.session_id,
                    "commit_shas": json.dumps([commit_sha] if commit_sha else []),
                    "diff_stats": json.dumps(diff_stats),
                    "error_message": scope_error,
                },
            )
            self._update_workflow({"status": "failed", "error_message": scope_error})
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

    def _validate_test_report_format(self, text: str) -> tuple[bool, str]:
        """Validate that test report follows standard format (Issue #1547).

        Returns:
            (is_valid, reason): True if format is valid, False with reason otherwise.

        Standard formats (enforced by prompt):
        - pytest: "X passed, Y failed, Z skipped" or "X passed in Y.Zs"
        - Jest: "X tests passed" or "Test Suites: X passed"
        - Go test: "PASS ok" or "X tests passed"
        - Rust: "test result: ok" or "running X tests"
        - Java: "Tests run: X" or "BUILD SUCCESS"

        Invalid formats (trigger retry):
        - "X个测试全部通过" (Chinese non-standard)
        - "所有测试都成功了" (Chinese non-standard)
        - "测试运行完成，全部通过" (Chinese non-standard)
        - "测试在后台运行中" (hallucination)
        - "测试进度约50%" (hallucination)
        """
        if not text or not text.strip():
            return False, "Empty test report"

        # Standard format patterns (these are VALID)
        _standard_patterns = [
            # pytest standard formats
            r"\d+\s+passed(?:\s*,\s*\d+\s+failed)?(?:\s*,\s*\d+\s+skipped)?(?:\s+in\s+[\d.]+s)?",
            r"\d+\s+passed\s+in\s+[\d.]+s",
            # Jest standard formats
            r"\d+\s+tests\s+passed(?:\s*,\s*\d+\s+tests\s+failed)?",
            r"Test\s+Suites:\s*\d+\s+passed",
            # Go test standard formats
            r"PASS\s+ok",
            r"FAIL\s+FAIL",
            r"\d+\s+tests\s+passed",
            # Rust cargo test standard formats
            r"test\s+result:\s*ok(?:\.\s*\d+\s+passed(?:;\s*\d+\s+failed)?)?",
            r"running\s+\d+\s+tests",
            # Java Maven/Gradle standard formats
            r"Tests\s+run:\s*\d+",
            r"BUILD\s+SUCCESS(?:FUL)?",
            r"BUILD\s+FAILURE",
            # pytest session markers (valid indicators)
            r"test\s+session\s+starts",
            r"collected\s+\d+\s+items",
            r"={3,}\s*\d+\s+(passed|failed|skipped)",
        ]

        # Check if text contains standard format
        has_standard = any(re.search(p, text, re.IGNORECASE) for p in _standard_patterns)

        # Non-standard format patterns (these are INVALID)
        # Issue #1544/1538: Chinese formats that were previously missed
        _non_standard_patterns = [
            # Chinese non-standard formats
            r"\d+\s*(个|项|件)\s*测试\s*(全部|全都|都)?\s*(通过|成功)",
            r"(所有|全部|全部的)\s*\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"(通过|成功)[:：\s]+\d+\s*(个|项|件|测试)?",  # Mixed colon formats
            r"(失败|错误)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"\d+\s*(个|项|件|测试)\s*(通过|成功|失败|跳过)",
            # Japanese non-standard formats
            r"(通過|成功)[:：\s]+\d+\s*(件|テスト)?",
            r"(失敗|エラー)[:：\s]+\d+\s*(件|テスト)?",
            # Korean non-standard formats
            r"(통과|성공)[:：\s]+\d+\s*(개|테스트)?",
            r"(실패|오류)[:：\s]+\d+\s*(개|테스트)?",
            # Natural language descriptions (hallucination indicators)
            r"测试(全部|都)?(成功|通过)",
            r"所有测试(都)?(成功|通过)",
            r"测试运行完成",
            r"测试.*后台.*运行",
            r"测试正在.*运行",
            r"测试进度.*%",
        ]

        has_non_standard = any(re.search(p, text, re.IGNORECASE) for p in _non_standard_patterns)

        # Validation logic:
        # 1. If has standard format → VALID (no retry needed)
        # 2. If has non-standard format AND no standard → INVALID (trigger retry)
        if has_standard:
            return True, "Standard format detected"
        elif has_non_standard:
            return False, "Non-standard format detected (requires retry)"
        else:
            # No recognizable format → likely hallucination or empty
            return False, "No test result format detected"

    def _run_test_phase(self, wf: dict, dev_round: int, gh: GitHubOps):
        """Run tests, post results to issue, handle retries.

        On unrecoverable failure, updates workflow status to 'failed' and returns.
        On success, transitions workflow to pr_review phase.
        """
        # Get Issue number for prompt context
        issue_number = wf.get("github_issue_number") or self.workflow.get("github_issue_number")
        project_path = wf.get("worktree_path") or wf.get("project_path", "")
        runtime_error = (
            ""
            if wf.get("workspace_type") == "remote"
            else self._runtime_environment_gate(project_path, gh)
        )
        if runtime_error:
            self._update_workflow({"status": "failed", "error_message": runtime_error})
            return
        test_ms = self._create_milestone(
            phase="development",
            dev_round=dev_round,
            milestone_type="tests_run",
            status="in_progress",
            title=f"Running tests round {dev_round}",
        )
        try:
            targeted_test_context = self._build_test_execution_context(wf, gh)
        except Exception as exc:
            logger.warning(
                "Failed to build targeted test context for workflow %s: %s",
                self._workflow_id[:8],
                exc,
            )
            targeted_test_context = (
                "## 本轮定向验证上下文\n"
                "- 自动构建验证上下文失败，请先自行检查最终方案、改动文件和仓库测试约定，"
                "然后选择最小必要的测试范围。"
            )

        test_prompt = (
            AUTONOMOUS_CONTEXT + "请基于最终方案和本轮实际改动，设计并执行一份定向验证矩阵。"
            "如果有失败，修复问题并重新测试。确保必测项全部通过后再结束。\n\n"
        )
        if issue_number:
            test_prompt += (
                f"## 关联 Issue\n"
                f"本任务关联 GitHub Issue #{issue_number}。\n"
                f"测试验证需确保修改满足 Issue #{issue_number} 的所有需求。\n\n"
            )
        test_prompt += f"{targeted_test_context}\n\n"
        test_prompt += self._project_runtime_contract(
            wf.get("worktree_path") or wf.get("project_path", ""), gh
        )
        test_prompt += (
            "## 重要：测试执行策略\n"
            "测试是必须执行的步骤，不能跳过。请严格遵循以下原则：\n"
            "1. 先根据“最终方案/验证计划 + 实际改动文件 + 仓库测试约定”设计验证矩阵，再执行命令。\n"
            "2. 必须优先覆盖上面列出的必测方面；只有在发现跨层影响、契约变化、迁移/依赖/CI 改动等证据时，才扩大到更广范围。\n"
            "3. 除非验证矩阵或仓库文档明确要求，否则不要从裸 `python -m pytest`、`pytest tests/`、全仓库扫描式命令开始。\n"
            "4. 具体命令必须优先从 `.github/workflows/`、`package.json`、`frontend/package.json`、`pytest.ini`、`tests/README.md`、`Makefile`、`tox.ini`、`scripts/` 等仓库事实中映射出来，不要凭空假设。\n"
            "5. 结果汇总时必须交代：验证方面、执行命令、结果、是否已覆盖方案要求；如果某一项不跑，必须说明理由。\n"
            "6. 如果仓库中完全没有明确约定，再按框架后备顺序尝试：\n"
            "   - Python：`python -m pytest` 或 `python3 -m pytest`\n"
            "   - Python 兜底：`python -m unittest discover -s tests`\n"
            "   - 前端项目：`npm test` 或 `npx vitest run`\n"
            "7. 如果所有测试框架都不可用，至少执行以下验证：\n"
            '   - 用 `python -c "import <模块>"` 验证关键模块能正常导入\n'
            "   - 用 `python -m py_compile <文件>` 验证修改的文件没有语法错误\n"
            "   - 手动验证核心功能逻辑\n"
            "8. 如果测试确实无法运行，在回复末尾单独一行输出 `TEST_STATUS: skipped`\n\n"
            "## ⛔ CRITICAL: 测试结果报告格式（强制要求）\n"
            "测试结果报告必须严格遵循以下标准格式之一：\n"
            '- Python pytest: "X passed, Y failed, Z skipped" 或 "X passed in Y.Zs"\n'
            '- JavaScript Jest: "X tests passed, Y tests failed" 或 "Test Suites: X passed"\n'
            '- Go test: "PASS ok" 或 "FAIL FAIL" 或 "X tests passed"\n'
            '- Rust cargo: "test result: ok. X passed; Y failed" 或 "running X tests"\n'
            '- Java Maven/Gradle: "Tests run: X, Failures: Y" 或 "BUILD SUCCESS"\n\n'
            "禁止使用以下非标准格式：\n"
            '- "X个测试全部通过" ❌\n'
            '- "所有测试都成功了" ❌\n'
            '- "测试运行完成，全部通过" ❌\n'
            '- "测试在后台运行中" ❌\n'
            '- "测试进度约50%" ❌\n'
            "如果测试已经真实执行但汇总格式不标准，编排器会记录格式警告；"
            "是否通过仍以测试进程结果为准。\n"
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

        # #2046 Phase A dual-write: persist structured CommandExecutionEvidence
        # from the same event_log the heuristics below consume. Shadow-only —
        # does not influence the verdict (Phase B migrates the gate to this).
        emit_command_evidence(
            session_id=test_result.session_id or test_result.tracking_session_id or "",
            workflow_id=self._workflow_id,
            milestone_id=test_ms.get("milestone_id", ""),
            event_log=test_result.event_log or [],
            sandbox_id=getattr(test_result, "sandbox_id", None),
            sandbox_generation=getattr(test_result, "sandbox_generation", None),
        )
        # #2022 P5: persist sandbox identity + final state onto the workflow row
        # (provider/id/generation/state) so the workflow records which sandbox ran
        # each task and reconciliation (#2022 P6) can probe/clean orphans.
        self._persist_sandbox_attribution(test_result)

        if self._abort_on_repo_integrity_violation(test_result, test_ms.get("milestone_id", "")):
            return

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
        # TEST_STATUS: skipped is a status tag the agent emits on its own line
        # at the end of the reply. Match it as a standalone line, not a bare
        # substring — otherwise an agent that *explains* "TEST_STATUS: skipped
        # 不适用" (i.e. asserts tests DID run) is false-positive matched and
        # the workflow needlessly retries (#1277 regression).
        _skip_tag_re = re.compile(r"(?mi)^\s*TEST_STATUS:\s*skipped\s*$")
        # Chinese skip markers: only count when they appear as a short
        # standalone statement (own line, <= 30 chars), not embedded in a
        # longer sentence like "本次跳过测试是因为..." which is an explanation.
        _cn_skip_re = re.compile(r"(?m)^\s*(测试被跳过|跳过测试)\s*[。.]?\s*$")
        has_skip_tag = bool(_skip_tag_re.search(test_response_text)) or bool(
            _cn_skip_re.search(test_response_text)
        )
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
            # Issue #1538: Japanese skipped keywords
            "pytestがインストールされていません",
            "テストフレームワークが利用できません",
            "コマンドがブロックされました",
            # Issue #1538: Korean skipped keywords
            "pytest가 설치되지 않았습니다",
            "테스트 프레임워크를 사용할 수 없습니다",
            "명령이 차단되었습니다",
        ]
        has_skip_keyword = any(kw in test_response_text for kw in _skipped_keywords)
        # ── Framework-aware test detection (Phase 1, P0) ──────────────────────
        # Infer framework type for layered detection strategy
        framework_type = _infer_test_framework(wf.get("project_path", ""), wf.get("cli_tool", ""))
        logger.debug(
            "Inferred test framework: %s for workflow %s", framework_type, self._workflow_id[:8]
        )

        # Detect hallucination patterns: agent claims tests are running but
        # outputs only descriptive text, not actual pytest results.
        _hallucination_patterns = [
            # Chinese: "测试在后台运行中", "测试进度约50%", etc.
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            # English: "tests running in background", "progress 50%", etc.
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
            # Issue #1538: Japanese hallucination patterns
            # Japanese: "テストがバックグラウンドで実行中", "テスト進捗50%"
            r"テスト.*バックグラウンド",
            r"テスト.*実行中",
            r"テスト.*進捗.*%",
            # Issue #1538: Korean hallucination patterns
            # Korean: "테스트가 백그라운드에서 실행 중", "테스트 진행 50%"
            r"테스트.*백그라운드",
            r"테스트.*실행.*중",
            r"테스트.*진행.*%",
        ]
        has_hallucination_desc = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _hallucination_patterns
        )

        # Framework-specific pytest output patterns (Layer 1)
        _pytest_output_patterns = [
            # pytest summary line: "3 passed, 2 failed" or "1 passed in 2.5s"
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            # pytest completion marker: "PASSED in 2.5s" / "FAILED in 1.2s"
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            # pytest summary banner: "= 3 passed in 2.50s =" (short form)
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            # pytest session start: "test session starts" / "collected 5 items"
            r"test session starts",
            r"collected\s+\d+\s+items",
            # pytest individual test result: "PASSED" / "FAILED" as standalone
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            # assertion error marker (real pytest output)
            r"AssertionError",
            # Issue #1538: Chinese output patterns
            # Chinese: "通过: 2398 个", "失败: 5 个", "跳过: 69 个"
            r"(通过|成功)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(失败|错误)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(跳过|忽略)[:：\s]+\d+\s*(个|项|件|测试)?",
            # Chinese reverse format: "2398个测试通过", "2398 个 通过"
            r"\d+\s*(个|项|件|测试)\s*(通过|成功|失败|跳过)",
            # Issue #1538: Japanese output patterns
            # Japanese: "通過: 2398 件", "失敗: 5件", "スキップ: 69件"
            r"(通過|成功)[:：\s]+\d+\s*(件|テスト)?",
            r"(失敗|エラー)[:：\s]+\d+\s*(件|テスト)?",
            r"(スキップ|スキップ済み)[:：\s]+\d+\s*(件|テスト)?",
            # Issue #1538: Korean output patterns
            # Korean: "통과: 2398개", "실패: 5개", "건너뜀: 69개"
            r"(통과|성공)[:：\s]+\d+\s*(개|테스트)?",
            r"(실패|오류)[:：\s]+\d+\s*(개|테스트)?",
            r"(건너뜀|스킵)[:：\s]+\d+\s*(개|테스트)?",
            # Issue #1544: Additional Chinese output patterns
            # Chinese: "2216 个测试全部通过", "所有 2216 个单元测试通过"
            r"\d+\s*(个|项|件)\s*测试\s*(全部|全都|都)?\s*(通过|成功)",
            r"(所有|全部|全部的)\s*\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
        ]

        # Jest patterns for JavaScript projects
        _jest_patterns = [
            r"PASS\s+\d+\s+tests",
            r"FAIL\s+\d+\s+tests",
            r"Test Suites:\s*\d+\s+passed",
            r"Tests:\s*\d+\s+passed",
            r"Snapshots:\s*\d+\s+passed",
        ]

        # Go test patterns for Go projects
        _go_test_patterns = [
            r"PASS\s+ok",
            r"FAIL\s+FAIL",
            r"===\s+RUN",
            r"---\s+PASS:",
            r"---\s+FAIL:",
            r"PASS\s+\(\d+\s+tests\)",
        ]

        # Unittest patterns for Python unittest
        _unittest_patterns = [
            r"OK\s+\(\d+\s+tests\)",
            r"FAILED\s+\(failures=\d+\)",
            r"ERROR\s+\(errors=\d+\)",
            r"Traceback\s+\(most\s+recent\s+call\s+last\)",
        ]

        # Issue #1538: Rust cargo test patterns
        _rust_patterns = [
            r"running\s+\d+\s+tests",
            r"test\s+result:\s*ok",
            r"test\s+result:\s*FAILED",
            r"\d+\s+passed;\s*\d+\s+failed",
            r"\d+\s+passed",
        ]

        # Issue #1538: Java Maven/Gradle patterns
        _java_patterns = [
            r"Tests\s+run:\s*\d+",
            r"Failures:\s*\d+",
            r"Errors:\s*\d+",
            r"BUILD\s+SUCCESS",
            r"BUILD\s+SUCCESSFUL",
            r"BUILD\s+FAILURE",
            r"FAILURE!",
        ]

        # Layered detection based on framework type
        has_actual_pytest_output = False

        if framework_type == "python":
            # Python projects: pytest + unittest, no keyword fallback
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
            ) or any(re.search(p, test_response_text, re.IGNORECASE) for p in _unittest_patterns)
        elif framework_type == "javascript":
            # JavaScript projects: Jest patterns + strict keywords
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _jest_patterns
            ) or _has_strict_keyword_result(test_response_text, has_hallucination_desc)
        elif framework_type == "go":
            # Go projects: go test patterns + strict keywords (PASS/FAIL + ok)
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _go_test_patterns
            ) or _has_strict_keyword_result(test_response_text, has_hallucination_desc)
        elif framework_type == "rust":
            # Issue #1538: Rust projects: cargo test patterns + strict keywords
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _rust_patterns
            ) or _has_strict_keyword_result(test_response_text, has_hallucination_desc)
        elif framework_type == "java":
            # Issue #1538: Java projects: Maven/Gradle patterns + strict keywords
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _java_patterns
            ) or _has_strict_keyword_result(test_response_text, has_hallucination_desc)
        elif framework_type == "mixed":
            # Mixed projects: combine all patterns + strict keywords
            all_patterns = (
                _pytest_output_patterns
                + _jest_patterns
                + _go_test_patterns
                + _unittest_patterns
                + _rust_patterns
                + _java_patterns
            )
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in all_patterns
            ) or _has_strict_keyword_result(test_response_text, has_hallucination_desc)
        else:  # unknown
            # Unknown framework: pytest + multilingual patterns, NO keyword fallback
            has_actual_pytest_output = any(
                re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
            )

        # Final test result determination
        has_test_result = has_actual_pytest_output
        test_status_tag = self._artifact_status_tag(test_result, "test_status").lower()

        # A tool invocation proves only that the agent *attempted* to run a
        # test command. It does not include the result/exit code, so it must
        # never be treated as passing evidence by itself.
        has_test_tool_call = _has_test_tool_call(test_result.tool_calls or [], framework_type)

        has_passing_tool_result = _has_passing_test_tool_result(
            test_result.event_log or [], framework_type
        )
        # Fallback: if the event log's tool_result capture is incomplete (e.g.
        # Claude Code stream-json truncation on long pytest output, or tool
        # results exceeding the per-event size limit), accept text-based
        # evidence: a real test tool call in the event log PLUS a "N passed"
        # pattern in the agent's visible text. The tool call proves the agent
        # invoked a test command; the "N passed" pattern proves it reported
        # passing results. Together they provide strong evidence that tests
        # ran and passed, even without structured tool_result events (#1830).
        has_text_pass_evidence = has_test_result and bool(
            re.search(r"\b[1-9]\d*\s+passed\b", test_response_text, re.IGNORECASE)
        )
        # #2046 Phase B: structured evidence is authoritative for PASSED/FAILED;
        # the legacy heuristic stays as the INCONCLUSIVE/NOT_RUN fallback
        # (#2046 §4 降级). Agent prose / TEST_STATUS can no longer promote a
        # run to PASSED on its own (#1967 invariant).
        structured_verdict, _structured_evidences, structured_reason = (
            self._compute_structured_test_verdict(test_result, framework_type, test_ms)
        )
        structured_authoritative = structured_verdict in (
            ExecutionVerdict.PASSED,
            ExecutionVerdict.FAILED,
        )
        if structured_verdict == ExecutionVerdict.PASSED:
            tests_actually_run = True
        elif structured_verdict == ExecutionVerdict.FAILED:
            tests_actually_run = False
        else:  # NOT_RUN / INCONCLUSIVE — fall back to the legacy heuristic.
            tests_actually_run = has_passing_tool_result or (
                has_test_tool_call and has_text_pass_evidence
            )
            self._emit_structured_test_fallback(
                structured_verdict, structured_reason, test_ms.get("milestone_id", "")
            )
        # test_result_inconclusive only applies when the structured layer could
        # not judge authoritatively; a structured FAILED is a real failure,
        # not an inconclusive run.
        test_result_inconclusive = (
            not structured_authoritative
            and test_result.success
            and (has_test_tool_call or has_test_result or test_status_tag in ("passed", "failed"))
            and not tests_actually_run
        )

        # #2046 shadow comparison: record divergence between the structured
        # test verdict and the heuristic. Phase A compared at command level;
        # Phase B compares the run-level verdict that now drives the gate.
        self._shadow_compare_evidence(
            test_result=test_result,
            milestone_id=test_ms.get("milestone_id", ""),
            heuristic_passed=has_passing_tool_result
            or (has_test_tool_call and has_text_pass_evidence),
            structured_verdict=structured_verdict,
        )

        # Issue #1547: Validate test report format
        # If agent used non-standard format but tests actually ran, trigger retry
        format_valid, format_reason = self._validate_test_report_format(test_response_text)
        has_non_standard_format = not format_valid and tests_actually_run

        # A structured PASSED/FAILED means tests really ran (and passed or
        # failed) — that is never a "skipped" outcome. Skip detection only
        # applies when the structured layer deferred to the heuristic.
        tests_actually_skipped = not structured_authoritative and (
            test_status_tag == "skipped"
            or has_skip_tag
            or (test_result.success and has_skip_keyword and not tests_actually_run)
            or (
                test_result.success
                and not tests_actually_run
                and not has_test_tool_call
                and not has_test_result
            )
        )

        # Post test results to issue
        issue_number = wf.get("github_issue_number")
        if issue_number:
            if tests_actually_skipped:
                status_line = "⚠️ Tests were not actually run — see details below"
            elif test_result_inconclusive:
                status_line = "⚠️ Test command was invoked but no verifiable result was captured"
            elif test_result.success:
                status_line = "✅ All tests passed"
            else:
                status_line = "❌ Tests failed"
            test_comment = (
                f"## 🧪 Test Results (Dev Round {dev_round})\n\n{status_line}\n\n{test_summary}"
            )
            self._post_github_comment(gh, issue_number, test_comment, context="test-results")

        if test_result_inconclusive:
            message = (
                "Test execution is inconclusive: a test command was invoked, but no "
                "structured TEST_STATUS or recognizable pass/fail output was captured"
            )
            self.repo.update_milestone(
                test_ms.get("milestone_id", ""),
                {"status": "failed", "error_message": message},
            )
            test_retries = int(wf.get("test_retries", 0) or 0) + 1
            if test_retries <= MAX_TEST_RETRIES:
                self._update_workflow({"test_retries": test_retries})
                return
            self._update_workflow({"status": "failed", "error_message": message})
            return

        # Treat skipped tests as failure — tests must actually run.
        # Allow 1 retry in case of transient environment issues.
        if tests_actually_skipped:
            primary_error = self._primary_result_error(test_result)
            if primary_error:
                self.repo.update_milestone(
                    test_ms.get("milestone_id", ""),
                    {
                        "status": "failed",
                        "error_message": primary_error,
                    },
                )
                self._update_workflow(
                    {
                        "status": "failed",
                        "error_message": f"Testing failed: {primary_error}",
                    }
                )
                return
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

        # Formatting is presentation metadata, not execution state.  When tool
        # calls/output prove tests actually ran, a non-standard summary must not
        # re-enter the scheduler and accidentally launch development again.
        if has_non_standard_format:
            logger.warning(
                "Non-standard test report format for dev round %d; using execution evidence: %s",
                dev_round,
                format_reason,
            )
            if issue_number:
                format_comment = (
                    f"⚠️ **Format Warning**: Test report used non-standard format.\n"
                    f"Reason: {format_reason}\n\n"
                    f"Tests were executed successfully, but format validation failed.\n"
                    f"Please ensure future reports follow standard format guidelines."
                )
                self._post_github_comment(
                    gh, issue_number, format_comment, context="format-warning"
                )

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
        self._update_workflow(
            {
                "test_retries": 0,
                "dev_retries_on_test_fail": 0,
                "skip_retries": 0,
                "ci_repair_context": "",
            }
        )

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
        """Test-compat shim (#2044 Phase B T11).

        The pr_review phase lives in ``phases/pr_review.py`` (registered in
        ``PHASE_HANDLERS``); advance()'s production path resolves it via the
        registry, NOT through this method. This thin wrapper exists only so the
        many ``o._do_pr_review(wf)`` direct callers in tests/ (and any
        ``patch('AutonomousOrchestrator._do_pr_review')`` sites that influence a
        direct call rather than advance()) keep working with zero per-test
        edits. It builds the (ctx, deps) bundle, delegates to
        ``phases.pr_review.handle``, and commits the returned PhaseResult
        through the single authoritative entrypoint — same behaviour as
        advance()'s dispatch.

        ACCEPTED DEBT (#2044 T14): kept rather than removed. Migrating the
        ~60+ direct-caller tests to invoke the registry handler (or advance())
        is out of scope for #2044 — these are test helpers, production
        advance() uses the registry. Remove in a follow-up that retires the
        direct-call test pattern wholesale.

        Behaviour note: the legacy ``_do_pr_review`` raised ``WorkflowPaused``
        when ``_poll_ci_status`` was interrupted by shutdown (the
        ``except WorkflowPaused: cancel + raise`` branch). The migrated handler
        preserves that raise (it re-raises after cancelling the in-progress
        milestone), so direct-call tests that assert the raise keep working
        without a shim-level re-raise — the handler's own raise propagates
        through here unchanged.
        """
        from app.modules.workspace.autonomous import phases as _phases

        # Legacy ``_do_pr_review`` called ``gh = self._get_gh()`` first (the
        # lazy initializer populates ``self._gh``); many direct-call tests stub
        # ``orch._get_gh.return_value`` rather than ``orch._gh``. Resolve the gh
        # binding through the same lazy path AND assign it to ``self._gh`` so
        # ``deps.gh`` (which reads ``self._gh`` via _build_phase_deps) sees the
        # same mock the legacy method did.
        self._gh = self._get_gh()
        ctx = self._build_workflow_context(wf)
        deps = self._build_phase_deps()
        result = _phases.pr_review.handle(ctx, deps)
        if result is not None:
            self._commit_phase_result(result)

    def _apply_pr_review_fix(
        self,
        wf: dict,
        gh,
        review_text: str,
        round_num: int,
        dev_round: int,
        ci_failures: list,
        pr_number,
    ) -> bool:
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

        def fail_fix(message: str, result: AgentTaskResult | None = None) -> bool:
            """Fail closed before the orchestrator stages or pushes anything."""
            self.repo.update_milestone(
                fix_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "session_id": result.session_id if result else "",
                    "error_message": message,
                    "result_summary": (
                        self._artifact_text(result)[:200] if result is not None else ""
                    ),
                },
            )
            self._update_workflow({"status": "failed", "error_message": message})
            return False

        fix_prompt = (
            AUTONOMOUS_CONTEXT
            + f"根据以下代码审查意见修改代码：\n\n{self._clean_agent_text(review_text)}\n\n"
        )
        # Issue reference is available in this method's scope
        issue_number = wf.get("github_issue_number") or self.workflow.get("github_issue_number")
        if issue_number:
            fix_prompt += (
                f"## 关联 Issue\n"
                f"本任务关联 GitHub Issue #{issue_number}。\n"
                f"修复时请确保修改满足 Issue #{issue_number} 的所有需求。\n\n"
            )
        fix_prompt += (
            "重要要求：\n"
            "1. 修改完成后，运行项目测试确保所有测试通过\n"
            "2. 如果测试失败，分析失败原因：\n"
            "   - 如果是本 PR 引入的问题，修复后重新运行测试\n"
            "   - 如果是预先存在的问题（与本 PR 修改的文件无关），在回复末尾"
            "单独一行输出 `CI_STATUS: pre-existing`\n"
            "3. 确认测试通过后保留工作区修改；不要执行 git add、git commit 或 git push，编排器会校验、提交并推送\n"
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

        # A review-fix call can only attribute and auto-stage changes safely
        # when it starts from a clean tree. If the worktree is dirty (e.g. a
        # previous dev/CI-repair round left formatting edits uncommitted), commit
        # those pre-existing changes first so the fix agent starts from a clean
        # tree. Refusing to proceed creates a dead-end: retrying hits the same
        # dirty worktree (#1828/#1830).
        #
        # Scope safety: capture commit_before BEFORE staging so the pre-existing
        # diff is also validated by _validate_autonomous_change_scope below
        # (called with commit_before_staging..commit_after_staging). Without
        # this, unrelated/unreviewed content in the dirty tree would be pushed
        # to the PR branch without scope validation.
        commit_before_staging = ""
        try:
            dirty_before = gh.has_uncommitted_changes()
        except Exception as exc:
            return fail_fix(f"Unable to verify clean worktree before PR review fix: {exc}")
        if dirty_before is True:
            try:
                commit_before_staging = gh.get_current_commit()
                gh.git_add_all()
                gh.git_commit(
                    "auto: stage pre-existing worktree changes before review fix",
                    no_verify=True,
                )
                staged_sha = gh.get_current_commit()
                self._record_trusted_head(gh, sha=staged_sha)
                # Validate the pre-existing changes against the autonomous
                # scope guard. If they touch files outside the allowed scope
                # (e.g. unrelated test/manual edits), refuse — do NOT push
                # out-of-scope content to the PR branch.
                pre_scope_error = self._validate_autonomous_change_scope(
                    gh, wf, commit_before_staging, staged_sha
                )
                if pre_scope_error:
                    try:
                        gh.reset_hard_to(commit_before_staging)
                    except Exception as reset_exc:
                        pre_scope_error += (
                            "; failed to discard the rejected pre-existing commit: " f"{reset_exc}"
                        )
                    return fail_fix(
                        f"Pre-existing worktree changes failed scope validation: {pre_scope_error}"
                    )
                logger.warning(
                    "Workflow %s: worktree was dirty before review fix; "
                    "committed and scope-validated pre-existing changes before "
                    "running fix agent",
                    self._workflow_id[:8],
                )
            except Exception as exc:
                # On any failure during staging, reset to the pre-staging
                # commit so we don't leave the tree in a half-staged state.
                if commit_before_staging:
                    try:
                        gh.reset_hard_to(commit_before_staging)
                    except Exception:
                        pass
                return fail_fix(
                    f"Worktree was dirty and auto-staging pre-existing changes failed: {exc}"
                )

        commit_before = ""
        try:
            commit_before = gh.get_current_commit()
        except Exception as exc:
            return fail_fix(f"Unable to capture branch HEAD before PR review fix: {exc}")

        fix_result = self._run_agent_with_context_recovery(
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

        if self._abort_on_repo_integrity_violation(fix_result, fix_ms.get("milestone_id", "")):
            return False

        if self._is_context_overflow(fix_result):
            message = (
                "PR review fix failed after context recovery: "
                f"{fix_result.error or self._artifact_text(fix_result) or 'no result'}"
            )
            return fail_fix(message, fix_result)

        if not fix_result.success:
            message = (
                "PR review fix agent failed: "
                f"{fix_result.error or self._artifact_text(fix_result) or 'no result'}"
            )
            return fail_fix(message, fix_result)

        if not self._artifact_text(fix_result).strip():
            return fail_fix("PR review fix agent returned no result", fix_result)

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
        except Exception as exc:
            return fail_fix(f"Unable to inspect branch HEAD after PR review fix: {exc}", fix_result)
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
                    self._record_trusted_head(gh, sha=commit_sha)
                    if not commit_sha or commit_sha == commit_before:
                        raise RuntimeError("review-fix commit did not advance branch HEAD")
                    sha_changed = True
            except Exception as e:
                logger.warning("Fix auto-commit failed: %s", e)
                return fail_fix(f"Unable to commit PR review fix: {e}", fix_result)

        # Track push failure to decide milestone status and avoid misleading comment
        push_failed = False
        push_error_msg = ""
        if sha_changed:
            push_error_msg = self._validate_autonomous_change_scope(
                gh, wf, commit_before, commit_sha
            )
            if push_error_msg:
                push_failed = True
                logger.error("Review fix scope rejected before push: %s", push_error_msg)
            try:
                if not push_failed:
                    gh.git_push(branch=wf.get("branch_name"), force_with_lease=True)
            except Exception as e:
                # Distinguish transient vs non-transient to enable Layer-2 retry
                # (Issue #1814).
                if _is_transient_git_error(e):
                    # Transient: propagate to trigger Layer-2 retry
                    logger.warning(
                        "Transient push failure in review fix round %d: %s",
                        round_num,
                        e,
                    )
                    raise
                else:
                    # Non-transient: mark failed, don't post misleading comment
                    push_failed = True
                    push_error_msg = str(e)
                    logger.error("Fix git_push failed (round %d): %s", round_num, e, exc_info=True)

        # Clear commit_sha on push failure to avoid referencing unpushed commit
        if push_failed:
            commit_sha = ""

        try:
            diff_stats = gh.get_commit_diff_stats(commit_sha) if commit_sha else {}
        except Exception:
            pass

        if push_failed:
            self.repo.update_milestone(
                fix_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "error_message": f"Push failed after review fix: {push_error_msg}",
                    "session_id": fix_result.session_id,
                    "commit_shas": json.dumps([]),
                    "diff_stats": json.dumps({}),
                    "result_summary": self._artifact_text(fix_result)[:200],
                    "tldr": self._artifact_tldr(fix_result),
                },
            )
            self._update_workflow(
                {
                    "status": "failed",
                    "error_message": f"Review fix was not pushed: {push_error_msg}",
                }
            )
            return False
        else:
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

        # Only post success comment if push succeeded (Issue #1814)
        if pr_number and not push_failed:
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
                comment += "\n> ⚠️ 部分 CI 检查失败，但经分析为预先存在的问题，非本 PR 引入。\n"
            self._post_github_comment(gh, pr_number, comment, is_pr=True, context="fix")
        return True

    # ── Phase: Report ───────────────────────────────────────────────

    def _do_report(self, ctx: WorkflowContext, deps: PhaseDeps) -> PhaseResult:
        """Generate progress report and update issue.

        Phase B #2044 T6: migrated onto the PhaseResult contract. Builds the
        report (no business-logic change), then returns a PhaseResult advancing
        to ``wait`` instead of writing ``current_phase``/``status`` inline. The
        three milestones it emits (progress_reported / round_completed /
        wait_started) travel in ``milestone_events`` and are committed by
        ``_commit_phase_result``; the phase_change event is emitted through
        ``deps.host`` so the commit entrypoint stays the sole phase/status
        authority.

        Until T10-T12 extract phases into phases/*.py, gh/repo stay on self;
        deps here carries the host (emit_phase_change) for this thin phase.
        """
        wf = ctx.workflow
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

        milestone_events = [
            {
                "phase": "report",
                "dev_round": dev_round,
                "milestone_type": "progress_reported",
                "status": "completed",
                "title": f"Progress report for round {dev_round}",
                "metadata": report_metadata,
            },
        ]

        # Post report to issue
        if issue_number:
            self._post_github_comment(gh, issue_number, report_markdown, context="progress-report")

        # Mark round completed
        milestone_events.append(
            {
                "phase": "report",
                "dev_round": dev_round,
                "milestone_type": "round_completed",
                "status": "completed",
                "title": f"Dev round {dev_round} completed",
            }
        )

        # Record wait phase start time for comment filtering
        milestone_events.append(
            {
                "phase": "report",
                "dev_round": dev_round,
                "milestone_type": "wait_started",
                "status": "completed",
                "title": "Wait phase starting",
                "metadata": json.dumps({"wait_started_at": datetime.now(timezone.utc).isoformat()}),
            }
        )

        # Emit the phase_change event through the host (the commit entrypoint
        # does NOT emit phase_change — Phase A contract — so the handler emits
        # its own). Payload is identical to the legacy inline _emit call.
        deps.host.emit_phase_change({"phase": "wait"})

        # Move to wait phase — advance via PhaseResult rather than an inline
        # current_phase/status write. Same decision the legacy method made.
        return PhaseResult.completed(
            next_phase="wait",
            next_status="waiting",
            milestone_events=milestone_events,
        )

    # ── Phase: Wait ─────────────────────────────────────────────────

    def _do_wait(self, ctx: WorkflowContext, deps: PhaseDeps) -> PhaseResult:
        """Poll for new requirements or completion signal.

        If user_feedback is stored on the workflow (from cancel-with-feedback),
        resume immediately from the cancelled milestone's phase.

        If auto_merge is enabled and PR exists, skip waiting and proceed to merge.

        Invariant: must not mutate the git working tree — relied on by the
        scheduler's waiting-bypass. ``autonomous_scheduler._process_workflows``
        skips batch/workspace/branch conflict locks for ``status == waiting``
        workflows on the assumption that this phase only touches DB/API state.
        Any git or agent work must happen in a later phase, after the workflow
        has left ``waiting``.

        Phase B #2044 T7: migrated onto the PhaseResult contract. Same
        decisions as the legacy inline-commit method, only the recording
        mechanism changes: phase/status transitions travel on the returned
        ``PhaseResult`` (``completed`` advances ``current_phase``; ``wait``
        parks the workflow), the ``requirement_received`` milestone travels in
        ``milestone_events``, and the phase_change event is emitted through
        ``deps.host`` so the commit entrypoint stays the sole phase/status
        authority. The four forbidden fields (current_phase/status/completed_at/
        paused_at) are no longer written inline by this handler.

        Until T10-T12 extract phases into phases/*.py, gh/repo stay on self;
        deps here carries the host (emit_phase_change) for this thin phase.
        """
        wf = ctx.workflow
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
            # Emit the phase_change event through the host (the commit entrypoint
            # does NOT emit phase_change — Phase A contract — so the handler emits
            # its own). Payload is identical to the legacy inline _emit call.
            deps.host.emit_phase_change(
                {"phase": cancelled_phase, "dev_round": new_dev_round, "resumed": True}
            )
            # Resume from the cancelled phase — advance via PhaseResult rather
            # than an inline current_phase/status write. Same decision the legacy
            # method made; dev_round/current_round are non-forbidden workflow
            # fields and travel in workflow_patch.
            return PhaseResult.completed(
                next_phase=cancelled_phase,
                next_status=PHASE_STATUS_MAP.get(cancelled_phase, "developing"),
                workflow_patch={"dev_round": new_dev_round, "current_round": 0},
            )

        # Auto merge check for batch workflows
        auto_merge = wf.get("auto_merge", True)
        github_pr_number = wf.get("github_pr_number")
        if auto_merge and github_pr_number:
            # PR exists and auto_merge enabled - skip waiting, go directly to merge
            logger.info(
                "Auto merge enabled for workflow %s, proceeding to merge phase",
                self._workflow_id[:8],
            )
            deps.host.emit_phase_change({"phase": "merge", "auto_merge": True})
            return PhaseResult.completed(
                next_phase="merge",
                next_status="merging",
            )

        # Original behavior: poll GitHub issue comments
        issue_number = wf.get("github_issue_number")
        gh = self._get_gh()

        if not issue_number:
            return PhaseResult.wait()  # No issue to check

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
            return PhaseResult.wait()

        if not comments:
            return PhaseResult.wait()  # No new comments

        # Filter out bot's own comments (comments authored by the automation)
        user_comments = [c for c in comments if not _is_bot_comment(c)]

        # Check for completion signals — match whole words/lines only
        for comment in reversed(user_comments):
            body = comment.get("body", "")
            for pattern in _COMPLETION_PATTERNS:
                if pattern.search(body):
                    deps.host.emit_phase_change({"phase": "merge"})
                    return PhaseResult.completed(
                        next_phase="merge",
                        next_status="merging",
                    )

        # No user comments left after filtering
        if not user_comments:
            return PhaseResult.wait()

        # New requirements detected
        new_req_comment = user_comments[-1]  # Latest user comment
        new_requirements = new_req_comment.get("body", "")
        # Use correct field name from gh CLI (camelCase)
        comment_time = new_req_comment.get("createdAt", "")

        milestone_events = [
            {
                "phase": "wait",
                "milestone_type": "requirement_received",
                "status": "completed",
                "title": "New requirements detected",
                "result_summary": new_requirements[:200],
                "metadata": json.dumps({"last_comment_time": comment_time}),
            }
        ]

        new_dev_round = wf.get("dev_round", 1) + 1
        deps.host.emit_phase_change({"phase": "planning", "dev_round": new_dev_round})
        # Start a new dev round — advance via PhaseResult. current_phase/status
        # come from next_phase/next_status; dev_round/current_round/
        # requirements_text are non-forbidden workflow fields in workflow_patch.
        return PhaseResult.completed(
            next_phase="planning",
            next_status="planning",
            workflow_patch={
                "current_round": 0,
                "dev_round": new_dev_round,
                "requirements_text": new_requirements,
            },
            milestone_events=milestone_events,
        )

    # ── Phase: Merge ────────────────────────────────────────────────

    def _do_merge(self, wf: dict):
        """Test-compat shim (#2044 Phase B T10).

        The merge phase lives in ``phases/merge.py`` (registered in
        ``PHASE_HANDLERS``); advance()'s production path resolves it via the
        registry, NOT through this method. This thin wrapper exists only so the
        many ``o._do_merge(wf)`` direct callers in tests/ (and any
        ``patch('AutonomousOrchestrator._do_merge')`` sites that influence a
        direct call rather than advance()) keep working with zero per-test
        edits. It builds the (ctx, deps) bundle, delegates to
        ``phases.merge.handle``, and commits the returned PhaseResult through
        the single authoritative entrypoint — same behaviour as advance()'s
        dispatch.

        ACCEPTED DEBT (#2044 T14): kept rather than removed. Migrating the
        ~60+ direct-caller tests to invoke the registry handler (or advance())
        is out of scope for #2044 — these are test helpers, production
        advance() uses the registry. Remove in a follow-up that retires the
        direct-call test pattern wholesale.

        Behaviour note: the legacy ``_do_merge`` raised ``WorkflowPaused`` on
        the merge-policy-pause branch, and direct-call tests assert that raise.
        The migrated handler returns ``PhaseResult.pause`` instead (advance()
        honours it by skipping the transient-retry reset). This shim re-raises
        ``WorkflowPaused`` after committing a pause result so those direct-call
        tests see the same exception they did before — production advance()
        never goes through this shim (it uses the registry), so the re-raise is
        test-compat only.
        """
        from app.modules.workspace.autonomous import phases as _phases

        ctx = self._build_workflow_context(wf)
        deps = self._build_phase_deps()
        result = _phases.merge.handle(ctx, deps)
        if result is not None:
            self._commit_phase_result(result)
            if result.outcome == "pause":
                # Re-raise so direct-call tests observe the legacy
                # WorkflowPaused (production advance() does NOT go through this
                # shim — it routes via the registry and the result path).
                message = ""
                if isinstance(result.structured_error, dict):
                    message = str(result.structured_error.get("message") or "")
                raise WorkflowPaused(message)

    def _sync_worktree_to_pr_remote_head(self, wt_gh: "GitHubOps", branch_name: str) -> None:
        return self._git_workspace.sync_worktree_to_pr_remote_head(wt_gh, branch_name)

    def _resolve_merge_conflicts(self, gh: GitHubOps, branch_name: str, pr_number: int):
        return self._git_workspace.resolve_merge_conflicts(gh, branch_name, pr_number)

    # ── Worktree transition journal (Issue #2050) ───────────────────
    # These helpers persist a minimal crash-recovery journal so a SIGKILL at
    # any git/DB boundary can be reconciled from the persisted intent + the
    # observed git registry/disk state. They never touch branch refs (that is
    # #2042's head-authority job).

    def _set_transition_state(
        self,
        state: str,
        *,
        original_path: str | None = None,
        temp_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Advance the persisted worktree transition journal (#2050).

        Writes ``worktree_transition_state`` and refreshes
        ``transition_updated_at``. ``transition_started_at`` is stamped once
        when the journal begins (state moves out of NULL). Path fields are only
        overwritten when provided, so a mid-transition update does not clobber
        the original/temp paths captured at the start.
        """
        wf = self.workflow
        updates: dict = {"worktree_transition_state": state}
        now = datetime.now(timezone.utc).isoformat()
        updates["transition_updated_at"] = now
        if not wf.get("transition_started_at"):
            updates["transition_started_at"] = now
        if original_path is not None:
            updates["transition_original_path"] = original_path
        if temp_path is not None:
            updates["transition_temp_path"] = temp_path
        updates["transition_error"] = error
        self._update_workflow(updates)

    def _clear_transition_journal(self, *, worktree_path: str | None = None) -> None:
        return self._git_workspace.clear_transition_journal(worktree_path=worktree_path)

    def _fail_transition_closed(self, reason: str, *, error_message: str | None = None) -> None:
        """Mark the transition unrecoverable and fail the workflow closed (#2050).

        Keeps the journal + paths for diagnosis; no later phase runs. The status
        mirrors the in-process restore-failure precedent (status=failed).
        """
        updates: dict = {
            "worktree_transition_state": "recovery_failed",
            "transition_error": reason,
            "transition_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error_message:
            updates["error_message"] = error_message
        updates["status"] = "failed"
        self._update_workflow(updates)

    @staticmethod
    def _is_registered_on_branch(entries: list[dict], path: str, branch: str) -> bool:
        """True if ``path`` is a registered worktree on ``branch``."""
        for w in entries:
            if w.get("path") == path:
                actual = w.get("branch")
                return actual in (branch, f"refs/heads/{branch}")
        return False

    def _verify_worktree_registered(self, gh: GitHubOps, path: str, branch: str) -> None:
        return self._git_workspace.verify_worktree_registered(gh, path, branch)

    def _path_within_worktrees_root(self, path: str, project_path: str) -> bool:
        return self._git_workspace.path_within_worktrees_root(path, project_path)

    def _reconcile_worktree_transition(self, wf: dict) -> None:
        return self._git_workspace.reconcile_worktree_transition(wf)

    def _reconcile_cleanup_temp_and_restore(
        self,
        main_gh: GitHubOps,
        entries: list[dict],
        temp_path: str,
        original_path: str,
        branch_name: str,
        system_account,
    ) -> None:
        """Shared reconcile body: remove temp (if any), restore original (#2050).

        Validates ownership before each git mutation. Raises ``_ReconcileFailed``
        on ambiguity so the caller fails closed with a usable message.
        """
        # 1. Tear down a lingering temp worktree. It holds untrusted half-done
        #    agent work and (if registered) blocks the branch.
        if temp_path:
            temp_entry = next((w for w in entries if w.get("path") == temp_path), None)
            if temp_entry is not None:
                actual_branch = temp_entry.get("branch")
                if actual_branch not in (branch_name, f"refs/heads/{branch_name}"):
                    # A temp registered on the wrong branch is not ours to remove.
                    raise _ReconcileFailed(
                        f"temp worktree {temp_path!r} registered on unexpected "
                        f"branch {actual_branch!r} (expected {branch_name!r})"
                    )
                self._remove_worktree_idempotent(main_gh, temp_path)
                logger.info("Reconcile removed temp worktree %s", temp_path)
                # Refresh the registry view before checking the original.
                entries = main_gh.list_worktrees()

        # 2. Restore the original. If it is already registered on the right
        #    branch, the restore already happened — just verify. Otherwise
        #    re-attach the existing branch to its path. We do NOT create or
        #    reset the branch here (#2042 owns head authority): add_worktree on
        #    a missing branch would error, which we fail closed on.
        original_entry = next((w for w in entries if w.get("path") == original_path), None)
        if original_entry is not None:
            actual_branch = original_entry.get("branch")
            if actual_branch not in (branch_name, f"refs/heads/{branch_name}"):
                raise _ReconcileFailed(
                    f"original worktree {original_path!r} registered on unexpected "
                    f"branch {actual_branch!r} (expected {branch_name!r})"
                )
        else:
            # Not registered → re-attach. add_worktree on the existing branch.
            git_file = os.path.join(original_path, ".git")
            if not main_gh.path_exists_as_user(git_file, file_only=True):
                try:
                    main_gh.add_worktree(original_path, branch_name)
                except GitHubOpsError as e:
                    # Most commonly: branch ref missing/divergent. That is #2042's
                    # decision, not ours — fail closed rather than recreate it.
                    raise _ReconcileFailed(
                        f"cannot re-attach original worktree {original_path!r} "
                        f"to branch {branch_name!r} (branch missing or "
                        f"divergent — needs #2042 head authority): {e}"
                    )
        # Verify registration + branch on the canonical gh view.
        self._verify_worktree_restored(main_gh, original_path, branch_name)
        # Converge DB path + journal in one write, rebind cached gh.
        self._clear_transition_journal(worktree_path=original_path)
        self._gh = GitHubOps(original_path, system_account=system_account)
        logger.info("Reconcile restored original worktree at %s", original_path)

    def _remove_worktree_idempotent(self, gh: GitHubOps, path: str) -> None:
        return self._git_workspace.remove_worktree_idempotent(gh, path)

    def _verify_worktree_restored(self, gh: GitHubOps, path: str, branch: str) -> None:
        return self._git_workspace.verify_worktree_restored(gh, path, branch)
