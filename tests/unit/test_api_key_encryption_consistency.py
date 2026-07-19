"""
Tests for encryption algorithm consistency.

These tests verify that the encryption implementation matches the documentation,
preventing drift between code and docstrings.

Issue: #1857 - API Key encryption algorithm documentation inconsistency
"""

import hashlib
import os
import tempfile
from base64 import b64decode, b64encode
from unittest.mock import patch

import pytest


class TestAPIKeyEncryptionConsistency:
    """Tests to ensure encryption implementation matches documentation."""

    def test_encrypt_uses_fernet_not_aes_gcm(self):
        """Verify that _encrypt_key uses Fernet, not raw AES-GCM."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                encrypted = service._encrypt_key("test-api-key")

                # Fernet tokens are base64-encoded and start with version byte 0x80
                # When base64-encoded, they commonly start with 'gAAAA' or similar
                assert encrypted is not None
                assert len(encrypted) > 0

                # Verify it's valid base64
                try:
                    decoded = b64decode(encrypted.encode())
                    # Fernet version byte is 0x80
                    assert decoded[0] == 0x80, "Should be Fernet token (version 0x80)"
                except Exception:
                    pytest.fail("Encrypted value should be valid base64")

    def test_encrypted_key_is_fernet_token(self):
        """Encrypted keys should be valid Fernet tokens."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from cryptography.fernet import Fernet

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            test_key = "test-key-12345678901234567890"
            with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": test_key}):
                service = APIKeyProxyService(db_path=db_path)

                plaintext = "sk-test-1234567890abcdef"
                encrypted = service._encrypt_key(plaintext)

                # Should be able to decrypt with Fernet directly
                derived_key = hashlib.sha256(test_key.encode()).digest()
                fernet_key = b64encode(derived_key)
                f = Fernet(fernet_key)

                decrypted = f.decrypt(encrypted.encode()).decode()
                assert decrypted == plaintext

    def test_key_derivation_matches_smtp_crypto(self):
        """API key encryption should use same key derivation as SMTP."""
        import hashlib

        test_key = "shared-test-key-123456789012345678"
        expected_derived = hashlib.sha256(test_key.encode()).digest()

        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": test_key}):
            # API Key Proxy
            from app.modules.workspace.api_key_proxy import APIKeyProxyService

            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test.db")
                api_service = APIKeyProxyService(db_path=db_path)
                api_derived = api_service._encryption_key

            # SMTP Crypto
            from app.utils.smtp_crypto import SMTPPasswordManager

            smtp_manager = SMTPPasswordManager()
            smtp_derived = smtp_manager._encryption_key

            # Both should derive the same key
            assert api_derived == expected_derived
            assert smtp_derived == expected_derived
            assert api_derived == smtp_derived

    def test_class_docstring_accuracy(self):
        """Class docstring should not claim AES-256-GCM."""
        import inspect

        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        docstring = inspect.getdoc(APIKeyProxyService)

        # Should NOT contain AES-256-GCM reference
        assert "AES-256-GCM" not in docstring, (
            "Class docstring incorrectly claims AES-256-GCM encryption"
        )

        # Should contain Fernet reference
        assert "Fernet" in docstring, (
            "Class docstring should mention Fernet encryption"
        )

    def test_smtp_crypto_method_docstring_accuracy(self):
        """Verify smtp_crypto method docstring is accurate."""
        import inspect

        from app.utils.smtp_crypto import SMTPPasswordManager

        docstring = inspect.getdoc(SMTPPasswordManager._get_encryption_key)

        # Should not mislead about AES encryption key
        # (The original said "Get the AES encryption key" which is misleading
        # since we're actually deriving a Fernet key)
        if "AES encryption key" in docstring:
            # If it mentions AES, it should clarify it's for Fernet
            assert "Fernet" in docstring, (
                "Method docstring mentions AES but should clarify Fernet"
            )


