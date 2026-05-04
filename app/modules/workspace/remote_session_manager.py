"""
Open ACE - Remote Session Manager

Manages remote workspace sessions: creation, message forwarding,
output collection, and session lifecycle. Integrates with existing
SessionManager for persistence and QuotaManager for enforcement.
"""

import contextlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.session_manager import SessionManager
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class RemoteSessionManager:
    """
    Manages remote workspace sessions.

    Handles:
    - Creating sessions that run on remote machines
    - Forwarding user messages to remote CLI processes
    - Collecting and buffering CLI output
    - Session lifecycle (start, stop, pause, resume)
    - Integration with SessionManager for persistence
    - Integration with QuotaManager for usage tracking
    """

    # Class-level buffer for accumulating assistant text across requests
    _assistant_text_buffer: dict[str, str] = {}
    _buffer_lock = threading.Lock()

    def __init__(self):
        self._session_manager = SessionManager()
        self._agent_manager = get_remote_agent_manager()
        self._api_key_proxy = APIKeyProxyService()
        # Cache of session permission modes to avoid unnecessary updates
        self._session_permission_modes: dict[str, str] = {}
        self._message_repo = MessageRepository()
        self._user_repo = UserRepository()
        # Cache user names to avoid repeated lookups
        self._user_name_cache: dict[int, str] = {}

    def create_remote_session(
        self,
        user_id: int,
        machine_id: str,
        project_path: str,
        model: Optional[str] = None,
        cli_tool: str = "qwen-code-cli",
        title: str = "",
        tenant_id: Optional[int] = None,
        permission_mode: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Create a new remote session.

        Args:
            user_id: User ID creating the session.
            machine_id: Target remote machine ID.
            project_path: Working directory on the remote machine.
            model: Optional model name to use.
            cli_tool: CLI tool to run (qwen-code-cli, claude-code, etc.).
            title: Optional session title.
            tenant_id: Tenant ID for API key lookup.

        Returns:
            Dict with session info or None on failure.
        """
        # Verify user has access to this machine
        if not self._agent_manager.check_user_access(machine_id, user_id):
            logger.warning(f"User {user_id} has no access to machine {machine_id}")
            return None

        # Check machine is connected
        if not self._agent_manager.is_connected(machine_id):
            logger.warning(f"Machine {machine_id} is not connected")
            return None

        # Get machine info
        machine = self._agent_manager.get_machine(machine_id)
        if not machine:
            return None

        # Determine provider based on CLI tool
        provider = self._cli_tool_to_provider(cli_tool)

        # Generate session ID
        session_id = str(uuid.uuid4())

        # Create session in SessionManager
        session = self._session_manager.create_session(
            tool_name=cli_tool,
            user_id=user_id,
            title=title or f"Remote: {machine.get('machine_name', machine_id[:8])}",
            host_name=machine.get("hostname", machine_id),
            model=model,
            project_path=project_path,
            session_id=session_id,
        )

        # Generate proxy token for this session
        effective_tenant_id = tenant_id or machine.get("tenant_id", 1)
        proxy_token = self._api_key_proxy.generate_proxy_token(
            user_id=user_id,
            session_id=session_id,
            tenant_id=effective_tenant_id,
            provider=provider,
        )

        # Bind session to machine in agent manager
        self._agent_manager.bind_session(session_id, machine_id)

        # Dispatch start_session command to remote agent
        command = {
            "type": "command",
            "command": "start_session",
            "session_id": session_id,
            "project_path": project_path,
            "model": model,
            "cli_tool": cli_tool,
            "proxy_token": proxy_token,
        }
        if permission_mode:
            command["permission_mode"] = permission_mode

        success = self._agent_manager.send_command(machine_id, command)
        if not success:
            for attempt in range(3):
                time.sleep(2)
                logger.info(
                    f"Retrying start_session (attempt {attempt+1}/3) for {machine_id[:8]}..."
                )
                success = self._agent_manager.send_command(machine_id, command)
                if success:
                    break
        if not success:
            self._agent_manager.unbind_session(session_id)
            self._session_manager.delete_session(session_id)
            logger.error(f"Failed to start remote session on {machine_id} after retries")
            return None

        # Update session with remote workspace info
        session.context["workspace_type"] = "remote"
        session.context["remote_machine_id"] = machine_id
        session.context["cli_tool"] = cli_tool
        self._session_manager.update_session(session)

        # Also update the dedicated columns (list_sessions reads from columns, not context JSON)
        try:
            from app.repositories.database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE agent_sessions SET workspace_type = %s, remote_machine_id = %s WHERE session_id = %s",
                    ("remote", machine_id, session_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update workspace_type column: {e}")

        logger.info(f"Created remote session {session_id} on machine {machine_id}")

        # Cache the initial permission mode (default if not specified)
        self._session_permission_modes[session_id] = permission_mode or "default"

        return {
            "session_id": session_id,
            "machine_id": machine_id,
            "status": "active",
            "project_path": project_path,
            "cli_tool": cli_tool,
            "model": model,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }

    def _get_machine_id(self, session_id: str) -> Optional[str]:
        """Get machine_id for a session, with DB fallback on restart.

        Tries in-memory mapping first, then falls back to agent_sessions
        table and session context JSON. Re-binds the mapping on recovery.
        """
        machine_id = self._agent_manager.get_machine_for_session(session_id)
        if machine_id:
            return machine_id

        # Fallback 1: session context JSON
        session = self._session_manager.get_session(session_id)
        if session and session.context:
            machine_id = session.context.get("remote_machine_id")

        # Fallback 2: dedicated DB column
        if not machine_id:
            try:
                from app.repositories.database import get_db_connection

                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT remote_machine_id FROM agent_sessions WHERE session_id = %s",
                        (session_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        machine_id = (
                            row[0]
                            if isinstance(row, (list, tuple))
                            else row.get("remote_machine_id")
                        )
            except Exception as e:
                logger.warning(f"DB fallback failed for session {session_id}: {e}")

        if machine_id:
            self._agent_manager.bind_session(session_id, machine_id)
            logger.info(f"Recovered machine binding for session {session_id[:8]}: {machine_id[:8]}")

        return machine_id

    def send_message(self, session_id: str, content: str, user_id: Optional[int] = None) -> bool:
        """
        Forward a user message to the remote CLI process.

        Args:
            session_id: Session ID.
            content: Message content.
            user_id: Optional user ID for verification.

        Returns:
            True if message was sent successfully.
        """
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            logger.warning(f"No machine bound for session {session_id}")
            return False

        # Store user message in session
        self._session_manager.add_message(
            session_id=session_id,
            role="user",
            content=content,
        )
        self._save_to_daily_messages(session_id, "user", content)

        command = {
            "type": "command",
            "command": "send_message",
            "session_id": session_id,
            "content": content,
        }

        return self._agent_manager.send_command(machine_id, command)

    def update_permission_mode(self, session_id: str, permission_mode: str) -> bool:
        """Send update_permission_mode command to the remote agent.

        Only sends the command if the permission mode has actually changed
        from the cached value to avoid unnecessary network traffic.
        """
        # Check cached permission mode - skip if unchanged
        cached_mode = self._session_permission_modes.get(session_id)
        # Treat None as equivalent to "default" for consistency
        new_mode = permission_mode or "default"

        # If no cached mode (e.g., after server restart), initialize it
        # and skip the update since we don't know if it's actually changed
        if cached_mode is None:
            self._session_permission_modes[session_id] = new_mode
            logger.debug(f"Initialized permission_mode cache for {session_id[:8]}: {new_mode}")
            return True

        current_mode = cached_mode or "default"

        if current_mode == new_mode:
            logger.debug(
                f"Skipping permission_mode update for {session_id[:8]}: unchanged ({new_mode})"
            )
            return True  # No change needed, consider it successful

        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "update_permission_mode",
            "session_id": session_id,
            "permission_mode": permission_mode,
        }
        success = self._agent_manager.send_command(machine_id, command)

        # Update cache on success
        if success:
            self._session_permission_modes[session_id] = permission_mode or "default"
            logger.info(f"Updated permission_mode for {session_id[:8]}: {new_mode}")

        return success

    def update_model(self, session_id: str, model: str) -> bool:
        """Switch the model of an active remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        # Update model in DB
        session = self._session_manager.get_session(session_id)
        if session:
            session.model = model
            self._session_manager.update_session(session)

        command = {
            "type": "command",
            "command": "update_model",
            "session_id": session_id,
            "model": model,
        }
        return self._agent_manager.send_command(machine_id, command)

    def abort_request(self, session_id: str) -> bool:
        """Abort the current in-progress request without stopping the session.

        Sends an interrupt signal (SIGINT/Ctrl+C) to the remote CLI process
        so the user can continue interacting with the session afterwards.
        """
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "abort_request",
            "session_id": session_id,
        }

        success = self._agent_manager.send_command(machine_id, command)
        if success:
            logger.info(f"Sent abort_request for session {session_id[:8]}")
        return success

    def stop_session(self, session_id: str) -> bool:
        """Stop a remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "stop_session",
            "session_id": session_id,
        }

        self._agent_manager.send_command(machine_id, command)

        # Complete session
        self._session_manager.complete_session(session_id)
        self._agent_manager.unbind_session(session_id)

        logger.info(f"Stopped remote session {session_id}")
        return True

    def pause_session(self, session_id: str) -> bool:
        """Pause a remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "pause_session",
            "session_id": session_id,
        }

        success = self._agent_manager.send_command(machine_id, command)
        if success:
            session = self._session_manager.get_session(session_id)
            if session:
                session.status = "paused"
                session.paused_at = datetime.utcnow()
                self._session_manager.update_session(session)

        return success

    def resume_session(self, session_id: str) -> bool:
        """Resume a paused remote session."""
        machine_id = self._get_machine_id(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "resume_session",
            "session_id": session_id,
        }

        success = self._agent_manager.send_command(machine_id, command)
        if success:
            session = self._session_manager.get_session(session_id)
            if session:
                session.status = "active"
                session.paused_at = None
                self._session_manager.update_session(session)

        return success

    def get_session_status(self, session_id: str) -> Optional[dict[str, Any]]:
        """Get remote session status and recent output."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return None

        machine_id = self._get_machine_id(session_id)
        output = self._agent_manager.get_buffered_output(session_id)

        # Include DB-stored messages for frontend replay on reconnect
        messages = []
        with contextlib.suppress(Exception):
            messages = self._session_manager.get_messages(session_id) or []

        return {
            "session_id": session_id,
            "status": session.status,
            "machine_id": machine_id,
            "project_path": session.project_path,
            "model": session.model,
            "total_tokens": session.total_tokens,
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
            "message_count": session.message_count,
            "request_count": session.request_count,
            "output": output,
            "messages": messages,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "paused_at": session.paused_at.isoformat() if session.paused_at else None,
        }

    def process_session_output(
        self, session_id: str, data: str, stream: str = "stdout", is_complete: bool = False
    ) -> None:
        """Process output received from a remote session."""
        output_entry = {
            "session_id": session_id,
            "data": data,
            "stream": stream,
            "is_complete": is_complete,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self._agent_manager.buffer_output(session_id, output_entry)

        if stream == "stdout":
            # Parse streaming JSON to accumulate assistant text per turn
            self._accumulate_assistant_text(session_id, data)

            # Flush on completion signal (process exit)
            if is_complete:
                self._flush_assistant_buffer(session_id)
        elif stream == "system" and is_complete and data.strip():
            self._session_manager.add_message(
                session_id=session_id,
                role="system",
                content=data,
            )
            self._save_to_daily_messages(session_id, "system", data)

    def _accumulate_assistant_text(self, session_id: str, data: str) -> None:
        """Parse streaming JSON and accumulate assistant text for a session."""
        if not data or not data.strip():
            return

        try:
            parsed = json.loads(data.strip())
        except (json.JSONDecodeError, ValueError):
            return

        if not isinstance(parsed, dict):
            return

        msg_type = parsed.get("type")

        if msg_type == "assistant":
            message = parsed.get("message", {})
            if not isinstance(message, dict):
                return
            content = message.get("content", [])
            if not isinstance(content, list):
                return

            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(text)

            if text_parts:
                combined = "".join(text_parts)
                with self._buffer_lock:
                    buf = self._assistant_text_buffer.get(session_id, "")
                    self._assistant_text_buffer[session_id] = buf + combined

        elif msg_type == "message":
            role = parsed.get("role")
            if role == "assistant":
                content = parsed.get("content")
                if isinstance(content, str) and content:
                    with self._buffer_lock:
                        buf = self._assistant_text_buffer.get(session_id, "")
                        self._assistant_text_buffer[session_id] = buf + content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                with self._buffer_lock:
                                    buf = self._assistant_text_buffer.get(session_id, "")
                                    self._assistant_text_buffer[session_id] = buf + text

        elif msg_type == "result":
            # End of turn — flush accumulated assistant text
            self._flush_assistant_buffer(session_id)

    def _flush_assistant_buffer(self, session_id: str) -> None:
        """Flush accumulated assistant text to DB."""
        with self._buffer_lock:
            text = self._assistant_text_buffer.pop(session_id, "")

        if text.strip():
            self._session_manager.add_message(
                session_id=session_id,
                role="assistant",
                content=text,
            )
            self._save_to_daily_messages(session_id, "assistant", text)

    def process_usage_report(
        self, session_id: str, tokens: dict[str, int], requests: int = 1
    ) -> None:
        """Process usage report from a remote agent."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return

        # Update session token counts
        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("output", 0)
        total = input_tokens + output_tokens

        session.total_tokens += total
        session.total_input_tokens += input_tokens
        session.total_output_tokens += output_tokens
        # request_count is managed by add_message() — avoid double counting
        self._session_manager.update_session(session)

        # Record usage in QuotaManager
        if session.user_id:
            try:
                from app.modules.governance.quota_manager import QuotaManager

                quota_mgr = QuotaManager()
                quota_mgr.record_usage(
                    user_id=session.user_id,
                    tokens=total,
                    requests=requests,
                )
            except Exception as e:
                logger.error(f"Failed to record quota usage: {e}")

            # Refresh user_daily_stats so quota checks see up-to-date data
            try:
                from app.repositories.daily_stats_repo import DailyStatsRepository

                daily_stats_repo = DailyStatsRepository()
                daily_stats_repo.refresh_stats()
            except Exception as e:
                logger.warning(f"Failed to refresh daily stats after usage report: {e}")

    def process_permission_request(self, session_id: str, control_request: dict) -> None:
        """
        Process a permission request from the remote agent.

        Buffers the permission request as a special output entry so it
        is delivered to the frontend via the SSE stream.  The entry uses
        a distinct ``permission_request`` type that the frontend can detect.
        """
        output_entry = {
            "session_id": session_id,
            "data": json.dumps(control_request),
            "stream": "permission",
            "is_complete": False,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self._agent_manager.buffer_output(session_id, output_entry)
        logger.info(
            "Buffered permission request for session %s: %s",
            session_id[:8],
            control_request.get("request", {}).get("subtype"),
        )

    def process_session_status_update(
        self, session_id: str, status: str, pid: Optional[int] = None
    ) -> None:
        """Process a session status update from a remote agent."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return

        if status in ("running", "active"):
            session.status = "active"
            session.paused_at = None
        elif status == "paused":
            session.status = "paused"
            if not session.paused_at:
                session.paused_at = datetime.utcnow()
        elif status == "stopped":
            # User-initiated stop — finalize the session
            session.status = "completed"
            session.paused_at = None
            self._agent_manager.unbind_session(session_id)
            self._agent_manager.mark_session_ended(session_id)
            # Clean up permission mode cache
            self._session_permission_modes.pop(session_id, None)
        elif status in ("completed", "exited"):
            # CLI exited after a response — keep session active so the user
            # can send follow-up messages (executor restarts the CLI).
            session.status = "active"
        elif status == "error":
            session.status = "error"
            session.paused_at = None
            self._agent_manager.mark_session_ended(session_id)
            # Clean up permission mode cache
            self._session_permission_modes.pop(session_id, None)

        self._session_manager.update_session(session)

    def _cli_tool_to_provider(self, cli_tool: str) -> str:
        """Map CLI tool name to LLM provider name."""
        mapping = {
            "qwen-code-cli": "openai",
            "claude-code": "anthropic",
            "openclaw": "openai",
        }
        return mapping.get(cli_tool, "openai")

    def _get_user_name(self, user_id: Optional[int]) -> str:
        """Get user display name with caching."""
        if not user_id:
            return ""
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        try:
            user = self._user_repo.get_user_by_id(user_id)
            name = user.get("display_name") or user.get("username", "") if user else ""
            self._user_name_cache[user_id] = name
            return name
        except Exception:
            return ""

    def _save_to_daily_messages(
        self, session_id: str, role: str, content: str, tokens_used: int = 0
    ) -> None:
        """Mirror a message to daily_messages so it appears in manage pages."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return
        try:
            now = datetime.utcnow()
            self._message_repo.save_message(
                date=now.strftime("%Y-%m-%d"),
                tool_name=session.tool_name or "unknown",
                message_id=str(uuid.uuid4()),
                role=role,
                host_name=session.host_name or "remote",
                content=content,
                full_entry=json.dumps(
                    {"session_id": session_id, "role": role, "content": content},
                    ensure_ascii=False,
                ),
                tokens_used=tokens_used,
                model=session.model,
                timestamp=now.isoformat(),
                sender_id=str(session.user_id) if session.user_id else None,
                sender_name=self._get_user_name(session.user_id),
                message_source="remote_workspace",
                agent_session_id=session_id,
                conversation_id=session_id,
            )
        except Exception as e:
            logger.warning(f"Failed to mirror message to daily_messages for {session_id[:8]}: {e}")


# Global singleton
_remote_session_manager: Optional["RemoteSessionManager"] = None


def get_remote_session_manager() -> "RemoteSessionManager":
    """Get the global RemoteSessionManager instance."""
    global _remote_session_manager
    if _remote_session_manager is None:
        _remote_session_manager = RemoteSessionManager()
    return _remote_session_manager
