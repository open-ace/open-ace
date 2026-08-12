"""
Unit tests for Issue #2531: Session state check functionality.

Tests the state checking logic for remote session operations:
- Non-idempotent endpoints should reject ended/paused sessions
- Idempotent endpoints (abort/stop) should handle ended/paused differently
- Unknown/null status should return 500
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCheckSessionState:
    """Tests for _check_session_state function."""

    def test_active_session_returns_none(self, app_context):
        """Active session should allow operation."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "active"}
        result = _check_session_state(session_info, "test_operation")

        assert result is None

    def test_paused_session_returns_409(self, app_context):
        """Paused session should return 409."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "paused"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 409
        data = response.get_json()
        assert data["success"] is False
        assert "paused" in data["error"].lower()

    def test_completed_session_returns_409(self, app_context):
        """Completed session should return 409."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "completed"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 409
        data = response.get_json()
        assert data["success"] is False
        assert "ended" in data["error"].lower()

    def test_stopped_session_returns_409(self, app_context):
        """Stopped session should return 409."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "stopped"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 409
        data = response.get_json()
        assert data["success"] is False

    def test_error_session_returns_409(self, app_context):
        """Error session should return 409."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "error"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 409
        data = response.get_json()
        assert data["success"] is False

    def test_null_status_returns_500(self, app_context):
        """Null status should return 500."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": None}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert "unavailable" in data["error"].lower()

    def test_unknown_status_returns_500(self, app_context):
        """Unknown status should return 500."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "unknown_state"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert "unknown" in data["error"].lower()

    def test_missing_status_key_returns_500(self, app_context):
        """Missing status key should return 500."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, status_code = result
        assert status_code == 500


class TestStateMessages:
    """Tests for state message content."""

    def test_paused_message_is_correct(self, app_context):
        """Paused session message should mention resume."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "paused"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, _ = result
        data = response.get_json()
        assert "resume" in data["error"].lower()

    def test_completed_message_is_correct(self, app_context):
        """Completed session message should mention ended."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "completed"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, _ = result
        data = response.get_json()
        assert "ended" in data["error"].lower()

    def test_stopped_message_is_correct(self, app_context):
        """Stopped session message should mention stopped."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "stopped"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, _ = result
        data = response.get_json()
        assert "stopped" in data["error"].lower()


class TestSessionStatusField:
    """Tests for session_status field in response."""

    def test_response_includes_session_status(self, app_context):
        """409 response should include session_status field."""
        from app.routes.remote import _check_session_state

        session_info = {"session_id": "test-session", "status": "paused"}
        result = _check_session_state(session_info, "test_operation")

        assert result is not None
        response, _ = result
        data = response.get_json()
        assert "session_status" in data
        assert data["session_status"] == "paused"


# ════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════


@pytest.fixture
def app_context():
    """Create test app context with mocked g.user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True

    with app.test_request_context():
        from flask import g

        # Mock user for audit logging
        g.user = {"id": 1, "username": "test_user"}
        g._user_loaded = True
        yield