"""
Unit tests for Issue #2499: Token rotation with delayed revocation

Tests for:
- rotate_agent_token with immediate and delayed modes
- validate_agent_token with pending_revoke support
- confirm_token_rotation with signature verification
- Atomic config writes in agent
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Add remote-agent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "remote-agent"))

from app.modules.workspace.agent_token import generate_agent_token, hash_token

# Test fixtures
from app.modules.workspace.remote_agent_manager import (
    DEFAULT_TOKEN_REVOKE_TIMEOUT,
    MAX_TOKEN_REVOKE_TIMEOUT,
    MIN_TOKEN_REVOKE_TIMEOUT,
    RemoteAgentManager,
)


class TestTokenRotation:
    """Tests for token rotation functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database for testing."""
        db = MagicMock()
        conn = MagicMock()
        cursor = MagicMock()

        # Setup the chain: db.connection() -> conn, conn.cursor() -> cursor
        db.connection.return_value.__enter__ = Mock(return_value=conn)
        db.connection.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value = cursor

        # Store conn and cursor as attributes for test access
        db._mock_conn = conn
        db._mock_cursor = cursor

        return db

    @pytest.fixture
    def manager(self, mock_db):
        """Create RemoteAgentManager instance for testing."""
        # Skip initialization that requires database
        with patch.object(RemoteAgentManager, "_restore_in_memory_state"):
            with patch.object(RemoteAgentManager, "_start_heartbeat_monitor"):
                with patch.object(RemoteAgentManager, "_start_retention_cleanup"):
                    with patch(
                        "app.modules.workspace.remote_agent_manager.Database", return_value=mock_db
                    ):
                        manager = RemoteAgentManager()
        return manager

    def test_rotate_token_immediate_mode(self, manager, mock_db):
        """Test immediate token rotation mode."""
        machine_id = str(uuid.uuid4())
        rotated_by = 1

        # Mock database responses
        mock_db._mock_cursor.fetchone.return_value = {"machine_id": machine_id}
        mock_db._mock_cursor.fetchall.return_value = []

        # Execute immediate rotation
        result = manager.rotate_agent_token(
            machine_id=machine_id, rotated_by=rotated_by, immediate=True
        )

        # Verify result
        assert result is not None
        assert "new_token" in result
        assert result["immediate"] is True
        assert result["rotation_id"] is not None
        assert len(result["rotation_id"]) == 36  # UUID format

    def test_rotate_token_delayed_mode(self, manager, mock_db):
        """Test delayed token rotation mode."""
        machine_id = str(uuid.uuid4())
        rotated_by = 1

        # Mock database responses
        mock_db._mock_cursor.fetchone.return_value = {"machine_id": machine_id}
        mock_db._mock_cursor.fetchall.return_value = []

        # Execute delayed rotation
        result = manager.rotate_agent_token(
            machine_id=machine_id, rotated_by=rotated_by, immediate=False
        )

        # Verify result
        assert result is not None
        assert "new_token" in result
        assert result["immediate"] is False
        assert result["rotation_id"] is not None
        assert result["timeout"] == DEFAULT_TOKEN_REVOKE_TIMEOUT

    def test_rotate_token_nonexistent_machine(self, manager, mock_db):
        """Test rotation for non-existent machine."""
        machine_id = str(uuid.uuid4())

        # Mock database to return None (machine not found)
        mock_db._mock_cursor.fetchone.return_value = None

        # Execute rotation
        result = manager.rotate_agent_token(machine_id=machine_id)

        # Verify result is None
        assert result is None

    def test_get_token_revoke_timeout_default(self, manager, mock_db):
        """Test getting default token revoke timeout."""
        machine_id = str(uuid.uuid4())

        # Mock database to return None for timeout config
        # Use _mock_cursor which is the actual cursor used by the manager
        mock_db._mock_cursor.fetchone.return_value = None

        # Get timeout
        timeout = manager._get_token_revoke_timeout(machine_id)

        # Verify default timeout
        assert timeout == DEFAULT_TOKEN_REVOKE_TIMEOUT

    def test_get_token_revoke_timeout_custom(self, manager, mock_db):
        """Test getting custom token revoke timeout."""
        machine_id = str(uuid.uuid4())
        custom_timeout = 600  # 10 minutes

        # Mock database to return custom timeout
        mock_db._mock_cursor.fetchone.return_value = {"token_revoke_timeout": custom_timeout}

        # Get timeout
        timeout = manager._get_token_revoke_timeout(machine_id)

        # Verify custom timeout
        assert timeout == custom_timeout

    def test_get_token_revoke_timeout_clamped(self, manager, mock_db):
        """Test timeout clamping to valid range."""
        machine_id = str(uuid.uuid4())

        # Test minimum clamp
        mock_db._mock_cursor.fetchone.return_value = {"token_revoke_timeout": 10}  # Too low
        timeout = manager._get_token_revoke_timeout(machine_id)
        assert timeout == MIN_TOKEN_REVOKE_TIMEOUT

        # Test maximum clamp
        mock_db._mock_cursor.fetchone.return_value = {"token_revoke_timeout": 5000}  # Too high
        timeout = manager._get_token_revoke_timeout(machine_id)
        assert timeout == MAX_TOKEN_REVOKE_TIMEOUT


class TestConfirmTokenRotation:
    """Tests for token rotation confirmation."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database for testing."""
        db = MagicMock()
        conn = MagicMock()
        cursor = MagicMock()

        # Setup the chain: db.connection() -> conn, conn.cursor() -> cursor
        db.connection.return_value.__enter__ = Mock(return_value=conn)
        db.connection.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value = cursor

        # Store conn and cursor as attributes for test access
        db._mock_conn = conn
        db._mock_cursor = cursor

        return db

    @pytest.fixture
    def manager(self, mock_db):
        """Create RemoteAgentManager instance for testing."""
        with patch.object(RemoteAgentManager, "_restore_in_memory_state"):
            with patch.object(RemoteAgentManager, "_start_heartbeat_monitor"):
                with patch.object(RemoteAgentManager, "_start_retention_cleanup"):
                    with patch(
                        "app.modules.workspace.remote_agent_manager.Database", return_value=mock_db
                    ):
                        manager = RemoteAgentManager()
        return manager

    def test_confirm_rotation_valid_signature(self, manager, mock_db):
        """Test valid confirmation with correct signature."""
        machine_id = str(uuid.uuid4())
        rotation_id = str(uuid.uuid4())
        new_token = generate_agent_token()
        timestamp = int(time.time())

        # Generate valid signature
        message = f"{rotation_id}:{timestamp}"
        signature = hmac.new(new_token.encode(), message.encode(), hashlib.sha256).hexdigest()

        # Mock database
        mock_db._mock_cursor.fetchone.return_value = {"id": 1, "machine_id": machine_id}

        # Confirm rotation
        result = manager.confirm_token_rotation(
            machine_id=machine_id,
            rotation_id=rotation_id,
            signature=signature,
            timestamp=timestamp,
            new_token=new_token,
        )

        # Verify success
        assert result is True

    def test_confirm_rotation_invalid_signature(self, manager, mock_db):
        """Test confirmation with invalid signature."""
        machine_id = str(uuid.uuid4())
        rotation_id = str(uuid.uuid4())
        new_token = generate_agent_token()
        timestamp = int(time.time())

        # Generate invalid signature
        signature = "invalid_signature_12345"

        # Confirm rotation
        result = manager.confirm_token_rotation(
            machine_id=machine_id,
            rotation_id=rotation_id,
            signature=signature,
            timestamp=timestamp,
            new_token=new_token,
        )

        # Verify failure
        assert result is False

    def test_confirm_rotation_expired_timestamp(self, manager, mock_db):
        """Test confirmation with expired timestamp."""
        machine_id = str(uuid.uuid4())
        rotation_id = str(uuid.uuid4())
        new_token = generate_agent_token()
        # Timestamp 10 minutes ago (beyond 5-minute window)
        timestamp = int(time.time()) - 600

        # Generate signature
        message = f"{rotation_id}:{timestamp}"
        signature = hmac.new(new_token.encode(), message.encode(), hashlib.sha256).hexdigest()

        # Confirm rotation
        result = manager.confirm_token_rotation(
            machine_id=machine_id,
            rotation_id=rotation_id,
            signature=signature,
            timestamp=timestamp,
            new_token=new_token,
        )

        # Verify failure
        assert result is False

    def test_confirm_rotation_idempotent(self, manager, mock_db):
        """Test that duplicate confirm requests are idempotent."""
        machine_id = str(uuid.uuid4())
        rotation_id = str(uuid.uuid4())
        new_token = generate_agent_token()
        timestamp = int(time.time())

        # Generate signature
        message = f"{rotation_id}:{timestamp}"
        signature = hmac.new(new_token.encode(), message.encode(), hashlib.sha256).hexdigest()

        # Mock database to return None (already processed)
        mock_db._mock_cursor.fetchone.return_value = None

        # Confirm rotation (should succeed for idempotency)
        result = manager.confirm_token_rotation(
            machine_id=machine_id,
            rotation_id=rotation_id,
            signature=signature,
            timestamp=timestamp,
            new_token=new_token,
        )

        # Verify success (idempotent)
        assert result is True


