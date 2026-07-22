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
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

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
from app.modules.workspace.autonomous.github_ops import _FAILURE_LINE_RE, GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.models import AgentTaskResult
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


_TEST_OUTPUT_FILTER_RE = re.compile(
    r"(?:\s+2>\&1)?\s*\|\s*(?:head|tail)(?:\s+-[^\s]+|\s+\d+)*\s*$",
    re.IGNORECASE,
)


def _normalize_test_command(command: str) -> str:
    """Return a stable identity for a test command across output-only filters.

    Autonomous agents commonly run ``pytest ... | head -100`` while exploring
    a failure and rerun the same target as ``pytest ... | tail -20`` after the
    fix.  Those filters change only which output is displayed, not the tests
    executed.  Treating the two strings as distinct left the truncated first
    run permanently inconclusive even when the later rerun passed.

    Strip only trailing ``head``/``tail`` pipelines (and their adjacent stderr
    merge).  Execution-affecting shell operators such as ``&&``/``||`` and
    pytest selectors/options remain part of the identity.
    """
    normalized = " ".join(str(command or "").split())
    while True:
        stripped = _TEST_OUTPUT_FILTER_RE.sub("", normalized).strip()
        if stripped == normalized:
            break
        normalized = stripped
    return re.sub(r"\s+2>\&1\s*$", "", normalized).strip()


_PytestScope = tuple[str, frozenset[str]]


def _pytest_test_scope(command: str) -> _PytestScope | None:
    """Return the pytest execution context and selectors when safely comparable.

    ``None`` means the command is too complex for safe scope comparison.  An
    empty selector set means a full-suite invocation.  This lets a later
    passing superset rerun clear earlier failures for the same files while
    ensuring a targeted pass or a different Python environment can never clear
    a failed full-suite command.
    """
    normalized = _normalize_test_command(command)
    if any(operator in normalized for operator in ("&&", "||", ";", "|")):
        return None
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return None

    pytest_index = -1
    for index, token in enumerate(tokens):
        if os.path.basename(token) in {"pytest", "py.test"}:
            pytest_index = index
            break
    if pytest_index < 0:
        return None

    # Only compare scopes when the invocation prefix has ordinary pytest
    # semantics.  Environment assignments and wrappers can change collection
    # even when the visible selectors are identical.
    prefix = tokens[:pytest_index]
    if prefix:
        python_name = os.path.basename(prefix[0])
        if (
            len(prefix) != 2
            or prefix[1] != "-m"
            or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", python_name) is None
        ):
            return None
        execution_context = f"{prefix[0]} -m {tokens[pytest_index]}"
    else:
        execution_context = tokens[pytest_index]

    scope_narrowing_options = {
        "-k",
        "-m",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--nf",
        "--new-first",
        "--sw",
        "--stepwise",
        "--stepwise-skip",
    }
    safe_flags = {
        "-q",
        "--quiet",
        "-v",
        "-vv",
        "--verbose",
        "-s",
        "-x",
        "--exitfirst",
        "--disable-warnings",
        "--strict-config",
        "--strict-markers",
        "--continue-on-collection-errors",
        "--full-trace",
        "--showlocals",
        "-l",
        "--no-header",
        "--no-summary",
    }
    safe_value_options = {
        "--tb",
        "--color",
        "--capture",
        "--durations",
        "--durations-min",
        "--junitxml",
        "--junit-prefix",
        "--basetemp",
        "--verbosity",
        "--maxfail",
    }

    selectors: set[str] = set()
    args = tokens[pytest_index + 1 :]
    index = 0
    selectors_only = False
    while index < len(args):
        token = args[index]
        if token == "--":
            selectors_only = True
            index += 1
            continue
        if not selectors_only and token.startswith("-"):
            option_name = token.split("=", 1)[0]
            if option_name in scope_narrowing_options:
                return None
            if token in safe_flags:
                index += 1
                continue
            if option_name in safe_value_options:
                if "=" not in token:
                    index += 1
                    if index >= len(args):
                        return None
                index += 1
                continue
            # Plugin and future pytest options are unknown here.  Exact-command
            # retries remain supported, but cross-command scope coverage is not.
            return None
        selectors.add(token.rstrip("/") or ".")
        index += 1

    return execution_context, frozenset(selectors)


