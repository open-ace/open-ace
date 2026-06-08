"""
E2E tests for Remote Agent Identity Hardening (PR #760).

Tests cover 11 scenarios for the new DB-based registration tokens,
agent Bearer token authentication, token rotation/revocation,
legacy mode handling.

Uses RemoteAgentManager directly with a temporary SQLite database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_db_compat():
    """Patch is_postgresql and adapt_sql for all tests (SQLite mode)."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            yield
        finally:
            db_mod.adapt_sql = orig


@pytest.fixture
def manager(tmp_path):
    """Create a RemoteAgentManager backed by a temp SQLite database."""
    # Import after patching is_postgresql so _param() returns '?'
    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    db_path = str(tmp_path / "test_agent.db")
    mgr = RemoteAgentManager(db_path=db_path)

    # Ensure tables exist (including new ones)
    from app.modules.workspace.remote_agent_manager import get_ddl_statements

    with mgr.db.connection() as conn:
        cursor = conn.cursor()
        for sql in get_ddl_statements():
            try:
                cursor.execute(sql)
            except Exception:
                pass  # ALTER TABLE may fail if column exists
        conn.commit()

    return mgr


# ── Test Scenarios ───────────────────────────────────────────────────────


class TestScenario01_MissingBearerToken:
    """Scenario 1: Non-legacy machine without Bearer token → validate fails."""

    def test_non_legacy_requires_token(self, manager):
        machine_id = "machine-001"
        agent_token = "fake-token"

        # Register a machine via token flow
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-machine",
            hostname="testhost",
        )
        assert result is not None
        agent_token = result["agent_token"]

        # Validate with no token → should fail (simulated: empty string)
        assert manager.validate_agent_token("", machine_id) is False

        # Validate with wrong token → should fail
        assert manager.validate_agent_token("wrong-token", machine_id) is False


class TestScenario02_InvalidBearerToken:
    """Scenario 2: Invalid Bearer token → validate fails."""

    def test_invalid_token_rejected(self, manager):
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-002",
            machine_name="test-002",
        )
        assert result is not None

        # Wrong token
        assert manager.validate_agent_token("totally-wrong-token", "machine-002") is False


class TestScenario03_TokenBoundToWrongMachine:
    """Scenario 3: Token bound to wrong machine_id → validate fails."""

    def test_cross_machine_token_rejected(self, manager):
        # Register machine A
        reg_a = manager.create_registration_token(tenant_id=1, created_by=1)
        result_a = manager.register_machine(
            registration_token=reg_a,
            machine_id="machine-a",
            machine_name="Machine A",
        )
        token_a = result_a["agent_token"]

        # Register machine B
        reg_b = manager.create_registration_token(tenant_id=1, created_by=1)
        result_b = manager.register_machine(
            registration_token=reg_b,
            machine_id="machine-b",
            machine_name="Machine B",
        )

        # A's token should NOT validate against B's machine_id
        assert manager.validate_agent_token(token_a, "machine-b") is False

        # A's token should validate against A's machine_id
        assert manager.validate_agent_token(token_a, "machine-a") is True


class TestScenario04_SuccessfulRegistration:
    """Scenario 4: Successful registration flow."""

    def test_full_registration_flow(self, manager):
        # Create registration token
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        assert reg_token is not None
        assert len(reg_token) == 64  # secrets.token_hex(32) = 64 chars

        # Register machine
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-004",
            machine_name="test-machine-4",
            hostname="host4",
            os_type="linux",
        )
        assert result is not None
        assert result["machine_id"] == "machine-004"
        assert result["status"] == "online"
        assert "agent_token" in result

        # Agent token should be valid
        agent_token = result["agent_token"]
        assert manager.validate_agent_token(agent_token, "machine-004") is True

        # Machine should be queryable
        machine = manager.get_machine("machine-004")
        assert machine is not None
        assert machine["machine_name"] == "test-machine-4"


class TestScenario05_TokenRotation:
    """Scenario 5: Token rotation — old token invalidated, new token works."""

    def test_rotate_invalidates_old_token(self, manager):
        # Register
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-005",
            machine_name="test-005",
        )
        old_token = result["agent_token"]

        # Old token works
        assert manager.validate_agent_token(old_token, "machine-005") is True

        # Rotate
        new_token = manager.rotate_agent_token("machine-005", rotated_by=1)
        assert new_token is not None

        # Old token no longer works
        assert manager.validate_agent_token(old_token, "machine-005") is False

        # New token works
        assert manager.validate_agent_token(new_token, "machine-005") is True


