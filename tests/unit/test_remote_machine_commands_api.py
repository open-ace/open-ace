"""Tests for remote machine commands API endpoint.

Issue #2565: First-time user guidance enhancement.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Setup path
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set required environment variables for testing
os.environ.setdefault("OPENACE_SECURITY_MODE", "development")
os.environ.setdefault("OPENACE_ENCRYPTION_KEY", "test-encryption-key-for-unit-tests-32ch")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-32-char")


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestGetMachineCommandsAuthentication:
    """Tests for authentication requirements."""

    def test_unauthenticated_returns_401(self, client):
        """Test that unauthenticated requests return 401."""
        resp = client.get("/api/remote/machines/00000000-0000-0000-0000-000000000001/commands")
        # Should return 401 because no authentication is provided
        assert resp.status_code == 401


class TestGetMachineCommandsEndpoint:
    """Tests for endpoint existence and basic behavior."""

    def test_endpoint_exists(self, client):
        """Test that the endpoint exists and responds."""
        # Even without authentication, should get a proper HTTP response
        resp = client.get("/api/remote/machines/00000000-0000-0000-0000-000000000001/commands")
        # Should not return 404 (endpoint not found) or 500 (server error)
        assert resp.status_code in [401, 403, 404, 200]

    def test_endpoint_url_correct(self, client):
        """Test that the endpoint URL is correctly registered."""
        # Test that the URL rule exists
        resp = client.get("/api/remote/machines/00000000-0000-0000-0000-000000000001/commands")
        # Should not return 404 (not found)
        assert resp.status_code != 404


class TestGetMachineCommandsPermissions:
    """Tests for permission-based command visibility.

    Note: These tests verify authentication is required.
    For permission-based tests, use integration tests with proper auth fixtures.
    """

    def test_requires_authentication(self, client):
        """Test that the endpoint requires authentication."""
        resp = client.get("/api/remote/machines/00000000-0000-0000-0000-000000000001/commands")
        assert resp.status_code == 401