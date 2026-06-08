"""
Open ACE - AI Agent Settings Routes

API routes for managing AI agent configuration:
- GitHub account used by autonomous workflows
- Token validation
"""

import logging
import os
import subprocess

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

logger = logging.getLogger(__name__)

ai_agent_settings_bp = Blueprint("ai_agent_settings", __name__)

repo = AiAgentSettingsRepo()
audit_logger = AuditLogger()


def _get_client_info():
    """Extract client IP and user agent from request."""
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }


@ai_agent_settings_bp.route("/ai-agent/settings", methods=["GET"])
@admin_required
def api_get_ai_agent_settings():
    """Get AI agent settings (token masked)."""
    settings = repo.get_ai_agent_settings(mask_token=True)
    return jsonify(settings)


@ai_agent_settings_bp.route("/ai-agent/settings", methods=["PUT"])
@admin_required
def api_update_ai_agent_settings():
    """Update AI agent settings."""
    data = request.get_json(silent=True) or {}

    # Only allow updating known keys
    allowed_keys = {"ai_github_token", "ai_github_author_name", "ai_github_author_email"}
    filtered = {k: v for k, v in data.items() if k in allowed_keys}

    if not filtered:
        return jsonify({"error": "No valid settings provided"}), 400

    # If token value is a masked value (contains ****), don't overwrite
    token = filtered.get("ai_github_token", "")
    if "****" in token:
        # Client sent back the masked value — skip token update
        filtered.pop("ai_github_token", None)
        if not filtered:
            return jsonify({"success": True, "message": "No changes to apply"})

    success = repo.update_ai_agent_settings(filtered)

    if success:
        client_info = _get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="ai_agent_settings",
            details={"action": "update", "keys": list(filtered.keys())},
            **client_info,
        )
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update AI agent settings"}), 500


@ai_agent_settings_bp.route("/ai-agent/settings/validate-github-token", methods=["POST"])
@admin_required
def api_validate_github_token():
    """Validate a GitHub PAT by calling the GitHub API."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()

    if not token:
        return jsonify({"valid": False, "error": "No token provided"}), 400

    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            env={**os.environ, "GH_TOKEN": token},
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            username = result.stdout.strip()
            return jsonify({"valid": True, "username": username})
        return jsonify(
            {
                "valid": False,
                "error": result.stderr.strip() or "Invalid token",
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify({"valid": False, "error": "Validation timed out"})
    except FileNotFoundError:
        return jsonify({"valid": False, "error": "gh CLI not found"})
    except Exception as e:
        logger.error("Token validation error: %s", e)
        return jsonify({"valid": False, "error": str(e)})
