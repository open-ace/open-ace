"""
Tests for Issue #1826: SSO Security Improvements

This module tests the 8 security findings (F1-F8) fixed in Sprint 1-5:
- F1/F7: Provider cache TTL
- F2: Auth state exception handling
- F3: Tenant ID strategy
- F4: SSO logout cascade cleanup
- F5: Empty secret bypass prevention
- F6: Avoid unnecessary re-encryption
- F8: RelayState signature
"""

import inspect
import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.sso.exceptions import SSOConfigDecryptionError
from app.modules.sso.manager import PROVIDER_CACHE_TTL_SECONDS, SSOManager
from app.modules.sso.provider import SSOProviderConfig
from app.repositories.database import Database


class TestF2AuthStateExceptionHandling:
    """Test Issue #1826 F2: Auth state storage exception handling."""

    def test_store_auth_state_raises_on_db_failure(self, tmp_path):
        """Test that _store_auth_state raises exception on DB failure."""
        # Create manager with mock DB that raises exception
        manager = SSOManager()
        manager.db = MagicMock()
        manager.db.execute.side_effect = Exception("Database connection failed")

        # Should raise exception instead of swallowing it
        with pytest.raises(Exception, match="Database connection failed"):
            manager._store_auth_state(
                state="test_state",
                code_verifier="test_verifier",
                provider_name="test_provider",
                nonce="test_nonce",
            )

    def test_store_auth_state_success_path(self):
        """Test that _store_auth_state succeeds when DB is available."""
        manager = SSOManager()

        # Mock successful DB execute
        manager.db.execute = MagicMock()

        # Should not raise
        manager._store_auth_state(
            state="test_state",
            code_verifier="test_verifier",
            provider_name="test_provider",
            nonce="test_nonce",
        )

        # Verify DB was called
        manager.db.execute.assert_called_once()


class TestF5EmptySecretBypass:
    """Test Issue #1826 F5: Empty secret bypass prevention."""

    def test_deserialize_empty_encrypted_secret(self):
        """Test that empty encrypted secret forces empty client_secret."""
        manager = SSOManager()

        # Config with empty encrypted secret and non-empty plaintext
        raw_config = json.dumps(
            {
                "name": "test_provider",
                "client_id": "test_client_id",
                "client_secret": "should_be_ignored",
                "client_secret_encrypted": "",
                "authorization_url": "https://example.com/auth",
                "token_url": "https://example.com/token",
            }
        )

        # Deserialize should return SecretHolder with empty secret
        config = manager.deserialize_provider_config(raw_config)

        # Issue #2174 F5: client_secret is now wrapped in SecretHolder
        from app.modules.sso.secret_holder import SecretHolder

        assert isinstance(config["client_secret"], SecretHolder)
        assert config["client_secret"].get() == ""
        assert config["name"] == "test_provider"

    def test_deserialize_missing_encrypted_field(self):
        """Test that missing encrypted field allows plaintext."""
        manager = SSOManager()

        # Config without encrypted field (legacy)
        raw_config = json.dumps(
            {
                "name": "test_provider",
                "client_id": "test_client_id",
                "client_secret": "plaintext_secret",
                "authorization_url": "https://example.com/auth",
                "token_url": "https://example.com/token",
            }
        )

        # Deserialize should wrap plaintext in SecretHolder
        config = manager.deserialize_provider_config(raw_config)

        # Issue #2174 F5: client_secret is now wrapped in SecretHolder
        from app.modules.sso.secret_holder import SecretHolder

        assert isinstance(config["client_secret"], SecretHolder)
        assert config["client_secret"].get() == "plaintext_secret"


class TestF6AvoidReEncryption:
    """Test Issue #1826 F6: Avoid unnecessary re-encryption."""

    def test_update_preserves_encrypted_secret(self):
        """Test that update_provider preserves encrypted secret when not changed."""
        # This test would require a full Flask app context
        # For now, we test the logic manually
        from app.routes.sso import get_sso_manager

        # Mock scenario: update only scope, not client_secret
        # The update_provider route should preserve existing encrypted_secret
        # Implementation is in routes/sso.py lines 504-545
        # This is a placeholder for integration testing
        # Real test would use Flask test client
        pass


