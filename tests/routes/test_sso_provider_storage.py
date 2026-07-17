#!/usr/bin/env python3
"""
Route tests for SSO provider secret migration and encrypted storage.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.utils.smtp_crypto as smtp_crypto
from app.modules.sso.manager import SSOManager
from app.repositories.database import Database, adapt_boolean_value
from app.repositories.schema_init import load_schema_from_file
from app.routes.sso import sso_bp

ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def sso_manager(tmp_path, monkeypatch):
    """Isolated SSO manager for route tests."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-route-sso-encryption-key")
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{tmp_path / 'route-sso.db'}")
    manager = SSOManager(db=db)
    load_schema_from_file(db_url=manager.db.db_url, dialect="sqlite")

    try:
        yield manager
    finally:
        smtp_crypto._password_manager_instance = None


@pytest.fixture
def client(sso_manager):
    """Flask client with auth and shared SSO manager patched in."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sso_bp)

    audit_logger = MagicMock()
    admin_user = {"id": 1, "username": "admin", "role": "admin", "tenant_id": None}

    with (
        patch("app.routes.sso.get_sso_manager", return_value=sso_manager),
        patch("app.routes.sso.get_audit_logger", return_value=audit_logger),
        patch("app.routes.sso.user_repo.get_user_by_id", return_value=admin_user),
        patch("app.auth.decorators._authenticate", return_value=(True, ADMIN_SESSION)),
    ):
        yield app.test_client()


def test_update_provider_rewrites_legacy_plaintext_secret_as_encrypted(client, sso_manager):
    """Updating a legacy provider should preserve the secret without storing plaintext."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sso_manager.db.execute(
        """
        INSERT INTO sso_providers (name, provider_type, config, tenant_id, is_active, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "google",
            "oauth2",
            json.dumps(
                {
                    "name": "google",
                    "provider_type": "oauth2",
                    "client_id": "legacy-client",
                    "client_secret": "legacy-secret",
                    "authorization_url": "https://example.com/oauth/authorize",
                    "token_url": "https://example.com/oauth/token",
                    "redirect_uri": "https://app.example.com/callback",
                    "scope": ["openid", "profile", "email"],
                }
            ),
            None,
            adapt_boolean_value(True),
            now,
        ),
    )

    resp = client.put(
        "/api/sso/providers/google",
        headers={"Authorization": "Bearer t"},
        json={
            "client_id": "updated-client",
            "authorization_url": "https://example.com/oauth2/authorize",
        },
    )

    assert resp.status_code == 200

    row = sso_manager.db.fetch_one(
        "SELECT config FROM sso_providers WHERE name = ?",
        ("google",),
    )
    assert row is not None

    raw_config = row["config"]
    stored = json.loads(raw_config)
    restored = sso_manager.deserialize_provider_config(raw_config)

    assert "client_secret" not in stored
    assert stored["client_secret_encrypted"]
    assert "legacy-secret" not in raw_config
    assert restored["client_secret"] == "legacy-secret"
    assert restored["client_id"] == "updated-client"
    assert restored["authorization_url"] == "https://example.com/oauth2/authorize"
