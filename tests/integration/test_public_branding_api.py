"""
Integration tests for public branding API (Issue #3271).

Tests the public endpoint for retrieving branding configuration,
including system-level and tenant-level branding merge logic.
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from flask import Flask

from app.routes.system import system_bp
from app.utils.config import set_system_setting


@pytest.fixture
def app_with_config(tmp_path):
    """Create a Flask app with system routes and config for testing."""
    # Set up temporary config directory
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")

    # Create initial config
    with open(config_path, "w") as f:
        json.dump({"system_settings": {}}, f)

    # Patch CONFIG_DIR before importing the app
    import app.repositories.database as db_mod

    original_config_dir = db_mod.CONFIG_DIR
    db_mod.CONFIG_DIR = config_dir

    try:
        # Create Flask app
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(system_bp, url_prefix="/api")

        yield app, config_path

    finally:
        # Restore CONFIG_DIR
        db_mod.CONFIG_DIR = original_config_dir


@pytest.fixture
def client(app_with_config):
    """Create a test client."""
    app, config_path = app_with_config
    return app.test_client()


class TestPublicBrandingAPI:
    """Tests for GET /api/public/branding endpoint."""

    def test_returns_system_branding_by_default(self, client):
        """Should return system-level branding when no tenant is specified."""
        # Set up system branding
        set_system_setting("brand_logo_url", "https://example.com/logo.png")
        set_system_setting("brand_system_name", "System ACE")
        set_system_setting(
            "brand_welcome_message", {"en": "Welcome to System", "zh": "欢迎使用系统"}
        )
        set_system_setting("brand_copyright_text", "© 2026 System")

        response = client.get("/api/public/branding")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert data["data"]["logo_url"] == "https://example.com/logo.png"
        assert data["data"]["system_name"] == "System ACE"
        assert data["data"]["welcome_message"]["en"] == "Welcome to System"
        assert data["data"]["welcome_message"]["zh"] == "欢迎使用系统"
        assert data["data"]["copyright_text"] == "© 2026 System"
        assert data["data"]["is_custom"] is False

    def test_returns_empty_branding_when_not_configured(self, app_with_config):
        """Should return empty/None values when branding is not configured."""
        # Create a fresh config file
        app, config_path = app_with_config
        import json

        with open(config_path, "w") as f:
            json.dump({"system_settings": {}}, f)

        # Clear the cache
        from app.utils import config as config_mod

        with config_mod._cache_lock:
            config_mod._cache.pop("_root", None)

        client = app.test_client()
        response = client.get("/api/public/branding")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert data["data"]["logo_url"] is None
        assert data["data"]["system_name"] is None
        assert data["data"]["welcome_message"] == {}
        assert data["data"]["copyright_text"] is None
        assert data["data"]["is_custom"] is False

    def test_tenant_not_exists_returns_system_branding(self, client):
        """Should return system branding when tenant does not exist."""
        # Set up system branding
        set_system_setting("brand_system_name", "System ACE")

        response = client.get("/api/public/branding?tenant_slug=nonexistent")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert data["data"]["system_name"] == "System ACE"
        assert data["data"]["is_custom"] is False

    def test_tenant_without_custom_branding_returns_system(self, client, tmp_path):
        """Should return system branding when tenant has no custom branding."""
        # This test would require database setup for tenant
        # For now, we test the fallback path
        set_system_setting("brand_system_name", "System ACE")

        # Tenant with custom_branding=False should use system branding
        # This would require mocking tenant_service
        response = client.get("/api/public/branding?tenant_slug=test-tenant")
        assert response.status_code == 200

        data = response.get_json()
        # Since tenant doesn't exist in DB, should fall back to system
        assert data["success"] is True
        assert data["data"]["system_name"] == "System ACE"

    def test_partial_branding_fields(self, client):
        """Should handle partial branding configuration."""
        # Only set logo URL
        set_system_setting("brand_logo_url", "https://example.com/logo.png")

        response = client.get("/api/public/branding")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert data["data"]["logo_url"] == "https://example.com/logo.png"
        assert data["data"]["system_name"] is None
        assert data["data"]["welcome_message"] == {}

    def test_multiple_languages_in_welcome_message(self, client):
        """Should support multiple languages in welcome message."""
        set_system_setting(
            "brand_welcome_message",
            {"en": "Welcome", "zh": "欢迎使用", "ja": "ようこそ", "ko": "환영합니다"},
        )

        response = client.get("/api/public/branding")
        assert response.status_code == 200

        data = response.get_json()
        assert data["data"]["welcome_message"]["en"] == "Welcome"
        assert data["data"]["welcome_message"]["zh"] == "欢迎使用"
        assert data["data"]["welcome_message"]["ja"] == "ようこそ"
        assert data["data"]["welcome_message"]["ko"] == "환영합니다"


class TestSystemSettingsUpdate:
    """Tests for PUT /api/settings endpoint with branding validation."""

    def test_update_branding_requires_auth(self, client):
        """Should require authentication for settings update."""
        response = client.put(
            "/api/settings",
            json={"brand_system_name": "New Name"},
        )
        # Should return 401 Unauthorized
        assert response.status_code == 401

    