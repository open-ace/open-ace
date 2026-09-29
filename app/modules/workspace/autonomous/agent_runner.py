from __future__ import annotations

# mypy: disable-error-code="assignment,arg-type,union-attr,var-annotated"
"""
Open ACE - Autonomous Agent Runner

Runs agent tools (claude code, codex, qwen code, etc.) autonomously,
collecting output and waiting for completion. Supports both local and
remote execution.
"""

import json
import logging
import os
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from app.modules.workspace.autonomous.artifact_text import pick_best_artifact_text
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.remote_machine import (
    RemoteMachineProvider,
    RemoteTurnSpec,
)
from app.modules.workspace.autonomous.sandbox.types import SandboxSpec
from app.modules.workspace.autonomous.task_isolation import (
    DEFAULT_TASK_ROOT,
    ensure_task_runtime_dirs,
    task_runtime_dirs,
)
from app.repositories.usage_repo import UsageRepository
from app.utils.tool_names import normalize_tool_name

if TYPE_CHECKING:  # pragma: no cover - annotations only (PEP 563)
    from app.modules.workspace.autonomous.sandbox.types import ExecHandle, SandboxHandle

logger = logging.getLogger(__name__)

_AUTONOMOUS_GIT_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*|\bsudo\s+)(?:\S*/)?git\s+[^\n]*\bpush\b"
    r"|(?:^|[;&|]\s*|\bsudo\s+)(?:\S*/)?gh\s+pr\s+(?:create|merge|close|reopen)\b",
    re.IGNORECASE,
)
_AUTONOMOUS_RUNTIME_BYPASS_RE = re.compile(
    r"(?:^|[;&|]\s*|\bsudo\s+)(?:/\S*/python(?:3(?:\.\d+)?)?|python3\.\d+)\b",
    re.IGNORECASE,
)


def _is_forbidden_autonomous_command(tool_name: str, tool_input: object) -> bool:
    """Reserve remote mutations for the post-validation orchestrator path."""
    if tool_name not in {"Bash", "run_shell_command", "Shell"}:
        return False
    if not isinstance(tool_input, dict):
        return False
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    return bool(
        _AUTONOMOUS_GIT_MUTATION_RE.search(command) or _AUTONOMOUS_RUNTIME_BYPASS_RE.search(command)
    )


def _iso_to_epoch(ts: str) -> float | None:
    """Parse a claude JSONL ISO timestamp to epoch seconds.

    Claude timestamps look like ``2026-06-24T12:39:05.000Z``. Returns None on
    any parse failure so callers can skip filtering rather than crash.
    """
    if not ts:
        return None
    try:
        # fromisoformat doesn't accept a trailing 'Z' until 3.11; strip it.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# Cached import — populated on first call to _run_local() which adds remote-agent to sys.path
_extract_stream_usage: Any = None
_is_cumulative_result_tool: Any = None
_diff_cumulative_usage: Any = None


def _ensure_usage_parser():
    """Import usage helpers once remote-agent is on sys.path."""
    global _extract_stream_usage, _is_cumulative_result_tool, _diff_cumulative_usage
    if _extract_stream_usage is None:
        try:
            from cli_adapters.usage_parser import (
                diff_cumulative_usage,
                extract_stream_usage,
                is_cumulative_result_tool,
            )
        except (ImportError, ModuleNotFoundError):
            logger.warning("Falling back to built-in stream usage parser")

            def extract_stream_usage(_cli_tool: str, parsed: dict) -> dict | None:
                usage = parsed.get("usage")
                if not isinstance(usage, dict):
                    message = parsed.get("message", {})
                    if isinstance(message, dict):
                        usage = message.get("usage")
                if not isinstance(usage, dict):
                    data = parsed.get("data", {})
                    if isinstance(data, dict):
                        usage = data.get("usage")
                if not isinstance(usage, dict):
                    return None
                return {
                    "input": int(usage.get("input_tokens", 0) or 0),
                    "output": int(usage.get("output_tokens", 0) or 0),
                }

            # Safe fallbacks: treat no tool as cumulative → report as-is.
            def is_cumulative_result_tool(_cli_tool: str) -> bool:
                return False

            def diff_cumulative_usage(
                cur: dict, _last_in: int | None, _last_out: int | None
            ) -> tuple[dict, int, int]:
                return cur, int(cur.get("input", 0) or 0), int(cur.get("output", 0) or 0)

        _extract_stream_usage = extract_stream_usage
        _is_cumulative_result_tool = is_cumulative_result_tool
        _diff_cumulative_usage = diff_cumulative_usage


# Default timeout for agent tasks — configurable via env var (default 1 hour)
try:
    DEFAULT_TASK_TIMEOUT = int(os.environ.get("AUTONOMOUS_TASK_TIMEOUT", "3600"))
except (ValueError, TypeError):
    logger.warning("Invalid AUTONOMOUS_TASK_TIMEOUT value, using default 3600")
    DEFAULT_TASK_TIMEOUT = 3600

SESSION_DETECTION_GRACE_SECONDS = 5.0
SESSION_DETECTION_WAIT_SECONDS = 5.0
SESSION_DETECTION_POLL_INTERVAL = 0.25

# How often the pause-aware completion loop re-checks the session state. The
# underlying ``Event.wait`` returns immediately when ``completed`` is set, so
# this only bounds how quickly we notice a pause/resume/stop transition.
COMPLETION_POLL_INTERVAL = 5.0

# CLI tools that run a persistent app-server process with their own stdio
# protocol (not Claude's stream-json). These must be driven through a dedicated
# session class instead of the generic _LocalSession path. Mirrors
# remote-agent/executor.py::_APPSERVER_TOOLS.
_APPSERVER_TOOLS = frozenset({"zcode", "zcode-code"})

# Tools whose transcript a `carried` provider (OpenSandbox) knows how to export
# and import across an ephemeral HOME (#3237/#3319): both take the stream-json
# path, emit their session_id on stdout, and have a provider transcript layout.
# A `carried`-provider resume for any other tool is refused rather than run into
# an empty HOME.
_CARRIED_STATE_TOOLS = frozenset({"claude-code", "qwen-code-cli"})

# Path to the cross-user agent launcher (Issue #1395). The service runs as
# `openace` but agent CLIs (claude-code/qwen-code/openclaw) infer the project
# root from cwd and have no --cwd flag, so they must launch with cwd=project.
# Under a user-private repo path Popen's chdir fails as the service user; this
# wrapper (root via a narrow sudoers rule) chdir's then drops to system_account
# via runuser. See scripts/openace-run-as.sh.
_OPENACE_RUN_AS = os.environ.get("OPENACE_RUN_AS", "/usr/local/bin/openace-run-as")
_OPENACE_AGENT_GUARD_BIN = os.environ.get(
    "OPENACE_AGENT_GUARD_BIN", "/usr/local/libexec/openace-agent-bin"
)
_AGENT_GUARD_EXECUTABLES = ("_guard_exec.py", "git", "gh", "python", "python3", "pytest")

# Security wrapper paths (Issue #1855)
_OPENACE_CAT_WRAPPER = os.environ.get("OPENACE_CAT_WRAPPER", "/usr/local/bin/openace-cat")
_OPENACE_MKDIR_WRAPPER = os.environ.get("OPENACE_MKDIR_WRAPPER", "/usr/local/bin/openace-mkdir")

# CLI tool → LLM provider, used to mint proxy tokens for local autonomous
# agents (mirrors remote_session_manager._cli_tool_to_provider). Local
# autonomous agents must route through the Open ACE LLM proxy with a signed
# proxy token — never the raw API key — so the key stays only in the DB.
_CLI_TOOL_PROVIDER = {
    "qwen-code-cli": "openai",
    "claude-code": "anthropic",
    "openclaw": "openai",
    "codex": "openai",
    "codex-cli": "openai",
    "zcode": "anthropic",
    "zcode-code": "anthropic",
}


class ZCodeSessionError(Exception):
    """Raised when a ZCode app-server session fails to start or send."""


@dataclass
class _AgentStatePlan:
    """What to do about agent state for one turn, decided BEFORE create (#3237).

    Deciding before the sandbox exists is what makes a refusal free: no pod is
    started and no tokens are spent on a turn that could not have resumed.
    """

    resume: bool
    blob: bytes | None = None
    refuse: bool = False
    reason_code: str = ""
    detail: str = ""


@dataclass
class _LocalSession:
    """Tracks a local CLI subprocess session."""

    session_id: str
    # The raw Popen, populated ONLY by LocalProcessTransport. Kept so the
    # pid-keyed call sites (mark_session_*_by_pid) behave exactly as before on
    # the Legacy path; a container backend leaves it None.
    process: subprocess.Popen | None
    # #2023: the agent IO seam. Every stdin/stdout/stderr/poll/wait call goes
    # through this. Derived from `process` when not supplied (see __post_init__)
    # so a session built directly from a Popen — which several suites and the
    # remote tracker do — still has a working transport.
    transport: Any = None
    cli_tool: str = "claude-code"
    allowed_tools: list[str] | None = None
    output_lines: list[str] = field(default_factory=list)
    assistant_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Cumulative-usage baseline for differencing per-turn deltas on tools
    # whose result message reports a cross-turn running total (qwen-code-cli).
    # Touched only by this session's stdout reader thread (see executor.py
    # invariant); reset naturally when a new _LocalSession is created.
    _last_cum_input: int | None = None
    _last_cum_output: int | None = None
    request_count: int = 0
    completed: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    # Runtime-only classification mirroring AgentTaskResult.error_code.
    error_code: str | None = None
    last_stderr: str = ""
    _stopped: threading.Event = field(default_factory=threading.Event)
    _stdout_thread: threading.Thread | None = None
    _stderr_thread: threading.Thread | None = None
    # Ordered event log for preserving actual message interleaving
    # Each entry: {"type": "assistant"|"tool_use"|"usage", ...}
    event_log: list[dict] = field(default_factory=list)
    project_path: str = ""
    encoded_project_path: str = ""
    workflow_id: str = ""
    user_id: int | None = None
    workspace_type: str = "local"
    remote_machine_id: str | None = None
    started_at_epoch: float = 0.0
    persisted_session_id: str = ""
    # Real Claude session_id captured from the SDK init control_response.
    # Preferred over mtime-based JSONL guessing so resume chains stay correct.
    cli_session_id: str = ""
    init_request_id: str = ""  # SDK initialize request_id for matching control_response
    sdk_initialized: threading.Event = field(default_factory=threading.Event)
    # Milestone this task belongs to — tags session_messages for per-phase detail views.
    milestone_id: str = ""
    system_account: str | None = None
    # #2439: the per-attempt task_id whose HOME the CLI writes its session JSONL
    # under. Drives per-task Claude-history resolution (_claude_projects_root);
    # empty for legacy callers (fall back to the system account's passwd home).
    task_id: str = ""
    # Distinct assistant message_ids counted toward request_count (dedup, since
    # claude emits multiple assistant events per message: thinking then text).
    _counted_message_ids: set = field(default_factory=set)
    # #2640: final-answer text from the terminal stream-json ``result`` event.
    # The CLI puts the turn's answer here; assistant stream events can be
    # dropped near the context limit, so this is kept as a fallback response
    # source (see _recover_final_response_text). Empty string = not captured.
    result_text: str = ""
    # #2640 diagnostics: count of stdout lines that failed JSON parsing.
    _unparseable_stdout_lines: int = 0
    _paused: threading.Event = field(default_factory=threading.Event)  # set when SIGSTOPed
    # Serializes remote session-id publication/message dispatch with shutdown.
    # Local subprocess sessions do not use it, but keeping it on the tracker
    # avoids a second tracker type and closes the create-vs-stop race.
    _remote_lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)
    # #2022 P3b: the SandboxProvider handle/exec_handle for the local spawn.
    # process (above) is the raw Popen the provider exposed; these let the
    # session release the provider sandbox at teardown. None on remote/legacy
    # tracker paths that don't go through the provider.
    sandbox_handle: SandboxHandle | None = None
    exec_handle: ExecHandle | None = None
    # #2022 P4 ③: the provider that owns this sandbox, so stop/pause/resume can
    # route through it (gVisor has no local Popen — signals MUST reach the
    # sandbox via the provider). None on legacy tracker paths.
    sandbox_provider: Any = None

    def __post_init__(self) -> None:
        # A session built directly from a Popen still gets the seam, so the
        # reader/writer paths have exactly one code path regardless of how the
        # session was constructed. Remote trackers pass neither and keep
        # transport None, which those paths already handle.
        if self.transport is None and self.process is not None:
            from app.modules.workspace.autonomous.sandbox.transport import LocalProcessTransport

            self.transport = LocalProcessTransport(self.process)


