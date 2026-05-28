"""
Open ACE - Auth Routes

API routes for authentication operations.
"""

import logging
import os
import uuid
from typing import Optional, cast

import bcrypt
import filetype
from flask import Blueprint, jsonify, make_response, request

from app.auth.decorators import public_endpoint
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)
auth_service = AuthService()
user_repo = UserRepository()


def _validate_avatar_url(user_id: int, avatar_url: Optional[str]) -> Optional[str]:
    """Return the avatar URL only if the file exists on disk.

    This is a read-only check — it does NOT mutate the database.
    DB cleanup only happens in explicit write paths
    (upload/delete avatar endpoints) so that multi-instance or
    rolling-deploy scenarios are not affected by transient file
    unavailability on a single node.
    """
    if not avatar_url:
        return None

    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
    filepath = os.path.join(static_dir, avatar_url.removeprefix("/static/"))

    if os.path.exists(filepath):
        return avatar_url

    logger.warning(f"Avatar file missing for user {user_id}: {filepath}")
    return None


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return cast("bool", bcrypt.checkpw(password.encode(), password_hash.encode()))
    except Exception:
        return False


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


@auth_bp.route("/auth/login", methods=["POST"])
def api_login():
    """Login endpoint."""
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    user, token_or_error = auth_service.login(username, password, verify_password)

    if user:
        from app.services.auth_service import _get_session_timeout_hours

        # Validate avatar file exists
        user["avatar_url"] = _validate_avatar_url(user["id"], user.get("avatar_url"))

        timeout_seconds = int(_get_session_timeout_hours() * 3600)
        response = make_response(jsonify({"success": True, "user": user}))
        response.set_cookie(
            "session_token",
            token_or_error,
            httponly=True,
            secure=request.is_secure,  # Auto-set based on HTTPS
            samesite="Lax",
            max_age=timeout_seconds,
        )

        # Pre-start webui instance for user (in multi-user mode)
        try:
            from app.services.webui_manager import get_webui_manager

            manager = get_webui_manager()
            if manager.config.enabled and manager.config.multi_user_mode:
                user_id = int(user.get("id", 0))
                # Get user's system_account
                user_data = user_repo.get_user_by_id(user_id)
                system_account = (
                    user_data.get("system_account") or user_data.get("username")
                    if user_data
                    else None
                )
                if user_id and system_account:
                    logger.info(
                        f"Pre-starting webui for user {user_id} ({system_account}) on login"
                    )
                    manager.prestart_user_instance_async(user_id, system_account)
        except Exception as e:
            logger.warning(f"Failed to pre-start webui on login: {e}")

        return response

    return jsonify({"error": token_or_error}), 401


@auth_bp.route("/auth/logout", methods=["POST"])
@public_endpoint
def api_logout():
    """Logout endpoint."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if token:
        auth_service.logout(token)

    response = make_response(jsonify({"success": True}))
    response.delete_cookie("session_token")
    return response


@auth_bp.route("/auth/profile", methods=["GET"])
def api_profile():
    """Get current user profile."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    if session_or_error is None:
        return jsonify({"error": "Invalid session"}), 401

    user_id = int(session_or_error.get("user_id", 0))
    profile = auth_service.get_user_profile(user_id)

    if profile:
        profile["avatar_url"] = _validate_avatar_url(user_id, profile.get("avatar_url"))
        return jsonify(profile)

    return jsonify({"error": "User not found"}), 404


# Session refresh threshold (only refresh when less than 10 minutes remaining)
_AUTH_SESSION_REFRESH_THRESHOLD_MINUTES = 10


def _refresh_auth_session(token: str) -> Optional[int]:
    """Extend session expiry when close to expiration.

    Returns new timeout seconds if session was refreshed, None otherwise.
    """
    from datetime import datetime, timedelta, timezone

    from app.services.auth_service import _get_session_timeout_hours

    try:
        # Get current expires_at
        row = user_repo.db.fetch_one("SELECT expires_at FROM sessions WHERE token = ?", (token,))
        if not row or not row.get("expires_at"):
            logger.debug(f"No session row found for token: {token[:16]}...")
            return None

        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at).replace(tzinfo=None)

        remaining = (expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
        threshold = timedelta(minutes=_AUTH_SESSION_REFRESH_THRESHOLD_MINUTES).total_seconds()
        logger.debug(
            f"Session refresh check: remaining={remaining/60:.1f}min, threshold={threshold/60:.1f}min"
        )
        if remaining > threshold:
            logger.debug(
                f"Session not refreshed: remaining ({remaining/60:.1f}min) > threshold ({threshold/60:.1f}min)"
            )
            return None

        # Refresh session
        timeout_hours = _get_session_timeout_hours()
        new_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            hours=timeout_hours
        )
        user_repo.extend_session_expiry(token, new_expires_at)
        logger.info(f"Session refreshed in auth check, new expiry: {new_expires_at}")
        return int(timeout_hours * 3600)
    except Exception as e:
        logger.warning(f"Failed to refresh session in auth check: {e}")
        return None


