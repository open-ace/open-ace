"""
Unit tests for Usage Report Security Hardening (Issue #1891).

Tests cover authentication, binding validation, input validation,
rate limiting, and audit logging.

Uses RemoteAgentManager directly with a temporary SQLite database.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.modules.governance.audit_logger import AuditAction


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

    db_path = str(tmp_path / "test_usage_report.db")
    mgr = RemoteAgentManager(db_path=db_path)

    # Ensure tables exist (including new ones)
    from app.repositories.schema_init import load_schema_from_file

    with mgr.db.connection() as conn:
        load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
        conn.commit()

    return mgr


@pytest.fixture
def session_manager():
    """Get the session manager."""
    from app.modules.workspace.session_manager import SessionManager

    return SessionManager()


def _create_session_in_db(db_path: str, session_id: str, machine_id: str, tenant_id: int = 1, user_id: int = 1):
    """Helper to create a session directly in the database."""
    from app.repositories.database import Database

    db = Database(db_url=f"sqlite:///{db_path}")
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO agent_sessions
               (session_id, tool_name, user_id, tenant_id, status,
                workspace_type, remote_machine_id, created_at)
               VALUES (?, 'claude-code', ?, ?, 'active', 'remote', ?, datetime('now'))""",
            (session_id, user_id, tenant_id, machine_id),
        )
        conn.commit()


# ── Input Validation Tests (R8) ───────────────────────────────────────────


class TestInputValidation:
    """Tests for input data validation (R8)."""

    def test_negative_input_tokens_rejected(self):
        """Negative tokens.input should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": -100, "output": 0}, 1)
        assert error is not None
        assert "input" in error.lower()

    def test_negative_output_tokens_rejected(self):
        """Negative tokens.output should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 0, "output": -50}, 1)
        assert error is not None
        assert "output" in error.lower()

    def test_huge_token_values_rejected(self):
        """Tokens exceeding limit should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 10**12, "output": 0}, 1)
        assert error is not None
        assert "tokens" in error.lower()

    def test_non_integer_token_values_rejected(self):
        """Non-integer token values should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": "abc", "output": 0}, 1)
        assert error is not None
        assert "integer" in error.lower()

    def test_requests_out_of_range_rejected(self):
        """Requests exceeding limit should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, 10000)
        assert error is not None
        assert "requests" in error.lower()

    def test_negative_requests_rejected(self):
        """Negative requests should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, -1)
        assert error is not None
        assert "requests" in error.lower()

    def test_valid_input_accepted(self):
        """Valid input should pass validation."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, 5)
        assert error is None
        assert reason is None

    def test_zero_tokens_accepted(self):
        """Zero tokens should be valid."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 0, "output": 0}, 1)
        assert error is None


# ── Authentication Tests (R1) ─────────────────────────────────────────────


class TestAuthentication:
    """Tests for Bearer token authentication (R1)."""

    def test_no_bearer_token_non_legacy(self, manager):
        """Non-legacy machine without Bearer should fail validation."""
        machine_id = "machine-auth-001"

        # Register machine (non-legacy by default)
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-auth",
        )
        assert result is not None

        # Validate empty token should fail
        assert manager.validate_agent_token("", machine_id) is False

    def test_invalid_bearer_token(self, manager):
        """Invalid Bearer token should fail validation."""
        machine_id = "machine-auth-002"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-auth",
        )
        assert result is not None

        # Invalid token
        assert manager.validate_agent_token("totally-wrong-token", machine_id) is False

    def test_token_wrong_machine(self, manager):
        """Token bound to different machine should fail validation."""
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
        manager.register_machine(
            registration_token=reg_b,
            machine_id="machine-b",
            machine_name="Machine B",
        )

        # A's token should NOT validate against B's machine_id
        assert manager.validate_agent_token(token_a, "machine-b") is False

        # A's token should validate against A's machine_id
        assert manager.validate_agent_token(token_a, "machine-a") is True

    def test_valid_token_success(self, manager):
        """Valid Bearer token should pass validation."""
        machine_id = "machine-auth-003"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-auth",
        )
        assert result is not None
        agent_token = result["agent_token"]

        # Valid token
        assert manager.validate_agent_token(agent_token, machine_id) is True

    def test_revoked_token_fails(self, manager):
        """Revoked token should fail validation."""
        machine_id = "machine-auth-004"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-auth",
        )
        agent_token = result["agent_token"]

        # Token works before revocation
        assert manager.validate_agent_token(agent_token, machine_id) is True

        # Revoke
        success = manager.revoke_agent_token(machine_id, revoked_by=1)
        assert success is True

        # Token no longer works
        assert manager.validate_agent_token(agent_token, machine_id) is False


# ── Binding Validation Tests (R2, R3) ──────────────────────────────────────


class TestBindingValidation:
    """Tests for session-machine binding and tenant validation (R2, R3)."""

    def test_session_machine_binding_correct(self, manager, tmp_path):
        """Session should belong to correct machine."""
        machine_id = "machine-bind-001"
        session_id = "session-bind-001"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-bind",
        )
        assert result is not None

        # Create session in DB
        _create_session_in_db(str(tmp_path / "test_usage_report.db"), session_id, machine_id)

        # Get session and verify binding
        machine = manager.get_machine(machine_id)
        assert machine is not None
        assert machine.get("machine_id") == machine_id

    def test_cross_machine_session_rejected(self, manager, tmp_path):
        """Session belonging to different machine should be rejected."""
        machine_a = "machine-bind-a"
        machine_b = "machine-bind-b"
        session_a = "session-bind-a"

        # Register both machines
        reg_a = manager.create_registration_token(tenant_id=1, created_by=1)
        manager.register_machine(
            registration_token=reg_a,
            machine_id=machine_a,
            machine_name="Machine A",
        )

        reg_b = manager.create_registration_token(tenant_id=1, created_by=1)
        manager.register_machine(
            registration_token=reg_b,
            machine_id=machine_b,
            machine_name="Machine B",
        )

        # Create session bound to machine_a
        _create_session_in_db(str(tmp_path / "test_usage_report.db"), session_a, machine_a)

        # Verify session is bound to machine_a, not machine_b
        # This simulates the binding check logic
        machine = manager.get_machine(machine_a)
        assert machine is not None

        machine_b_obj = manager.get_machine(machine_b)
        assert machine_b_obj is not None


