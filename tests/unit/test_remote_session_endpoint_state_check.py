"""
Endpoint-level tests for Issue #2531: Session state check across remote session endpoints.

Tests the integration of state checking in the actual Flask route handlers.
"""

from unittest.mock import patch

import pytest


class TestSendRemoteMessageEndpoint:
    """Tests for POST /sessions/<id>/chat endpoint with state checking."""

    def test_send_message_to_stopped_session_returns_409(self, app_context):
        """Issue #2531: Sending message to stopped session returns 409."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "stopped"},
                None,
            )

            from app.routes.remote import send_remote_message

            response = send_remote_message("test-session")

            assert response[1] == 409
            data = response[0].get_json()
            assert data["success"] is False
            assert "stopped" in data["error"].lower()

    def test_send_message_to_paused_session_returns_409(self, app_context):
        """Issue #2531: Sending message to paused session returns 409."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "paused"},
                None,
            )

            from app.routes.remote import send_remote_message

            response = send_remote_message("test-session")

            assert response[1] == 409
            data = response[0].get_json()
            assert "paused" in data["error"].lower()


class TestAbortRemoteRequestEndpoint:
    """Tests for POST /sessions/<id>/abort endpoint with state handling."""

    def test_abort_on_ended_returns_200_idempotent(self, app_context):
        """Issue #2531: Abort on ended session returns 200 (idempotent)."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "stopped"},
                None,
            )

            from app.routes.remote import abort_remote_request

            response = abort_remote_request("test-session")

            # Response object has status_code attribute
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_abort_on_paused_calls_underlying(self, app_context):
        """Issue #2531: Abort on paused session should call underlying method."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "paused"},
                None,
            )
            with patch("app.routes.remote.get_remote_session_manager") as mock_mgr:
                mock_mgr.return_value.abort_request.return_value = True

                from app.routes.remote import abort_remote_request

                response = abort_remote_request("test-session")

                # Should call underlying method, not return early
                mock_mgr.return_value.abort_request.assert_called_once()
                assert response.status_code == 200


class TestStopRemoteSessionEndpoint:
    """Tests for POST /sessions/<id>/stop endpoint with state handling."""

    def test_stop_on_ended_returns_200_idempotent(self, app_context):
        """Issue #2531: Stop on ended session returns 200 (idempotent)."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "stopped"},
                None,
            )

            from app.routes.remote import stop_remote_session

            response = stop_remote_session("test-session")

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_stop_on_paused_returns_200(self, app_context):
        """Issue #2531: Stop on paused session returns 200."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "paused"},
                None,
            )
            with patch("app.routes.remote.get_remote_session_manager") as mock_mgr:
                mock_mgr.return_value.stop_session.return_value = True

                from app.routes.remote import stop_remote_session

                response = stop_remote_session("test-session")

                # Should call underlying method
                mock_mgr.return_value.stop_session.assert_called_once()
                assert response.status_code == 200


class TestGetRemoteSessionEndpoint:
    """Tests for GET /sessions/<id> endpoint with state handling."""

    def test_get_ended_session_returns_409(self, app_context):
        """Issue #2531: GET ended session returns 409."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {
                    "session_id": "test-session",
                    "status": "completed",
                    "messages": [],
                },
                None,
            )

            from app.routes.remote import get_remote_session

            response = get_remote_session("test-session")

            assert response[1] == 409
            data = response[0].get_json()
            assert data["success"] is False

    def test_get_paused_session_returns_409(self, app_context):
        """Issue #2531: GET paused session returns 409."""
        with patch("app.routes.remote._check_session_access") as mock_access:
            mock_access.return_value = (
                {"session_id": "test-session", "status": "paused", "messages": []},
                None,
            )

            from app.routes.remote import get_remote_session

            response = get_remote_session("test-session")

            assert response[1] == 409


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
