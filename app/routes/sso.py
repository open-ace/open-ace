"""
Open ACE - SSO Routes

API endpoints for Single Sign-On authentication.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import requests
from flask import Blueprint, Response, g, jsonify, make_response, redirect, request, url_for

from app.auth.decorators import admin_required, auth_required, public_endpoint
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.sso.manager import SSOManager
from app.modules.sso.provider import get_provider_config, list_providers
from app.modules.sso.saml import NSMAP, SAML_ASSERTION_NS, SAML_PROTOCOL_NS, SAML_SUCCESS_STATUS
from app.repositories.database import adapt_boolean_value
from app.repositories.user_repo import UserRepository
from app.services.auth_service import _get_session_timeout_hours
from app.utils.config import get_config_value
from app.utils.outbound_url_guard import OutboundUrlBlockedError, safe_request

logger = logging.getLogger(__name__)

# Issue #1826 F6: Transition warning for SSO_NULL_TENANT_POLICY=warn
# Check at module load time and emit deprecation notice if warn policy is configured
_null_tenant_policy = os.environ.get("SSO_NULL_TENANT_POLICY", "reject")
if _null_tenant_policy == "warn":
    logger.warning(
        "DEPRECATION NOTICE: SSO_NULL_TENANT_POLICY=warn currently rejects user creation. "
        "This behavior will continue in future versions. "
        "Please migrate to 'reject' or configure provider default_tenant_id."
    )

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


def _build_acs_url(provider_name: str) -> str:
    """Build the SAML ACS URL, preferring a configured canonical base URL.

    Issue #1832 F7: the three SAML ACS call sites (SP metadata, the SAML
    branch of ``start_login``, and the ``saml_acs`` callback) previously each
    called ``url_for("sso.saml_acs", ..., _external=True)``, which derives the
    host from the incoming request's ``Host`` / ``X-Forwarded-*`` headers. A
    spoofed Host header can then influence the ACS URL embedded in SP metadata
    / the AuthnRequest and the one compared against the SAML Response's
    Destination/Recipient — the three values MUST stay identical, so all three
    sites route through this single helper.

    When ``sso.canonical_base_url`` is configured (e.g.
    ``https://openace.example.com``) the ACS URL uses that fixed scheme+host,
    independent of request headers. When unset or invalid, behavior is
    unchanged (falls back to ``url_for``). This is a hardening measure, not a
    directly-exploitable fix; configure it only behind a trusted reverse proxy
    / fixed public domain.

    Scope (important for operators):
      * This affects ONLY the three SAML ACS URLs. The OAuth callback
        (``sso.callback``) is intentionally NOT converged here — its
        redirect_uri is still derived from the request host, because OAuth
        providers register redirect URIs out of band and a mismatch would
        break OAuth login. ``start_login`` calls this helper only on its
        ``is_saml`` branch.
      * A single global ``canonical_base_url`` is shared by every SAML
        provider. Deployments running multiple SAML IdPs behind different
        public domains are not fully served by this one setting; a
        per-provider override is left as a future extension.
    """
    default = url_for("sso.saml_acs", provider_name=provider_name, _external=True)
    canonical_base = get_config_value("sso", "canonical_base_url", None)
    if not canonical_base:
        return cast("str", default)

    canonical_base = str(canonical_base).strip().rstrip("/")
    parsed = urlparse(canonical_base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        logger.error(
            "sso.canonical_base_url %r is not a valid http(s) URL; "
            "falling back to request-derived ACS URL",
            canonical_base,
        )
        return cast("str", default)

    # Keep the route-defined path/query, swap only scheme + host so the ACS
    # path can never drift from the blueprint registration.
    default_parsed = urlparse(default)
    return urlunparse(
        (parsed.scheme, parsed.netloc, default_parsed.path, "", default_parsed.query, "")
    )


# Issue #1826 F8: RelayState signing configuration
# Use HMAC-SHA256 from standard library (no new dependencies)


def _get_relaystate_signing_key() -> bytes:
    """Get signing key for RelayState.

    Issue #1826 F8: Use Fernet key or independent SSO_RELAYSTATE_SIGNING_KEY.
    """
    # Try dedicated key first
    key = os.environ.get("SSO_RELAYSTATE_SIGNING_KEY")
    if key:
        return key.encode("utf-8")

    # Fall back to encryption key (same as used by SMTPPasswordManager)
    from app.utils.security_env import get_encryption_key_material

    try:
        # Use same key derivation as SMTPPasswordManager
        key_material = get_encryption_key_material(purpose="RelayState signing")
        return hashlib.sha256(key_material.encode()).digest()
    except Exception:
        # Last resort: generate a stable key from app secret
        app_secret = os.environ.get("SECRET_KEY", "open-ace-default-secret")
        return hashlib.sha256(app_secret.encode()).digest()


def _encode_state(original_state: str, redirect_uri: str) -> str:
    """Encode redirect_uri into state parameter with HMAC signature.

    Issue #1826 F8: Add HMAC-SHA256 signature for integrity protection.

    Args:
        original_state: Original state for CSRF verification.
        redirect_uri: Frontend redirect URI.

    Returns:
        str: Base64 encoded state containing both values and signature.
    """
    import base64

    # New format with signature (v=2)
    state_data = {
        "v": 2,  # Version identifier
        "s": original_state,  # Original state for CSRF verification
        "r": redirect_uri,  # Frontend redirect URI
        "t": int(datetime.now(timezone.utc).timestamp()),  # Timestamp for replay protection
    }

    # Serialize and sign
    payload = json.dumps(state_data, sort_keys=True).encode()
    signing_key = _get_relaystate_signing_key()
    signature = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()

    state_data["sig"] = signature

    return base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()


def _decode_state(encoded_state: str) -> tuple[str, str | None]:
    """Decode state parameter to get original state and redirect_uri.

    Issue #1826 F8: Verify HMAC signature, reject tampered states.

    Args:
        encoded_state: Base64 encoded state parameter.

    Returns:
        tuple: (original_state, redirect_uri or None)

    Note: Transition period ends 2027-01-31 (6 months from 2026-07-31).
    After that date, legacy format will be rejected with 400 error.
    """
    import base64

    try:
        state_data = json.loads(base64.urlsafe_b64decode(encoded_state).decode())

        # Check version
        if state_data.get("v") == 2:
            # New format with signature verification
            signature = state_data.get("sig", "")

            # Reconstruct payload for verification (without signature)
            verify_data = {k: v for k, v in state_data.items() if k != "sig"}
            payload = json.dumps(verify_data, sort_keys=True).encode()

            signing_key = _get_relaystate_signing_key()
            expected_signature = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                logger.warning("RelayState signature verification failed")
                # Issue #1826 F8: Reject tampered state instead of degrading
                return (encoded_state, None)

            # Signature valid, extract data
            return (state_data.get("s", encoded_state), state_data.get("r"))

        else:
            # Old format (no signature) - log warning during transition period
            # Issue #1826 F8: Transition period ends 2027-01-31
            # After that, this branch should be removed and legacy format rejected
            logger.warning(
                "RelayState using legacy format without signature. "
                "This format will be rejected after 2027-01-31. "
                "Count: relaystate_legacy_format_total"
            )
            return (state_data.get("s", encoded_state), state_data.get("r"))

    except (json.JSONDecodeError, Exception) as e:
        # Issue #1826 F8: Don't silently fall back to treating input as valid state
        logger.warning(f"Failed to decode RelayState: {e}")
        # Return (encoded_state, None) to allow legacy format during transition
        # After transition period, this should return error
        return (encoded_state, None)


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

    # Issue #1826 F6: Only update client_secret if provided in request
    # This avoids unnecessary re-encryption (Fernet IV churn) and audit noise
    has_new_secret = False
    if data.get("client_id"):
        new_config["client_id"] = data["client_id"]
    if data.get("client_secret"):
        new_config["client_secret"] = data["client_secret"]
        has_new_secret = True
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

    # Issue #1826 F6: Preserve existing encrypted secret if no new secret provided
    if has_new_secret:
        # Re-encrypt with new secret
        serialized_config = get_sso_manager().serialize_provider_config(new_config)
    else:
        # Preserve existing encrypted secret - avoid unnecessary re-encryption
        # Load existing config and check if encrypted_secret exists
        existing_raw_config = json.loads(existing["config"])
        existing_encrypted = existing_raw_config.get("client_secret_encrypted", "")

        if existing_encrypted:
            # Provider already has encrypted secret - preserve it to avoid IV churn
            # Remove client_secret from new_config to avoid re-encryption
            config_for_serialize = new_config.copy()
            config_for_serialize.pop("client_secret", None)

            # Serialize and manually inject existing encrypted secret
            serialized_config_dict = json.loads(
                get_sso_manager().serialize_provider_config(config_for_serialize)
            )
            serialized_config_dict["client_secret_encrypted"] = existing_encrypted
            serialized_config = json.dumps(serialized_config_dict)
        else:
            # No existing encrypted secret - this is a legacy provider or SAML
            # Serialize with current client_secret (empty for SAML, or plaintext for legacy)
            # serialize_provider_config will encrypt it properly for legacy providers
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

    acs_url = _build_acs_url(provider_name)
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

    # Build callback/ACS URL (this is where the provider redirects/posts back to).
    # Issue #1832 F7: SAML providers route through the canonical ACS helper so the
    # ACS URL declared to the IdP here is byte-identical to the one compared in the
    # saml_acs callback. OAuth providers MUST stay on the request-derived callback
    # (their redirect_uri is registered out of band), so the helper is applied only
    # on the is_saml branch.
    callback_uri = (
        _build_acs_url(provider_name)
        if is_saml
        else url_for("sso.callback", provider_name=provider_name, _external=True)
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

    acs_url = _build_acs_url(provider_name)
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


# ============================================================================
# Issue #2174 F7: SAML Single Logout (SLO) Endpoints
# ============================================================================


@sso_bp.route("/slo/<provider_name>", methods=["POST"])
@public_endpoint
def saml_slo_post(provider_name: str):
    """Handle SAML HTTP-POST Single Logout Service.

    Issue #2174 F7: Process LogoutRequest from IdP or return LogoutResponse.

    This endpoint handles:
    1. IdP-initiated logout requests (LogoutRequest from IdP)
    2. SP-initiated logout responses (LogoutResponse to our request)
    """
    # Parse-DoS cap for unauthenticated SAML messages
    max_saml_message = 256 * 1024
    if (request.content_length or 0) > max_saml_message:
        return jsonify({"error": "saml_message_too_large"}), 413

    saml_message = request.form.get("SAMLRequest") or request.form.get("SAMLResponse")
    relay_state = request.form.get("RelayState", "")

    if not saml_message:
        return jsonify({"error": "Missing SAMLRequest or SAMLResponse"}), 400

    provider = get_sso_manager().get_provider(provider_name)
    if not provider or provider.provider_type != "saml":
        return jsonify({"error": "SAML provider not found"}), 404

    from app.modules.sso.saml import SAMLProvider

    if not isinstance(provider, SAMLProvider):
        return jsonify({"error": "Invalid provider type"}), 400

    # Check if this is a LogoutRequest (IdP-initiated) or LogoutResponse (SP-initiated)
    try:
        import base64

        decoded = base64.b64decode(saml_message, validate=True)
        from lxml import etree

        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(decoded, parser=parser)

        # Check the root element tag
        tag_local = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        if tag_local == "LogoutRequest":
            # IdP-initiated logout request
            return _handle_saml_logout_request(provider, saml_message, relay_state)
        elif tag_local == "LogoutResponse":
            # Response to our SP-initiated logout
            return _handle_saml_logout_response(provider, saml_message, relay_state)
        else:
            return jsonify({"error": f"Unexpected SAML message type: {tag_local}"}), 400

    except Exception as e:
        logger.error(f"Failed to parse SAML SLO message: {e}")
        return jsonify({"error": "Invalid SAML message"}), 400


@sso_bp.route("/slo-redirect/<provider_name>", methods=["GET"])
@public_endpoint
def saml_slo_redirect(provider_name: str):
    """Handle SAML HTTP-Redirect Single Logout Service.

    Issue #2174 F7: Process LogoutRequest/Response via redirect binding.

    This endpoint handles both:
    1. SP-initiated logout requests (redirect user to IdP)
    2. IdP-initiated logout requests (process LogoutRequest)
    3. Logout responses from IdP (after SP-initiated logout)
    """
    saml_message = request.args.get("SAMLRequest") or request.args.get("SAMLResponse")
    relay_state = request.args.get("RelayState", "")

    provider = get_sso_manager().get_provider(provider_name)
    if not provider or provider.provider_type != "saml":
        return jsonify({"error": "SAML provider not found"}), 404

    from app.modules.sso.saml import SAMLProvider

    if not isinstance(provider, SAMLProvider):
        return jsonify({"error": "Invalid provider type"}), 400

    # If SAMLRequest parameter, this is an IdP-initiated logout request
    if request.args.get("SAMLRequest"):
        return _handle_saml_logout_request(provider, saml_message, relay_state)

    # If SAMLResponse parameter, this is a response to our logout request
    if request.args.get("SAMLResponse"):
        return _handle_saml_logout_response(provider, saml_message, relay_state)

    # Otherwise, this is a request to initiate logout
    # Get session info from cookie or token
    token = request.cookies.get("session_token") or request.args.get("session_token")
    if not token:
        return jsonify({"error": "No session to logout"}), 400

    session_data = get_sso_manager().get_sso_session(token)
    if not session_data:
        return jsonify({"error": "Session not found or expired"}), 401

    return _initiate_saml_logout(provider_name, token, session_data, relay_state)


def _initiate_saml_logout(
    provider_name: str, token: str, session_data: dict, relay_state: str | None = None
):
    """Initiate SP-initiated SAML logout.

    Issue #2174 F7: Build and send LogoutRequest to IdP.

    Args:
        provider_name: SAML provider name.
        token: Session token to logout.
        session_data: Session data containing user info.
        relay_state: Optional relay state to round-trip.
    """
    from app.modules.sso.saml import SAMLProvider

    provider = get_sso_manager().get_provider(provider_name)
    if not provider or not isinstance(provider, SAMLProvider):
        return jsonify({"error": "SAML provider not found"}), 404

    # Get NameID and SessionIndex from session data
    # These were stored during the original SSO login
    name_id = session_data.get("name_id")
    session_index = session_data.get("session_index")

    if not name_id:
        # No NameID - just delete local session
        _delete_session_and_cookies(token)
        return jsonify({"message": "Logged out successfully (no SLO)"}), 200

    try:
        logout_url, request_id = provider.build_logout_request(
            name_id=name_id,
            session_index=session_index,
            relay_state=relay_state,
        )

        # Store the request_id for validation (in production, use Redis)
        # Issue #2174 F7: Use auth_state table to store logout state
        import secrets

        state = secrets.token_urlsafe(32)
        get_sso_manager()._store_auth_state(state, request_id, provider_name, None)

        # Redirect to IdP for logout
        return redirect(logout_url)

    except ValueError as e:
        logger.warning(f"SAML logout not available: {e}")
        # No SLO endpoint - just delete local session
        _delete_session_and_cookies(token)
        return jsonify({"message": "Logged out successfully (IdP does not support SLO)"}), 200


def _handle_saml_logout_request(provider, saml_request: str, relay_state: str):
    """Handle IdP-initiated SAML logout request.

    Issue #2174 F7: Validate LogoutRequest and return LogoutResponse.

    Args:
        provider: SAML provider instance.
        saml_request: Base64-encoded LogoutRequest.
        relay_state: RelayState to include in response.
    """
    import base64
    import zlib

    from lxml import etree

    try:
        # Decode the request (may be deflated for redirect binding)
        try:
            # Try deflated first (redirect binding)
            decoded = zlib.decompress(base64.b64decode(saml_request), -15)
        except Exception:
            # Fall back to plain base64 (post binding)
            decoded = base64.b64decode(saml_request, validate=True)

        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(decoded, parser=parser)

        # Extract NameID
        name_id_elem = root.find(".//saml:NameID", namespaces=NSMAP)

        name_id = name_id_elem.text if name_id_elem is not None and name_id_elem.text else None

        if not name_id:
            return jsonify({"error": "LogoutRequest missing NameID"}), 400

        # Find and terminate the session(s) for this NameID
        # In a production system, you'd look up sessions by NameID
        # For now, we just acknowledge the logout
        logger.info(f"SAML logout request received for NameID: {name_id[:8]}...")

        # Build LogoutResponse
        response_id = provider.generate_request_id()
        now = provider._now().isoformat(timespec="seconds").replace("+00:00", "Z")
        in_response_to = root.get("ID")

        response_root = etree.Element(
            f"{{{SAML_PROTOCOL_NS}}}LogoutResponse",
            nsmap=NSMAP,
            ID=response_id,
            Version="2.0",
            IssueInstant=now,
            InResponseTo=in_response_to,
        )
        etree.SubElement(response_root, f"{{{SAML_ASSERTION_NS}}}Issuer").text = (
            provider.sp_entity_id
        )

        status = etree.SubElement(response_root, f"{{{SAML_PROTOCOL_NS}}}Status")
        etree.SubElement(status, f"{{{SAML_PROTOCOL_NS}}}StatusCode", Value=SAML_SUCCESS_STATUS)

        response_xml = etree.tostring(response_root, xml_declaration=False, encoding="UTF-8")

        # Get SLO endpoint
        slo_url = provider.idp_slo_url
        if not slo_url:
            return jsonify({"error": "IdP SLO URL not configured"}), 400

        # For POST binding, return HTML form that auto-submits
        encoded_response = base64.b64encode(response_xml).decode("ascii")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>SAML Logout</title></head>
        <body onload="document.forms[0].submit()">
            <noscript><p>Redirecting to logout...</p></noscript>
            <form method="post" action="{slo_url}">
                <input type="hidden" name="SAMLResponse" value="{encoded_response}">
                <input type="hidden" name="RelayState" value="{relay_state}">
            </form>
        </body>
        </html>
        """
        return Response(html, mimetype="text/html")

    except Exception as e:
        logger.error(f"Failed to handle SAML logout request: {e}")
        return jsonify({"error": "Failed to process logout request"}), 500


def _handle_saml_logout_response(provider, saml_response: str, relay_state: str):
    """Handle SAML logout response from IdP.

    Issue #2174 F7: Validate LogoutResponse and complete local logout.

    Args:
        provider: SAML provider instance.
        saml_response: Base64-encoded LogoutResponse.
        relay_state: RelayState (may contain redirect URL).
    """
    import base64
    import zlib

    try:
        # Decode the response (may be deflated for redirect binding)
        try:
            zlib.decompress(base64.b64decode(saml_response), -15)
        except Exception:
            base64.b64decode(saml_response, validate=True)

        # Validate the response
        success, error = provider.validate_logout_response(saml_response)

        if not success:
            logger.warning(f"SAML logout response validation failed: {error}")
            # Still proceed with local logout

        # Parse redirect URL from relay_state if available
        redirect_url = None
        if relay_state:
            _, redirect_url = _decode_state(relay_state)

        # Clear local session cookie
        response = jsonify({"message": "Logged out successfully"})
        response.delete_cookie("session_token", httponly=True, samesite="Lax")

        # Redirect to frontend if provided
        if redirect_url and _validate_redirect_uri(redirect_url):
            return redirect(f"{redirect_url}?logout=success")

        return response

    except Exception as e:
        logger.error(f"Failed to handle SAML logout response: {e}")
        return jsonify({"error": "Failed to process logout response"}), 500


def _delete_session_and_cookies(token: str):
    """Delete session from database and clear cookies.

    Issue #2174 F7: Helper to clean up local session state.
    """
    try:
        # Get session data before deletion
        session_data = get_sso_manager().get_sso_session(token)

        if session_data:
            user_id = session_data.get("user_id")

            # Delete from both tables
            with get_sso_manager().db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM sso_sessions WHERE session_token = ?",
                    (token,),
                )
                if user_id:
                    cursor.execute(
                        "DELETE FROM sessions WHERE user_id = ? AND token = ?",
                        (user_id, token),
                    )
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to delete session: {e}")


