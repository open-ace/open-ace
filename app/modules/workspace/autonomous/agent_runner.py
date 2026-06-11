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
    persisted_session_ready: threading.Event = field(default_factory=threading.Event)


class AutonomousAgentRunner:
    """Runs agent tools autonomously and returns results."""

    def __init__(
        self,
        session_manager=None,
        remote_session_manager=None,
        server_url: str = "",
        activity_callback=None,
    ):
        """
        Args:
            session_manager: SessionManager for creating session records.
            remote_session_manager: RemoteSessionManager for remote execution.
            server_url: Open ACE server URL for proxy config.
            activity_callback: Optional callback ``(session_id, activity_dict)``
                invoked for each assistant/tool_use/usage event, enabling
                real-time streaming of agent activity to the frontend.
        """
        self.session_manager = session_manager
        self.remote_session_manager = remote_session_manager
        self.server_url = server_url or os.environ.get(
            "OPENACE_SERVER_URL", "http://localhost:5000"
        )
        self._activity_callback = activity_callback
        self._local_sessions: dict[str, _LocalSession] = {}

    @staticmethod
    def _uses_sidebar_session_source(cli_tool: str, workspace_type: str) -> bool:
        """Whether this task should resolve to the real sidebar Claude session."""
        return workspace_type == "local" and cli_tool == "claude-code"

    @staticmethod
    def _encode_project_path(project_path: str) -> str:
        """Match the encoded project path used by sidebar session history."""
        return project_path.replace("/", "-") if project_path.startswith("/") else project_path

    def _find_latest_claude_session_id(
        self,
        encoded_project_path: str,
        min_mtime_epoch: float,
    ) -> str:
        """Find the latest Claude JSONL session created for the active worktree."""
        if not encoded_project_path:
            return ""

        project_dir = Path.home() / ".claude" / "projects" / encoded_project_path
        if not project_dir.is_dir():
            return ""

        latest_file = None
        latest_mtime = min_mtime_epoch - 1
        try:
            for candidate in project_dir.glob("*.jsonl"):
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if stat.st_mtime >= min_mtime_epoch - 1 and stat.st_mtime >= latest_mtime:
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

        persisted_id = self._find_latest_claude_session_id(
            session.encoded_project_path,
            session.started_at_epoch,
        )
        if not persisted_id:
            return ""

        session.persisted_session_id = persisted_id
        session.persisted_session_ready.set()

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

        if self._activity_callback:
            self._activity_callback(
                persisted_id,
                {
                    "type": "session_resolved",
                    "session_id": persisted_id,
                },
            )

        return persisted_id

    def _sync_sidebar_session_totals(
        self, session: _LocalSession, status: str | None = None
    ) -> None:
        """Write the current local Claude usage into the persisted sidebar session."""
        if not self._uses_sidebar_session_source(session.cli_tool, session.workspace_type):
            return
        persisted_id = session.persisted_session_id or self._ensure_sidebar_session(session)
        if not persisted_id or not self.session_manager:
            return

        updates = {
            "request_count": session.request_count,
            "total_tokens": session.total_tokens,
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
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

        # Create wrapper sessions only for tools without a native sidebar session source.
        if self.session_manager and not uses_sidebar_session:
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
                )

            persisted_session_id = (
                result.session_id if uses_sidebar_session else (result.session_id or session_id)
            )

            # Persist session messages to database (Issue #776 Bug 1)
            if self.session_manager and persisted_session_id and workspace_type == "local":
                try:
                    self._persist_local_session_messages(persisted_session_id, result)
                except Exception as e:
                    logger.warning("Failed to persist session messages: %s", e)

            # Update session record
            if self.session_manager and persisted_session_id:
                try:
                    update_fields = {
                        "request_count": result.request_count,
                        "total_tokens": result.total_tokens,
                        "total_input_tokens": result.total_input_tokens,
                        "total_output_tokens": result.total_output_tokens,
                    }
                    if result.success:
                        update_fields["status"] = "completed"
                    else:
                        update_fields["status"] = "error"
                    self.session_manager.update_session_fields(persisted_session_id, update_fields)
                except Exception as e:
                    logger.warning("Failed to update session record: %s", e)

            return result

        except Exception as e:
            logger.error("Agent task failed: %s", e)
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

        # Expand project path
        project_path = os.path.expanduser(project_path)
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Build env vars (use direct env vars, no proxy for local autonomous)
        env = dict(os.environ)

        # Build command
        adapter_args = adapter.build_start_args(
            session_id,
            project_path,
            model,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
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
        )
        self._local_sessions[session_id] = session

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

        # Wait for completion or timeout
        completed = session.completed.wait(timeout=timeout)

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

    def _persist_local_session_messages(self, session_id: str, result: AgentTaskResult) -> None:
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
        if result.event_log:
            for event in result.event_log:
                if event.get("type") == "assistant":
                    self.session_manager.add_message(
                        session_id=session_id,
                        role="assistant",
                        content=event.get("text", ""),
                        model=event.get("model"),
                        metadata=(
                            {"message_id": event.get("message_id")}
                            if event.get("message_id")
                            else None
                        ),
                    )
                elif event.get("type") == "tool_use":
                    tool_input = event.get("tool_input", {})
                    self.session_manager.add_message(
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
                    )
                # usage events are metadata-only, not persisted as messages
        else:
            # Fallback: write as single assistant + individual tool messages
            if result.response_text:
                self.session_manager.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=result.response_text,
                )
            for tool_call in result.tool_calls:
                tool_info = tool_call.get("tool", {})
                tool_name = tool_info.get("name", "unknown")
                tool_input = tool_info.get("input", {})
                self.session_manager.add_message(
                    session_id=session_id,
                    role="tool",
                    content=(
                        json.dumps(tool_input)
                        if isinstance(tool_input, (dict, list))
                        else str(tool_input)
                    ),
                    metadata={"tool_name": tool_name},
                )

    def _send_sdk_init(self, session: _LocalSession) -> bool:
        """Send SDK initialize message."""
        init_msg = {
            "type": "control_request",
            "request_id": str(uuid.uuid4()),
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
                    self._ensure_sidebar_session(session)
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
                                },
                            )

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
        """Stop a running local session."""
        session = self._local_sessions.get(session_id)
        if session and session.process and session.process.returncode is None:
            try:
                os.killpg(os.getpgid(session.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            session._stopped.set()
            session.completed.set()