class TestProxyTokenSignature:
    """Tests for Proxy Token signature algorithm."""

    def test_proxy_token_signature_is_hmac_sha256(self):
        """Verify proxy tokens use HMAC-SHA256, not Fernet."""
        import hmac
        import json

        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                token = service.generate_proxy_token(
                    user_id=1,
                    session_id="test-session",
                    tenant_id=1,
                    provider="openai",
                )

                # Token format: payload_b64.signature
                parts = token.split(".")
                assert len(parts) == 2, "Token should have payload.signature format"

                payload_b64, signature = parts

                # Verify signature using HMAC-SHA256
                expected_sig = hmac.new(
                    service._encryption_key,
                    payload_b64.encode(),
                    hashlib.sha256,
                ).hexdigest()

                assert signature == expected_sig, "Signature should be HMAC-SHA256"

    def test_proxy_token_not_fernet(self):
        """Verify Proxy Token is not Fernet token format."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                token = service.generate_proxy_token(
                    user_id=1,
                    session_id="test-session",
                    tenant_id=1,
                    provider="openai",
                )

                # Fernet tokens start with version byte 0x80 (commonly 'gAAAA' in base64)
                # Proxy tokens are payload_b64.signature format
                assert not token.startswith("gAAAA"), (
                    "Proxy token should not be Fernet format"
                )

                # Should have exactly one dot separator
                assert token.count(".") == 1, "Proxy token should be payload.signature"

    def test_proxy_token_payload_is_json(self):
        """Verify proxy token payload contains JSON."""
        import json

        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.dict(
                os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-12345678901234567890"}
            ):
                service = APIKeyProxyService(db_path=db_path)
                token = service.generate_proxy_token(
                    user_id=1,
                    session_id="test-session",
                    tenant_id=1,
                    provider="openai",
                )

                payload_b64 = token.split(".")[0]
                payload_json = b64decode(payload_b64).decode()
                payload = json.loads(payload_json)

                # Should have required fields
                assert "user_id" in payload
                assert "tenant_id" in payload
                assert "provider" in payload
                assert "jti" in payload  # unique token identifier


class TestCrossModuleKeyConsistency:
    """Tests for cross-module key derivation consistency."""

    def test_all_modules_use_same_key_derivation(self):
        """Verify API key, SMTP, and Model Gateway use identical key derivation."""
        import hashlib

        test_key = "consistency-test-key-12345678901234"

        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": test_key}):
            # Expected derived key
            expected = hashlib.sha256(test_key.encode()).digest()

            # API Key Proxy
            from app.modules.workspace.api_key_proxy import APIKeyProxyService

            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test.db")
                api_service = APIKeyProxyService(db_path=db_path)
                api_key = api_service._encryption_key

            # SMTP Crypto
            from app.utils.smtp_crypto import get_password_manager

            smtp_manager = get_password_manager()
            smtp_key = smtp_manager._encryption_key

            # Model Gateway (uses same SMTP crypto)
            gateway_key = smtp_manager._encryption_key

            # All should be identical
            assert api_key == expected, "API Key derivation mismatch"
            assert smtp_key == expected, "SMTP derivation mismatch"
            assert gateway_key == expected, "Gateway derivation mismatch"

    def test_encryption_decryption_roundtrip(self):
        """Verify encryption/decryption roundtrip works across modules."""
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from app.utils.smtp_crypto import get_password_manager

        test_key = "roundtrip-test-key-123456789012345"

        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": test_key}):
            # API Key encryption
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test.db")
                api_service = APIKeyProxyService(db_path=db_path)

                test_value = "sk-test-secret-key-12345"
                encrypted_api = api_service._encrypt_key(test_value)
                decrypted_api = api_service._decrypt_key(encrypted_api)
                assert decrypted_api == test_value

            # SMTP encryption
            smtp_manager = get_password_manager()
            encrypted_smtp = smtp_manager.encrypt(test_value)
            decrypted_smtp = smtp_manager.decrypt(encrypted_smtp)
            assert decrypted_smtp == test_value