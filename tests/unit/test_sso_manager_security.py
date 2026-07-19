#!/usr/bin/env python3
"""
Security-focused tests for SSO secret storage and PKCE completion flow.

Issue #1815: Tests for decrypt failure handling, auth_state TTL, and error message sanitization.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.utils.smtp_crypto as smtp_crypto
from app.modules.sso.exceptions import SSOConfigDecryptionError
from app.modules.sso.manager import AUTH_STATE_TTL_SECONDS, CLEANUP_BATCH_SIZE, SSOManager
from app.modules.sso.oauth2 import (
    ERROR_TOKEN_EXCHANGE_BLOCKED,
    ERROR_TOKEN_EXCHANGE_ERROR,
    ERROR_TOKEN_EXCHANGE_FAILED,
    OAuth2Provider,
)
from app.modules.sso.provider import SSOAuthResult, SSOProviderConfig
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.utils.outbound_url_guard import OutboundUrlBlockedError


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


# ============================================================================
# Issue #1815 Finding 1: Decrypt failure tests
# ============================================================================


def test_deserialize_provider_config_raises_on_decrypt_failure(sso_manager):
    """Issue #1815 Finding 1: Decrypt failure must raise exception, not silent fallback."""
    # Create a config with an invalid encrypted secret
    invalid_config = json.dumps(
        {
            "name": "google",
            "provider_type": "oauth2",
            "client_id": "client-id-123",
            "client_secret_encrypted": "invalid-encrypted-value-that-cannot-be-decrypted",
            "authorization_url": "https://example.com/oauth/authorize",
            "token_url": "https://example.com/oauth/token",
        }
    )

    # Should raise SSOConfigDecryptionError
    with pytest.raises(SSOConfigDecryptionError) as exc_info:
        sso_manager.deserialize_provider_config(invalid_config, provider_name="google")

    # Verify exception attributes
    assert exc_info.value.provider_name == "google"
    assert exc_info.value.original_error is not None


def test_deserialize_provider_config_success_with_valid_encrypted_secret(sso_manager):
    """Issue #1815 Finding 1: Valid encrypted secret should decrypt correctly."""
    # First register a provider to encrypt the secret
    assert sso_manager.register_provider(
        name="test-provider",
        provider_type="oauth2",
        client_id="client-id-123",
        client_secret="test-secret-value",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
    )

    # Now retrieve and decrypt
    row = sso_manager.db.fetch_one(
        "SELECT config FROM sso_providers WHERE name = ?",
        ("test-provider",),
    )

    restored = sso_manager.deserialize_provider_config(row["config"], provider_name="test-provider")
    assert restored["client_secret"] == "test-secret-value"


def test_get_provider_returns_none_on_decrypt_failure(sso_manager, caplog):
    """Issue #1815 Finding 1: get_provider should return None and log error on decrypt failure."""
    # Insert a provider with invalid encrypted secret directly into DB
    invalid_config = json.dumps(
        {
            "name": "broken-provider",
            "provider_type": "oauth2",
            "client_id": "client-id",
            "client_secret_encrypted": "invalid-encrypted-value",
            "authorization_url": "https://example.com/oauth/authorize",
            "token_url": "https://example.com/oauth/token",
        }
    )
    sso_manager.db.execute(
        "INSERT INTO sso_providers (name, provider_type, config) VALUES (?, ?, ?)",
        ("broken-provider", "oauth2", invalid_config),
    )

    # get_provider should return None (not cache invalid provider)
    result = sso_manager.get_provider("broken-provider")
    assert result is None

    # Verify provider was not cached
    with sso_manager._providers_lock:
        assert "broken-provider" not in sso_manager._providers

    # Verify error was logged at ERROR level
    assert "decryption" in caplog.text.lower() or "decrypt" in caplog.text.lower()


# ============================================================================
# Issue #1815 Finding 2: Auth state TTL tests
# ============================================================================


def test_store_auth_state_includes_expires_at(sso_manager):
    """Issue #1815 Finding 2: Stored auth_state should have expires_at."""
    sso_manager._store_auth_state("state-ttl-1", "verifier-1", "google", "nonce-1")

    # Query directly to verify expires_at was set
    row = sso_manager.db.fetch_one(
        "SELECT * FROM sso_auth_states WHERE state = ?",
        ("state-ttl-1",),
    )

    assert row is not None
    assert row["expires_at"] is not None

    # Verify expires_at is approximately 10 minutes from now
    expires_at = row["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00").replace("+00:00", ""))

    expected_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=AUTH_STATE_TTL_SECONDS
    )
    # Allow 1 minute tolerance for test execution time
    assert abs((expires_at - expected_expiry).total_seconds()) < 60


def test_get_auth_state_returns_none_for_expired_state(sso_manager):
    """Issue #1815 Finding 2: Expired auth_state should return None."""
    # Insert an already-expired auth state directly
    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    sso_manager.db.execute(
        "INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("state-expired", "verifier", "google", "nonce", past_time),
    )

    # Should return None (expired)
    result = sso_manager._get_auth_state("state-expired")
    assert result is None


