#!/usr/bin/env python3
"""
Open ACE - Remote Session Manager

Manages remote workspace sessions: creation, message forwarding,
output collection, and session lifecycle. Integrates with existing
SessionManager for persistence and QuotaManager for enforcement.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.session_manager import SessionManager, SessionType

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

    def __init__(self):
        self._session_manager = SessionManager()
        self._agent_manager = get_remote_agent_manager()
        self._api_key_proxy = APIKeyProxyService()

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
    ) -> Optional[Dict[str, Any]]:
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
            # Clean up
            self._agent_manager.unbind_session(session_id)
            self._session_manager.delete_session(session_id)
            logger.error(f"Failed to start remote session on {machine_id}")
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

        return {
            "session_id": session_id,
            "machine_id": machine_id,
            "status": "active",
            "project_path": project_path,
            "cli_tool": cli_tool,
            "model": model,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }

    def send_message(self, session_id: str, content: str,
                     user_id: Optional[int] = None) -> bool:
        """
        Forward a user message to the remote CLI process.

        Args:
            session_id: Session ID.
            content: Message content.
            user_id: Optional user ID for verification.

        Returns:
            True if message was sent successfully.
        """
        machine_id = self._agent_manager.get_machine_for_session(session_id)
        if not machine_id:
            logger.warning(f"No machine bound for session {session_id}")
            return False

        # Store user message in session
        self._session_manager.add_message(
            session_id=session_id,
            role="user",
            content=content,
        )

        command = {
            "type": "command",
            "command": "send_message",
            "session_id": session_id,
            "content": content,
        }

        return self._agent_manager.send_command(machine_id, command)

    def update_permission_mode(self, session_id: str, permission_mode: str) -> bool:
        """Send update_permission_mode command to the remote agent."""
        machine_id = self._agent_manager.get_machine_for_session(session_id)
        if not machine_id:
            return False

        command = {
            "type": "command",
            "command": "update_permission_mode",
            "session_id": session_id,
            "permission_mode": permission_mode,
        }
        return self._agent_manager.send_command(machine_id, command)

    def update_model(self, session_id: str, model: str) -> bool:
        """Switch the model of an active remote session."""
        machine_id = self._agent_manager.get_machine_for_session(session_id)
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

    def stop_session(self, session_id: str) -> bool:
        """Stop a remote session."""
        machine_id = self._agent_manager.get_machine_for_session(session_id)
        if not machine_id:
            # Session might already be unbound, try to get from session data
            session = self._session_manager.get_session(session_id)
            if session and session.context:
                machine_id = session.context.get("remote_machine_id")
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
        machine_id = self._agent_manager.get_machine_for_session(session_id)
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
                self._session_manager.update_session(session)

        return success

    def resume_session(self, session_id: str) -> bool:
        """Resume a paused remote session."""
        machine_id = self._agent_manager.get_machine_for_session(session_id)
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
                self._session_manager.update_session(session)

        return success

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get remote session status and recent output."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return None

        machine_id = self._agent_manager.get_machine_for_session(session_id)
        output = self._agent_manager.get_buffered_output(session_id)

        return {
            "session_id": session_id,
            "status": session.status,
            "machine_id": machine_id,
            "project_path": session.project_path,
            "model": session.model,
            "total_tokens": session.total_tokens,
            "message_count": session.message_count,
            "request_count": session.request_count,
            "output": output,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }

    def process_session_output(self, session_id: str, data: str,
                                stream: str = "stdout",
                                is_complete: bool = False) -> None:
        """Process output received from a remote session."""
        output_entry = {
            "session_id": session_id,
            "data": data,
            "stream": stream,
            "is_complete": is_complete,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self._agent_manager.buffer_output(session_id, output_entry)

        # If this is a complete assistant message, store it
        if is_complete and stream == "stdout":
            self._session_manager.add_message(
                session_id=session_id,
                role="assistant",
                content=data,
            )

    def process_usage_report(self, session_id: str,
                             tokens: Dict[str, int],
                             requests: int = 1) -> None:
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
        session.request_count += requests
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

    def process_permission_request(self, session_id: str,
                                    control_request: dict) -> None:
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

    def process_session_status_update(self, session_id: str, status: str,
                                      pid: Optional[int] = None) -> None:
        """Process a session status update from a remote agent."""
        session = self._session_manager.get_session(session_id)
        if not session:
            return

        if status in ("running", "active"):
            session.status = "active"
        elif status == "stopped":
            # User-initiated stop — finalize the session
            session.status = "completed"
            self._agent_manager.unbind_session(session_id)
            self._agent_manager.mark_session_ended(session_id)
        elif status in ("completed", "exited"):
            # CLI exited after a response — keep session active so the user
            # can send follow-up messages (executor restarts the CLI).
            session.status = "active"
        elif status == "error":
            session.status = "error"
            self._agent_manager.mark_session_ended(session_id)

        self._session_manager.update_session(session)

    def _cli_tool_to_provider(self, cli_tool: str) -> str:
        """Map CLI tool name to LLM provider name."""
        mapping = {
            "qwen-code-cli": "openai",
            "claude-code": "anthropic",
            "openclaw": "openai",
        }
        return mapping.get(cli_tool, "openai")
