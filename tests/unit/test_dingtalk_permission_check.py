"""Tests for DingTalk connection test with org-sync permission checks (Issue #3022).

The ``/management/dingtalk-config/test`` endpoint must now verify not only that
AppKey/AppSecret can obtain an access token, but also that the app has the
necessary permissions for organisation sync (department read and member read).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    """Create a minimal Flask app with the notification-integrations blueprint."""
    from flask import Flask

    from app.routes.notification_integrations import notification_integrations_bp

    app = Flask(__name__)
    app.register_blueprint(notification_integrations_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    return app


@pytest.fixture()
def admin_client(app):
    """A test client that bypasses admin authentication."""

    class _AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def post(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="tok"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "admin"},
                ):
                    return self._client.post(*args, **kwargs)

    return _AuthenticatedClient(app.test_client())


# ---------------------------------------------------------------------------
# Helpers to build mock responses
# ---------------------------------------------------------------------------


def _mock_token_response(token="fake-token"):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"accessToken": token, "expireIn": 7200}
    return resp


def _mock_oapi_success():
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"errcode": 0, "errmsg": "ok", "result": []}
    return resp


def _mock_oapi_error(errcode, errmsg):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"errcode": errcode, "errmsg": errmsg}
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDingTalkPermissionCheck:
    """Verify that the connection test now checks org-sync permissions."""

    def test_missing_credentials_returns_400(self, admin_client):
        """Without AppKey/AppSecret the endpoint should reject immediately."""
        with patch(
            "app.routes.notification_integrations.get_notification_settings_repository",
            return_value=MagicMock(get=MagicMock(return_value={})),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={},
            )
        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "required" in data["message"].lower()

    def test_all_checks_pass(self, admin_client):
        """When all checks succeed, success=True and all checks are 'passed'."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "checks" in data
        assert data["checks"]["access_token"]["status"] == "passed"
        assert data["checks"]["department_list"]["status"] == "passed"
        assert data["checks"]["user_list"]["status"] == "passed"

    def test_token_failure_short_circuits(self, admin_client):
        """When token exchange fails, dept/user checks should NOT be attempted."""
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}  # No accessToken

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                return_value=resp,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is False
        assert "checks" in data
        assert data["checks"]["access_token"]["status"] == "failed"
        # department_list and user_list should NOT be present (short-circuited)
        assert "department_list" not in data["checks"]
        assert "user_list" not in data["checks"]

    def test_department_permission_missing(self, admin_client):
        """When department API returns permission error, department_list fails."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            if "department/listsub" in url:
                return _mock_oapi_error(60011, "no permission for this API")
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        data = response.get_json()
        assert data["success"] is False
        assert data["checks"]["access_token"]["status"] == "passed"
        assert data["checks"]["department_list"]["status"] == "failed"
        assert "permission" in data["checks"]["department_list"]["message"].lower()
        # user_list should still have been attempted
        assert "user_list" in data["checks"]
        assert data["checks"]["user_list"]["status"] == "passed"

    def test_user_permission_missing(self, admin_client):
        """When user list API returns permission error, user_list fails."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            if "user/list" in url:
                return _mock_oapi_error(60011, "no permission for this API")
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        data = response.get_json()
        assert data["success"] is False
        assert data["checks"]["access_token"]["status"] == "passed"
        assert data["checks"]["department_list"]["status"] == "passed"
        assert data["checks"]["user_list"]["status"] == "failed"
        assert "permission" in data["checks"]["user_list"]["message"].lower()

    def test_both_permissions_missing(self, admin_client):
        """When both dept and user APIs fail, both checks report failure."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            return _mock_oapi_error(60011, "no permission for this API")

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        data = response.get_json()
        assert data["success"] is False
        assert data["checks"]["department_list"]["status"] == "failed"
        assert data["checks"]["user_list"]["status"] == "failed"

    def test_network_error_on_token(self, admin_client):
        """A network error during token exchange should return 502."""
        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=ConnectionError("network down"),
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        assert response.status_code == 502
        data = response.get_json()
        assert data["success"] is False
        assert data["checks"]["access_token"]["status"] == "failed"

    def test_ssrf_blocked_returns_403(self, admin_client):
        """An SSRF-blocked token request should return 403."""
        from app.utils.outbound_url_guard import OutboundUrlBlockedError

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=OutboundUrlBlockedError("blocked"),
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        assert response.status_code == 403
        data = response.get_json()
        assert data["success"] is False

    def test_response_does_not_leak_secrets(self, admin_client):
        """Response must never contain AppSecret or access token values."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response("super-secret-token-xyz")
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "my-key", "app_secret": "my-secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "my-key", "app_secret": "my-secret"},
            )

        body = response.get_data(as_text=True)
        assert "my-secret" not in body
        assert "super-secret-token-xyz" not in body

    def test_network_error_on_dept_check_does_not_crash(self, admin_client):
        """A transport error during dept check should degrade gracefully."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            if "department/listsub" in url:
                raise ConnectionError("timeout")
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={"app_key": "key", "app_secret": "secret"},
            )

        data = response.get_json()
        assert data["checks"]["department_list"]["status"] == "failed"
        # user_list should still be attempted despite dept failure
        assert data["checks"]["user_list"]["status"] == "passed"

    def test_legacy_fields_still_present(self, admin_client):
        """Backward compatibility: 'success' and 'message' fields must be present."""

        def fake_safe_request(method, url, **kwargs):
            if "oauth2/accessToken" in url:
                return _mock_token_response()
            return _mock_oapi_success()

        with (
            patch(
                "app.routes.notification_integrations.get_notification_settings_repository",
                return_value=MagicMock(
                    get=MagicMock(return_value={"app_key": "key", "app_secret": "secret"})
                ),
            ),
            patch(
                "app.routes.notification_integrations.safe_request",
                side_effect=fake_safe_request,
            ),
        ):
            response = admin_client.post(
                "/api/management/dingtalk-config/test",
                json={},
            )

        data = response.get_json()
        assert "success" in data
        assert "message" in data
        assert isinstance(data["success"], bool)
        assert isinstance(data["message"], str)
