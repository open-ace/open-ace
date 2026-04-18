#!/usr/bin/env python3
"""
Open ACE - Remote Agent Manager

Manages WebSocket connections to remote agents, heartbeat monitoring,
command dispatching, and message routing for remote workspace sessions.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Union

from app.repositories.database import DB_PATH, is_postgresql, get_database_url

logger = logging.getLogger(__name__)


def _param() -> str:
    return "?" if not is_postgresql() else "%s"


def _params(count: int) -> str:
    p = _param()
    return ", ".join([p] * count)


class RemoteAgentManager:
    """
    Singleton manager for remote agent WebSocket connections.

    Tracks active agent connections, monitors heartbeats, dispatches
    commands to agents, and routes agent responses back to sessions.
    """

    HEARTBEAT_TIMEOUT_SECONDS = 180  # 3 minutes without heartbeat = offline
    HEARTBEAT_CHECK_INTERVAL = 60  # Check every 60 seconds

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        # Active WebSocket connections: {machine_id: websocket_connection}
        self._connections: Dict[str, Any] = {}
        # Session to machine mapping: {session_id: machine_id}
        self._session_machines: Dict[str, str] = {}
        # Output buffers: {session_id: [output_lines]}
        self._output_buffers: Dict[str, List[Dict]] = {}
        # Registration tokens: {token: {tenant_id, created_by, created_at}}
        self._registration_tokens: Dict[str, Dict] = {}
        # Command queues for HTTP-mode agents: {machine_id: [commands]}
        self._command_queues: Dict[str, List[Dict]] = {}
        # Lock for thread safety
        self._lock = threading.Lock()
        self._ensure_tables()
        self._start_heartbeat_monitor()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                url = get_database_url()
                return psycopg2.connect(url, cursor_factory=RealDictCursor)
            except ImportError:
                raise ImportError("psycopg2 is required for PostgreSQL")
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ensure_tables(self) -> None:
        """Ensure required tables exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS remote_machines (
                id {id_type},
                machine_id TEXT NOT NULL UNIQUE,
                machine_name TEXT NOT NULL,
                hostname TEXT,
                os_type TEXT,
                os_version TEXT,
                ip_address TEXT,
                status TEXT DEFAULT 'offline',
                agent_version TEXT,
                capabilities TEXT,
                cli_path TEXT,
                work_dir TEXT,
                tenant_id INTEGER,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_heartbeat TIMESTAMP
            )
        """
        )

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS machine_assignments (
                id {id_type},
                machine_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                permission TEXT DEFAULT 'user',
                granted_by INTEGER,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(machine_id, user_id)
            )
        """
        )

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_remote_machines_machine_id ON remote_machines(machine_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_remote_machines_status ON remote_machines(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_machine_assignments_user_id ON machine_assignments(user_id)")

        conn.commit()
        conn.close()

    def _start_heartbeat_monitor(self) -> None:
        """Start background thread for heartbeat monitoring."""
        def monitor():
            while True:
                try:
                    self._check_heartbeats()
                except Exception as e:
                    logger.error(f"Heartbeat monitor error: {e}")
                time.sleep(self.HEARTBEAT_CHECK_INTERVAL)

        thread = threading.Thread(target=monitor, daemon=True, name="heartbeat-monitor")
        thread.start()
        logger.info("Heartbeat monitor started")

    def _check_heartbeats(self) -> None:
        """Check for stale heartbeats and mark machines offline."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cutoff = datetime.utcnow() - timedelta(seconds=self.HEARTBEAT_TIMEOUT_SECONDS)

        cursor.execute(
            f"""
            UPDATE remote_machines
            SET status = 'offline', updated_at = {_param()}
            WHERE status = 'online' AND last_heartbeat < {_param()}
        """,
            (datetime.utcnow().isoformat(), cutoff.isoformat()),
        )

        updated = cursor.rowcount
        if updated > 0:
            logger.info(f"Marked {updated} machines offline due to heartbeat timeout")

        conn.commit()
        conn.close()

    # ==================== Registration ====================

    def create_registration_token(self, tenant_id: int, created_by: int) -> str:
        """
        Generate a one-time registration token for a new machine.

        Args:
            tenant_id: Tenant ID to associate the machine with.
            created_by: User ID who initiated registration.

        Returns:
            Registration token string.
        """
        token = str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")
        with self._lock:
            self._registration_tokens[token] = {
                "tenant_id": tenant_id,
                "created_by": created_by,
                "created_at": datetime.utcnow().isoformat(),
            }
        logger.info(f"Created registration token for tenant {tenant_id}")
        return token

    def register_machine(
        self,
        registration_token: str,
        machine_id: str,
        machine_name: str,
        hostname: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        capabilities: Optional[Dict] = None,
        agent_version: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Register a new remote machine using a registration token.

        Args:
            registration_token: One-time registration token.
            machine_id: UUID generated by the agent.
            machine_name: Display name for the machine.
            hostname: Machine hostname.
            os_type: Operating system type.
            os_version: OS version string.
            capabilities: Dict with machine capabilities.
            agent_version: Agent software version.
            ip_address: Client IP address.

        Returns:
            Dict with machine info or None if token invalid.
        """
        with self._lock:
            token_info = self._registration_tokens.pop(registration_token, None)

        if not token_info:
            logger.warning("Invalid or expired registration token")
            return None

        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        try:
            cursor.execute(
                f"""
                INSERT INTO remote_machines
                (machine_id, machine_name, hostname, os_type, os_version, ip_address,
                 status, agent_version, capabilities, tenant_id, created_by, created_at, updated_at, last_heartbeat)
                VALUES ({_params(14)})
            """,
                (
                    machine_id,
                    machine_name,
                    hostname,
                    os_type,
                    os_version,
                    ip_address,
                    "online",
                    agent_version,
                    json.dumps(capabilities) if capabilities else None,
                    token_info["tenant_id"],
                    token_info["created_by"],
                    now,
                    now,
                    now,
                ),
            )

            conn.commit()

            # Also auto-assign the creator
            if is_postgresql():
                cursor.execute(
                    f"""
                    INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at)
                    VALUES ({_params(5)})
                    ON CONFLICT (machine_id, user_id) DO NOTHING
                """,
                    (machine_id, token_info["created_by"], "admin", token_info["created_by"], now),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT OR IGNORE INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at)
                    VALUES ({_params(5)})
                """,
                (machine_id, token_info["created_by"], "admin", token_info["created_by"], now),
            )
            conn.commit()

            return {
                "machine_id": machine_id,
                "machine_name": machine_name,
                "status": "online",
                "tenant_id": token_info["tenant_id"],
            }
        except Exception as e:
            logger.error(f"Failed to register machine: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    def deregister_machine(self, machine_id: str) -> bool:
        """Remove a machine and its assignments."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"DELETE FROM machine_assignments WHERE machine_id = {_param()}", (machine_id,))
        cursor.execute(f"DELETE FROM remote_machines WHERE machine_id = {_param()}", (machine_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        # Close active connection
        with self._lock:
            self._connections.pop(machine_id, None)

        return success

    # ==================== Connection Management ====================

    def register_connection(self, machine_id: str, websocket) -> None:
        """Register an active WebSocket connection from a remote agent."""
        with self._lock:
            self._connections[machine_id] = websocket

        # Update status to online
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE remote_machines
            SET status = 'online', last_heartbeat = {_param()}, updated_at = {_param()}
            WHERE machine_id = {_param()}
        """,
            (datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), machine_id),
        )
        conn.commit()
        conn.close()

        logger.info(f"Agent connected: {machine_id}")

    def unregister_connection(self, machine_id: str) -> None:
        """Unregister an agent WebSocket connection."""
        with self._lock:
            self._connections.pop(machine_id, None)

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE remote_machines SET status = 'offline', updated_at = {_param()}
            WHERE machine_id = {_param()}
        """,
            (datetime.utcnow().isoformat(), machine_id),
        )
        conn.commit()
        conn.close()

        logger.info(f"Agent disconnected: {machine_id}")

    def is_connected(self, machine_id: str) -> bool:
        """Check if a machine has an active WebSocket connection."""
        return machine_id in self._connections

    # ==================== Command Dispatch ====================

    def send_command(self, machine_id: str, command: Dict[str, Any]) -> bool:
        """
        Send a command to a remote agent via WebSocket.

        If no WebSocket is available, queues the command for HTTP polling.

        Args:
            machine_id: Target machine ID.
            command: Command dict with 'type', 'command', etc.

        Returns:
            True if command was sent or queued successfully.
        """
        with self._lock:
            ws = self._connections.get(machine_id)

        if not ws:
            # No WebSocket — queue command for HTTP polling
            if machine_id in self._connections:
                # Connected via HTTP (ws is None)
                with self._lock:
                    if machine_id not in self._command_queues:
                        self._command_queues[machine_id] = []
                    self._command_queues[machine_id].append(command)
                logger.info(f"Queued command for HTTP-mode agent {machine_id}")
                return True
            logger.warning(f"No active connection for machine {machine_id}")
            return False

        try:
            ws.send(json.dumps(command))
            return True
        except Exception as e:
            logger.error(f"Failed to send command to {machine_id}: {e}")
            self.unregister_connection(machine_id)
            return False

    def get_pending_commands(self, machine_id: str) -> List[Dict]:
        """Get and clear pending commands for an HTTP-mode agent."""
        with self._lock:
            return self._command_queues.pop(machine_id, [])

    # ==================== Session Tracking ====================

    def bind_session(self, session_id: str, machine_id: str) -> None:
        """Bind a session to a machine."""
        with self._lock:
            self._session_machines[session_id] = machine_id
            self._output_buffers[session_id] = []

    def unbind_session(self, session_id: str) -> None:
        """Remove session binding."""
        with self._lock:
            self._session_machines.pop(session_id, None)
            self._output_buffers.pop(session_id, None)

    def get_machine_for_session(self, session_id: str) -> Optional[str]:
        """Get the machine ID for a session."""
        return self._session_machines.get(session_id)

    # ==================== Output Buffering ====================

    def buffer_output(self, session_id: str, output: Dict[str, Any]) -> None:
        """Buffer output from a remote session."""
        with self._lock:
            if session_id not in self._output_buffers:
                self._output_buffers[session_id] = []
            self._output_buffers[session_id].append(output)

    def get_buffered_output(self, session_id: str, after_index: int = 0) -> List[Dict]:
        """Get buffered output for a session after a given index."""
        with self._lock:
            buf = self._output_buffers.get(session_id, [])
            return buf[after_index:]

    # ==================== Heartbeat ====================

    def process_heartbeat(self, machine_id: str, status: str = "idle",
                          active_sessions: int = 0) -> None:
        """Process a heartbeat from a remote agent."""
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        cursor.execute(
            f"""
            UPDATE remote_machines
            SET last_heartbeat = {_param()}, status = 'online', updated_at = {_param()}
            WHERE machine_id = {_param()}
        """,
            (now, now, machine_id),
        )
        conn.commit()
        conn.close()

    # ==================== Machine Queries ====================

    def get_machine(self, machine_id: str) -> Optional[Dict[str, Any]]:
        """Get machine details."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"SELECT * FROM remote_machines WHERE machine_id = {_param()}", (machine_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._row_to_machine(row)

    def list_machines(self, tenant_id: Optional[int] = None,
                      user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List machines, optionally filtered by tenant or user assignments."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id:
            cursor.execute(
                f"""
                SELECT rm.* FROM remote_machines rm
                JOIN machine_assignments ma ON rm.machine_id = ma.machine_id
                WHERE ma.user_id = {_param()}
                ORDER BY rm.updated_at DESC
            """,
                (user_id,),
            )
        elif tenant_id:
            cursor.execute(
                f"""
                SELECT * FROM remote_machines
                WHERE tenant_id = {_param()}
                ORDER BY updated_at DESC
            """,
                (tenant_id,),
            )
        else:
            cursor.execute("SELECT * FROM remote_machines ORDER BY updated_at DESC")

        rows = cursor.fetchall()
        conn.close()

        machines = [self._row_to_machine(row) for row in rows]
        if user_id:
            for m in machines:
                m["current_user_permission"] = self.check_user_access(m["machine_id"], user_id)
        return machines

    def get_available_machines(self, user_id: int) -> List[Dict[str, Any]]:
        """Get machines available to a specific user."""
        return self.list_machines(user_id=user_id)

    def assign_user(self, machine_id: str, user_id: int, granted_by: int,
                    permission: str = "user") -> bool:
        """Assign a user to a machine."""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            if is_postgresql():
                cursor.execute(
                    f"""
                    INSERT INTO machine_assignments
                    (machine_id, user_id, permission, granted_by, granted_at)
                    VALUES ({_params(5)})
                    ON CONFLICT (machine_id, user_id) DO NOTHING
                """,
                    (machine_id, user_id, permission, granted_by, datetime.utcnow().isoformat()),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT OR IGNORE INTO machine_assignments
                    (machine_id, user_id, permission, granted_by, granted_at)
                    VALUES ({_params(5)})
                """,
                    (machine_id, user_id, permission, granted_by, datetime.utcnow().isoformat()),
                )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to assign user: {e}")
            return False
        finally:
            conn.close()

    def revoke_user(self, machine_id: str, user_id: int) -> bool:
        """Revoke a user's access to a machine."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            DELETE FROM machine_assignments
            WHERE machine_id = {_param()} AND user_id = {_param()}
        """,
            (machine_id, user_id),
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def check_user_access(self, machine_id: str, user_id: int) -> Optional[str]:
        """Check user access, returns permission level ('admin'/'user') or None."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT permission FROM machine_assignments
            WHERE machine_id = {_param()} AND user_id = {_param()}
        """,
            (machine_id, user_id),
        )
        result = cursor.fetchone()
        conn.close()
        if result is None:
            return None
        return result["permission"] if isinstance(result, dict) else result[0]

    def get_user_permission(self, machine_id: str, user_id: int) -> Optional[str]:
        """Return user's machine permission: 'admin', 'user', or None."""
        return self.check_user_access(machine_id, user_id)

    def get_machine_assignments(self, machine_id: str) -> List[Dict[str, Any]]:
        """Get list of users assigned to a machine."""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                f"""
                SELECT ma.user_id, u.username, ma.permission, ma.granted_at
                FROM machine_assignments ma
                LEFT JOIN users u ON ma.user_id = u.id
                WHERE ma.machine_id = {_param()}
                ORDER BY ma.granted_at ASC
            """,
                (machine_id,),
            )

            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    "user_id": row["user_id"] if isinstance(row, dict) else row["user_id"],
                    "username": row["username"] if isinstance(row, dict) else row["username"],
                    "permission": row["permission"] if isinstance(row, dict) else row["permission"],
                    "granted_at": row["granted_at"] if isinstance(row, dict) else row["granted_at"],
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get machine assignments: {e}")
            return []
        finally:
            conn.close()

    def _row_to_machine(self, row) -> Dict[str, Any]:
        """Convert a database row to machine dict."""
        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        capabilities = get_value("capabilities")
        if isinstance(capabilities, str):
            try:
                capabilities = json.loads(capabilities)
            except (json.JSONDecodeError, TypeError):
                capabilities = {}

        return {
            "id": get_value("id"),
            "machine_id": get_value("machine_id"),
            "machine_name": get_value("machine_name"),
            "hostname": get_value("hostname"),
            "os_type": get_value("os_type"),
            "os_version": get_value("os_version"),
            "ip_address": get_value("ip_address"),
            "status": get_value("status") or "offline",
            "agent_version": get_value("agent_version"),
            "capabilities": capabilities,
            "cli_path": get_value("cli_path"),
            "work_dir": get_value("work_dir"),
            "tenant_id": get_value("tenant_id"),
            "created_by": get_value("created_by"),
            "created_at": get_value("created_at"),
            "updated_at": get_value("updated_at"),
            "last_heartbeat": get_value("last_heartbeat"),
            "connected": self.is_connected(get_value("machine_id") or ""),
        }


# Global singleton
_agent_manager: Optional[RemoteAgentManager] = None


def get_remote_agent_manager() -> RemoteAgentManager:
    """Get the global RemoteAgentManager instance."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = RemoteAgentManager()
    return _agent_manager
