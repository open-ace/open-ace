"""Unit tests for agent identity model (Issue #754).

Tests registration token persistence, agent token issuance,
validation, rotation, revocation, and legacy mode handling
in RemoteAgentManager.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


class TestRegistrationTokenPersistence(unittest.TestCase):
    """Tests for create_registration_token persisting to DB."""

    def _make_service(self):
        """Create a RemoteAgentManager with mocked DB."""
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            with (
                patch(
                    "app.modules.workspace.remote_agent_manager.Database",
                    return_value=mock_db,
                ),
                patch("app.modules.workspace.remote_agent_manager.DB_PATH", "/tmp/test.db"),
                patch(
                    "app.modules.workspace.remote_agent_manager.is_postgresql",
                    return_value=False,
                ),
            ):
                from app.modules.workspace.remote_agent_manager import RemoteAgentManager

                mgr = RemoteAgentManager.__new__(RemoteAgentManager)
                mgr.db = mock_db
                from gevent.lock import Semaphore

                mgr._lock = MagicMock()
                mgr._connections = {}
                mgr._session_machines = {}
                mgr._output_buffers = {}
                mgr._command_queues = {}
                mgr._session_end_flags = {}
                mgr._last_heartbeat_db_write = {}
                mgr._browse_results = {}
                return mgr, mock_db, mock_cursor

    def test_create_registration_token_persists_to_db(self):
        """create_registration_token should INSERT into registration_tokens table."""
        mgr, mock_db, mock_cursor = self._make_service()
        token = mgr.create_registration_token(tenant_id=1, created_by=42)

        self.assertIsNotNone(token)
        self.assertEqual(len(token), 64)  # Two UUID4s concatenated
        # Verify DB was called to INSERT
        mock_cursor.execute.assert_called()
        insert_calls = [c for c in mock_cursor.execute.call_args_list if "INSERT" in str(c)]
        self.assertTrue(len(insert_calls) > 0)

    def test_consume_registration_token_valid(self):
        """_consume_registration_token should return info for valid unconsumed token."""
        mgr, mock_db, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "tenant_id": 1,
            "created_by": 42,
        }
        mock_cursor.rowcount = 1

        result = mgr._consume_registration_token("valid_token_123")
        self.assertIsNotNone(result)
        self.assertEqual(result["tenant_id"], 1)
        self.assertEqual(result["created_by"], 42)

    def test_consume_registration_token_invalid(self):
        """_consume_registration_token should return None for invalid/expired token."""
        mgr, mock_db, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = None

        result = mgr._consume_registration_token("invalid_token")
        self.assertIsNone(result)

    def test_consume_registration_token_marks_consumed(self):
        """_consume_registration_token should mark the token as consumed."""
        mgr, mock_db, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "tenant_id": 1,
            "created_by": 42,
        }

        mgr._consume_registration_token("valid_token")
        # Verify an UPDATE was issued to mark consumed
        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE" in str(c)]
        self.assertTrue(len(update_calls) > 0)


class TestAgentTokenIssuance(unittest.TestCase):
    """Tests for agent token issuance on registration."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_issue_agent_token_returns_plaintext(self):
        """_issue_agent_token should return a 64-char hex string."""
        mgr, _, _ = self._make_service()
        token = mgr._issue_agent_token("test-machine-123")
        self.assertIsNotNone(token)
        self.assertEqual(len(token), 64)
        int(token, 16)  # Verify valid hex

    def test_issue_agent_token_stores_hash_not_plaintext(self):
        """_issue_agent_token should INSERT hash, not plaintext, into DB."""
        mgr, _, mock_cursor = self._make_service()
        token = mgr._issue_agent_token("test-machine-123")

        # Find INSERT calls
        insert_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if "INSERT" in str(c) and "agent_tokens" in str(c)
        ]
        self.assertTrue(len(insert_calls) > 0)
        # The hash stored should NOT be the plaintext token
        for call in insert_calls:
            args = call[0][1] if len(call[0]) > 1 else call[1].get("args")
            if args:
                stored_hash = args[1]  # Second arg after machine_id
                self.assertNotEqual(stored_hash, token)
                self.assertEqual(len(stored_hash), 64)  # SHA-256 hex length


