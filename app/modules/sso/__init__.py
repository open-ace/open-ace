#!/usr/bin/env python3
"""
Open ACE - SSO Module

Single Sign-On integration for enterprise authentication.
Supports OAuth2, OIDC, and SAML providers.
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