def test_get_auth_state_returns_valid_for_non_expired_state(sso_manager):
    """Issue #1815 Finding 2: Non-expired auth_state should return valid data."""
    # Store a fresh auth state (should have future expires_at)
    sso_manager._store_auth_state("state-valid", "verifier", "google", "nonce")

    result = sso_manager._get_auth_state("state-valid")
    assert result is not None
    assert result["provider_name"] == "google"
    assert result["code_verifier"] == "verifier"


def test_cleanup_expired_auth_states_deletes_expired_rows(sso_manager):
    """Issue #1815 Finding 2: cleanup_expired_auth_states should delete expired rows."""
    # Insert expired auth state
    past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    sso_manager.db.execute(
        "INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("expired-1", "verifier-1", "google", "nonce-1", past_time),
    )
    sso_manager.db.execute(
        "INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("expired-2", "verifier-2", "google", "nonce-2", past_time),
    )

    # Insert non-expired auth state
    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    sso_manager.db.execute(
        "INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("valid-1", "verifier-3", "google", "nonce-3", future_time),
    )

    # Run cleanup
    deleted = sso_manager.cleanup_expired_auth_states()

    # Verify expired rows were deleted
    assert deleted >= 2

    # Verify valid row still exists
    row = sso_manager.db.fetch_one("SELECT * FROM sso_auth_states WHERE state = ?", ("valid-1",))
    assert row is not None

    # Verify expired rows are gone
    row = sso_manager.db.fetch_one("SELECT * FROM sso_auth_states WHERE state = ?", ("expired-1",))
    assert row is None


# ============================================================================
# Issue #1815 Finding 3: Error message sanitization tests
# ============================================================================


def test_oauth2_exchange_code_sanitizes_non_json_4xx_response():
    """Issue #1815 Finding 3: Non-JSON 4xx response should use sanitized error message."""
    config = SSOProviderConfig(
        name="test",
        provider_type="oauth2",
        client_id="client-id",
        client_secret="client-secret",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
    )
    provider = OAuth2Provider(config)

    # Mock response for 4xx non-JSON
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Internal error: code=abc123, client_id=secret123"
    mock_response.json.side_effect = ValueError("Not JSON")

    with patch("app.modules.sso.oauth2.safe_request", return_value=mock_response):
        result = provider.exchange_code("code123")

    assert result.success is False
    assert result.error == "token_exchange_failed"
    # Should use sanitized message, not raw response.text
    assert result.error_description == ERROR_TOKEN_EXCHANGE_FAILED
    assert "abc123" not in result.error_description
    assert "secret123" not in result.error_description


def test_oauth2_exchange_code_sanitizes_outbound_url_blocked():
    """Issue #1815 Finding 3: OutboundUrlBlockedError should use sanitized message."""
    config = SSOProviderConfig(
        name="test",
        provider_type="oauth2",
        client_id="client-id",
        client_secret="client-secret",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
    )
    provider = OAuth2Provider(config)

    # Mock safe_request to raise OutboundUrlBlockedError
    blocked_error = OutboundUrlBlockedError("Blocked URL: https://internal.example.com/admin")
    with patch("app.modules.sso.oauth2.safe_request", side_effect=blocked_error):
        result = provider.exchange_code("code123")

    assert result.success is False
    assert result.error == "token_exchange_blocked"
    # Should use sanitized message, not the exception string
    assert result.error_description == ERROR_TOKEN_EXCHANGE_BLOCKED
    assert "internal.example.com" not in result.error_description


def test_oauth2_exchange_code_sanitizes_generic_exception():
    """Issue #1815 Finding 3: Generic exception should use sanitized message."""
    config = SSOProviderConfig(
        name="test",
        provider_type="oauth2",
        client_id="client-id",
        client_secret="client-secret",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
    )
    provider = OAuth2Provider(config)

    # Mock safe_request to raise generic exception
    with patch(
        "app.modules.sso.oauth2.safe_request",
        side_effect=RuntimeError("Internal error: /var/secrets/key.pem"),
    ):
        result = provider.exchange_code("code123")

    assert result.success is False
    assert result.error == "token_exchange_error"
    # Should use sanitized message
    assert result.error_description == ERROR_TOKEN_EXCHANGE_ERROR
    assert "/var/secrets" not in result.error_description


def test_oauth2_exchange_code_transparently_passes_idp_error():
    """Issue #1815 Finding 3: IdP-returned JSON error should be passed through."""
    config = SSOProviderConfig(
        name="test",
        provider_type="oauth2",
        client_id="client-id",
        client_secret="client-secret",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
    )
    provider = OAuth2Provider(config)

    # Mock response for 4xx with JSON body (IdP standard error)
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Authorization code expired",
    }

    with patch("app.modules.sso.oauth2.safe_request", return_value=mock_response):
        result = provider.exchange_code("code123")

    assert result.success is False
    assert result.error == "invalid_grant"
    # IdP error_description should be passed through
    assert result.error_description == "Authorization code expired"