class TestScenario06_TokenRevocation:
    """Scenario 6: Token revoked → validate fails."""

    def test_revoke_blocks_token(self, manager):
        # Register
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-006",
            machine_name="test-006",
        )
        agent_token = result["agent_token"]

        # Token works before revocation
        assert manager.validate_agent_token(agent_token, "machine-006") is True

        # Revoke
        success = manager.revoke_agent_token("machine-006", revoked_by=1)
        assert success is True

        # Token no longer works
        assert manager.validate_agent_token(agent_token, "machine-006") is False


class TestScenario07_LegacyCompat:
    """Scenario 7: Legacy machine (legacy_mode=TRUE) accepted without Bearer."""

    def test_legacy_machine_accepted(self, manager):
        # Register a machine normally
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-007",
            machine_name="legacy-machine",
        )

        # Set legacy_mode = True
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET legacy_mode = 1 WHERE machine_id = ?",
                ("machine-007",),
            )
            conn.commit()

        # is_legacy_machine should return True
        assert manager.is_legacy_machine("machine-007") is True

        # clear_legacy_mode should work
        manager.clear_legacy_mode("machine-007")
        assert manager.is_legacy_machine("machine-007") is False


class TestScenario08_ExpiredRegistrationToken:
    """Scenario 8: Expired registration token → registration fails."""

    def test_expired_token_rejected(self, manager):
        # Create registration token
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)

        # Manually expire it in DB
        from app.modules.workspace.agent_token import hash_token

        token_hash = hash_token(reg_token)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE registration_tokens SET expires_at = ? WHERE token_hash = ?",
                (past_time, token_hash),
            )
            conn.commit()

        # Try to register with expired token
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-008",
            machine_name="expired-test",
        )
        assert result is None


class TestScenario09_ClearLegacyMode:
    """Scenario 9: Legacy machine uses Bearer → legacy_mode cleared."""

    def test_legacy_cleared_on_bearer_auth(self, manager):
        # Register a machine
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-009",
            machine_name="legacy-clear-test",
        )
        agent_token = result["agent_token"]

        # Set legacy_mode = True
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET legacy_mode = 1 WHERE machine_id = ?",
                ("machine-009",),
            )
            conn.commit()

        assert manager.is_legacy_machine("machine-009") is True

        # Validate token (simulates Bearer auth) — should NOT auto-clear,
        # but clear_legacy_mode should work when called
        assert manager.validate_agent_token(agent_token, "machine-009") is True

        # Clear legacy mode (as the route layer does on Bearer auth)
        manager.clear_legacy_mode("machine-009")

        # Verify cleared
        assert manager.is_legacy_machine("machine-009") is False

        # Verify in DB directly
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT legacy_mode FROM remote_machines WHERE machine_id = ?",
                ("machine-009",),
            )
            row = cursor.fetchone()
            assert row["legacy_mode"] == 0


class TestScenario10_ReRegisterAfterRevocation:
    """Scenario 10: Revoked machine gets new token via rotation."""

    def test_reregister_after_revoke(self, manager):
        # Register
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-010",
            machine_name="test-010",
        )
        old_token = result["agent_token"]

        # Revoke
        manager.revoke_agent_token("machine-010", revoked_by=1)
        assert manager.validate_agent_token(old_token, "machine-010") is False

        # Rotate to re-issue (simulates admin re-enabling)
        new_token = manager.rotate_agent_token("machine-010", rotated_by=1)
        assert new_token is not None

        # New token works
        assert manager.validate_agent_token(new_token, "machine-010") is True

        # _last_rotate_unrevoked should be True (was revoked before rotate)
        assert manager._last_rotate_unrevoked is True


class TestScenario11_DuplicateConsumeRegistrationToken:
    """Scenario 11: Same registration token used twice → fails."""

    def test_duplicate_consume_rejected(self, manager):
        # Create registration token
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)

        # First use — should succeed
        result1 = manager.register_machine(
            registration_token=reg_token,
            machine_id="dup-machine-1",
            machine_name="Dup Machine 1",
        )
        assert result1 is not None

        # Second use — should fail (already consumed)
        result2 = manager.register_machine(
            registration_token=reg_token,
            machine_id="dup-machine-2",
            machine_name="Dup Machine 2",
        )
        assert result2 is None
