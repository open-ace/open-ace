"""Unit tests for agent authentication (RemoteAgentManager token management).

Tests cover:
- Registration token creation (SHA-256 hash storage)
- Machine registration with agent_token issuance
- Agent token validation (with cache)
- Token rotation (dual-window)
- Token revocation
- Registration rate limiting
- Expired token cleanup
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.remote_agent_manager import RemoteAgentManager, _sha256


@pytest.fixture
def agent_mgr(tmp_path):
    """Create a RemoteAgentManager with a temporary SQLite database."""
    import app.modules.workspace.remote_agent_manager as ram_mod

    db_path = str(tmp_path / "test.db")

    # Patch is_postgresql to return False for the entire module scope
    with patch.object(ram_mod, "is_postgresql", return_value=False):
        mgr = RemoteAgentManager(db_path=db_path)

        # Create tables using DDL statements
        from app.modules.workspace.remote_agent_manager import get_ddl_statements

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            for sql in get_ddl_statements():
                try:
                    cursor.execute(sql)
                except Exception:
                    pass
            conn.commit()

        yield mgr


def _sha256_local(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class TestCreateRegistrationToken:
    """Tests for create_registration_token()."""

    def test_returns_64_char_hex_token(self, agent_mgr):
        token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_stores_sha256_hash_in_db(self, agent_mgr):
        token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        expected_hash = _sha256_local(token)

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_hash, state, tenant_id FROM registration_tokens")
            row = cursor.fetchone()

        assert row is not None
        assert row["token_hash"] == expected_hash
        assert row["state"] == "active"
        assert row["tenant_id"] == 1

    def test_expires_at_is_24h_from_now(self, agent_mgr):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT expires_at FROM registration_tokens")
            row = cursor.fetchone()

        expires_at = datetime.fromisoformat(row["expires_at"])
        assert (
            before + timedelta(hours=23, minutes=59)
            < expires_at
            < after + timedelta(hours=24, minutes=1)
        )

    def test_different_tokens_each_time(self, agent_mgr):
        t1 = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        t2 = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        assert t1 != t2


class TestRegisterMachine:
    """Tests for register_machine()."""

    def _make_token(self, agent_mgr, tenant_id=1, created_by=1):
        return agent_mgr.create_registration_token(tenant_id=tenant_id, created_by=created_by)

    def test_returns_agent_token_on_success(self, agent_mgr):
        reg_token = self._make_token(agent_mgr)
        result = agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-001",
            machine_name="TestMachine",
            hostname="testhost",
        )
        assert result is not None
        assert result["machine_id"] == "test-machine-001"
        assert result["agent_token"] is not None
        assert len(result["agent_token"]) == 64

    def test_stores_agent_token_hash_in_db(self, agent_mgr):
        reg_token = self._make_token(agent_mgr)
        result = agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-002",
            machine_name="TestMachine",
        )
        agent_token = result["agent_token"]
        expected_hash = _sha256_local(agent_token)

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT agent_token_hash FROM remote_machines WHERE machine_id = ?",
                ("test-machine-002",),
            )
            row = cursor.fetchone()

        assert row["agent_token_hash"] == expected_hash

    def test_invalid_registration_token_returns_none(self, agent_mgr):
        result = agent_mgr.register_machine(
            registration_token="invalid-token",
            machine_id="test-machine-003",
            machine_name="TestMachine",
        )
        assert result is None

    def test_consumed_token_cannot_be_reused(self, agent_mgr):
        reg_token = self._make_token(agent_mgr)
        agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-004",
            machine_name="TestMachine1",
        )
        # Try reusing the same token
        result = agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-005",
            machine_name="TestMachine2",
        )
        assert result is None

    def test_registration_token_marked_consumed(self, agent_mgr):
        reg_token = self._make_token(agent_mgr)
        agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-006",
            machine_name="TestMachine",
        )
        token_hash = _sha256_local(reg_token)
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state, consumed_by_machine_id FROM registration_tokens WHERE token_hash = ?",
                (token_hash,),
            )
            row = cursor.fetchone()

        assert row["state"] == "consumed"
        assert row["consumed_by_machine_id"] == "test-machine-006"

    def test_hostname_conflict_returns_error(self, agent_mgr):
        reg_token1 = self._make_token(agent_mgr)
        agent_mgr.register_machine(
            registration_token=reg_token1,
            machine_id="test-machine-007",
            machine_name="Machine1",
            hostname="conflict-host",
        )

        reg_token2 = self._make_token(agent_mgr)
        # Make the first machine appear online
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET status = 'online' WHERE machine_id = ?",
                ("test-machine-007",),
            )
            conn.commit()

        result = agent_mgr.register_machine(
            registration_token=reg_token2,
            machine_id="test-machine-008",
            machine_name="Machine2",
            hostname="conflict-host",
        )
        assert result is not None
        assert result.get("error") == "hostname_conflict"


class TestValidateAgentToken:
    """Tests for validate_agent_token()."""

    def _register_machine(self, agent_mgr):
        reg_token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        result = agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-val",
            machine_name="ValTest",
        )
        return result["agent_token"]

    def test_valid_token_returns_true(self, agent_mgr):
        agent_token = self._register_machine(agent_mgr)
        assert agent_mgr.validate_agent_token(agent_token, "test-machine-val") is True

    def test_invalid_token_returns_false(self, agent_mgr):
        self._register_machine(agent_mgr)
        assert agent_mgr.validate_agent_token("invalid-token", "test-machine-val") is False

    def test_wrong_machine_id_returns_false(self, agent_mgr):
        agent_token = self._register_machine(agent_mgr)
        assert agent_mgr.validate_agent_token(agent_token, "wrong-machine") is False

    def test_nonexistent_machine_returns_false(self, agent_mgr):
        assert agent_mgr.validate_agent_token("any-token", "nonexistent") is False

    def test_cache_is_used(self, agent_mgr):
        agent_token = self._register_machine(agent_mgr)
        # First call populates cache
        assert agent_mgr.validate_agent_token(agent_token, "test-machine-val") is True
        # Modify DB to invalidate — cache should still return True
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET agent_token_hash = 'wrong' WHERE machine_id = ?",
                ("test-machine-val",),
            )
            conn.commit()
        # Cache hit should still return True (within TTL)
        assert agent_mgr.validate_agent_token(agent_token, "test-machine-val") is True

    def test_cache_expires_after_ttl(self, agent_mgr):
        agent_token = self._register_machine(agent_mgr)
        # First call populates cache
        agent_mgr.validate_agent_token(agent_token, "test-machine-val")
        # Manually expire the cache entry
        token_hash = _sha256_local(agent_token)
        agent_mgr._token_cache[token_hash] = ("test-machine-val", time.time() - 9999)
        # Corrupt DB
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET agent_token_hash = 'wrong' WHERE machine_id = ?",
                ("test-machine-val",),
            )
            conn.commit()
        # Cache expired -> DB lookup -> should return False
        assert agent_mgr.validate_agent_token(agent_token, "test-machine-val") is False


class TestRotateAgentToken:
    """Tests for rotate_agent_token()."""

    def _register_machine(self, agent_mgr):
        reg_token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        return agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-rot",
            machine_name="RotTest",
        )

    def test_returns_new_token(self, agent_mgr):
        self._register_machine(agent_mgr)
        new_token = agent_mgr.rotate_agent_token("test-machine-rot")
        assert new_token is not None
        assert len(new_token) == 64

    def test_old_token_still_valid_during_dual_window(self, agent_mgr):
        result = self._register_machine(agent_mgr)
        old_token = result["agent_token"]
        agent_mgr.rotate_agent_token("test-machine-rot")
        # Old token should still be valid (within 60s dual window)
        # Need to invalidate cache first to force DB lookup
        agent_mgr._invalidate_token_cache("test-machine-rot")
        assert agent_mgr.validate_agent_token(old_token, "test-machine-rot") is True

    def test_new_token_valid(self, agent_mgr):
        self._register_machine(agent_mgr)
        new_token = agent_mgr.rotate_agent_token("test-machine-rot")
        assert agent_mgr.validate_agent_token(new_token, "test-machine-rot") is True

    def test_nonexistent_machine_returns_none(self, agent_mgr):
        assert agent_mgr.rotate_agent_token("nonexistent") is None

    def test_previous_hash_stored_in_db(self, agent_mgr):
        result = self._register_machine(agent_mgr)
        old_hash = _sha256_local(result["agent_token"])
        agent_mgr.rotate_agent_token("test-machine-rot")

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT previous_token_hash, previous_token_expires_at FROM remote_machines WHERE machine_id = ?",
                ("test-machine-rot",),
            )
            row = cursor.fetchone()

        assert row["previous_token_hash"] == old_hash
        assert row["previous_token_expires_at"] is not None


class TestRevokeAgentToken:
    """Tests for revoke_agent_token()."""

    def _register_machine(self, agent_mgr):
        reg_token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        return agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="test-machine-rev",
            machine_name="RevTest",
        )

    def test_revoked_token_no_longer_valid(self, agent_mgr):
        result = self._register_machine(agent_mgr)
        agent_token = result["agent_token"]
        agent_mgr.revoke_agent_token("test-machine-rev")
        # Invalidate cache to force DB lookup
        agent_mgr._invalidate_token_cache("test-machine-rev")
        assert agent_mgr.validate_agent_token(agent_token, "test-machine-rev") is False

    def test_revoked_clears_db_hash(self, agent_mgr):
        self._register_machine(agent_mgr)
        agent_mgr.revoke_agent_token("test-machine-rev")

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT agent_token_hash, previous_token_hash FROM remote_machines WHERE machine_id = ?",
                ("test-machine-rev",),
            )
            row = cursor.fetchone()

        assert row["agent_token_hash"] is None
        assert row["previous_token_hash"] is None

    def test_nonexistent_machine_returns_false(self, agent_mgr):
        assert agent_mgr.revoke_agent_token("nonexistent") is False

    def test_machine_without_token_returns_false(self, agent_mgr):
        # Insert a machine without token
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines (machine_id, machine_name, status, tenant_id, created_by, created_at, updated_at, last_heartbeat) "
                "VALUES (?, ?, 'offline', 1, 1, ?, ?, ?)",
                (
                    "legacy-machine",
                    "Legacy",
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        assert agent_mgr.revoke_agent_token("legacy-machine") is False


class TestIsLegacyMachine:
    """Tests for is_legacy_machine()."""

    def test_machine_without_token_is_legacy(self, agent_mgr):
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines (machine_id, machine_name, status, tenant_id, created_by, created_at, updated_at, last_heartbeat) "
                "VALUES (?, ?, 'offline', 1, 1, ?, ?, ?)",
                (
                    "legacy-machine",
                    "Legacy",
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        assert agent_mgr.is_legacy_machine("legacy-machine") is True

    def test_machine_with_token_is_not_legacy(self, agent_mgr):
        reg_token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="new-machine",
            machine_name="New",
        )
        assert agent_mgr.is_legacy_machine("new-machine") is False

    def test_nonexistent_machine_is_not_legacy(self, agent_mgr):
        assert agent_mgr.is_legacy_machine("nonexistent") is False


class TestRegisterRateLimit:
    """Tests for check_register_rate_limit()."""

    def test_first_request_allowed(self, agent_mgr):
        allowed, retry_after = agent_mgr.check_register_rate_limit("1.2.3.4")
        assert allowed is True
        assert retry_after is None

    def test_within_limit_allowed(self, agent_mgr):
        for i in range(10):
            allowed, _ = agent_mgr.check_register_rate_limit("1.2.3.4")
        assert allowed is True

    def test_over_limit_blocked(self, agent_mgr):
        for i in range(10):
            agent_mgr.check_register_rate_limit("1.2.3.4")
        allowed, retry_after = agent_mgr.check_register_rate_limit("1.2.3.4")
        assert allowed is False
        assert retry_after is not None
        assert retry_after > 0

    def test_different_ips_independent(self, agent_mgr):
        for i in range(10):
            agent_mgr.check_register_rate_limit("1.2.3.4")
        allowed, _ = agent_mgr.check_register_rate_limit("5.6.7.8")
        assert allowed is True


class TestCleanupExpiredRegistrationTokens:
    """Tests for _cleanup_expired_registration_tokens()."""

    def test_expires_active_tokens_past_expiry(self, agent_mgr):
        token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        token_hash = _sha256_local(token)

        # Manually set expires_at to the past
        past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE registration_tokens SET expires_at = ? WHERE token_hash = ?",
                (past, token_hash),
            )
            conn.commit()

        agent_mgr._cleanup_expired_registration_tokens()

        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state FROM registration_tokens WHERE token_hash = ?", (token_hash,)
            )
            row = cursor.fetchone()

        assert row["state"] == "expired"


class TestGetAuthStatus:
    """Tests for get_auth_status()."""

    def test_machine_with_token(self, agent_mgr):
        reg_token = agent_mgr.create_registration_token(tenant_id=1, created_by=1)
        agent_mgr.register_machine(
            registration_token=reg_token,
            machine_id="auth-test-1",
            machine_name="AuthTest",
        )
        status = agent_mgr.get_auth_status("auth-test-1")
        assert status is not None
        assert status["has_token"] is True
        assert status["is_legacy"] is False
        assert status["last_rotated"] is not None

    def test_machine_without_token(self, agent_mgr):
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines (machine_id, machine_name, status, tenant_id, created_by, created_at, updated_at, last_heartbeat) "
                "VALUES (?, ?, 'offline', 1, 1, ?, ?, ?)",
                (
                    "legacy-1",
                    "Legacy",
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        status = agent_mgr.get_auth_status("legacy-1")
        assert status is not None
        assert status["has_token"] is False
        assert status["is_legacy"] is True

    def test_nonexistent_machine_returns_none(self, agent_mgr):
        assert agent_mgr.get_auth_status("nonexistent") is None
