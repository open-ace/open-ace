"""
Open ACE - SSO Routes

API endpoints for Single Sign-On authentication.
"""

import logging
from datetime import datetime
from typing import Optional

from flask import Blueprint, g, jsonify, redirect, request, url_for

from app.auth.decorators import admin_required, auth_required, public_endpoint
from app.modules.sso.manager import SSOManager
from app.modules.sso.provider import list_providers
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Create blueprint
sso_bp = Blueprint("sso", __name__, url_prefix="/api/sso")

# Services
_sso_manager = None


def get_sso_manager():
    global _sso_manager
    if _sso_manager is None:
        _sso_manager = SSOManager()
    return _sso_manager


user_repo = UserRepository()


@sso_bp.route("/providers", methods=["GET"])
@public_endpoint
def list_sso_providers():
    """List available SSO providers."""
    tenant_id = request.args.get("tenant_id", type=int)

    providers = get_sso_manager().list_providers(tenant_id=tenant_id)

    # Also include predefined providers
    predefined = list_providers()

    return jsonify(
        {
            "registered": providers,
            "predefined": predefined,
        }
    )


@sso_bp.route("/providers", methods=["POST"])
@admin_required
def register_provider():
    """Register a new SSO provider (admin only)."""
    # Check admin auth

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    provider_name = data.get("name") or data.get("provider_name")
    if not provider_name:
        return jsonify({"error": "Provider name is required"}), 400

    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    redirect_uri = data.get("redirect_uri")

    if not client_id or not client_secret:
        return jsonify({"error": "client_id and client_secret are required"}), 400

    # Check if it's a predefined provider
    if data.get("predefined"):
        success = get_sso_manager().register_predefined_provider(
            provider_name=provider_name,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            tenant_id=data.get("tenant_id"),
            extra_params=data.get("extra_params"),
        )
    else:
        # Custom provider
        success = get_sso_manager().register_provider(
            name=provider_name,
            provider_type=data.get("provider_type", "oauth2"),
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=data.get("authorization_url", ""),
            token_url=data.get("token_url", ""),
            userinfo_url=data.get("userinfo_url"),
            redirect_uri=redirect_uri,
            scope=data.get("scope"),
            issuer_url=data.get("issuer_url"),
            tenant_id=data.get("tenant_id"),
            extra_params=data.get("extra_params"),
        )

    if success:
        return jsonify({"message": f"Provider {provider_name} registered successfully"}), 201
    else:
        return jsonify({"error": "Failed to register provider"}), 500


@sso_bp.route("/providers/<provider_name>", methods=["DELETE"])
@admin_required
def disable_provider(provider_name: str):
    """Disable an SSO provider (admin only)."""

    success = get_sso_manager().disable_provider(provider_name)

    if success:
        return jsonify({"message": f"Provider {provider_name} disabled"})
    else:
        return jsonify({"error": "Failed to disable provider"}), 500


@sso_bp.route("/login/<provider_name>", methods=["GET"])
@public_endpoint
def start_login(provider_name: str):
    """
    Start SSO login flow.

    Returns the authorization URL to redirect the user.
    """
    # Get redirect URI from query params or use default
    redirect_uri = request.args.get("redirect_uri")

    if not redirect_uri:
        # Build default callback URL
        redirect_uri = url_for("sso.callback", provider_name=provider_name, _external=True)

    result = get_sso_manager().start_authentication(provider_name, redirect_uri)

    if not result:
        return jsonify({"error": f"Failed to start authentication for {provider_name}"}), 500

    # For API clients, return the URL
    if request.args.get("json") or request.headers.get("Accept") == "application/json":
        return jsonify(result)

    # For browsers, redirect directly
    return redirect(result["authorization_url"])


@sso_bp.route("/callback/<provider_name>", methods=["GET"])
def callback(provider_name: str):
    """
    Handle SSO callback.

    This endpoint receives the authorization code from the provider.
    """
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    # Handle error from provider
    if error:
        logger.error(f"SSO error from {provider_name}: {error} - {error_description}")
        return (
            jsonify(
                {
                    "error": error,
                    "error_description": error_description,
                }
            ),
            400,
        )

    if not code or not state:
        return jsonify({"error": "Missing code or state"}), 400

    # Get redirect URI (should match what was used in start_login)
    redirect_uri = request.args.get("redirect_uri") or url_for(
        "sso.callback", provider_name=provider_name, _external=True
    )

    # Complete authentication
    auth_result = get_sso_manager().complete_authentication(
        provider_name=provider_name,
        code=code,
        state=state,
        redirect_uri=redirect_uri,
    )

    if not auth_result.success:
        return (
            jsonify(
                {
                    "error": auth_result.error,
                    "error_description": auth_result.error_description,
                }
            ),
            400,
        )

    # Get or create local user
    user_id = None
    if auth_result.user:
        user_id = get_sso_manager().get_user_by_sso_identity(
            provider_name,
            auth_result.user.provider_user_id,
        )

        if not user_id:
            # Try to find user by email
            if auth_result.user.email:
                existing_user = user_repo.get_user_by_email(auth_result.user.email)
                if existing_user:
                    user_id = existing_user.get("id")

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

        # Also create local session
        UserRepository().create_session(
            user_id=user_id,
            token=session_token,
            expires_at=datetime.utcnow(),
        )

    # Return result
    return jsonify(
        {
            "success": True,
            "user": auth_result.user.to_dict() if auth_result.user else None,
            "session_token": session_token,
        }
    )


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

    if token:
        get_sso_manager().delete_sso_session(token)

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


def _create_user_from_sso(sso_user, provider_name: str) -> Optional[int]:
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