# Top-level keys that indicate a JSON object is a leaked tool-call blob
# rather than genuine assistant prose. ZCode sometimes streams tool
# invocations as text content before emitting a structured tool.* event.
_TOOL_JSON_KEYS = frozenset(
    {
        "tool",
        "command",
        "subagent_type",
        "file_path",
        # ZCode sometimes leaks child-agent invocation payloads as assistant
        # text; those arrive as {"description": "...", "prompt": "..."}.
        "description",
        "prompt",
    }
)
_NON_VISIBLE_BLOCK_TYPES = frozenset({"thinking", "tool_use", "tool_result", "reasoning"})
_STRUCTURED_TAG_PATTERNS = {
    "tldr": re.compile(r"^\s*TL;DR:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE),
    "test_status": re.compile(r"^\s*TEST_STATUS:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE),
    "ci_status": re.compile(r"^\s*CI_STATUS:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE),
}


def _looks_like_tool_json(text: str) -> bool:
    """Detect leaked tool-call JSON in assistant text deltas.

    Only filters when the ENTIRE text is valid JSON with a tool-call key at
    the top level. This avoids false-positives on legitimate prose that
    contains JSON snippets (e.g. a plan section showing a config example).
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return False
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(obj, dict) and any(k in obj for k in _TOOL_JSON_KEYS)


def _extract_visible_text(content: Any) -> str:
    """Extract user-visible assistant text from mixed protocol payloads.

    Handles three common autonomous-runner payload shapes:

    - Claude/OpenAI-style content blocks: ``[{"type":"text","text":"..."}]``
    - Stringified JSON arrays/objects leaked through assistant content
      (e.g. Claude ``thinking`` / ``tool_use`` blocks)
    - ZCode leaked plan/tool objects, where ``{"plan":"..."}`` should render
      as the final plan text while tool-call payloads should be hidden.
    """

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _extract_visible_text(item)
            if text:
                parts.append(text)
        return "".join(parts)

    if isinstance(content, dict):
        block_type = content.get("type")
        if block_type in _NON_VISIBLE_BLOCK_TYPES:
            return ""
        text_value = content.get("text")
        if block_type == "text" and isinstance(text_value, str):
            return text_value
        plan_value = content.get("plan")
        if isinstance(plan_value, str):
            return plan_value
        if any(k in content for k in _TOOL_JSON_KEYS):
            return ""
        if "content" in content:
            return _extract_visible_text(content.get("content"))
        if isinstance(text_value, str):
            return text_value
        return ""

    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return content
            return _extract_visible_text(parsed)
        return content

    return ""


def _coalesce_assistant_events(event_log: list[dict]) -> list[dict]:
    """Merge adjacent assistant deltas into turn-sized assistant messages."""

    merged: list[dict] = []
    current_assistant: dict | None = None

    for event in event_log or []:
        if event.get("type") == "assistant":
            text = event.get("text", "")
            if not text:
                continue
            if current_assistant is None:
                current_assistant = {
                    "type": "assistant",
                    "text": text,
                    "message_id": event.get("message_id"),
                    "model": event.get("model"),
                }
            else:
                current_assistant["text"] += text
                if event.get("message_id"):
                    current_assistant["message_id"] = event.get("message_id")
                if event.get("model"):
                    current_assistant["model"] = event.get("model")
            continue

        if current_assistant is not None:
            merged.append(current_assistant)
            current_assistant = None

        if event.get("type") == "tool_use":
            merged.append(event)

    if current_assistant is not None:
        merged.append(current_assistant)

    return merged


def _extract_final_response_text(event_log: list[dict], fallback_text: str = "") -> str:
    """Return the best publishable assistant turn from an event log."""

    assistant_turns: list[str] = [
        event.get("text", "").strip()
        for event in _coalesce_assistant_events(event_log)
        if event.get("type") == "assistant" and event.get("text", "").strip()
    ]
    if assistant_turns:
        return pick_best_artifact_text(*assistant_turns, fallback_text)
    return pick_best_artifact_text(fallback_text)


def _extract_visible_response_text(event_log: list[dict], fallback_text: str = "") -> str:
    """Return all visible assistant turns, excluding hidden/tool payloads.

    This preserves structured tags like ``TL;DR:`` or ``CI_STATUS:`` even when
    the agent emits them before a trailing tool call or short final ack turn.
    """

    assistant_turns: list[str] = [
        event.get("text", "").strip()
        for event in _coalesce_assistant_events(event_log)
        if event.get("type") == "assistant" and event.get("text", "").strip()
    ]
    if assistant_turns:
        return "\n\n".join(assistant_turns)
    return fallback_text.strip()


def _extract_structured_response_tags(text: str) -> dict[str, str]:
    """Extract structured status tags from assistant-visible text."""
    if not text:
        return {}
    tags: dict[str, str] = {}
    for key, pattern in _STRUCTURED_TAG_PATTERNS.items():
        matches = list(pattern.finditer(text))
        if matches:
            tags[key] = matches[-1].group(1).strip()
    return tags


def _build_agent_task_result(
    *,
    session_id: str,
    tracking_session_id: str,
    source_session_id: str = "",
    event_log: list[dict] | None = None,
    fallback_text: str = "",
    total_tokens: int = 0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    request_count: int = 0,
    tool_calls: list | None = None,
    success: bool = False,
    error: str | None = None,
    error_code: str | None = None,
    messages: list | None = None,
) -> AgentTaskResult:
    """Build a task result with both final-output and visible-text views."""
    normalized_event_log = event_log or []
    visible_text = _extract_visible_response_text(normalized_event_log, fallback_text)
    final_text = _extract_final_response_text(normalized_event_log, fallback_text)
    return AgentTaskResult(
        session_id=session_id,
        tracking_session_id=tracking_session_id,
        source_session_id=source_session_id,
        response_text=final_text,
        visible_response_text=visible_text,
        structured_tags=_extract_structured_response_tags(visible_text),
        messages=messages or [],
        total_tokens=total_tokens,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        request_count=request_count,
        tool_calls=tool_calls or [],
        success=success,
        error=error,
        error_code=error_code,
        event_log=normalized_event_log,
    )


def _extract_cli_result_error(parsed: dict, stderr_hint: str = "") -> tuple[str | None, str | None]:
    """Classify an error-bearing CLI ``result`` payload using observed failures only."""
    if not parsed.get("is_error"):
        return None, None

    # Claude SDK (2.1.x) can emit contradictory events near the context limit:
    # is_error=true with subtype="success" and no actual error message. Treat
    # this as success — the agent completed its task; the is_error flag is a
    # spurious SDK signal, not a real failure (#1816 regression).
    subtype = str(parsed.get("subtype") or "").strip()
    if subtype == "success":
        return None, None

    candidates: list[str] = []
    errors = parsed.get("errors")
    if isinstance(errors, list):
        candidates.extend(str(item).strip() for item in errors if str(item).strip())

    error_value = parsed.get("error")
    if isinstance(error_value, str) and error_value.strip():
        candidates.append(error_value.strip())

    if stderr_hint.strip():
        candidates.append(stderr_hint.strip())

    message = next((item for item in candidates if item), "")
    normalized = message.lower()

    if "no conversation found with session id" in normalized:
        return "resume_session_not_found", message

    # Session resume failure: the CLI could not open the session transcript
    # file. On macOS, ``com.apple.provenance`` xattr or TCC restrictions can
    # cause EPERM even when the file is owner-readable. The transcript is
    # inaccessible but the task itself may still succeed on a fresh session,
    # so classify distinctly from generic CLI errors to let the orchestrator
    # recover by dropping ``--resume`` and starting a new session (#2035).
    if "failed to resume session" in normalized or (
        "eperm" in normalized and "resume" in normalized
    ):
        return "resume_session_failed", message

    if "not logged in" in normalized or "/login" in normalized:
        return "cli_auth_failed", message or "Not logged in · Please run /login"

    if message:
        return "unknown_cli_error", message
    if subtype:
        return "unknown_cli_error", subtype
    return "unknown_cli_error", "CLI execution failed"


class _ZcodeResultCollector:
    """Collects assistant text, tool calls, and token usage from ZCode app-server.

    Acts as the ``output_callback`` / ``usage_callback`` for
    ``ZCodeAppServerSession``, translating ZCode Protocol notifications into
    the same data ``_LocalSession`` accumulates for Claude SDK sessions.
    """

    def __init__(
        self,
        activity_callback=None,
        session_id_resolver=None,
    ) -> None:
        self.assistant_text: str = ""
        self.tool_calls: list[dict] = []
        self.total_tokens: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.request_count: int = 0
        # When the usage_callback provides an authoritative model_requests
        # count, we use that instead of counting done=True callbacks. This
        # avoids double-counting: ZCode calls on_usage (with modelRequestCount)
        # BEFORE the final on_output(done=True), so both would increment.
        self._request_count_from_usage: bool = False
        self.event_log: list = []
        self.error: str | None = None
        # Real-time activity forwarding. activity_callback mirrors what
        # _LocalSession._read_stdout does for the Claude SDK path so the SSE
        # stream (and the timeline's live AI activity panel) works for ZCode
        # too. session_id_resolver returns the current best session id — the
        # real CLI session id once session/create resolves, else the tracking
        # uuid — because activity flows before persisted_session_id is set.
        self._activity_callback = activity_callback
        self._session_id_resolver = session_id_resolver or (lambda: "")

    def on_output(self, session_id: str, data: str, stream: str, done: bool) -> None:
        """Receive ZCode output_callback invocations.

        Builds ``event_log`` dicts in the same shape ``_LocalSession._read_stdout``
        produces, so ``_persist_local_session_messages`` can consume them
        identically regardless of CLI tool. See agent_runner.py:1443-1472.
        """
        if not data:
            if done and not self._request_count_from_usage:
                # Fallback: no authoritative model_requests from usage_callback,
                # so count this turn as 1 request.
                self.request_count += 1
            return
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return

        if not isinstance(parsed, dict):
            return

        msg_type = parsed.get("type", "")

        # Assistant text delta (from model.streaming → {"type":"assistant",...})
        if msg_type == "assistant":
            content = parsed.get("message", {}).get("content", "")
            text = _extract_visible_text(content)
            # Filter out leaked tool-call JSON: ZCode sometimes streams tool
            # invocations as text content before emitting a structured tool.*
            # event. These look like raw JSON with tool/command markers and
            # would pollute the plan text if appended verbatim.
            if text and _looks_like_tool_json(text):
                return
            if text:
                self.assistant_text += text
                self.event_log.append(
                    {
                        "type": "assistant",
                        "text": text,
                        "message_id": parsed.get("message", {}).get("id"),
                        "model": parsed.get("message", {}).get("model"),
                    }
                )
                # Forward to the live SSE activity stream (mirrors the Claude
                # SDK path in _LocalSession._read_stdout).
                if self._activity_callback:
                    self._activity_callback(
                        self._session_id_resolver(),
                        {"type": "assistant", "text": text[:500]},
                    )

        # Tool events: ZCode emits both tool.<name> calls and tool.updated
        # lifecycle records.  Preserve started/result pairs in the same
        # tool_use/tool_result contract used by Claude and Codex.
        elif msg_type.startswith("tool."):
            tool_name = msg_type.split(".", 1)[1]
            payload = parsed.get("data", {}) or {}
            if not isinstance(payload, dict):
                return
            kind = payload.get("kind")
            if tool_name == "updated" and kind == "result":
                result = payload.get("result")
                if not isinstance(result, dict):
                    result = payload
                result_text = (
                    result.get("aggregated_output")
                    or result.get("output")
                    or result.get("content")
                    or result.get("message")
                    or ""
                )
                exit_code = result.get("exit_code", result.get("exitCode"))
                self.event_log.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": payload.get("id")
                        or payload.get("toolCallId")
                        or result.get("id"),
                        "text": str(result_text),
                        "exit_code": exit_code,
                        "is_error": bool(result.get("is_error", False))
                        or (isinstance(exit_code, int) and exit_code != 0),
                    }
                )
                return
            if tool_name == "updated" and kind in ("scheduled", "started"):
                tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
                tool_name = str(
                    payload.get("toolName") or payload.get("name") or tool.get("name") or "unknown"
                )
                if tool_name == "unknown" or "input" not in payload:
                    return
            elif tool_name == "updated" and kind == "batch":
                return
            tool_input = payload.get("input", payload)
            tool_id = payload.get("id") or payload.get("toolCallId") or ""
            self.tool_calls.append(
                {"tool": {"name": tool_name, "input": tool_input, "id": tool_id}}
            )
            self.event_log.append(
                {
                    "type": "tool_use",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_use_id": tool_id,
                }
            )
            if self._activity_callback:
                self._activity_callback(
                    self._session_id_resolver(),
                    {
                        "type": "tool_use",
                        "tool_name": tool_name,
                        "tool_input": str(tool_input)[:200],
                    },
                )

        elif msg_type == "error":
            err_data = parsed.get("data", {})
            if isinstance(err_data, dict):
                msg = err_data.get("message", "")
                if msg and self.error is None:
                    self.error = msg

    def on_usage(self, session_id: str, usage: dict) -> None:
        """Receive ZCode usage_callback invocations.

        ZCodeAppServerSession emits a normalized dict with snake_case keys
        (input, output, cache_read, reasoning, model_requests), NOT the
        camelCase session/usage response. See zcode_app_server.py:335-417.
        """
        self.input_tokens = usage.get("input", self.input_tokens)
        self.output_tokens = usage.get("output", self.output_tokens)
        self.total_tokens = self.input_tokens + self.output_tokens
        # model_requests is the authoritative request count from the model
        # gateway. When present, it takes precedence over the done=True
        # fallback counting in on_output to avoid double-counting.
        if "model_requests" in usage:
            self.request_count = usage["model_requests"]
            self._request_count_from_usage = True
        if self._activity_callback:
            self._activity_callback(
                self._session_id_resolver(),
                {
                    "type": "usage",
                    "total_tokens": self.total_tokens,
                    "total_input_tokens": self.input_tokens,
                    "total_output_tokens": self.output_tokens,
                    "request_count": self.request_count,
                },
            )

    def on_permission(self, session_id: str, control_request: dict) -> None:
        """Auto-approve all permission requests in autonomous mode.

        ZCode app-server in edit/yolo mode auto-approves tool calls, so this
        callback is rarely invoked. Left as a no-op notification hook.
        """
        pass


class AutonomousAgentRunner:
    """Runs agent tools autonomously and returns results."""

    def __init__(
        self,
        session_manager=None,
        remote_session_manager=None,
        server_url: str = "",
        activity_callback=None,
        on_pid_registered=None,
        on_pid_cleared=None,
        sandbox_provider=None,
        on_sandbox_created=None,
    ):
        """
        Args:
            session_manager: SessionManager for creating session records.
            remote_session_manager: RemoteSessionManager for remote execution.
            server_url: Open ACE server URL for proxy config.
            activity_callback: Optional callback ``(session_id, activity_dict)``
                invoked for each assistant/tool_use/usage event, enabling
                real-time streaming of agent activity to the frontend.
            on_pid_registered: Optional callback ``(session_id, pid)`` called
                when a local subprocess is created, for PID persistence.
            on_pid_cleared: Optional callback ``(session_id)`` called when a
                local subprocess exits, for PID cleanup.
            sandbox_provider: #2022 P3b — the SandboxProvider that owns local
                spawn/ACL-wrap. Defaults to LegacyPosixProvider; injectable for
                tests. P4 will branch on workspace_type for RemoteMachineProvider.
            on_sandbox_created: Optional callback
                ``(session_id, sandbox_id, provider_name, remote_session_id_or_None)``
                called right after the provider execs (#2022 P6), so the
                orchestrator can persist a mid-run ``sandbox_state='running'``
                row + attribution. A crash between exec and task completion then
                leaves a row the startup reconciler can destroy by id.
        """
        self.session_manager = session_manager
        self.remote_session_manager = remote_session_manager
        self.server_url = server_url or os.environ.get(
            "OPENACE_SERVER_URL", "http://localhost:19888"
        )
        self._activity_callback = activity_callback
        self._on_pid_registered = on_pid_registered
        self._on_pid_cleared = on_pid_cleared
        self._on_sandbox_created = on_sandbox_created
        self._local_sessions: dict[str, _LocalSession] = {}
        # #2022 P3b: local spawns route through the SandboxProvider so the
        # orchestrator no longer touches sudo/_wrap_agent_cmd/Popen directly
        # for spawn. The CLI protocol layer (reader threads, stdin handshake)
        # still drives the raw Popen the provider exposes via get_process().
        self._sandbox_provider = sandbox_provider or LegacyPosixProvider()

    @staticmethod
    def _uses_sidebar_session_source(cli_tool: str, workspace_type: str) -> bool:
        """Whether this task should resolve to the real sidebar Claude session."""
        return workspace_type == "local" and cli_tool == "claude-code"

    # Issue #2020: structured error codes for resource/isolation failures.
    # The launcher emits distinct exit codes + stderr sentinels; these classify
    # them so the orchestrator sees a structured reason, not an opaque exit.
    TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE = "task_wall_clock_timeout"

    def _load_task_policy(self) -> Any:
        """Load the #2020 AgentTaskPolicy from agent-launcher.conf, or None.

        Passed into ``SandboxSpec.policy`` so #2020 HOME/TMP/quota is *expressed
        via the spec* (the #2022 acceptance), not only enforced ad hoc by
        _build_agent_env + openace-run-as. Legacy does not yet consume
        spec.policy (the existing isolation path stands), but the spec now
        carries it for contract completeness and future providers. None if the
        conf is missing/unreadable.
        """
        try:
            from app.modules.workspace.autonomous.task_isolation import (
                read_agent_task_policy,
                resolve_agent_task_policy_path,
            )

            conf = resolve_agent_task_policy_path(os.environ.get("OPENACE_LAUNCHER_CONF"))
            if not conf:
                return None
            return read_agent_task_policy(conf)
        except Exception:
            return None

    def _resource_policy_configured(self) -> bool:
        """Whether agent-launcher.conf sets a non-zero memory/pids/cpu limit.

        Used to gate signal-kill classification: ``task_resource_limit_exceeded``
        should mean a configured limit was actually hit, not an unrelated kill.
        """
        policy = self._load_task_policy()
        return bool(policy and (policy.memory_max_bytes or policy.pids_max or policy.cpu_max))

    @staticmethod
    def _resolve_wall_clock_timeout(explicit_timeout: int, policy: Any) -> int:
        """Effective wall-clock cap for one attempt (#2020 Phase B).

        ``AgentTaskPolicy.wall_clock_limit`` (>0) takes precedence — it is the
        contract dimension the spec/UI surfaces and ``CPU_MEM_PIDS_TIME_QUOTA``
        covers. 0 (unset) falls back to the orchestrator-passed ``explicit_timeout``
        (itself defaulting to ``AUTONOMOUS_TASK_TIMEOUT`` / 3600). ``policy`` None
        (no conf) likewise falls back.
        """
        if policy is not None and policy.wall_clock_limit > 0:
            return int(policy.wall_clock_limit)
        return explicit_timeout

    def _sync_usage_to_daily_usage(
        self,
        session_id: str,
        cli_tool: str,
        tenant_id: int,
        result: AgentTaskResult,
    ) -> None:
        """Sync usage data to daily_usage table for autonomous sessions.

        Implements idempotent synchronization using daily_usage_synced flag
        to prevent double-counting on retries or repeated calls.

        Issue #2585: Ensure autonomous development usage is included in
        daily_usage statistics.

        Args:
            session_id: Session identifier (persisted_session_id or session_id).
            cli_tool: CLI tool name (claude-code, qwen-code, etc.).
            tenant_id: Tenant ID for the session.
            result: Agent task result containing usage data.
        """
        # Zero activity check: skip if no requests and no tokens
        if (result.request_count or 0) == 0 and (result.total_tokens or 0) == 0:
            logger.debug(
                "Skipping daily_usage sync: zero activity (session=%s)",
                session_id[:8],
            )
            return

        # Idempotency check: verify not already synced
        if self.session_manager:
            try:
                session = self.session_manager.get_session(session_id)
                if session and getattr(session, "daily_usage_synced", False):
                    logger.debug(
                        "Skipping daily_usage sync: already synced (session=%s)",
                        session_id[:8],
                    )
                    return
            except Exception as e:
                logger.warning("Failed to check daily_usage_synced status: %s", e)
                # Continue to attempt sync anyway

        # Perform sync
        try:
            repo = UsageRepository()
            host_name = socket.gethostname()

            # Normalize tool name
            tool_name = normalize_tool_name(cli_tool)

            # Prepare models_used
            models_used = [result.model] if hasattr(result, "model") and result.model else None

            # Call increment_usage (atomic UPSERT)
            increment_ok = repo.increment_usage(
                tool_name=tool_name,
                host_name=host_name,
                tenant_id=tenant_id,
                tokens_used=result.total_tokens or 0,
                input_tokens=result.total_input_tokens or 0,
                output_tokens=result.total_output_tokens or 0,
                cache_tokens=0,  # Cache tokens not supported in autonomous yet
                request_count=result.request_count or 0,
                models_used=models_used,
            )

            if not increment_ok:
                # Keep daily_usage_synced unset so subsequent activity can
                # still be recorded; this round's usage is not retried.
                logger.warning(
                    "increment_usage failed for session=%s; leaving daily_usage_synced unset",
                    session_id[:8],
                )
                return

            # Set sync flag to mark as synced (idempotency)
            if self.session_manager:
                try:
                    self.session_manager.update_session_fields(
                        session_id,
                        {"daily_usage_synced": True},
                        require_tenant=False,
                    )
                except Exception as e:
                    logger.warning("Failed to set daily_usage_synced flag: %s", e)
                    # Don't fail the sync - flag is for optimization

            logger.debug(
                "Synced usage to daily_usage: session=%s tool=%s requests=%d",
                session_id[:8],
                tool_name,
                result.request_count or 0,
            )
        except Exception as e:
            # Non-blocking: log warning and continue
            logger.warning(
                "Failed to sync usage to daily_usage: session=%s error=%s",
                session_id[:8],
                e,
            )

    @staticmethod
    def _stamp_sandbox_attribution(
        result: AgentTaskResult, sandbox_handle: Any, provider: Any
    ) -> AgentTaskResult:
        """Stamp sandbox identity + final state onto a result (#2022 P5).

        Called at every _run_local/_run_remote return path where a sandbox was
        created, so the orchestrator can persist sandbox_provider/id/generation/
        state onto the workflow row and every evidence path carries attribution.
        No-op when sandbox_handle is None (spawn failed before create).
        """
        if sandbox_handle is None:
            return result
        result.sandbox_id = sandbox_handle.sandbox_id
        result.sandbox_generation = sandbox_handle.generation
        result.sandbox_provider = sandbox_handle.provider_name
        # #2022 P6: stamp the TASK-terminal state, not provider.inspect(). A
        # remote CLI session is deliberately left alive on success (reusable
        # sidebar), so inspect() returns 'running' — which the startup
        # reconciler would mis-flag as an orphan and destroy. The workflow row's
        # sandbox_state must mean "is THIS task's sandbox still occupying
        # resources": success → destroyed (task done; local literally, remote
        # task-done even if the session is reused), failure → error. A 'running'
        # row therefore only exists from a mid-task write — and at startup, any
        # 'running'/'paused' row is a genuine crash orphan.
        result.sandbox_state = "destroyed" if result.success else "error"
        return result

    def _notify_sandbox_created(
        self,
        session_id: str,
        sandbox_handle: Any,
        remote_session_id: str | None,
        provider: Any = None,
    ) -> None:
        """Fire ``on_sandbox_created`` so the orchestrator persists mid-run state (#2022 P6).

        Called right after ``provider.exec()`` returns (local + remote), so a
        crash between exec and task completion leaves a ``sandbox_state='running'``
        workflow row the startup/periodic reconciler can destroy by id. The
        remote path passes the manager's session id; local/gVisor pass ``None``.
        Best-effort: a callback failure is logged, never propagated to the run
        path (mirrors ``on_pid_registered``).
        """
        if self._on_sandbox_created is None or sandbox_handle is None:
            return
        try:
            # #2020 Phase B: snapshot the actually-effective resource/isolation
            # policy for this run. Built from the provider's DECLARED capabilities
            # + the spec's AgentTaskPolicy so the workflow UI shows what was in
            # effect, independent of the live agent-launcher.conf.
            from app.modules.workspace.autonomous.sandbox.effective_policy import (
                build_effective_policy,
            )

            try:
                # The provider that actually ran this task, NOT the injected
                # default. Reading self._sandbox_provider stamped Legacy's
                # capability set onto a row labelled provider_name="opensandbox"
                # — so the row claimed CPU_MEM_PIDS_TIME_QUOTA (always in
                # _LEGACY_CAPS) even for a tier that attested no pod pids limit,
                # and omitted the namespace/egress isolation it really had.
                # effective_policy's contract is that the map cannot lie.
                declared_caps = (provider or self._sandbox_provider).capabilities()
            except Exception:
                declared_caps = frozenset()
            effective_policy = build_effective_policy(
                sandbox_handle.provider_name,
                declared_caps,
                getattr(sandbox_handle.spec, "policy", None),
            )
            self._on_sandbox_created(
                session_id,
                sandbox_handle.sandbox_id,
                sandbox_handle.provider_name,
                remote_session_id,
                effective_policy,
            )
        except Exception as e:
            logger.warning("on_sandbox_created callback failed: %s", e)

    @staticmethod
    def _classify_isolated_exit_code(
        return_code: int | None,
        stderr: str = "",
        *,
        orchestrator_initiated: bool = False,
        resource_policy_configured: bool = False,
    ) -> tuple[str | None, str | None]:
        """Map an isolated-launcher child exit to a structured error code.

        Returns ``(error_code, message)`` where both may be ``None`` for a
        clean or generic non-isolation failure. Recognized cases:

        * ``OPENACE_CGROUP_REQUIRED`` stderr sentinel → resource policy
          unavailable (cgroup forced on but unwritable). Matched by sentinel
          only — exit 66 is overloaded in the launcher for unrelated
          pre-flight failures (validator/flock/base64/reject_conf).
        * exit 68 / ``OPENACE_REPO_INTEGRITY_VIOLATION`` → .git tamper.
        * a resource-limit breach: only when the orchestrator did NOT
          initiate the kill, a resource policy is configured, and the kernel
          killed the process with an exhaustion signal (SIGKILL/SIGXCPU).
          Python ``subprocess.Popen.returncode`` encodes signal deaths as
          negative values (-signum); SIGTERM(-15)/SIGINT(-2) are the
          orchestrator's own stop/timeout signals and are never a resource
          breach, so timeouts/stops are never misreported here.
        """
        lowered = (stderr or "").lower()
        if return_code == 70:
            return (
                "preserve_preparation_failed",
                "Could not prepare .claude preserve directory; nesting guard aborted to protect --resume history",
            )
        if "openace_cgroup_required" in lowered:
            return (
                "task_resource_policy_unavailable",
                "Task resource policy could not be enforced (cgroup unavailable)",
            )
        if return_code == 68 or "openace_repo_integrity_violation" in lowered:
            return ("repo_integrity_violation", None)
        if (
            not orchestrator_initiated
            and resource_policy_configured
            and return_code is not None
            and return_code < 0
            and -return_code in (signal.SIGKILL, signal.SIGXCPU)
        ):
            return (
                "task_resource_limit_exceeded",
                "Agent process killed by signal (possible resource-limit breach)",
            )
        return (None, None)

    @staticmethod
    def _classify_sidebar_start_failure(
        return_code: int | None,
        error_code: str = "",
        stderr: str = "",
    ) -> str:
        """Return a safe, actionable reason for a pre-session Claude exit.

        Raw stderr can contain command arguments or provider credentials, so it
        must never be copied into workflow milestones.  Classify only known
        launcher/guard signatures and otherwise report the numeric exit code.
        """
        lowered = (stderr or "").lower()
        if error_code == "repo_integrity_violation" or (
            "openace_repo_integrity_violation:" in lowered
        ):
            return "Protected .git entry changed during autonomous agent execution"

        # openace-run-as forwards the child process status unchanged, so exit
        # codes 64-68 alone do not identify launcher failures. Match only its
        # exact sentinel prefix and stable message fragments.
        if lowered.startswith("openace-run-as:"):
            if "cannot chdir" in lowered:
                return "Isolated launcher could not enter the project directory"
            if "is required for isolated" in lowered:
                return "Isolated launcher prerequisites are unavailable"
            if "isolated agent must" in lowered or "isolated project path must" in lowered:
                return "Isolated agent launch was rejected by safety checks"
            if "usage:" in lowered:
                return "Invalid isolated launcher invocation"
            return "Isolated agent launcher failed before Claude session initialization"

        if "mutating git commands are reserved" in lowered:
            return "Agent command guard rejected a mutating Git operation during CLI startup"
        if "fatal: detected dubious ownership" in lowered:
            return "Git safe-directory validation failed during CLI startup"

        if return_code not in (None, 0):
            return f"Claude CLI exited before session initialization (exit code {return_code})"
        return "Failed to detect Claude sidebar session JSONL for autonomous task"

    @staticmethod
    def _extract_stream_session_id(parsed: dict[str, Any]) -> str:
        """Best-effort extraction of Claude's real session_id from a stream event."""
        if not isinstance(parsed, dict):
            return ""

        def _append_candidates(container: Any, candidates: list[Any]) -> None:
            if not isinstance(container, dict):
                return
            candidates.extend(
                [
                    container.get("session_id"),
                    container.get("sessionId"),
                    container.get("uuid"),
                ]
            )

        candidates: list[Any] = []
        _append_candidates(parsed, candidates)
        response = parsed.get("response", {}) or {}
        _append_candidates(response, candidates)
        inner_response = response.get("response", {}) or {}
        _append_candidates(inner_response, candidates)

        for candidate in candidates:
            session_id = str(candidate or "").strip()
            if session_id:
                return session_id
        return ""

    @staticmethod
    def _resolve_home_dir(system_account: str | None, task_id: str | None = None) -> Path:
        """Resolve the HOME whose .claude/projects the CLI writes session JSONL to.

        With a per-attempt ``task_id`` (#2020 isolation), the CLI runs under the
        per-task HOME (``/run/openace-agent-tasks/<task_id>/home``, set in
        ``_build_env`` + the cross-user launcher), so its history lives there —
        NOT under the system account's passwd home. Resolving the passwd home
        here (#2439) returned the wrong directory, making the mtime session
        fallback and the timeout usage replay silently miss. Fall back to the
        passwd/system home only for legacy callers without a task_id.
        """
        if task_id:
            return Path(task_runtime_dirs(task_id)["home"])
        if system_account:
            try:
                pw_entry = pwd.getpwnam(system_account)
                if pw_entry.pw_dir:
                    return Path(pw_entry.pw_dir)
            except KeyError:
                logger.warning("Unknown system_account for Claude history: %s", system_account)
        return Path.home()

    @classmethod
    def _claude_projects_root(cls, system_account: str | None, task_id: str | None = None) -> Path:
        """Return the ~/.claude/projects root for the workflow owner."""
        return cls._resolve_home_dir(system_account, task_id) / ".claude" / "projects"

    @staticmethod
    def _read_text_as_user(path: Path, system_account: str | None) -> str:
        """Read a text file, routing through sudo for cross-user private homes.

        Issue #1855: 优先使用 openace-cat 安全 wrapper，wrapper 内部做路径校验和审计日志。
        """
        if not AutonomousAgentRunner._is_cross_user(system_account):
            return path.read_text(encoding="utf-8")
        assert system_account is not None  # _is_cross_user guarantees non-empty

        # Issue #1855: 优先使用安全 wrapper
        if os.path.isfile(_OPENACE_CAT_WRAPPER) and os.access(_OPENACE_CAT_WRAPPER, os.X_OK):
            result = subprocess.run(
                ["sudo", _OPENACE_CAT_WRAPPER, system_account, str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        else:
            # Fallback: 使用传统 sudo -u cat 命令
            result = subprocess.run(
                ["sudo", "-u", system_account, "cat", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or f"cat exited {result.returncode}")
        return result.stdout

    @staticmethod
    def _list_jsonl_files(project_dir: Path, system_account: str | None) -> list[Path]:
        """List Claude JSONL files, even when the directory sits under a 0700 home."""
        if not AutonomousAgentRunner._is_cross_user(system_account):
            if not project_dir.is_dir():
                return []
            return list(project_dir.glob("*.jsonl"))
        assert system_account is not None  # _is_cross_user guarantees non-empty
        result = subprocess.run(
            ["sudo", "-u", system_account, "ls", "-1", str(project_dir)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [
            project_dir / line.strip()
            for line in result.stdout.splitlines()
            if line.strip().endswith(".jsonl")
        ]

    @staticmethod
    def _stat_mtime(path: Path, system_account: str | None) -> float:
        """Stat a file, routing through sudo when the owner differs."""
        if not AutonomousAgentRunner._is_cross_user(system_account):
            return path.stat().st_mtime
        assert system_account is not None  # _is_cross_user guarantees non-empty
        result = subprocess.run(
            ["sudo", "-u", system_account, "stat", "-c", "%Y", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or f"stat exited {result.returncode}")
        return float((result.stdout or "0").strip())

    def _capture_cli_session_id(
        self,
        session: _LocalSession,
        parsed: dict[str, Any],
        source: str,
    ) -> str:
        """Capture the CLI's own session_id from the stream onto the session.

        For claude-code this is the sidebar/resume id (its long-standing use).
        For qwen-code-cli (#3319) it is the id needed to name the carried
        transcript — qwen emits ``session_id`` on the same stream-json stdout.
        The gate widens for qwen HERE only; ``_uses_sidebar_session_source`` and
        its sidebar-linkage call sites are untouched, so qwen's sidebar
        behaviour does not change.
        """
        if (
            not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type)
            and session.cli_tool != "qwen-code-cli"
        ):
            return ""

        cli_session_id = self._extract_stream_session_id(parsed)
        if not cli_session_id:
            return ""

        if cli_session_id != session.cli_session_id:
            session.cli_session_id = cli_session_id
            logger.info(
                "Captured %s session_id from %s (workflow=%s tracking=%s cli=%s)",
                session.cli_tool,
                source,
                session.workflow_id,
                (session.session_id or "")[:8],
                cli_session_id[:8],
            )
        return cli_session_id

    @staticmethod
    def _ensure_project_dir(project_path: str, system_account: str | None) -> None:
        """Ensure ``project_path`` is reachable by the selected agent account.

        Trusted orchestration creates local worktrees as the repository owner
        before an AI process starts.  A credentialless cross-user agent cannot
        traverse the owner's 0700 home until ``openace-run-as --isolated``
        grants its temporary ACLs, so probing with ``Path`` or asking that
        account to run ``mkdir`` produces a false "missing directory" error.
        Use the same root-owned launcher as the real process for the existence
        check; it grants and revokes the scoped ACL in one short invocation.
        """
        same_user = False
        if system_account:
            try:
                same_user = pwd.getpwuid(os.getuid()).pw_name == system_account
            except (KeyError, OverflowError):
                same_user = False
        if system_account and not same_user:
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "-u",
                    "root",
                    _OPENACE_RUN_AS,
                    "--isolated",
                    system_account,
                    project_path,
                    "/usr/bin/test",
                    "-d",
                    ".",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise PermissionError(
                    f"Failed to access project dir {project_path} as "
                    f"{system_account} (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )
        else:
            Path(project_path).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _is_cross_user(system_account: str | None) -> bool:
        """Whether agent launch needs the run-as wrapper for a different user.

        Mirrors github_ops._needs_sudo: True only when system_account is set
        AND differs from the service process user. Same-user skips the wrapper
        to avoid failing under systemd NoNewPrivileges (sudo is forbidden).
        """
        if not system_account:
            return False
        try:
            return pwd.getpwuid(os.getuid()).pw_name != system_account
        except (KeyError, OverflowError):
            # Cannot determine the current user; assume cross-user to stay safe.
            return True

    @staticmethod
    def isolated_launcher_path() -> str:
        """Return the configured path to the ``openace-run-as`` launcher.

        Exposed as a public getter so cross-module callers (e.g. the
        orchestrator's warning log) don't have to reach into the private
        ``_OPENACE_RUN_AS`` module attribute.
        """
        return _OPENACE_RUN_AS

    @staticmethod
    def is_isolated_launcher_available() -> bool:
        """Whether the privileged ``openace-run-as --isolated`` launcher is
        installed and executable.

        The isolated-agent path (a credentialless principal + scoped ACLs via
        ``openace-run-as``) is the security model for multi-user Linux
        deployments. The launcher is Linux-only (setfacl/getent/runuser) and is
        not provisioned on dev installs or non-Linux platforms (macOS/Windows),
        where the service already runs as the repository owner and there is no
        security benefit to a second account. Callers use this to decide
        whether to engage isolated-agent mode or fall back to same-user
        execution (mirroring the Windows single-user downgrade).

        Design tradeoff (fail-open on Linux multi-user): if the launcher is
        accidentally removed or loses its exec bit on a multi-user Linux host,
        this returns ``False`` and the workflow silently degrades to same-user
        mode (running as the repo owner with credentials) rather than failing
        closed. This is a conscious tradeoff: the dev/macOS single-user case is
        the common path and must not be blocked, and a fail-closed policy would
        make the launcher a single point of failure for every workflow on the
        host. The ``logger.warning`` in the caller's ``else`` branch surfaces
        the degradation; operators in security-sensitive multi-user deployments
        should monitor that log and/or install the launcher via a package
        manager so it cannot drift. Hardening this to fail-closed on Linux
        would require a platform check (``sys.platform.startswith("linux")``)
        combined with a multi-user detection heuristic, which is tracked as a
        future enhancement rather than a blocking issue.
        """
        return os.path.isfile(_OPENACE_RUN_AS) and os.access(_OPENACE_RUN_AS, os.X_OK)

    @staticmethod
    def _wrap_agent_cmd(
        cmd: list[str],
        project_path: str,
        system_account: str | None,
        env: dict[str, str] | None = None,
        task_id: str | None = None,
    ) -> tuple[list[str], str | None]:
        """Wrap an agent CLI command for cross-user launch (Issue #1395).

        Returns (cmd, cwd) for subprocess.Popen. Same-user keeps the command
        verbatim and cwd=project_path. Cross-user replaces the old
        ``["sudo","-u",account]+cmd`` (which left Popen chdir'ing as the
        service user and failing under a private home) with the run-as
        wrapper: it chdir's as root then drops to system_account via runuser,
        so the CLI inherits the project cwd with the owner's identity.
        """
        if AutonomousAgentRunner._is_cross_user(system_account):
            assert system_account is not None  # _is_cross_user guarantees non-empty
            if env:
                env = dict(env)
                env["OPENACE_GIT_CACHE_ROOT"] = str(
                    AutonomousAgentRunner._resolve_home_dir(system_account)
                    / ".cache"
                    / "pre-commit"
                )
                AutonomousAgentRunner._validate_cross_user_guard_bin(env)
            guard_env = []
            for key in (
                "PATH",
                "OPENACE_REAL_GIT",
                "OPENACE_REAL_GH",
                "OPENACE_GIT_CACHE_ROOT",
                "OPENACE_PYTHON_COMMAND",
                "OPENACE_PROXY_URL",
                "OPENACE_PROXY_TOKEN",
                "OPENACE_MODEL",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "BAILIAN_CODING_PLAN_API_KEY",
                "GEMINI_API_KEY",
                "GEMINI_BASE_URL",
                "GIT_AUTHOR_NAME",
                "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL",
                "GH_CONFIG_DIR",
                "GIT_TERMINAL_PROMPT",
                "SKIP",
            ):
                if env and env.get(key):
                    guard_env.append(f"{key}={env[key]}")
            guarded_cmd = ["/usr/bin/env", *guard_env, *cmd] if guard_env else cmd
            # Issue #2020: pass a sanitized per-attempt task_id so the launcher
            # can key HOME/TMP/XDG/flock/cgroup off the attempt rather than the
            # shared Agent UID. Omitted for legacy callers without a task_id.
            launcher_opts: list[str] = ["--isolated"]
            if task_id:
                from app.modules.workspace.autonomous.task_isolation import sanitize_task_id

                launcher_opts += ["--task-id", sanitize_task_id(task_id)]
            wrapped = [
                "sudo",
                "-n",
                "-u",
                "root",
                _OPENACE_RUN_AS,
                *launcher_opts,
                system_account,
                project_path,
                *guarded_cmd,
            ]
            return wrapped, None  # wrapper chdir's internally; cwd must be None
        return cmd, project_path

    @staticmethod
    def _resolve_agent_guard_bin() -> str:
        """Return the installed guard directory, with a source-tree fallback.

        Cross-user launches require the root-owned packaged directory because
        the isolated agent cannot traverse the service account's private home.
        Same-user development can still use the guards shipped beside this
        module when the package has not been installed system-wide.
        """
        # Freeze a canonical path before checking its contents. Returning a
        # configured symlink would let PATH traverse that link after the
        # cross-user ownership validation, creating a check/use race if the
        # link were replaced in between.
        packaged_guard_bin = Path(os.path.realpath(_OPENACE_AGENT_GUARD_BIN))
        if all((packaged_guard_bin / name).is_file() for name in _AGENT_GUARD_EXECUTABLES):
            return str(packaged_guard_bin)
        return str(Path(__file__).with_name("agent_bin"))

    @staticmethod
    def _validate_cross_user_guard_bin(env: dict[str, str]) -> None:
        """Fail closed unless cross-user command guards are safely installed.

        A source-tree guard path under a 0700 service home is visible to the
        service but not to ``openace-agent``.  PATH would then silently fall
        through to unguarded system binaries.  Cross-user launches therefore
        require the root-owned packaged guard directory installed by every
        supported deployment path.
        """
        path_head = (env.get("PATH") or "").split(os.pathsep, 1)[0]
        expected = os.path.realpath(_OPENACE_AGENT_GUARD_BIN)
        if not path_head or os.path.realpath(path_head) != expected:
            raise RuntimeError(
                "Cross-user autonomous launch requires the packaged agent command guards"
            )
        try:
            directory_stat = os.stat(expected, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"Missing packaged agent guard directory: {expected}") from exc
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != 0
            or directory_stat.st_mode & 0o022
            or directory_stat.st_mode & 0o555 != 0o555
        ):
            raise RuntimeError(f"Unsafe packaged agent guard directory: {expected}")
        for name in _AGENT_GUARD_EXECUTABLES:
            guard_path = os.path.join(expected, name)
            try:
                stat_result = os.stat(guard_path, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"Missing packaged agent guard: {guard_path}") from exc
            if (
                not stat.S_ISREG(stat_result.st_mode)
                or stat_result.st_uid != 0
                or stat_result.st_mode & 0o022
                or stat_result.st_mode & 0o555 != 0o555
            ):
                raise RuntimeError(f"Unsafe packaged agent guard: {guard_path}")

    @staticmethod
    def _build_agent_env(
        adapter: Any,
        cli_tool: str,
        user_id: int | None,
        session_id: str,
        model: str,
        runtime_python_command: list[str] | None = None,
        task_id: str | None = None,
    ) -> dict[str, str]:
        """Build subprocess env with LLM proxy auth for a local agent.

        Local autonomous agents (claude-code/qwen-code/etc.) must authenticate
        through the Open ACE LLM proxy — never with the raw API key. This mints
        a short-lived signed proxy token (via APIKeyProxyService) and asks the
        adapter to map it onto the tool-specific env vars (e.g. ANTHROPIC_API_KEY
        / ANTHROPIC_BASE_URL for claude-code). Mirrors executor._build_env but
        runs in-process, without an HTTP round-trip.

        Issue #2019: every raw credential (provider/GitHub/SSH/cloud + dynamic
        custom envKeys) is scrubbed before the proxy token is injected, and a
        proxy-token minting failure fails closed (raises) — in production always,
        in development unless OPENACE_ALLOW_RAW_KEY_FALLBACK=1 is set. The agent
        never inherits a real key from the service env.
        """
        from app.utils.security_mode import is_production

        env = dict(os.environ)
        guard_bin = AutonomousAgentRunner._resolve_agent_guard_bin()
        real_git = shutil.which("git", path=env.get("PATH"))
        real_gh = shutil.which("gh", path=env.get("PATH"))
        if real_git:
            env["OPENACE_REAL_GIT"] = real_git
        if real_gh:
            env["OPENACE_REAL_GH"] = real_gh
        env["OPENACE_PYTHON_COMMAND"] = json.dumps(runtime_python_command or [sys.executable])
        try:
            agent_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OverflowError):
            agent_home = Path.home()
        env["OPENACE_GIT_CACHE_ROOT"] = str(agent_home / ".cache" / "pre-commit")
        # The orchestrator owns all remote mutations. Agent subprocesses use
        # the LLM proxy for model auth and do not need GitHub credentials.
        # CI-only hook exclusions are set explicitly by the orchestrator's
        # convergence command. Never let a service-level SKIP leak into a
        # normal autonomous agent and silently suppress repository hooks.
        env.pop("SKIP", None)
        # Process topology must not leak into agent subprocesses: the agent's
        # pytest run would start real schedulers inside the test process
        # (TESTING guard covers create_app; this keeps topology out entirely so
        # non-TESTING create_app calls are safe too). (class-2, 2026-08-15)
        env.pop("SCHEDULER_MODE", None)
        env["GH_CONFIG_DIR"] = "/var/empty/openace-autonomous-gh"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PATH"] = guard_bin + os.pathsep + env.get("PATH", "")

        # Issue #2020 Phase A: per-attempt HOME/TMP/XDG runtime tree keyed by
        # task_id, so concurrent attempts on the same Agent UID never share
        # HOME/TMP/cache. The cross-user launcher re-derives the same paths
        # from ``--task-id`` (its env -i overrides these); on the same-user
        # path these env vars are authoritative. OPENACE_GIT_CACHE_ROOT
        # (pre-commit baseline cache) is relocated to the per-task cache dir
        # for full isolation (decision: 2026-07-26). When task_id is absent
        # (legacy callers) the shared defaults above stand.
        if task_id:
            task_root = os.environ.get("OPENACE_AGENT_TASK_ROOT", DEFAULT_TASK_ROOT)
            # Create the tree when the root is writable (same-user/dev path,
            # where the launcher is not invoked). On the cross-user path the
            # root launcher creates it under root-owned /run; this returns None
            # there and we leave HOME untouched so the launcher sets it.
            task_dirs = ensure_task_runtime_dirs(task_id, task_root)
            if task_dirs is not None:
                env["HOME"] = task_dirs["home"]
                env["TMPDIR"] = task_dirs["tmp"]
                env["XDG_CACHE_HOME"] = task_dirs["cache"]
                env["XDG_CONFIG_HOME"] = task_dirs["config"]
                env["XDG_DATA_HOME"] = task_dirs["data"]
                env["OPENACE_GIT_CACHE_ROOT"] = str(Path(task_dirs["cache"]) / "pre-commit")

        # Issue #2019: scrub every raw credential (provider/GitHub/SSH/cloud +
        # dynamic custom envKeys) BEFORE injecting the proxy token, and fail
        # closed if the proxy token could not be minted. The agent must never
        # inherit a real key from the service env.
        (
            sensitive_env_keys,
            llm_provider_env_keys,
            collect_dynamic_env_keys,
            build_secure_agent_env,
        ) = AutonomousAgentRunner._import_env_security()
        adapter_settings = AutonomousAgentRunner._load_adapter_settings(adapter)
        dynamic_env_keys = collect_dynamic_env_keys(adapter_settings)
        sensitive_keys = set(sensitive_env_keys) | dynamic_env_keys
        llm_keys = set(llm_provider_env_keys) | dynamic_env_keys

        proxy_env_vars: dict[str, str] = {}
        proxy_ok = False
        try:
            from app.modules.workspace.api_key_proxy import get_api_key_proxy_service
            from app.utils.config import get_config_value

            provider = _CLI_TOOL_PROVIDER.get(cli_tool, "openai")
            api_proxy = get_api_key_proxy_service()

            # Resolve the local server URL the CLI should call back to. Prefer
            # the configured server_url/external_url; fall back to localhost:port
            # so it works on a stock single-machine install.
            server_url = (
                get_config_value("server", "server_url")
                or get_config_value(None, "external_url")
                or f"http://localhost:{get_config_value('server', 'web_port', 5000)}"
            ).rstrip("/")
            proxy_url = f"{server_url}/api/remote/llm-proxy"

            tenant_id = 1
            if user_id:
                try:
                    from app.repositories.user_repo import UserRepository

                    user = UserRepository().get_user_by_id(user_id)
                    if user and user.get("tenant_id"):
                        tenant_id = int(user["tenant_id"])
                except Exception:
                    pass  # default tenant

            proxy_token = api_proxy.generate_proxy_token(
                user_id=user_id or 0,
                session_id=session_id,
                tenant_id=tenant_id,
                provider=provider,
                session_type="agent",
            )
            proxy_env_vars.update(adapter.get_env_vars(proxy_url, proxy_token))
            # Re-add custom modelProvider envKeys (e.g. BAILIAN_CODING_PLAN_API_KEY)
            # as the proxy token so qwen reads the token regardless of envKey.
            for env_key in dynamic_env_keys:
                if env_key != "OPENAI_API_KEY":
                    proxy_env_vars[env_key] = proxy_token
            proxy_env_vars["OPENACE_PROXY_URL"] = proxy_url
            proxy_env_vars["OPENACE_PROXY_TOKEN"] = proxy_token
            if model:
                proxy_env_vars["OPENACE_MODEL"] = model
            proxy_ok = True
        except Exception as e:
            # Fail closed below; never fall back to inheriting raw keys silently.
            logger.error("LLM proxy setup failed for %s: %s", cli_tool, e)

        return cast(
            "dict[str, str]",
            build_secure_agent_env(
                base_env=env,
                sensitive_keys=sensitive_keys,
                llm_provider_keys=llm_keys,
                proxy_env_vars=proxy_env_vars,
                proxy_ok=proxy_ok,
                is_production=is_production(),
                raw_fallback_allowed=os.environ.get("OPENACE_ALLOW_RAW_KEY_FALLBACK") == "1",
            ),
        )

    @staticmethod
    def _import_env_security():
        """Import the shared env-security helpers from the remote-agent subtree.

        ``remote-agent/`` ships with the app and is added to ``sys.path`` at
        runtime, mirroring the ``cli_adapters`` import in ``_run_local_agent_task``.
        """
        import sys

        remote_agent_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "remote-agent")
        )
        if remote_agent_dir not in sys.path:
            sys.path.insert(0, remote_agent_dir)
        from constants import LLM_PROVIDER_ENV_KEYS, SENSITIVE_ENV_KEYS, collect_dynamic_env_keys
        from env_security import build_secure_agent_env

        return (
            SENSITIVE_ENV_KEYS,
            LLM_PROVIDER_ENV_KEYS,
            collect_dynamic_env_keys,
            build_secure_agent_env,
        )

    @staticmethod
    def _load_adapter_settings(adapter: Any) -> dict:
        """Best-effort load of the CLI adapter's settings.json (for dynamic envKeys)."""
        try:
            settings_path = adapter.get_settings_path()
            if settings_path:
                with open(settings_path, encoding="utf-8") as f:
                    return cast("dict[str, Any]", json.load(f))
        except Exception:
            pass
        return {}

    @staticmethod
    def _encode_project_path(project_path: str) -> str:
        """Best-effort match for the encoded project path used by Claude session history.

        Claude stores session history under ``~/.claude/projects/<encoded>`` where
        ``<encoded>`` is the *real* (symlink/``..``-resolved) absolute path with
        ``/`` replaced by ``-``. Callers can pass any form — a worktree path
        produced by ``f"{repo}/../branch"`` still contains ``..`` — so we must
        normalize first or the encoded dir never matches what Claude actually
        wrote (#814). ``realpath`` also resolves symlinks, matching Claude's
        own ``getcwd``-based encoding.
        """
        if not project_path:
            return ""
        resolved = os.path.realpath(project_path)
        # Claude Code 2.1.201's embedded project-path diagnostic specifies
        # ``pwd | sed 's|[^a-zA-Z0-9]|-|g'`` (also observed in its project-dir
        # function). In particular ``/.worktrees`` becomes ``--worktrees``;
        # replacing slashes alone leaves ``-.worktrees`` and misses the
        # directory the CLI actually created.
        return re.sub(r"[^A-Za-z0-9]", "-", resolved)

    def _find_latest_claude_session_id(
        self,
        encoded_project_path: str,
        min_mtime_epoch: float,
        bound_cli_session_ids: set[str] | None = None,
        system_account: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Find the latest Claude JSONL session created for the active worktree.

        This is best-effort discovery based on the encoded worktree path and file mtime.
        It assumes only one local autonomous Claude task is creating a new session for a
        given worktree at a time; concurrent tasks on the same worktree can still race.

        ``bound_cli_session_ids`` excludes files belonging to sessions already bound to
        another session line (main/review/test). Without this, a shared "main" session —
        which is continuously appended across milestones and thus always has the newest
        mtime — would be wrongly picked for a fresh review/test line, collapsing the
        3-session topology into one (issue #723).
        """
        if not encoded_project_path:
            return ""

        project_dir = self._claude_projects_root(system_account, task_id) / encoded_project_path
        candidates = self._list_jsonl_files(project_dir, system_account)
        if not candidates:
            return ""

        latest_file = None
        latest_mtime = min_mtime_epoch - SESSION_DETECTION_GRACE_SECONDS
        try:
            for candidate in candidates:
                try:
                    candidate_mtime = self._stat_mtime(candidate, system_account)
                except (OSError, ValueError):
                    continue
                if not (
                    candidate_mtime >= min_mtime_epoch - SESSION_DETECTION_GRACE_SECONDS
                    and candidate_mtime >= latest_mtime
                ):
                    continue
                # Exclude files whose session id is already bound to another line
                # (e.g. the always-being-written main session). This prevents the
                # shared main session from being re-picked for a fresh review/test
                # line and collapsing the 3-session design (#723).
                if bound_cli_session_ids:
                    sid = self._peek_jsonl_session_id(candidate, system_account=system_account)
                    if sid and sid in bound_cli_session_ids:
                        continue
                latest_file = candidate
                latest_mtime = candidate_mtime
        except OSError:
            return ""

        return latest_file.stem if latest_file else ""

    @staticmethod
    def _peek_jsonl_session_id(filepath: Path, system_account: str | None = None) -> str:
        """Read the session id from the first record of a Claude JSONL file.

        Claude writes the session uuid as ``sessionId`` (and sometimes ``uuid``)
        on every record; we only read the first non-empty line to identify the
        file's owning session without parsing the whole transcript.
        """
        try:
            content = AutonomousAgentRunner._read_text_as_user(filepath, system_account)
        except OSError:
            return ""
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            sid = ""
            if isinstance(rec, dict):
                sid = (
                    rec.get("sessionId") or rec.get("session_id") or rec.get("uuid") or ""
                ).strip()
            if sid:
                return sid
            break
        return ""

    def _replay_usage_from_jsonl(self, session: _LocalSession, cli_session_id: str) -> None:
        """Replay token/request usage from the claude session JSONL.

        Used on the timeout path when the subprocess did real work but never
        emitted a closing ``result`` event (so ``session.total_tokens`` is 0).
        Reads the JSONL for ``cli_session_id`` and accumulates usage from
        records whose timestamp is at/after this call's ``started_at_epoch``,
        filling ``total_tokens``/``request_count`` so the milestone records the
        real cost instead of 0/0 (#723).

        Only fills in values when the live counters are zero (timeout), so a
        normal completed run is never overwritten.
        """
        if not cli_session_id or not session.encoded_project_path:
            return
        jsonl_path = (
            self._claude_projects_root(session.system_account, session.task_id)
            / session.encoded_project_path
            / f"{cli_session_id}.jsonl"
        )
        try:
            content = self._read_text_as_user(jsonl_path, session.system_account)
        except OSError:
            return
        # Accumulate usage per distinct message_id. Claude repeats the FULL
        # message usage on every block-line of a message (thinking line, text
        # line, ...), so summing raw rows would double/triple count. Take the
        # max usage per message_id (each row carries the same totals for that
        # message), then sum across distinct messages.
        per_msg: dict[str, dict[str, int]] = {}
        started = session.started_at_epoch
        try:
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                # Only count records from this call onward.
                ts = rec.get("timestamp", "")
                ts_epoch = _iso_to_epoch(ts)
                if ts_epoch is not None and ts_epoch < started:
                    continue
                if rec.get("type") == "assistant":
                    msg = rec.get("message", {}) or {}
                    mid = msg.get("id") or ""
                    usage = msg.get("usage") or {}
                    if isinstance(usage, dict):
                        row_in = usage.get("input_tokens", 0) or 0
                        row_out = usage.get("output_tokens", 0) or 0
                        cur = per_msg.get(mid, {"in": 0, "out": 0})
                        # max per message_id (rows repeat the same totals)
                        cur["in"] = max(cur["in"], row_in)
                        cur["out"] = max(cur["out"], row_out)
                        per_msg[mid] = cur
        except Exception:
            logger.warning("Failed to replay usage JSONL for session %s", cli_session_id[:8])
            return
        in_t = sum(v["in"] for v in per_msg.values())
        out_t = sum(v["out"] for v in per_msg.values())
        requests = len(per_msg)
        if in_t or out_t or requests:
            session.total_input_tokens = in_t
            session.total_output_tokens = out_t
            session.total_tokens = in_t + out_t
            if session.request_count == 0:
                session.request_count = requests
            logger.info(
                "Replayed timeout usage from JSONL: in=%d out=%d req=%d (session=%s)",
                in_t,
                out_t,
                requests,
                cli_session_id[:8],
            )

    def _recover_response_text_from_jsonl(self, session: _LocalSession, cli_session_id: str) -> str:
        """Return the last visible assistant text from the session JSONL (#2640).

        Uses the same transcript-reading machinery as
        ``_replay_usage_from_jsonl`` (projects root + owning-user read) but
        extracts assistant text instead of usage, keeping only records with
        timestamp >= this run's ``started_at_epoch``. Fail-soft: any error
        logs a warning and returns "" so a broken fallback can never fail
        the run itself.
        """
        if not cli_session_id or not session.encoded_project_path:
            return ""
        try:
            jsonl_path = (
                self._claude_projects_root(session.system_account, session.task_id)
                / session.encoded_project_path
                / f"{cli_session_id}.jsonl"
            )
            content = self._read_text_as_user(jsonl_path, session.system_account)
        except OSError as exc:
            logger.warning(
                "Transcript text fallback could not read JSONL (session=%s): %s",
                cli_session_id[:8],
                exc,
            )
            return ""
        return self._last_assistant_text_in_transcript(content, session, cli_session_id)

    def _last_assistant_text_in_transcript(
        self, content: str, session: _LocalSession, cli_session_id: str
    ) -> str:
        """Extract THIS turn's final assistant text from a transcript (#2640).

        Shared by the host-JSONL reader above and the carried-transcript reader
        below (#3237), because they must not diverge. Two properties do the
        work and both matter:

        * records older than ``started_at_epoch`` are skipped — a session
          line's transcript is cumulative across milestones, so without this a
          later turn would recover the whole line's history;
        * only the LAST visible assistant text is kept — concatenating every
          block would fold intermediate narration ("Let me read the file.")
          into the deliverable.

        An earlier version of the carried reader did neither, and its own test
        asserted the concatenation. An independent review caught it.
        """
        last_text = ""
        try:
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                ts_epoch = _iso_to_epoch(rec.get("timestamp", ""))
                if ts_epoch is not None and ts_epoch < session.started_at_epoch:
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                text = _extract_visible_text(msg.get("content", ""))
                if text.strip():
                    last_text = text
        except Exception as exc:  # fail-soft by contract (#2640)
            logger.warning(
                "Transcript text fallback failed (session=%s): %s",
                cli_session_id[:8],
                exc,
            )
            return ""
        return last_text.strip()

    def _recover_final_response_text(self, session: _LocalSession, cli_session_id: str) -> None:
        """Recover response text when assistant stream events were dropped (#2640).

        Large-context (--resume) turns can complete with a terminal ``result``
        event — usage accounted, exit 0 — while the final assistant stream
        event never arrives, leaving ``event_log`` without any assistant text.
        The orchestrator then terminal-fails the phase with "<agent> returned
        no result" even though the deliverable exists.

        Layered fallback, only when event_log has NO assistant text:
        1. the ``result`` event's ``result`` text (captured in _read_stdout);
        2. the last visible assistant message in the transcript JSONL written
           at/after this run's start.

        On recovery the text is written back into ``session.event_log`` (a
        synthetic assistant entry) and ``session.assistant_text`` so both
        ``_build_agent_task_result`` and ``_persist_local_session_messages``
        see the turn — the DB timeline recovers it too.
        """
        if any(
            event.get("type") == "assistant" and str(event.get("text") or "").strip()
            for event in session.event_log
        ):
            return  # normal path: assistant stream events were captured

        recovered = (session.result_text or "").strip()
        source = "result_event"
        if not recovered and cli_session_id:
            recovered = self._recover_response_text_from_jsonl(session, cli_session_id)
            source = "transcript_jsonl"

        if not recovered:
            # #3237: the reader above resolves a HOST path
            # (_claude_projects_root), which is empty for a run that happened
            # inside a sandbox — so this net silently no-opped on exactly the
            # large-context turns it exists for. Carried source, same parsing:
            # both go through _last_assistant_text_in_transcript.
            recovered = self._recover_response_text_from_store(
                session, session.workflow_id, session.session_id
            )
            source = "carried_transcript"

        if not recovered:
            return

        session.event_log.append(
            {
                "type": "assistant",
                "text": recovered,
                "message_id": None,
                "model": None,
            }
        )
        session.assistant_text += recovered
        logger.info(
            "Recovered final response text from %s for session %s (%d chars)",
            source,
            session.session_id[:8],
            len(recovered),
        )

    def _recover_response_text_from_store(
        self, session: _LocalSession, workflow_id: str, tracking_session_id: str
    ) -> str:
        """Recover THIS turn's assistant text from the CARRIED transcript (#3237).

        The sibling ``_recover_response_text_from_jsonl`` reads the host's
        ``~/.claude/projects``, which is empty for a run that happened inside a
        sandbox — the transcript lived in the pod and left with it. Different
        source, IDENTICAL parsing: both delegate to
        ``_last_assistant_text_in_transcript``, so the turn filter and the
        last-message rule cannot drift between them.

        That sharing is load-bearing here rather than merely tidy. The carried
        blob is the whole session line's transcript, appended across every
        milestone, so a reader without the ``started_at_epoch`` filter would
        hand back milestones 1-N concatenated and the caller would persist it
        as this milestone's deliverable.

        Never raises: this is a recovery net, and a net that can fail the run
        it is catching is worse than no net.
        """
        if not workflow_id or not tracking_session_id:
            return ""
        try:
            blob = self._agent_state_store.get(workflow_id, tracking_session_id)
        except Exception:  # noqa: BLE001 - recovery must never raise
            return ""
        if not blob:
            return ""
        return self._last_assistant_text_in_transcript(
            blob.decode("utf-8", errors="replace"), session, tracking_session_id
        )

    def _finalize_local_completed_result(
        self,
        session: _LocalSession,
        session_id: str,
        resolved_session_id: str,
    ) -> AgentTaskResult:
        """Build the AgentTaskResult for a completed local run (#2640).

        Runs the response-text recovery first so the returned result AND the
        session persistence (which consumes result.event_log) both see the
        recovered turn.
        """
        self._recover_final_response_text(session, resolved_session_id or session.cli_session_id)
        result = _build_agent_task_result(
            session_id=session_id,
            tracking_session_id=session_id,
            source_session_id=resolved_session_id,
            event_log=session.event_log,
            fallback_text=session.assistant_text,
            total_tokens=session.total_tokens,
            total_input_tokens=session.total_input_tokens,
            total_output_tokens=session.total_output_tokens,
            request_count=session.request_count,
            tool_calls=session.tool_calls,
            success=session.error is None,
            error=session.error,
            error_code=session.error_code,
        )
        # #2022 P5: attribute the result (provider/id/generation/state) so the
        # orchestrator can persist sandbox identity + every evidence path carries it.
        return self._stamp_sandbox_attribution(
            result, session.sandbox_handle, session.sandbox_provider
        )

    def _ensure_sidebar_session(self, session: _LocalSession) -> str:
        """Resolve the real sidebar Claude session id for a workflow-owned line."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return session.session_id
        if session.persisted_session_id:
            return session.persisted_session_id

        # Prefer the real CLI session_id captured from the SDK init
        # control_response; fall back to mtime-based JSONL discovery only when
        # the control_response was never received (older CLI / parse miss).
        if session.cli_session_id:
            persisted_id = session.cli_session_id
        else:
            # mtime fallback — this is the original #848 pollution mechanism.
            # control_response covers the vast majority of cases; if this fires
            # and the guessed id is wrong, it can get pinned to a session line
            # and propagated via --resume. Warn so it's traceable.
            # Exclude sessions already bound to another line (main/review/test):
            # a shared main session is continuously appended and would otherwise
            # always win the mtime race, collapsing the 3-session design (#723).
            bound_ids = set()
            if self.session_manager:
                bound_ids = self.session_manager.list_cli_session_ids_for_project(
                    session.project_path
                )
            persisted_id = self._find_latest_claude_session_id(
                session.encoded_project_path,
                session.started_at_epoch,
                bound_cli_session_ids=bound_ids,
                system_account=session.system_account,
                task_id=session.task_id,
            )
            logger.warning(
                "Using mtime fallback to resolve session (control_response missed) — "
                "workflow=%s path=%s -> %s",
                session.workflow_id,
                session.encoded_project_path,
                (persisted_id or "<none>")[:8],
            )
        if not persisted_id:
            return ""

        if self.session_manager:
            try:
                existing = self.session_manager.get_session(session.session_id) or {}
                # get_session returns an AgentSession object (or {} when absent);
                # read its context via getattr so prior context is preserved
                # rather than clobbered. cli_session_id is the authoritative
                # column; we mirror it into context for any legacy reader.
                context = dict(getattr(existing, "context", {}) or {})
                context.update(
                    {
                        "workflow_id": session.workflow_id,
                        "cli_session_id": persisted_id,
                    }
                )
                self.session_manager.update_session_fields(
                    session.session_id,
                    {
                        "context": context,
                        "status": "active",
                        "cli_session_id": persisted_id,
                    },
                    require_tenant=False,
                )
            except Exception as e:
                session.persisted_session_id = ""
                logger.warning("Failed to sync resolved Claude session mapping: %s", e)
                return ""
        session.persisted_session_id = persisted_id
        if self._activity_callback:
            self._activity_callback(
                session.session_id,
                {
                    "type": "session_resolved",
                    "session_id": session.session_id,
                    "source_session_id": persisted_id,
                },
            )

        return persisted_id

    def _resolve_sidebar_session(
        self,
        session: _LocalSession,
        wait_timeout: float = 0.0,
    ) -> str:
        """Resolve the persisted sidebar session, optionally waiting for late JSONL flushes."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return session.session_id
        if session.persisted_session_id:
            return session.persisted_session_id

        deadline = time.monotonic() + max(wait_timeout, 0.0)
        while True:
            persisted_id = self._ensure_sidebar_session(session)
            if persisted_id:
                return persisted_id
            if time.monotonic() >= deadline:
                return ""
            time.sleep(SESSION_DETECTION_POLL_INTERVAL)

    def _sync_sidebar_session_totals(
        self, session: _LocalSession, status: str | None = None
    ) -> None:
        """Write the current local Claude usage into the workflow-owned session row."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return
        cli_session_id = session.persisted_session_id or self._resolve_sidebar_session(session)
        if not cli_session_id or not self.session_manager:
            return

        # NOTE: request_count / total_tokens / in-out are owned by
        # increment_session_usage at finish time (per-call delta), NOT written
        # here. Writing them here during streaming (ASSIGN) would double-count
        # against the finish increment (#1007 review).
        updates = {
            "project_path": session.project_path,
        }
        if session.user_id:
            updates["user_id"] = session.user_id
        if status:
            updates["status"] = status
        try:
            existing = self.session_manager.get_session(session.session_id) or {}
            # get_session returns an AgentSession; read context via getattr.
            context = dict(getattr(existing, "context", {}) or {})
            context.update(
                {
                    "workflow_id": session.workflow_id,
                    "cli_session_id": cli_session_id,
                }
            )
            updates["context"] = context
            # Authoritative column — written in the same UPDATE as context so a
            # partial context merge can never lose the resume target (#1200).
            updates["cli_session_id"] = cli_session_id
        except Exception as e:
            logger.warning("Failed to load tracking session context: %s", e)

        try:
            self.session_manager.update_session_fields(
                session.session_id, updates, require_tenant=False
            )
        except Exception as e:
            logger.warning("Failed to sync sidebar session totals: %s", e)

    @staticmethod
    def _wait_for_completion(session: _LocalSession, timeout: float) -> bool:
        """Wait for ``session.completed`` while excluding paused time from the budget.

        ``threading.Event.wait(timeout=...)`` is a wall-clock countdown — it keeps
        ticking even while the underlying process is frozen with SIGSTOP. That means
        pausing a task for longer than the timeout (default 1h) would reap the
        in-flight agent even though no real work was being done (#1005).

        This loop instead accounts for pause/resume: while ``session._paused``
        is set we do not consume the timeout budget, extending the deadline by
        the duration of each pause. The budget resumes counting down only once
        the process is unfrozen. Returns ``True`` if the session completed (or
        was stopped) within the budget, ``False`` if the *active* time budget
        ran out.
        """
        deadline = time.monotonic() + max(timeout, 0.0)
        poll = COMPLETION_POLL_INTERVAL
        while True:
            if session.completed.wait(timeout=min(poll, max(deadline - time.monotonic(), 0.0))):
                return True
            now = time.monotonic()
            # While paused, freeze the deadline so the remaining budget is
            # preserved across the suspension. Resume stretches the deadline by
            # the full paused duration.
            if session._paused.is_set():
                pause_started = now
                # Fixed `poll` here (not min(poll, remaining)) is deliberate:
                # paused time is not budgeted, so there's no deadline to bound
                # the wait against — we just need to notice resume/completion.
                while session._paused.is_set() and not session.completed.is_set():
                    session.completed.wait(timeout=poll)
                now = time.monotonic()
                deadline += now - pause_started
            if now >= deadline:
                return False

    def run_agent_task(
        self,
        workflow_id: str,
        cli_tool: str,
        model: str,
        project_path: str,
        prompt: str,
        workspace_type: str = "local",
        remote_machine_id: str = None,
        permission_mode: str = "auto-edit",
        session_type: str = "workflow",
        timeout: int = DEFAULT_TASK_TIMEOUT,
        session_id: str = None,
        user_id: int | None = None,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
        resume_session_id: str = None,
        milestone_id: str = "",
        system_account: str | None = None,
        runtime_python_command: list[str] | None = None,
    ) -> AgentTaskResult:
        """
        Execute an agent task and wait for completion.

        Args:
            workflow_id: Parent workflow ID.
            cli_tool: CLI tool name (claude-code, qwen-code-cli, codex, etc.).
            model: Model name.
            project_path: Project directory path.
            prompt: The prompt to send to the agent.
            workspace_type: 'local' or 'remote'.
            remote_machine_id: Required for remote execution.
            permission_mode: Permission mode for the agent.
            session_type: Session type record.
            timeout: Maximum wait time in seconds.
            session_id: Optional pre-generated session_id (for cancellation tracking).

        Args:
            system_account: Optional system account name for multi-user permission isolation.
                When set, CLI tools will be run via `sudo -u <system_account>` to access
                user-private directories.

        Returns:
            AgentTaskResult with response text, messages, tokens, etc.
        """
        # #2020 Phase B: honor AgentTaskPolicy.wall_clock_limit (>0) over the
        # orchestrator-passed timeout. wall_clock is the "TIME" in
        # CPU_MEM_PIDS_TIME_QUOTA — Legacy enforces it via this deadline.
        timeout = self._resolve_wall_clock_timeout(timeout, self._load_task_policy())
        session_id = session_id or str(uuid.uuid4())
        uses_sidebar_session = self._uses_sidebar_session_source(cli_tool, workspace_type)
        # App-server tools (ZCode) resolve their real CLI session id only after
        # session/create inside _run_zcode_appserver. The wrapper row must be
        # keyed by that CLI id (not the uuid) so add_message's session-exists
        # check passes and milestone/messages keys line up — same invariant
        # _ensure_sidebar_session gives Claude. So skip the uuid-keyed pre-create
        # here; _run_zcode_appserver creates the row under the real CLI id.
        creates_session_late = cli_tool in _APPSERVER_TOOLS

        # Resolved unconditionally. It is read later by _sync_usage_to_daily_usage
        # on BOTH the local and remote paths, but was assigned only inside the
        # branch below — so any app-server tool (ZCode sets creates_session_late)
        # raised UnboundLocalError there. That surfaced as a bare
        # "cannot access local variable" with error_code=None, masking the real
        # diagnostic, including the #2023 production-isolation refusal. The bug
        # predates this PR; the new refusal is what made it visible.
        wf_tenant_id = self._resolve_tenant_id(user_id)

        # Create wrapper sessions only for tools without a deferred session id.
        if self.session_manager and not creates_session_late:
            try:
                self.session_manager.create_session(
                    session_id=session_id,
                    session_type=session_type,
                    title=f"Autonomous: {workflow_id[:8]}",
                    tool_name=cli_tool,
                    user_id=user_id,
                    tenant_id=wf_tenant_id,
                    project_path=project_path,
                    workspace_type=workspace_type,
                    remote_machine_id=remote_machine_id,
                    context={"workflow_id": workflow_id},
                )
            except Exception as e:
                logger.warning("Failed to create session record: %s", e)

        # A resumed session line (main/review/test) may carry a completed/error
        # status from a prior run (run_agent_task writes the terminal status at
        # the end of every call). The LLM proxy token validator requires
        # agent_sessions.status in (active, paused), so reactivate before any
        # proxy-token-bearing env is built. Only touches the autonomous path —
        # WebUI/other create_session callers are unaffected.
        if self.session_manager and session_id:
            try:
                existing = self.session_manager.get_session(session_id)
                if existing:
                    cur_status = getattr(existing, "status", "") or (
                        existing.get("status", "") if isinstance(existing, dict) else ""
                    )
                    if cur_status not in ("active", "paused"):
                        # Clear stale terminal timestamps too — otherwise the
                        # Sessions UI keeps rendering the old completed_at for a
                        # row that is now active again (#1475 review).
                        self.session_manager.update_session_fields(
                            session_id,
                            {"status": "active", "completed_at": None, "paused_at": None},
                            require_tenant=False,
                        )
                        logger.info(
                            "Reactivated session %s (was %s) for agent run",
                            session_id[:8],
                            cur_status,
                        )
            except Exception as e:
                logger.warning("Failed to reactivate session %s: %s", session_id[:8], e)

        logger.info(
            "Starting agent task: tool=%s model=%s workspace=%s session=%s",
            cli_tool,
            model,
            workspace_type,
            session_id[:8],
        )

        try:
            if workspace_type == "remote" and remote_machine_id:
                result = self._run_remote(
                    session_id=session_id,
                    user_id=user_id,
                    cli_tool=cli_tool,
                    model=model,
                    project_path=project_path,
                    prompt=prompt,
                    remote_machine_id=remote_machine_id,
                    permission_mode=permission_mode,
                    timeout=timeout,
                    allowed_tools=allowed_tools,
                )
            else:
                result = self._run_local(
                    session_id=session_id,
                    cli_tool=cli_tool,
                    model=model,
                    project_path=project_path,
                    prompt=prompt,
                    permission_mode=permission_mode,
                    timeout=timeout,
                    workflow_id=workflow_id,
                    user_id=user_id,
                    workspace_type=workspace_type,
                    allowed_tools=allowed_tools,
                    resume=resume,
                    resume_session_id=resume_session_id,
                    milestone_id=milestone_id,
                    system_account=system_account,
                    runtime_python_command=runtime_python_command,
                )

            result.prompt = prompt
            persisted_session_id = (
                result.session_id if uses_sidebar_session else (result.session_id or session_id)
            )
            message_delta = 0

            # Persist session messages to database (Issue #776 Bug 1)
            if self.session_manager and persisted_session_id and workspace_type == "local":
                try:
                    message_delta = self._persist_local_session_messages(
                        persisted_session_id, result, milestone_id
                    )
                except Exception as e:
                    logger.warning("Failed to persist session messages: %s", e)

            # Update session record. Counters are INCREMENTED (not overwritten)
            # so the session columns stay cumulative and Σ milestone == Σ session
            # holds (#1003). The previous overwrite reset the columns to each
            # call's local count, breaking the invariant.
            if self.session_manager and persisted_session_id and workspace_type != "remote":
                try:
                    self.session_manager.increment_session_usage(
                        persisted_session_id,
                        message_delta=message_delta,
                        request_delta=result.request_count or 0,
                        total_tokens_delta=result.total_tokens or 0,
                        total_input_delta=result.total_input_tokens or 0,
                        total_output_delta=result.total_output_tokens or 0,
                        require_tenant=False,
                    )
                    status = "completed" if result.success else "error"
                    self.session_manager.update_session_fields(
                        persisted_session_id, {"status": status}, require_tenant=False
                    )
                except Exception as e:
                    logger.warning("Failed to update session record: %s", e)

                # Issue #2585: Sync usage to daily_usage table for local autonomous
                if persisted_session_id or session_id:
                    self._sync_usage_to_daily_usage(
                        session_id=persisted_session_id or session_id,
                        cli_tool=cli_tool,
                        tenant_id=wf_tenant_id,
                        result=result,
                    )

            # RemoteSessionManager owns the actual remote row's transcript,
            # usage and status lifecycle. Re-applying the result totals here
            # would double-count usage reports that the remote endpoint has
            # already persisted. Only close the hidden stable tracking row;
            # its cli_session_id mapping was written by _run_remote.
            if self.session_manager and workspace_type == "remote":
                try:
                    self.session_manager.update_session_fields(
                        session_id,
                        {"status": "completed" if result.success else "error"},
                        require_tenant=False,
                    )
                except Exception as e:
                    logger.warning("Failed to update remote tracking session: %s", e)

                # Issue #2585: Sync usage to daily_usage table for remote autonomous
                self._sync_usage_to_daily_usage(
                    session_id=session_id,
                    cli_tool=cli_tool,
                    tenant_id=wf_tenant_id,
                    result=result,
                )

            # Close the eagerly-created workflow wrapper row when the agent never
            # produced a real CLI session id (sidebar source + executable not
            # found / spawn failed → result.session_id=""). Without this the
            # wrapper stays status='active' forever — a zombie row. The wrapper
            # is keyed by `session_id` (the tracking uuid), not the empty CLI id.
            if uses_sidebar_session and not persisted_session_id and self.session_manager:
                try:
                    self.session_manager.update_session_fields(
                        session_id,
                        {
                            "status": "error",
                            "error_message": result.error or "agent failed to start",
                        },
                        require_tenant=False,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to close orphan workflow wrapper %s: %s",
                        session_id[:8],
                        e,
                    )

            return result

        except Exception as e:
            logger.error("Agent task failed: %s", e)
            # For tools that defer row creation (app-server tools), a failure
            # before _run_zcode_appserver ran (e.g. adapter/executable not
            # found) leaves no agent_sessions row. Create one under the uuid so
            # the failed run stays visible. Claude (uses_sidebar_session) is
            # excluded — its row is created under the CLI id inside _run_local,
            # and pre-create tools already have their row from the block above.
            # App-server tools only run locally, so guard on workspace_type too.
            if creates_session_late and not uses_sidebar_session and workspace_type == "local":
                self._create_workflow_session(
                    session_id,
                    workflow_id,
                    cli_tool,
                    user_id,
                    project_path,
                    workspace_type,
                )
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=str(e),
            )

    @property
    def _agent_state_store(self) -> Any:
        """Holds one CLI transcript per session line between turns (#3237).

        Lazy rather than set in ``__init__`` deliberately: the runner is
        legitimately built with ``__new__`` in tests and helper paths, and an
        attribute that only exists when ``__init__`` ran would make the export
        hook fail there — turning a storage detail into a broken run.
        """
        from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

        existing = self.__dict__.get("_agent_state_store_instance")
        if existing is None:
            existing = AgentStateStore()
            self.__dict__["_agent_state_store_instance"] = existing
        return existing

    @_agent_state_store.setter
    def _agent_state_store(self, store: Any) -> None:
        self.__dict__["_agent_state_store_instance"] = store

    @staticmethod
    def _workflow_no_longer_wants_state(workflow_id: str, session: Any) -> bool:
        """Whether this turn's transcript is already dead weight (#3237).

        Asked of the DATABASE, not of a ``threading.Event``. An earlier version
        gated only on ``session._stopped``, which cannot work in the shipped
        topology: the routes run in the web process and the runner in the
        scheduler process, so `_stop_running_task` — whose three strategies all
        resolve through the in-process ``AutonomousScheduler`` singleton or a
        local PID — never reaches this session's Event at all. `delete_workflow`
        and `delete_batch` do not even call it. The flag stayed false, the
        export ran, and the just-purged directory came back.

        The Event is still consulted first, because when it IS set it is both
        correct and free. The row is the authority behind it.

        Fail-safe direction on an unreadable database: **export anyway**. A
        resurrected transcript is bounded — the reaper and the next delete both
        reclaim it — whereas a skipped export loses history the next turn needs
        and starts it cold, which is the failure this whole change exists to
        remove.
        """
        stopped = getattr(session, "_stopped", None)
        if stopped is not None and stopped.is_set():
            return True
        if not workflow_id:
            return False
        try:
            from app.modules.workspace.autonomous.orchestrator import _TERMINAL_WORKFLOW_STATUSES
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository

            workflow = AutonomousWorkflowRepository().get_workflow(workflow_id)
        except Exception:  # noqa: BLE001 - unknown means export, see above
            return False
        if workflow is None:
            # The row is gone: deleted while this turn was unwinding. Nothing
            # will ever resume it, and the delete route's purge already ran.
            return True
        return str(workflow.get("status") or "") in _TERMINAL_WORKFLOW_STATUSES

    def _plan_agent_state(
        self,
        provider: object,
        *,
        workflow_id: str,
        tracking_session_id: str,
        resume: bool,
        cli_tool: str = "claude-code",
    ) -> _AgentStatePlan:
        """Decide whether this turn can resume, before anything is created.

        Mirrors ``scripts/openace-run-as.sh`` point for point rather than
        applying one blanket policy — the launcher fails closed only where
        failing is free and continuing would corrupt:

        * an ``ephemeral`` provider asked to resume -> refuse, the ``exit 70``
          analogue. Nothing has run, so aborting costs nothing, and continuing
          would burn a whole invocation on a ``--resume`` that cannot work.
        * a slot present but unreadable -> refuse, same reasoning: we cannot
          tell "no history" from "broken history".
        * a slot simply ABSENT -> start fresh. That is the launcher's
          ``if [ -d "$preserve_claude_dir" ]`` guard around its restore, and it
          is why a control-plane reboot that clears tmpfs does not kill
          in-flight workflows.
        """
        from app.modules.workspace.autonomous.sandbox.agent_state_store import CorruptAgentState
        from app.modules.workspace.autonomous.sandbox.provider import (
            AGENT_STATE_CARRIED,
            AGENT_STATE_PERSISTS,
            agent_state_persistence,
        )

        if not resume:
            return _AgentStatePlan(resume=False)

        mode = agent_state_persistence(provider)
        # A carrying provider still only carries the tools it knows how to
        # address. OpenSandbox reads/writes each tool's own transcript layout —
        # `.claude/projects/-workspace/<id>.jsonl` for claude-code,
        # `.qwen/projects/-workspace/chats/<id>.jsonl` for qwen-code-cli (#3319)
        # — and `_capture_cli_session_id` yields the id for both from the
        # stream. A tool with neither a captured id nor a provider path would get
        # an empty id, discard the slot, and cold-start the next turn: silent
        # continuity loss. Refuse instead — the turn costs nothing yet, and a
        # stated refusal is recoverable where a silent cold start is not.
        #
        # claude-code and qwen-code-cli (#3319) are the tools this seam can
        # carry: both take the stream-json path, emit their session_id on stdout,
        # and the provider knows their transcript layout. ZCode and the
        # single-shot tools (codex, openclaw) return from _run_local before
        # `_select_sandbox_provider` and spawn locally, so they never reach here;
        # any future stream-json tool without a provider path must refuse rather
        # than resume into an empty HOME.
        if mode == AGENT_STATE_CARRIED and cli_tool not in _CARRIED_STATE_TOOLS:
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=(
                    f"{type(provider).__name__} does not carry agent state for "
                    f"{cli_tool!r}: its transcript location and session-id capture are "
                    "not wired for this tool. Refusing rather than resuming "
                    "into an empty HOME."
                ),
            )
        if mode == AGENT_STATE_PERSISTS:
            return _AgentStatePlan(resume=True)

        if mode != AGENT_STATE_CARRIED:
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=(
                    f"provider {type(provider).__name__} does not carry agent state "
                    "between turns, so --resume would be sent into an empty HOME and "
                    "the whole invocation wasted. Refusing before the sandbox is created."
                ),
            )

        try:
            blob = self._agent_state_store.get(workflow_id, tracking_session_id)
        except CorruptAgentState as exc:
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=str(exc),
            )
        except ValueError as exc:
            # An id that is not a plain path component. Refuse rather than
            # silently run without history under a key we could not form.
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=f"cannot address agent state: {exc}",
            )

        if blob is None:
            # Absent, not broken: first turn on this line, or tmpfs cleared.
            return _AgentStatePlan(resume=False)
        return _AgentStatePlan(resume=True, blob=blob)

    @staticmethod
    def _resolve_agent_command(
        adapter: Any, adapter_args: list[str], previous_cmd: list[str]
    ) -> list[str]:
        """Turn adapter args into a launchable command, reusing argv[0] (#3237).

        The first build resolves the executable through ``shutil.which`` for
        adapters whose ``provides_full_command()`` is False. A re-derivation
        later in the run must land on the SAME argv[0] — taking the adapter
        args verbatim would silently swap a resolved path for a bare name, so
        a merely degraded turn (no ``--resume``) would become a turn that
        launches a different binary, or none.

        ``previous_cmd`` supplies that already-resolved argv[0] rather than
        re-running ``which``: it is what this run actually started from. The
        first build passes the freshly resolved executable; the re-derivation
        passes the ``cmd`` it is replacing. Both go through here so the two
        cannot drift — the duplication is what the extraction exists to remove.

        The bare-name fallback for an empty ``previous_cmd`` is unreachable
        from either call site (the first build has just resolved one, and the
        re-derivation runs only after it succeeded); it is a floor, not a path.
        """
        if adapter.provides_full_command():
            return list(adapter_args)
        head = previous_cmd[0] if previous_cmd else adapter.get_executable_name()
        return [head] + (list(adapter_args[1:]) if len(adapter_args) > 1 else [])

    def _build_agent_argv(
        self,
        adapter: Any,
        resume_target: str,
        project_path: str,
        model: str,
        permission_mode: str | None,
        allowed_tools: list[str] | None,
        *,
        resume: bool,
    ) -> list[str]:
        """Build the adapter's argv. PURE — no I/O, no side effects.

        Extracted so the sandbox path can re-derive argv once it knows whether
        the agent state actually landed (#3237). ``cmd`` is otherwise built
        before the sandbox exists, which bakes ``--resume`` in before anything
        could know whether the transcript is there to resume from.
        """
        return cast(
            "list[str]",
            adapter.build_start_args(
                resume_target,
                project_path,
                model,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                resume=resume,
            ),
        )

    def _run_local(
        self,
        session_id: str,
        cli_tool: str,
        model: str,
        project_path: str,
        prompt: str,
        permission_mode: str,
        timeout: int,
        workflow_id: str,
        user_id: int | None,
        workspace_type: str,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
        resume_session_id: str = None,
        milestone_id: str = "",
        system_account: str | None = None,
        runtime_python_command: list[str] | None = None,
        tenant_id: int | None = None,
    ) -> AgentTaskResult:
        """Run an agent task locally using a CLI subprocess."""
        import sys

        _remote_agent_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "remote-agent")
        )
        if _remote_agent_dir not in sys.path:
            sys.path.insert(0, _remote_agent_dir)
        from cli_adapters import get_adapter

        # Cache the usage parser now that remote-agent is on sys.path
        _ensure_usage_parser()

        # Find executable
        adapter = get_adapter(cli_tool)

        # Expand project path
        project_path = os.path.expanduser(project_path)
        # Ensure the project dir exists (cross-user safe, Issue #1395).
        self._ensure_project_dir(project_path, system_account)

        # #2023: resolve the tenant and check the isolation policy HERE, above
        # the protocol dispatch. The dispatch below returns from inside itself
        # for ZCode and for every adapter without stdin, so neither ever reached
        # the provider gate further down — a tenant in production_required_tenants
        # picking one of those tools would have run on Legacy with nothing
        # recorded to say the policy had been bypassed. Fail closed instead.
        try:
            if tenant_id is None:
                tenant_id = self._resolve_tenant_for_isolation(
                    user_id, cli_tool=cli_tool, adapter=adapter
                )
        except SandboxError as e:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Sandbox unavailable: {e}",
                error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
            )

        # Protocol dispatch: different CLI tools speak different stdin protocols.
        # The generic _LocalSession path below assumes Claude SDK stream-json,
        # which only claude-code and qwen-code-cli support. Route tools with
        # their own app-server protocol (ZCode) or no stdin protocol at all
        # (codex, openclaw) through dedicated paths. Mirrors the dispatch in
        # remote-agent/executor.py (_APPSERVER_TOOLS + supports_stdin_input).
        if cli_tool in _APPSERVER_TOOLS:
            return self._run_zcode_appserver(
                session_id=session_id,
                cli_tool=cli_tool,
                model=model,
                project_path=project_path,
                prompt=prompt,
                permission_mode=permission_mode,
                timeout=timeout,
                workflow_id=workflow_id,
                user_id=user_id,
                workspace_type=workspace_type,
                allowed_tools=allowed_tools,
                resume=resume,
                resume_session_id=resume_session_id,
                milestone_id=milestone_id,
                system_account=system_account,
                runtime_python_command=runtime_python_command,
                tenant_id=tenant_id,
            )
        if not adapter.supports_stdin_input():
            return self._run_single_shot(
                session_id=session_id,
                cli_tool=cli_tool,
                model=model,
                project_path=project_path,
                prompt=prompt,
                timeout=timeout,
                workflow_id=workflow_id,
                milestone_id=milestone_id,
                system_account=system_account,
                user_id=user_id,
                runtime_python_command=runtime_python_command,
                resume=resume,
                resume_session_id=resume_session_id,
            )

        # Build env vars with LLM proxy auth so the agent authenticates through
        # Open ACE (short-lived proxy token, never the raw API key).
        env = (
            self._build_agent_env(
                adapter,
                cli_tool,
                user_id,
                session_id,
                model,
                runtime_python_command,
                task_id=session_id,
            )
            if runtime_python_command
            else self._build_agent_env(
                adapter, cli_tool, user_id, session_id, model, task_id=session_id
            )
        )

        # #2023: resolve the provider per run through the isolation gate rather
        # than reaching for the constructor-injected one.
        # #3237: resolved HERE, above argv, because the agent-state plan decides
        # whether `--resume` goes into argv at all. Selecting it twice would
        # double-emit audit events and re-run the boot probes, so there is
        # exactly one call and `provider` is reused by the create block below.
        # A fail-closed refusal (a missing attestation, a probe mismatch) and a
        # malformed backend config are both SandboxError, not OSError — without
        # this they would escape _run_local with no AgentTaskResult at all.
        try:
            provider = self._select_sandbox_provider(
                "local",
                tenant_id=tenant_id,
                project_path=project_path,
                generation=self._resolve_sandbox_generation(workflow_id),
            )
        except SandboxError as e:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Sandbox unavailable: {e}",
                error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
            )

        # #3237: decide agent state BEFORE argv and before anything is created,
        # so a turn that could not have resumed costs no sandbox and no tokens.
        state_plan = self._plan_agent_state(
            provider,
            workflow_id=workflow_id,
            tracking_session_id=session_id,
            resume=resume,
            cli_tool=cli_tool,
        )
        if state_plan.refuse:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=state_plan.detail,
                error_code=state_plan.reason_code,
            )
        resume = state_plan.resume
        # The CLI session id this turn actually resumed, set only once the
        # transcript is really in place. The export below falls back to it when
        # the stream capture missed.
        resumed_with = ""

        # Build command
        # When resuming an established session, pass the real CLI session_id
        # so the adapter emits `--resume <id>`; otherwise let the CLI mint a new
        # session and capture its id from the control_response.
        resume_target = resume_session_id if (resume and resume_session_id) else session_id
        adapter_args = self._build_agent_argv(
            adapter,
            resume_target,
            project_path,
            model,
            permission_mode,
            allowed_tools,
            resume=resume,
        )

        # Some adapters (e.g. ZCode) return a self-contained command whose first
        # element is an interpreter (``node <engine.cjs>``) rather than a PATH
        # executable. Use those args verbatim; otherwise resolve the executable
        # via PATH and prepend it.
        if adapter.provides_full_command():
            cmd = self._resolve_agent_command(adapter, adapter_args, [])
        else:
            exe_name = adapter.get_executable_name()
            executable = shutil.which(exe_name)
            if not executable:
                return AgentTaskResult(
                    session_id=(
                        ""
                        if self._uses_sidebar_session_source(cli_tool, workspace_type)
                        else session_id
                    ),
                    tracking_session_id=session_id,
                    success=False,
                    error=f"CLI tool '{exe_name}' not found",
                )
            # Same helper the #3237 re-derivation uses, so the two builds cannot
            # drift. `which` stays here: it is the resolution itself, and its
            # failure is a run-ending error rather than something to repeat.
            cmd = self._resolve_agent_command(adapter, adapter_args, [executable])

        # #2022 P3b: spawn through the SandboxProvider. The orchestrator no
        # longer touches sudo/_wrap_agent_cmd/Popen directly for spawn; the
        # provider owns the ACL wrap (build_launch_argv) + spawn + cwd. The CLI
        # stream-json protocol (reader threads, stdin handshake below) still
        # drives the raw Popen the provider exposes via get_process().
        if self._is_cross_user(system_account):
            # Env finalization moved here from _wrap_agent_cmd's cross-user
            # branch. The launcher forwards guard_env verbatim (runuser ...
            # /usr/bin/env -i <env_args> "$@" — env_args sets only HOME/TMPDIR/
            # XDG/GIT_CONFIG, NOT OPENACE_GIT_CACHE_ROOT) and does NOT re-derive
            # it, so the agent user's pre-commit cache path must be set here;
            # the guard-bin safety check fails closed before spawn. _build_agent_env
            # set it to the service user's path (it has no system_account), so
            # this overwrite is load-bearing for cross-user.
            env["OPENACE_GIT_CACHE_ROOT"] = str(
                self._resolve_home_dir(system_account) / ".cache" / "pre-commit"
            )
            self._validate_cross_user_guard_bin(env)
        # `provider` was resolved above, before argv, so the agent-state plan
        # could decide whether `--resume` belongs in the command (#3237). Do NOT
        # re-select it here: a second call double-emits audit events and re-runs
        # the boot probes.
        try:
            sandbox_handle = provider.create(
                SandboxSpec(
                    task_id=session_id,
                    project_path=project_path,
                    cli_tool=cli_tool,
                    system_account=system_account,
                    # #2022 P5: express #2020 HOME/TMP/quota via the spec (not just
                    # enforce ad hoc); Legacy doesn't consume it yet, future providers do.
                    policy=self._load_task_policy(),
                )
            )
            # Persist attribution the moment an id exists, BEFORE upload/exec/
            # attach. Recording it only after attach left a window in which a
            # crash — or a failed boot probe whose best-effort delete also
            # failed — left a live sandbox no workflow row could name, and the
            # metadata-scoped server sweep that could have found it anyway has
            # no production caller. Emitting twice is harmless: the second call
            # rewrites the same row with the same ids.
            self._notify_sandbox_created(session_id, sandbox_handle, None, provider)
        except SandboxError as e:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Sandbox unavailable: {e}",
                error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
            )
        # Preserve the old log: the wrapped launch argv (sudo/openace-run-as for
        # cross-user, verbatim for same-user). build_launch_argv is pure, so
        # calling it for display is harmless; exec re-derives it internally.
        # build_launch_argv is a Legacy-only escape hatch (deliberately NOT on
        # the Protocol), and this call exists only to build a log line — calling
        # it unconditionally would raise AttributeError on any other provider
        # before exec even runs.
        if hasattr(provider, "build_launch_argv"):
            logger.info(
                "Launching local agent: %s",
                " ".join(provider.build_launch_argv(sandbox_handle, cmd, env)),
            )
        else:
            logger.info("Launching agent in sandbox %s", sandbox_handle.sandbox_id)

        try:
            # #2023: materialize the project tree inside the sandbox BEFORE the
            # agent starts. Legacy no-ops (the worktree is already there); an
            # ephemeral backend would otherwise hand the agent an empty /workspace.
            provider.upload_workspace(sandbox_handle, None)
            # #3237: place the CLI transcript before the agent starts, so
            # `--resume` finds a conversation instead of an empty HOME.
            #
            # Restore is BEST EFFORT — openace-run-as.sh's own restore is
            # `mv ... || true` — because the fallback (a fresh session) is
            # correct, merely worse. On failure we re-derive argv without
            # `--resume`, which _build_agent_argv makes cheap and safe.
            if state_plan.blob is not None and hasattr(provider, "import_agent_state"):
                try:
                    provider.import_agent_state(
                        sandbox_handle,
                        cli_session_id=resume_target,
                        blob=state_plan.blob,
                        cli_tool=cli_tool,
                    )
                    resumed_with = resume_target
                except Exception as exc:  # noqa: BLE001 - degrade, never fail the turn
                    logger.warning(
                        "Agent state import failed for %s, starting a fresh session: %s",
                        resume_target[:8],
                        exc,
                    )
                    # Rebuild through the SAME executable-resolution branch the
                    # first build used. Taking the adapter args verbatim here
                    # would silently change argv[0] from the resolved path to a
                    # bare name for every adapter whose provides_full_command()
                    # is False — which is claude-code, the only tool this path
                    # carries state for.
                    cmd = self._resolve_agent_command(
                        adapter,
                        self._build_agent_argv(
                            adapter,
                            resume_target,
                            project_path,
                            model,
                            permission_mode,
                            allowed_tools,
                            resume=False,
                        ),
                        cmd,
                    )
                    # Clearing argv is not enough: the flag drives the session
                    # pre-seed below, which pins `persisted_session_id` to the
                    # id we are no longer resuming. That pin then SKIPS
                    # `_resolve_sidebar_session`, so the fresh id the CLI is
                    # about to mint is never picked up — the turn reports the
                    # stale id, and the transcript it exports is filed against
                    # the previous turn's mapping. The turn succeeds while
                    # quietly breaking the next one's resume.
                    resume = False
            # The provider decides its own per-turn policy. Passing a literal
            # None here is what kept every OpenSandbox run on the foreground
            # /command branch, whose ExecHandle get_transport() then refuses.
            exec_policy = provider.agent_turn_policy(prompt=prompt, model=model, env=env)
            exec_handle = provider.exec(
                sandbox_handle, command=cmd, env=env, exec_policy=exec_policy
            )
            # #2023: the "replaceable local seam" #2022 anticipated. The CLI
            # stream-json layer (_read_stdout/_send_sdk_init) now drives an
            # AgentTransport rather than a raw Popen, so a container backend
            # with no local process can serve the same protocol.
            # LocalProcessTransport is a strict pass-through, so the Legacy path
            # keeps the same object and the same syscalls.
            transport = provider.get_transport(exec_handle)
            # Populated only by LocalProcessTransport; a container backend leaves
            # it None and the pid-keyed paths below are inapplicable there.
            process = getattr(transport, "process", None)
            # #2022 P6: persist a mid-run 'running' row so a crash between exec
            # and task completion leaves an orphan the reconciler can destroy.
            # Local has no external session id → None.
            self._notify_sandbox_created(session_id, sandbox_handle, None, provider)
        except Exception as e:  # noqa: BLE001 - the sandbox EXISTS; nothing may leak it
            # Deliberately catch-all. The previous tuple (OSError,
            # SubprocessError, SandboxError) missed the normal failure of a
            # rejected PTY upgrade: websockets raises InvalidStatus on a
            # 401/403 handshake, which is none of those. The sandbox is
            # already created at this point, so an escaping exception would
            # leak a live sandbox.
            try:
                provider.destroy(sandbox_handle)
            except Exception as destroy_error:  # noqa: BLE001 - must not mask `e`
                # The server that just failed the exec is the one being asked to
                # delete, so this raising is entirely plausible — and letting it
                # replace `e` would escape _run_local with no AgentTaskResult at
                # all. Attribution was persisted right after create, so the
                # reconciler can still find this sandbox.
                logger.warning(
                    "sandbox destroy failed while handling %s: %s", type(e).__name__, destroy_error
                )
            return self._stamp_sandbox_attribution(
                AgentTaskResult(
                    session_id=(
                        ""
                        if self._uses_sidebar_session_source(cli_tool, workspace_type)
                        else session_id
                    ),
                    tracking_session_id=session_id,
                    success=False,
                    error=f"Failed to start process: {e}",
                ),
                sandbox_handle,
                provider,
            )

        session = _LocalSession(
            session_id=session_id,
            process=process,
            transport=transport,
            cli_tool=cli_tool,
            allowed_tools=allowed_tools,
            project_path=project_path,
            encoded_project_path=self._encode_project_path(project_path),
            workflow_id=workflow_id,
            user_id=user_id,
            workspace_type=workspace_type,
            started_at_epoch=time.time(),
            milestone_id=milestone_id,
            system_account=system_account,
            task_id=session_id,
            sandbox_handle=sandbox_handle,
            exec_handle=exec_handle,
            sandbox_provider=provider,
        )
        # For a resumed session the real CLI session_id is known up front; pin
        # it so sidebar detection reuses the existing record instead of guessing.
        if resume and resume_session_id:
            session.cli_session_id = resume_session_id
            session.persisted_session_id = resume_session_id
            session.sdk_initialized.set()
        self._local_sessions[session_id] = session

        # Persist PID to database for reliable cancel/pause. A container backend
        # has no pid; cancellation there routes through the provider instead.
        if self._on_pid_registered and transport.pid is not None:
            try:
                self._on_pid_registered(session_id, transport.pid)
            except Exception as e:
                logger.warning("on_pid_registered callback failed: %s", e)

        # Start output reader threads
        session._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(session,),
            daemon=True,
        )
        session._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(session,),
            daemon=True,
        )
        session._stdout_thread.start()
        session._stderr_thread.start()

        # Send SDK init
        self._send_sdk_init(session)

        # Send the prompt
        self._send_message(session, prompt)

        # Wait for completion or timeout. Use the pause-aware wait so that
        # time spent frozen via SIGSTOP does not consume the timeout budget (#1005).
        completed = self._wait_for_completion(session, timeout)

        # Cleanup
        if completed and transport.returncode is None:
            # The isolated launcher performs its .git integrity check after the
            # CLI emits the terminal result event. Give that trusted wrapper a
            # short window to finish BEFORE escalation — folding this into
            # shutdown() would start signalling mid-check.
            transport.wait(timeout=5)
        if transport.returncode is None:
            # "Signal the process group" has no meaning for a container backend,
            # and os.getpgid(None) raises a TypeError none of the handlers here
            # catch. The transport owns its own escalation.
            transport.shutdown(grace=5.0)

        # #2023: bring the agent's work product back out BEFORE destroy — after
        # destroy the ephemeral filesystem is gone and the run's entire output
        # with it. Legacy no-ops (the agent edited the trusted worktree in
        # place). A failure here is NOT swallowed: for an ephemeral backend an
        # un-collected run has produced nothing, and reporting it as a success
        # is exactly the quiet data loss this backend exists to prevent.
        try:
            provider.apply_changes(sandbox_handle, project_path)
        except Exception as e:  # noqa: BLE001 - any failure invalidates the run
            logger.error("sandbox changeset apply failed for %s: %s", session_id[:8], e)
            if not session.error:
                session.error = f"Sandbox work product could not be applied: {e}"
                session.error_code = getattr(e, "reason_code", "") or "changeset_apply_failed"

        # #3237: capture the CLI transcript before the pod goes, so the next
        # turn on this session line can resume.
        #
        # LOG ONLY on failure. openace-run-as.sh's exit-trap capture logs rather
        # than exits for exactly this reason — the agent has already done the
        # work, and an exit here "would rewrite the status". Discarding a
        # completed milestone because its transcript could not be saved trades a
        # quality loss for a correctness loss. The stale slot is dropped so the
        # next turn starts fresh cleanly instead of resuming half a history.
        # A turn whose workflow has already gone terminal must not write state
        # back. The routes do not wait for this thread: they write the terminal
        # status and purge the workflow's directory while this thread is still
        # unwinding through apply_changes, so without a gate the export
        # re-creates the directory that was just purged — and since the reaper
        # runs only at scheduler startup and only by age, the resurrected
        # transcript of a cancelled or DELETED workflow can sit there
        # indefinitely.
        #
        # The race was unreachable while the routes and the scheduler owned
        # separate per-pod directories, because the purge hit an unrelated
        # empty tree. Sharing the storage is what makes it real.
        if self._workflow_no_longer_wants_state(workflow_id, session):
            logger.info(
                "Agent state export skipped for %s: the workflow is terminal or gone",
                session_id[:8],
            )
        elif hasattr(provider, "export_agent_state"):
            # Prefer the id the stream reported; fall back to the id this turn
            # actually resumed, which is the file the CLI has been appending to.
            #
            # The fallback is DEFENSIVE, not load-bearing: reaching it needs
            # `resume=True` with a falsy `cli_session_id`, and
            # `_resolve_session_line` never returns that shape — it yields
            # `(id, None, False)` when the mapping is missing and `(id, id,
            # True)` for a tool it does not map. Kept because the alternative
            # failure is silent (the line simply stops carrying history), and
            # because stream capture is gated (claude-code and qwen-code-cli
            # only), a condition this code does not control.
            captured = getattr(session, "cli_session_id", "") or resumed_with
            try:
                blob = provider.export_agent_state(
                    sandbox_handle, cli_session_id=captured, cli_tool=cli_tool
                )
                if blob is not None:
                    self._agent_state_store.put(workflow_id, session_id, blob)
                    # #3319: persist the captured id so the NEXT milestone's
                    # `_resolve_session_line` can map the tracking id to it. The
                    # streaming tools' claude-only sidebar sync never runs for
                    # qwen, so this is an explicit, best-effort write here.
                    self._persist_carried_resume_id(session, cli_tool, captured)
                else:
                    self._agent_state_store.discard(workflow_id, session_id)
            except Exception as exc:  # noqa: BLE001 - never discard finished work
                logger.warning("Agent state export failed for %s: %s", (captured or "?")[:8], exc)
                self._agent_state_store.discard(workflow_id, session_id)

            # CONVERGENCE, not a second guard. The check above is
            # check-then-act: the route can write the terminal status and purge
            # in the window between it returning False and the `put` landing,
            # and the put would then re-create the directory the route had just
            # removed. Re-reading afterwards closes every ordering:
            #
            #   route acts before this re-read -> we see terminal and purge, so
            #     ours is the last write;
            #   route acts after this re-read  -> its own purge runs after our
            #     put, so its write is the last one.
            #
            # Either way the directory ends up gone, which is the only outcome
            # a cancelled or deleted workflow may have. The purge is idempotent
            # and best-effort, so the redundant case costs nothing.
            if self._workflow_no_longer_wants_state(workflow_id, session):
                logger.info(
                    "Agent state purged after export for %s: the workflow went terminal mid-turn",
                    session_id[:8],
                )
                self._agent_state_store.purge(workflow_id)

        # #2022 P3b: release the provider sandbox (reap any stragglers the
        # process-group signal missed + clear its _procs so a shared provider
        # instance does not leak across sessions). The reap above already killed
        # the proc; destroy is idempotent on an already-dead sandbox.
        try:
            provider.destroy(sandbox_handle)
        except Exception as e:
            logger.warning("sandbox destroy failed for %s: %s", session_id[:8], e)

        self._local_sessions.pop(session_id, None)

        # Clear PID from database
        if self._on_pid_cleared:
            try:
                self._on_pid_cleared(session_id)
            except Exception as e:
                logger.warning("on_pid_cleared callback failed: %s", e)

        if session._stderr_thread:
            session._stderr_thread.join(timeout=1)
        # Issue #2020: classify isolated-launcher exits (repo integrity,
        # resource-policy unavailable, signal-killed) into a structured code.
        if not session.error_code:
            # orchestrator_initiated: a timeout (`not completed`) or an
            # explicit stop (`session._stopped`) means WE killed it — never a
            # resource breach, so the wall-clock/stop code below is preserved.
            cls_code, cls_msg = self._classify_isolated_exit_code(
                transport.returncode,
                session.last_stderr,
                orchestrator_initiated=(not completed) or session._stopped.is_set(),
                resource_policy_configured=self._resource_policy_configured(),
            )
            if cls_code:
                session.error_code = cls_code
                if cls_code == "repo_integrity_violation":
                    session.error = "Protected .git entry changed during autonomous agent execution"
                elif cls_msg:
                    session.error = cls_msg

        if (
            completed
            and self._uses_sidebar_session_source(cli_tool, workspace_type)
            and not session.persisted_session_id
        ):
            self._resolve_sidebar_session(
                session,
                wait_timeout=min(SESSION_DETECTION_WAIT_SECONDS, max(timeout, 0)),
            )

        resolved_session_id = (
            session.persisted_session_id
            if self._uses_sidebar_session_source(cli_tool, workspace_type)
            else session_id
        )
        if self._uses_sidebar_session_source(cli_tool, workspace_type) and not resolved_session_id:
            return _build_agent_task_result(
                session_id=session_id,
                tracking_session_id=session_id,
                source_session_id="",
                event_log=session.event_log,
                fallback_text=session.assistant_text,
                total_tokens=session.total_tokens,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                request_count=session.request_count,
                tool_calls=session.tool_calls,
                success=False,
                error=self._classify_sidebar_start_failure(
                    transport.returncode,
                    session.error_code,
                    session.last_stderr,
                ),
                error_code=session.error_code,
            )

        if not completed:
            # Timeout: the claude subprocess may have done real work (and
            # consumed tokens) without ever emitting the closing `result` event,
            # leaving session.total_tokens==0 / request_count==0. Recover the
            # real usage by replaying the session JSONL for records written
            # after this call started, so the milestone/session don't record a
            # zero-cost round (issue #723: dev timed out with 0/0 but actually
            # produced a 3721-line commit costing ~370K tokens).
            if (
                session.total_tokens == 0
                and resolved_session_id
                and self._uses_sidebar_session_source(cli_tool, workspace_type)
            ):
                self._replay_usage_from_jsonl(session, resolved_session_id)
            return self._stamp_sandbox_attribution(
                _build_agent_task_result(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    source_session_id=resolved_session_id,
                    event_log=session.event_log,
                    fallback_text=session.assistant_text,
                    total_tokens=session.total_tokens,
                    total_input_tokens=session.total_input_tokens,
                    total_output_tokens=session.total_output_tokens,
                    request_count=session.request_count,
                    tool_calls=session.tool_calls,
                    success=False,
                    error=f"Agent task timed out after {timeout}s",
                    error_code=session.error_code
                    or AutonomousAgentRunner.TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE,
                ),
                sandbox_handle,
                provider,
            )

        # #2640: route through the recovery-aware finalizer so a run whose
        # assistant stream events were dropped (large-context --resume) still
        # yields its deliverable as response_text and session persistence.
        return self._finalize_local_completed_result(session, session_id, resolved_session_id)

    def _create_workflow_session(
        self,
        sid: str,
        workflow_id: str,
        cli_tool: str,
        user_id: int | None,
        project_path: str,
        workspace_type: str,
    ) -> None:
        """Create the agent_sessions wrapper row for an autonomous workflow run.

        Centralizes the kwargs so the success, error, and pre-dispatch paths
        can't silently diverge. create_session is idempotent.
        """
        if not self.session_manager or not sid:
            return
        # Resolve tenant_id (default 1) so fail-closed tenant resolution passes.
        tenant_id = 1
        if user_id:
            try:
                from app.repositories.user_repo import UserRepository

                user = UserRepository().get_user_by_id(user_id)
                if user and user.get("tenant_id"):
                    tenant_id = int(user["tenant_id"])
            except Exception:
                pass  # default tenant
        try:
            self.session_manager.create_session(
                session_id=sid,
                session_type="workflow",
                title=f"Autonomous: {workflow_id[:8]}",
                tool_name=cli_tool,
                user_id=user_id,
                tenant_id=tenant_id,
                project_path=project_path,
                workspace_type=workspace_type,
                context={"workflow_id": workflow_id},
            )
        except Exception as e:
            logger.warning("Failed to create workflow session record: %s", e)

    def _run_zcode_appserver(
        self,
        session_id: str,
        cli_tool: str,
        model: str,
        project_path: str,
        prompt: str,
        permission_mode: str,
        timeout: int,
        workflow_id: str,
        user_id: int | None,
        workspace_type: str,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
        resume_session_id: str = None,
        milestone_id: str = "",
        system_account: str | None = None,
        runtime_python_command: list[str] | None = None,
        tenant_id: int | None = None,
    ) -> AgentTaskResult:
        """Run a ZCode agent task via the persistent app-server protocol.

        ZCode speaks the *ZCode Protocol* (``{id, method, params}``), not
        Claude's stream-json. We reuse ``ZCodeAppServerSession`` — the same
        class the interactive executor uses — to drive ``session/create`` →
        ``session/send`` → ``session/events`` and collect results.
        """
        import sys

        _remote_agent_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "remote-agent")
        )
        if _remote_agent_dir not in sys.path:
            sys.path.insert(0, _remote_agent_dir)
        from cli_adapters import get_adapter
        from zcode_app_server import ZCodeAppServerSession

        _ensure_usage_parser()
        adapter = get_adapter(cli_tool)

        env = (
            self._build_agent_env(
                adapter,
                cli_tool,
                user_id,
                session_id,
                model,
                runtime_python_command,
                task_id=session_id,
            )
            if runtime_python_command
            else self._build_agent_env(
                adapter, cli_tool, user_id, session_id, model, task_id=session_id
            )
        )
        # Resolve the ZCode session mode. The --mode CLI flag is ignored by
        # app-server (verified: sessions always start in "build" mode regardless
        # of the flag). The mode is set via session/setMode protocol call in
        # ZCodeAppServerSession.start() after session/create.
        #
        # Planning/review phases pass permission_mode="plan" (from
        # _zcode_planning_mode helper) → read-only.
        # Dev/test phases pass "auto-edit" → yolo (fully autonomous).
        # build/edit modes stall on tool-approval-request — never use them.
        zcode_mode = permission_mode if permission_mode in ("plan", "yolo") else "yolo"
        cmd = adapter.build_start_args(
            resume_session_id if (resume and resume_session_id) else session_id,
            project_path,
            model,
            permission_mode=zcode_mode,
            resume=resume,
        )
        # Cross-user launch via run-as wrapper (zcode needs cwd=project too).
        cmd, cwd = (
            self._wrap_agent_cmd(cmd, project_path, system_account, env, task_id=session_id)
            if self._is_cross_user(system_account)
            else self._wrap_agent_cmd(cmd, project_path, system_account, task_id=session_id)
        )

        # #3323: pick the isolation provider. A production-isolation tenant gets
        # OpenSandbox and ZCode runs INSIDE the pod, driving the app-server over
        # the PTY transport wrapped in a Popen-shaped adapter. Every other tenant
        # gets the local Legacy fallback and keeps the direct-Popen path below,
        # byte-for-byte unchanged.
        if tenant_id is None:
            try:
                tenant_id = self._resolve_tenant_for_isolation(
                    user_id, cli_tool=cli_tool, adapter=adapter
                )
            except SandboxError as e:
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=f"Sandbox unavailable: {e}",
                    error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
                )
        try:
            provider = self._select_sandbox_provider(
                # Hardcoded "local", matching the stream-json path:
                # _run_zcode_appserver is only reached via _run_local
                # (run_agent_task routes remote workspaces to _run_remote), so
                # execution is always local — passing workspace_type would pick a
                # RemoteMachineProvider on the remote-without-machine-id edge case.
                "local",
                tenant_id=tenant_id,
                project_path=project_path,
                generation=self._resolve_sandbox_generation(workflow_id),
            )
        except SandboxError as e:
            # Mirror the tenant-resolution and stream-json handlers: a selection
            # failure for a production-required tenant (unhealthy backend) must
            # keep its sandbox_unavailable classification, not fall through to
            # run_agent_task's generic except which drops the error_code.
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Sandbox unavailable: {e}",
                error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
            )
        from app.modules.workspace.autonomous.sandbox.provider import (
            AGENT_STATE_CARRIED,
            AGENT_STATE_EPHEMERAL,
            agent_state_persistence,
        )

        use_sandbox = agent_state_persistence(provider) in (
            AGENT_STATE_CARRIED,
            AGENT_STATE_EPHEMERAL,
        )

        sandbox_handle = None
        exec_handle = None
        transport = None
        if use_sandbox:
            # ZCode's own agent-state carry is deferred (#3323). A resuming turn
            # in a fresh pod would resolve `--resume` into an empty HOME and cold
            # start silently, so refuse before creating the pod — it costs
            # nothing and a stated refusal is recoverable where the cold start is
            # not. Lifting this needs ZCode's transcript path + session-id capture
            # established the way #3319 did for qwen.
            if resume:
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=(
                        "ZCode agent-state carry is not implemented yet (#3323): refusing "
                        "to resume into an empty sandbox HOME rather than start cold silently"
                    ),
                    error_code="agent_state_unavailable",
                )
            try:
                sandbox_handle = provider.create(
                    SandboxSpec(
                        task_id=session_id,
                        project_path=project_path,
                        cli_tool=cli_tool,
                        system_account=system_account,
                        policy=self._load_task_policy(),
                    )
                )
                self._notify_sandbox_created(session_id, sandbox_handle, None, provider)
            except SandboxError as e:
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=f"Sandbox unavailable: {e}",
                    error_code=getattr(e, "reason_code", "") or "sandbox_unavailable",
                )
            logger.info(
                "Launching ZCode app-server (mode=%s) in sandbox %s",
                zcode_mode,
                sandbox_handle.sandbox_id,
            )
            try:
                # Materialize the project tree in the pod before the agent starts,
                # then run the app-server over the PTY transport (#2023 seam).
                provider.upload_workspace(sandbox_handle, None)
                exec_policy = provider.agent_turn_policy(prompt=prompt, model=model, env=env)
                exec_handle = provider.exec(
                    sandbox_handle, command=cmd, env=env, exec_policy=exec_policy
                )
                transport = provider.get_transport(exec_handle)
                # LocalProcessTransport exposes a real Popen; a container backend
                # leaves it None, so wrap the transport in the Popen-shaped adapter.
                process = getattr(transport, "process", None)
                if process is None:
                    from app.modules.workspace.autonomous.sandbox.opensandbox.popen_adapter import (
                        TransportPopenAdapter,
                    )

                    process = TransportPopenAdapter(transport)
            except Exception as e:  # noqa: BLE001 - the sandbox EXISTS; nothing may leak it
                try:
                    provider.destroy(sandbox_handle)
                except Exception as destroy_error:  # noqa: BLE001 - must not mask `e`
                    logger.warning(
                        "sandbox destroy failed while handling %s: %s",
                        type(e).__name__,
                        destroy_error,
                    )
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=f"Failed to start ZCode process: {e}",
                )
        else:
            logger.info("Launching ZCode app-server (mode=%s): %s", zcode_mode, " ".join(cmd))
            try:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                )
            except (OSError, subprocess.SubprocessError) as e:
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=f"Failed to start ZCode process: {e}",
                )

        # Register PID + wrap into a _LocalSession-compatible tracker so the
        # orchestrator's stop/pause/cancel can reach the process. On the sandbox
        # path the tracker carries transport/provider/exec_handle and NO local
        # process, so stop_session/pause_session/resume_session route through the
        # provider and never call os.getpgid(None). Created before the collector
        # so the collector's session_id_resolver can read the real CLI session id
        # off the tracker once session/create resolves.
        tracker = _LocalSession(
            session_id=session_id,
            process=None if use_sandbox else process,
            transport=transport,
            cli_tool=cli_tool,
            project_path=project_path,
            encoded_project_path=self._encode_project_path(project_path),
            workflow_id=workflow_id,
            user_id=user_id,
            workspace_type=workspace_type,
            started_at_epoch=time.time(),
            milestone_id=milestone_id,
            task_id=session_id,
            sandbox_handle=sandbox_handle,
            exec_handle=exec_handle,
            sandbox_provider=provider if use_sandbox else None,
        )

        collector = _ZcodeResultCollector(
            activity_callback=self._activity_callback,
            session_id_resolver=lambda: tracker.persisted_session_id or tracker.session_id,
        )

        zc_session = ZCodeAppServerSession(
            session_id=session_id,
            process=process,
            project_path=project_path,
            output_callback=collector.on_output,
            usage_callback=collector.on_usage,
            permission_callback=lambda sid, req: collector.on_permission(sid, req),
            model=model,
            permission_mode=zcode_mode,
            env=env,
        )
        zc_session.allowed_tools = []

        self._local_sessions[session_id] = tracker
        # A pidless sandbox backend (process.pid is None) registers nothing —
        # writing a NULL pid row would be meaningless; cancellation routes
        # through the provider instead.
        if self._on_pid_registered and process.pid is not None:
            try:
                self._on_pid_registered(session_id, process.pid)
            except Exception as e:
                logger.warning("on_pid_registered callback failed: %s", e)

        # Start stderr reader so app-server diagnostics surface as errors.
        stderr_thread = threading.Thread(
            target=self._read_zcode_stderr_local,
            args=(session_id, process, collector),
            name=f"zcode-err-{session_id[:8]}",
            daemon=True,
        )
        stderr_thread.start()

        try:
            if not zc_session.start(
                model=model,
                permission_mode=permission_mode,
                resume_session_id=resume_session_id if resume else None,
            ):
                raise ZCodeSessionError("ZCode session/create failed")

            tracker.cli_session_id = zc_session._cli_session_id
            tracker.persisted_session_id = zc_session._cli_session_id
            tracker.sdk_initialized.set()

            # Emit session_resolved so the orchestrator links the real CLI
            # session id to the in-progress milestone. The frontend matches
            # live activity to a milestone by milestone.session_id, which this
            # populates. Without it the activity panel stays empty even though
            # events are flowing (#1194).
            if self._activity_callback and zc_session._cli_session_id:
                self._activity_callback(
                    zc_session._cli_session_id,
                    {"type": "session_resolved"},
                )

            # Create the wrapper agent_sessions row under the REAL CLI session
            # id (not the uuid). run_agent_task skips the pre-create for
            # app-server tools because the CLI id is only known after
            # session/create. Keying the row here by cli_sid makes add_message's
            # session-exists check pass during _persist_local_session_messages
            # and keeps milestone/session_messages keys aligned — mirroring
            # Claude's _ensure_sidebar_session. create_session is idempotent.
            self._create_workflow_session(
                zc_session._cli_session_id,
                workflow_id,
                cli_tool,
                user_id,
                project_path,
                workspace_type,
            )

            if not zc_session.send_message(prompt, timeout=timeout):
                raise ZCodeSessionError(zc_session.last_send_error or "ZCode session/send failed")

            completed = zc_session.wait_turn(timeout=timeout)
        except Exception as e:
            # Catch ALL exceptions (not just ZCodeSessionError) so that any
            # failure after session/create — including unexpected errors in
            # send_message/wait_turn — takes this single error path and returns
            # from here. This prevents a non-ZCodeSessionError from escaping to
            # run_agent_task's outer except, which would create a SECOND row
            # under the uuid (duplicate of the CLI-id row created above). The
            # row-creation under err_sid below is idempotent, so if the CLI-id
            # row already exists this is a harmless no-op.
            logger.warning("ZCode app-server task failed: %s", e)
            err_sid = zc_session._cli_session_id or session_id
            self._create_workflow_session(
                err_sid,
                workflow_id,
                cli_tool,
                user_id,
                project_path,
                workspace_type,
            )
            return AgentTaskResult(
                session_id=err_sid,
                tracking_session_id=session_id,
                success=False,
                error=str(e),
            )
        finally:
            # Always clean up: stop the process, remove the tracker, clear PID.
            zc_session.stop()
            # #3323: on the sandbox path, bring the agent's work product back out
            # BEFORE destroy — after destroy the ephemeral filesystem is gone with
            # the run's entire output. apply then destroy; Legacy no-ops both. An
            # apply failure invalidates the run (recorded on the collector so the
            # success-path result reports it).
            if use_sandbox and sandbox_handle is not None:
                try:
                    provider.apply_changes(sandbox_handle, project_path)
                except Exception as apply_error:  # noqa: BLE001 - any failure invalidates the run
                    logger.error(
                        "sandbox changeset apply failed for %s: %s", session_id[:8], apply_error
                    )
                    if collector.error is None:
                        collector.error = (
                            f"Sandbox work product could not be applied: {apply_error}"
                        )
                try:
                    provider.destroy(sandbox_handle)
                except Exception as destroy_error:  # noqa: BLE001 - idempotent, best effort
                    logger.warning(
                        "sandbox destroy failed for %s: %s", session_id[:8], destroy_error
                    )
            self._local_sessions.pop(session_id, None)
            if self._on_pid_cleared:
                try:
                    self._on_pid_cleared(session_id)
                except Exception as e:
                    logger.warning("on_pid_cleared callback failed: %s", e)

        cli_sid = zc_session._cli_session_id or session_id
        if not completed:
            return _build_agent_task_result(
                session_id=cli_sid,
                tracking_session_id=session_id,
                event_log=collector.event_log,
                fallback_text=collector.assistant_text,
                total_tokens=collector.total_tokens,
                total_input_tokens=collector.input_tokens,
                total_output_tokens=collector.output_tokens,
                request_count=collector.request_count,
                tool_calls=collector.tool_calls,
                success=False,
                error=f"Agent task timed out after {timeout}s",
                error_code=AutonomousAgentRunner.TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE,
            )

        return _build_agent_task_result(
            session_id=cli_sid,
            tracking_session_id=session_id,
            event_log=collector.event_log,
            fallback_text=collector.assistant_text,
            total_tokens=collector.total_tokens,
            total_input_tokens=collector.input_tokens,
            total_output_tokens=collector.output_tokens,
            request_count=collector.request_count,
            tool_calls=collector.tool_calls,
            success=collector.error is None,
            error=collector.error,
        )

    @staticmethod
    def _parse_single_shot_line(parsed: dict, cli_tool: str) -> dict | None:
        """Normalize one parsed JSON stdout line into a typed event-log dict.

        Single-shot tools (codex ``exec --json``, openclaw ``--agent --json``)
        emit line-delimited JSON with per-tool shapes. This coalesces the common
        shapes into the same ``{"type": ...}`` dict contract used by the
        interactive path (see ``_read_stdout``) so that
        ``_extract_visible_response_text`` / ``_persist_local_session_messages``
        — which assume dict entries with ``type``/``text`` keys — work correctly.

        Returns ``None`` when the line is not a recognizable assistant/tool event
        (the caller then falls back to best-effort text extraction).
        """
        if not isinstance(parsed, dict):
            return None
        msg_type = parsed.get("type", "")

        # Claude stream-json shape: {"type":"assistant","message":{"content":...}}
        if msg_type == "assistant":
            msg = parsed.get("message", {}) or {}
            text = _extract_visible_text(msg.get("content", ""))
            if text:
                return {
                    "type": "assistant",
                    "text": text,
                    "message_id": msg.get("id"),
                    "model": msg.get("model"),
                }
            return None

        # Claude stream-json shape: {"type":"tool_use","tool":{"name":...,"input":...}}
        if msg_type == "tool_use":
            tool_info = parsed.get("tool", {}) or parsed
            return {
                "type": "tool_use",
                "tool_name": tool_info.get("name", "unknown"),
                "tool_input": tool_info.get("input", {}),
                "tool_use_id": tool_info.get("id"),
            }

        if msg_type == "user":
            content = (parsed.get("message", {}) or {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        raw_content = block.get("content", "")
                        return {
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id"),
                            "text": str(raw_content or ""),
                            "exit_code": block.get("exit_code"),
                            "is_error": bool(block.get("is_error", False)),
                        }
            return None

        # Codex exec --json lifecycle.  command_execution items provide a
        # stable item id in both started and completed events, plus an explicit
        # process exit code on completion.
        if msg_type in ("item.started", "item.completed"):
            item = parsed.get("item", {}) or {}
            if item.get("type") == "command_execution":
                item_id = item.get("id")
                if msg_type == "item.started":
                    return {
                        "type": "tool_use",
                        "tool_name": "Bash",
                        "tool_input": {"command": item.get("command", "")},
                        "tool_use_id": item_id,
                    }
                exit_code = item.get("exit_code")
                return {
                    "type": "tool_result",
                    "tool_use_id": item_id,
                    "text": str(item.get("aggregated_output") or item.get("output") or ""),
                    "exit_code": exit_code,
                    "is_error": isinstance(exit_code, int) and exit_code != 0,
                }
            if item.get("type") == "agent_message" and msg_type == "item.completed":
                text = str(item.get("text") or "")
                if text:
                    return {"type": "assistant", "text": text, "message_id": item.get("id")}
            return None

        # Codex/OpenAI shape: {"type":"message","role":"assistant","content":[{"type":"output_text","text":...}]}
        if msg_type == "message" and parsed.get("role") == "assistant":
            text = _extract_visible_text(parsed.get("content", ""))
            if text:
                return {"type": "assistant", "text": text, "message_id": None, "model": None}
            return None

        # Codex/OpenAI shape: {"type":"function_call","name":...,"arguments":...}
        if msg_type in ("function_call", "custom_tool_call"):
            name = parsed.get("name", "")
            if name:
                return {
                    "type": "tool_use",
                    "tool_name": name,
                    "tool_input": parsed.get("arguments", parsed.get("input", {})),
                    "tool_use_id": parsed.get("call_id"),
                }
            return None

        if msg_type in ("function_call_output", "custom_tool_call_output"):
            output = parsed.get("output", "")
            exit_code = parsed.get("exit_code")
            return {
                "type": "tool_result",
                "tool_use_id": parsed.get("call_id"),
                "text": output if isinstance(output, str) else str(output),
                "exit_code": exit_code,
                "is_error": bool(parsed.get("is_error", False))
                or (isinstance(exit_code, int) and exit_code != 0),
            }

        return None

    def _parse_single_shot_stdout(
        self, stdout: str, cli_tool: str
    ) -> tuple[list, str, int, int, list]:
        """Parse single-shot stdout into typed event log + text + tokens + tools.

        Returns ``(event_log, response_text, input_tokens, output_tokens,
        tool_calls)`` where ``event_log`` contains dict entries (matching the
        interactive-path contract) instead of raw strings, so that downstream
        extractors and persistence behave identically for single-shot runs.
        """
        event_log: list[dict] = []
        response_text = ""
        input_tokens = 0
        output_tokens = 0
        tool_calls: list[dict] = []

        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                # Non-JSON line (e.g. progress noise): keep as raw text only.
                response_text += line + "\n"
                continue

            if not isinstance(parsed, dict):
                continue

            event = self._parse_single_shot_line(parsed, cli_tool)
            if event:
                event_log.append(event)
                if event["type"] == "assistant" and event.get("text"):
                    response_text += event["text"] + "\n"
                elif event["type"] == "tool_use":
                    tool_calls.append(
                        {"tool": {"name": event["tool_name"], "input": event["tool_input"]}}
                    )
            else:
                # Unrecognized JSON event: best-effort text extraction, plus
                # usage parsing (single-shot result/usage lines often land here).
                text = (
                    parsed.get("response")
                    or parsed.get("text")
                    or parsed.get("content")
                    or parsed.get("output")
                )
                if isinstance(text, str):
                    response_text += text + "\n"

            if _extract_stream_usage is not None:
                usage = _extract_stream_usage(cli_tool, parsed)
                if usage:
                    input_tokens += usage["input"]
                    output_tokens += usage["output"]

        return event_log, response_text.strip(), input_tokens, output_tokens, tool_calls

    def _persist_carried_resume_id(
        self, session: _LocalSession, cli_tool: str, cli_session_id: str
    ) -> None:
        """Write a carried tool's stream session id to ``cli_session_id`` (#3319).

        For claude-code the sidebar sync already persists this during streaming;
        qwen-code-cli's does not (it is gated claude-only), so its export-time id
        needs an explicit write here so the next milestone's
        ``_resolve_session_line`` can map the tracking id to it. Best-effort — a
        DB error degrades the next turn to a cold start, never fails this one.
        """
        if cli_tool != "qwen-code-cli" or not cli_session_id:
            return
        session_manager = getattr(self, "session_manager", None)
        if not session_manager:
            return
        try:
            session_manager.update_session_fields(
                session.session_id, {"cli_session_id": cli_session_id}, require_tenant=False
            )
        except Exception as exc:  # noqa: BLE001 - never fail a finished milestone
            logger.warning(
                "Failed to persist carried resume id for %s: %s",
                (session.session_id or "")[:8],
                exc,
            )

    @staticmethod
    def _extract_thread_started_id(stdout_text: str) -> str:
        """Return codex's minted thread_id from the first ``thread.started`` event.

        ``codex exec --json`` emits ``{"type":"thread.started","thread_id":...}``
        as its first event; the id names the rollout file and is what
        ``codex exec resume <id>`` accepts (#3321). Empty string when absent
        (e.g. openclaw, which emits no such event).
        """
        for raw in (stdout_text or "").splitlines():
            line = raw.strip()
            if not line or '"thread.started"' not in line:
                continue
            try:
                parsed = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "thread.started":
                thread_id = str(parsed.get("thread_id") or "").strip()
                if thread_id:
                    return thread_id
        return ""

    def _persist_single_shot_resume_id(self, session_id: str, stdout_text: str) -> None:
        """Write the CLI-minted resume id to ``agent_sessions.cli_session_id`` (#3321).

        Explicit, additive write — the single-shot path never runs the
        claude-only sidebar sync that populates the column for streaming tools.
        Best-effort: a DB hiccup degrades the next milestone to a cold start,
        it never fails the completed milestone. Writes only when an id was
        found, so a no-``thread.started`` run (openclaw) records nothing.
        """
        session_manager = getattr(self, "session_manager", None)
        if not session_manager:
            return
        thread_id = self._extract_thread_started_id(stdout_text)
        if not thread_id:
            return
        try:
            session_manager.update_session_fields(
                session_id, {"cli_session_id": thread_id}, require_tenant=False
            )
        except Exception as exc:  # noqa: BLE001 - never fail a finished milestone
            logger.warning(
                "Failed to persist single-shot resume id for %s: %s", session_id[:8], exc
            )

    def _run_single_shot(
        self,
        session_id: str,
        cli_tool: str,
        model: str,
        project_path: str,
        prompt: str,
        timeout: int,
        workflow_id: str,
        milestone_id: str = "",
        system_account: str | None = None,
        user_id: int | None = None,
        runtime_python_command: list[str] | None = None,
        resume: bool = False,
        resume_session_id: str = "",
    ) -> AgentTaskResult:
        """Run a CLI tool in single-shot mode for tools without stdin protocol.

        Uses ``adapter.build_single_shot_args`` to produce a self-contained
        command (e.g. ``codex exec --json "<prompt>"``) and captures output.

        ``resume`` / ``resume_session_id`` (#3321) let codex continue a prior
        milestone via ``codex exec resume <id>``; the adapter ignores them for
        tools that cannot resume non-interactively (openclaw).
        """
        import sys

        _remote_agent_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "remote-agent")
        )
        if _remote_agent_dir not in sys.path:
            sys.path.insert(0, _remote_agent_dir)
        from cli_adapters import get_adapter

        _ensure_usage_parser()
        adapter = get_adapter(cli_tool)

        exe_name = adapter.get_executable_name()
        executable = shutil.which(exe_name)
        if not executable:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"CLI tool '{exe_name}' not found",
            )

        args = adapter.build_single_shot_args(
            prompt, project_path, model, resume=resume, resume_session_id=resume_session_id
        )
        cmd = [executable] + (args[1:] if len(args) > 1 and args[0] == exe_name else args)
        env = (
            self._build_agent_env(
                adapter,
                cli_tool,
                user_id,
                session_id,
                model,
                runtime_python_command,
                task_id=session_id,
            )
            if runtime_python_command
            else self._build_agent_env(
                adapter, cli_tool, user_id, session_id, model, task_id=session_id
            )
        )

        # Cross-user launch via run-as wrapper (single-shot CLIs also need
        # cwd=project; openclaw documents it relies on the caller's cwd).
        cmd, cwd = (
            self._wrap_agent_cmd(cmd, project_path, system_account, env, task_id=session_id)
            if self._is_cross_user(system_account)
            else self._wrap_agent_cmd(cmd, project_path, system_account, task_id=session_id)
        )

        logger.info("Launching single-shot agent (%s): %s", cli_tool, " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as te:
            # Salvage partial output: the agent may have emitted assistant text
            # and tool calls before the wall-clock timeout fired. ``run`` populates
            # ``TimeoutExpired.output`` with whatever stdout was captured. Parse
            # it so the result carries real text/events rather than an empty
            # shell (an empty visible_response_text would, e.g., make the
            # orchestrator's test-skip detector false-positive). NB: on POSIX
            # the captured output is bytes even when ``text=True`` was passed
            # (the decode step only runs on the normal return path, not when
            # the exception is re-raised mid-read), so decode it explicitly.
            partial_out = te.output
            if isinstance(partial_out, bytes):
                partial_out = partial_out.decode("utf-8", "replace")
            elif not isinstance(partial_out, str):
                partial_out = ""
            event_log, response_text, input_tokens, output_tokens, tool_calls = (
                self._parse_single_shot_stdout(partial_out, cli_tool)
            )
            logger.warning(
                "Single-shot agent (%s) timed out after %ds; salvaged %d events",
                cli_tool,
                timeout,
                len(event_log),
            )
            return _build_agent_task_result(
                session_id=session_id,
                tracking_session_id=session_id,
                event_log=event_log,
                fallback_text=response_text,
                total_tokens=input_tokens + output_tokens,
                total_input_tokens=input_tokens,
                total_output_tokens=output_tokens,
                request_count=1 if event_log else 0,
                tool_calls=tool_calls,
                success=False,
                error=f"Agent task timed out after {timeout}s",
                error_code=AutonomousAgentRunner.TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Failed to run command: {e}",
            )

        event_log, response_text, input_tokens, output_tokens, tool_calls = (
            self._parse_single_shot_stdout(proc.stdout or "", cli_tool)
        )
        total_tokens = input_tokens + output_tokens

        # #3321: persist the CLI-minted resume id so the next milestone can
        # `--resume` it. Explicit write here — the single-shot path never enters
        # the claude-only sidebar sync that populates cli_session_id for the
        # streaming tools.
        self._persist_single_shot_resume_id(session_id, proc.stdout or "")

        stderr_text = (proc.stderr or "").strip()
        success = proc.returncode == 0
        error = None
        error_code = None
        if not success:
            error = stderr_text or f"Command exited with code {proc.returncode}"
            # Issue #2020: structured classification of isolated-launcher exits.
            # Single-shot runs synchronously; its timeout is handled by the
            # subprocess.run(timeout=...) branch above, so a signal death here
            # was not orchestrator-initiated.
            cls_code, cls_msg = self._classify_isolated_exit_code(
                proc.returncode,
                stderr_text,
                resource_policy_configured=self._resource_policy_configured(),
            )
            if cls_code:
                error_code = cls_code
                if cls_code == "repo_integrity_violation":
                    error = "Protected .git entry changed during autonomous agent execution"
                elif cls_msg:
                    error = cls_msg

        return _build_agent_task_result(
            session_id=session_id,
            tracking_session_id=session_id,
            event_log=event_log,
            fallback_text=response_text,
            total_tokens=total_tokens,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            request_count=1,
            tool_calls=tool_calls,
            success=success,
            error=error,
            error_code=error_code,
        )

    @staticmethod
    def _read_zcode_stderr_local(
        session_id: str, process: subprocess.Popen, collector: _ZcodeResultCollector
    ) -> None:
        """Forward ZCode app-server stderr lines as errors (autonomous variant)."""
        stream = process.stderr
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                text = line.strip()
                if text:
                    logger.warning("[zcode %s stderr] %s", session_id[:8], text)
                    if collector.error is None:
                        collector.error = text
        except (OSError, ValueError):
            pass

    @staticmethod
    def _normalize_remote_messages(messages: list) -> tuple[list[dict], list[dict]]:
        """Convert persisted remote messages to the local event-log contract.

        ``SessionManager.get_messages`` returns ``SessionMessage`` instances,
        while older test doubles and remote implementations may return dicts.
        Preserve structured tool calls/results from either representation so
        remote test phases are held to the same evidence requirements as local
        phases.
        """
        events: list[dict] = []
        tool_calls: list[dict] = []

        def _as_dict(message) -> dict:
            if isinstance(message, dict):
                return message
            to_dict = getattr(message, "to_dict", None)
            if callable(to_dict):
                value = to_dict()
                return value if isinstance(value, dict) else {}
            return {
                key: getattr(message, key)
                for key in ("role", "content", "content_blocks", "metadata")
                if hasattr(message, key)
            }

        def _result_text(value) -> str:
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = []
                for item in value:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    else:
                        parts.append(str(item))
                return "\n".join(part for part in parts if part)
            if value is None:
                return ""
            return str(value)

        for raw_message in messages or []:
            message = _as_dict(raw_message)
            role = str(message.get("role") or "")
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            blocks = message.get("content_blocks") or metadata.get("content_blocks") or []
            if not isinstance(blocks, list):
                blocks = []

            visible_block_text = False
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in ("text", "output_text") and role == "assistant":
                    text = str(block.get("text") or "")
                    if text:
                        visible_block_text = True
                        events.append({"type": "assistant", "text": text})
                elif block_type in ("tool_use", "function_call"):
                    tool_name = str(block.get("name") or block.get("tool_name") or "unknown")
                    tool_input = block.get("input", block.get("arguments", {}))
                    tool_id = block.get("id") or block.get("tool_use_id") or block.get("call_id")
                    tool_calls.append(
                        {"tool": {"name": tool_name, "input": tool_input, "id": tool_id}}
                    )
                    events.append(
                        {
                            "type": "tool_use",
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "tool_use_id": tool_id,
                        }
                    )
                elif block_type in ("tool_result", "function_call_output"):
                    exit_code = block.get("exit_code", block.get("exitCode"))
                    events.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id")
                            or block.get("call_id")
                            or block.get("id"),
                            "text": _result_text(
                                block.get("content", block.get("output", block.get("text", "")))
                            ),
                            "exit_code": exit_code,
                            "is_error": bool(block.get("is_error", False))
                            or (isinstance(exit_code, int) and exit_code != 0),
                        }
                    )

            content = message.get("content", "")
            if role == "assistant" and not visible_block_text:
                text = _extract_visible_text(content)
                if text:
                    events.append({"type": "assistant", "text": text})
            elif role in ("tool", "tool_result", "toolResult") and not any(
                isinstance(block, dict)
                and block.get("type") in ("tool_result", "function_call_output")
                for block in blocks
            ):
                exit_code = message.get("exit_code", metadata.get("exit_code"))
                events.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_use_id")
                        or message.get("tool_call_id")
                        or metadata.get("tool_use_id")
                        or metadata.get("tool_call_id")
                        or message.get("external_message_id"),
                        "text": _result_text(content),
                        "exit_code": exit_code,
                        "is_error": bool(message.get("is_error", metadata.get("is_error", False)))
                        or (isinstance(exit_code, int) and exit_code != 0),
                    }
                )

        return events, tool_calls

    def _resolve_tenant_id_strict(self, user_id: int | None) -> int:
        """Resolve the tenant, raising rather than guessing (#2023).

        Single source: the sandbox isolation tier and the session row must agree
        on which tenant a run belongs to, and a second derivation path would
        drift from the first — so this is the only lookup, and
        :meth:`_resolve_tenant_id` is a wrapper over it.

        A lookup FAILURE is not the same as "tenant 1".
        ``production_required_tenants`` is keyed by tenant, so answering 1 for a
        tenant whose policy demands production isolation silently downgrades
        exactly the runs the policy exists to protect — and a DB blip is when
        that is most likely to happen. Callers deciding *isolation* use this and
        fail closed; callers doing *bookkeeping* use the lenient wrapper,
        because a usage-sync row is not worth failing a run over.
        """
        if not user_id:
            return 1
        try:
            from app.repositories.user_repo import UserRepository

            user = UserRepository().get_user_by_id(user_id)
        except Exception as e:
            raise SandboxError(
                f"cannot resolve tenant for user {user_id}: {e}; refusing to assume "
                "the default tenant because its isolation policy may be weaker"
            ) from e
        if user and user.get("tenant_id"):
            try:
                return int(user["tenant_id"])
            except (TypeError, ValueError) as e:
                raise SandboxError(
                    f"user {user_id} has a non-integer tenant_id {user['tenant_id']!r}"
                ) from e
        # No row, or a row with no tenant: a real, readable answer, not an error.
        return 1

    def _resolve_tenant_id(self, user_id: int | None) -> int:
        """Lenient tenant resolution for session/usage bookkeeping only.

        NOT for the isolation decision — see :meth:`_resolve_tenant_id_strict`.
        """
        try:
            return self._resolve_tenant_id_strict(user_id)
        except SandboxError as e:
            logger.warning(
                "tenant lookup failed for user %s, using 1 for bookkeeping: %s", user_id, e
            )
            return 1

    def _resolve_tenant_for_isolation(
        self,
        user_id: int | None,
        *,
        cli_tool: str,
        adapter: Any,
    ) -> int:
        """Resolve the tenant and refuse a path that cannot honour its policy (#2023).

        ``_select_sandbox_provider`` is the gate, but two paths in ``_run_local``
        return before reaching it: ZCode speaks its own app-server protocol, and
        an adapter without stdin goes through ``_run_single_shot``. Both spawn a
        local process directly, which for a tenant in
        ``production_required_tenants`` is exactly the silent fallback
        acceptance criterion 12 forbids. It becomes a refusal here instead —
        naming the tool, because "sandbox unavailable" with no cause sends an
        operator to the cluster to look for a fault that is not there.

        Strictness is scoped rather than global. When no tenant has a
        production-isolation policy, a guessed tenant id cannot downgrade
        anyone, so a tenant lookup that fails stays lenient — failing every
        local run because the users table hiccuped would be a far larger
        outage than the one this guards against.
        """
        from app.modules.workspace.autonomous.sandbox.isolation_tier import (
            requires_production_isolation,
        )

        config = self._load_backend_config()
        if config is None or not config.production_required_tenants:
            return self._resolve_tenant_id(user_id)

        tenant_id = self._resolve_tenant_id_strict(user_id)
        if not requires_production_isolation(tenant_id, config):
            return tenant_id
        # #3323: ZCode (an _APPSERVER_TOOLS member) now runs INSIDE the sandbox
        # over the PTY transport — `_run_zcode_appserver` selects the provider —
        # so it is allowed through here. Only the single-shot tools (codex,
        # openclaw), which spawn a local process and have no sandbox path, are
        # still refused: their execution really would bypass the provider.
        if cli_tool in _APPSERVER_TOOLS:
            return tenant_id
        if not adapter.supports_stdin_input():
            raise SandboxError(
                f"tenant {tenant_id} requires production isolation, but {cli_tool!r} "
                "has no stdin protocol and runs single-shot, so its execution path "
                "bypasses the sandbox provider; choose a CLI tool that supports the "
                "stream-json protocol (claude-code, qwen-code-cli) or remove the tenant "
                "from production_required_tenants"
            )
        return tenant_id

    def _select_sandbox_provider(
        self,
        workspace_type: str,
        *,
        tenant_id: int | None = None,
        project_path: str | None = None,
        generation: int = 1,
    ) -> Any:
        """Pick the SandboxProvider for a task (#2022 P4 ③).

        Centralizes backend selection so adding gVisor (#2023) is one branch
        here, not a new dispatch fork in ``run_agent_task``. Local → the
        injected LegacyPosixProvider; remote → a RemoteMachineProvider wrapping
        ``remote_session_manager``. The runner no longer decides isolation
        mechanics inline — it asks the provider.
        """
        if workspace_type == "remote":
            # Remote never routes through the local isolation gate: doing so
            # could hand a remote workspace an OpenSandboxProvider.
            if self.remote_session_manager is not None:
                return RemoteMachineProvider(self.remote_session_manager)
            return self._sandbox_provider
        # #2023: local runs route through the isolation gate. A tenant listed in
        # production_required_tenants gets OpenSandbox or an exception — there is
        # no path from "required" to Legacy. With no backend configured the gate
        # returns the injected provider unchanged, so behaviour and constructor
        # injection are both preserved.
        from app.modules.workspace.autonomous.sandbox.isolation_tier import select_provider

        return select_provider(
            tenant=tenant_id,
            project_path=project_path,
            config=self._load_backend_config(),
            fallback=self._sandbox_provider,
            generation=generation,
        )

    def _resolve_sandbox_generation(self, workflow_id: str) -> int:
        """Read the workflow's current ``sandbox_generation``, defaulting to 1.

        The reconciler bumps this on every restart sweep. The provider compares
        a handle's generation against it, so passing a constant here would make
        the stale-handle guard accept exactly the handles it exists to reject.
        """
        if not workflow_id:
            return 1
        try:
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository

            workflow = AutonomousWorkflowRepository().get_workflow(workflow_id)
            if workflow and workflow.get("sandbox_generation"):
                return int(workflow["sandbox_generation"])
        except Exception:
            pass  # a missing row or an unavailable DB must not fail the run
        return 1

    def _load_backend_config(self):
        """Load the sandbox backend config, cached on the config file's mtime.

        Re-reading and re-parsing on every task would do three stat calls plus a
        JSON parse per run for a file that changes at deploy time.
        """
        from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
            load_backend_config,
            resolve_backend_config_path,
        )

        path = resolve_backend_config_path()
        stamp = (path, os.path.getmtime(path)) if path else (None, 0.0)
        cached = getattr(self, "_backend_config_cache", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        config = load_backend_config()
        self._backend_config_cache = (stamp, config)
        return config

    def _run_remote(
        self,
        session_id: str,
        user_id: int | None,
        cli_tool: str,
        model: str,
        project_path: str,
        prompt: str,
        remote_machine_id: str,
        permission_mode: str,
        timeout: int,
        allowed_tools: list[str] | None = None,
    ) -> AgentTaskResult:
        """Run an agent task on a remote machine via RemoteSessionManager."""
        if not self.remote_session_manager:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error="Remote session manager not available",
            )
        if not self.session_manager:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error="Session manager not available for remote session polling",
            )
        if user_id is None:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error="User ID is required for remote autonomous execution",
            )

        # #2022 P4: route autonomous remote execution through RemoteMachineProvider
        # so the orchestrator speaks the same create/exec/stop/destroy contract as
        # the local path. The provider wraps RemoteSessionManager (create_remote_
        # session / send_message / stop_session); it does NOT change the manager
        # or remote.py (autonomous-only scope).
        provider = self._select_sandbox_provider("remote")
        sandbox_handle: SandboxHandle | None = None
        exec_handle: ExecHandle | None = None
        remote_session_id = session_id
        try:
            # Register a tracker so orchestrator can signal cancellation
            tracker = _LocalSession(
                session_id=session_id,
                process=None,  # type: ignore[arg-type]
                task_id=session_id,
            )
            self._local_sessions[session_id] = tracker

            sandbox_handle = provider.create(
                SandboxSpec(
                    task_id=session_id,
                    project_path=project_path,
                    cli_tool=cli_tool,
                    machine_id=remote_machine_id,
                    user_id=user_id,
                    # #2022 P5: express #2020 HOME/TMP/quota via the spec.
                    policy=self._load_task_policy(),
                )
            )
            try:
                with tracker._remote_lifecycle_lock:
                    if tracker._stopped.is_set():
                        # Shutdown arrived before dispatch — do not create/send.
                        return self._build_remote_cancelled_result(session_id, session_id)
                    # exec = create_remote_session + send_message(prompt). It
                    # raises SandboxError on either failure (fail-closed),
                    # replacing the old success-False / sent-False branches.
                    # cancel_check restores the create↔send _stopped window the
                    # old code had (#2078 review 🟡A): a shutdown landing during
                    # create_remote_session is caught BEFORE send dispatches the
                    # prompt.
                    exec_handle = provider.exec(
                        sandbox_handle,
                        command=[],
                        env=None,
                        exec_policy=RemoteTurnSpec(
                            prompt=prompt,
                            model=model,
                            permission_mode=permission_mode,
                            allowed_tools=tuple(allowed_tools) if allowed_tools else None,
                        ),
                        cancel_check=lambda: tracker._stopped.is_set(),
                    )
                    remote_session_id = exec_handle.command_id
                    # #2022 P6: persist mid-run 'running' + remote_session_id so
                    # a crash leaves an orphan the reconciler can destroy by id.
                    self._notify_sandbox_created(
                        session_id, sandbox_handle, remote_session_id, provider
                    )
                    tracker.persisted_session_id = remote_session_id
                    tracker.sandbox_handle = sandbox_handle
                    tracker.exec_handle = exec_handle
                    tracker.sandbox_provider = provider
                    if tracker._stopped.is_set():
                        # Shutdown won while exec was in flight — stop the real
                        # session before the poll loop observes it.
                        provider.stop(exec_handle)
                        return self._build_remote_cancelled_result(remote_session_id, session_id)

                    # Map the hidden stable workflow line to the real remote row so
                    # milestone details resolve the transcript without replacing
                    # the main/review/test tracking identity.
                    tracking_row = self.session_manager.get_session(session_id)
                    context = dict(getattr(tracking_row, "context", {}) or {})
                    if isinstance(tracking_row, dict):
                        context = dict(tracking_row.get("context", {}) or {})
                    context.update(
                        {
                            "workflow_id": context.get("workflow_id", ""),
                            "cli_session_id": remote_session_id,
                        }
                    )
                    self.session_manager.update_session_fields(
                        session_id,
                        {
                            "context": context,
                            "status": "active",
                            "cli_session_id": remote_session_id,
                        },
                        require_tenant=False,
                    )
            except SandboxError as e:
                # provider.exec failed closed (create/send). No remote session
                # leak: exec already stopped a partially-created session.
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=str(e),
                )

            # Poll until session completes. This stays runner-side: it needs the
            # local session_manager for messages/tokens + _remote_turn_complete,
            # which are CLI-protocol concerns (the provider's stream() is the
            # contract equivalent for lifecycle events only).
            import time

            start_time = time.time()
            while time.time() - start_time < timeout:
                # Check if the session has been cancelled externally
                local_session = self._local_sessions.get(session_id)
                if local_session and local_session._stopped.is_set():
                    return self._build_remote_cancelled_result(remote_session_id, session_id)

                session_data = self.session_manager.get_session(remote_session_id)
                if session_data:
                    status = self._session_field(session_data, "status", "active")
                    remote_state = None
                    get_remote_status = getattr(
                        self.remote_session_manager, "get_session_status", None
                    )
                    if callable(get_remote_status):
                        remote_state = get_remote_status(remote_session_id)
                    turn_complete = self._remote_turn_complete(remote_state)
                    if status in ("completed", "stopped", "error", "exited") or turn_complete:
                        # Get messages
                        messages = []
                        if hasattr(self.session_manager, "get_messages"):
                            messages = self.session_manager.get_messages(remote_session_id) or []

                        remote_events, remote_tool_calls = self._normalize_remote_messages(messages)

                        result = _build_agent_task_result(
                            session_id=remote_session_id,
                            tracking_session_id=session_id,
                            source_session_id=remote_session_id,
                            event_log=remote_events,
                            messages=messages,
                            total_tokens=self._session_field(session_data, "total_tokens", 0),
                            total_input_tokens=self._session_field(
                                session_data, "total_input_tokens", 0
                            ),
                            total_output_tokens=self._session_field(
                                session_data, "total_output_tokens", 0
                            ),
                            request_count=self._session_field(session_data, "request_count", 0),
                            tool_calls=remote_tool_calls,
                            # A normal remote CLI turn reports completion via
                            # an is_complete output entry while its reusable
                            # sidebar session deliberately remains active.
                            success=(status in ("active", "completed", "exited")),
                            error=(
                                self._session_field(session_data, "error_message", None)
                                if status != "completed"
                                else None
                            ),
                        )
                        return self._stamp_sandbox_attribution(result, sandbox_handle, provider)
                time.sleep(5)

            timeout_result = self._build_remote_cancelled_result(remote_session_id, session_id)
            if exec_handle is not None:
                try:
                    provider.stop(exec_handle)
                except Exception:
                    logger.warning(
                        "Failed to stop timed-out remote session %s",
                        remote_session_id[:8],
                        exc_info=True,
                    )
            timeout_result.error = f"Remote agent task timed out after {timeout}s"
            return self._stamp_sandbox_attribution(timeout_result, sandbox_handle, provider)

        except Exception as e:
            # Once a real remote id exists, never drop its local tracker while
            # leaving the remote task running after a mapping/polling failure.
            if exec_handle is not None and remote_session_id != session_id:
                try:
                    provider.stop(exec_handle)
                except Exception:
                    logger.warning(
                        "Failed to stop remote session after runner error %s",
                        remote_session_id[:8],
                        exc_info=True,
                    )
            return AgentTaskResult(
                session_id=remote_session_id,
                tracking_session_id=session_id,
                source_session_id=remote_session_id,
                success=False,
                error=f"Remote execution error: {e}",
            )
        finally:
            # #2078 review 🟢: do NOT destroy/stop unconditionally. A successful
            # remote turn leaves its reusable sidebar session deliberately
            # active (see the success-path comment above); the failure paths
            # (timeout / exception / cancel) already call provider.stop
            # explicitly. The provider is per-call (factory mints a fresh one),
            # so not destroying here leaks no cross-session state. Only the
            # local tracker is popped.
            # Clean up the remote session tracker
            self._local_sessions.pop(session_id, None)

    @staticmethod
    def _session_field(session_data: Any, field_name: str, default: Any = None) -> Any:
        """Read SessionManager dataclasses and legacy/test dict rows uniformly."""
        if isinstance(session_data, dict):
            return session_data.get(field_name, default)
        return getattr(session_data, field_name, default)

    @staticmethod
    def _remote_turn_complete(remote_state: Any) -> bool:
        """Whether the remote manager observed the current request finish."""
        if not isinstance(remote_state, dict):
            return False
        output = remote_state.get("output") or []
        return any(
            isinstance(entry, dict)
            and bool(entry.get("is_complete"))
            and entry.get("stream") in ("stdout", "stderr", "system")
            for entry in output
        )

    def _build_remote_cancelled_result(
        self, remote_session_id: str, tracking_session_id: str
    ) -> AgentTaskResult:
        """Snapshot durable partial remote usage before unwinding shutdown."""
        session_data = (
            self.session_manager.get_session(remote_session_id) if self.session_manager else None
        )
        return AgentTaskResult(
            session_id=remote_session_id,
            tracking_session_id=tracking_session_id,
            source_session_id=remote_session_id,
            total_tokens=self._session_field(session_data, "total_tokens", 0),
            total_input_tokens=self._session_field(session_data, "total_input_tokens", 0),
            total_output_tokens=self._session_field(session_data, "total_output_tokens", 0),
            request_count=self._session_field(session_data, "request_count", 0),
            success=False,
            error="Remote session cancelled by orchestrator",
        )

    # ── Local helpers ──────────────────────────────────────────────

    def _persist_local_session_messages(
        self, session_id: str, result: AgentTaskResult, milestone_id: str = ""
    ) -> int:
        """Write workflow-visible messages to ``session_messages``.

        Autonomous workflow detail views should show the final assistant output
        for a phase, not every streamed delta / hidden reasoning fragment. We
        therefore persist all tool-use events but collapse assistant deltas down
        to the last visible assistant turn.

        Falls back to separate ``response_text`` + ``tool_calls`` if event_log
        is empty (e.g. remote sessions or legacy code path).

        Called after the agent task finishes, before the session status is
        updated.  Errors are caught by the caller and do not affect the
        main workflow.
        """
        persisted_count = 0

        if result.prompt:
            prompt_external_id = f"phase-prompt:{milestone_id}" if milestone_id else ""
            stored = self.session_manager.append_transcript_message(
                session_id=session_id,
                role="user",
                content=result.prompt,
                milestone_id=milestone_id,
                source="autonomous_local_runner",
                external_message_id=prompt_external_id,
            )
            if getattr(stored, "_was_inserted", False):
                persisted_count += 1

        # Prefer ordered event log for accurate message interleaving
        # count_usage=False: the agent runner owns request_count/total_tokens via
        # increment_session_usage at finish time; counting here too would
        # double-count (#1003 / #1007 review).
        if result.event_log:
            merged_events = _coalesce_assistant_events(result.event_log)
            final_assistant = None
            for event in merged_events:
                if event.get("type") == "assistant":
                    final_assistant = event
                elif event.get("type") == "tool_use":
                    tool_input = event.get("tool_input", {})
                    stored = self.session_manager.append_transcript_message(
                        session_id=session_id,
                        role="tool",
                        content=(
                            json.dumps(tool_input)
                            if isinstance(tool_input, (dict, list))
                            else str(tool_input)
                        ),
                        metadata={
                            "tool_name": event.get("tool_name", "unknown"),
                            **(
                                {"tool_use_id": event.get("tool_use_id")}
                                if event.get("tool_use_id")
                                else {}
                            ),
                        },
                        milestone_id=milestone_id,
                        source="autonomous_local_runner",
                        external_message_id=event.get("tool_use_id", ""),
                    )
                    if getattr(stored, "_was_inserted", False):
                        persisted_count += 1

                    # Parse file changes from tool_use events (Issue #2589)
                    try:
                        from shared.file_change_parser import (
                            FileChangeParserRegistry,
                            ParserContext,
                        )

                        tool_name = event.get("tool_name", "unknown")
                        tool_use_id = event.get("tool_use_id", "")
                        parse_context = ParserContext(
                            session_id=session_id,
                            tool_use_id=tool_use_id,
                            project_path=getattr(self, "project_path", ""),
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        )
                        records = FileChangeParserRegistry.parse(
                            tool_name, tool_input, parse_context
                        )
                        if records:
                            self.session_manager.append_transcript_message(
                                session_id=session_id,
                                role="assistant",
                                content="",
                                metadata={
                                    "content_blocks": [
                                        {
                                            "type": "file_change",
                                            "status": "pending",
                                            "changes": [r.to_dict() for r in records],
                                        }
                                    ]
                                },
                                milestone_id=milestone_id,
                                source="file_change_parser",
                                external_message_id=f"fc:{tool_use_id}",
                            )
                    except Exception as e:
                        logger.warning(f"File change parsing failed: {e}")
            if final_assistant:
                assistant_content = pick_best_artifact_text(
                    final_assistant.get("text", ""),
                    result.response_text,
                    result.visible_response_text,
                )
                stored = self.session_manager.append_transcript_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content or final_assistant.get("text", ""),
                    model=final_assistant.get("model"),
                    metadata=(
                        {"message_id": final_assistant.get("message_id")}
                        if final_assistant.get("message_id")
                        else None
                    ),
                    milestone_id=milestone_id,
                    source="autonomous_local_runner",
                    external_message_id=final_assistant.get("message_id", ""),
                )
                if getattr(stored, "_was_inserted", False):
                    persisted_count += 1
                # usage events are metadata-only, not persisted as messages
        else:
            # Fallback: write as single assistant + individual tool messages
            if result.response_text:
                stored = self.session_manager.append_transcript_message(
                    session_id=session_id,
                    role="assistant",
                    content=result.response_text,
                    milestone_id=milestone_id,
                    source="autonomous_local_runner",
                )
                if getattr(stored, "_was_inserted", False):
                    persisted_count += 1
            for tool_call in result.tool_calls:
                tool_info = tool_call.get("tool", {})
                tool_name = tool_info.get("name", "unknown")
                tool_input = tool_info.get("input", {})
                stored = self.session_manager.append_transcript_message(
                    session_id=session_id,
                    role="tool",
                    content=(
                        json.dumps(tool_input)
                        if isinstance(tool_input, (dict, list))
                        else str(tool_input)
                    ),
                    metadata={"tool_name": tool_name},
                    milestone_id=milestone_id,
                    source="autonomous_local_runner",
                )
                if getattr(stored, "_was_inserted", False):
                    persisted_count += 1
        return persisted_count

    def _send_sdk_init(self, session: _LocalSession) -> bool:
        """Send SDK initialize message and record request_id for response matching."""
        request_id = str(uuid.uuid4())
        session.init_request_id = request_id
        init_msg = {
            "type": "control_request",
            "request_id": request_id,
            "request": {"subtype": "initialize"},
        }
        return self._write_stdin(session, json.dumps(init_msg))

    def _send_message(self, session: _LocalSession, content: str) -> bool:
        """Send a user message to the agent."""
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
        }
        return self._write_stdin(session, json.dumps(msg))

    def _write_stdin(self, session: _LocalSession, payload: str) -> bool:
        """Write a JSON message to the subprocess stdin."""
        try:
            if session.transport is None:
                return False
            session.transport.write_stdin((payload + "\n").encode("utf-8"))
            return True
        except (OSError, BrokenPipeError, AttributeError) as e:
            logger.error("Failed to write to stdin for %s: %s", session.session_id[:8], e)
            return False

    def _accumulate_turn_usage(self, session: _LocalSession, usage: dict) -> None:
        """Accumulate one result message's usage into session totals.

        For tools whose result message reports CROSS-TURN CUMULATIVE usage
        (qwen-code-cli), difference successive snapshots so the running total
        isn't re-added every turn (which inflated total_*_tokens / quota).
        Otherwise add the raw per-request value as-is.
        """
        if _is_cumulative_result_tool(session.cli_tool):
            delta, session._last_cum_input, session._last_cum_output = _diff_cumulative_usage(
                usage,
                session._last_cum_input,
                session._last_cum_output,
            )
            session.total_input_tokens += delta["input"]
            session.total_output_tokens += delta["output"]
        else:
            session.total_input_tokens += usage["input"]
            session.total_output_tokens += usage["output"]

    def _read_stdout(self, session: _LocalSession) -> None:
        """Read stdout lines from the subprocess."""
        try:
            while not session._stopped.is_set():
                if session.transport is None:
                    break
                line = session.transport.readline_stdout()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = line.strip()
                if not line:
                    continue

                session.output_lines.append(line)

                try:
                    parsed = json.loads(line)
                    msg_type = parsed.get("type", "")

                    if msg_type == "assistant":
                        # Accumulate assistant text
                        msg = parsed.get("message", {})
                        content = msg.get("content", "")
                        message_id = msg.get("id")
                        text_delta = _extract_visible_text(content)
                        if text_delta:
                            session.assistant_text += text_delta
                        # Count one request per distinct assistant message (by
                        # message_id), aligning with the session-detail口径
                        # (fetch dedups assistant turns by message_id). Claude
                        # --print emits one `result` summarizing all turns, so
                        # counting on `result` would always yield 1 regardless
                        # of how many model turns happened (#723).
                        if message_id and message_id not in session._counted_message_ids:
                            session._counted_message_ids.add(message_id)
                            session.request_count += 1
                        # Record in event log for ordered message persistence
                        if text_delta:
                            session.event_log.append(
                                {
                                    "type": "assistant",
                                    "text": text_delta,  # full text for DB persistence
                                    "message_id": message_id,
                                    "model": msg.get("model"),
                                }
                            )
                        # Current Claude stream-json embeds tool_use blocks in
                        # the assistant message instead of emitting the legacy
                        # top-level ``type=tool_use`` shape handled below.
                        # Preserve both the invocation and its stable id so a
                        # later user/tool_result block can prove which test
                        # command actually completed.
                        if isinstance(content, list):
                            for block in content:
                                if not isinstance(block, dict) or block.get("type") != "tool_use":
                                    continue
                                tool_info = {
                                    "name": block.get("name", "unknown"),
                                    "input": block.get("input", {}),
                                    "id": block.get("id"),
                                }
                                session.tool_calls.append({"type": "tool_use", "tool": tool_info})
                                session.event_log.append(
                                    {
                                        "type": "tool_use",
                                        "tool_name": tool_info["name"],
                                        "tool_input": tool_info["input"],
                                        "tool_use_id": tool_info["id"],
                                    }
                                )
                                if self._activity_callback:
                                    self._activity_callback(
                                        session.session_id,
                                        {
                                            "type": "tool_use",
                                            "tool_name": tool_info["name"],
                                            "tool_input": str(tool_info["input"])[:200],
                                        },
                                    )
                        # Emit activity for real-time frontend display
                        if self._activity_callback and text_delta:
                            self._activity_callback(
                                session.session_id,
                                {
                                    "type": "assistant",
                                    "text": text_delta[:500],  # truncate for SSE
                                },
                            )

                    elif msg_type == "tool_use":
                        session.tool_calls.append(parsed)
                        # Record in event log for ordered message persistence
                        tool_info = parsed.get("tool", {})
                        session.event_log.append(
                            {
                                "type": "tool_use",
                                "tool_name": tool_info.get("name", "unknown"),
                                "tool_input": tool_info.get("input", {}),
                                "tool_use_id": tool_info.get("id"),
                            }
                        )
                        # Emit tool call activity
                        if self._activity_callback:
                            self._activity_callback(
                                session.session_id,
                                {
                                    "type": "tool_use",
                                    "tool_name": tool_info.get("name", "unknown"),
                                    "tool_input": str(tool_info.get("input", ""))[:200],
                                },
                            )

                    elif msg_type == "user":
                        # Claude stream-json carries Bash/tool execution output
                        # as user-message ``tool_result`` blocks. Preserve it
                        # in the private event log (never visible assistant
                        # text) so test verification can use actual command
                        # evidence instead of trusting the model's summary.
                        content = (parsed.get("message", {}) or {}).get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                if (
                                    not isinstance(block, dict)
                                    or block.get("type") != "tool_result"
                                ):
                                    continue
                                raw_content = block.get("content", "")
                                if isinstance(raw_content, list):
                                    result_text = "\n".join(
                                        str(item.get("text", ""))
                                        for item in raw_content
                                        if isinstance(item, dict) and item.get("text")
                                    )
                                else:
                                    result_text = str(raw_content or "")
                                session.event_log.append(
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": block.get("tool_use_id"),
                                        "text": result_text,
                                        "exit_code": block.get("exit_code"),
                                        "is_error": bool(block.get("is_error", False)),
                                    }
                                )

                    elif msg_type == "result":
                        self._capture_cli_session_id(session, parsed, "result")
                        # End of turn - extract usage via shared parser.
                        # request_count is counted per assistant message_id above
                        # (not here), since one --print result summarizes all turns.
                        usage = _extract_stream_usage(session.cli_tool, parsed)
                        if usage:
                            self._accumulate_turn_usage(session, usage)
                        session.total_tokens = (
                            session.total_input_tokens + session.total_output_tokens
                        )
                        # Fallback: if no assistant turn carried a message_id
                        # (older/non-Claude adapters, or chunks without id),
                        # count this result as one request so request accounting
                        # doesn't silently drop to 0. When ids WERE seen, turns
                        # were already counted per-id above and result must NOT
                        # bump (it summarizes the whole --print run).
                        if not session._counted_message_ids and session.request_count == 0:
                            session.request_count += 1
                        # #2640: the terminal ``result`` event carries the
                        # CLI's final answer in its ``result`` text field.
                        # Assistant stream events can be dropped near the
                        # context limit (large-context --resume turns), so
                        # capture it as a fallback response source instead of
                        # discarding it (see _recover_final_response_text).
                        result_value = parsed.get("result")
                        if isinstance(result_value, str) and result_value.strip():
                            session.result_text = result_value
                        error_code, error_message = _extract_cli_result_error(
                            parsed, session.last_stderr
                        )
                        if error_message:
                            session.error_code = error_code
                            session.error = error_message
                            logger.warning(
                                "Agent CLI result reported error (%s): %s",
                                error_code or "unknown_cli_error",
                                error_message,
                            )
                        self._sync_sidebar_session_totals(
                            session, status="error" if session.error else "active"
                        )
                        # ``--input-format stream-json`` keeps the CLI alive for
                        # another turn until stdin reaches EOF. A result event is
                        # terminal for this one-shot invocation, so close the
                        # write side before waking the caller. Otherwise killing
                        # only the sudo launcher can strand the isolated wrapper
                        # while it still holds the per-agent ACL lock.
                        try:
                            if session.transport is not None:
                                session.transport.close_stdin()
                        except (OSError, BrokenPipeError, AttributeError, ValueError):
                            pass
                        session.completed.set()
                        # Emit usage activity for real-time token display
                        if self._activity_callback:
                            self._activity_callback(
                                session.session_id,
                                {
                                    "type": "usage",
                                    "total_tokens": session.total_tokens,
                                    "total_input_tokens": session.total_input_tokens,
                                    "total_output_tokens": session.total_output_tokens,
                                    "request_count": session.request_count,
                                },
                            )

                    elif msg_type in {"system", "initialized"}:
                        subtype = parsed.get("subtype", "")
                        # Claude CLI emits subtype "init" (older versions used
                        # "initialized"); accept both so the real cli_session_id
                        # is captured at agent start instead of via mtime fallback.
                        if msg_type == "initialized" or subtype in ("initialized", "init"):
                            self._capture_cli_session_id(
                                session, parsed, f"system.{subtype or 'init'}"
                            )
                        # Forward key system events to the UI so the workflow
                        # detail shows progress during long LLM waits.
                        # Without this, an agent stuck in api_retry (upstream
                        # overload / transient auth) emits only `system` events
                        # — which never triggered _activity_callback — so the
                        # UI showed "no AI activity" for minutes until the
                        # first `assistant` event finally arrived.
                        # This is a high-frequency cumulative estimate, not a
                        # discrete user-visible activity. Skipping the event
                        # also prevents one sidebar fallback probe per token.
                        if subtype == "thinking_tokens":
                            continue
                        if self._activity_callback and subtype:
                            activity: dict[str, Any] = {
                                "type": "system",
                                "subtype": subtype,
                            }
                            if subtype == "api_retry":
                                activity["attempt"] = parsed.get("attempt", 0)
                            self._activity_callback(session.session_id, activity)

                    elif msg_type == "control_response":
                        # Capture the real Claude session_id from the SDK
                        # initialize response (mirrors remote executor.py).
                        resp = parsed.get("response", {}) or {}
                        if (
                            session.init_request_id
                            and resp.get("request_id") == session.init_request_id
                        ):
                            if resp.get("subtype") == "success":
                                cli_sid = self._capture_cli_session_id(
                                    session,
                                    parsed,
                                    "control_response.initialize",
                                )
                                if not cli_sid and not session.cli_session_id:
                                    # Initialize succeeded but no session_id — the
                                    # SDK response shape may have changed. Log so the
                                    # mtime fallback (next resort) is traceable rather
                                    # than silently degrading (#723).
                                    logger.warning(
                                        "control_response initialize success but no "
                                        "session_id (workflow=%s); raw=%s",
                                        session.workflow_id,
                                        str(parsed)[:300],
                                    )
                            session.sdk_initialized.set()

                    elif msg_type == "control_request":
                        # Auto-approve permissions in autonomous mode,
                        # with filtering when allowed_tools is set (Issue #761).
                        req_id = parsed.get("request_id", "")
                        if req_id:
                            request_payload = parsed.get("request", {})
                            tool_name = request_payload.get("tool_name", "")
                            tool_input = request_payload.get("input", {})

                            forbidden_command = _is_forbidden_autonomous_command(
                                tool_name, tool_input
                            )
                            if forbidden_command or (
                                session.allowed_tools is not None
                                and tool_name not in session.allowed_tools
                            ):
                                # Tool not in allowed list — deny
                                response = {
                                    "type": "control_response",
                                    "response": {
                                        "request_id": req_id,
                                        "subtype": "success",
                                        "response": {
                                            "behavior": "deny",
                                            "message": (
                                                "git push and mutating gh pr commands are reserved "
                                                "for the orchestrator, and Python must use the "
                                                "runtime-bound PATH shim."
                                                if forbidden_command
                                                else f"Tool '{tool_name}' is not allowed in this phase."
                                            ),
                                        },
                                    },
                                }
                                logger.warning(
                                    "Denied tool '%s' for session %s (not in allowed list)",
                                    tool_name,
                                    (session.persisted_session_id or session.session_id)[:8],
                                )
                            else:
                                # Approve (no restriction, or tool is allowed)
                                response = {
                                    "type": "control_response",
                                    "response": {
                                        "request_id": req_id,
                                        "subtype": "success",
                                        "response": {"behavior": "allow"},
                                    },
                                }
                            self._write_stdin(session, json.dumps(response))

                    if not session.persisted_session_id and (
                        session.cli_session_id
                        or session.sdk_initialized.is_set()
                        or msg_type == "result"
                    ):
                        self._resolve_sidebar_session(session)

                except (json.JSONDecodeError, ValueError):
                    # Non-JSON output line. #2640: these were silently
                    # swallowed, hiding CLI stream-json protocol drift (e.g.
                    # dropped assistant events near the context limit) from
                    # diagnosis. Emit a truncated-prefix warning with a
                    # per-run counter — never the full line.
                    session._unparseable_stdout_lines += 1
                    if (
                        session._unparseable_stdout_lines <= 5
                        or session._unparseable_stdout_lines % 100 == 0
                    ):
                        logger.warning(
                            "Agent CLI emitted non-JSON stdout line #%d " "(session=%s): %.200s",
                            session._unparseable_stdout_lines,
                            session.session_id[:8],
                            line,
                        )

        except (OSError, ValueError):
            pass
        finally:
            # If process exited without sending result, mark completed.
            # poll() must be called to reap the child and populate
            # returncode — readline() returning EOF does NOT set it
            # automatically, so without poll() the returncode check
            # would fail and session.completed would never be set,
            # causing _wait_for_completion to block until the 1-hour
            # timeout (#2031).
            if not session.completed.is_set():
                session._stopped.wait(2.0)
                if session.transport is not None:
                    # Two steps on purpose. `returncode` is the non-reaping
                    # accessor the pause/resume guards share, so the transport
                    # keeps the two operations separate; this loop is the one
                    # place that should actively reap, then read the result.
                    session.transport.poll()
                    if session.transport.returncode is not None:
                        session.completed.set()

    def _read_stderr(self, session: _LocalSession) -> None:
        """Read stderr from the subprocess."""
        try:
            while not session._stopped.is_set():
                if session.transport is None:
                    break
                line = session.transport.readline_stderr()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                stripped = line.strip()
                if stripped:
                    session.last_stderr = stripped
                logger.debug("[%s/stderr] %s", session.session_id[:8], line.strip())
        except (OSError, ValueError):
            pass

    def stop_session(self, session_id: str) -> None:
        """Stop a running local session with SIGTERM + SIGKILL escalation.

        If the process is currently paused (SIGSTOP), it will be resumed
        first with SIGCONT so it can handle SIGTERM.
        """
        session = self._local_sessions.get(session_id)
        if not session:
            return
        # #2022 P4 ③: route stop through the SandboxProvider when the session has
        # one. gVisor (#2023) has no local Popen — cancellation/timeout MUST
        # reach the sandbox via provider.stop. Legacy (raw-proc killpg) and
        # Remote (remote_session_manager.stop_session) both set sandbox_provider
        # on the tracker, so this covers both; the branches below are the
        # raw-proc / no-provider fallbacks for legacy tracker paths.
        if session.sandbox_provider is not None and session.exec_handle is not None:
            session._stopped.set()
            with session._remote_lifecycle_lock:
                try:
                    session.sandbox_provider.stop(session.exec_handle)
                except Exception as exc:
                    logger.warning("sandbox stop failed for %s: %s", session_id[:8], exc)
            session.completed.set()
            return
        if session.process is None:
            # Remote autonomous calls use a process-less local tracker so the
            # synchronous poll loop can observe cancellation. Stop the actual
            # remote session when possible, but always trip the local events:
            # shutdown must drain even if the remote machine is unreachable.
            # Publish cancellation before waiting for an in-flight mapping or
            # send call. _run_remote re-checks this flag immediately before
            # dispatch, so shutdown cannot be hidden behind the lifecycle lock.
            session._stopped.set()
            with session._remote_lifecycle_lock:
                try:
                    if self.remote_session_manager:
                        remote_session_id = session.persisted_session_id or session_id
                        self.remote_session_manager.stop_session(remote_session_id)
                except Exception as exc:
                    logger.warning("Failed to stop remote session %s: %s", session_id[:8], exc)
                finally:
                    session.completed.set()
            return
        if session.process.returncode is not None:
            return

        try:
            pgid = os.getpgid(session.process.pid)
        except (ProcessLookupError, OSError):
            session._stopped.set()
            session.completed.set()
            return

        # If paused, resume first so it can handle SIGTERM
        if session._paused.is_set():
            try:
                os.killpg(pgid, signal.SIGCONT)
                session._paused.clear()
            except (ProcessLookupError, OSError):
                pass

        # Stage 1: SIGTERM
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            session._stopped.set()
            session.completed.set()
            return

        # Stage 2: wait up to 5 seconds for graceful exit
        try:
            session.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Stage 3: SIGKILL
            logger.warning(
                "Process %d did not exit after SIGTERM, sending SIGKILL",
                session.process.pid,
            )
            try:
                os.killpg(pgid, signal.SIGKILL)
                session.process.wait(timeout=3)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                pass

        session._stopped.set()
        session.completed.set()

    def pause_session(self, session_id: str) -> bool:
        """Suspend a running local session using SIGSTOP.

        The process is frozen in place and can be resumed with
        :meth:`resume_session` using SIGCONT. A remote tracker has no transport
        and returns False here (a remote CLI session has no SIGSTOP analogue —
        pause is unsupported, not silently claimed);
        ``RemoteMachineProvider.pause`` is a documented no-op.

        #2023: the guard keys off the transport, not a local ``process``. A
        container backend is pidless, so the old ``not session.process`` check
        returned False before the provider branch below could run — making pause
        permanently unavailable for it. ``mark_session_paused_by_pid`` /
        ``mark_session_resumed_by_pid`` remain pid-keyed and are therefore
        inapplicable to a pidless backend; for those, this method is the only
        pause path, and the provider branch sets ``_paused``, which is what
        freezes ``_wait_for_completion``'s budget.
        """
        session = self._local_sessions.get(session_id)
        # #2023: the liveness guard must not require a local process. A container
        # backend has session.process None, and the old ordering returned False
        # here — making pause permanently unavailable for it, before the provider
        # branch below was ever reached. returncode (not poll()) keeps this a
        # cached read, so no waitpid() is added to a path that runs concurrently
        # with _wait_for_completion's own poll().
        if not session or session.transport is None:
            return False
        if session.transport.returncode is not None:
            return False
        if session._paused.is_set():
            return True
        # #2022 P4 ③: route through the SandboxProvider when present (gVisor
        # paves container pause); equivalent to the raw SIGSTOP below for Legacy.
        if session.sandbox_provider is not None and session.exec_handle is not None:
            try:
                session.sandbox_provider.pause(session.exec_handle)
                session._paused.set()
                logger.info("Paused session %s via sandbox provider", session_id[:8])
                return True
            except Exception as e:
                logger.error("Failed to pause session %s: %s", session_id[:8], e)
                return False
        try:
            pgid = os.getpgid(session.process.pid)
            os.killpg(pgid, signal.SIGSTOP)
            session._paused.set()
            logger.info("Paused session %s (pid %d)", session_id[:8], session.process.pid)
            return True
        except (ProcessLookupError, OSError) as e:
            logger.error("Failed to pause session %s: %s", session_id[:8], e)
            return False

    def resume_session(self, session_id: str) -> bool:
        """Resume a paused local session using SIGCONT."""
        session = self._local_sessions.get(session_id)
        # See pause_session: the guard is transport-based so a pidless backend
        # reaches the provider branch below.
        if not session or session.transport is None:
            return False
        if session.transport.returncode is not None:
            return False
        if not session._paused.is_set():
            return True
        if session.sandbox_provider is not None and session.exec_handle is not None:
            try:
                session.sandbox_provider.resume(session.exec_handle)
                session._paused.clear()
                logger.info("Resumed session %s via sandbox provider", session_id[:8])
                return True
            except Exception as e:
                logger.error("Failed to resume session %s: %s", session_id[:8], e)
                return False
        try:
            pgid = os.getpgid(session.process.pid)
            os.killpg(pgid, signal.SIGCONT)
            session._paused.clear()
            logger.info("Resumed session %s (pid %d)", session_id[:8], session.process.pid)
            return True
        except (ProcessLookupError, OSError) as e:
            logger.error("Failed to resume session %s: %s", session_id[:8], e)
            return False

    def mark_session_paused_by_pid(self, pid: int) -> bool:
        """Flag the in-memory session owning ``pid`` as paused.

        The pause fallback paths (``_pause_running_task`` Strategy 2/3) send
        SIGSTOP directly to a PID without going through :meth:`pause_session`,
        so the session's ``_paused`` Event stays clear. ``_wait_for_completion``
        then keeps counting the timeout budget and reaps the frozen process
        once it elapses — surfacing as a paused workflow "auto-resuming". This
        marks the matching session paused so the budget freezes correctly,
        regardless of which path delivered the SIGSTOP.
        """
        for session in self._local_sessions.values():
            if session.process and session.process.pid == pid:
                session._paused.set()
                return True
        return False

    def mark_session_resumed_by_pid(self, pid: int) -> bool:
        """Clear the paused flag on the in-memory session owning ``pid``.

        Mirror of :meth:`mark_session_paused_by_pid` for the resume fallback
        paths, so ``_wait_for_completion`` unfreezes the deadline.
        """
        for session in self._local_sessions.values():
            if session.process and session.process.pid == pid:
                session._paused.clear()
                return True
        return False
