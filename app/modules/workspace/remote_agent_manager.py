from __future__ import annotations

"""
Open ACE - Remote Agent Manager

Manages WebSocket connections to remote agents, heartbeat monitoring,
command dispatching, and message routing for remote workspace sessions.
"""

import json
import logging
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import gevent
from gevent.event import Event
from gevent.lock import Semaphore

from app.modules.workspace.agent_token import (
    generate_agent_token,
    generate_registration_token,
    hash_token,
)
from app.repositories.database import DB_PATH, Database, adapt_boolean_value, is_postgresql

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

    # Heartbeat rate-limiting interval (seconds)
    HEARTBEAT_DB_WRITE_INTERVAL = 30

    # Session recovery window (seconds) - allows reconnection after brief disconnects
    # Sessions are only cleaned up if they've been inactive longer than this window
    SESSION_RECOVERY_WINDOW_SECONDS = 300  # 5 minutes recovery window

    # Registration token cleanup interval (seconds)
    REGISTRATION_TOKEN_CLEANUP_INTERVAL = 3600  # 1 hour

    # Registration token default TTL (seconds)
    REGISTRATION_TOKEN_TTL = 3600  # 1 hour

    # Legacy mode deadline in days — machines older than this must re-register
    LEGACY_MODE_DEADLINE_DAYS = 90

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(DB_PATH)
        if db_path:
            self.db = Database(db_url=f"sqlite:///{self.db_path}")
        else:
            self.db = Database()
        # Active WebSocket connections: {machine_id: websocket_connection}
        self._connections: dict[str, Any] = {}
        # Session to machine mapping: {session_id: machine_id}
        self._session_machines: dict[str, str] = {}
        # Output buffers: {session_id: [output_lines]}
        self._output_buffers: dict[str, list[dict]] = {}
        # Command queues for HTTP-mode agents: {machine_id: [commands]}
        self._command_queues: dict[str, list[dict]] = {}
        # Session end flags: {session_id: True} — set when session completes/stops/errors
        self._session_end_flags: dict[str, bool] = {}
        # Heartbeat rate limiter: {machine_id: last_db_write_timestamp}
        self._last_heartbeat_db_write: dict[str, float] = {}
        # Browse results: {request_id: result} for directory browsing
        self._browse_results: dict[str, dict] = {}
        # Pending command requests: {request_id: {"event": Event, "result": dict}}
        # Used for synchronous command-response pattern (Issue #669)
        self._pending_requests: dict[str, dict] = {}
        # Lock for gevent coroutine safety
        self._lock = Semaphore(1)
        # Token cleanup lazy-start flag
        self._token_cleanup_started: bool = False
        # (removed _last_rotate_unrevoked — rotate_agent_token now returns the info)
        self._restore_in_memory_state()
        # Defer session cleanup to heartbeat monitor instead of running on startup.
        # This gives agents time to re-register after a server restart before their
        # sessions are cleaned up. The heartbeat monitor runs every 60 seconds and
        # will naturally clean up stale sessions (Ref: #596).
        self._start_heartbeat_monitor()

    def _restore_in_memory_state(self) -> None:
        """Restore _session_machines and _session_end_flags from DB after restart.

        The agent_sessions table already persists remote_machine_id and status,
        so we can rebuild the in-memory mappings needed for send_message, SSE
        stream, and other session operations.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Restore session → machine mapping for active/paused sessions
                cursor.execute(
                    "SELECT session_id, remote_machine_id FROM agent_sessions "
                    "WHERE workspace_type = 'remote' AND status IN ('active', 'paused') "
                    "AND remote_machine_id IS NOT NULL"
                )
                rows = cursor.fetchall()
                with self._lock:
                    for row in rows:
                        sid = row["session_id"]
                        mid = row["remote_machine_id"]
                        if sid and mid:
                            self._session_machines[sid] = mid
                            self._output_buffers.setdefault(sid, [])

                # Restore end flags for completed/stopped/error sessions
                cursor.execute(
                    "SELECT session_id FROM agent_sessions "
                    "WHERE workspace_type = 'remote' AND status IN ('completed', 'error', 'stopped')"
                )
                with self._lock:
                    for row in cursor.fetchall():
                        self._session_end_flags[row["session_id"]] = True

            if self._session_machines:
                logger.info(
                    "Restored %d remote session bindings from DB",
                    len(self._session_machines),
                )
        except Exception as e:
            logger.warning("Failed to restore in-memory state: %s", e)

    def _start_heartbeat_monitor(self) -> None:
        """Start background thread for heartbeat monitoring."""

        def monitor():
            while True:
                try:
                    self._check_heartbeats()
                except Exception as e:
                    logger.error(f"Heartbeat monitor error: {e}")
                gevent.sleep(self.HEARTBEAT_CHECK_INTERVAL)

        gevent.spawn(monitor)
        logger.info("Heartbeat monitor started")

    def _check_heartbeats(self) -> None:
        """Check for stale heartbeats and mark machines offline.

        Also cleans up remote sessions that have been offline longer than
        the recovery window, allowing reconnection for brief disconnects.
        """
        recovery_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=self.SESSION_RECOVERY_WINDOW_SECONDS
        )

        with self.db.connection() as conn:
            cursor = conn.cursor()
            heartbeat_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                seconds=self.HEARTBEAT_TIMEOUT_SECONDS
            )

            cursor.execute(
                f"""
                UPDATE remote_machines
                SET status = 'offline', updated_at = {_param()}
                WHERE status != 'offline' AND last_heartbeat < {_param()}
            """,
                (
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    heartbeat_cutoff.isoformat(),
                ),
            )

            updated = cursor.rowcount
            if updated > 0:
                logger.info(f"Marked {updated} machines offline due to heartbeat timeout")

            # Remove stale entries from _connections for machines offline
            # beyond heartbeat timeout, so is_connected() stays accurate
            cursor.execute(
                "SELECT machine_id FROM remote_machines "
                f"WHERE status = 'offline' AND last_heartbeat < {_param()}",
                (heartbeat_cutoff.isoformat(),),
            )
            stale_machines = [r["machine_id"] for r in cursor.fetchall()]
            if stale_machines:
                with self._lock:
                    for mid in stale_machines:
                        self._connections.pop(mid, None)
                logger.info(f"Pruned {len(stale_machines)} stale connections from _connections")

            # Clean up sessions on offline machines that exceed recovery window
            cursor.execute(
                "SELECT s.session_id FROM agent_sessions s "
                "LEFT JOIN remote_machines m ON s.remote_machine_id = m.machine_id "
                "WHERE s.status = 'active' AND s.workspace_type = 'remote' "
                "AND m.status = 'offline' "
                f"AND s.updated_at < {_param()}",
                (recovery_cutoff.isoformat(),),
            )
            expired_sessions = cursor.fetchall()

            if expired_sessions:
                sids = [r["session_id"] for r in expired_sessions]
                with self._lock:
                    for sid in sids:
                        self._session_end_flags[sid] = True

                placeholders = ", ".join([_param()] * len(sids))
                cursor.execute(
                    f"UPDATE agent_sessions SET status = 'completed', "
                    f"updated_at = {_param()} WHERE session_id IN ({placeholders})",
                    [datetime.now(timezone.utc).replace(tzinfo=None).isoformat()] + sids,
                )
                logger.info(
                    f"Cleaned up %d remote sessions (offline > {self.SESSION_RECOVERY_WINDOW_SECONDS}s)",
                    len(expired_sessions),
                )

            conn.commit()

        # Clean up sessions paused too long (>4 hours)
        self._cleanup_stale_paused_sessions()

    def _cleanup_stale_paused_sessions(self) -> None:
        """Stop sessions that have been paused for more than 4 hours."""
        PAUSE_TIMEOUT_HOURS = 4
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=PAUSE_TIMEOUT_HOURS
        )

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    SELECT session_id FROM agent_sessions
                    WHERE status = 'paused' AND paused_at < {_param()}
                    """,
                    (cutoff.isoformat(),),
                )
                stale_sessions = [row["session_id"] for row in cursor.fetchall()]
        except Exception:
            return

        if not stale_sessions:
            return

        from app.modules.workspace.remote_session_manager import get_remote_session_manager

        session_mgr = get_remote_session_manager()

        for session_id in stale_sessions:
            logger.info(
                "Stopping stale paused session %s (paused > %dh)",
                session_id[:8],
                PAUSE_TIMEOUT_HOURS,
            )
            try:
                session_mgr.stop_session(session_id)
            except Exception as e:
                logger.error(
                    "Failed to stop stale paused session %s: %s",
                    session_id[:8],
                    e,
                )

    # ==================== Registration ====================

    def create_registration_token(self, tenant_id: int, created_by: int) -> str:
        """
        Generate a one-time registration token for a new machine.

        The token is stored as a SHA-256 hash in the database. The plaintext
        token is returned once and cannot be retrieved again.

        Args:
            tenant_id: Tenant ID to associate the machine with.
            created_by: User ID who initiated registration.

        Returns:
            Registration token string (plaintext).
        """
        # Lazy-start token cleanup on first use
        if not self._token_cleanup_started:
            self._token_cleanup_started = True
            self._start_token_cleanup()

        token = generate_registration_token()
        token_hash = hash_token(token)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = (now + timedelta(seconds=self.REGISTRATION_TOKEN_TTL)).isoformat()

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO registration_tokens (token_hash, tenant_id, created_by, created_at, expires_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()})
            """,
                (token_hash, tenant_id, created_by, now.isoformat(), expires_at),
            )
            conn.commit()

        logger.info("Created registration token for tenant %d", tenant_id)
        return token

    def register_machine(
        self,
        registration_token: str,
        machine_id: str,
        machine_name: str,
        hostname: str | None = None,
        os_type: str | None = None,
        os_version: str | None = None,
        capabilities: dict | None = None,
        agent_version: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any] | None:
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
        # Consume the one-time registration token (DB-based)
        token_info = self._consume_registration_token(registration_token)

        if not token_info:
            logger.warning("Invalid or expired registration token")
            return None

        with self._lock, self.db.connection() as conn:
            cursor = conn.cursor()

            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            try:
                # Check for existing machine with same hostname in same tenant
                merged = False
                if hostname:
                    cursor.execute(
                        f"""
                        SELECT machine_id, status FROM remote_machines
                        WHERE hostname = {_param()} AND tenant_id = {_param()}
                        ORDER BY updated_at DESC
                        """,
                        (hostname, token_info["tenant_id"]),
                    )
                    existing = cursor.fetchall()

                    online_match = [r for r in existing if r["status"] == "online"]
                    if online_match:
                        conn.rollback()
                        return {
                            "error": "hostname_conflict",
                            "message": f"Hostname '{hostname}' is already registered and online. "
                            f"Contact an admin to resolve the conflict.",
                        }

                    offline_match = [r for r in existing if r["status"] == "offline"]
                    if offline_match:
                        old_machine_id = offline_match[0]["machine_id"]
                        merged = True
                        logger.info(
                            "Merging re-registered machine: hostname=%s, old_id=%s, new_id=%s",
                            hostname,
                            old_machine_id[:8],
                            machine_id[:8],
                        )

                        # Update the existing record with new machine_id and metadata
                        cursor.execute(
                            f"""
                            UPDATE remote_machines
                            SET machine_id = {_param()}, machine_name = {_param()},
                                os_type = {_param()}, os_version = {_param()},
                                ip_address = {_param()}, status = {_param()},
                                agent_version = {_param()}, capabilities = {_param()},
                                updated_at = {_param()}, last_heartbeat = {_param()},
                                created_by = {_param()}
                            WHERE machine_id = {_param()}
                            """,
                            (
                                machine_id,
                                machine_name,
                                os_type,
                                os_version,
                                ip_address,
                                "online",
                                agent_version,
                                json.dumps(capabilities) if capabilities else None,
                                now,
                                now,
                                token_info["created_by"],
                                old_machine_id,
                            ),
                        )

                        # Migrate machine_assignments: delete conflicting, then update
                        cursor.execute(
                            f"""
                            DELETE FROM machine_assignments
                            WHERE machine_id = {_param()}
                            AND user_id IN (
                                SELECT user_id FROM machine_assignments
                                WHERE machine_id = {_param()}
                            )
                            """,
                            (old_machine_id, machine_id),
                        )
                        cursor.execute(
                            f"""
                            UPDATE machine_assignments SET machine_id = {_param()}
                            WHERE machine_id = {_param()}
                            """,
                            (machine_id, old_machine_id),
                        )

                        # Migrate agent_sessions (table may not exist yet)
                        try:
                            cursor.execute(
                                f"""
                                UPDATE agent_sessions SET remote_machine_id = {_param()}
                                WHERE remote_machine_id = {_param()}
                                """,
                                (machine_id, old_machine_id),
                            )
                        except Exception:
                            logger.debug(
                                "agent_sessions table not available during merge, skipping"
                            )

                        # Clean up in-memory state for old machine_id
                        self._connections.pop(old_machine_id, None)
                        self._command_queues.pop(old_machine_id, None)
                        self._last_heartbeat_db_write.pop(old_machine_id, None)
                        for sid, mid in list(self._session_machines.items()):
                            if mid == old_machine_id:
                                self._session_machines[sid] = machine_id

                        conn.commit()

                if not merged:
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
                        (
                            machine_id,
                            token_info["created_by"],
                            "admin",
                            token_info["created_by"],
                            now,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""
                        INSERT OR IGNORE INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at)
                        VALUES ({_params(5)})
                    """,
                        (
                            machine_id,
                            token_info["created_by"],
                            "admin",
                            token_info["created_by"],
                            now,
                        ),
                    )
                conn.commit()

                # Issue an agent token for the newly registered machine
                agent_token = self._create_agent_token(machine_id)

                return {
                    "machine_id": machine_id,
                    "machine_name": machine_name,
                    "status": "online",
                    "tenant_id": token_info["tenant_id"],
                    "agent_token": agent_token,
                }
            except Exception as e:
                logger.error(f"Failed to register machine: {e}")
                conn.rollback()
                return None

    def deregister_machine(self, machine_id: str) -> bool:
        """Remove a machine and its assignments."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                f"DELETE FROM machine_assignments WHERE machine_id = {_param()}", (machine_id,)
            )
            cursor.execute(f"DELETE FROM agent_tokens WHERE machine_id = {_param()}", (machine_id,))
            cursor.execute(
                f"DELETE FROM remote_machines WHERE machine_id = {_param()}", (machine_id,)
            )

            success = cast("bool", cursor.rowcount > 0)
            conn.commit()

        # Close active connection and cleanup rate limiter
        with self._lock:
            self._connections.pop(machine_id, None)
        self._last_heartbeat_db_write.pop(machine_id, None)

        return cast("bool", success)

    # ==================== Token Management ====================

    def _consume_registration_token(self, token: str) -> dict[str, Any] | None:
        """Consume a one-time registration token.

        Validates that the token exists, has not expired, and has not been
        consumed. Atomically marks it as consumed.

        Returns:
            Dict with tenant_id and created_by if valid, None otherwise.
        """
        token_hash_val = hash_token(token)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self._lock, self.db.connection() as conn:
            cursor = conn.cursor()

            # Look up the token
            cursor.execute(
                f"""
                SELECT id, token_hash, tenant_id, created_by, expires_at, is_consumed
                FROM registration_tokens
                WHERE token_hash = {_param()}
            """,
                (token_hash_val,),
            )
            row = cursor.fetchone()

            if not row:
                logger.warning("Registration token not found in DB")
                return None

            # Check consumed
            is_consumed = row["is_consumed"]
            if is_postgresql():
                is_consumed = bool(is_consumed) if is_consumed is not None else False
            else:
                is_consumed = bool(is_consumed)

            if is_consumed:
                logger.warning("Registration token already consumed (id=%s)", row["id"])
                return None

            # Check expiry
            expires_at = row["expires_at"]
            if expires_at:
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at)
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.replace(tzinfo=None)
                if now > expires_at:
                    logger.warning("Registration token expired (id=%s)", row["id"])
                    return None

            # Mark as consumed (P0-1 fix: 3 placeholders / 3 params)
            cursor.execute(
                f"""
                UPDATE registration_tokens
                SET is_consumed = {_param()}, consumed_at = {_param()}
                WHERE id = {_param()}
            """,
                (adapt_boolean_value(True), now.isoformat(), row["id"]),
            )
            conn.commit()

        return {
            "tenant_id": row["tenant_id"],
            "created_by": row["created_by"],
        }

    def _create_agent_token(self, machine_id: str) -> str:
        """Generate and store a new agent token for a machine.

        Returns:
            The plaintext agent token (shown once to the caller).
        """
        token = generate_agent_token()
        token_hash_val = hash_token(token)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO agent_tokens (token_hash, machine_id, created_at)
                VALUES ({_param()}, {_param()}, {_param()})
            """,
                (token_hash_val, machine_id, now),
            )
            conn.commit()

        logger.info("Issued agent token for machine %s", machine_id[:8])
        return token

    def validate_agent_token(self, token: str, machine_id: str) -> bool:
        """Validate an agent Bearer token against a machine_id.

        Checks that the token hash exists, belongs to the given machine,
        and has not been revoked.

        Returns:
            True if valid, False otherwise.
        """
        token_hash_val = hash_token(token)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, machine_id, is_revoked
                FROM agent_tokens
                WHERE token_hash = {_param()}
            """,
                (token_hash_val,),
            )
            row = cursor.fetchone()

        if not row:
            return False

        # Check revoked
        is_revoked = row["is_revoked"]
        if is_postgresql():
            is_revoked = bool(is_revoked) if is_revoked is not None else False
        else:
            is_revoked = bool(is_revoked)

        if is_revoked:
            return False

        # Check machine_id binding
        return bool(row["machine_id"] == machine_id)

    def rotate_agent_token(
        self, machine_id: str, rotated_by: int | None = None
    ) -> dict[str, str | bool] | None:
        """Rotate the agent token for a machine.

        Revokes all existing tokens for the machine and issues a new one.
        If existing tokens were already revoked (e.g., the machine was
        previously revoked), the revocation is silently lifted — this is
        an intentional admin action tracked via audit logging.

        Args:
            machine_id: The machine whose token to rotate.
            rotated_by: User ID who initiated the rotation.

        Returns:
            New plaintext agent token, or None if machine not found.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self._lock, self.db.connection() as conn:
            cursor = conn.cursor()

            # Verify machine exists
            cursor.execute(
                f"SELECT machine_id FROM remote_machines WHERE machine_id = {_param()}",
                (machine_id,),
            )
            if not cursor.fetchone():
                return None

            # Revoke all existing active tokens for this machine
            cursor.execute(
                f"""
                SELECT id, is_revoked FROM agent_tokens
                WHERE machine_id = {_param()}
            """,
                (machine_id,),
            )
            existing = cursor.fetchall()
            any_unrevoked = False
            for row in existing:
                is_revoked = row["is_revoked"]
                if is_postgresql():
                    is_revoked = bool(is_revoked) if is_revoked is not None else False
                else:
                    is_revoked = bool(is_revoked)
                if not is_revoked:
                    any_unrevoked = True

            # Mark all existing tokens as revoked
            cursor.execute(
                f"""
                UPDATE agent_tokens
                SET is_revoked = {_param()}, revoked_at = {_param()}, revoked_by = {_param()}
                WHERE machine_id = {_param()} AND is_revoked = {_param()}
            """,
                (
                    adapt_boolean_value(True),
                    now.isoformat(),
                    rotated_by,
                    machine_id,
                    adapt_boolean_value(False),
                ),
            )

            conn.commit()

        # Track if rotate also lifted a prior revocation (audit detail)
        had_revoked_tokens = len(existing) > 0 and not any_unrevoked

        # Issue a new token
        new_token = self._create_agent_token(machine_id)
        logger.info("Rotated agent token for machine %s", machine_id[:8])
        return {
            "new_token": new_token,
            "unrevoked": had_revoked_tokens,
        }

    def revoke_agent_token(self, machine_id: str, revoked_by: int | None = None) -> bool:
        """Revoke all active agent tokens for a machine.

        Args:
            machine_id: The machine whose tokens to revoke.
            revoked_by: User ID who initiated the revocation.

        Returns:
            True if any tokens were revoked, False otherwise.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self._lock, self.db.connection() as conn:
            cursor = conn.cursor()

            # P0-3 fix: 4 placeholders / 4 params
            cursor.execute(
                f"""
                UPDATE agent_tokens
                SET is_revoked = {_param()}, revoked_at = {_param()}, revoked_by = {_param()}
                WHERE machine_id = {_param()} AND is_revoked = {_param()}
            """,
                (
                    adapt_boolean_value(True),
                    now.isoformat(),
                    revoked_by,
                    machine_id,
                    adapt_boolean_value(False),
                ),
            )
            affected = cursor.rowcount
            conn.commit()

        if affected > 0:
            logger.info("Revoked %d agent token(s) for machine %s", affected, machine_id[:8])
            return True

        return False

    def clear_legacy_mode(self, machine_id: str) -> None:
        """Clear the legacy_mode flag for a machine after it authenticates
        with a valid Bearer token.

        Uses parameterized boolean values for cross-DB compatibility.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE remote_machines
                SET legacy_mode = {_param()}, updated_at = {_param()}
                WHERE machine_id = {_param()} AND legacy_mode = {_param()}
            """,
                (
                    adapt_boolean_value(False),
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    machine_id,
                    adapt_boolean_value(True),
                ),
            )
            conn.commit()

    def is_legacy_machine(self, machine_id: str) -> bool:
        """Check if a machine is in legacy mode (no Bearer token auth)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT legacy_mode, created_at FROM remote_machines
                WHERE machine_id = {_param()}
            """,
                (machine_id,),
            )
            row = cursor.fetchone()

        if not row:
            return False

        raw_legacy = row["legacy_mode"]
        if is_postgresql():
            return bool(raw_legacy) if raw_legacy is not None else False
        else:
            return bool(raw_legacy)

    def cleanup_expired_registration_tokens(self) -> int:
        """Remove expired registration tokens that have NOT been consumed.

        Consumed tokens are retained for audit trail (who authorized which
        machine, when it was consumed, etc.).

        Returns:
            Number of tokens removed.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Only delete tokens that are expired AND not yet consumed.
            # Consumed tokens are kept for audit purposes.
            cursor.execute(
                f"""
                DELETE FROM registration_tokens
                WHERE is_consumed = {_param()}
                   AND expires_at < {_param()}
            """,
                (adapt_boolean_value(False), now.isoformat()),
            )
            removed = int(cursor.rowcount)
            conn.commit()

        if removed:
            logger.info("Cleaned up %d expired unconsumed registration tokens", removed)
        return removed

    def _start_token_cleanup(self) -> None:
        """Start the registration token cleanup timer (lazy, daemon thread)."""

        def _tick():
            try:
                self.cleanup_expired_registration_tokens()
            except Exception as e:
                logger.error("Token cleanup error: %s", e)
            # Reschedule
            timer = threading.Timer(self.REGISTRATION_TOKEN_CLEANUP_INTERVAL, _tick)
            timer.daemon = True
            timer.start()

        timer = threading.Timer(self.REGISTRATION_TOKEN_CLEANUP_INTERVAL, _tick)
        timer.daemon = True
        timer.start()
        logger.info(
            "Registration token cleanup timer started (interval=%ds)",
            self.REGISTRATION_TOKEN_CLEANUP_INTERVAL,
        )

    # ==================== Connection Management ====================

    def register_connection(self, machine_id: str, websocket=None) -> None:
        """Register an active connection from a remote agent (HTTP polling mode)."""
        with self._lock:
            self._connections[machine_id] = None  # HTTP polling — no WebSocket

        # Update status to online
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE remote_machines
                SET status = 'online', last_heartbeat = {_param()}, updated_at = {_param()}
                WHERE machine_id = {_param()}
            """,
                (
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    machine_id,
                ),
            )
            conn.commit()

        logger.info(f"Agent connected (HTTP): {machine_id}")

    def unregister_connection(self, machine_id: str, websocket=None) -> None:
        """Unregister an agent connection."""
        with self._lock:
            self._connections.pop(machine_id, None)
        self._last_heartbeat_db_write.pop(machine_id, None)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE remote_machines SET status = 'offline', updated_at = {_param()}
                WHERE machine_id = {_param()}
            """,
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), machine_id),
            )
            conn.commit()

        logger.info(f"Agent disconnected: {machine_id}")

    def is_connected(self, machine_id: str) -> bool:
        """Check if a machine has an active connection (HTTP polling)."""
        return machine_id in self._connections

    def ensure_agent_tracked(self, machine_id: str) -> None:
        """Ensure an HTTP polling agent is tracked in _connections.

        Used by lightweight poll handlers to register agents without
        triggering a full heartbeat DB write.
        """
        with self._lock:
            if machine_id not in self._connections:
                self._connections[machine_id] = None
                logger.info(f"Registered HTTP polling agent: {machine_id}")

    # ==================== Command Dispatch ====================

    def is_agent_connected(self, machine_id: str) -> bool:
        """Check if an agent is currently connected."""
        return machine_id in self._connections

    def send_command(self, machine_id: str, command: dict[str, Any]) -> bool:
        """
        Queue a command for a remote agent (delivered via HTTP polling).

        Commands are always queued even if the agent is not currently connected.
        When the agent re-registers, get_pending_commands() will deliver them.

        Args:
            machine_id: Target machine ID.
            command: Command dict with 'type', 'command', etc.

        Returns:
            True if command was queued successfully.
        """
        with self._lock:
            if machine_id not in self._command_queues:
                self._command_queues[machine_id] = []
            self._command_queues[machine_id].append(command)

        if machine_id not in self._connections:
            logger.info(
                "Agent %s not connected, command queued for delivery on re-registration",
                machine_id,
            )
        else:
            logger.info("Queued command for agent %s", machine_id)
        return True

    def get_pending_commands(self, machine_id: str) -> list[dict]:
        """Get and clear pending commands for an HTTP-mode agent."""
        with self._lock:
            return self._command_queues.pop(machine_id, [])

    def send_command_with_response(
        self,
        machine_id: str,
        command: str,
        session_id: str,
        timeout: float = 5.0,
    ) -> dict | None:
        """
        Send a command to an agent and wait for a response.

        This implements a synchronous command-response pattern using gevent Event
        for coroutine-safe waiting. Used for commands like get_session_info that
        need to query agent state before proceeding (Issue #669).

        Args:
            machine_id: Target machine ID.
            command: Command name (e.g., "get_session_info").
            session_id: Target session ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Response dict if received within timeout, None otherwise.
        """
        request_id = str(uuid.uuid4())

        # Register pending request with gevent Event
        with self._lock:
            self._pending_requests[request_id] = {
                "event": Event(),
                "result": None,
            }

        # Send the command
        self.send_command(machine_id, {
            "type": "command",
            "command": command,
            "session_id": session_id,
            "request_id": request_id,
        })

        # Wait for response (gevent Event.wait is coroutine-safe)
        pending = self._pending_requests[request_id]
        if pending["event"].wait(timeout):
            result = pending["result"]
        else:
            result = None  # Timeout
            logger.warning(
                "Timeout waiting for %s response from agent %s (session %s)",
                command,
                machine_id,
                session_id[:8],
            )

        # Cleanup
        with self._lock:
            self._pending_requests.pop(request_id, None)

        return result

    def handle_command_response(self, data: dict) -> None:
        """
        Handle command_response from an agent.

        Matches the response to a pending request and signals the waiting coroutine.

        Args:
            data: Response dict with request_id and result.
        """
        request_id = data.get("request_id")
        if request_id and request_id in self._pending_requests:
            with self._lock:
                pending = self._pending_requests.get(request_id)
                if pending:
                    pending["result"] = data.get("result")
                    pending["event"].set()
                    logger.debug("Received response for request %s", request_id[:8])
        else:
            logger.warning("Received response for unknown request %s", request_id[:8] if request_id else "N/A")

    def store_browse_result(self, request_id: str, result: dict) -> None:
        """Store browse result from agent for later retrieval."""
        with self._lock:
            self._browse_results[request_id] = result
        logger.info("Stored browse result for request %s", request_id[:8])

    def get_browse_result(self, request_id: str, timeout: float = 10.0) -> dict | None:
        """Wait for and retrieve browse result.

        Polls for the result with a timeout. Returns None if timeout expires.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._lock:
                if request_id in self._browse_results:
                    return self._browse_results.pop(request_id)
            # Sleep briefly before checking again
            gevent.sleep(0.1)
        logger.warning("Timeout waiting for browse result %s", request_id[:8])
        return None

    def store_terminal_info(self, machine_id: str, terminal_id: str, info: dict) -> None:
        """Store terminal status info reported by an agent.

        When terminal is running with a ws_url, expose it through the Open ACE
        backend WebSocket route. Browsers may not be able to reach the remote
        agent host or backend-side random ports, but they can already reach the
        main Open ACE origin.
        """
        from app.modules.workspace.terminal_store import terminal_info_store

        # If terminal is running with ws_url, publish the backend bridge route
        status = info.get("status")
        ws_url = info.get("ws_url")
        logger.info(
            "store_terminal_info called: terminal=%s status=%s ws_url=%s",
            terminal_id[:8],
            status,
            ws_url,
        )

        if status == "running" and ws_url:
            import secrets

            existing = terminal_info_store.get(machine_id, terminal_id) or {}
            browser_token = existing.get("token") or secrets.token_hex(32)
            original_ws_url = ws_url
            original_token = info.get("token", "")

            info["ws_url"] = f"/api/remote/terminal/{terminal_id}/ws"
            info["token"] = browser_token
            info["original_ws_url"] = original_ws_url
            info["original_token"] = original_token

            logger.info("Stored backend WS route for terminal %s", terminal_id[:8])

        terminal_info_store.put(machine_id, terminal_id, info)

    def get_backend_url(self, request_base_url: str | None = None) -> str:
        """Get the externally reachable backend URL for agents and CLI tools."""
        config: dict[str, Any] = {}
        try:
            import json
            import os

            from app.repositories.database import CONFIG_DIR

            config_path = os.path.join(CONFIG_DIR, "config.json")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)

        except Exception:
            pass

        external_url = config.get("external_url")
        if external_url:
            return cast("str", external_url).rstrip("/")

        if request_base_url:
            return request_base_url.rstrip("/")

        server_config = config.get("server") or {}
        server_url = server_config.get("server_url")
        if server_url:
            return cast("str", server_url).rstrip("/")

        # Preserve the legacy no-context fallback. Runtime request paths pass
        # request.host_url so forwarded deployments use the browser-visible URL.
        return "http://localhost:5001"

    def _get_backend_url(self) -> str:
        """Backward-compatible alias for internal callers without request context."""
        return self.get_backend_url()

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

    def get_machine_for_session(self, session_id: str) -> str | None:
        """Get the machine ID for a session."""
        return self._session_machines.get(session_id)

    # ==================== Output Buffering ====================

    def buffer_output(self, session_id: str, output: dict[str, Any]) -> None:
        """Buffer output from a remote session."""
        with self._lock:
            if session_id not in self._output_buffers:
                self._output_buffers[session_id] = []
            self._output_buffers[session_id].append(output)

    def get_buffered_output(self, session_id: str, after_index: int = 0) -> list[dict]:
        """Get buffered output for a session after a given index."""
        with self._lock:
            buf = self._output_buffers.get(session_id, [])
            return buf[after_index:]

    def mark_session_ended(self, session_id: str) -> None:
        """Mark a session as ended (completed/stopped/error)."""
        with self._lock:
            self._session_end_flags[session_id] = True

    def is_session_ended(self, session_id: str) -> bool:
        """Check if a session has ended (in-memory, no DB query)."""
        with self._lock:
            return self._session_end_flags.get(session_id, False)

    # ==================== Heartbeat ====================

    def process_heartbeat(
        self,
        machine_id: str,
        status: str = "idle",
        active_sessions: int = 0,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        """Process a heartbeat from a remote agent."""
        # Ensure HTTP polling agents are tracked in _connections
        # (needed after server restart, since _connections is in-memory)
        with self._lock:
            if machine_id not in self._connections:
                self._connections[machine_id] = None
                logger.info(f"Re-registered HTTP polling agent via heartbeat: {machine_id}")

        # Rate-limit DB writes: skip if last write was within HEARTBEAT_DB_WRITE_INTERVAL
        now_ts = time.time()
        last_write = self._last_heartbeat_db_write.get(machine_id, 0)
        if now_ts - last_write < self.HEARTBEAT_DB_WRITE_INTERVAL:
            return
        self._last_heartbeat_db_write[machine_id] = now_ts

        # Update capabilities separately if provided (within rate limit)
        if capabilities:
            self.update_capabilities(machine_id, capabilities)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            cursor.execute(
                f"""
                UPDATE remote_machines
                SET last_heartbeat = {_param()}, status = {_param()}, updated_at = {_param()}
                WHERE machine_id = {_param()}
            """,
                (now, status, now, machine_id),
            )
            conn.commit()

    def update_capabilities(self, machine_id: str, capabilities: dict[str, Any]) -> None:
        """Update capabilities for a remote machine."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            cursor.execute(
                f"""
                UPDATE remote_machines
                SET capabilities = {_param()}, updated_at = {_param()}
                WHERE machine_id = {_param()}
            """,
                (json.dumps(capabilities), now, machine_id),
            )
            conn.commit()
            logger.info("Updated capabilities for machine %s", machine_id[:8])

    def update_machine_ip(self, machine_id: str, ip_address: str) -> None:
        """Update IP address for a remote machine."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            cursor.execute(
                f"""
                UPDATE remote_machines
                SET ip_address = {_param()}, updated_at = {_param()}
                WHERE machine_id = {_param()}
            """,
                (ip_address, now, machine_id),
            )
            conn.commit()
            logger.info("Updated IP address for machine %s to %s", machine_id[:8], ip_address)

    # ==================== Machine Queries ====================

    def get_machine(self, machine_id: str) -> dict[str, Any] | None:
        """Get machine details."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                f"SELECT * FROM remote_machines WHERE machine_id = {_param()}", (machine_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_machine(row)

    def list_machines(
        self, tenant_id: int | None = None, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """List machines, optionally filtered by tenant or user assignments."""
        with self.db.connection() as conn:
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

        machines = [self._row_to_machine(row) for row in rows]
        if user_id:
            for m in machines:
                m["current_user_permission"] = self.check_user_access(m["machine_id"], user_id)
        return machines

    def get_available_machines(self, user_id: int) -> list[dict[str, Any]]:
        """Get machines available to a specific user."""
        return self.list_machines(user_id=user_id)

    def assign_user(
        self, machine_id: str, user_id: int, granted_by: int, permission: str = "user"
    ) -> bool:
        """Assign a user to a machine."""
        with self.db.connection() as conn:
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
                        (
                            machine_id,
                            user_id,
                            permission,
                            granted_by,
                            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        ),
                    )
                else:
                    cursor.execute(
                        f"""
                        INSERT OR IGNORE INTO machine_assignments
                        (machine_id, user_id, permission, granted_by, granted_at)
                        VALUES ({_params(5)})
                    """,
                        (
                            machine_id,
                            user_id,
                            permission,
                            granted_by,
                            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        ),
                    )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to assign user: {e}")
                with suppress(Exception):
                    conn.rollback()
                return False

    def revoke_user(self, machine_id: str, user_id: int) -> bool:
        """Revoke a user's access to a machine."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                f"""
                DELETE FROM machine_assignments
                WHERE machine_id = {_param()} AND user_id = {_param()}
            """,
                (machine_id, user_id),
            )
            success = cast("bool", cursor.rowcount > 0)
            conn.commit()

        return cast("bool", success)

    def check_user_access(self, machine_id: str, user_id: int) -> str | None:
        """Check user access, returns permission level ('admin'/'user') or None."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                f"""
                SELECT permission FROM machine_assignments
                WHERE machine_id = {_param()} AND user_id = {_param()}
            """,
                (machine_id, user_id),
            )
            result = cursor.fetchone()

        if result is None:
            return None
        return cast("str | None", result["permission"] if isinstance(result, dict) else result[0])

    def get_user_permission(self, machine_id: str, user_id: int) -> str | None:
        """Return user's machine permission: 'admin', 'user', or None."""
        return self.check_user_access(machine_id, user_id)

    def get_machine_assignments(self, machine_id: str) -> list[dict[str, Any]]:
        """Get list of users assigned to a machine."""
        with self.db.connection() as conn:
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
                    result.append(
                        {
                            "user_id": row["user_id"] if isinstance(row, dict) else row["user_id"],
                            "username": (
                                row["username"] if isinstance(row, dict) else row["username"]
                            ),
                            "permission": (
                                row["permission"] if isinstance(row, dict) else row["permission"]
                            ),
                            "granted_at": (
                                row["granted_at"] if isinstance(row, dict) else row["granted_at"]
                            ),
                        }
                    )
                return result
            except Exception as e:
                logger.error(f"Failed to get machine assignments: {e}")
                return []

    def _row_to_machine(self, row) -> dict[str, Any]:
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


