#!/usr/bin/env python3
"""
Route tests for upload auth key configuration hardening.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture
def app():
    from flask import Flask

    from app.routes.upload import upload_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(upload_bp, url_prefix="/api")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestUploadAuthConfig:
    def test_placeholder_upload_auth_key_disables_endpoint(self, client, monkeypatch):
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "change-me-in-production")

        resp = client.post(
            "/api/upload/usage",
            headers={"X-Upload-Auth": "change-me-in-production"},
            json={"date": "2026-07-17", "tool_name": "codex", "tokens_used": 1},
        )

        assert resp.status_code == 503
        assert resp.get_json()["error"] == "Upload service not configured"

    def test_strong_upload_auth_key_still_allows_upload(self, client, monkeypatch):
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "upload-auth-key-123")

        with patch("app.routes.upload.usage_service.save_usage", return_value=True) as save_usage:
            resp = client.post(
                "/api/upload/usage",
                headers={"X-Upload-Auth": "upload-auth-key-123"},
                json={"date": "2026-07-17", "tool_name": "codex", "tokens_used": 1},
            )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        save_usage.assert_called_once()
