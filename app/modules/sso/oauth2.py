#!/usr/bin/env python3
"""
Open ACE - OAuth2 Provider

Implementation of OAuth2 authentication flow.
"""

import base64
import hashlib
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProvider,
    SSOProviderConfig,
    SSOToken,
    SSOUser,
)

logger = logging.getLogger(__name__)


class OAuth2Provider(SSOProvider):
    """OAuth2 authentication provider."""

    def __init__(self, config: SSOProviderConfig):
        """
        Initialize OAuth2 provider.

        Args:
            config: Provider configuration.
        """
        super().__init__(config)
        self._http_session = None

    def get_authorization_url(
        self, state: str, redirect_uri: Optional[str] = None, code_challenge: Optional[str] = None
    ) -> str:
        """
        Get the authorization URL for the OAuth flow.

        Args:
            state: State parameter for CSRF protection.
            redirect_uri: Override redirect URI.
            code_challenge: PKCE code challenge (optional).

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
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri or self.config.redirect_uri,
        }

        if code_verifier:
            data["code_verifier"] = code_verifier

        try:
            # Use synchronous HTTP request
            import json
            import urllib.request

            req = urllib.request.Request(
                self.config.token_url,
                data=urllib.parse.urlencode(data).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                token_data = json.loads(response.read().decode("utf-8"))

            token = self._parse_token_response(token_data)

            return SSOAuthResult(
                success=True,
                token=token,
            )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            logger.error(f"OAuth2 token exchange failed: {e.code} - {error_body}")

            try:
                error_data = json.loads(error_body)
                return SSOAuthResult(
                    success=False,
                    error=error_data.get("error", "token_exchange_failed"),
                    error_description=error_data.get("error_description"),
                )
            except json.JSONDecodeError:
                return SSOAuthResult(
                    success=False,
                    error="token_exchange_failed",
                    error_description=error_body,
                )

        except Exception as e:
            logger.error(f"OAuth2 token exchange error: {e}")
            return SSOAuthResult(
                success=False,
                error="token_exchange_error",
                error_description=str(e),
            )

    def get_user_info(self, access_token: str) -> Optional[SSOUser]:
        """
        Get user information using access token.

        Args:
            access_token: OAuth access token.

        Returns:
            Optional[SSOUser]: User information or None.
        """
        if not self.config.userinfo_url:
            logger.warning(f"No userinfo URL configured for provider {self.name}")
            return None

        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                self.config.userinfo_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                user_data = json.loads(response.read().decode("utf-8"))

            return self._parse_user_info(user_data)

        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return None

    def refresh_token(self, refresh_token: str) -> Optional[SSOToken]:
        """
        Refresh the access token.

        Args:
            refresh_token: OAuth refresh token.

        Returns:
            Optional[SSOToken]: New token or None.
        """
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                self.config.token_url,
                data=urllib.parse.urlencode(data).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                token_data = json.loads(response.read().decode("utf-8"))

            return self._parse_token_response(token_data)

        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            return None

    def _parse_token_response(self, data: Dict[str, Any]) -> SSOToken:
        """
        Parse token response from provider.

        Args:
            data: Token response data.

        Returns:
            SSOToken: Parsed token.
        """
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        return SSOToken(
            access_token=data.get("access_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_in=expires_in,
            refresh_token=data.get("refresh_token"),
            id_token=data.get("id_token"),
            scope=data.get("scope"),
            expires_at=expires_at,
        )

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """
        Parse user info from provider response.

        This method should be overridden by subclasses for provider-specific parsing.

        Args:
            data: User info response data.

        Returns:
            SSOUser: Parsed user info.
        """
        # Generic parsing - works for most OAuth2 providers
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("id", data.get("sub", ""))),
            email=data.get("email"),
            username=data.get("login", data.get("username", data.get("preferred_username"))),
            name=data.get("name"),
            first_name=data.get("given_name", data.get("first_name")),
            last_name=data.get("family_name", data.get("last_name")),
            picture=data.get("picture", data.get("avatar_url")),
            locale=data.get("locale"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )

    @staticmethod
    def generate_pkce() -> tuple:
        """
        Generate PKCE code verifier and challenge.

        Returns:
            tuple: (code_verifier, code_challenge)
        """
        # Generate code verifier (43-128 characters)
        code_verifier = secrets.token_urlsafe(96)[:128]

        # Generate code challenge
        challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")

        return code_verifier, code_challenge

    @staticmethod
    def generate_state() -> str:
        """
        Generate a random state string for CSRF protection.

        Returns:
            str: Random state string.
        """
        return secrets.token_urlsafe(32)


class GitHubProvider(OAuth2Provider):
    """GitHub-specific OAuth2 provider."""

    def _parse_user_info(self, data: Dict[str, Any]) -> SSOUser:
        """Parse GitHub user info."""
        return SSOUser(
            provider=self.name,
            provider_user_id=str(data.get("id", "")),
            email=data.get("email"),
            username=data.get("login"),
            name=data.get("name"),
            picture=data.get("avatar_url"),
            locale=None,
            email_verified=False,  # GitHub doesn't provide this
            raw_data=data,
        )

    def get_user_info(self, access_token: str) -> Optional[SSOUser]:
        """Get GitHub user info including emails."""
        user = super().get_user_info(access_token)

        if user and not user.email:
            # Fetch emails endpoint if primary email not in user info
            try:
                import json
                import urllib.request

                req = urllib.request.Request(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )

                with urllib.request.urlopen(req, timeout=30) as response:
                    emails = json.loads(response.read().decode("utf-8"))

                # Find primary verified email
                for email_info in emails:
                    if email_info.get("primary") and email_info.get("verified"):
                        user.email = email_info.get("email")
                        user.email_verified = True
                        break

            except Exception as e:
                logger.warning(f"Failed to fetch GitHub emails: {e}")

        return user
