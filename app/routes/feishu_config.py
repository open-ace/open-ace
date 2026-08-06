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
        service = get_feishu_config_service()
        config = service.get_config()

        if not config:
            return jsonify(
                {"success": True, "data": None, "message": "Feishu configuration not set"}
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

        if not app_id or not app_secret:
            return (
                jsonify({"success": False, "error": "Missing required fields: app_id, app_secret"}),
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

        service = get_feishu_config_service()

        try:
            config = service.save_config(
                app_id=app_id,
                app_secret=app_secret,
                org_sync_enabled=bool(org_sync_enabled),
                org_sync_tenant_id=int(org_sync_tenant_id) if org_sync_tenant_id else None,
                org_sync_interval_minutes=int(org_sync_interval_minutes),
                org_sync_max_runtime_seconds=int(org_sync_max_runtime_seconds),
                org_sync_auto_recover=bool(org_sync_auto_recover),
            )

            logger.info(f"Feishu configuration updated by user {user_id}")

            # Audit log for Feishu config save (do not log app_secret)
            audit_logger.log_action(
                action=AuditAction.FEISHU_CONFIG_SAVE.value,
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
    """Test Feishu connection."""
    try:
        data = request.get_json() or {}

        service = get_feishu_config_service()

        # Test with provided parameters or saved config
        result = service.test_connection(
            app_id=data.get("app_id"),
            app_secret=data.get("app_secret"),
        )

        if result["success"]:
            logger.info("Feishu connection test successful")
        else:
            logger.error(f"Feishu connection test failed: {result['message']}")

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error testing Feishu connection: {e}")
        return jsonify({"success": False, "error": "Internal server error", "message": str(e)}), 500


@feishu_config_bp.route("/management/feishu-config", methods=["DELETE"])
def delete_feishu_config():
    """Delete Feishu configuration."""
    try:
        service = get_feishu_config_service()

        # Get config before deleting for audit log
        config = service.get_config()
        if not config:
            return (
                jsonify({"success": False, "error": "No Feishu configuration to delete"}),
                404,
            )

        success = service.delete_config()

        if not success:
            return jsonify({"success": False, "error": "Failed to delete configuration"}), 500

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        logger.info(f"Feishu configuration deleted by user {user_id}")

        # Audit log for Feishu config delete (do not log app_secret)
        audit_logger.log_action(
            action=AuditAction.FEISHU_CONFIG_DELETE.value,
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