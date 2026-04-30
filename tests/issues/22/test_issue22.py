#!/usr/bin/env python3
"""
Test for Issue #22: OIDC ID Token Signature Verification

This test verifies that the OIDC provider properly validates ID token signatures
using JWKS (JSON Web Key Set).
"""

import base64
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest

from app.modules.sso.oidc import OIDCProvider
from app.modules.sso.provider import SSOProviderConfig


@pytest.fixture
def oidc_config():
    """Create a test OIDC configuration."""
    return SSOProviderConfig(
        name="test_oidc",
        provider_type="oidc",
        client_id="test_client_id",
        client_secret="test_client_secret",
        authorization_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
        userinfo_url="https://example.com/oauth/userinfo",
        issuer_url="https://example.com",
        scope=["openid", "profile", "email"],
    )


@pytest.fixture
def rsa_key_pair():
    """Generate RSA key pair for testing."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    # Get public key
    public_key = private_key.public_key()

    # Serialize keys
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # Get JWK format
    public_numbers = public_key.public_numbers()
    n = public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
    e = public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")

    jwk = {
        "kty": "RSA",
        "kid": "test_key_id",
        "use": "sig",
        "alg": "RS256",
        "n": base64.urlsafe_b64encode(n).rstrip(b"=").decode("utf-8"),
        "e": base64.urlsafe_b64encode(e).rstrip(b"=").decode("utf-8"),
    }

    return {
        "private_pem": private_pem,
        "public_pem": public_pem,
        "jwk": jwk,
        "kid": "test_key_id",
    }


class TestOIDCSignatureVerification:
    """Test OIDC ID token signature verification."""

    def test_verify_id_token_with_valid_signature(self, oidc_config, rsa_key_pair):
        """Test that a valid ID token with correct signature is verified."""
        provider = OIDCProvider(oidc_config)

        # Create a valid ID token with proper timestamp handling
        now = int(time.time())
        exp_time = now + 3600  # 1 hour from now

        payload = {
            "sub": "user123",
            "iss": oidc_config.issuer_url,
            "aud": oidc_config.client_id,
            "exp": exp_time,
            "iat": now,
            "email": "test@example.com",
        }

        # Sign the token with the test private key
        id_token = jwt.encode(
            payload,
            rsa_key_pair["private_pem"],
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},
        )

        # Mock JWKS response
        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        with patch.object(provider, "_get_jwks", return_value=jwks_response):
            result = provider._verify_id_token(id_token)

        assert result is not None
        assert result["sub"] == "user123"
        assert result["email"] == "test@example.com"

    def test_verify_id_token_rejects_invalid_signature(self, oidc_config, rsa_key_pair):
        """Test that an ID token with invalid signature is rejected."""
        provider = OIDCProvider(oidc_config)

        # Create a token signed with a different key
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Generate a different key
        different_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        different_pem = different_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        now = int(time.time())
        payload = {
            "sub": "attacker",
            "iss": oidc_config.issuer_url,
            "aud": oidc_config.client_id,
            "exp": now + 3600,
            "iat": now,
        }

        # Sign with the different key
        id_token = jwt.encode(
            payload,
            different_pem,
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},  # Use original kid
        )

        # Mock JWKS response with original key
        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        with patch.object(provider, "_get_jwks", return_value=jwks_response):
            result = provider._verify_id_token(id_token)

        # Should reject the token with invalid signature
        assert result is None

    def test_verify_id_token_rejects_expired_token(self, oidc_config, rsa_key_pair):
        """Test that expired ID tokens are rejected."""
        provider = OIDCProvider(oidc_config)

        # Create an expired token
        now = int(time.time())
        payload = {
            "sub": "user123",
            "iss": oidc_config.issuer_url,
            "aud": oidc_config.client_id,
            "exp": now - 3600,  # Expired 1 hour ago
            "iat": now - 7200,
        }

        id_token = jwt.encode(
            payload,
            rsa_key_pair["private_pem"],
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},
        )

        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        with patch.object(provider, "_get_jwks", return_value=jwks_response):
            result = provider._verify_id_token(id_token)

        assert result is None

    def test_verify_id_token_rejects_wrong_audience(self, oidc_config, rsa_key_pair):
        """Test that tokens with wrong audience are rejected."""
        provider = OIDCProvider(oidc_config)

        now = int(time.time())
        payload = {
            "sub": "user123",
            "iss": oidc_config.issuer_url,
            "aud": "different_client_id",  # Wrong audience
            "exp": now + 3600,
            "iat": now,
        }

        id_token = jwt.encode(
            payload,
            rsa_key_pair["private_pem"],
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},
        )

        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        with patch.object(provider, "_get_jwks", return_value=jwks_response):
            result = provider._verify_id_token(id_token)

        assert result is None

    def test_verify_id_token_rejects_wrong_issuer(self, oidc_config, rsa_key_pair):
        """Test that tokens with wrong issuer are rejected."""
        provider = OIDCProvider(oidc_config)

        now = int(time.time())
        payload = {
            "sub": "user123",
            "iss": "https://attacker.com",  # Wrong issuer
            "aud": oidc_config.client_id,
            "exp": now + 3600,
            "iat": now,
        }

        id_token = jwt.encode(
            payload,
            rsa_key_pair["private_pem"],
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},
        )

        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        with patch.object(provider, "_get_jwks", return_value=jwks_response):
            result = provider._verify_id_token(id_token)

        assert result is None

    def test_jwks_caching(self, oidc_config, rsa_key_pair):
        """Test that JWKS are cached properly."""
        provider = OIDCProvider(oidc_config)

        jwks_response = {"keys": [rsa_key_pair["jwk"]]}

        # Mock the requests.get call
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = jwks_response
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            # First call should fetch from network
            result1 = provider._get_jwks()
            assert result1 == jwks_response
            assert mock_get.call_count == 1

            # Second call should use cache
            result2 = provider._get_jwks()
            assert result2 == jwks_response
            assert mock_get.call_count == 1  # No additional call

    def test_jwk_to_pem_conversion(self, oidc_config, rsa_key_pair):
        """Test JWK to PEM conversion."""
        provider = OIDCProvider(oidc_config)

        # Convert JWK to PEM
        pem = provider._jwk_to_pem(rsa_key_pair["jwk"])

        # Verify the PEM is valid
        assert pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert pem.endswith("-----END PUBLIC KEY-----\n")

        # Verify the PEM can be used to verify a signature
        now = int(time.time())
        payload = {
            "sub": "user123",
            "iss": oidc_config.issuer_url,
            "aud": oidc_config.client_id,
            "exp": now + 3600,
            "iat": now,
        }

        id_token = jwt.encode(
            payload,
            rsa_key_pair["private_pem"],
            algorithm="RS256",
            headers={"kid": rsa_key_pair["kid"]},
        )

        # Verify using the converted PEM
        decoded = jwt.decode(
            id_token,
            key=pem,
            algorithms=["RS256"],
            audience=oidc_config.client_id,
            issuer=oidc_config.issuer_url,
        )

        assert decoded["sub"] == "user123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
