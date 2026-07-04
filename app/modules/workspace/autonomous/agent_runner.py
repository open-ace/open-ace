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
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.modules.workspace.autonomous.artifact_text import pick_best_artifact_text
from app.modules.workspace.autonomous.models import AgentTaskResult

logger = logging.getLogger(__name__)


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

# Path to the cross-user agent launcher (Issue #1395). The service runs as
# `openace` but agent CLIs (claude-code/qwen-code/openclaw) infer the project
# root from cwd and have no --cwd flag, so they must launch with cwd=project.
# Under a user-private repo path Popen's chdir fails as the service user; this
# wrapper (root via a narrow sudoers rule) chdir's then drops to system_account
# via runuser. See scripts/openace-run-as.sh.
_OPENACE_RUN_AS = os.environ.get("OPENACE_RUN_AS", "/usr/local/bin/openace-run-as")

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
class _LocalSession:
    """Tracks a local CLI subprocess session."""

    session_id: str
    process: subprocess.Popen | None
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
    # Distinct assistant message_ids counted toward request_count (dedup, since
    # claude emits multiple assistant events per message: thinking then text).
    _counted_message_ids: set = field(default_factory=set)
    _paused: threading.Event = field(default_factory=threading.Event)  # set when SIGSTOPed


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
        event_log=normalized_event_log,
    )


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

        # Tool events: ZCode emits tool.<name> with data payload.
        # Normalize to the Claude SDK tool_use shape for persistence.
        elif msg_type.startswith("tool."):
            tool_name = msg_type.split(".", 1)[1]
            payload = parsed.get("data", {}) or {}
            # ZCode sends tool.updated lifecycle notifications (kind =
            # scheduled/started/result/batch) that carry only scheduling
            # metadata, not real tool input/output. These would leak as
            # tool_name="updated" garbage messages, so skip them. Real
            # tool_use events have no "kind" field and carry actual input.
            if isinstance(payload, dict) and payload.get("kind") in (
                "scheduled",
                "started",
                "result",
                "batch",
            ):
                return
            tool_input = payload.get("input", payload)
            tool_id = payload.get("id", "")
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
        """
        self.session_manager = session_manager
        self.remote_session_manager = remote_session_manager
        self.server_url = server_url or os.environ.get(
            "OPENACE_SERVER_URL", "http://localhost:19888"
        )
        self._activity_callback = activity_callback
        self._on_pid_registered = on_pid_registered
        self._on_pid_cleared = on_pid_cleared
        self._local_sessions: dict[str, _LocalSession] = {}

    @staticmethod
    def _uses_sidebar_session_source(cli_tool: str, workspace_type: str) -> bool:
        """Whether this task should resolve to the real sidebar Claude session."""
        return workspace_type == "local" and cli_tool == "claude-code"

    @staticmethod
    def _extract_stream_session_id(parsed: dict[str, Any]) -> str:
        """Best-effort extraction of Claude's real session_id from a stream event."""
        if not isinstance(parsed, dict):
            return ""

        candidates: list[Any] = [parsed.get("session_id")]
        response = parsed.get("response", {}) or {}
        if isinstance(response, dict):
            candidates.append(response.get("session_id"))
            inner_response = response.get("response", {}) or {}
            if isinstance(inner_response, dict):
                candidates.append(inner_response.get("session_id"))

        for candidate in candidates:
            session_id = str(candidate or "").strip()
            if session_id:
                return session_id
        return ""

    def _capture_cli_session_id(
        self,
        session: _LocalSession,
        parsed: dict[str, Any],
        source: str,
    ) -> str:
        """Persist the authoritative Claude session_id when the stream exposes it."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return ""

        cli_session_id = self._extract_stream_session_id(parsed)
        if not cli_session_id:
            return ""

        if cli_session_id != session.cli_session_id:
            session.cli_session_id = cli_session_id
            logger.info(
                "Captured Claude session_id from %s (workflow=%s tracking=%s cli=%s)",
                source,
                session.workflow_id,
                (session.session_id or "")[:8],
                cli_session_id[:8],
            )
        return cli_session_id

    @staticmethod
    def _ensure_project_dir(project_path: str, system_account: str | None) -> None:
        """Ensure ``project_path`` exists, cross-user safe (Issue #1395).

        ``Path.mkdir`` stats/creates as the service user and raises
        ``PermissionError`` when the path lives under a user-private parent
        (e.g. a 0700 home). When ``system_account`` differs from the service
        user, route through ``sudo -u <account> mkdir -p`` (``mkdir`` is
        covered by the sudoers ``OPENACE_UTILS`` alias). Same-user skips sudo
        to avoid failing under systemd ``NoNewPrivileges`` (mirrors
        ``github_ops._needs_sudo``).
        """
        same_user = False
        if system_account:
            try:
                same_user = pwd.getpwuid(os.getuid()).pw_name == system_account
            except (KeyError, OverflowError):
                same_user = False
        if system_account and not same_user:
            # Mirror Path.mkdir's failure semantics: raise on error so the
            # caller fails fast instead of chalking it up to a later, fuzzier
            # CLI-launch failure (Issue #1395 review).
            result = subprocess.run(
                ["sudo", "-u", system_account, "mkdir", "-p", project_path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise PermissionError(
                    f"Failed to create project dir {project_path} as "
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
    def _wrap_agent_cmd(
        cmd: list[str], project_path: str, system_account: str | None
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
            wrapped = [
                "sudo",
                "-n",
                "-u",
                "root",
                _OPENACE_RUN_AS,
                system_account,
                project_path,
            ] + cmd
            return wrapped, None  # wrapper chdir's internally; cwd must be None
        return cmd, project_path

    @staticmethod
    def _build_agent_env(
        adapter: Any,
        cli_tool: str,
        user_id: int | None,
        session_id: str,
        model: str,
    ) -> dict[str, str]:
        """Build subprocess env with LLM proxy auth for a local agent.

        Local autonomous agents (claude-code/qwen-code/etc.) must authenticate
        through the Open ACE LLM proxy — never with the raw API key. This mints
        a short-lived signed proxy token (via APIKeyProxyService) and asks the
        adapter to map it onto the tool-specific env vars (e.g. ANTHROPIC_API_KEY
        / ANTHROPIC_BASE_URL for claude-code). Mirrors executor._build_env but
        runs in-process, without an HTTP round-trip.

        Falls back to dict(os.environ) when proxy setup is unavailable (e.g. no
        API key configured for this tool) so a dev box with env-injected keys
        keeps working.
        """
        env = dict(os.environ)
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
            env.update(adapter.get_env_vars(proxy_url, proxy_token))
            env["OPENACE_PROXY_URL"] = proxy_url
            env["OPENACE_PROXY_TOKEN"] = proxy_token
            if model:
                env["OPENACE_MODEL"] = model
        except Exception as e:
            logger.warning(
                "Could not set up LLM proxy auth for %s (falling back to env): %s",
                cli_tool,
                e,
            )
        return env

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
        return resolved.replace("/", "-")

    def _find_latest_claude_session_id(
        self,
        encoded_project_path: str,
        min_mtime_epoch: float,
        bound_cli_session_ids: set[str] | None = None,
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

        project_dir = Path.home() / ".claude" / "projects" / encoded_project_path
        if not project_dir.is_dir():
            return ""

        latest_file = None
        latest_mtime = min_mtime_epoch - SESSION_DETECTION_GRACE_SECONDS
        try:
            for candidate in project_dir.glob("*.jsonl"):
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if not (
                    stat.st_mtime >= min_mtime_epoch - SESSION_DETECTION_GRACE_SECONDS
                    and stat.st_mtime >= latest_mtime
                ):
                    continue
                # Exclude files whose session id is already bound to another line
                # (e.g. the always-being-written main session). This prevents the
                # shared main session from being re-picked for a fresh review/test
                # line and collapsing the 3-session design (#723).
                if bound_cli_session_ids:
                    sid = self._peek_jsonl_session_id(candidate)
                    if sid and sid in bound_cli_session_ids:
                        continue
                latest_file = candidate
                latest_mtime = stat.st_mtime
        except OSError:
            return ""

        return latest_file.stem if latest_file else ""

    @staticmethod
    def _peek_jsonl_session_id(filepath: Path) -> str:
        """Read the session id from the first record of a Claude JSONL file.

        Claude writes the session uuid as ``sessionId`` (and sometimes ``uuid``)
        on every record; we only read the first non-empty line to identify the
        file's owning session without parsing the whole transcript.
        """
        try:
            with open(filepath, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    sid = ""
                    if isinstance(rec, dict):
                        sid = (rec.get("sessionId") or rec.get("uuid") or "").strip()
                    if sid:
                        return sid
                    break
        except OSError:
            pass
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
            Path.home()
            / ".claude"
            / "projects"
            / session.encoded_project_path
            / f"{cli_session_id}.jsonl"
        )
        if not jsonl_path.is_file():
            return
        # Accumulate usage per distinct message_id. Claude repeats the FULL
        # message usage on every block-line of a message (thinking line, text
        # line, ...), so summing raw rows would double/triple count. Take the
        # max usage per message_id (each row carries the same totals for that
        # message), then sum across distinct messages.
        per_msg: dict[str, dict[str, int]] = {}
        started = session.started_at_epoch
        try:
            with open(jsonl_path, encoding="utf-8") as fh:
                for line in fh:
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
        except OSError:
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
            self.session_manager.update_session_fields(session.session_id, updates)
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
        session_id = session_id or str(uuid.uuid4())
        uses_sidebar_session = self._uses_sidebar_session_source(cli_tool, workspace_type)
        # App-server tools (ZCode) resolve their real CLI session id only after
        # session/create inside _run_zcode_appserver. The wrapper row must be
        # keyed by that CLI id (not the uuid) so add_message's session-exists
        # check passes and milestone/messages keys line up — same invariant
        # _ensure_sidebar_session gives Claude. So skip the uuid-keyed pre-create
        # here; _run_zcode_appserver creates the row under the real CLI id.
        creates_session_late = cli_tool in _APPSERVER_TOOLS

        # Create wrapper sessions only for tools without a deferred session id.
        if self.session_manager and not creates_session_late:
            try:
                self.session_manager.create_session(
                    session_id=session_id,
                    session_type=session_type,
                    title=f"Autonomous: {workflow_id[:8]}",
                    tool_name=cli_tool,
                    user_id=user_id,
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
            if self.session_manager and persisted_session_id:
                try:
                    self.session_manager.increment_session_usage(
                        persisted_session_id,
                        message_delta=message_delta,
                        request_delta=result.request_count or 0,
                        total_tokens_delta=result.total_tokens or 0,
                        total_input_delta=result.total_input_tokens or 0,
                        total_output_delta=result.total_output_tokens or 0,
                    )
                    status = "completed" if result.success else "error"
                    self.session_manager.update_session_fields(
                        persisted_session_id, {"status": status}
                    )
                except Exception as e:
                    logger.warning("Failed to update session record: %s", e)

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
            )

        # Build env vars with LLM proxy auth so the agent authenticates through
        # Open ACE (short-lived proxy token, never the raw API key).
        env = self._build_agent_env(adapter, cli_tool, user_id, session_id, model)

        # Build command
        # When resuming an established session, pass the real CLI session_id
        # so the adapter emits `--resume <id>`; otherwise let the CLI mint a new
        # session and capture its id from the control_response.
        resume_target = resume_session_id if (resume and resume_session_id) else session_id
        adapter_args = adapter.build_start_args(
            resume_target,
            project_path,
            model,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            resume=resume,
        )

        # Some adapters (e.g. ZCode) return a self-contained command whose first
        # element is an interpreter (``node <engine.cjs>``) rather than a PATH
        # executable. Use those args verbatim; otherwise resolve the executable
        # via PATH and prepend it.
        if adapter.provides_full_command():
            cmd = adapter_args
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
            cmd = [executable] + (adapter_args[1:] if len(adapter_args) > 1 else [])

        # Cross-user launch: the run-as wrapper chdir's as root then drops to
        # system_account (claude-code/qwen-code infer project root from cwd
        # and have no --cwd flag). Same-user runs verbatim with cwd=project.
        cmd, cwd = self._wrap_agent_cmd(cmd, project_path, system_account)

        logger.info("Launching local agent: %s", " ".join(cmd))

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
                session_id=(
                    ""
                    if self._uses_sidebar_session_source(cli_tool, workspace_type)
                    else session_id
                ),
                tracking_session_id=session_id,
                success=False,
                error=f"Failed to start process: {e}",
            )

        session = _LocalSession(
            session_id=session_id,
            process=process,
            cli_tool=cli_tool,
            allowed_tools=allowed_tools,
            project_path=project_path,
            encoded_project_path=self._encode_project_path(project_path),
            workflow_id=workflow_id,
            user_id=user_id,
            workspace_type=workspace_type,
            started_at_epoch=time.time(),
            milestone_id=milestone_id,
        )
        # For a resumed session the real CLI session_id is known up front; pin
        # it so sidebar detection reuses the existing record instead of guessing.
        if resume and resume_session_id:
            session.cli_session_id = resume_session_id
            session.persisted_session_id = resume_session_id
            session.sdk_initialized.set()
        self._local_sessions[session_id] = session

        # Persist PID to database for reliable cancel/pause
        if self._on_pid_registered:
            try:
                self._on_pid_registered(session_id, process.pid)
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
        if process.returncode is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

        self._local_sessions.pop(session_id, None)

        # Clear PID from database
        if self._on_pid_cleared:
            try:
                self._on_pid_cleared(session_id)
            except Exception as e:
                logger.warning("on_pid_cleared callback failed: %s", e)

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
                error="Failed to detect Claude sidebar session JSONL for autonomous task",
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
            return _build_agent_task_result(
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
            )

        return _build_agent_task_result(
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
        )

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
        try:
            self.session_manager.create_session(
                session_id=sid,
                session_type="workflow",
                title=f"Autonomous: {workflow_id[:8]}",
                tool_name=cli_tool,
                user_id=user_id,
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

        env = self._build_agent_env(adapter, cli_tool, user_id, session_id, model)
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
        cmd, cwd = self._wrap_agent_cmd(cmd, project_path, system_account)

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
        # orchestrator's stop/pause/cancel can reach the process. Created
        # before the collector so the collector's session_id_resolver can read
        # the real CLI session id off the tracker once session/create resolves.
        tracker = _LocalSession(
            session_id=session_id,
            process=process,
            cli_tool=cli_tool,
            project_path=project_path,
            encoded_project_path=self._encode_project_path(project_path),
            workflow_id=workflow_id,
            user_id=user_id,
            workspace_type=workspace_type,
            started_at_epoch=time.time(),
            milestone_id=milestone_id,
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
        if self._on_pid_registered:
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
    ) -> AgentTaskResult:
        """Run a CLI tool in single-shot mode for tools without stdin protocol.

        Uses ``adapter.build_single_shot_args`` to produce a self-contained
        command (e.g. ``codex exec --json "<prompt>"``) and captures output.
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

        args = adapter.build_single_shot_args(prompt, project_path, model)
        cmd = [executable] + (args[1:] if len(args) > 1 and args[0] == exe_name else args)
        env = self._build_agent_env(adapter, cli_tool, user_id, session_id, model)

        # Cross-user launch via run-as wrapper (single-shot CLIs also need
        # cwd=project; openclaw documents it relies on the caller's cwd).
        cmd, cwd = self._wrap_agent_cmd(cmd, project_path, system_account)

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

        stderr_text = (proc.stderr or "").strip()
        success = proc.returncode == 0
        error = None
        if not success:
            error = stderr_text or f"Command exited with code {proc.returncode}"

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

    def _run_remote(
        self,
        session_id: str,
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

        try:
            # Register a tracker so orchestrator can signal cancellation
            self._local_sessions[session_id] = _LocalSession(
                session_id=session_id,
                process=None,  # type: ignore[arg-type]
            )

            # Create remote session
            result = self.remote_session_manager.create_remote_session(
                machine_id=remote_machine_id,
                project_path=project_path,
                cli_tool=cli_tool,
                model=model,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
            )

            if not result.get("success"):
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error=result.get("error", "Failed to create remote session"),
                )

            # Send the prompt
            self.remote_session_manager.send_message(
                session_id=session_id,
                message=prompt,
            )

            # Poll until session completes
            import time

            if not self.session_manager:
                return AgentTaskResult(
                    session_id=session_id,
                    tracking_session_id=session_id,
                    success=False,
                    error="Session manager not available for remote session polling",
                )

            start_time = time.time()
            while time.time() - start_time < timeout:
                # Check if the session has been cancelled externally
                local_session = self._local_sessions.get(session_id)
                if local_session and local_session._stopped.is_set():
                    return AgentTaskResult(
                        session_id=session_id,
                        tracking_session_id=session_id,
                        success=False,
                        error="Remote session cancelled by orchestrator",
                    )

                session_data = self.session_manager.get_session(session_id)
                if session_data:
                    status = session_data.get("status", "active")
                    if status in ("completed", "stopped", "error", "exited"):
                        # Get messages
                        messages = []
                        if hasattr(self.session_manager, "get_messages"):
                            messages = self.session_manager.get_messages(session_id) or []

                        # Extract assistant text
                        assistant_events: list[dict] = []
                        for msg in messages:
                            if msg.get("role") == "assistant":
                                text = _extract_visible_text(msg.get("content", ""))
                                if text:
                                    assistant_events.append({"type": "assistant", "text": text})

                        return _build_agent_task_result(
                            session_id=session_id,
                            tracking_session_id=session_id,
                            event_log=assistant_events,
                            messages=messages,
                            total_tokens=session_data.get("total_tokens", 0),
                            total_input_tokens=session_data.get("total_input_tokens", 0),
                            total_output_tokens=session_data.get("total_output_tokens", 0),
                            request_count=session_data.get("request_count", 0),
                            success=status == "completed",
                            error=(
                                session_data.get("error_message") if status != "completed" else None
                            ),
                        )
                time.sleep(5)

            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Remote agent task timed out after {timeout}s",
            )

        except Exception as e:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=f"Remote execution error: {e}",
            )
        finally:
            # Clean up the remote session tracker
            self._local_sessions.pop(session_id, None)

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
            if session.process is None:
                return False
            session.process.stdin.write((payload + "\n").encode("utf-8"))
            session.process.stdin.flush()
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
                if session.process is None:
                    break
                line = session.process.stdout.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = line.strip()
                if not line:
                    continue

                session.output_lines.append(line)

                try:
                    if not session.persisted_session_id:
                        self._resolve_sidebar_session(session)
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
                        self._sync_sidebar_session_totals(session, status="active")
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
                        if msg_type == "initialized" or parsed.get("subtype") == "initialized":
                            self._capture_cli_session_id(session, parsed, "system.initialized")

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

                            if (
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
                                                f"Tool '{tool_name}' is not "
                                                "allowed in planning phase."
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

                except (json.JSONDecodeError, ValueError):
                    pass  # Non-JSON output, skip

        except (OSError, ValueError):
            pass
        finally:
            # If process exited without sending result, mark completed
            if not session.completed.is_set():
                session._stopped.wait(2.0)
                if session.process and session.process.returncode is not None:
                    session.completed.set()

    def _read_stderr(self, session: _LocalSession) -> None:
        """Read stderr from the subprocess."""
        try:
            while not session._stopped.is_set():
                if session.process is None:
                    break
                line = session.process.stderr.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                logger.debug("[%s/stderr] %s", session.session_id[:8], line.strip())
        except (OSError, ValueError):
            pass

    def stop_session(self, session_id: str) -> None:
        """Stop a running local session with SIGTERM + SIGKILL escalation.

        If the process is currently paused (SIGSTOP), it will be resumed
        first with SIGCONT so it can handle SIGTERM.
        """
        session = self._local_sessions.get(session_id)
        if not session or not session.process or session.process.returncode is not None:
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
        :meth:`resume_session` using SIGCONT.
        """
        session = self._local_sessions.get(session_id)
        if not session or not session.process or session.process.returncode is not None:
            return False
        if session._paused.is_set():
            return True
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
        if not session or not session.process or session.process.returncode is not None:
            return False
        if not session._paused.is_set():
            return True
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