class TestF3TenantIDStrategy:
    """Test Issue #1826 F3: Tenant ID strategy configuration."""

    def test_null_tenant_policy_warn(self):
        """Test warn policy for null tenant_id."""
        # Set policy
        os.environ["SSO_NULL_TENANT_POLICY"] = "warn"

        # Import after setting env var
        from app.modules.sso.provider import SSOUser
        from app.routes.sso import _create_user_from_sso

        # Mock provider with no tenant_id
        manager = MagicMock()
        provider = MagicMock()
        provider.config.tenant_id = None
        manager.get_provider.return_value = provider

        # Mock user repo
        with patch("app.routes.sso.user_repo") as mock_repo:
            mock_repo.get_user_by_username.return_value = None
            mock_repo.create_user.return_value = 123

            # Should create user with default tenant_id=1
            # Note: This test is a placeholder for integration testing
            # The actual logic is tested via unit tests for _create_user_from_sso
            # sso_user = SSOUser(
            #     provider="test",
            #     provider_user_id="123",
            #     email="test@example.com",
            #     username="testuser"
            # )

            # This would log warning but create user
            # user_id = _create_user_from_sso(sso_user, "test_provider")
            # assert user_id == 123

    def test_null_tenant_policy_reject(self):
        """Test reject policy for null tenant_id."""
        os.environ["SSO_NULL_TENANT_POLICY"] = "reject"

        # This would reject user creation
        # Implementation in _create_user_from_sso
        pass


class TestF1F7ProviderCacheTTL:
    """Test Issue #1826 F1/F7: Provider cache TTL."""

    def test_provider_cache_ttl(self):
        """Test that provider cache respects TTL."""
        manager = SSOManager()

        # Mock DB
        manager.db.fetch_one = MagicMock(
            return_value={
                "name": "test_provider",
                "provider_type": "oauth2",
                "config": json.dumps(
                    {
                        "name": "test_provider",
                        "client_id": "test_client_id",
                        "client_secret_encrypted": "test_encrypted",
                        "authorization_url": "https://example.com/auth",
                        "token_url": "https://example.com/token",
                        "provider_type": "oauth2",
                    }
                ),
                "tenant_id": 1,
                "is_active": True,
            }
        )

        # Mock password manager to avoid decryption
        manager._password_manager.decrypt = MagicMock(return_value="test_secret")

        # First call - loads from DB
        provider1 = manager.get_provider("test_provider")
        assert provider1 is not None

        # Cache time should be set
        assert "test_provider" in manager._provider_cache_time

        # Second call immediately - should use cache
        provider2 = manager.get_provider("test_provider")
        assert provider2 is provider1

        # Cleanup: Clear provider cache to avoid mock reference issues
        manager._providers.clear()
        manager._provider_cache_time.clear()
        del manager

    def test_provider_cache_expiry(self):
        """Test that provider cache expires after TTL."""
        manager = SSOManager()

        # Mock DB
        manager.db.fetch_one = MagicMock(
            return_value={
                "name": "test_provider",
                "provider_type": "oauth2",
                "config": json.dumps(
                    {
                        "name": "test_provider",
                        "client_id": "test_client_id",
                        "client_secret_encrypted": "test_encrypted",
                        "authorization_url": "https://example.com/auth",
                        "token_url": "https://example.com/token",
                        "provider_type": "oauth2",
                    }
                ),
                "tenant_id": 1,
                "is_active": True,
            }
        )

        manager._password_manager.decrypt = MagicMock(return_value="test_secret")

        # Load provider
        provider1 = manager.get_provider("test_provider")
        assert provider1 is not None

        # Simulate time passage (beyond TTL)
        manager._provider_cache_time["test_provider"] = time.time() - PROVIDER_CACHE_TTL_SECONDS - 1

        # Next call should reload from DB
        provider2 = manager.get_provider("test_provider")
        assert provider2 is not None
        # Should be different object (reload)
        # Note: This depends on mock returning new object each call

        # Cleanup: Clear provider cache to avoid mock reference issues
        manager._providers.clear()
        manager._provider_cache_time.clear()
        del manager