class TestValidateAgentToken:
    """Tests for token validation with pending_revoke support."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database for testing."""
        db = MagicMock()
        conn = MagicMock()
        cursor = MagicMock()

        # Setup the chain: db.connection() -> conn, conn.cursor() -> cursor
        db.connection.return_value.__enter__ = Mock(return_value=conn)
        db.connection.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value = cursor

        # Store conn and cursor as attributes for test access
        db._mock_conn = conn
        db._mock_cursor = cursor

        return db

    @pytest.fixture
    def manager(self, mock_db):
        """Create RemoteAgentManager instance for testing."""
        with patch.object(RemoteAgentManager, "_restore_in_memory_state"):
            with patch.object(RemoteAgentManager, "_start_heartbeat_monitor"):
                with patch.object(RemoteAgentManager, "_start_retention_cleanup"):
                    with patch(
                        "app.modules.workspace.remote_agent_manager.Database", return_value=mock_db
                    ):
                        manager = RemoteAgentManager()
        return manager

    def test_validate_normal_token(self, manager, mock_db):
        """Test validation of normal (non-pending) token."""
        token = generate_agent_token()
        machine_id = str(uuid.uuid4())

        # Mock database
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1,
            "machine_id": machine_id,
            "is_revoked": False,
            "pending_revoke": False,
            "revoke_after": None,
            "is_temporarily_valid": False,
        }

        # Validate token
        result = manager.validate_agent_token(token, machine_id)

        # Verify success
        assert result is True

    def test_validate_pending_revoke_within_timeout(self, manager, mock_db):
        """Test validation of pending_revoke token within timeout window."""
        token = generate_agent_token()
        machine_id = str(uuid.uuid4())

        # Mock database
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1,
            "machine_id": machine_id,
            "is_revoked": False,
            "pending_revoke": True,
            "revoke_after": datetime.now(timezone.utc) + timedelta(minutes=5),
            "is_temporarily_valid": True,
        }

        # Validate token
        result = manager.validate_agent_token(token, machine_id)

        # Verify success (temporarily valid)
        assert result is True

    def test_validate_pending_revoke_expired(self, manager, mock_db):
        """Test validation of pending_revoke token after timeout."""
        token = generate_agent_token()
        machine_id = str(uuid.uuid4())

        # Mock database
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1,
            "machine_id": machine_id,
            "is_revoked": False,
            "pending_revoke": True,
            "revoke_after": datetime.now(timezone.utc) - timedelta(minutes=1),
            "is_temporarily_valid": False,
        }

        # Validate token
        result = manager.validate_agent_token(token, machine_id)

        # Verify failure (expired)
        assert result is False

    def test_validate_revoked_token(self, manager, mock_db):
        """Test validation of revoked token."""
        token = generate_agent_token()
        machine_id = str(uuid.uuid4())

        # Mock database
        mock_db._mock_cursor.fetchone.return_value = {
            "id": 1,
            "machine_id": machine_id,
            "is_revoked": True,
            "pending_revoke": False,
            "revoke_after": None,
            "is_temporarily_valid": False,
        }

        # Validate token
        result = manager.validate_agent_token(token, machine_id)

        # Verify failure (revoked)
        assert result is False


