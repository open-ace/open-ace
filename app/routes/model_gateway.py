"""
Open ACE - Model Gateway Configuration API Routes

REST API endpoints for the optional LiteLLM-compatible model gateway:
- Get / save / delete gateway configuration
- Test gateway connection

Admin-only access. This file is the admin route registration for the removable
model_gateway module; deleting it (plus unregistering the blueprint) is part of
the feature's removal checklist.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.workspace.model_gateway.service import get_gateway_service

logger = logging.getLogger(__name__)

model_gateway_bp = Blueprint("model_gateway", __name__)


@model_gateway_bp.before_request
@admin_required
def check_admin():
    """Ensure user is admin before each request."""
    pass


# ==================== Model Gateway Configuration API ====================


@model_gateway_bp.route("/management/model-gateway-config", methods=["GET"])
def get_model_gateway_config():
    """Get the model gateway configuration (API key masked)."""
    try:
        config = get_gateway_service().get_config()
        if not config:
            return jsonify(
                {"success": True, "data": None, "message": "Model gateway not configured"}
            )
        return jsonify({"success": True, "data": config})
    except Exception as e:
        logger.error("Error getting model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config", methods=["PUT"])
def update_model_gateway_config():
    """Save (replace) the model gateway configuration."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        base_url = data.get("base_url")
        api_key = data.get("api_key")
        if not base_url:
            return jsonify({"success": False, "error": "Missing required field: base_url"}), 400

        model_prefix_mode = bool(data.get("model_prefix_mode", False))
        model_prefix = data.get("model_prefix") or None
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        config = get_gateway_service().save_config(
            base_url=base_url,
            api_key=api_key,
            model_prefix_mode=model_prefix_mode,
            model_prefix=model_prefix,
            created_by=user_id,
        )
        logger.info("Model gateway configuration updated by user %s", user_id)
        return jsonify(
            {
                "success": True,
                "data": config,
                "message": "Model gateway configuration saved.",
            }
        )
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Error updating model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config", methods=["DELETE"])
def delete_model_gateway_config():
    """Delete the model gateway configuration."""
    try:
        deleted = get_gateway_service().delete_config()
        return jsonify(
            {
                "success": True,
                "deleted": deleted,
                "message": (
                    "Model gateway configuration deleted." if deleted else "No config to delete."
                ),
            }
        )
    except Exception as e:
        logger.error("Error deleting model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config/test", methods=["POST"])
def test_model_gateway_connection():
    """Test the gateway connection with supplied (or stored) credentials."""
    try:
        data = request.get_json() or {}
        base_url = data.get("base_url")
        api_key = data.get("api_key")

        # Fall back to stored config when the caller omits credentials.
        if not base_url or not api_key:
            stored = get_gateway_service().get_config_with_key()
            if stored is not None:
                base_url = base_url or stored.base_url
                api_key = api_key or stored.api_key

        result = get_gateway_service().test_connection(
            base_url=base_url or "", api_key=api_key or ""
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error("Error testing model gateway connection: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500
