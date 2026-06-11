"""
Open ACE - SMTP Configuration API Routes

REST API endpoints for SMTP configuration management:
- Get/Update SMTP configuration
- Test SMTP connection
- View email sending statistics

Admin-only access.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.services.email_notification_service import get_email_notification_service
from app.services.smtp_config_service import get_smtp_config_service

logger = logging.getLogger(__name__)

smtp_config_bp = Blueprint("smtp_config", __name__)


@smtp_config_bp.before_request
@admin_required
def check_admin():
    """Ensure user is admin before each request."""
    pass


# ==================== SMTP Configuration API ====================


@smtp_config_bp.route("/management/smtp-config", methods=["GET"])
def get_smtp_config():
    """Get SMTP configuration."""
    try:
        service = get_smtp_config_service()
        config = service.get_config()

        if not config:
            return jsonify({
                "success": True,
                "data": None,
                "message": "SMTP configuration not set"
            })

        return jsonify({
            "success": True,
            "data": config
        })
    except Exception as e:
        logger.error(f"Error getting SMTP config: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500


@smtp_config_bp.route("/management/smtp-config", methods=["PUT"])
def update_smtp_config():
    """Update SMTP configuration."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400

        # Required fields
        smtp_host = data.get("smtp_host")
        smtp_port = data.get("smtp_port")
        from_address = data.get("from_address")

        if not smtp_host or not smtp_port or not from_address:
            return jsonify({
                "success": False,
                "error": "Missing required fields: smtp_host, smtp_port, from_address"
            }), 400

        # Optional fields
        smtp_user = data.get("smtp_user")
        smtp_password = data.get("smtp_password")
        use_tls = data.get("use_tls", True)

        # Get current user
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        service = get_smtp_config_service()
        config = service.save_config(
            smtp_host=smtp_host,
            smtp_port=int(smtp_port),
            from_address=from_address,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            use_tls=use_tls,
            created_by=user_id,
        )

        logger.info(f"SMTP configuration updated by user {user_id}")

        return jsonify({
            "success": True,
            "data": config,
            "message": "SMTP configuration saved. Please test connection before enabling email notifications."
        })
    except Exception as e:
        logger.error(f"Error updating SMTP config: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500


@smtp_config_bp.route("/management/smtp-config/test", methods=["POST"])
def test_smtp_connection():
    """Test SMTP connection."""
    try:
        data = request.get_json() or {}

        service = get_smtp_config_service()

        # Test with provided parameters or saved config
        result = service.test_connection(
            smtp_host=data.get("smtp_host"),
            smtp_port=data.get("smtp_port"),
            smtp_user=data.get("smtp_user"),
            smtp_password=data.get("smtp_password"),
            from_address=data.get("from_address"),
            use_tls=data.get("use_tls"),
        )

        if result["success"]:
            logger.info("SMTP connection test successful")
        else:
            logger.error(f"SMTP connection test failed: {result['message']}")

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error testing SMTP connection: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error",
            "message": str(e)
        }), 500


@smtp_config_bp.route("/management/smtp-config", methods=["DELETE"])
def delete_smtp_config():
    """Delete SMTP configuration."""
    try:
        service = get_smtp_config_service()
        success = service.delete_config()

        if not success:
            return jsonify({
                "success": False,
                "error": "No SMTP configuration to delete"
            }), 404

        logger.info(f"SMTP configuration deleted by user {g.user.get('id')}")

        return jsonify({
            "success": True,
            "message": "SMTP configuration deleted"
        })
    except Exception as e:
        logger.error(f"Error deleting SMTP config: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500


@smtp_config_bp.route("/management/smtp-config/statistics", methods=["GET"])
def get_email_statistics():
    """Get email sending statistics."""
    try:
        days = int(request.args.get("days", 7))
        service = get_smtp_config_service()
        stats = service.get_statistics(days)

        return jsonify({
            "success": True,
            "data": stats
        })
    except Exception as e:
        logger.error(f"Error getting email statistics: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500


@smtp_config_bp.route("/management/smtp-config/send-test", methods=["POST"])
def send_test_email():
    """Send a test email notification."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400

        recipient_email = data.get("recipient_email")
        if not recipient_email:
            return jsonify({
                "success": False,
                "error": "Missing recipient_email"
            }), 400

        language = data.get("language", "en")

        service = get_email_notification_service()
        result = service.send_test_email(recipient_email, language)

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error sending test email: {e}")
        return jsonify({
            "success": False,
            "error": "Internal server error",
            "message": str(e)
        }), 500
