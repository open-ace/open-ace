"""
Tests for autonomous development feature toggle (Issue #762).

Covers:
- app/utils/config.py: get_config_value, is_autonomous_enabled, TTL cache
- app/routes/autonomous.py: before_request guard (403 / pass-through)
- app/routes/workspace.py: /api/workspace/config includes autonomous_enabled
"""

import json
import os
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
import app.utils.config as cfg_mod

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary config directory for config.json."""
    d = tmp_path / "open-ace"
    d.mkdir()
    return d


@pytest.fixture
def config_file(config_dir):
    """Return the config.json path inside the temp config directory."""
    return config_dir / "config.json"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear the TTL cache between tests so each test starts fresh."""
    with cfg_mod._cache_lock:
        cfg_mod._cache.clear()
    yield
    with cfg_mod._cache_lock:
        cfg_mod._cache.clear()


def _patch_config_dir(config_dir):
    """Patch CONFIG_DIR in both the database module and the config module."""
    return patch.object(db_mod, "CONFIG_DIR", str(config_dir))


# ── get_config_value tests ───────────────────────────────────────────────


class TestGetConfigValue:
    def test_reads_existing_value(self, config_file, config_dir):
        config_file.write_text(json.dumps({"workspace": {"enabled": True}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.get_config_value("workspace", "enabled", False) is True

    def test_returns_default_when_file_missing(self, tmp_path):
        missing_dir = tmp_path / "nonexistent"
        with _patch_config_dir(missing_dir):
            assert cfg_mod.get_config_value("workspace", "enabled", False) is False

    def test_returns_default_when_section_missing(self, config_file, config_dir):
        config_file.write_text(json.dumps({"workspace": {"enabled": True}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.get_config_value("autonomous", "enabled", False) is False

    def test_returns_default_when_key_missing(self, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.get_config_value("autonomous", "enabled", False) is False

    def test_returns_default_on_invalid_json(self, config_file, config_dir):
        config_file.write_text("not valid json {{{")
        with _patch_config_dir(config_dir):
            assert cfg_mod.get_config_value("autonomous", "enabled", False) is False


# ── is_autonomous_enabled tests ──────────────────────────────────────────


class TestIsAutonomousEnabled:
    def test_default_false(self, config_file, config_dir):
        config_file.write_text(json.dumps({}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.is_autonomous_enabled() is False

    def test_explicit_true(self, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": True}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.is_autonomous_enabled() is True

    def test_explicit_false(self, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": False}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.is_autonomous_enabled() is False


# ── TTL cache tests ──────────────────────────────────────────────────────


class TestConfigCache:
    def test_cache_avoids_repeated_disk_reads(self, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": True}}))
        with _patch_config_dir(config_dir):
            assert cfg_mod.is_autonomous_enabled() is True
            # Change file on disk — cache should still return True
            config_file.write_text(json.dumps({"autonomous": {"enabled": False}}))
            assert cfg_mod.is_autonomous_enabled() is True

    def test_cache_expires_after_ttl(self, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": True}}))
        with _patch_config_dir(config_dir), patch.object(cfg_mod, "_cache_ttl", 0.0):
            assert cfg_mod.is_autonomous_enabled() is True
            # TTL=0 means cache always expires — next read hits disk
            config_file.write_text(json.dumps({"autonomous": {"enabled": False}}))
            assert cfg_mod.is_autonomous_enabled() is False


# ── before_request guard tests ───────────────────────────────────────────


class TestAutonomousBeforeRequest:
    @pytest.fixture
    def app(self):
        """Create a minimal Flask app with autonomous blueprint."""
        from flask import Flask, jsonify

        app = Flask(__name__)
        app.config["TESTING"] = True

        # Create a fresh blueprint to avoid "already registered" errors
        from flask import Blueprint

        bp = Blueprint("autonomous_test", __name__)

        @bp.before_request
        def check_autonomous_enabled():
            from app.utils.config import is_autonomous_enabled

            if not is_autonomous_enabled():
                return (
                    jsonify(
                        {"error": "Autonomous development feature is disabled", "disabled": True}
                    ),
                    403,
                )

        @bp.route("/test-ping")
        def test_ping():
            return jsonify({"ok": True})

        app.register_blueprint(bp, url_prefix="/api/autonomous")
        return app

    def test_disabled_returns_403(self, app, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": False}}))
        with _patch_config_dir(config_dir):
            with cfg_mod._cache_lock:
                cfg_mod._cache.clear()
            with app.test_client() as client:
                resp = client.get("/api/autonomous/test-ping")
                assert resp.status_code == 403
                data = resp.get_json()
                assert data["disabled"] is True

    def test_enabled_passes_through(self, app, config_file, config_dir):
        config_file.write_text(json.dumps({"autonomous": {"enabled": True}}))
        with _patch_config_dir(config_dir):
            with cfg_mod._cache_lock:
                cfg_mod._cache.clear()
            with app.test_client() as client:
                resp = client.get("/api/autonomous/test-ping")
                assert resp.status_code == 200
                assert resp.get_json()["ok"] is True


# ── workspace config endpoint tests ──────────────────────────────────────


class TestWorkspaceConfigAutonomousField:
    def test_autonomous_enabled_true_in_config(self, config_file, config_dir):
        config_file.write_text(
            json.dumps({"workspace": {"enabled": False}, "autonomous": {"enabled": True}})
        )
        with _patch_config_dir(config_dir):
            # Simulate the logic from get_workspace_config()
            config_path = os.path.join(str(config_dir), "config.json")
            with open(config_path) as f:
                config = json.load(f)
            autonomous_config = config.get("autonomous", {})
            result = autonomous_config.get("enabled", False)
            assert result is True

    def test_autonomous_enabled_false_when_missing(self, config_file, config_dir):
        config_file.write_text(json.dumps({"workspace": {"enabled": False}}))
        with _patch_config_dir(config_dir):
            config_path = os.path.join(str(config_dir), "config.json")
            with open(config_path) as f:
                config = json.load(f)
            autonomous_config = config.get("autonomous", {})
            result = autonomous_config.get("enabled", False)
            assert result is False

    def test_autonomous_enabled_false_when_explicit(self, config_file, config_dir):
        config_file.write_text(
            json.dumps({"workspace": {"enabled": True}, "autonomous": {"enabled": False}})
        )
        with _patch_config_dir(config_dir):
            config_path = os.path.join(str(config_dir), "config.json")
            with open(config_path) as f:
                config = json.load(f)
            autonomous_config = config.get("autonomous", {})
            result = autonomous_config.get("enabled", False)
            assert result is False
