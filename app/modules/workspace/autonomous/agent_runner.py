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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.modules.workspace.autonomous.models import AgentTaskResult

logger = logging.getLogger(__name__)

# Default timeout for agent tasks — configurable via env var (default 1 hour)
DEFAULT_TASK_TIMEOUT = int(os.environ.get("AUTONOMOUS_TASK_TIMEOUT", "3600"))


@dataclass
class _LocalSession:
    """Tracks a local CLI subprocess session."""

    session_id: str
    process: subprocess.Popen
    output_lines: list[str] = field(default_factory=list)
    assistant_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    completed: threading.Event = field(default_factory=threading.Event)
    error: Optional[str] = None
    _stopped: threading.Event = field(default_factory=threading.Event)
    _stdout_thread: Optional[threading.Thread] = None
    _stderr_thread: Optional[threading.Thread] = None


class AutonomousAgentRunner:
    """Runs agent tools autonomously and returns results."""

    def __init__(self, session_manager=None, remote_session_manager=None, server_url: str = ""):
        """
        Args:
            session_manager: SessionManager for creating session records.
            remote_session_manager: RemoteSessionManager for remote execution.
            server_url: Open ACE server URL for proxy config.
        """
        self.session_manager = session_manager
        self.remote_session_manager = remote_session_manager
        self.server_url = server_url or os.environ.get(
            "OPENACE_SERVER_URL", "http://localhost:5000"
        )
        self._local_sessions: dict[str, _LocalSession] = {}

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

        Returns:
            AgentTaskResult with response text, messages, tokens, etc.
        """
        session_id = str(uuid.uuid4())

        # Create session record
        if self.session_manager:
            try:
                self.session_manager.create_session(
                    session_id=session_id,
                    session_type=session_type,
                    title=f"Autonomous: {workflow_id[:8]}",
                    tool_name=cli_tool,
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
                )

            # Update session record
            if self.session_manager and result.success:
                try:
                    self.session_manager.update_session_fields(
                        session_id,
                        {
                            "status": "completed",
                            "total_tokens": result.total_tokens,
                            "total_input_tokens": result.total_input_tokens,
                            "total_output_tokens": result.total_output_tokens,
                        },
                    )
                except Exception as e:
                    logger.warning("Failed to update session record: %s", e)

            return result

        except Exception as e:
            logger.error("Agent task failed: %s", e)
            return AgentTaskResult(
                session_id=session_id,
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
    ) -> AgentTaskResult:
        """Run an agent task locally using a CLI subprocess."""
        import sys

        _remote_agent_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "remote-agent")
        )
        if _remote_agent_dir not in sys.path:
            sys.path.insert(0, _remote_agent_dir)
        from cli_adapters import get_adapter

        # Find executable
        adapter = get_adapter(cli_tool)
        exe_name = adapter.get_executable_name()
        executable = shutil.which(exe_name)
        if not executable:
            return AgentTaskResult(
                session_id=session_id,
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
                session_id=session_id,
                success=False,
                error=f"Failed to start process: {e}",
            )

        session = _LocalSession(session_id=session_id, process=process)
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

        if not completed:
            return AgentTaskResult(
                session_id=session_id,
                response_text=session.assistant_text,
                total_tokens=session.total_tokens,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                tool_calls=session.tool_calls,
                success=False,
                error=f"Agent task timed out after {timeout}s",
            )

        return AgentTaskResult(
            session_id=session_id,
            response_text=session.assistant_text,
            total_tokens=session.total_tokens,
            total_input_tokens=session.total_input_tokens,
            total_output_tokens=session.total_output_tokens,
            tool_calls=session.tool_calls,
            success=session.error is None,
            error=session.error,
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
    ) -> AgentTaskResult:
        """Run an agent task on a remote machine via RemoteSessionManager."""
        if not self.remote_session_manager:
            return AgentTaskResult(
                session_id=session_id,
                success=False,
                error="Remote session manager not available",
            )

        try:
            # Create remote session
            result = self.remote_session_manager.create_remote_session(
                machine_id=remote_machine_id,
                project_path=project_path,
                cli_tool=cli_tool,
                model=model,
                permission_mode=permission_mode,
            )

            if not result.get("success"):
                return AgentTaskResult(
                    session_id=session_id,
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
                    success=False,
                    error="Session manager not available for remote session polling",
                )

            start_time = time.time()
            while time.time() - start_time < timeout:
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
                            response_text=assistant_text,
                            messages=messages,
                            total_tokens=session_data.get("total_tokens", 0),
                            total_input_tokens=session_data.get("total_input_tokens", 0),
                            total_output_tokens=session_data.get("total_output_tokens", 0),
                            success=status == "completed",
                            error=(
                                session_data.get("error_message") if status != "completed" else None
                            ),
                        )
                time.sleep(5)

            return AgentTaskResult(
                session_id=session_id,
                success=False,
                error=f"Remote agent task timed out after {timeout}s",
            )

        except Exception as e:
            return AgentTaskResult(
                session_id=session_id,
                success=False,
                error=f"Remote execution error: {e}",
            )

    # ── Local helpers ──────────────────────────────────────────────

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
                    parsed = json.loads(line)
                    msg_type = parsed.get("type", "")

                    if msg_type == "assistant":
                        # Accumulate assistant text
                        msg = parsed.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    session.assistant_text += block.get("text", "")
                        elif isinstance(content, str):
                            session.assistant_text += content

                    elif msg_type == "tool_use":
                        session.tool_calls.append(parsed)

                    elif msg_type == "result":
                        # End of turn - extract usage
                        data = parsed.get("data", {})
                        usage = data.get("usage") or data.get("message", {}).get("usage", {})
                        if isinstance(usage, dict):
                            session.total_input_tokens += usage.get(
                                "input_tokens", usage.get("input", 0)
                            )
                            session.total_output_tokens += usage.get(
                                "output_tokens", usage.get("output", 0)
                            )
                        session.total_tokens = (
                            session.total_input_tokens + session.total_output_tokens
                        )
                        session.completed.set()

                    elif msg_type == "control_request":
                        # Auto-approve permissions in autonomous mode
                        req_id = parsed.get("request_id", "")
                        if req_id:
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
                if session.process.returncode is not None:
                    session.completed.set()

    def _read_stderr(self, session: _LocalSession) -> None:
        """Read stderr from the subprocess."""
        try:
            while not session._stopped.is_set():
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
        if session and session.process.returncode is None:
            try:
                os.killpg(os.getpgid(session.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            session._stopped.set()
            session.completed.set()