@auth_bp.route("/auth/check", methods=["GET"])
def api_auth_check():
    """Check if user is authenticated and extend session if needed."""
    from flask import make_response

    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if not token:
        return jsonify({"authenticated": False})

    session = auth_service.get_session(token)
    if not session:
        return jsonify({"authenticated": False})

    # Check if session needs refresh (sliding expiration)
    new_timeout_seconds = _refresh_auth_session(token)

    user_id = int(session.get("user_id", 0))
    user_data = user_repo.get_user_by_id(user_id)

    avatar_url = user_data.get("avatar_url") if user_data else None
    avatar_url = _validate_avatar_url(user_id, avatar_url)

    response_data = {
        "authenticated": True,
        "user": {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "email": session.get("email"),
            "role": session.get("role"),
            "avatar_url": avatar_url,
        },
    }

    response = make_response(jsonify(response_data))

    # Update cookie max_age if session was refreshed
    if new_timeout_seconds and request.cookies.get("session_token"):
        response.set_cookie(
            "session_token",
            request.cookies["session_token"],
            httponly=True,
            secure=request.is_secure,
            samesite="Lax",
            max_age=new_timeout_seconds,
        )

    return response


@auth_bp.route("/auth/me", methods=["GET"])
def api_current_user():
    """Get current user info (alias for /auth/profile)."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    if session_or_error is None:
        return jsonify({"error": "Invalid session"}), 401

    user_id = int(session_or_error.get("user_id", 0))
    profile = auth_service.get_user_profile(user_id)

    if profile:
        profile["avatar_url"] = _validate_avatar_url(user_id, profile.get("avatar_url"))
        return jsonify({"user": profile})

    return jsonify({"error": "User not found"}), 404


@auth_bp.route("/auth/change-password", methods=["POST"])
def api_change_password():
    """Change password endpoint."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    if session_or_error is None:
        return jsonify({"error": "Invalid session"}), 401

    data = request.get_json() or {}
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not current_password or not new_password:
        return jsonify({"error": "Current password and new password required"}), 400

    user_id = int(session_or_error.get("user_id", 0))

    success, error = auth_service.change_password(
        user_id, current_password, new_password, verify_password, hash_password
    )

    if success:
        return jsonify({"success": True, "message": "Password changed successfully"})

    return jsonify({"error": error}), 400


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@auth_bp.route("/user/avatar", methods=["POST"])
def api_upload_avatar():
    """Upload user avatar."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    if session_or_error is None:
        return jsonify({"error": "Invalid session"}), 401

    user_id = int(session_or_error.get("user_id", 0))

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename is None or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Allowed: jpg, jpeg, png, gif, webp"}), 400

    # Check MIME type
    if not file.content_type or file.content_type not in ALLOWED_MIME_TYPES:
        return jsonify({"error": "Invalid content type"}), 400

    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({"error": "File too large. Maximum size: 2MB"}), 400

    # Generate unique filename
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"

    # Save to static/avatars/
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
    avatars_dir = os.path.join(static_dir, "avatars")
    os.makedirs(avatars_dir, exist_ok=True)

    # Delete old avatar file if exists
    profile = auth_service.get_user_profile(user_id)
    if profile and profile.get("avatar_url"):
        old_filepath = os.path.join(static_dir, profile["avatar_url"].removeprefix("/static/"))
        try:
            if os.path.exists(old_filepath):
                os.remove(old_filepath)
        except OSError:
            pass

    filepath = os.path.join(avatars_dir, filename)
    file.save(filepath)

    # Verify actual image content (not just extension)
    kind = filetype.guess(filepath)
    if not kind or kind.mime not in ALLOWED_MIME_TYPES:
        try:
            os.remove(filepath)
        except OSError:
            pass
        return jsonify({"error": "Invalid image content"}), 400

    # Update user avatar_url in database
    avatar_url = f"/static/avatars/{filename}"
    success = user_repo.update_avatar(user_id, avatar_url)

    if success:
        return jsonify({"success": True, "avatar_url": avatar_url})

    # Clean up file if database update failed
    try:
        os.remove(filepath)
    except OSError:
        pass

    return jsonify({"error": "Failed to update avatar"}), 500


@auth_bp.route("/user/avatar", methods=["DELETE"])
def api_delete_avatar():
    """Delete user avatar."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    if session_or_error is None:
        return jsonify({"error": "Invalid session"}), 401

    user_id = int(session_or_error.get("user_id", 0))

    # Get current avatar URL to delete file
    profile = auth_service.get_user_profile(user_id)
    if profile and profile.get("avatar_url"):
        static_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static"
        )
        old_filepath = os.path.join(static_dir, profile["avatar_url"].removeprefix("/static/"))
        try:
            if os.path.exists(old_filepath):
                os.remove(old_filepath)
        except OSError:
            pass

    # Update database
    success = user_repo.update_avatar(user_id, None)

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete avatar"}), 500