class TestAgentTokenValidation(unittest.TestCase):
    """Tests for validate_agent_bearer."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_validate_valid_token(self):
        """Valid token hash should return the bound machine_id."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = {"machine_id": "machine-abc-123"}

        result = mgr.validate_agent_bearer("some_hash_value")
        self.assertEqual(result, "machine-abc-123")

    def test_validate_revoked_token(self):
        """Revoked token should return None."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = None

        result = mgr.validate_agent_bearer("revoked_hash")
        self.assertIsNone(result)

    def test_validate_unknown_hash(self):
        """Unknown hash should return None."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = None

        result = mgr.validate_agent_bearer("nonexistent_hash")
        self.assertIsNone(result)


class TestAgentTokenRotation(unittest.TestCase):
    """Tests for rotate_agent_token."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_rotate_returns_new_token(self):
        """Rotation should return a new valid token."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = {"id": 1}  # Existing token
        mock_cursor.rowcount = 1

        new_token = mgr.rotate_agent_token("machine-123", rotated_by=1)
        self.assertIsNotNone(new_token)
        self.assertEqual(len(new_token), 64)

    def test_rotate_creates_token_for_legacy_machine(self):
        """Rotation should create a new token if none exists (legacy machine)."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.fetchone.return_value = None  # No existing token
        mock_cursor.rowcount = 1

        new_token = mgr.rotate_agent_token("legacy-machine-123", rotated_by=1)
        self.assertIsNotNone(new_token)


class TestAgentTokenRevocation(unittest.TestCase):
    """Tests for revoke_agent_token."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_revoke_active_token(self):
        """Revoking an active token should return True."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.rowcount = 1

        result = mgr.revoke_agent_token("machine-123", revoked_by=1)
        self.assertTrue(result)

    def test_revoke_already_revoked(self):
        """Revoking an already-revoked token should return False."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.rowcount = 0

        result = mgr.revoke_agent_token("machine-123", revoked_by=1)
        self.assertFalse(result)


class TestLegacyModeHandling(unittest.TestCase):
    """Tests for legacy mode clear."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_clear_legacy_mode(self):
        """clear_legacy_mode should UPDATE the machine record."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.rowcount = 1

        mgr.clear_legacy_mode("machine-123")
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if "UPDATE" in str(c) and "legacy_mode" in str(c)
        ]
        self.assertTrue(len(update_calls) > 0)


class TestRegistrationTokenCleanup(unittest.TestCase):
    """Tests for cleanup_expired_registration_tokens."""

    def _make_service(self):
        with patch.dict(
            "sys.modules",
            {
                "gevent": MagicMock(),
                "gevent.lock": MagicMock(),
            },
        ):
            mock_db = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cursor
            mock_db.connection.return_value = mock_conn

            from app.modules.workspace.remote_agent_manager import RemoteAgentManager

            mgr = RemoteAgentManager.__new__(RemoteAgentManager)
            mgr.db = mock_db
            from gevent.lock import Semaphore

            mgr._lock = MagicMock()
            return mgr, mock_db, mock_cursor

    def test_cleanup_deletes_expired_tokens(self):
        """cleanup should DELETE expired and old consumed tokens."""
        mgr, _, mock_cursor = self._make_service()
        mock_cursor.rowcount = 5

        deleted = mgr.cleanup_expired_registration_tokens()
        self.assertEqual(deleted, 5)
        delete_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if "DELETE" in str(c) and "registration_tokens" in str(c)
        ]
        self.assertTrue(len(delete_calls) > 0)


if __name__ == "__main__":
    unittest.main()
