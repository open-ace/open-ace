"""
Open ACE - SSO Routes
API endpoints for Single Sign-On authentication.
"""

from __future__ import annotations


import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from flask import Blueprint, Response, g, jsonify, make_response, redirect, request, url_for

from app.auth.decorators import admin_required, auth_required, public_endpoint
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.sso.manager import SSOManager
from app.modules.sso.provider import get_provider_config, list_providers
from app.repositories.database import adapt_boolean_value
from app.repositories.user_repo import UserRepository
from app.services.auth_service import _get_session_timeout_hours
from app.utils.outbound_url_guard import OutboundUrlBlockedError, safe_request

logger = logging.getLogger(__name__)

# Create blueprint
sso_bp = Blueprint("sso", __name__, url_prefix="/api/sso")

# Services
_sso_manager = None
_audit_logger = None

# Test connection concurrency limit
_test_connection_lock = threading.Lock()
_test_connection_counter = 0
MAX_CONCURRENT_TESTS = 3


def get_sso_manager():
    global _sso_manager
    if _sso_manager is None:
        _sso_manager = SSOManager()
    return _sso_manager


def get_audit_logger():
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def _encode_state(original_state: str, redirect_uri: str) -> str:
    """Encode redirect_uri into state parameter.

    Args:
        original_state: Original state for CSRF verification.
        redirect_uri: Frontend redirect URI.

    Returns:
        str: Base64 encoded state containing both values.
    """
    import base64

    state_data = {
        "s": original_state,  # 原始 state 用于验证
        "r": redirect_uri,  # 前端重定向地址
    }
    return base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()


def _decode_state(encoded_state: str) -> tuple[str, str | None]:
    """Decode state parameter to get original state and redirect_uri.

    Args:
        encoded_state: Base64 encoded state parameter.

    Returns:
        tuple: (original_state, redirect_uri or None)
    """
    import base64

    try:
        state_data = json.loads(base64.urlsafe_b64decode(encoded_state).decode())
        return state_data.get("s", encoded_state), state_data.get("r")
    except (json.JSONDecodeError, Exception):
        # 兼容旧格式（纯 state 字符串）
        return encoded_state, None


def _get_allowed_redirect_domains() -> list[str]:
    """Get allowed redirect domains from environment variable.

    Returns:
        list: List of allowed domains.
    """
    domains = os.environ.get("SSO_ALLOWED_REDIRECT_DOMAINS", "")
    if domains:
        return [d.strip() for d in domains.split(",") if d.strip()]
    return []


def _validate_redirect_uri(redirect_uri: str) -> bool:
    """Validate redirect_uri against domain whitelist.

    Security: Prevent open redirect attacks.

    Args:
        redirect_uri: The frontend redirect URI to validate.

    Returns:
        bool: True if valid, False otherwise.
    """
    from urllib.parse import urlparse

    if not redirect_uri:
        return False

    try:
        parsed = urlparse(redirect_uri)
        if not parsed.scheme or not parsed.netloc:
            return False

        # 只允许 https（生产环境）或 http（开发环境 localhost）
        if parsed.scheme not in ("http", "https"):
            return False

        allowed_domains = _get_allowed_redirect_domains()

        # 如果没有配置白名单，只允许 localhost（开发环境）
        if not allowed_domains:
            hostname = parsed.netloc.split(":")[0]
            return hostname in ("localhost", "127.0.0.1", "[::1]")

        # 检查域名是否在白名单中
        for domain in allowed_domains:
            if parsed.netloc == domain or parsed.netloc.endswith(f".{domain}"):
                return True

        return False
    except Exception:
        return False


user_repo = UserRepository()


def validate_tenant_access(
    tenant_id: int | None = None, provider_name: str | None = None
) -> tuple[bool, int | None, str | None]:
    """
    Validate tenant access for the current user.

    Args:
        tenant_id: Target tenant ID (optional).
        provider_name: Provider name to check ownership (optional).

    Returns:
        tuple: (is_allowed, effective_tenant_id, error_message)
    """
    # Get current user info
    user_id = g.user_id
    if not user_id:
        return False, None, "Authentication required"

    user = user_repo.get_user_by_id(user_id)
    if not user:
        return False, None, "User not found"

    user_tenant_id = user.get("tenant_id")
    user_role = user.get("role")

    # Admin has cross-tenant access
    is_admin = user_role == "admin"

    # If provider_name is given, check provider's tenant
    if provider_name:
        provider_row = get_sso_manager().db.fetch_one(
            "SELECT tenant_id FROM sso_providers WHERE name = ?",
            (provider_name,),
        )
        if not provider_row:
            return False, None, "Provider not found"

        provider_tenant_id = provider_row.get("tenant_id")

        # If admin, allow access
        if is_admin:
            return True, provider_tenant_id, None

        # Non-admin: must match user's tenant
        if provider_tenant_id != user_tenant_id:
            return False, None, "无权管理该租户的 Provider"

        return True, provider_tenant_id, None

    # If tenant_id is given
    if tenant_id is not None:
        # If admin, allow access
        if is_admin:
            return True, tenant_id, None

        # Non-admin: must match user's tenant
        if tenant_id != user_tenant_id:
            return False, None, "无权管理该租户的 Provider"

        return True, tenant_id, None

    # No tenant_id given, use user's tenant
    effective_tenant = user_tenant_id
    return True, effective_tenant, None


