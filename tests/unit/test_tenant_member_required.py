"""
Tests for tenant_member_required decorator.

Issue #3082: Manager 角色告警管理入口
"""

import pytest
from flask import Flask, g, jsonify
from unittest.mock import patch, MagicMock

from app.auth.decorators import tenant_member_required


@pytest.fixture
def app():
    """Create a Flask app for testing."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret-key"

    @app.route("/protected")
    @tenant_member_required
    def protected_route():
        return jsonify({"success": True, "user_id": g.user_id})

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


def test_tenant_member_required_accepts_manager_role(app, client):
    """Test that manager role is accepted by tenant_member_required."""
    # Mock user with manager role
    mock_user = {
        "id": 1,
        "username": "manager_user",
        "role": "manager",
        "tenant_id": 1,
        "must_change_password": False,
    }

    with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
            response = client.get("/protected")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            assert data["user_id"] == 1


def test_tenant_member_required_accepts_tenant_admin_role(app, client):
    """Test that tenant_admin role is accepted by tenant_member_required."""
    # Mock user with tenant_admin role
    mock_user = {
        "id": 2,
        "username": "tenant_admin_user",
        "role": "tenant_admin",
        "tenant_id": 1,
        "must_change_password": False,
    }

    with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
            response = client.get("/protected")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True


def test_tenant_member_required_accepts_platform_admin_role(app, client):
    """Test that platform_admin role is accepted by tenant_member_required."""
    # Mock user with platform_admin role
    mock_user = {
        "id": 3,
        "username": "platform_admin_user",
        "role": "platform_admin",
        "tenant_id": None,  # Platform admin has no tenant
        "must_change_password": False,
    }

    with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
            response = client.get("/protected")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True


def test_tenant_member_required_accepts_legacy_admin_role(app, client):
    """Test that legacy admin role is accepted by tenant_member_required."""
    # Mock user with admin role
    mock_user = {
        "id": 4,
        "username": "admin_user",
        "role": "admin",
        "tenant_id": None,
        "must_change_password": False,
    }

    with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
            response = client.get("/protected")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True


def test_tenant_member_required_rejects_user_role(app, client):
    """Test that regular user role is rejected by tenant_member_required."""
    # Mock user with user role
    mock_user = {
        "id": 5,
        "username": "regular_user",
        "role": "user",
        "tenant_id": 1,
        "must_change_password": False,
    }

    with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
            response = client.get("/protected")
            assert response.status_code == 403
            data = response.get_json()
            assert "Tenant member access required" in data["error"]


def test_tenant_member_required_rejects_missing_token(app, client):
    """Test that missing token is rejected."""
    with patch("app.auth.decorators._extract_session_token", return_value=""):
        response = client.get("/protected")
        assert response.status_code == 401
        data = response.get_json()
        assert "Authentication required" in data["error"]


def test_tenant_member_required_rejects_invalid_token(app, client):
    """Test that invalid token is rejected."""
    with patch("app.auth.decorators._extract_session_token", return_value="invalid-token"):
        with patch("app.auth.decorators._load_user_from_token", return_value=None):
            response = client.get("/protected")
            assert response.status_code == 401
            data = response.get_json()
            assert "Invalid or expired session" in data["error"]


def test_tenant_member_required_sets_flask_context(app, client):
    """Test that Flask context is set correctly."""
    # Mock user with manager role
    mock_user = {
        "id": 1,
        "username": "manager_user",
        "role": "manager",
        "tenant_id": 1,
        "must_change_password": False,
    }

    with app.test_request_context("/protected"):
        with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
            with patch("app.auth.decorators._load_user_from_token", return_value=mock_user):
                # Manually invoke the decorator
                @tenant_member_required
                def test_route():
                    # Verify Flask context is set
                    assert hasattr(g, "user")
                    assert g.user == mock_user
                    assert hasattr(g, "user_id")
                    assert g.user_id == 1
                    assert hasattr(g, "user_role")
                    assert g.user_role == "manager"
                    assert hasattr(g, "tenant_id")
                    assert g.tenant_id == 1
                    return jsonify({"success": True})

                response = test_route()
                assert response.status_code == 200