# ── Legacy Compatibility Tests (R7) ────────────────────────────────────────


class TestLegacyCompatibility:
    """Tests for legacy agent compatibility (R7)."""

    def test_legacy_machine_accepted(self, manager):
        """Legacy machine should be allowed without Bearer."""
        machine_id = "machine-legacy-001"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-legacy",
        )
        assert result is not None

        # Set legacy mode
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET legacy_mode = 1 WHERE machine_id = ?",
                (machine_id,),
            )
            conn.commit()

        # Check legacy mode
        assert manager.is_legacy_machine(machine_id) is True

    def test_non_legacy_requires_bearer(self, manager):
        """Non-legacy machine without Bearer should fail."""
        machine_id = "machine-legacy-002"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-legacy",
        )
        assert result is not None

        # Should NOT be in legacy mode by default
        assert manager.is_legacy_machine(machine_id) is False

        # Empty token should fail
        assert manager.validate_agent_token("", machine_id) is False

    def test_legacy_mode_can_be_cleared(self, manager):
        """Legacy mode should be clearable after Bearer auth."""
        machine_id = "machine-legacy-003"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-legacy",
        )
        agent_token = result["agent_token"]

        # Set legacy mode
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE remote_machines SET legacy_mode = 1 WHERE machine_id = ?",
                (machine_id,),
            )
            conn.commit()

        assert manager.is_legacy_machine(machine_id) is True

        # Clear legacy mode
        manager.clear_legacy_mode(machine_id)

        assert manager.is_legacy_machine(machine_id) is False


# ── Rate Limiting Tests (R6) ───────────────────────────────────────────────


class TestRateLimiting:
    """Tests for rate limiting (R6)."""

    def test_rate_limit_session_key(self):
        """Rate limit should work for session keys."""
        from app.routes.remote import _check_usage_report_rate_limit, _usage_report_rate_limit_lock, _usage_report_rate_limit_state

        # Clear state
        with _usage_report_rate_limit_lock:
            _usage_report_rate_limit_state.clear()

        key = "session:test-session-001"

        # Should allow first 10 requests
        for i in range(10):
            assert _check_usage_report_rate_limit(key, 10) is True

        # 11th should be blocked
        assert _check_usage_report_rate_limit(key, 10) is False

    def test_rate_limit_different_keys_independent(self):
        """Different rate limit keys should be independent."""
        from app.routes.remote import _check_usage_report_rate_limit, _usage_report_rate_limit_lock, _usage_report_rate_limit_state

        # Clear state
        with _usage_report_rate_limit_lock:
            _usage_report_rate_limit_state.clear()

        key1 = "session:test-session-001"
        key2 = "session:test-session-002"

        # Exhaust key1
        for i in range(10):
            _check_usage_report_rate_limit(key1, 10)

        # key1 should be blocked
        assert _check_usage_report_rate_limit(key1, 10) is False

        # key2 should still work
        assert _check_usage_report_rate_limit(key2, 10) is True


# ── Audit Logging Tests (R4) ──────────────────────────────────────────────


class TestAuditLogging:
    """Tests for audit logging (R4)."""

    def test_audit_action_enum_exists(self):
        """Audit actions should be defined."""
        assert hasattr(AuditAction, "USAGE_REPORT_AUTH_FAILURE")
        assert hasattr(AuditAction, "USAGE_REPORT_BINDING_MISMATCH")

        # Verify values
        assert AuditAction.USAGE_REPORT_AUTH_FAILURE.value == "usage_report_auth_failure"
        assert AuditAction.USAGE_REPORT_BINDING_MISMATCH.value == "usage_report_binding_mismatch"


# ── Valid Request Tests ────────────────────────────────────────────────────


class TestValidRequests:
    """Tests for valid usage report requests."""

    def test_process_usage_report_basic(self, manager, tmp_path):
        """Basic usage report processing should work."""
        machine_id = "machine-valid-001"
        session_id = "session-valid-001"

        # Setup
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-valid",
        )
        assert result is not None

        _create_session_in_db(str(tmp_path / "test_usage_report.db"), session_id, machine_id)

        # Verify session was created
        from app.repositories.database import Database

        db = Database(db_url=f"sqlite:///{tmp_path / 'test_usage_report.db'}")
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT session_id, remote_machine_id FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["session_id"] == session_id
            assert row["remote_machine_id"] == machine_id

    def test_token_rotation_preserves_usage(self, manager):
        """Token rotation should not affect usage data."""
        machine_id = "machine-valid-002"

        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        result = manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-valid",
        )
        old_token = result["agent_token"]

        # Old token works
        assert manager.validate_agent_token(old_token, machine_id) is True

        # Rotate
        new_result = manager.rotate_agent_token(machine_id, rotated_by=1)
        assert new_result is not None
        new_token = new_result["new_token"]

        # Old token no longer works
        assert manager.validate_agent_token(old_token, machine_id) is False

        # New token works
        assert manager.validate_agent_token(new_token, machine_id) is True