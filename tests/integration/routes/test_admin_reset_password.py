"""Route tests for admin reset-password API endpoint."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Stub pwd only when the platform genuinely does not provide it. Checking
# sys.modules alone replaced the real Unix module and leaked into later tests.
try:
    import pwd  # noqa: F401
except ImportError:  # pragma: no cover - exercised on Windows
    sys.modules["pwd"] = type(sys)("pwd")

import pytest  # noqa: E402

from app.routes.admin import admin_bp  # noqa: E402


@pytest.fixture
def app():
    """Create a minimal Flask app with admin routes."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    return app


@pytest.fixture
def admin_client(app):
    """Create an admin-authenticated client."""
    test_client = app.test_client()

    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def post(self, *args, **kwargs):
            with patch(
                "app.auth.decorators._extract_session_token",
                return_value="test-token",
            ):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={
                        "id": 1,
                        "role": "admin",
                        "username": "test_admin",
                    },
                ):
                    return self._client.post(*args, **kwargs)

    return AuthenticatedClient(test_client)


def _common_mocks():
    """Return a context manager that patches all admin.reset_password dependencies."""
    return (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=None),
        patch("app.routes.admin.user_repo"),
    )


def test_reset_password_auto_generate_no_body(admin_client):
    """TC1: No password body -- should auto-generate and return temporary password."""
    mock_hash, mock_settings, mock_user_repo = _common_mocks()
    with mock_hash, mock_settings, mock_user_repo as mu:
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}
        mu.update_password.return_value = True
        mu.set_must_change_password.return_value = True

        response = admin_client.post("/api/admin/users/1/reset-password")

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "temporary_password" in data
    assert len(data["temporary_password"]) >= 12
    mu.set_must_change_password.assert_called_once_with(1, True)


def test_reset_password_with_valid_custom_password(admin_client):
    """TC2: Valid custom password -- should use it and return it."""
    settings = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": True,
    }
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password") as mh,
        patch("app.routes.admin.get_security_settings_cached", return_value=settings),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}
        mu.update_password.return_value = True
        mu.set_must_change_password.return_value = True

        custom_pw = "MyCustomP@ss123"
        response = admin_client.post(
            "/api/admin/users/1/reset-password",
            json={"password": custom_pw},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["temporary_password"] == custom_pw
    mh.assert_called_once_with(custom_pw)
    mu.set_must_change_password.assert_called_once_with(1, True)


def test_reset_password_with_invalid_short_password(admin_client):
    """TC3: Too short password -- should return 400."""
    settings = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": True,
    }
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=settings),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}

        response = admin_client.post(
            "/api/admin/users/1/reset-password",
            json={"password": "short"},
        )

    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    mu.update_password.assert_not_called()


def test_reset_password_with_invalid_missing_complexity(admin_client):
    """TC3b: Password missing required character type -- should return 400."""
    settings = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": True,
    }
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=settings),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}

        response = admin_client.post(
            "/api/admin/users/1/reset-password",
            json={"password": "alllowercase123"},
        )

    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    mu.update_password.assert_not_called()


def test_reset_password_user_not_found(admin_client):
    """TC5: Non-existent user -- should return 404."""
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=None),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = None

        response = admin_client.post("/api/admin/users/9999/reset-password")

    assert response.status_code == 404
    data = response.get_json()
    assert "error" in data


def test_reset_password_custom_sets_must_change_true(admin_client):
    """TC4: Custom password path must set must_change_password to True."""
    settings = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": True,
    }
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=settings),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}
        mu.update_password.return_value = True
        mu.set_must_change_password.return_value = True

        response = admin_client.post(
            "/api/admin/users/1/reset-password",
            json={"password": "ValidP@ss123"},
        )

    assert response.status_code == 200
    assert mu.update_password.called
    mu.set_must_change_password.assert_called_once_with(1, True)


def test_reset_password_auto_generate_sets_must_change_true(admin_client):
    """TC4b: Auto-generated password path must also set must_change_password to True."""
    settings = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": True,
    }
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=settings),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}
        mu.update_password.return_value = True
        mu.set_must_change_password.return_value = True

        response = admin_client.post("/api/admin/users/1/reset-password")

    assert response.status_code == 200
    assert mu.update_password.called
    mu.set_must_change_password.assert_called_once_with(1, True)


def test_reset_password_with_empty_password_string(admin_client):
    """TC6: Empty password string in body -- should return 400, not auto-generate."""
    with (
        patch("app.routes.admin.hash_password", return_value="hashed_password"),
        patch("app.routes.admin.get_security_settings_cached", return_value=None),
        patch("app.routes.admin.user_repo") as mu,
    ):
        mu.get_user_by_id.return_value = {"id": 1, "username": "testuser"}

        response = admin_client.post(
            "/api/admin/users/1/reset-password",
            json={"password": ""},
        )

    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "empty" in data["error"].lower()
    mu.update_password.assert_not_called()
