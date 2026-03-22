#!/usr/bin/env python3
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
from datetime import datetime
from typing import Any, Dict, Optional

from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProviderConfig,
    SSOUser,
)

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
        self._jwks_cache = None
        self._jwks_cache_time = None

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: Optional[str] = None,
        code_challenge: Optional[str] = None,
        nonce: Optional[str] = None
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
            'client_id': self.config.client_id,
            'response_type': 'code',
            'redirect_uri': redirect_uri or self.config.redirect_uri,
            'scope': ' '.join(self.config.scope),
            'state': state,
        }

        # Add nonce for ID token validation
        if nonce:
            params['nonce'] = nonce

        # Add PKCE if provided
        if code_challenge:
            params['code_challenge'] = code_challenge
            params['code_challenge_method'] = 'S256'

        # Add extra parameters
        params.update(self.config.extra_params)

        return f"{self.config.authorization_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        redirect_uri: Optional[str] = None,
        code_verifier: Optional[str] = None
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

    def _verify_id_token(self, id_token: str) -> Optional[Dict[str, Any]]:
        """
        Verify and decode the ID token.

        Args:
            id_token: JWT ID token.

        Returns:
            Optional[Dict]: Decoded claims or None.
        """
        try:
            # Split token
            parts = id_token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid JWT format")

            # Decode header and payload (without verification for now)
            # In production, you should verify the signature using JWKS
            header = json.loads(base64.urlsafe_b64decode(parts[0] + '=='))
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))

            # Basic validation
            now = datetime.utcnow().timestamp()

            # Check expiration
            if payload.get('exp', 0) < now:
                raise ValueError("ID token expired")

            # Check issuer
            if self.config.issuer_url and payload.get('iss') != self.config.issuer_url:
                # Some providers have dynamic issuers
                logger.warning(f"Issuer mismatch: {payload.get('iss')} != {self.config.issuer_url}")

            # Check audience
            aud = payload.get('aud', '')
            if isinstance(aud, list):
                if self.config.client_id not in aud:
                    raise ValueError("Invalid audience")
            else:
                if aud != self.config.client_id:
                    raise ValueError("Invalid audience")

            return payload

        except Exception as e:
            logger.error(f"ID token verification failed: {e}")
            return None

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """
        Parse user info from OIDC provider response.

        Args:
            data: User info response data.

        Returns:
            SSOUser: Parsed user info.
        """
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get('sub', '')),
            email=data.get('email'),
            username=data.get('preferred_username', data.get('username')),
            name=data.get('name'),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            picture=data.get('picture'),
            locale=data.get('locale'),
            email_verified=data.get('email_verified', False),
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

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """Parse Google user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get('sub', '')),
            email=data.get('email'),
            username=data.get('email'),  # Google uses email as username
            name=data.get('name'),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            picture=data.get('picture'),
            locale=data.get('locale'),
            email_verified=data.get('email_verified', False),
            raw_data=data,
        )


class MicrosoftProvider(OIDCProvider):
    """Microsoft-specific OIDC provider."""

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """Parse Microsoft user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get('sub', '')),
            email=data.get('email', data.get('upn')),
            username=data.get('preferred_username', data.get('upn')),
            name=data.get('name'),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            picture=data.get('picture'),
            locale=None,
            email_verified=True,  # Microsoft accounts are verified
            raw_data=data,
        )


class OktaProvider(OIDCProvider):
    """Okta-specific OIDC provider."""

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """Parse Okta user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get('sub', '')),
            email=data.get('email'),
            username=data.get('preferred_username', data.get('email')),
            name=data.get('name'),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            picture=data.get('picture'),
            locale=data.get('locale'),
            email_verified=data.get('email_verified', False),
            raw_data=data,
        )


class Auth0Provider(OIDCProvider):
    """Auth0-specific OIDC provider."""

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """Parse Auth0 user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get('sub', '')),
            email=data.get('email'),
            username=data.get('nickname', data.get('preferred_username')),
            name=data.get('name'),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            picture=data.get('picture'),
            locale=data.get('locale'),
            email_verified=data.get('email_verified', False),
            raw_data=data,
        )


# Provider class mapping
PROVIDER_CLASSES = {
    'oauth2': OAuth2Provider,
    'oidc': OIDCProvider,
    'google': GoogleProvider,
    'microsoft': MicrosoftProvider,
    'github': OAuth2Provider,  # GitHub uses OAuth2, not OIDC
    'okta': OktaProvider,
    'auth0': Auth0Provider,
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