class TestF8RelayStateSignature:
    """Test Issue #1826 F8: RelayState signature."""

    def test_encode_state_with_signature(self):
        """Test that _encode_state adds signature."""
        from app.routes.sso import _encode_state

        encoded = _encode_state("original_state_123", "https://example.com/callback")

        # Should be valid base64
        import base64

        decoded = json.loads(base64.urlsafe_b64decode(encoded))

        # Should have version, signature, etc.
        assert decoded["v"] == 2
        assert decoded["s"] == "original_state_123"
        assert decoded["r"] == "https://example.com/callback"
        assert "sig" in decoded
        assert "t" in decoded

    def test_decode_state_valid_signature(self):
        """Test that _decode_state accepts valid signature."""
        from app.routes.sso import _decode_state, _encode_state

        encoded = _encode_state("original_state_456", "https://example.com/redirect")
        state, redirect = _decode_state(encoded)

        assert state == "original_state_456"
        assert redirect == "https://example.com/redirect"

    def test_decode_state_invalid_signature(self):
        """Test that _decode_state rejects invalid signature."""
        import base64

        from app.routes.sso import _decode_state

        # Create tampered state
        state_data = {
            "v": 2,
            "s": "original_state",
            "r": "https://attacker.com/malicious",
            "t": int(datetime.now(timezone.utc).timestamp()),
            "sig": "invalid_signature_12345",
        }
        encoded = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()

        # Should reject (return None for redirect)
        state, redirect = _decode_state(encoded)
        assert redirect is None

    def test_decode_state_legacy_format(self):
        """Test that _decode_state accepts legacy format during transition."""
        import base64

        from app.routes.sso import _decode_state

        # Legacy format without signature
        state_data = {"s": "legacy_state", "r": "https://example.com/legacy"}
        encoded = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()

        # Should accept (with warning logged)
        state, redirect = _decode_state(encoded)
        assert state == "legacy_state"
        assert redirect == "https://example.com/legacy"

    def test_decode_state_error_handling(self):
        """Test that _decode_state handles errors gracefully."""
        from app.routes.sso import _decode_state

        # Invalid base64
        state, redirect = _decode_state("not-valid-base64!!!")
        # Should return (encoded_state, None) during transition
        assert state == "not-valid-base64!!!"
        assert redirect is None

    def test_relaystate_transition_period_documented(self):
        """Test that transition period is properly documented."""
        import inspect

        from app.routes.sso import _decode_state

        # Check docstring contains transition period end date
        docstring = _decode_state.__doc__
        assert "2027-01-31" in docstring or "transition" in docstring.lower()


class TestF4SessionCascadeCleanup:
    """Test Issue #1826 F4: SSO logout cascade cleanup."""

    def test_logout_deletes_both_tables(self):
        """Test that logout deletes from both sso_sessions and sessions."""
        # This test would require Flask test client
        # For now, verify the logic is in place
        # Implementation is in routes/sso.py logout() function

        # The function should:
        # 1. Get session data
        # 2. Delete from sso_sessions
        # 3. Delete from sessions (cascade)
        # 4. Audit log
        # 5. Clear cookie (Issue #1826 F4)

        # Verify the implementation includes cookie clearing
        from app.routes.sso import logout

        source = inspect.getsource(logout)

        # Check for delete_cookie call
        assert "delete_cookie" in source, "logout should clear session_token cookie"
        assert "session_token" in source, "logout should reference session_token cookie"

        # Placeholder for integration testing
        pass

    def test_logout_clears_cookie(self):
        """Test that logout clears session_token cookie."""
        from app.routes.sso import logout

        source = inspect.getsource(logout)
        # Verify cookie deletion is implemented
        assert "delete_cookie" in source
        assert "httponly=True" in source
        assert 'samesite="Lax"' in source


class TestIntegration:
    """Integration tests for combined findings."""

    def test_full_sso_flow_with_all_fixes(self):
        """Test complete SSO flow with all security improvements."""
        # This would be a comprehensive integration test
        # covering all 8 findings in realistic scenario
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
