"""
Tenant Check Middleware for Issue #2163

Checks tenant_version mismatch between session and user to detect
tenant migration and invalidate sessions accordingly.
"""

import logging

from flask import g, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class TenantMigratedError(HTTPException):
    """Exception raised when user's tenant has been migrated."""

    code = 401
    description = "Tenant migrated"

    def __init__(self, old_tenant_id: int | None = None, new_tenant_id: int | None = None):
        super().__init__()
        self.old_tenant_id = old_tenant_id
        self.new_tenant_id = new_tenant_id


class SessionExpiredError(HTTPException):
    """Exception raised when session has expired."""

    code = 401
    description = "Session expired"


def check_tenant_version():
    """
    Middleware to check if session tenant_version matches user tenant_version.

    This should be registered as a before_request handler.
    It must run after authentication middleware.
    """
    # Skip if no user context
    user = getattr(g, "user", None)
    if not user:
        return

    # Skip if no session context
    session = getattr(g, "session", None)
    if not session:
        return

    user_tenant_version = user.get("tenant_version", 1)
    session_tenant_version = session.get("tenant_version", 1)

    # Check for tenant migration
    if session_tenant_version != user_tenant_version:
        logger.info(
            f"Tenant version mismatch detected: "
            f"user_version={user_tenant_version}, session_version={session_tenant_version}, "
            f"user_id={user.get('id')}"
        )

        # Raise tenant migrated error with custom response
        raise TenantMigratedError(
            old_tenant_id=session.get("tenant_id"),
            new_tenant_id=user.get("tenant_id")
        )


def handle_tenant_migrated_error(error: TenantMigratedError):
    """Handle TenantMigratedError with custom JSON response."""
    response_data = {
        "error": "TENANT_MIGRATED",
        "code": "AUTH_002",
        "message": "Your account has been migrated to a new tenant. Please re-login.",
        "message_zh": "您的账户已迁移到新租户，请重新登录。",
        "message_ja": "アカウントが新しいテナントに移行されました。再度ログインしてください。",
        "message_ko": "계정이 새 테넌트로 이관되었습니다. 다시 로그인해주세요.",
    }

    if error.new_tenant_id:
        response_data["new_tenant_id"] = error.new_tenant_id

    return jsonify(response_data), 401


def handle_session_expired_error(error: SessionExpiredError):
    """Handle SessionExpiredError with custom JSON response."""
    response_data = {
        "error": "SESSION_EXPIRED",
        "code": "AUTH_001",
        "message": "Your session has expired. Please log in again.",
        "message_zh": "会话已过期，请重新登录。",
        "message_ja": "セッションが期限切れです。再度ログインしてください。",
        "message_ko": "세션이 만료되었습니다. 다시 로그인해주세요.",
    }

    return jsonify(response_data), 401


def init_tenant_check_middleware(app):
    """
    Initialize tenant check middleware.

    Args:
        app: Flask application instance
    """
    # Register before_request handler globally
    app.before_request(check_tenant_version)

    # Register error handlers
    app.register_error_handler(TenantMigratedError, handle_tenant_migrated_error)
    app.register_error_handler(SessionExpiredError, handle_session_expired_error)

    logger.info("Tenant check middleware initialized")