def _pytest_scope_covers(
    passing_scope: _PytestScope | None,
    earlier_scope: _PytestScope | None,
) -> bool:
    """Whether a passing pytest command covers an earlier command's scope."""
    if passing_scope is None or earlier_scope is None:
        return False
    passing_context, passing_selectors = passing_scope
    earlier_context, earlier_selectors = earlier_scope
    if passing_context != earlier_context:
        return False
    if not passing_selectors:
        return True
    if not earlier_selectors:
        return False

    def _selector_covers(passing: str, earlier: str) -> bool:
        if passing == earlier:
            return True
        if passing in {".", "./"}:
            return True
        passing_path = passing.split("::", 1)[0].rstrip("/")
        earlier_path = earlier.split("::", 1)[0].rstrip("/")
        if "::" not in passing and passing_path == earlier_path:
            return True
        return "::" not in passing and earlier_path.startswith(f"{passing_path}/")

    return all(
        any(_selector_covers(passing, earlier) for passing in passing_selectors)
        for earlier in earlier_selectors
    )


def _has_passing_test_tool_result(event_log: list, framework_type: str) -> bool:
    """Require conclusive success for every distinct test command in this run.

    A later successful retry only supersedes an earlier failure when it reruns
    the same normalized command.  This deliberately prevents a failing full
    suite followed by one passing targeted test from opening a PR.
    """
    test_tools: dict[str, tuple[str, _PytestScope | None]] = {}
    anonymous_tool_calls: list[tuple[str, _PytestScope | None] | None] = []
    for event in event_log or []:
        if not isinstance(event, dict) or event.get("type") != "tool_use":
            continue
        tool_id = str(event.get("tool_use_id") or "")
        as_tool_call = {
            "tool": {
                "name": event.get("tool_name", ""),
                "input": event.get("tool_input", {}),
            }
        }
        command_info = None
        if _has_test_tool_call([as_tool_call], framework_type):
            tool_input = event.get("tool_input") or {}
            command = ""
            if isinstance(tool_input, dict):
                command = str(
                    tool_input.get("command")
                    or tool_input.get("cmd")
                    or tool_input.get("args")
                    or ""
                )
            command_key = _normalize_test_command(command) or (f"tool:{event.get('tool_name', '')}")
            scope = _pytest_test_scope(command) if framework_type == "python" else None
            command_info = (command_key, scope)
            if tool_id:
                test_tools[tool_id] = command_info
        if not tool_id:
            # Preserve every anonymous call's position.  Otherwise an unrelated
            # anonymous result can be paired with the next pytest invocation,
            # potentially turning a real failure into a false pass.
            anonymous_tool_calls.append(command_info)

    states: dict[str, bool] = {}
    scopes: dict[str, _PytestScope | None] = {}
    result_orders: dict[str, int] = {}
    anonymous_index = 0
    for event_order, event in enumerate(event_log or []):
        if not isinstance(event, dict) or event.get("type") != "tool_result":
            continue
        tool_id = str(event.get("tool_use_id") or "")
        if tool_id:
            command_info = test_tools.get(tool_id)
            if not command_info:
                continue
        else:
            if anonymous_index >= len(anonymous_tool_calls):
                continue
            command_info = anonymous_tool_calls[anonymous_index]
            anonymous_index += 1
            if command_info is None:
                continue
        command_key, scope = command_info
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
        result_orders[command_key] = event_order

    expected_commands = {command_key for command_key, _scope in test_tools.values()} | {
        command_key
        for command_info in anonymous_tool_calls
        if command_info is not None
        for command_key, _scope in (command_info,)
    }
    if not expected_commands:
        return False

    for command_key, scope in test_tools.values():
        scopes.setdefault(command_key, scope)
    for command_info in anonymous_tool_calls:
        if command_info is None:
            continue
        command_key, scope = command_info
        scopes.setdefault(command_key, scope)

    passing_commands = [
        (command_key, scopes.get(command_key), result_orders.get(command_key, -1))
        for command_key, passed in states.items()
        if passed
    ]
    for command_key in expected_commands:
        if states.get(command_key) is True:
            continue
        earlier_scope = scopes.get(command_key)
        earlier_order = result_orders.get(command_key, -1)
        if framework_type != "python" or not any(
            passing_order > earlier_order and _pytest_scope_covers(passing_scope, earlier_scope)
            for _passing_key, passing_scope, passing_order in passing_commands
        ):
            return False
    return True


