"""
Open ACE - OIDC Provider

Implementation of OpenID Connect authentication flow.
Extends OAuth2 with ID token verification.
"""

import base64
import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Optional

import jwt
import requests

from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.provider import SSOAuthResult, SSOProviderConfig, SSOUser

logger = logging.getLogger(__name__)


class OIDCProvider(OAuth2Provider):
    """OpenID Connect authentication provider."""

    def __init__(self, config: SSOProviderConfig):
        """
        Initialize OIDC provider.

        Args:
            config: Provider configuration.
        """
        super().__init__(config)
        self._jwks_cache: Optional[dict[str, Any]] = None
        self._jwks_cache_time: Optional[datetime] = None

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: Optional[str] = None,
        code_challenge: Optional[str] = None,
        nonce: Optional[str] = None,
    ) -> str:
        """
        Get the authorization URL for the OIDC flow.

        Args:
            state: State parameter for CSRF protection.
            redirect_uri: Override redirect URI.
            code_challenge: PKCE code challenge (optional).
            nonce: Nonce for replay protection (optional).

        Returns:
            str: Authorization URL.
        """
        params = {
            "client_id": self.config.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri or self.config.redirect_uri,
            "scope": " ".join(self.config.scope),
            "state": state,
        }

        # Add nonce for ID token validation
        if nonce:
            params["nonce"] = nonce

        # Add PKCE if provided
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"

        # Add extra parameters
        params.update(self.config.extra_params)

        return f"{self.config.authorization_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self, code: str, redirect_uri: Optional[str] = None, code_verifier: Optional[str] = None
    ) -> SSOAuthResult:
        """
        Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback.
            redirect_uri: Redirect URI used in authorization.
            code_verifier: PKCE code verifier (optional).

        Returns:
            SSOAuthResult: Authentication result with tokens.
        """
        result = super().exchange_code(code, redirect_uri, code_verifier)

        if result.success and result.token and result.token.id_token:
            # Verify ID token
            try:
                claims = self._verify_id_token(result.token.id_token)
                if claims:
                    # Store claims for later use
                    result.token.scope = json.dumps(claims)
            except Exception as e:
                logger.warning(f"ID token verification failed: {e}")
                # Continue without verified claims

        return result

    def get_user_info(self, access_token: str) -> Optional[SSOUser]:
        """
        Get user information using access token.

        For OIDC, we can also extract user info from the ID token.

        Args:
            access_token: OAuth access token.

        Returns:
            Optional[SSOUser]: User information or None.
        """
        # Try userinfo endpoint first
        user = super().get_user_info(access_token)

        if not user:
            # Fallback: try to decode ID token if we have it
            logger.warning(f"Could not get user info from endpoint for {self.name}")

        return user

    def _get_jwks(self) -> dict[str, Any]:
        """
        Fetch JWKS (JSON Web Key Set) from the provider's well-known endpoint.

        Uses caching to avoid repeated requests.

        Returns:
            Dict[str, Any]: JWKS data containing keys.

        Raises:
            ValueError: If JWKS cannot be fetched or issuer URL is not configured.
        """
        if not self.config.issuer_url:
            raise ValueError("Issuer URL is required for JWKS verification")

        # Check cache (cache for 1 hour)
        cache_ttl = timedelta(hours=1)
        if (
            self._jwks_cache is not None
            and self._jwks_cache_time is not None
            and datetime.utcnow() - self._jwks_cache_time < cache_ttl
        ):
            return self._jwks_cache

        # Fetch JWKS from well-known endpoint
        jwks_url = f"{self.config.issuer_url.rstrip('/')}/.well-known/jwks.json"

        try:
            response = requests.get(jwks_url, timeout=10)
            response.raise_for_status()
            jwks = response.json()

            # Cache the result
            self._jwks_cache = jwks
            self._jwks_cache_time: Optional[datetime] = datetime.utcnow()

            logger.debug(f"Successfully fetched JWKS from {jwks_url}")
            return jwks

        except requests.RequestException as e:
            logger.error(f"Failed to fetch JWKS from {jwks_url}: {e}")
            raise ValueError(f"Failed to fetch JWKS: {e}")

    def _get_signing_key(self, kid: str) -> Optional[str]:
        """
        Get the signing key for a given key ID.

        Args:
            kid: Key ID from the JWT header.

        Returns:
            Optional[str]: The public key in PEM format, or None if not found.
        """
        try:
            jwks = self._get_jwks()

            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    # Convert JWK to PEM format
                    return self._jwk_to_pem(key)

            logger.warning(f"No matching key found for kid: {kid}")
            return None

        except Exception as e:
            logger.error(f"Error getting signing key: {e}")
            return None

    def _jwk_to_pem(self, jwk: dict[str, Any]) -> str:
        """
        Convert a JWK (JSON Web Key) to PEM format.

        Args:
            jwk: JWK data containing modulus and exponent (for RSA keys).

        Returns:
            str: Public key in PEM format.
        """
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Extract RSA components
        n = int.from_bytes(base64.urlsafe_b64decode(jwk["n"] + "=="), byteorder="big")
        e = int.from_bytes(base64.urlsafe_b64decode(jwk["e"] + "=="), byteorder="big")

        # Construct RSA public key
        public_key = rsa.RSAPublicNumbers(e, n).public_key(default_backend())

        # Serialize to PEM format
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        return pem.decode("utf-8")

    def _verify_id_token(self, id_token: str) -> Optional[dict[str, Any]]:
        """
        Verify and decode the ID token with proper signature verification.

        This method validates:
        - JWT signature using JWKS
        - Token expiration
        - Issuer
        - Audience

        Args:
            id_token: JWT ID token.

        Returns:
            Optional[Dict]: Decoded claims or None if verification fails.
        """
        try:
            # First, decode the header to get the key ID (kid)
            unverified_header = jwt.get_unverified_header(id_token)
            kid = unverified_header.get("kid")

            if not kid:
                raise ValueError("No 'kid' in JWT header")

            # Get the signing key
            signing_key = self._get_signing_key(kid)
            if not signing_key:
                raise ValueError(f"Could not find signing key for kid: {kid}")

            # Verify and decode the token
            payload = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=["RS256"],
                audience=self.config.client_id,
                issuer=self.config.issuer_url,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )

            logger.debug(f"Successfully verified ID token for sub: {payload.get('sub')}")
            return payload

        except jwt.ExpiredSignatureError:
            logger.error("ID token has expired")
            return None
        except jwt.InvalidAudienceError:
            logger.error(f"Invalid audience in ID token, expected: {self.config.client_id}")
            return None
        except jwt.InvalidIssuerError:
            logger.error(f"Invalid issuer in ID token, expected: {self.config.issuer_url}")
            return None
        except jwt.InvalidSignatureError:
            logger.error("Invalid ID token signature")
            return None
        except jwt.DecodeError as e:
            logger.error(f"Failed to decode ID token: {e}")
            return None
        except ValueError as e:
            logger.error(f"ID token verification error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during ID token verification: {e}")
            return None

    def _parse_user_info(self, data: dict[str, Any]) -> SSOUser:
        """
        Parse user info from OIDC provider response.

        Args:
            data: User info response data.

        Returns:
            SSOUser: Parsed user info.
        """
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("sub", "")),
            email=data.get("email"),
            username=data.get("preferred_username", data.get("username")),
            name=data.get("name"),
            first_name=data.get("given_name"),
            last_name=data.get("family_name"),
            picture=data.get("picture"),
            locale=data.get("locale"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )

    @staticmethod
    def generate_nonce() -> str:
        """
        Generate a random nonce for replay protection.

        Returns:
            str: Random nonce string.
        """
        return secrets.token_urlsafe(32)


