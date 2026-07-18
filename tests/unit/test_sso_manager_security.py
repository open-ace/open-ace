#!/usr/bin/env python3
"""
Security-focused tests for SSO secret storage and PKCE completion flow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.utils.smtp_crypto as smtp_crypto
from app.modules.sso.manager import SSOManager
from app.modules.sso.provider import SSOAuthResult
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file


@pytest.fixture
def sso_manager(tmp_path, monkeypatch):
    """Build an SSO manager against an isolated SQLite DB with a stable key."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-sso-encryption-key")
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{tmp_path / 'sso.db'}")
    manager = SSOManager(db=db)
    load_schema_from_file(db_url=manager.db.db_url, dialect="sqlite")

    try:
        yield manager
    finally:
        smtp_crypto._password_manager_instance = None


def test_register_provider_stores_only_encrypted_client_secret(sso_manager):
    """Provider config at rest must not retain plaintext client_secret."""
    assert (
        sso_manager.register_provider(
            name="google",
            provider_type="oauth2",
            client_id="client-id-123",
            client_secret="super-secret-value",
            authorization_url="https://example.com/oauth/authorize",
            token_url="https://example.com/oauth/token",
            redirect_uri="https://app.example.com/callback",
        )
        is True
    )

    row = sso_manager.db.fetch_one(
        "SELECT config FROM sso_providers WHERE name = ?",
        ("google",),
    )
    assert row is not None

    raw_config = row["config"]
    stored = json.loads(raw_config)

    assert "client_secret" not in stored
    assert stored["client_secret_encrypted"]
    assert "super-secret-value" not in raw_config

    restored = sso_manager.deserialize_provider_config(raw_config)
    assert restored["client_secret"] == "super-secret-value"


def test_deserialize_provider_config_supports_legacy_plaintext_secret(sso_manager):
    """Legacy configs remain readable before they are rewritten in encrypted form."""
    legacy_config = json.dumps(
        {
            "name": "google",
            "provider_type": "oauth2",
            "client_id": "legacy-client",
            "client_secret": "legacy-secret",
            "authorization_url": "https://example.com/oauth/authorize",
            "token_url": "https://example.com/oauth/token",
        }
    )

    restored = sso_manager.deserialize_provider_config(legacy_config)

    assert restored["client_secret"] == "legacy-secret"
    assert restored["client_id"] == "legacy-client"


def test_complete_authentication_passes_pkce_verifier_to_provider(sso_manager):
    """The stored PKCE verifier must be forwarded during code exchange."""
    provider = MagicMock()
    provider.authenticate.return_value = SSOAuthResult(success=True)

    with sso_manager._providers_lock:
        sso_manager._providers["google"] = provider

    sso_manager._store_auth_state("state-1", "verifier-1", "google", "nonce-1")

    result = sso_manager.complete_authentication(
        provider_name="google",
        code="auth-code-1",
        state="state-1",
        redirect_uri="https://app.example.com/api/sso/callback/google",
    )

    assert result.success is True
    provider.authenticate.assert_called_once_with(
        "auth-code-1",
        "https://app.example.com/api/sso/callback/google",
        "verifier-1",
    )
    assert sso_manager._get_auth_state("state-1") is None


def test_complete_authentication_rejects_missing_pkce_verifier(sso_manager):
    """A stored auth state without a code_verifier must hard-fail, not silently
    degrade PKCE by omitting the verifier from the token request."""
    provider = MagicMock()
    provider.authenticate.return_value = SSOAuthResult(success=True)

    with sso_manager._providers_lock:
        sso_manager._providers["google"] = provider

    # The fixture uses SQLite; force the SQLite placeholder path so the INSERT
    # below is not mis-adapted to '%s' by a leaked global PostgreSQL config.
    with patch("app.repositories.database.is_postgresql", return_value=False):
        # Simulate the upstream failure mode flagged by the review: state storage
        # produced a row but the verifier never landed in it.
        sso_manager.db.execute(
            "INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce) "
            "VALUES (?, ?, ?, ?)",
            ("state-no-verifier", "", "google", "nonce-x"),  # empty verifier
        )

        result = sso_manager.complete_authentication(
            provider_name="google",
            code="auth-code",
            state="state-no-verifier",
            redirect_uri="https://app.example.com/api/sso/callback/google",
        )

    assert result.success is False
    assert result.error == "invalid_state"
    provider.authenticate.assert_not_called()  # the load-bearing assertion
    with patch("app.repositories.database.is_postgresql", return_value=False):
        assert sso_manager._get_auth_state("state-no-verifier") is None


def test_complete_authentication_rejects_missing_pkce_verifier_key(sso_manager):
    """A stored auth state whose row lacks the code_verifier key entirely (e.g. a
    future schema regression or a partially-written state) must hard-fail."""
    provider = MagicMock()
    provider.authenticate.return_value = SSOAuthResult(success=True)

    with sso_manager._providers_lock:
        sso_manager._providers["google"] = provider

    # Inject a row-shaped auth state that carries no code_verifier at all, to
    # exercise the dict-miss path (auth_state.get("code_verifier") -> None).
    sso_manager._get_auth_state = lambda state: {  # type: ignore[assignment]
        "state": state,
        "provider_name": "google",
        "nonce": "nonce-y",
    }

    captured = {}

    def _fail_delete(state):
        captured["deleted"] = state

    sso_manager._delete_auth_state = _fail_delete  # type: ignore[assignment]

    result = sso_manager.complete_authentication(
        provider_name="google",
        code="auth-code",
        state="state-missing-key",
        redirect_uri="https://app.example.com/api/sso/callback/google",
    )

    assert result.success is False
    assert result.error == "invalid_state"
    provider.authenticate.assert_not_called()
    assert captured.get("deleted") == "state-missing-key"


def test_complete_authentication_rejects_state_bound_to_other_provider(sso_manager):
    """Auth state cannot be replayed across providers."""
    provider = MagicMock()

    with sso_manager._providers_lock:
        sso_manager._providers["github"] = provider

    sso_manager._store_auth_state("state-2", "verifier-2", "google", "nonce-2")

    result = sso_manager.complete_authentication(
        provider_name="github",
        code="auth-code-2",
        state="state-2",
        redirect_uri="https://app.example.com/api/sso/callback/github",
    )

    assert result.success is False
    assert result.error == "invalid_state"
    provider.authenticate.assert_not_called()