# Prefix added to all prompts to inform the agent it is running autonomously
AUTONOMOUS_CONTEXT = (
    "## 重要提示\n"
    "你正在无人值守的自动化工作流中运行。请遵守以下规则：\n"
    "1. 不要请求人类确认或等待权限批准，如果操作被阻止请跳过并继续\n"
    "2. 不要使用需要交互式确认的 gh CLI 命令（如 gh pr create）\n"
    "3. 直接执行文件修改和验证，不要仅输出方案文本；不要执行 git add/commit/push，编排器负责提交和推送\n"
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

# PR review is an independent, read-only gate.  In particular, do not expose
# Write/Edit/Bash or Agent here: a reviewer that edits the shared worktree can
# accidentally make its own findings appear resolved without the main session
# committing or testing those changes.  The main session applies findings in
# ``_apply_pr_review_fix`` with ``AUTONOMOUS_DEV_ALLOWED_TOOLS`` instead.
REVIEW_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": [
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
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
    "zcode": [],
}

# OpenClaw's current single-shot adapter does not accept per-run permission or
# tool-policy arguments.  Treating an empty allowlist as read-only would be a
# false security boundary, so autonomous review must fail closed for this tool
# until the adapter can provide an enforceable per-run sandbox.
READ_ONLY_REVIEW_UNSUPPORTED_TOOLS = frozenset({"openclaw"})

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
        "run_shell_command",
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


def _is_transient_git_error(e: Exception) -> bool:
    """Check if an exception is a transient git push error.

    Used by git_push exception handlers to decide whether to propagate
    the error (triggering Layer-2 orchestrator retry) or handle it locally.

    Args:
        e: The caught exception.

    Returns:
        True if the error is transient and should trigger retry.
    """
    from app.modules.workspace.autonomous.github_ops import GitHubOpsError

    if not isinstance(e, GitHubOpsError):
        return False
    err_str = str(e).lower()
    return any(kw in err_str for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS)


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


def _extract_pr_number_from_error(error_text: str) -> int | None:
    """Extract a PR number from a gh "already exists" error message.

    gh's already-exists message includes the PR URL, e.g.:
      "a pull request for branch X into branch main already exists:
       https://github.com/owner/repo/pull/1877"
    Parsing the /pull/<n> URL gives the PR number directly — this avoids
    coupling recovery to the exact "already exists" wording, skips the
    eventually-consistent find_existing_pr API call, and proves the error
    really is the already-exists case (no URL → not recoverable here).
    Returns the PR number or None.
    """
    if not error_text:
        return None
    m = re.search(r"/pull/(\d+)", error_text)
    return int(m.group(1)) if m else None


