"""
Open ACE - Feishu Configuration API Routes

REST API endpoints for Feishu configuration management:
- Get/Update/Delete Feishu configuration
- Test Feishu connection

Admin-only access.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.notification_settings_repository import get_notification_settings_repository
from app.services.feishu_config_service import get_feishu_config_service

logger = logging.getLogger(__name__)
audit_logger = AuditLogger()

feishu_config_bp = Blueprint("feishu_config", __name__)


@feishu_config_bp.before_request
@admin_required
def check_admin():
    """Ensure user is admin before each request."""
    pass


# ==================== Feishu Configuration API ====================


@feishu_config_bp.route("/management/feishu-config", methods=["GET"])
def get_feishu_config():
    """Get Feishu configuration."""
    try:
        config = get_notification_settings_repository().get("feishu")

        if not config:
            return jsonify(
                {"success": True, "data": None, "message": "Feishu configuration not set"}
            )

        config.update(
            {
                "org_sync_enabled": config.pop("sync_enabled"),
                "org_sync_tenant_id": config.pop("target_tenant_id"),
                "org_sync_interval_minutes": config.pop("interval_minutes"),
                "org_sync_max_runtime_seconds": config.pop("max_runtime_seconds"),
                "org_sync_auto_recover": config.pop("auto_recovery"),
                "app_secret_masked": "****" if config.pop("app_secret_configured", False) else "",
            }
        )
        return jsonify({"success": True, "data": config})
    except Exception as e:
        logger.error(f"Error getting Feishu config: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@feishu_config_bp.route("/management/feishu-config", methods=["PUT"])
def update_feishu_config():
    """Update Feishu configuration."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Required fields
        app_id = data.get("app_id")
        app_secret = data.get("app_secret")

        if not app_id:
            return (
                jsonify({"success": False, "error": "Missing required field: app_id"}),
                400,
            )

        # Optional fields
        org_sync_enabled = data.get("org_sync_enabled", False)
        org_sync_tenant_id = data.get("org_sync_tenant_id")
        org_sync_interval_minutes = data.get("org_sync_interval_minutes", 60)
        org_sync_max_runtime_seconds = data.get("org_sync_max_runtime_seconds", 1800)
        org_sync_auto_recover = data.get("org_sync_auto_recover", False)

        # Get current user
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        try:
            repo = get_notification_settings_repository()
            current = repo.get("feishu")
            if not app_secret and not current:
                raise ValueError("app_secret is required")
            config = repo.save(
                "feishu",
                {
                    "app_id": app_id,
                    **({"app_secret": app_secret} if app_secret is not None else {}),
                    "sync_enabled": bool(org_sync_enabled),
                    "target_tenant_id": int(org_sync_tenant_id) if org_sync_tenant_id else None,
                    "interval_minutes": int(org_sync_interval_minutes),
                    "max_runtime_seconds": int(org_sync_max_runtime_seconds),
                    "auto_recovery": bool(org_sync_auto_recover),
                },
                user_id,
            )
            config.update(
                {
                    "org_sync_enabled": config.pop("sync_enabled"),
                    "org_sync_tenant_id": config.pop("target_tenant_id"),
                    "org_sync_interval_minutes": config.pop("interval_minutes"),
                    "org_sync_max_runtime_seconds": config.pop("max_runtime_seconds"),
                    "org_sync_auto_recover": config.pop("auto_recovery"),
                    "app_secret_masked": (
                        "****" if config.pop("app_secret_configured", False) else ""
                    ),
                }
            )

            logger.info(f"Feishu configuration updated by user {user_id}")

            # Audit log for Feishu config save (do not log app_secret)
            audit_logger.log_action(
                action=AuditAction.FEISHU_CONFIG_SAVE,
                user_id=user_id,
                resource_type="feishu_config",
                resource_name=app_id,
                details={
                    "app_id": app_id,
                    "org_sync_enabled": bool(org_sync_enabled),
                    "org_sync_tenant_id": org_sync_tenant_id,
                    "org_sync_interval_minutes": int(org_sync_interval_minutes),
                    "app_secret_updated": bool(app_secret),
                },
            )

            return jsonify(
                {
                    "success": True,
                    "data": config,
                    "message": "Feishu configuration saved. Please test connection before enabling org sync.",
                }
            )

        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except RuntimeError as e:
            logger.error(f"Failed to save Feishu config: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    except Exception as e:
        logger.error(f"Error updating Feishu config: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@feishu_config_bp.route("/management/feishu-config/test", methods=["POST"])
def test_feishu_connection():
    """Test Feishu connection and persist verification status."""
    try:
        data = request.get_json() or {}

        # Test with provided parameters or saved config
        repo = get_notification_settings_repository()
        saved = repo.get("feishu", include_secrets=True) or {}
        service = get_feishu_config_service()
        result = service.test_connection(
            app_id=data.get("app_id") or saved.get("app_id"),
            app_secret=data.get("app_secret") or saved.get("app_secret"),
        )

        # Persist verification status if config exists in database
        if saved and saved.get("app_id"):
            if result["success"]:
                repo.update_verification_status("feishu", "connected")
                logger.info("Feishu connection test successful, status persisted")
            else:
                # Determine error code
                error_code = _classify_feishu_error(result.get("message", ""))
                repo.update_verification_status(
                    "feishu",
                    "connection_failed",
                    error_code=error_code,
                    error_summary=result.get("message", "")[:200],  # Truncate for safety
                )
                logger.error(f"Feishu connection test failed: {result['message']}")
        else:
            if result["success"]:
                logger.info("Feishu connection test successful (no saved config)")
            else:
                logger.error(f"Feishu connection test failed: {result['message']}")

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error testing Feishu connection: {e}")
        return jsonify({"success": False, "error": "Internal server error", "message": str(e)}), 500


def _classify_feishu_error(message: str) -> str:
    """Classify Feishu error message into a stable error code."""
    message_lower = message.lower()
    if "blocked by security policy" in message_lower:
        return "FEISHU_REQUEST_BLOCKED"
    if "app_id" in message_lower or "app_secret" in message_lower:
        return "FEISHU_INVALID_CREDENTIALS"
    if "placeholder" in message_lower:
        return "FEISHU_PLACEHOLDER_VALUE"
    if "timeout" in message_lower or "network" in message_lower:
        return "FEISHU_NETWORK_ERROR"
    if "code=" in message_lower:
        # Extract API error code
        import re

        match = re.search(r"code=(\d+)", message)
        if match:
            return f"FEISHU_API_ERROR_{match.group(1)}"
    return "FEISHU_UNKNOWN_ERROR"


@feishu_config_bp.route("/management/feishu-config", methods=["DELETE"])
def delete_feishu_config():
    """Delete Feishu configuration."""
    try:
        # Get config before deleting for audit log
        repo = get_notification_settings_repository()
        config = repo.get("feishu")
        if not config:
            return (
                jsonify({"success": False, "error": "No Feishu configuration to delete"}),
                404,
            )

        success = repo.delete("feishu")

        if not success:
            return jsonify({"success": False, "error": "Failed to delete configuration"}), 500

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        logger.info(f"Feishu configuration deleted by user {user_id}")

        # Audit log for Feishu config delete (do not log app_secret)
        audit_logger.log_action(
            action=AuditAction.FEISHU_CONFIG_DELETE,
            user_id=user_id,
            resource_type="feishu_config",
            resource_name=config.get("app_id"),
            details={
                "app_id": config.get("app_id"),
                "org_sync_enabled": config.get("org_sync_enabled"),
                "org_sync_tenant_id": config.get("org_sync_tenant_id"),
            },
        )

        return jsonify({"success": True, "message": "Feishu configuration deleted"})
    except Exception as e:
        logger.error(f"Error deleting Feishu config: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
