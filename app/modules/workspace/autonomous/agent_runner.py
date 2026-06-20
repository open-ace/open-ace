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
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.modules.workspace.autonomous.models import AgentTaskResult

logger = logging.getLogger(__name__)


# Cached import — populated on first call to _run_local() which adds remote-agent to sys.path
_extract_stream_usage: Any = None


def _ensure_usage_parser():
    """Import extract_stream_usage once remote-agent is on sys.path."""
    global _extract_stream_usage
    if _extract_stream_usage is None:
        try:
            from cli_adapters.usage_parser import extract_stream_usage
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

        _extract_stream_usage = extract_stream_usage


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
    _paused: threading.Event = field(default_factory=threading.Event)  # set when SIGSTOPed


# Top-level keys that indicate a JSON object is a leaked tool-call blob
# rather than genuine assistant prose. ZCode sometimes streams tool
# invocations as text content before emitting a structured tool.* event.
_TOOL_JSON_KEYS = frozenset({"tool", "command", "subagent_type", "file_path"})


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


class _ZcodeResultCollector:
    """Collects assistant text, tool calls, and token usage from ZCode app-server.

    Acts as the ``output_callback`` / ``usage_callback`` for
    ``ZCodeAppServerSession``, translating ZCode Protocol notifications into
    the same data ``_LocalSession`` accumulates for Claude SDK sessions.
    """

    def __init__(self) -> None:
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
            text = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
            elif isinstance(content, str):
                text = content
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

        # Tool events: ZCode emits tool.<name> with data payload.
        # Normalize to the Claude SDK tool_use shape for persistence.
        elif msg_type.startswith("tool."):
            tool_name = msg_type.split(".", 1)[1]
            payload = parsed.get("data", {}) or {}
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
            "OPENACE_SERVER_URL", "http://localhost:5000"
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
    ) -> str:
        """Find the latest Claude JSONL session created for the active worktree.

        This is best-effort discovery based on the encoded worktree path and file mtime.
        It assumes only one local autonomous Claude task is creating a new session for a
        given worktree at a time; concurrent tasks on the same worktree can still race.
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
                if (
                    stat.st_mtime >= min_mtime_epoch - SESSION_DETECTION_GRACE_SECONDS
                    and stat.st_mtime >= latest_mtime
                ):
                    latest_file = candidate
                    latest_mtime = stat.st_mtime
        except OSError:
            return ""

        return latest_file.stem if latest_file else ""

    def _ensure_sidebar_session(self, session: _LocalSession) -> str:
        """Resolve and create the single persisted sidebar session for local Claude."""
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
            persisted_id = self._find_latest_claude_session_id(
                session.encoded_project_path,
                session.started_at_epoch,
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
                self.session_manager.create_session(
                    session_id=persisted_id,
                    session_type="chat",
                    title=f"claude - {persisted_id[:8]}",
                    tool_name="claude",
                    user_id=session.user_id,
                    project_path=session.encoded_project_path,
                    workspace_type=session.workspace_type,
                    remote_machine_id=session.remote_machine_id,
                    context={"workflow_id": session.workflow_id},
                )
            except Exception as e:
                logger.warning("Failed to create resolved sidebar session: %s", e)
                return ""

        session.persisted_session_id = persisted_id
        if self._activity_callback:
            self._activity_callback(
                persisted_id,
                {
                    "type": "session_resolved",
                    "session_id": persisted_id,
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
        """Write the current local Claude usage into the persisted sidebar session."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return
        persisted_id = session.persisted_session_id or self._resolve_sidebar_session(session)
        if not persisted_id or not self.session_manager:
            return

        # NOTE: request_count / total_tokens / in-out are owned by
        # increment_session_usage at finish time (per-call delta), NOT written
        # here. Writing them here during streaming (ASSIGN) would double-count
        # against the finish increment (#1007 review).
        updates = {
            "project_path": session.encoded_project_path,
        }
        if session.user_id:
            updates["user_id"] = session.user_id
        if status:
            updates["status"] = status

        try:
            self.session_manager.update_session_fields(persisted_id, updates)
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
        creates_session_late = uses_sidebar_session or cli_tool in _APPSERVER_TOOLS

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
                )

            persisted_session_id = (
                result.session_id if uses_sidebar_session else (result.session_id or session_id)
            )

            # Persist session messages to database (Issue #776 Bug 1)
            if self.session_manager and persisted_session_id and workspace_type == "local":
                try:
                    self._persist_local_session_messages(persisted_session_id, result, milestone_id)
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
                session_id="" if uses_sidebar_session else session_id,
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
        Path(project_path).mkdir(parents=True, exist_ok=True)

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
            )

        # Build env vars (use direct env vars, no proxy for local autonomous)
        env = dict(os.environ)

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

        logger.info("Launching local agent: %s", " ".join(cmd))

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_path,
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
            return AgentTaskResult(
                session_id="",
                tracking_session_id=session_id,
                response_text=session.assistant_text,
                total_tokens=session.total_tokens,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                request_count=session.request_count,
                tool_calls=session.tool_calls,
                success=False,
                error="Failed to detect Claude sidebar session JSONL for autonomous task",
                event_log=session.event_log,
            )

        if not completed:
            return AgentTaskResult(
                session_id=resolved_session_id,
                tracking_session_id=session_id,
                response_text=session.assistant_text,
                total_tokens=session.total_tokens,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                request_count=session.request_count,
                tool_calls=session.tool_calls,
                success=False,
                error=f"Agent task timed out after {timeout}s",
                event_log=session.event_log,
            )

        return AgentTaskResult(
            session_id=resolved_session_id,
            tracking_session_id=session_id,
            response_text=session.assistant_text,
            total_tokens=session.total_tokens,
            total_input_tokens=session.total_input_tokens,
            total_output_tokens=session.total_output_tokens,
            request_count=session.request_count,
            tool_calls=session.tool_calls,
            success=session.error is None,
            error=session.error,
            event_log=session.event_log,
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

        env = dict(os.environ)
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
        logger.info("Launching ZCode app-server (mode=%s): %s", zcode_mode, " ".join(cmd))

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_path,
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

        collector = _ZcodeResultCollector()

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

        # Register PID + wrap into a _LocalSession-compatible tracker so the
        # orchestrator's stop/pause/cancel can reach the process.
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

            if not zc_session.send_message(prompt):
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
            return AgentTaskResult(
                session_id=cli_sid,
                tracking_session_id=session_id,
                response_text=collector.assistant_text,
                total_tokens=collector.total_tokens,
                total_input_tokens=collector.input_tokens,
                total_output_tokens=collector.output_tokens,
                request_count=collector.request_count,
                tool_calls=collector.tool_calls,
                success=False,
                error=f"Agent task timed out after {timeout}s",
                event_log=collector.event_log,
            )

        return AgentTaskResult(
            session_id=cli_sid,
            tracking_session_id=session_id,
            response_text=collector.assistant_text,
            total_tokens=collector.total_tokens,
            total_input_tokens=collector.input_tokens,
            total_output_tokens=collector.output_tokens,
            request_count=collector.request_count,
            tool_calls=collector.tool_calls,
            success=collector.error is None,
            error=collector.error,
            event_log=collector.event_log,
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
        env = dict(os.environ)

        logger.info("Launching single-shot agent (%s): %s", cli_tool, " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=project_path,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
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

        response_text = ""
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        event_log = []

        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            event_log.append(line)
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                response_text += line + "\n"
                continue

            # Extract text content from common JSON shapes
            if isinstance(parsed, dict):
                text = (
                    parsed.get("response")
                    or parsed.get("text")
                    or parsed.get("content")
                    or parsed.get("output")
                )
                if isinstance(text, str):
                    response_text += text + "\n"
                usage = _extract_stream_usage(cli_tool, parsed)
                if usage:
                    input_tokens += usage["input"]
                    output_tokens += usage["output"]

        total_tokens = input_tokens + output_tokens

        stderr_text = (proc.stderr or "").strip()
        success = proc.returncode == 0
        error = None
        if not success:
            error = stderr_text or f"Command exited with code {proc.returncode}"

        return AgentTaskResult(
            session_id=session_id,
            tracking_session_id=session_id,
            response_text=response_text.strip(),
            total_tokens=total_tokens,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            request_count=1,
            success=success,
            error=error,
            event_log=event_log,
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
                        assistant_text = ""
                        for msg in messages:
                            if msg.get("role") == "assistant":
                                assistant_text += msg.get("content", "")

                        return AgentTaskResult(
                            session_id=session_id,
                            tracking_session_id=session_id,
                            response_text=assistant_text,
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
    ) -> None:
        """Write agent conversation to session_messages preserving order.

        Uses the ordered event_log from _LocalSession to maintain the actual
        interleaving (assistant -> tool_use -> assistant -> ...).
        Falls back to separate assistant_text + tool_calls if event_log is empty
        (e.g. remote sessions or legacy code path).

        Called after the agent task finishes, before the session status is
        updated.  Errors are caught by the caller and do not affect the
        main workflow.
        """
        # Prefer ordered event log for accurate message interleaving
        # count_usage=False: the agent runner owns request_count/total_tokens via
        # increment_session_usage at finish time; counting here too would
        # double-count (#1003 / #1007 review).
        if result.event_log:
            for event in result.event_log:
                if event.get("type") == "assistant":
                    self.session_manager.add_message(
                        session_id=session_id,
                        milestone_id=milestone_id,
                        role="assistant",
                        content=event.get("text", ""),
                        model=event.get("model"),
                        metadata=(
                            {"message_id": event.get("message_id")}
                            if event.get("message_id")
                            else None
                        ),
                        count_usage=False,
                    )
                elif event.get("type") == "tool_use":
                    tool_input = event.get("tool_input", {})
                    self.session_manager.add_message(
                        session_id=session_id,
                        milestone_id=milestone_id,
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
                        count_usage=False,
                    )
                # usage events are metadata-only, not persisted as messages
        else:
            # Fallback: write as single assistant + individual tool messages
            if result.response_text:
                self.session_manager.add_message(
                    session_id=session_id,
                    milestone_id=milestone_id,
                    role="assistant",
                    content=result.response_text,
                    count_usage=False,
                )
            for tool_call in result.tool_calls:
                tool_info = tool_call.get("tool", {})
                tool_name = tool_info.get("name", "unknown")
                tool_input = tool_info.get("input", {})
                self.session_manager.add_message(
                    session_id=session_id,
                    milestone_id=milestone_id,
                    role="tool",
                    content=(
                        json.dumps(tool_input)
                        if isinstance(tool_input, (dict, list))
                        else str(tool_input)
                    ),
                    metadata={"tool_name": tool_name},
                    count_usage=False,
                )

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
                        text_delta = ""
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text_delta = block.get("text", "")
                                    session.assistant_text += text_delta
                        elif isinstance(content, str):
                            text_delta = content
                            session.assistant_text += content
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
                                session.persisted_session_id or session.session_id,
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
                                session.persisted_session_id or session.session_id,
                                {
                                    "type": "tool_use",
                                    "tool_name": tool_info.get("name", "unknown"),
                                    "tool_input": str(tool_info.get("input", ""))[:200],
                                },
                            )

                    elif msg_type == "result":
                        # End of turn - extract usage via shared parser
                        usage = _extract_stream_usage(session.cli_tool, parsed)
                        if usage:
                            session.total_input_tokens += usage["input"]
                            session.total_output_tokens += usage["output"]
                        session.total_tokens = (
                            session.total_input_tokens + session.total_output_tokens
                        )
                        session.request_count += 1
                        self._sync_sidebar_session_totals(session, status="active")
                        session.completed.set()
                        # Emit usage activity for real-time token display
                        if self._activity_callback:
                            self._activity_callback(
                                session.persisted_session_id or session.session_id,
                                {
                                    "type": "usage",
                                    "total_tokens": session.total_tokens,
                                    "total_input_tokens": session.total_input_tokens,
                                    "total_output_tokens": session.total_output_tokens,
                                    "request_count": session.request_count,
                                },
                            )

                    elif msg_type == "control_response":
                        # Capture the real Claude session_id from the SDK
                        # initialize response (mirrors remote executor.py).
                        resp = parsed.get("response", {}) or {}
                        if (
                            session.init_request_id
                            and resp.get("request_id") == session.init_request_id
                        ):
                            if resp.get("subtype") == "success":
                                inner = resp.get("response", {}) or {}
                                cli_sid = inner.get("session_id", "")
                                if cli_sid and not session.cli_session_id:
                                    session.cli_session_id = cli_sid
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
                                    "Denied tool '%s' for session %s " "(not in allowed list)",
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