def _review_approval_phrase(content_language: str | None) -> str:
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
MAX_CI_DIAGNOSTICS_ATTEMPTS = 6  # bound scheduler polls when failed job logs stay unavailable
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
        )
        self._gh: GitHubOps | None = None

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
        """
        rc = gh._run_git(["merge-base", "--is-ancestor", a, b], check=False).returncode
        if rc == 0:
            return True
        if rc == 1:
            return False
        return None

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
        return ""

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

        Issue #1573: Added branch consistency verification when worktree exists.
        If the actual branch doesn't match expected branch_name, we attempt to
        recreate the worktree (after safety check for uncommitted changes).

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

            # Issue #1573: Verify branch consistency when worktree exists.
            # Check that the worktree's actual branch matches the expected branch_name.
            expected_branch = wf.get("branch_name", "")
            if expected_branch:
                try:
                    wt_gh = GitHubOps(canonical, system_account=system_account)
                    actual_branch = wt_gh.get_current_branch()
                    if actual_branch != expected_branch:
                        logger.error(
                            "Branch mismatch detected for workflow %s: expected=%s, actual=%s, worktree_path=%s",
                            self._workflow_id[:8],
                            expected_branch,
                            actual_branch,
                            canonical,
                        )
                        # Safety check: refuse to delete worktree with uncommitted changes
                        if wt_gh.has_uncommitted_changes():
                            logger.error(
                                "Worktree %s has uncommitted changes, refusing to delete",
                                canonical,
                            )
                            self._create_milestone(
                                phase=wf.get("current_phase", "preparation"),
                                milestone_type="branch_mismatch",
                                status="failed",
                                title=f"Branch mismatch with uncommitted changes: expected {expected_branch}, actual {actual_branch}",
                                error_message=f"Cannot recreate worktree: uncommitted changes detected on branch {actual_branch}",
                            )
                            raise GitHubOpsError(
                                f"Worktree branch mismatch ({actual_branch} != {expected_branch}) "
                                f"with uncommitted changes. Manual intervention required."
                            )
                        # Safe to delete - recreate worktree with correct branch
                        logger.warning(
                            "Attempting to recreate worktree %s on correct branch %s",
                            canonical,
                            expected_branch,
                        )
                        main_gh.remove_worktree(canonical)
                        # Recreate with correct branch
                        branch_check = main_gh._run_git(
                            ["show-ref", "--verify", "--quiet", f"refs/heads/{expected_branch}"],
                            check=False,
                        )
                        remote_check = main_gh._run_git(
                            [
                                "show-ref",
                                "--verify",
                                "--quiet",
                                f"refs/remotes/origin/{expected_branch}",
                            ],
                            check=False,
                        )
                        if branch_check.returncode == 0 or remote_check.returncode == 0:
                            main_gh._run_git(["worktree", "add", canonical, expected_branch])
                        else:
                            main_gh._run_git(
                                ["worktree", "add", "-b", expected_branch, canonical, "origin/main"]
                            )
                        self._create_milestone(
                            phase=wf.get("current_phase", "preparation"),
                            milestone_type="worktree_restored",
                            status="completed",
                            title=f"Worktree recreated on correct branch {expected_branch}",
                        )
                        logger.info(
                            "Worktree %s recreated on correct branch %s",
                            canonical,
                            expected_branch,
                        )
                        # Reset cached gh so it picks up the new worktree
                        self._gh = None
                except GitHubOpsError:
                    raise
                except Exception as e:
                    logger.warning("Branch verification failed: %s", e)
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
        """Return the canonical worktree path the workflow should reuse."""
        preferred = (wf.get("preferred_worktree_path") or "").strip()
        if preferred:
            return preferred
        current = (wf.get("worktree_path") or "").strip()
        if current:
            return current
        project_path = (wf.get("project_path") or "").strip()
        workflow_id = (wf.get("workflow_id") or self._workflow_id or "").strip()
        if not project_path or not workflow_id:
            return ""
        return os.path.join(project_path, ".worktrees", workflow_id)

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
            "6. 对 `pre-commit run --all-files` 这类全仓检查，必须原样保留 `--all-files`，并在结束前取得一次干净的 exit 0。\n"
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

        isolated_account = self._resolve_isolated_agent_account()
        project_system_account = self._resolve_system_account(wf)
        if project_system_account and isolated_account == project_system_account:
            raise RuntimeError(
                "Autonomous validation account must differ from the repository owner account"
            )
        runtime_command, _ = self._select_project_python_runtime(project_path, gh)
        guard_bin = AutonomousAgentRunner._resolve_agent_guard_bin()
        env = {
            "PATH": guard_bin + os.pathsep + os.environ.get("PATH", ""),
            "OPENACE_REAL_GIT": real_git,
            "OPENACE_PYTHON_COMMAND": json.dumps(runtime_command or [sys.executable]),
            "GH_CONFIG_DIR": "/var/empty/openace-autonomous-gh",
            "GIT_TERMINAL_PROMPT": "0",
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
        try:
            commit_sha = gh.get_current_commit()
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

        # Check attempt limit FIRST so the terminal round skips the fingerprint
        # network fetches (gh run view --log-failed for each failing check).
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
            updates["worktree_path"] = preferred_worktree_path
        self._update_workflow(updates)
        self._run_merge_ci_repair(self.workflow or wf, gh, pr_number, failed_checks)

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
        field = SESSION_LINE_FIELDS.get(session_line)

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
        retry_usage = {
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "request_count": 0,
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
                        "paused_at": datetime.now(timezone.utc),
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
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")

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
            branch_name = f"auto-dev/{self._workflow_id[:8]}"

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
                        wf = self.workflow
                        base_commit_sha = wf.get("base_commit_sha")
                        base_ref = base_commit_sha if base_commit_sha else "origin/main"
                        # Resolve the symbolic ref before branch creation and
                        # persist that immutable SHA. Scope checks must not use
                        # a later, moving origin/main value.
                        resolved_base = gh._run_git(["rev-parse", base_ref]).stdout.strip()
                        if not resolved_base:
                            raise GitHubOpsError(f"Unable to resolve branch base {base_ref}")
                        if not base_commit_sha:
                            self._update_workflow({"base_commit_sha": resolved_base})
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

                    # Update workflow with worktree_path and branch_name in single transaction
                    self._update_workflow(
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
                    wf = self.workflow
                    base_commit_sha = wf.get("base_commit_sha")
                    base_ref = base_commit_sha if base_commit_sha else "origin/main"
                    resolved_base = gh._run_git(["rev-parse", base_ref]).stdout.strip()
                    if not resolved_base:
                        raise GitHubOpsError(f"Unable to resolve branch base {base_ref}")
                    gh.create_branch(branch_name, base=resolved_base)
                    updates = {"branch_name": branch_name}
                    if not base_commit_sha:
                        updates["base_commit_sha"] = resolved_base
                    self._update_workflow(updates)

                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="completed",
                    title=f"Branch '{branch_name}' created",
                )
            except GitHubOpsError as e:
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
                self._update_workflow({"branch_name": current_branch})
                wf["branch_name"] = current_branch
            except GitHubOpsError as e:
                self._create_milestone(
                    phase="preparation",
                    milestone_type="branch_created",
                    status="failed",
                    title="Current branch detection failed",
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
            wf = self.workflow or {}
            if wf.get("status") == "failed":
                logger.info(
                    "Development round %d failed, skipping test phase for workflow %s",
                    dev_round,
                    self._workflow_id[:8],
                )
                return
            # Post development completion comment — but only if dev succeeded.
            # _run_development_agent sets status="failed" on failure; without
            # this guard, a "✅ Completed" comment is posted with a stale
            # commit that isn't the agent's work (#525).
            self._post_dev_completion_comment(wf, dev_round, gh)

        # ── Test phase (always runs) ──
        self._run_test_phase(wf, dev_round, gh)

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
        tests_actually_run = has_passing_tool_result
        test_result_inconclusive = (
            test_result.success
            and (has_test_tool_call or has_test_result or test_status_tag in ("passed", "failed"))
            and not tests_actually_run
        )

        # Issue #1547: Validate test report format
        # If agent used non-standard format but tests actually ran, trigger retry
        format_valid, format_reason = self._validate_test_report_format(test_response_text)
        has_non_standard_format = not format_valid and tests_actually_run

        tests_actually_skipped = (
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
        # Distinguish "branch behind main (timing issue)" from "no actual changes" (Issue #1552)
        has_changes = False
        is_timing_issue = False
        try:
            branch_sha = gh._run_git(["rev-parse", branch_name]).stdout.strip()
            main_sha = gh._run_git(["rev-parse", "main"]).stdout.strip()

            # Check if branch is an ancestor of main (behind main)
            is_ancestor = (
                gh._run_git(
                    ["merge-base", "--is-ancestor", branch_sha, main_sha], check=False
                ).returncode
                == 0
            )

            if is_ancestor:
                # Branch is behind main → timing issue
                is_timing_issue = True
                has_changes = False
                logger.warning(
                    "Branch %s is behind main (timing issue). base_commit_sha=%s",
                    branch_name,
                    wf.get("base_commit_sha", "none"),
                )
            else:
                # Branch is ahead or parallel → normal diff check
                diff_stats = gh.get_diff_stats("main", branch_name)
                has_changes = diff_stats.get("commits", 0) > 0
                if has_changes:
                    scope_error = self._validate_autonomous_change_scope(
                        gh,
                        wf,
                        (wf.get("base_commit_sha") or branch_sha),
                        branch_sha,
                    )
                    if scope_error:
                        self._update_workflow({"status": "failed", "error_message": scope_error})
                        return
        except Exception as e:
            logger.warning("Failed to check branch status: %s", e)
            pass

        if not has_changes:
            # No code changes produced — skip PR, post to issue, and mark completed
            issue_number = wf.get("github_issue_number")

            # Distinguish timing issue from no changes (Issue #1552)
            if is_timing_issue:
                no_change_msg = (
                    f"## ⚠️ Timing Issue Detected\n\n"
                    f"Branch `{branch_name}` is behind main (created from an older commit that was merged).\n"
                    f"This indicates a race condition during workflow creation.\n\n"
                    f"**Recommendation**: This issue should be fixed by locking base commit during batch creation.\n"
                )
            else:
                no_change_msg = (
                    f"## ℹ️ No Changes Detected\n\n"
                    f"Agent completed dev round {dev_round} without producing code changes.\n"
                    f"Skipping PR creation."
                )

            if issue_number:
                self._post_github_comment(gh, issue_number, no_change_msg, context="no-changes")
            self._create_milestone(
                phase="pr_review",
                dev_round=dev_round,
                milestone_type="timing_issue" if is_timing_issue else "no_changes",
                status="completed",
                title=(
                    "Branch behind main (timing issue)"
                    if is_timing_issue
                    else "No code changes produced"
                ),
                result_summary=(
                    "Branch behind main: possible timing issue during workflow creation"
                    if is_timing_issue
                    else "Agent did not produce any code changes. Skipping PR creation."
                ),
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

        issue_number = wf.get("github_issue_number") or self.workflow.get("github_issue_number")
        # Ensure branch is pushed to remote before PR creation
        try:
            # P1 修复（Issue #1611）：检查当前分支是否与预期一致
            current_branch = gh.get_current_branch()
            if branch_name and current_branch != branch_name:
                logger.error(
                    "Branch mismatch before push: workflow=%s expected=%s actual=%s",
                    self._workflow_id[:8],
                    branch_name,
                    current_branch,
                )
                raise RuntimeError(
                    f"Branch mismatch before push: expected {branch_name}, actual {current_branch}"
                )
            gh.git_push(branch=branch_name, force_with_lease=True)
        except Exception as e:
            # Distinguish transient vs non-transient errors to enable Layer-2 retry
            # for network flakiness (Issue #1814).
            if _is_transient_git_error(e):
                # Transient: propagate GitHubOpsError to trigger Layer-2 retry
                logger.warning("Transient push failure for branch %s: %s", branch_name, e)
                raise
            else:
                # Non-transient: wrap as RuntimeError to signal permanent failure
                logger.error("Failed to push branch %s: %s", branch_name, e, exc_info=True)
                # 推送失败必须阻止后续 PR 创建，避免 "No commits" 错误 (Issue #1736)
                raise RuntimeError(f"Branch push failed before PR creation: {e}") from e

        # Create PR on first round (idempotent: skip if a PR already exists for
        # this workflow). advance() is reentrant — the scheduler may call it
        # again while a review agent is still running and current_round hasn't
        # been persisted yet (it's written at the end of the review round). On
        # re-entry round_num is still 1, so without this guard the workflow
        # would call gh pr create again and hit "a pull request ... already
        # exists", failing the whole workflow (#1857). Checking github_pr_number
        # covers both re-entry and process-restart resume.
        #
        # Reads from both the passed-in wf dict AND self.workflow: self.workflow
        # is a @property that re-queries the repo on every access, so once an
        # earlier advance() persisted github_pr_number, a later advance()'s
        # fallback (self.workflow.get) sees the fresh value even though the
        # caller's wf snapshot is stale. This is what makes the guard reliable
        # across re-entries (the test suite mocks get_workflow statically, so
        # this property-refresh path is exercised in production but not in the
        # PR-creation unit tests — see test_create_pr_already_exists_recovers).
        existing_pr_number = wf.get("github_pr_number") or self.workflow.get("github_pr_number")
        if round_num == 1 and not existing_pr_number:
            try:
                # Build PR body with issue linkage
                pr_body = f"Autonomous development for dev round {dev_round}.\n\nRequirements: {wf.get('requirements_text', '')}"
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
                # Graceful recovery ONLY for the "already exists" case (race
                # between the github_pr_number guard above and the API call, or
                # a re-entrant advance()). Other GitHubOpsError causes (network
                # / auth / body-too-long) must NOT be masked by reusing an
                # unrelated leftover open PR on this branch — those still raise.
                #
                # Prefer parsing the PR URL out of gh's error text (gh's
                # already-exists message includes the PR URL, e.g.
                # "... already exists: https://github.com/o/r/pull/1877").
                # This avoids coupling to the exact "already exists" wording
                # AND skips the eventually-consistent find_existing_pr race
                # AND saves an API call. find_existing_pr is the fallback when
                # the error text has no parseable URL.
                pr_number_reused = _extract_pr_number_from_error(str(e))
                if pr_number_reused:
                    existing = {"number": pr_number_reused}
                else:
                    # Only treat as recoverable if it really is the
                    # already-exists case (no PR URL to prove it). Other
                    # errors have no PR URL and fall through to raise below.
                    if "already exists" not in str(e).lower():
                        self._create_milestone(
                            phase="pr_review",
                            milestone_type="pr_created",
                            status="failed",
                            title="PR creation failed",
                            error_message=str(e),
                        )
                        raise
                    existing = gh.find_existing_pr(branch_name)
                    if not existing:
                        # GitHub's PR list API is eventually consistent — the
                        # PR that "already exists" may not be indexed yet right
                        # after a concurrent create. One short retry covers it.
                        time.sleep(2)
                        existing = gh.find_existing_pr(branch_name)
                if existing:
                    pr_number = existing.get("number")
                    pr_url = existing.get("url", "")
                    logger.warning(
                        "PR create for %s returned 'already exists'; reusing PR #%s",
                        branch_name,
                        pr_number,
                    )
                    self._create_milestone(
                        phase="pr_review",
                        dev_round=dev_round,
                        milestone_type="pr_created",
                        status="completed",
                        title=f"PR #{pr_number} already exists (reused)",
                        github_pr_number=pr_number,
                        result_summary=pr_url,
                    )
                    self._update_workflow(
                        {
                            "github_pr_number": pr_number,
                            "github_pr_url": pr_url,
                        }
                    )
                else:
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
                except WorkflowPaused:
                    raise
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
        diff_text = self._get_pr_review_diff(gh, pr_number, branch_name)

        # Check CI status for the PR — poll until checks finish or timeout
        ci_checks: list = []
        ci_failures: list = []
        if pr_number:
            try:
                ci_checks = self._poll_ci_status(gh, pr_number)
                ci_failures = [c for c in ci_checks if c.get("bucket") == "fail"]
            except WorkflowPaused:
                self._cancel_milestone_for_shutdown(review_ms.get("milestone_id", ""))
                raise
            except Exception:
                pass

        review_prompt = (
            AUTONOMOUS_CONTEXT + f"你是一位资深代码审查专家。请审查以下 PR 的代码变更。\n\n"
            f"## 代码变更\n{self._smart_truncate_diff(diff_text)}\n\n"
        )

        # Add Issue reference
        if issue_number:
            review_prompt += (
                f"## 关联 Issue\n"
                f"本 PR 关联 GitHub Issue #{issue_number}。\n"
                f"审查时请确保代码变更满足 Issue #{issue_number} 的所有需求。\n\n"
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
            "本阶段是只读审查：不要修改文件、不要创建提交，也不要执行任何会改变仓库状态的命令。\n"
            "只有在所有 Issue 验收标准均已满足、没有 P0/P1 阻塞项或未落实项时才能批准；"
            "只要仍有阻塞项，即使核心功能已基本完成，也必须要求修改。\n"
            f"如果没有重大问题，请在审查结论中明确写出批准标记：{approval_phrase}。\n"
            "必须把下面的机器可读单行 JSON 作为 TL;DR 摘要之前的最后一个非摘要行"
            "（不要放进代码块）。所有未解决的 P0/P1 都必须逐项放入 blocking_findings；"
            "只有数组为空时 verdict 才能是 APPROVE：\n"
            'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
            'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES",'
            '"blocking_findings":["finding 1"]}\n\n'
            "重要：直接输出审查结果，不要添加引导文字(如'我来审查...'、'让我...'等)"
            "或结尾引导(如'下一步是否...'等)。"
        )

        review_tool = wf.get("cli_tool", "claude-code")
        if review_tool in READ_ONLY_REVIEW_UNSUPPORTED_TOOLS:
            message = (
                f"PR review cannot run safely with {review_tool}: its single-shot adapter "
                "does not provide an enforceable per-run read-only sandbox. Configure a "
                "review-capable CLI and retry the workflow."
            )
            self.repo.update_milestone(
                review_ms.get("milestone_id", ""),
                {"status": "failed", "error_message": message},
            )
            self._update_workflow({"status": "failed", "error_message": message})
            if pr_number:
                self._post_github_comment(
                    gh,
                    pr_number,
                    f"## ⛔ PR Review Blocked\n\n{message}",
                    is_pr=True,
                    context="code-review",
                )
            return

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
            cli_tool=review_tool,
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=review_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=_zcode_planning_mode(wf),
            allowed_tools=REVIEW_ALLOWED_TOOLS.get(review_tool, []),
            session_line="review",
            milestone_id=review_ms.get("milestone_id", ""),
        )

        self._accumulate_tokens(review_result)

        if self._abort_on_repo_integrity_violation(
            review_result, review_ms.get("milestone_id", "")
        ):
            return

        review_text = self._artifact_text(review_result)
        # Detect approval using the language-aware marker, then persist a
        # structured verdict so progress_reported doesn't re-scan review text.
        # The legacy zh marker is accepted too, for workflows whose content
        # language predates this field (mirrors _derive_review_passed).
        review_passed = self._review_is_approved(review_text, approval_phrase)
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
            #
            # The ENTIRE summary block (create milestone → run agent → fill
            # review_content → post comment) must run BEFORE the CI check.
            # Otherwise a CI failure redirects to the CI repair loop and
            # returns, leaving the milestone with status="in_progress" and
            # empty review_content — the frontend "PR Review Summary" button
            # checks review_content?.trim() and stays disabled (#1813).
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
            )
            if issue_number:
                summary_prompt += (
                    f"## 关联 Issue\n"
                    f"本 PR 关联 GitHub Issue #{issue_number}。\n"
                    f"总结中请确认修改是否满足 Issue #{issue_number} 的所有需求。\n\n"
                )
            summary_prompt += (
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
            if self._abort_on_repo_integrity_violation(
                summary_result, summary_ms.get("milestone_id", "")
            ):
                return
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

            # Check CI status before proceeding to report phase (Issue #1662)
            # If CI failed, enter CI repair loop instead of reporting
            if ci_failures:
                self._create_milestone(
                    phase="pr_review",
                    dev_round=dev_round,
                    round_number=round_num,
                    milestone_type="ci_failed_before_report",
                    status="completed",
                    title=f"CI failed after review passed: {len(ci_failures)} checks",
                    result_summary=", ".join(c.get("name", "unknown") for c in ci_failures),
                )
                # Reuse merge-phase CI repair loop
                self._start_ci_repair_round(wf, pr_number, ci_failures)
                return

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

        if self._abort_on_repo_integrity_violation(fix_result, fix_ms.get("milestone_id", "")):
            return

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
            return
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
        the full CI duration (10+ min for Python 3.10) and naturally adapts
        to variable CI times without --admin bypass or long polls.

        Note: an automatic CI repair attempt runs synchronously in this phase
        on the existing PR branch. That intentionally spends one merge worker
        for the repair task, but avoids bouncing the workflow back through the
        full development/test/review/report loop.
        """
        gh = self._get_gh()
        pr_number = wf.get("github_pr_number")
        branch_name = wf.get("branch_name", "")

        if pr_number:
            try:
                pr_head_sha = gh.get_pr_head_sha(pr_number)
                scope_error = self._validate_autonomous_change_scope(
                    gh, wf, (wf.get("base_commit_sha") or pr_head_sha), pr_head_sha
                )
            except Exception as exc:
                scope_error = f"Pre-merge change scope could not be verified: {exc}"
            if scope_error:
                self._update_workflow({"status": "failed", "error_message": scope_error})
                return
            try:
                checks = gh.get_pr_checks(pr_number)
            except Exception as e:
                raise GitHubOpsError(
                    f"Unable to query CI checks before merging PR #{pr_number}: {e}"
                ) from e
            failed = [c for c in checks if c.get("bucket") == "fail"]
            if failed:
                self._start_ci_repair_round(wf, pr_number, failed)
                return
            # If CI is still running, defer this merge to the next scheduler
            # cycle instead of blocking (synchronous poll) or failing. The
            # scheduler re-enters _do_merge every ~10s.
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
                        self._start_ci_repair_round(wf, pr_number, failed)
                        return
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
            original_pr_head = wt_gh.get_current_commit()
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
                )
                # Issue reference is available in this method's scope
                issue_number = wf.get("github_issue_number") or self.workflow.get(
                    "github_issue_number"
                )
                if issue_number:
                    conflict_prompt += (
                        f"## 关联 Issue\n"
                        f"本任务关联 GitHub Issue #{issue_number}。\n"
                        f"冲突解决时请确保修改满足 Issue #{issue_number} 的所有需求。\n\n"
                    )
                conflict_prompt += (
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
            # P1 修复（Issue #1611）：Conflict 解决后检查分支一致性
            current_branch = wt_gh.get_current_branch()
            if isinstance(current_branch, str) and current_branch and current_branch != branch_name:
                logger.warning(
                    "Conflict resolution branch mismatch: expected=%s, actual=%s",
                    branch_name,
                    current_branch,
                )
                branch_name = current_branch
            resolved_head = wt_gh.get_current_commit()
            merge_scope_wf = dict(wf)
            merge_scope_wf["base_commit_sha"] = "origin/main"
            scope_error = self._validate_autonomous_change_scope(
                wt_gh, merge_scope_wf, original_pr_head, resolved_head
            )
            if scope_error:
                raise RuntimeError(f"Conflict resolution scope rejected before push: {scope_error}")
            wt_gh.git_push(branch=branch_name, force_with_lease=True)
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