# ============================================================================
# End SAML SLO Endpoints
# ============================================================================


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
    """Logout from SSO session.

    Issue #1826 F4: Cascade delete both sso_sessions and sessions tables
    to ensure complete session cleanup and prevent session_token reuse.
    """
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    session_data = None
    user_id = None

    if token:
        # Get session data before deletion
        session_data = get_sso_manager().get_sso_session(token)

        if session_data:
            user_id = session_data.get("user_id")

            # Issue #1826 F4: Cascade delete in transaction
            # Delete from both sso_sessions and sessions tables
            try:
                with get_sso_manager().db.connection() as conn:
                    cursor = conn.cursor()

                    # Delete from sso_sessions first
                    cursor.execute(
                        "DELETE FROM sso_sessions WHERE session_token = ?",
                        (token,),
                    )

                    # Then delete from sessions table (shared session)
                    if user_id:
                        cursor.execute(
                            "DELETE FROM sessions WHERE user_id = ? AND token = ?",
                            (user_id, token),
                        )

                    conn.commit()

            except Exception as e:
                logger.error(f"Failed to cleanup SSO session: {e}")
                # Still proceed to return success - best effort cleanup
                # The session will eventually expire

    # Audit-log the SSO logout so SSO session termination is visible alongside
    # password-auth logouts.
    try:
        get_audit_logger().log(
            action=AuditAction.LOGOUT.value,
            user_id=user_id,
            resource_type="sso_session",
            resource_id=session_data.get("provider_name") if session_data else None,
            details={
                "method": "sso",
                "provider": session_data.get("provider_name") if session_data else None,
                "cascade_delete": True,  # Issue #1826 F4: Mark cascade delete
            },
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
            success=bool(session_data),
        )
    except Exception:
        logger.warning("Failed to audit-log SSO logout", exc_info=True)

    # Issue #1826 F4: Clear session_token cookie to prevent reuse
    response = jsonify({"message": "Logged out successfully"})
    response.delete_cookie("session_token", httponly=True, samesite="Lax")
    return response


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

    Issue #1826 F3: Explicit tenant_id passing with policy configuration.
    Issue #2174 F6: Fail-closed tenant resolution with priority chain.
    """
    from flask import g

    # Generate username if not provided
    username = sso_user.username or sso_user.email or f"{provider_name}_{sso_user.provider_user_id}"

    # Ensure username is unique
    base_username = username
    counter = 1
    while user_repo.get_user_by_username(username):
        username = f"{base_username}_{counter}"
        counter += 1

    # Issue #2174 F6: Tenant resolution priority chain
    # Priority 1: Provider configuration (default_tenant_id in provider config)
    # Priority 2: Request tenant context (from authenticated session)
    # Priority 3: REJECT if neither available

    provider = get_sso_manager().get_provider(provider_name)
    tenant_id = None

    # Priority 1: Try provider configuration default_tenant_id
    if provider and provider.config:
        # Try extra_params first (where default_tenant_id is typically configured)
        if provider.config.extra_params:
            tenant_id = provider.config.extra_params.get("default_tenant_id")
        # Fall back to provider's tenant_id if not in extra_params
        if not tenant_id:
            tenant_id = provider.config.tenant_id

    # Priority 2: Try request tenant context from authenticated session
    if tenant_id is None:
        request_tenant_id = getattr(g, "tenant_id", None) or getattr(g, "user", {}).get("tenant_id")
        if request_tenant_id:
            tenant_id = int(request_tenant_id)

    # Priority 3: Check policy for missing tenant_id
    if tenant_id is None:
        null_tenant_policy = os.environ.get(
            "SSO_NULL_TENANT_POLICY", "reject"
        )  # Changed default to reject

        if null_tenant_policy == "reject":
            logger.error(
                f"SSO user creation rejected - no tenant binding: "
                f"provider={provider_name}, username={username}. "
                f"Contact administrator to configure default_tenant_id for this provider."
            )
            return None
        elif null_tenant_policy == "warn":
            logger.warning(
                f"SSO user creation rejected - no tenant binding (policy=warn): "
                f"provider={provider_name}, username={username}. "
                f"DEPRECATION NOTICE: SSO_NULL_TENANT_POLICY=warn will reject user creation. "
                f"Please migrate to 'reject' or configure provider default_tenant_id."
            )
            return None  # Issue #1826 F6: Reject creation instead of falling back to tenant 1
        # "allow" policy: silent allow (for admin accounts with global scope)
        # tenant_id remains None

    # Create user
    try:
        user_id = user_repo.create_user(
            username=username,
            email=sso_user.email or "",
            password_hash="",  # No password for SSO users
            role="user",
            tenant_id=tenant_id,  # Issue #1826 F6: Pass None directly for allow policy
        )

        if user_id:
            logger.info(
                f"Created user {username} from SSO provider {provider_name} with tenant_id={tenant_id}"
            )

        return user_id

    except Exception as e:
        logger.error(f"Failed to create user from SSO: {e}")
        return None


def register_sso_routes(app):
    """Register SSO routes with the Flask app."""
    app.register_blueprint(sso_bp)
