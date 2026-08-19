"""
Pytest configuration for tests/unit.

Add shared fixtures and hooks here.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret-key"})
    return app


@pytest.fixture
def client(app):
    """Create test client without authentication."""
    with app.app_context():
        yield app.test_client()


@pytest.fixture
def admin_client(app):
    """Create authenticated admin test client."""
    # Mock authenticated admin user
    admin_user = {
        "id": 1,
        "username": "admin",
        "email": "admin@test.com",
        "role": "admin",
        "tenant_id": None,  # Platform admin
    }

    with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
        with app.app_context():
            client = app.test_client()
            client.set_cookie("session_token", "test-admin-token")
            yield client


@pytest.fixture
def mock_governance_repo():
    """Mock governance repository for testing.

    This fixture patches the governance_repo instance used by routes.
    """
    mock_repo = MagicMock()

    # Patch the module-level instance in routes.governance
    with patch("app.routes.governance.governance_repo", mock_repo):
        yield mock_repo