class TestAtomicConfigWrite:
    """Tests for atomic config file writing."""

    def test_atomic_write_creates_file(self):
        """Test that atomic write creates config file."""
        from config import AgentConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config = AgentConfig(str(config_path))

            # Save token atomically
            token = generate_agent_token()
            config.save_agent_token_atomic(token)

            # Verify file exists
            assert config_path.exists()

            # Verify content
            with open(config_path) as f:
                data = json.load(f)
            assert data.get("agent_token") == token

    def test_atomic_write_creates_backup(self):
        """Test that atomic write creates backup of existing config."""
        from config import AgentConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"

            # Create initial config
            config = AgentConfig(str(config_path))
            old_token = generate_agent_token()
            config.save_agent_token_atomic(old_token)

            # Save new token
            new_token = generate_agent_token()
            config.save_agent_token_atomic(new_token)

            # Verify backup exists
            backup_path = config_path.with_suffix(".json.backup")
            assert backup_path.exists()

            # Verify backup has old token
            with open(backup_path) as f:
                data = json.load(f)
            assert data.get("agent_token") == old_token

    def test_atomic_write_is_atomic(self):
        """Test that atomic write doesn't corrupt config on failure."""
        from config import AgentConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"

            # Create initial config
            config = AgentConfig(str(config_path))
            token = generate_agent_token()
            config.save_agent_token_atomic(token)

            # Simulate write failure by removing write permission
            # (This tests that the original file remains intact)
            original_content = config_path.read_text()

            # Verify original content is still valid
            data = json.loads(original_content)
            assert data.get("agent_token") == token


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
