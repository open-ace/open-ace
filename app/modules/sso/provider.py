"""
Open ACE - SSO Provider Base

Base class and configuration for SSO providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ProviderType(Enum):
    """SSO provider types."""

    OAUTH2 = "oauth2"
    OIDC = "oidc"
    SAML = "saml"


@dataclass
class SSOProviderConfig:
    """Configuration for an SSO provider."""

    name: str
    provider_type: str
    client_id: str
    client_secret: str
    authorization_url: str
    token_url: str
    userinfo_url: Optional[str] = None
    redirect_uri: Optional[str] = None
    scope: list[str] = field(default_factory=lambda: ["openid", "profile", "email"])
    issuer_url: Optional[str] = None
    jwks_url: Optional[str] = None

    # Additional configuration
    extra_params: dict[str, Any] = field(default_factory=dict)

    # Tenant association
    tenant_id: Optional[int] = None

    # Status
    is_active: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "provider_type": self.provider_type,
            "client_id": self.client_id,
            "client_secret": "***",  # Don't expose secret
            "authorization_url": self.authorization_url,
            "token_url": self.token_url,
            "userinfo_url": self.userinfo_url,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "issuer_url": self.issuer_url,
            "jwks_url": self.jwks_url,
            "extra_params": self.extra_params,
            "tenant_id": self.tenant_id,
            "is_active": self.is_active,
        }


@dataclass
class SSOUser:
    """User information from SSO provider."""

    provider: str
    provider_user_id: str
    email: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    picture: Optional[str] = None
    locale: Optional[str] = None
    email_verified: bool = False
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "provider_user_id": self.provider_user_id,
            "email": self.email,
            "username": self.username,
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "picture": self.picture,
            "locale": self.locale,
            "email_verified": self.email_verified,
        }


@dataclass
class SSOToken:
    """SSO token information."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None
    scope: Optional[str] = None
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "access_token": "***",  # Don't expose token
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_token": "***" if self.refresh_token else None,
            "id_token": "***" if self.id_token else None,
            "scope": self.scope,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def is_expired(self) -> bool:
        """Check if token is expired."""
        if not self.expires_at:
            return False
        return datetime.utcnow() >= self.expires_at


@dataclass
class SSOAuthResult:
    """Result of SSO authentication."""

    success: bool
    user: Optional[SSOUser] = None
    token: Optional[SSOToken] = None
    error: Optional[str] = None
    error_description: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "user": self.user.to_dict() if self.user else None,
            "token": self.token.to_dict() if self.token else None,
            "error": self.error,
            "error_description": self.error_description,
        }


class SSOProvider(ABC):
    """Abstract base class for SSO providers."""

    def __init__(self, config: SSOProviderConfig):
        """
        Initialize SSO provider.

        Args:
            config: Provider configuration.
        """
        self.config = config

    @abstractmethod
    def get_authorization_url(
        self,
        state: str,
        redirect_uri: Optional[str] = None,
        code_challenge: Optional[str] = None,
        nonce: Optional[str] = None,
    ) -> str:
        """
        Get the authorization URL for the OAuth flow.

        Args:
            state: State parameter for CSRF protection.
            redirect_uri: Override redirect URI.
            code_challenge: PKCE code challenge (optional).
            nonce: Nonce for replay protection (optional).

        Returns:
            str: Authorization URL.
        """
        pass

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: Optional[str] = None) -> SSOAuthResult:
        """
        Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback.
            redirect_uri: Redirect URI used in authorization.

        Returns:
            SSOAuthResult: Authentication result with tokens.
        """
        pass

    @abstractmethod
    def get_user_info(self, access_token: str) -> Optional[SSOUser]:
        """
        Get user information using access token.

        Args:
            access_token: OAuth access token.

        Returns:
            Optional[SSOUser]: User information or None.
        """
        pass

    @abstractmethod
    def refresh_token(self, refresh_token: str) -> Optional[SSOToken]:
        """
        Refresh the access token.

        Args:
            refresh_token: OAuth refresh token.

        Returns:
            Optional[SSOToken]: New token or None.
        """
        pass

    def authenticate(self, code: str, redirect_uri: Optional[str] = None) -> SSOAuthResult:
        """
        Complete authentication flow.

        Args:
            code: Authorization code.
            redirect_uri: Redirect URI.

        Returns:
            SSOAuthResult: Authentication result.
        """
        # Exchange code for tokens
        result = self.exchange_code(code, redirect_uri)

        if not result.success:
            return result

        # Get user info
        if result.token:
            user = self.get_user_info(result.token.access_token)
            if user:
                result.user = user
            else:
                result.success = False
                result.error = "failed_to_get_user_info"

        return result

    def is_active(self) -> bool:
        """Check if provider is active."""
        return self.config.is_active

    @property
    def name(self) -> str:
        """Get provider name."""
        return self.config.name

    @property
    def provider_type(self) -> str:
        """Get provider type."""
        return self.config.provider_type


# Predefined provider configurations
PROVIDER_CONFIGS = {
    "google": {
        "name": "Google",
        "provider_type": "oidc",
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "issuer_url": "https://accounts.google.com",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "scope": ["openid", "profile", "email"],
    },
    "microsoft": {
        "name": "Microsoft",
        "provider_type": "oidc",
        "authorization_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "issuer_url": "https://login.microsoftonline.com/common/v2.0",
        "jwks_url": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
        "scope": ["openid", "profile", "email"],
    },
    "github": {
        "name": "GitHub",
        "provider_type": "oauth2",
        "authorization_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scope": ["user:email", "read:user"],
    },
    "okta": {
        "name": "Okta",
        "provider_type": "oidc",
        # These need to be configured per-tenant
        "authorization_url": "",  # https://{domain}/oauth2/v1/authorize
        "token_url": "",  # https://{domain}/oauth2/v1/token
        "userinfo_url": "",  # https://{domain}/oauth2/v1/userinfo
        "issuer_url": "",  # https://{domain}
        "scope": ["openid", "profile", "email"],
    },
    "auth0": {
        "name": "Auth0",
        "provider_type": "oidc",
        # These need to be configured per-tenant
        "authorization_url": "",  # https://{domain}/authorize
        "token_url": "",  # https://{domain}/oauth/token
        "userinfo_url": "",  # https://{domain}/userinfo
        "issuer_url": "",  # https://{domain}
        "scope": ["openid", "profile", "email"],
    },
}


def get_provider_config(provider_name: str) -> Optional[dict[str, Any]]:
    """
    Get predefined provider configuration.

    Args:
        provider_name: Provider name (e.g., 'google', 'microsoft').

    Returns:
        Optional[Dict]: Provider configuration or None.
    """
    return PROVIDER_CONFIGS.get(provider_name)


def list_providers() -> list[str]:
    """
    List available predefined providers.

    Returns:
        List[str]: List of provider names.
    """
    return list(PROVIDER_CONFIGS.keys())
