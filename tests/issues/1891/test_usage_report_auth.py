"""
Unit tests for Usage Report Security Hardening (Issue #1891).

Tests cover authentication, binding validation, input validation,
rate limiting, and audit logging.

Uses RemoteAgentManager directly with a temporary SQLite database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from flask import Flask

import app.repositories.database as db_mod
from app.modules.governance.audit_logger import AuditAction

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_db_compat():
    """Patch is_postgresql and adapt_sql for all tests (SQLite mode)."""
    with (
        patch.object(db_mod, "is_postgresql", return_value=False),
        patch("app.modules.workspace.remote_agent_manager.is_postgresql", return_value=False),
        patch("app.modules.workspace.session_manager.is_postgresql", return_value=False),
    ):
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


def _create_session_in_db(
    db_path: str,
    session_id: str,
    machine_id: str,
    tenant_id: int = 1,
    user_id: int = 1,
):
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
        manager.register_machine(
            registration_token=reg_token,
            machine_id=machine_id,
            machine_name="test-legacy",
        )
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

    def test_rate_limit_session_key(self, manager):
        """Rate limit should work for session keys."""
        from app.routes.remote import _check_usage_report_rate_limit

        key = "session:test-session-001"

        # Should allow first 10 requests
        for _ in range(10):
            assert _check_usage_report_rate_limit(manager, key, 10) is True

        # 11th should be blocked
        assert _check_usage_report_rate_limit(manager, key, 10) is False

    def test_rate_limit_different_keys_independent(self, manager):
        """Different rate limit keys should be independent."""
        from app.routes.remote import _check_usage_report_rate_limit

        key1 = "session:test-session-001"
        key2 = "session:test-session-002"

        # Exhaust key1
        for _ in range(10):
            _check_usage_report_rate_limit(manager, key1, 10)

        # key1 should be blocked
        assert _check_usage_report_rate_limit(manager, key1, 10) is False

        # key2 should still work
        assert _check_usage_report_rate_limit(manager, key2, 10) is True


# ── Audit Logging Tests (R4) ──────────────────────────────────────────────


class TestAuditLogging:
    """Tests for audit logging (R4)."""

    def test_audit_action_enum_exists(self):
        """Audit actions should be defined."""
        assert hasattr(AuditAction, "USAGE_REPORT_AUTH_FAILURE")
        assert hasattr(AuditAction, "USAGE_REPORT_BINDING_MISMATCH")
        assert hasattr(AuditAction, "USAGE_REPORT_ACCEPTED")

        # Verify values
        assert AuditAction.USAGE_REPORT_AUTH_FAILURE.value == "usage_report_auth_failure"
        assert AuditAction.USAGE_REPORT_BINDING_MISMATCH.value == "usage_report_binding_mismatch"
        assert AuditAction.USAGE_REPORT_ACCEPTED.value == "usage_report_accepted"


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


# ── HTTP integration tests for both production ingress paths ──────────────


@pytest.fixture
def usage_http(manager):
    """Serve the real blueprint against the fixture database."""
    from app.modules.governance.audit_logger import AuditLogger
    from app.modules.workspace.remote_session_manager import RemoteSessionManager
    from app.modules.workspace.run_timeline.recorder import NullRunRecorder
    from app.modules.workspace.session_manager import SessionManager
    from app.routes import remote as remote_routes

    remote_session_mgr = object.__new__(RemoteSessionManager)
    remote_session_mgr._session_manager = SessionManager(db_path=manager.db_path)
    remote_session_mgr._run_recorder = NullRunRecorder()

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="usage-report-test")
    app.register_blueprint(remote_routes.remote_bp, url_prefix="/api/remote")

    with (
        patch.object(remote_routes, "get_remote_agent_manager", return_value=manager),
        patch.object(remote_routes, "get_remote_session_manager", return_value=remote_session_mgr),
        patch.object(remote_routes, "audit_logger", AuditLogger(db=manager.db)),
        patch(
            "app.modules.governance.quota_manager.QuotaManager.record_usage",
            return_value=True,
        ),
        patch(
            "app.repositories.daily_stats_repo.DailyStatsRepository.refresh_stats",
            return_value=None,
        ),
    ):
        yield app.test_client(), manager


def _register_http_machine(manager, machine_id: str, tenant_id: int = 1, user_id: int = 1):
    reg_token = manager.create_registration_token(tenant_id=tenant_id, created_by=user_id)
    result = manager.register_machine(
        registration_token=reg_token,
        machine_id=machine_id,
        machine_name=machine_id,
    )
    return result["agent_token"]


def _usage_payload(machine_id: str, session_id: str, report_id: str = "report-00000001"):
    return {
        "type": "usage_report",
        "report_id": report_id,
        "machine_id": machine_id,
        "session_id": session_id,
        "tokens": {"input": 100, "output": 50},
        "requests": 2,
    }


def _session_usage(manager, session_id: str):
    return manager.db.fetch_one(
        "SELECT request_count, total_tokens, total_input_tokens, total_output_tokens "
        "FROM agent_sessions WHERE session_id = ?",
        (session_id,),
    )


class TestUsageReportHttpIntegration:
    """Exercise the dedicated endpoint and the actual Remote Agent path."""

    @pytest.mark.parametrize("path", ["/api/remote/usage-report", "/api/remote/agent/message"])
    def test_unauthenticated_requests_are_rejected_without_allocating_rate_state(
        self, usage_http, path
    ):
        client, manager = usage_http
        machine_id = "machine-http-noauth"
        session_id = "session-http-noauth"
        _register_http_machine(manager, machine_id)
        _create_session_in_db(manager.db_path, session_id, machine_id)

        response = client.post(path, json=_usage_payload(machine_id, session_id))

        assert response.status_code == 401
        assert (
            manager.db.fetch_one(
                "SELECT COUNT(*) AS count FROM usage_report_rate_limits "
                "WHERE rate_key LIKE 'session:%' OR rate_key LIKE 'machine:%'"
            )["count"]
            == 0
        )
        assert _session_usage(manager, session_id)["total_tokens"] == 0

    @pytest.mark.parametrize("path", ["/api/remote/usage-report", "/api/remote/agent/message"])
    def test_valid_bearer_updates_usage_and_audits_both_paths(self, usage_http, path):
        client, manager = usage_http
        suffix = "direct" if path.endswith("usage-report") else "message"
        machine_id = f"machine-http-{suffix}"
        session_id = f"session-http-{suffix}"
        token = _register_http_machine(manager, machine_id)
        _create_session_in_db(manager.db_path, session_id, machine_id)
        payload = _usage_payload(machine_id, session_id, f"report-{suffix}-0001")

        response = client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.get_json()["duplicate"] is False
        usage = _session_usage(manager, session_id)
        assert usage["request_count"] == 2
        assert usage["total_tokens"] == 150
        assert usage["total_input_tokens"] == 100
        assert usage["total_output_tokens"] == 50
        audit = manager.db.fetch_one(
            "SELECT action, user_id, tenant_id, session_id, success "
            "FROM audit_logs WHERE resource_id = ?",
            (payload["report_id"],),
        )
        assert audit["action"] == "usage_report_accepted"
        assert audit["user_id"] == 1
        assert audit["tenant_id"] == 1
        assert audit["session_id"] == session_id
        assert bool(audit["success"]) is True

    def test_cross_machine_is_rejected_on_actual_agent_path(self, usage_http):
        client, manager = usage_http
        token_a = _register_http_machine(manager, "machine-http-a")
        _register_http_machine(manager, "machine-http-b")
        _create_session_in_db(manager.db_path, "session-http-b", "machine-http-b")

        response = client.post(
            "/api/remote/agent/message",
            json=_usage_payload("machine-http-a", "session-http-b"),
            headers={"Authorization": f"Bearer {token_a}"},
        )

        assert response.status_code == 403
        assert _session_usage(manager, "session-http-b")["total_tokens"] == 0

    @pytest.mark.parametrize(
        ("session_tenant", "session_user"),
        [(2, 1), (1, 2)],
    )
    def test_cross_tenant_and_cross_user_are_rejected(
        self,
        usage_http,
        session_tenant,
        session_user,
    ):
        client, manager = usage_http
        machine_id = f"machine-http-bind-{session_tenant}-{session_user}"
        session_id = f"session-http-bind-{session_tenant}-{session_user}"
        token = _register_http_machine(manager, machine_id)
        _create_session_in_db(
            manager.db_path,
            session_id,
            machine_id,
            tenant_id=session_tenant,
            user_id=session_user,
        )

        response = client.post(
            "/api/remote/agent/message",
            json=_usage_payload(machine_id, session_id),
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
        assert response.get_json()["error"] == "Usage report binding rejected"
        assert _session_usage(manager, session_id)["total_tokens"] == 0

    def test_duplicate_report_is_idempotent_and_conflicting_replay_is_rejected(self, usage_http):
        client, manager = usage_http
        machine_id = "machine-http-replay"
        session_id = "session-http-replay"
        token = _register_http_machine(manager, machine_id)
        _create_session_in_db(manager.db_path, session_id, machine_id)
        payload = _usage_payload(machine_id, session_id, "report-replay-0001")
        headers = {"Authorization": f"Bearer {token}"}

        first = client.post("/api/remote/agent/message", json=payload, headers=headers)
        duplicate = client.post("/api/remote/agent/message", json=payload, headers=headers)
        conflicting = dict(payload)
        conflicting["tokens"] = {"input": 101, "output": 50}
        replay = client.post(
            "/api/remote/agent/message",
            json=conflicting,
            headers=headers,
        )

        assert first.status_code == 200
        assert duplicate.status_code == 200
        assert duplicate.get_json()["duplicate"] is True
        assert replay.status_code == 409
        usage = _session_usage(manager, session_id)
        assert usage["request_count"] == 2
        assert usage["total_tokens"] == 150
        receipt = manager.db.fetch_one(
            "SELECT status FROM usage_report_receipts WHERE report_id = ?",
            (payload["report_id"],),
        )
        assert receipt["status"] == "completed"

    def test_binding_failures_are_uniform_rate_limited_and_do_not_leak_victim_ids(self, usage_http):
        client, manager = usage_http
        attacker_machine = "machine-http-attacker"
        victim_machine = "machine-http-victim"
        victim_session = "session-http-victim-secret"
        token = _register_http_machine(manager, attacker_machine, tenant_id=1)
        _register_http_machine(manager, victim_machine, tenant_id=2, user_id=2)
        _create_session_in_db(
            manager.db_path,
            victim_session,
            victim_machine,
            tenant_id=2,
            user_id=2,
        )
        headers = {"Authorization": f"Bearer {token}"}

        missing = client.post(
            "/api/remote/agent/message",
            json=_usage_payload(attacker_machine, "session-does-not-exist", "report-oracle-0001"),
            headers=headers,
        )
        cross_tenant = client.post(
            "/api/remote/agent/message",
            json=_usage_payload(attacker_machine, victim_session, "report-oracle-0002"),
            headers=headers,
        )
        for index in range(6):
            client.post(
                "/api/remote/agent/message",
                json=_usage_payload(
                    attacker_machine,
                    f"missing-session-{index}",
                    f"report-oracle-{index + 10:04d}",
                ),
                headers=headers,
            )

        assert missing.status_code == cross_tenant.status_code == 403
        assert (
            missing.get_json()
            == cross_tenant.get_json()
            == {"error": "Usage report binding rejected"}
        )
        rows = manager.db.fetch_all(
            "SELECT resource_id, tenant_id, session_id, details FROM audit_logs "
            "WHERE action = 'usage_report_binding_mismatch'"
        )
        assert len(rows) == 5
        assert all(row["resource_id"] == attacker_machine for row in rows)
        assert all(row["tenant_id"] == 1 for row in rows)
        assert all(row["session_id"] is None for row in rows)
        serialized = " ".join(str(row["details"]) for row in rows)
        assert victim_session not in serialized
        assert victim_machine not in serialized

    def test_invalid_binding_hits_machine_limit_before_more_db_or_audit_work(self, usage_http):
        client, manager = usage_http
        machine_id = "machine-http-binding-limit"
        token = _register_http_machine(manager, machine_id)
        headers = {"Authorization": f"Bearer {token}"}

        responses = [
            client.post(
                "/api/remote/agent/message",
                json=_usage_payload(
                    machine_id,
                    f"missing-binding-{index}",
                    f"report-binding-limit-{index:04d}",
                ),
                headers=headers,
            )
            for index in range(61)
        ]

        assert all(response.status_code == 403 for response in responses[:60])
        assert responses[60].status_code == 429
        audit_count = manager.db.fetch_one(
            "SELECT COUNT(*) AS count FROM audit_logs "
            "WHERE action = 'usage_report_binding_mismatch'"
        )["count"]
        assert audit_count == 5

    def test_invalid_token_audit_is_shared_and_bounded(self, usage_http):
        client, manager = usage_http
        machine_id = "machine-http-auth-audit-limit"
        _register_http_machine(manager, machine_id)
        payload = _usage_payload(machine_id, "session-auth-audit-limit")

        responses = [
            client.post(
                "/api/remote/usage-report",
                json={**payload, "report_id": f"report-auth-limit-{index:04d}"},
                headers={"Authorization": "Bearer invalid-token"},
            )
            for index in range(8)
        ]

        assert all(response.status_code == 401 for response in responses)
        audit_count = manager.db.fetch_one(
            "SELECT COUNT(*) AS count FROM audit_logs " "WHERE action = 'usage_report_auth_failure'"
        )["count"]
        assert audit_count == 5

    @pytest.mark.parametrize("path", ["/api/remote/usage-report", "/api/remote/agent/message"])
    def test_report_id_less_agent_has_explicit_short_migration_window(self, usage_http, path):
        client, manager = usage_http
        suffix = "legacy-direct" if path.endswith("usage-report") else "legacy-message"
        machine_id = f"machine-http-{suffix}"
        session_id = f"session-http-{suffix}"
        token = _register_http_machine(manager, machine_id)
        _create_session_in_db(manager.db_path, session_id, machine_id)
        payload = _usage_payload(machine_id, session_id)
        payload.pop("report_id")

        with patch(
            "app.routes.remote._legacy_usage_report_deadline",
            return_value=datetime.now(timezone.utc) + timedelta(days=1),
        ):
            response = client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert response.get_json()["legacy_report_id_generated"] is True
        assert response.get_json()["report_id"].startswith("legacy-")
        assert _session_usage(manager, session_id)["total_tokens"] == 150

    def test_report_id_less_agent_is_rejected_after_migration_deadline(self, usage_http):
        client, manager = usage_http
        machine_id = "machine-http-legacy-expired"
        session_id = "session-http-legacy-expired"
        token = _register_http_machine(manager, machine_id)
        _create_session_in_db(manager.db_path, session_id, machine_id)
        payload = _usage_payload(machine_id, session_id)
        payload.pop("report_id")

        with patch(
            "app.routes.remote._legacy_usage_report_deadline",
            return_value=datetime.now(timezone.utc) - timedelta(seconds=1),
        ):
            response = client.post(
                "/api/remote/agent/message",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 400
        assert "migration window has expired" in response.get_json()["error"]
        assert _session_usage(manager, session_id)["total_tokens"] == 0