def get_ddl_statements() -> list[str]:
    """Return DDL statements for remote agent manager tables."""
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
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
            last_heartbeat TIMESTAMP,
            legacy_mode INTEGER DEFAULT 0
        )
        """,
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
        """,
        f"""
        CREATE TABLE IF NOT EXISTS registration_tokens (
            id {id_type},
            token_hash TEXT NOT NULL UNIQUE,
            tenant_id INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_consumed INTEGER DEFAULT 0,
            consumed_at TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS agent_tokens (
            id {id_type},
            token_hash TEXT NOT NULL UNIQUE,
            machine_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_revoked INTEGER DEFAULT 0,
            revoked_at TIMESTAMP,
            revoked_by INTEGER,
            rotated_at TIMESTAMP
        )
        """,
        # --- Indexes ---
        "CREATE INDEX IF NOT EXISTS idx_remote_machines_machine_id ON remote_machines(machine_id)",
        "CREATE INDEX IF NOT EXISTS idx_remote_machines_status ON remote_machines(status)",
        "CREATE INDEX IF NOT EXISTS idx_remote_machines_hostname_tenant ON remote_machines(hostname, tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_machine_assignments_user_id ON machine_assignments(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_registration_tokens_hash ON registration_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tokens_hash ON agent_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tokens_machine ON agent_tokens(machine_id)",
        # --- ALTER TABLE for legacy_mode column (idempotent) ---
        "ALTER TABLE remote_machines ADD COLUMN legacy_mode INTEGER DEFAULT 0",
    ]


# Global singleton
_agent_manager: RemoteAgentManager | None = None


def get_remote_agent_manager() -> RemoteAgentManager:
    """Get the global RemoteAgentManager instance."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = RemoteAgentManager()
    return _agent_manager
