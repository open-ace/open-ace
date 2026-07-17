"""
Open ACE - SSO Module

Single Sign-On integration for enterprise authentication.
Supports OAuth2 and OIDC providers. SAML 2.0 is tracked in issue #1784.
"""

from app.modules.sso.manager import SSOManager
from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.oidc import OIDCProvider
from app.modules.sso.provider import SSOProvider, SSOProviderConfig

__all__ = [
    "SSOProvider",
    "SSOProviderConfig",
    "OAuth2Provider",
    "OIDCProvider",
    "SSOManager",
]