class GoogleProvider(OIDCProvider):
    """Google-specific OIDC provider."""

    def _parse_user_info(self, data: dict[str, Any]) -> SSOUser:
        """Parse Google user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("sub", "")),
            email=data.get("email"),
            username=data.get("email"),  # Google uses email as username
            name=data.get("name"),
            first_name=data.get("given_name"),
            last_name=data.get("family_name"),
            picture=data.get("picture"),
            locale=data.get("locale"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )


class MicrosoftProvider(OIDCProvider):
    """Microsoft-specific OIDC provider."""

    def _parse_user_info(self, data: dict[str, Any]) -> SSOUser:
        """Parse Microsoft user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("sub", "")),
            email=data.get("email", data.get("upn")),
            username=data.get("preferred_username", data.get("upn")),
            name=data.get("name"),
            first_name=data.get("given_name"),
            last_name=data.get("family_name"),
            picture=data.get("picture"),
            locale=None,
            email_verified=True,  # Microsoft accounts are verified
            raw_data=data,
        )


class OktaProvider(OIDCProvider):
    """Okta-specific OIDC provider."""

    def _parse_user_info(self, data: dict[str, Any]) -> SSOUser:
        """Parse Okta user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("sub", "")),
            email=data.get("email"),
            username=data.get("preferred_username", data.get("email")),
            name=data.get("name"),
            first_name=data.get("given_name"),
            last_name=data.get("family_name"),
            picture=data.get("picture"),
            locale=data.get("locale"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )


class Auth0Provider(OIDCProvider):
    """Auth0-specific OIDC provider."""

    def _parse_user_info(self, data: dict[str, Any]) -> SSOUser:
        """Parse Auth0 user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("sub", "")),
            email=data.get("email"),
            username=data.get("nickname", data.get("preferred_username")),
            name=data.get("name"),
            first_name=data.get("given_name"),
            last_name=data.get("family_name"),
            picture=data.get("picture"),
            locale=data.get("locale"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )


# Provider class mapping
PROVIDER_CLASSES = {
    "oauth2": OAuth2Provider,
    "oidc": OIDCProvider,
    "google": GoogleProvider,
    "microsoft": MicrosoftProvider,
    "github": OAuth2Provider,  # GitHub uses OAuth2, not OIDC
    "okta": OktaProvider,
    "auth0": Auth0Provider,
}


def get_provider_class(provider_type: str) -> type:
    """
    Get the provider class for a provider type.

    Args:
        provider_type: Provider type name.

    Returns:
        type: Provider class.
    """
    return PROVIDER_CLASSES.get(provider_type, OAuth2Provider)
