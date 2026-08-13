#!/usr/bin/env python3
"""Unit tests for machine_id UUID validation (Issue #2540).

Tests that non-UUID machine_id inputs return 400 instead of causing
TypeError in slice operations (machine_id[:8]).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def app_context():
    """Create Flask app context for tests that need jsonify."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    with app.app_context():
        yield app


class TestValidateMachineIdFormat:
    """Tests for _validate_machine_id_format function."""

    def test_valid_uuid_accepted(self):
        """Valid UUID string should pass validation."""
        from app.routes.remote import _validate_machine_id_format

        valid_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        is_valid, error = _validate_machine_id_format(valid_uuid)

        assert is_valid is True
        assert error is None

    def test_valid_uuid_uppercase_accepted(self):
        """Valid UUID string in uppercase should pass validation."""
        from app.routes.remote import _validate_machine_id_format

        valid_uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
        is_valid, error = _validate_machine_id_format(valid_uuid)

        assert is_valid is True
        assert error is None

    def test_int_rejected_with_400(self, app_context):
        """Integer machine_id should return 400."""
        from app.routes.remote import _validate_machine_id_format

        is_valid, error = _validate_machine_id_format(1)

        assert is_valid is False
        assert error is not None
        # error is a tuple of (response, status_code)
        response, status_code = error
        assert status_code == 400
        json_data = response.get_json()
        assert "machine_id must be a valid UUID" in json_data["error"]

    def test_empty_string_rejected(self, app_context):
        """Empty string should return 400."""
        from app.routes.remote import _validate_machine_id_format

        is_valid, error = _validate_machine_id_format("")

        assert is_valid is False
        assert error is not None
        response, status_code = error
        assert status_code == 400

    def test_none_rejected(self, app_context):
        """None value should return 400."""
        from app.routes.remote import _validate_machine_id_format

        is_valid, error = _validate_machine_id_format(None)

        assert is_valid is False
        assert error is not None
        response, status_code = error
        assert status_code == 400

    def test_invalid_uuid_format_rejected(self, app_context):
        """Invalid UUID format should return 400."""
        from app.routes.remote import _validate_machine_id_format

        is_valid, error = _validate_machine_id_format("not-a-uuid")

        assert is_valid is False
        assert error is not None
        response, status_code = error
        assert status_code == 400

    def test_partial_uuid_rejected(self, app_context):
        """Partial UUID (missing parts) should return 400."""
        from app.routes.remote import _validate_machine_id_format

        is_valid, error = _validate_machine_id_format("a1b2c3d4-e5f6-7890")

        assert is_valid is False
        assert error is not None
        response, status_code = error
        assert status_code == 400


class TestSafeMachineIdPrefix:
    """Tests for safe_machine_id_prefix function."""

    def test_safe_prefix_with_none(self):
        """None should return 'unknown'."""
        from app.routes.remote import safe_machine_id_prefix

        assert safe_machine_id_prefix(None) == "unknown"

    def test_safe_prefix_with_empty_string(self):
        """Empty string should return 'unknown'."""
        from app.routes.remote import safe_machine_id_prefix

        assert safe_machine_id_prefix("") == "unknown"

    def test_safe_prefix_with_int(self):
        """Integer should be converted to string and sliced."""
        from app.routes.remote import safe_machine_id_prefix

        assert safe_machine_id_prefix(123456789) == "12345678"

    def test_safe_prefix_with_short_int(self):
        """Short integer should return full string representation."""
        from app.routes.remote import safe_machine_id_prefix

        assert safe_machine_id_prefix(123) == "123"

    def test_safe_prefix_with_uuid(self):
        """UUID string should return first 8 characters."""
        from app.routes.remote import safe_machine_id_prefix

        uuid_str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert safe_machine_id_prefix(uuid_str) == "a1b2c3d4"

    def test_safe_prefix_preserves_case(self):
        """Should preserve case of the input."""
        from app.routes.remote import safe_machine_id_prefix

        uuid_str = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
        assert safe_machine_id_prefix(uuid_str) == "A1B2C3D4"


class TestTerminalStartWithInvalidMachineId:
    """Integration tests for terminal endpoints with invalid machine_id."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"

        # Register the remote blueprint
        from app.routes.remote import remote_bp

        app.register_blueprint(remote_bp, url_prefix="/api/remote")

        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    def test_terminal_start_requires_auth(self, client):
        """POST /terminal/start requires authentication.

        The endpoint has @machine_access_required decorator which checks auth first.
        This test verifies that authentication is required before reaching validation.
        """
        response = client.post(
            "/api/remote/terminal/start", json={"machine_id": 1}, content_type="application/json"
        )

        # Should return 401 (unauthorized) because no auth provided
        assert response.status_code == 401

    def test_agent_message_with_int_machine_id_returns_400(self, client):
        """POST /agent/message with int machine_id should return 400, not 500.

        Issue #2540: agent_message doesn't have @machine_access_required decorator,
        so it needs explicit validation.
        """
        response = client.post(
            "/api/remote/agent/message",
            json={"machine_id": 1, "type": "heartbeat"},
            content_type="application/json",
        )

        # Should return 400 (bad request), not 500 (internal server error)
        assert response.status_code == 400
        json_data = response.get_json()
        assert "machine_id must be a valid UUID" in json_data.get("error", "")

    def test_agent_message_with_invalid_uuid_returns_400(self, client):
        """POST /agent/message with invalid UUID format should return 400."""
        response = client.post(
            "/api/remote/agent/message",
            json={"machine_id": "not-a-uuid", "type": "heartbeat"},
            content_type="application/json",
        )

        assert response.status_code == 400
        json_data = response.get_json()
        assert "machine_id must be a valid UUID" in json_data.get("error", "")

    def test_terminal_cli_start_requires_auth(self, client):
        """POST /terminal/cli/start requires authentication.

        The endpoint has @machine_access_required decorator which checks auth first.
        """
        response = client.post(
            "/api/remote/terminal/cli/start",
            json={"machine_id": 1},
            content_type="application/json",
        )

        # Should return 401 (unauthorized) because no auth provided
        assert response.status_code == 401

    def test_usage_report_with_int_machine_id_returns_400(self, client):
        """POST /usage-report with int machine_id should return 400, not 401.

        Issue #2540: usage_report should validate UUID format before checking machine existence.
        """
        response = client.post(
            "/api/remote/usage-report", json={"machine_id": 1}, content_type="application/json"
        )

        # Should return 400 (bad request), not 401/500
        assert response.status_code == 400
        json_data = response.get_json()
        assert "machine_id must be a valid UUID" in json_data.get("error", "")

    def test_usage_report_with_invalid_uuid_returns_400(self, client):
        """POST /usage-report with invalid UUID format should return 400."""
        response = client.post(
            "/api/remote/usage-report",
            json={"machine_id": "not-a-uuid"},
            content_type="application/json",
        )

        assert response.status_code == 400
        json_data = response.get_json()
        assert "machine_id must be a valid UUID" in json_data.get("error", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