def sanitize_config_for_audit(config: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize provider configuration for audit logging.

    Args:
        config: Provider configuration dict.

    Returns:
        dict: Sanitized configuration with sensitive fields masked.
    """
    if not config:
        return {}

    sanitized = config.copy()

    # Mask client_secret
    if "client_secret" in sanitized:
        sanitized["client_secret"] = "***"
    if "idp_x509_cert" in sanitized:
        sanitized["idp_x509_cert"] = "***"

    # Check extra_params for sensitive fields
    extra_params = sanitized.get("extra_params", {})
    if isinstance(extra_params, dict):
        sensitive_keywords = ["api_key", "private_key", "token", "secret", "password", "credential"]
        for key in list(extra_params.keys()):
            if any(kw in key.lower() for kw in sensitive_keywords):
                extra_params[key] = "***"
        sanitized["extra_params"] = extra_params

    return sanitized


def _get_client_ip() -> str | None:
    """Get client IP address from request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return str(forwarded_for.split(",")[0].strip())
    return str(request.remote_addr) if request.remote_addr else None


# ============================================================================
# Provider Management APIs
# ============================================================================


@sso_bp.route("/providers", methods=["GET"])
@public_endpoint
def list_sso_providers():
    """List available SSO providers."""
    tenant_id = request.args.get("tenant_id", type=int)

    providers = get_sso_manager().list_providers(tenant_id=tenant_id)

    # Transform provider fields to match frontend SSOProvider type:
    # - provider_type -> type
    # - is_active -> is_enabled
    registered = [
        {
            "name": p.get("name"),
            "type": p.get("provider_type"),
            "is_enabled": p.get("is_active", True),
            "tenant_id": p.get("tenant_id"),
        }
        for p in providers
    ]

    # Also include predefined providers with full config (type, display_name, icon)
    predefined_names = list_providers()
    predefined = []
    for name in predefined_names:
        config = get_provider_config(name)
        if config:
            # Determine default icon based on provider type if not configured
            provider_type = config.get("provider_type", "oidc")
            default_icon = "bi-shield-lock" if provider_type == "oidc" else "bi-key"
            predefined.append(
                {
                    "name": name,
                    "type": provider_type,
                    "display_name": config.get("name", name),
                    "icon": config.get("icon") or default_icon,
                }
            )

    return jsonify(
        {
            "registered": registered,
            "predefined": predefined,
        }
    )


@sso_bp.route("/providers/<provider_name>", methods=["GET"])
@admin_required
def get_provider_detail(provider_name: str):
    """Get detailed information about a specific SSO provider."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    # Get provider from database (including disabled ones)
    row = get_sso_manager().db.fetch_one(
        "SELECT * FROM sso_providers WHERE name = ?",
        (provider_name,),
    )

    if not row:
        return jsonify({"error": "Provider not found"}), 404

    try:
        config_data = get_sso_manager().deserialize_provider_config(row["config"])

        # Check if it's a predefined provider
        predefined_config = get_provider_config(provider_name)
        is_predefined = predefined_config is not None

        # Build response (exclude client_secret)
        response = {
            "name": row["name"],
            "type": row["provider_type"],
            "is_enabled": bool(row.get("is_active", True)),
            "is_predefined": is_predefined,
            "tenant_id": row.get("tenant_id"),
            "client_id": config_data.get("client_id", ""),
            "redirect_uri": config_data.get("redirect_uri"),
            "scope": config_data.get("scope", []),
            "authorization_url": config_data.get("authorization_url", ""),
            "token_url": config_data.get("token_url", ""),
            "userinfo_url": config_data.get("userinfo_url"),
            "issuer_url": config_data.get("issuer_url"),
            "extra_params": sanitize_config_for_audit(config_data.get("extra_params", {})),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Failed to get provider detail: {e}")
        return jsonify({"error": "Failed to get provider details"}), 500


@sso_bp.route("/providers", methods=["POST"])
@admin_required
def register_provider():
    """Register a new SSO provider (admin only)."""
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    provider_name = data.get("name") or data.get("provider_name")
    if not provider_name:
        return jsonify({"error": "Provider name is required"}), 400

    client_id = data.get("client_id")
    provider_type = data.get("provider_type", "oauth2")
    client_secret = data.get("client_secret", "")
    redirect_uri = data.get("redirect_uri")

    if provider_type == "saml":
        if not client_id:
            return jsonify({"error": "client_id is required as the SAML SP entity ID"}), 400
    elif not client_id or not client_secret:
        return jsonify({"error": "client_id and client_secret are required"}), 400

    # Validate tenant access
    tenant_id = data.get("tenant_id")
    allowed, effective_tenant_id, error = validate_tenant_access(tenant_id=tenant_id)
    if not allowed:
        return jsonify({"error": error}), 403

    success = False
    if data.get("predefined"):
        # Get override URLs for Okta/Auth0
        authorization_url = data.get("authorization_url")
        token_url = data.get("token_url")
        userinfo_url = data.get("userinfo_url")

        predefined_config = get_provider_config(provider_name)
        if not predefined_config:
            return jsonify({"error": f"Unknown predefined provider: {provider_name}"}), 400

        # Use override URLs if provided, otherwise use predefined defaults
        success = get_sso_manager().register_provider(
            name=provider_name,
            provider_type=predefined_config["provider_type"],
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=authorization_url or predefined_config["authorization_url"],
            token_url=token_url or predefined_config["token_url"],
            userinfo_url=userinfo_url or predefined_config.get("userinfo_url"),
            redirect_uri=redirect_uri,
            scope=data.get("scope") or predefined_config.get("scope"),
            issuer_url=data.get("issuer_url") or predefined_config.get("issuer_url"),
            tenant_id=effective_tenant_id,
            extra_params=data.get("extra_params"),
        )
    else:
        # Custom provider
        success = get_sso_manager().register_provider(
            name=provider_name,
            provider_type=provider_type,
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=data.get("authorization_url", ""),
            token_url=data.get("token_url", ""),
            userinfo_url=data.get("userinfo_url"),
            redirect_uri=redirect_uri,
            scope=data.get("scope"),
            issuer_url=data.get("issuer_url"),
            tenant_id=effective_tenant_id,
            extra_params=data.get("extra_params"),
        )

    if success:
        # Audit log
        get_audit_logger().log(
            action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="sso_provider",
            resource_id=provider_name,
            details={
                "operation": "register",
                "provider_name": provider_name,
                "tenant_id": effective_tenant_id,
                "is_predefined": data.get("predefined", False),
            },
            ip_address=_get_client_ip(),
        )

        return jsonify({"message": f"Provider {provider_name} registered successfully"}), 201
    else:
        return jsonify({"error": "Failed to register provider"}), 500


@sso_bp.route("/providers/<provider_name>", methods=["PUT"])
@admin_required
def update_provider(provider_name: str):
    """Update an existing SSO provider configuration."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Get existing provider
    existing = get_sso_manager().db.fetch_one(
        "SELECT * FROM sso_providers WHERE name = ?",
        (provider_name,),
    )

    if not existing:
        return jsonify({"error": "Provider not found"}), 404

    try:
        existing_config = get_sso_manager().deserialize_provider_config(existing["config"])
    except Exception:
        return jsonify({"error": "Failed to parse existing configuration"}), 500

    # Optimistic lock check
    expected_updated_at = data.get("updated_at")
    if expected_updated_at:
        current_updated_at = existing.get("updated_at")
        if current_updated_at:
            if isinstance(current_updated_at, datetime):
                current_str = current_updated_at.isoformat()
            else:
                current_str = str(current_updated_at)
            if current_str != expected_updated_at:
                return (
                    jsonify(
                        {
                            "error": "配置已被他人修改，请刷新后重新编辑",
                            "current_updated_at": current_str,
                        }
                    ),
                    409,
                )
    else:
        logger.warning(f"Update provider {provider_name} without updated_at (old client)")

    # Merge configuration (keep existing values for fields not provided)
    new_config = existing_config.copy()

    if data.get("client_id"):
        new_config["client_id"] = data["client_id"]
    if data.get("client_secret"):
        new_config["client_secret"] = data["client_secret"]
    if "redirect_uri" in data:
        new_config["redirect_uri"] = data["redirect_uri"]
    if "scope" in data:
        new_config["scope"] = data["scope"]
    if "authorization_url" in data:
        new_config["authorization_url"] = data["authorization_url"]
    if "token_url" in data:
        new_config["token_url"] = data["token_url"]
    if "userinfo_url" in data:
        new_config["userinfo_url"] = data["userinfo_url"]
    if "issuer_url" in data:
        new_config["issuer_url"] = data["issuer_url"]
    if "extra_params" in data:
        new_config["extra_params"] = data["extra_params"]

    # Update provider
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Check if provider was disabled - auto-enable on update
    was_disabled = not existing.get("is_active", True)

    serialized_config = get_sso_manager().serialize_provider_config(new_config)
    get_sso_manager().db.execute(
        """
        UPDATE sso_providers
        SET config = ?, updated_at = ?, is_active = ?
        WHERE name = ?
    """,
        (serialized_config, now, adapt_boolean_value(True), provider_name),
    )

    # Clear cache
    with get_sso_manager()._providers_lock:
        if provider_name in get_sso_manager()._providers:
            del get_sso_manager()._providers[provider_name]

    # Audit log
    get_audit_logger().log(
        action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="sso_provider",
        resource_id=provider_name,
        details={
            "operation": "update",
            "provider_name": provider_name,
            "auto_enabled": was_disabled,
            "changes": sanitize_config_for_audit(new_config),
        },
        ip_address=_get_client_ip(),
    )

    return jsonify(
        {
            "message": f"Provider {provider_name} updated successfully",
            "updated_at": now.isoformat(),
            "auto_enabled": was_disabled,
        }
    )


@sso_bp.route("/providers/<provider_name>/enable", methods=["PATCH"])
@admin_required
def enable_provider_route(provider_name: str):
    """Enable an SSO provider."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    success = get_sso_manager().enable_provider(provider_name)

    if success:
        # Audit log
        get_audit_logger().log(
            action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="sso_provider",
            resource_id=provider_name,
            details={
                "operation": "enable",
                "provider_name": provider_name,
            },
            ip_address=_get_client_ip(),
        )

        return jsonify({"message": f"Provider {provider_name} enabled"})
    else:
        return jsonify({"error": "Failed to enable provider"}), 500


@sso_bp.route("/providers/<provider_name>/disable", methods=["PATCH"])
@admin_required
def disable_provider_route(provider_name: str):
    """Disable an SSO provider (PATCH method, recommended)."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    success = get_sso_manager().disable_provider(provider_name)

    if success:
        # Audit log
        get_audit_logger().log(
            action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="sso_provider",
            resource_id=provider_name,
            details={
                "operation": "disable",
                "provider_name": provider_name,
            },
            ip_address=_get_client_ip(),
        )

        return jsonify({"message": f"Provider {provider_name} disabled"})
    else:
        return jsonify({"error": "Failed to disable provider"}), 500


@sso_bp.route("/providers/<provider_name>", methods=["DELETE"])
@admin_required
def disable_provider(provider_name: str):
    """Disable an SSO provider (DELETE method, deprecated - use PATCH /disable instead)."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    success = get_sso_manager().disable_provider(provider_name)

    if success:
        # Audit log
        get_audit_logger().log(
            action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="sso_provider",
            resource_id=provider_name,
            details={
                "operation": "disable",
                "provider_name": provider_name,
                "method": "DELETE (deprecated)",
            },
            ip_address=_get_client_ip(),
        )

        return jsonify({"message": f"Provider {provider_name} disabled"})
    else:
        return jsonify({"error": "Failed to disable provider"}), 500


@sso_bp.route("/providers/<provider_name>/reset", methods=["POST"])
@admin_required
def reset_provider_to_defaults(provider_name: str):
    """Reset a predefined provider to its default configuration."""
    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    # Check if it's a predefined provider
    predefined_config = get_provider_config(provider_name)
    if not predefined_config:
        return jsonify({"error": "Not a predefined provider"}), 400

    # Get existing provider
    existing = get_sso_manager().db.fetch_one(
        "SELECT * FROM sso_providers WHERE name = ?",
        (provider_name,),
    )

    if not existing:
        return jsonify({"error": "Provider not found"}), 404

    try:
        existing_config = get_sso_manager().deserialize_provider_config(existing["config"])
    except Exception:
        return jsonify({"error": "Failed to parse existing configuration"}), 500

    # Reset to predefined defaults, keeping client_id and client_secret
    new_config = {
        "name": provider_name,
        "provider_type": predefined_config["provider_type"],
        "client_id": existing_config.get("client_id", ""),
        "client_secret": existing_config.get("client_secret", ""),
        "authorization_url": predefined_config["authorization_url"],
        "token_url": predefined_config["token_url"],
        "userinfo_url": predefined_config.get("userinfo_url"),
        "redirect_uri": existing_config.get("redirect_uri"),
        "scope": predefined_config.get("scope", ["openid", "profile", "email"]),
        "issuer_url": predefined_config.get("issuer_url"),
        "tenant_id": existing.get("tenant_id"),
        "extra_params": {},
    }

    # Update provider
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    serialized_config = get_sso_manager().serialize_provider_config(new_config)
    get_sso_manager().db.execute(
        """
        UPDATE sso_providers
        SET config = ?, updated_at = ?
        WHERE name = ?
    """,
        (serialized_config, now, provider_name),
    )

    # Clear cache
    with get_sso_manager()._providers_lock:
        if provider_name in get_sso_manager()._providers:
            del get_sso_manager()._providers[provider_name]

    # Audit log
    get_audit_logger().log(
        action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="sso_provider",
        resource_id=provider_name,
        details={
            "operation": "reset",
            "provider_name": provider_name,
        },
        ip_address=_get_client_ip(),
    )

    return jsonify({"message": f"Provider {provider_name} reset to defaults"})


@sso_bp.route("/providers/<provider_name>/metadata", methods=["GET"])
@public_endpoint
def saml_metadata(provider_name: str):
    """Return SAML Service Provider metadata for an enabled SAML provider."""
    provider = get_sso_manager().get_provider(provider_name)
    if not provider or provider.provider_type != "saml":
        return jsonify({"error": "SAML provider not found"}), 404
    if not hasattr(provider, "get_service_provider_metadata"):
        return jsonify({"error": "Provider does not support metadata"}), 400

    acs_url = url_for("sso.saml_acs", provider_name=provider_name, _external=True)
    metadata = provider.get_service_provider_metadata(acs_url=acs_url)
    return Response(metadata, mimetype="application/samlmetadata+xml")


@sso_bp.route("/providers/<provider_name>/test", methods=["POST"])
@admin_required
def test_provider_connection(provider_name: str):
    """Test SSO provider connection (basic validation)."""
    global _test_connection_counter

    # Validate tenant access
    allowed, _, error = validate_tenant_access(provider_name=provider_name)
    if not allowed:
        return jsonify({"error": error}), 403

    # Concurrency limit
    with _test_connection_lock:
        if _test_connection_counter >= MAX_CONCURRENT_TESTS:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "测试连接繁忙，请稍后再试",
                    }
                ),
                429,
            )
        _test_connection_counter += 1

    try:
        # Get provider config
        existing = get_sso_manager().db.fetch_one(
            "SELECT * FROM sso_providers WHERE name = ?",
            (provider_name,),
        )

        if not existing:
            return jsonify({"success": False, "error": "Provider not found"}), 404

        try:
            config = json.loads(existing["config"])
        except Exception:
            return jsonify({"success": False, "error": "Failed to parse configuration"}), 500

        results = []
        all_passed = True

        provider_type = existing.get("provider_type") or config.get("provider_type")
        extra_params = (
            config.get("extra_params") if isinstance(config.get("extra_params"), dict) else {}
        )

        # Test authorization_url
        auth_url = config.get("authorization_url", "")
        if auth_url:
            url_result = _test_url_accessible(auth_url)
            results.append(
                {
                    "check": "authorization_url",
                    "url": auth_url,
                    "success": url_result["success"],
                    "error": url_result.get("error"),
                }
            )
            if not url_result["success"]:
                all_passed = False
        elif provider_type == "saml" and extra_params.get("idp_metadata_url"):
            url_result = _test_url_accessible(str(extra_params["idp_metadata_url"]))
            results.append(
                {
                    "check": "idp_metadata_url",
                    "url": extra_params["idp_metadata_url"],
                    "success": url_result["success"],
                    "error": url_result.get("error"),
                }
            )
            if not url_result["success"]:
                all_passed = False
        else:
            results.append(
                {
                    "check": "authorization_url",
                    "success": False,
                    "error": "Authorization URL not configured",
                }
            )
            all_passed = False

        # Test token_url for OAuth/OIDC. SAML validates its IdP certificate instead.
        token_url = config.get("token_url", "")
        if provider_type == "saml":
            idp_cert = (
                extra_params.get("idp_x509_cert") or extra_params.get("x509cert")
                if isinstance(extra_params, dict)
                else None
            )
            cert_ok = bool(
                idp_cert
                or extra_params.get("idp_metadata_xml")
                or extra_params.get("idp_metadata_url")
            )
            results.append(
                {
                    "check": "idp_x509_cert",
                    "success": cert_ok,
                    "error": None if cert_ok else "IdP signing certificate not configured",
                }
            )
            if not cert_ok:
                all_passed = False
        elif token_url:
            url_result = _test_url_accessible(token_url)
            results.append(
                {
                    "check": "token_url",
                    "url": token_url,
                    "success": url_result["success"],
                    "error": url_result.get("error"),
                }
            )
            if not url_result["success"]:
                all_passed = False
        else:
            results.append(
                {
                    "check": "token_url",
                    "success": False,
                    "error": "Token URL not configured",
                }
            )
            all_passed = False

        # Validate client_id format
        client_id = config.get("client_id", "")
        if not client_id or len(client_id) < 10:
            results.append(
                {
                    "check": "client_id",
                    "success": False,
                    "error": "Client ID 格式无效（长度不足）",
                }
            )
            all_passed = False
        else:
            results.append(
                {
                    "check": "client_id",
                    "success": True,
                }
            )

        # Validate scope
        scope = config.get("scope", [])
        if provider_type == "saml":
            results.append({"check": "scope", "success": True})
        elif not scope or not isinstance(scope, list) or len(scope) == 0:
            results.append(
                {
                    "check": "scope",
                    "success": False,
                    "error": "Scope 配置无效",
                }
            )
            all_passed = False
        else:
            results.append(
                {
                    "check": "scope",
                    "success": True,
                }
            )

        # Audit log
        get_audit_logger().log(
            action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="sso_provider",
            resource_id=provider_name,
            details={
                "operation": "test",
                "provider_name": provider_name,
                "success": all_passed,
            },
            ip_address=_get_client_ip(),
        )

        return jsonify(
            {
                "success": all_passed,
                "results": results,
            }
        )

    finally:
        with _test_connection_lock:
            _test_connection_counter -= 1


def _test_url_accessible(url: str) -> dict:
    """
    Test if a URL is accessible.

    Args:
        url: URL to test.

    Returns:
        dict: {"success": bool, "error": str or None}
    """
    try:
        current_url = url
        method = "HEAD"

        # Tight redirect budget (was 6): each hop is independently vulnerable to
        # DNS rebinding and a server-supplied protocol-relative ``Location``
        # (``//attacker.com``) can re-target the fetch. Two hops covers the
        # common HTTPS-upgrade / trailing-slash redirect case without leaving a
        # wide SSRF-amplification loop open.
        for _ in range(2):
            # ``safe_request`` resolves+validates the IP AND sends the request
            # pinned to that verified IP, closing the rebinding window per hop.
            if method == "HEAD":
                response = safe_request("HEAD", current_url, timeout=10, allow_redirects=False)
            else:
                response = safe_request(
                    "GET",
                    current_url,
                    timeout=10,
                    stream=True,
                    allow_redirects=False,
                )
                response.close()  # Don't read body

            if response.status_code == 405 and method == "HEAD":
                method = "GET"
                continue

            if 300 <= response.status_code < 400:
                from urllib.parse import urljoin

                location = response.headers.get("Location")
                if not location:
                    return {"success": False, "error": "重定向响应缺少 Location"}
                # Reject protocol-relative Location (``//attacker.com``) before
                # urljoin turns it into a fetch of an attacker-controlled host.
                if location.startswith("//"):
                    return {
                        "success": False,
                        "error": "不支持协议相对的重定向地址",
                    }
                current_url = urljoin(current_url, location)
                continue

            if response.status_code < 400:
                return {"success": True}
            return {"success": False, "error": f"HTTP {response.status_code}"}

        return {"success": False, "error": "重定向次数过多"}

    except requests.Timeout:
        return {"success": False, "error": "连接超时"}
    except requests.ConnectionError:
        return {"success": False, "error": "无法连接到服务器"}
    except OutboundUrlBlockedError as e:
        return {"success": False, "error": f"URL 被安全策略拦截: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@sso_bp.route("/providers/export", methods=["GET"])
@admin_required
def export_providers():
    """Export SSO provider configurations."""
    data = request.args
    tenant_id = data.get("tenant_id", type=int)

    # Validate tenant access
    allowed, effective_tenant_id, error = validate_tenant_access(tenant_id=tenant_id)
    if not allowed:
        return jsonify({"error": error}), 403

    # Get user role
    user_id = g.user_id
    user = user_repo.get_user_by_id(user_id)
    is_admin = user and user.get("role") == "admin"

    # Query providers
    if is_admin and not effective_tenant_id:
        # Admin without tenant filter - export all
        rows = get_sso_manager().db.fetch_all(
            "SELECT name, provider_type, config, tenant_id, is_active, created_at, updated_at FROM sso_providers"
        )
    else:
        # Non-admin or specific tenant
        rows = get_sso_manager().db.fetch_all(
            "SELECT name, provider_type, config, tenant_id, is_active, created_at, updated_at FROM sso_providers WHERE tenant_id = ?",
            (effective_tenant_id,),
        )

    # Build export data (exclude client_secret)
    providers = []
    provider_names = []

    for row in rows:
        try:
            config = get_sso_manager().deserialize_provider_config(row["config"])
            provider_names.append(row["name"])

            # Check if predefined
            predefined_config = get_provider_config(row["name"])
            is_predefined = predefined_config is not None

            providers.append(
                {
                    "name": row["name"],
                    "type": row["provider_type"],
                    "is_enabled": bool(row.get("is_active", True)),
                    "is_predefined": is_predefined,
                    "tenant_id": row.get("tenant_id"),
                    "client_id": config.get("client_id", ""),
                    "redirect_uri": config.get("redirect_uri"),
                    "scope": config.get("scope", []),
                    "authorization_url": config.get("authorization_url", ""),
                    "token_url": config.get("token_url", ""),
                    "userinfo_url": config.get("userinfo_url"),
                    "issuer_url": config.get("issuer_url"),
                    "extra_params": sanitize_config_for_audit(config.get("extra_params", {})),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        except Exception as e:
            logger.error(f"Failed to export provider {row.get('name')}: {e}")

    # Audit log
    get_audit_logger().log(
        action=AuditAction.DATA_EXPORT.value,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="sso_provider",
        resource_id="export",
        details={
            "exported_count": len(providers),
            "provider_names": provider_names,
            "export_format": "json",
            "tenant_filter": effective_tenant_id or "all",
        },
        ip_address=_get_client_ip(),
    )

    return jsonify(
        {
            "providers": providers,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(providers),
        }
    )


# ============================================================================
# SSO Authentication APIs (existing)
# ============================================================================


@sso_bp.route("/login/<provider_name>", methods=["GET"])
@public_endpoint
def start_login(provider_name: str):
    """
    Start SSO login flow.

    Returns the authorization URL to redirect the user.

    Query Parameters:
        redirect_uri: Frontend URL to redirect after successful SSO login.
                      Encoded into OAuth state parameter for reliability.
    """
    import urllib.parse

    # Get frontend redirect URI (for post-auth redirect)
    frontend_redirect_uri = request.args.get("redirect_uri")

    provider = get_sso_manager().get_provider(provider_name)
    is_saml = bool(provider and provider.provider_type == "saml")

    # Build callback/ACS URL (this is where the provider redirects/posts back to)
    callback_uri = url_for(
        "sso.saml_acs" if is_saml else "sso.callback",
        provider_name=provider_name,
        _external=True,
    )

    result = get_sso_manager().start_authentication(provider_name, callback_uri)

    if not result:
        return jsonify({"error": f"Failed to start authentication for {provider_name}"}), 500

    # Encode redirect_uri into state parameter (more reliable than session)
    if frontend_redirect_uri and _validate_redirect_uri(frontend_redirect_uri):
        encoded_state = _encode_state(result["state"], frontend_redirect_uri)

        # Update authorization_url with new state
        parsed = urllib.parse.urlparse(result["authorization_url"])
        query_params = urllib.parse.parse_qs(parsed.query)
        state_key = "RelayState" if "SAMLRequest" in query_params else "state"
        query_params[state_key] = [encoded_state]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        result["authorization_url"] = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )
        result["state"] = encoded_state
    elif frontend_redirect_uri:
        # 域名验证失败，记录警告但不阻止登录
        logger.warning(f"Invalid redirect_uri domain rejected: {frontend_redirect_uri}")

    # For API clients, return the URL
    if request.args.get("json") or request.headers.get("Accept") == "application/json":
        return jsonify(result)

    # For browsers, redirect directly
    return redirect(result["authorization_url"])


@sso_bp.route("/callback/<provider_name>", methods=["GET"])
@public_endpoint
def callback(provider_name: str):
    """
    Handle SSO callback.

    This endpoint receives the authorization code from the provider.
    On success, redirects to frontend with session token.
    """
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    # Decode redirect_uri from state parameter
    original_state, frontend_url = _decode_state(state)

    # Handle error from provider
    if error:
        logger.error(f"SSO error from {provider_name}: {error} - {error_description}")
        if frontend_url and _validate_redirect_uri(frontend_url):
            return redirect(f"{frontend_url}?sso_error=auth_failed")
        return (
            jsonify(
                {
                    "error": error,
                    "error_description": error_description,
                }
            ),
            400,
        )

    if not code or not original_state:
        if frontend_url and _validate_redirect_uri(frontend_url):
            return redirect(f"{frontend_url}?sso_error=invalid_request")
        return jsonify({"error": "Missing code or state"}), 400

    # Get OAuth callback URI (must match what was used in start_login)
    oauth_callback_uri = url_for("sso.callback", provider_name=provider_name, _external=True)

    # Complete authentication (use original_state for verification)
    auth_result = get_sso_manager().complete_authentication(
        provider_name=provider_name,
        code=code,
        state=original_state,
        redirect_uri=oauth_callback_uri,
    )

    if not auth_result.success:
        if frontend_url and _validate_redirect_uri(frontend_url):
            error_type = auth_result.error or "auth_failed"
            return redirect(f"{frontend_url}?sso_error={error_type}")
        return (
            jsonify(
                {
                    "error": auth_result.error,
                    "error_description": auth_result.error_description,
                }
            ),
            400,
        )

    return _finalize_sso_login(provider_name, auth_result, frontend_url)


def _allow_email_linking(provider_name: str) -> bool:
    """Return True only when the SSO provider explicitly opts into email-based
    account linking.

    Default is False (secure): an IdP-asserted email is not trusted to bind onto a
    pre-existing local account, which prevents privilege escalation via email
    collision when the IdP asserts an unverified/attacker-controlled address.
    """
    provider = get_sso_manager().get_provider(provider_name)
    if provider is None:
        return False
    return bool(provider.config.extra_params.get("allow_email_linking"))


def _finalize_sso_login(provider_name: str, auth_result, frontend_url: str | None):
    """Create/link the local user and establish Open ACE sessions after SSO success."""
    user_id = None
    linked_by_email = False
    if auth_result.user:
        user_id = get_sso_manager().get_user_by_sso_identity(
            provider_name,
            auth_result.user.provider_user_id,
        )

        if not user_id:
            # Link to an existing local account by email ONLY when the provider
            # explicitly opts in. Default behaviour is to provision a fresh user
            # rather than risk binding an IdP-asserted (unverified) email onto an
            # existing account — especially a privileged one.
            if auth_result.user.email and _allow_email_linking(provider_name):
                existing_user = user_repo.get_user_by_email(auth_result.user.email)
                if existing_user:
                    user_id = existing_user.get("id")
                    linked_by_email = True  # an actual binding happened

            # Create new user if not found
            if not user_id:
                user_id = _create_user_from_sso(auth_result.user, provider_name)

            # Link identity
            if user_id:
                get_sso_manager().link_identity(
                    user_id=user_id,
                    provider_name=provider_name,
                    provider_user_id=auth_result.user.provider_user_id,
                    provider_data=auth_result.user.to_dict(),
                )

    # Create session
    session_token = None
    if user_id and auth_result.token:
        session_token = get_sso_manager().create_sso_session(
            user_id=user_id,
            provider_name=provider_name,
            access_token=auth_result.token.access_token,
            refresh_token=auth_result.token.refresh_token,
            expires_in=auth_result.token.expires_in,
        )

        # Also create local session with correct expiration time
        timeout_hours = _get_session_timeout_hours()
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            hours=timeout_hours
        )
        UserRepository().create_session(
            user_id=user_id,
            token=session_token,
            expires_at=expires_at,
        )

    # Audit-log the SSO login. Password auth records LOGIN/LOGOUT; SSO sessions,
    # email-based linking, and auto-provisioning must be visible to the audit trail
    # for forensic purposes.
    try:
        get_audit_logger().log(
            action=AuditAction.LOGIN.value,
            user_id=user_id,
            username=auth_result.user.username if auth_result.user else None,
            resource_type="sso_session",
            resource_id=provider_name,
            details={
                "provider": provider_name,
                "method": "sso",
                # Reflect the ACTUAL linking outcome for forensic accuracy: True
                # only when an existing local account was bound to the IdP email.
                "email_linked": linked_by_email,
                # Config snapshot so investigators can tell whether linking was
                # even permitted at login time, independent of the outcome above.
                "email_linking_enabled": _allow_email_linking(provider_name),
            },
            ip_address=request.remote_addr if request else None,
            user_agent=request.headers.get("User-Agent") if request else None,
            success=bool(user_id),
        )
    except Exception:
        logger.warning("Failed to audit-log SSO login", exc_info=True)

    # Redirect to frontend if configured, otherwise return JSON
    if frontend_url and session_token and _validate_redirect_uri(frontend_url):
        timeout_seconds = int(_get_session_timeout_hours() * 3600)
        response = make_response(redirect(f"{frontend_url}?sso_success=1"))
        response.set_cookie(
            "session_token",
            session_token,
            max_age=timeout_seconds,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
        )
        return response

    # Return result (fallback for API calls or missing session_token)
    return jsonify(
        {
            "success": True,
            "user": auth_result.user.to_dict() if auth_result.user else None,
            "session_token": session_token,
        }
    )


@sso_bp.route("/acs/<provider_name>", methods=["POST"])
@public_endpoint
def saml_acs(provider_name: str):
    """Handle SAML HTTP-POST Assertion Consumer Service callbacks."""
    # Scope the parse-DoS cap to this one unauthenticated endpoint (a real
    # SAMLResponse is well under 100KB; 256KB is a generous ceiling). We check
    # request.content_length here instead of setting a global MAX_CONTENT_LENGTH
    # so authenticated upload endpoints that legitimately carry larger bodies
    # are unaffected.
    max_saml_response = 256 * 1024
    if (request.content_length or 0) > max_saml_response:
        return jsonify({"error": "saml_response_too_large"}), 413

    saml_response = request.form.get("SAMLResponse")
    relay_state = request.form.get("RelayState", "")

    original_state, frontend_url = _decode_state(relay_state)
    if not saml_response or not original_state:
        if frontend_url and _validate_redirect_uri(frontend_url):
            return redirect(f"{frontend_url}?sso_error=invalid_request")
        return jsonify({"error": "Missing SAMLResponse or RelayState"}), 400

    acs_url = url_for("sso.saml_acs", provider_name=provider_name, _external=True)
    auth_result = get_sso_manager().complete_saml_authentication(
        provider_name=provider_name,
        saml_response=saml_response,
        relay_state=original_state,
        acs_url=acs_url,
    )

    if not auth_result.success:
        if frontend_url and _validate_redirect_uri(frontend_url):
            error_type = auth_result.error or "auth_failed"
            return redirect(f"{frontend_url}?sso_error={error_type}")
        return (
            jsonify(
                {
                    "error": auth_result.error,
                    "error_description": auth_result.error_description,
                }
            ),
            400,
        )

    return _finalize_sso_login(provider_name, auth_result, frontend_url)


@sso_bp.route("/session", methods=["GET"])
def get_session():
    """Get current SSO session info."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if not token:
        return jsonify({"error": "No session token provided"}), 401

    session_data = get_sso_manager().get_sso_session(token)

    if not session_data:
        return jsonify({"error": "Invalid or expired session"}), 401

    return jsonify(session_data)


@sso_bp.route("/session", methods=["DELETE"])
@public_endpoint
def logout():
    """Logout from SSO session."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    session_data = None
    if token:
        session_data = get_sso_manager().get_sso_session(token)
        get_sso_manager().delete_sso_session(token)

    # Audit-log the SSO logout so SSO session termination is visible alongside
    # password-auth logouts.
    try:
        get_audit_logger().log(
            action=AuditAction.LOGOUT.value,
            user_id=session_data.get("user_id") if session_data else None,
            resource_type="sso_session",
            resource_id=session_data.get("provider_name") if session_data else None,
            details={
                "method": "sso",
                "provider": session_data.get("provider_name") if session_data else None,
            },
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
            success=bool(session_data),
        )
    except Exception:
        logger.warning("Failed to audit-log SSO logout", exc_info=True)

    return jsonify({"message": "Logged out successfully"})


@sso_bp.route("/identities/<int:user_id>", methods=["GET"])
@auth_required
def get_user_identities(user_id: int):
    """Get SSO identities for a user."""

    # Only allow users to see their own identities (or admins)
    session_user_id = g.user_id
    is_admin = g.user_role == "admin"

    if session_user_id != user_id and not is_admin:
        return jsonify({"error": "Access denied"}), 403

    # Get identities from database
    identities = get_sso_manager().db.fetch_all(
        """
        SELECT provider_name, provider_user_id, created_at, last_used_at
        FROM sso_identities
        WHERE user_id = ?
    """,
        (user_id,),
    )

    return jsonify(
        {
            "user_id": user_id,
            "identities": [dict(i) for i in identities],
        }
    )


@sso_bp.route("/identities/<int:user_id>/<provider_name>", methods=["DELETE"])
@auth_required
def unlink_identity(user_id: int, provider_name: str):
    """Unlink an SSO identity from a user."""

    # Only allow users to unlink their own identities (or admins)
    session_user_id = g.user_id
    is_admin = g.user_role == "admin"

    if session_user_id != user_id and not is_admin:
        return jsonify({"error": "Access denied"}), 403

    try:
        with get_sso_manager().db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM sso_identities
                WHERE user_id = ? AND provider_name = ?
            """,
                (user_id, provider_name),
            )
            conn.commit()

            if cursor.rowcount > 0:
                return jsonify({"message": f"Identity {provider_name} unlinked"})
            else:
                return jsonify({"error": "Identity not found"}), 404

    except Exception as e:
        logger.error(f"Failed to unlink identity: {e}")
        return jsonify({"error": "Failed to unlink identity"}), 500


def _create_user_from_sso(sso_user, provider_name: str) -> int | None:
    """
    Create a local user from SSO user info.

    Args:
        sso_user: SSO user info.
        provider_name: SSO provider name.

    Returns:
        Optional[int]: New user ID or None.
    """
    # Generate username if not provided
    username = sso_user.username or sso_user.email or f"{provider_name}_{sso_user.provider_user_id}"

    # Ensure username is unique
    base_username = username
    counter = 1
    while user_repo.get_user_by_username(username):
        username = f"{base_username}_{counter}"
        counter += 1

    # Create user
    try:
        user_id = user_repo.create_user(
            username=username,
            email=sso_user.email or "",
            password_hash="",  # No password for SSO users
            role="user",
        )

        if user_id:
            logger.info(f"Created user {username} from SSO provider {provider_name}")

        return user_id

    except Exception as e:
        logger.error(f"Failed to create user from SSO: {e}")
        return None


def register_sso_routes(app):
    """Register SSO routes with the Flask app."""
    app.register_blueprint(sso_bp)
