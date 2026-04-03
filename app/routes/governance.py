#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Governance Routes

API routes for enterprise governance features:
- Audit logging
- Quota management
- Content filtering
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.governance.content_filter import ContentFilter
from app.modules.governance.quota_manager import QuotaManager
from app.services.auth_service import AuthService

governance_bp = Blueprint("governance", __name__)
auth_service = AuthService()
audit_logger = AuditLogger()
quota_manager = QuotaManager()
content_filter = ContentFilter()
logger = logging.getLogger(__name__)


def require_admin(token: str):
    """Require admin role and return session data."""
    is_admin, session_or_error = auth_service.require_admin(token)
    return is_admin, session_or_error


def require_auth(token: str):
    """Require authentication and return session data."""
    is_auth, session_or_error = auth_service.require_auth(token)
    return is_auth, session_or_error


def get_client_info():
    """Get client IP and user agent."""
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }


# ============================================================================
# Audit Log Routes
# ============================================================================


@governance_bp.route("/audit/logs", methods=["GET"])
def api_get_audit_logs():
    """Get audit logs with filters."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    # Get query parameters
    user_id = request.args.get("user_id", type=int)
    username = request.args.get("username")
    action = request.args.get("action")
    resource_type = request.args.get("resource_type")
    severity = request.args.get("severity")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = request.args.get("limit", default=100, type=int)
    offset = request.args.get("offset", default=0, type=int)

    # Parse dates
    start_time = datetime.fromisoformat(start_date) if start_date else None
    end_time = datetime.fromisoformat(end_date) if end_date else None

    # Query logs
    logs = audit_logger.query(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        severity=severity,
        start_time=start_time,
        end_time=end_time,
        limit=min(limit, 1000),  # Cap at 1000
        offset=offset,
    )

    # Get total count
    total = audit_logger.count(
        user_id=user_id, action=action, start_time=start_time, end_time=end_time
    )

    return jsonify(
        {
            "logs": [log.to_dict() for log in logs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@governance_bp.route("/audit-logs", methods=["GET"])
def api_audit_logs():
    """Get audit logs with filters (alias for /audit/logs)."""
    return api_get_audit_logs()


@governance_bp.route("/governance/audit-logs", methods=["GET"])
def api_governance_audit_logs():
    """Get audit logs with filters (full path alias for /audit/logs)."""
    return api_get_audit_logs()


@governance_bp.route("/audit/logs/export", methods=["GET"])
def api_export_audit_logs():
    """Export audit logs."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    # Get date range
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    format_type = request.args.get("format", "json")

    start_time = (
        datetime.fromisoformat(start_date) if start_date else datetime.utcnow() - timedelta(days=30)
    )
    end_time = datetime.fromisoformat(end_date) if end_date else datetime.utcnow()

    # Export logs
    exported_data = audit_logger.export_logs(
        start_time=start_time, end_time=end_time, format=format_type
    )

    # Log the export action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_EXPORT,
        user_id=session_or_error.get("user_id"),
        username=session_or_error.get("username"),
        resource_type="audit_logs",
        details={"format": format_type, "start": start_date, "end": end_date},
        **client_info,
    )

    # Return appropriate response
    if format_type == "csv":
        from flask import Response

        return Response(
            exported_data,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=audit_logs_{start_date}_{end_date}.csv"
            },
        )
    else:
        return jsonify(
            {
                "data": exported_data,
                "format": format_type,
                "exported_at": datetime.utcnow().isoformat(),
            }
        )


@governance_bp.route("/audit/user/<int:user_id>/activity", methods=["GET"])
def api_user_activity(user_id):
    """Get activity summary for a user."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    days = request.args.get("days", default=30, type=int)

    activity = audit_logger.get_user_activity(user_id, days=days)

    return jsonify(activity)


# ============================================================================
# Quota Management Routes
# ============================================================================

# NOTE: /quota/status route is defined in quota_bp (app/routes/quota.py)
# to avoid duplicate route definitions and ensure consistent response format.


@governance_bp.route("/quota/status/all", methods=["GET"])
def api_all_quota_status():
    """Get quota status for all users (admin only)."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    statuses = quota_manager.get_all_quota_statuses()

    return jsonify([s.to_dict() for s in statuses])


@governance_bp.route("/quota/check", methods=["POST"])
def api_check_quota():
    """Check if user has quota available."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    user_id = session_or_error.get("user_id")
    data = request.get_json() or {}

    tokens = data.get("tokens", 0)
    requests = data.get("requests", 1)

    result = quota_manager.check_quota(user_id, tokens, requests)

    return jsonify(result)


@governance_bp.route("/quota/alerts", methods=["GET"])
def api_get_quota_alerts():
    """Get quota alerts."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    unacknowledged_only = request.args.get("unacknowledged_only", default=False, type=bool)
    limit = request.args.get("limit", default=100, type=int)

    alerts = quota_manager.get_all_alerts(unacknowledged_only=unacknowledged_only, limit=limit)

    return jsonify([a.to_dict() for a in alerts])


@governance_bp.route("/quota/alerts/<int:alert_id>/acknowledge", methods=["POST"])
def api_acknowledge_alert(alert_id):
    """Acknowledge a quota alert."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    user_id = session_or_error.get("user_id")

    success = quota_manager.acknowledge_alert(alert_id, user_id)

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.QUOTA_ALERT,
            user_id=user_id,
            username=session_or_error.get("username"),
            resource_type="quota_alert",
            resource_id=str(alert_id),
            details={"action": "acknowledged"},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to acknowledge alert"}), 500


# ============================================================================
# Content Filter Routes
# ============================================================================


@governance_bp.route("/content/check", methods=["POST"])
def api_check_content():
    """Check content for sensitive information."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    data = request.get_json() or {}
    content = data.get("content", "")

    result = content_filter.check_content(content)

    # Log if blocked
    if not result.passed:
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.CONTENT_BLOCKED,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="content",
            details={
                "risk_level": result.risk_level,
                "matched_rules": result.matched_rules,
            },
            **client_info,
        )

    return jsonify(result.to_dict())


@governance_bp.route("/content/filter/stats", methods=["GET"])
def api_filter_stats():
    """Get content filter statistics."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    stats = content_filter.get_stats()

    return jsonify(stats)


@governance_bp.route("/content/filter/patterns", methods=["POST"])
def api_add_pattern():
    """Add a custom content filter pattern."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}
    name = data.get("name")
    pattern = data.get("pattern")
    risk = data.get("risk", "medium")

    if not name or not pattern:
        return jsonify({"error": "Name and pattern are required"}), 400

    try:
        content_filter.add_custom_pattern(name, pattern, risk)

        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="content_filter",
            details={"action": "add_pattern", "name": name, "risk": risk},
            **client_info,
        )

        return jsonify({"success": True, "pattern": name})

    except Exception as e:
        logger.error(f"Failed to add pattern: {e}")
        return jsonify({"error": str(e)}), 400


@governance_bp.route("/content/filter/keywords", methods=["POST"])
def api_add_keyword():
    """Add a custom sensitive keyword."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}
    keyword = data.get("keyword")

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    content_filter.add_custom_keyword(keyword)

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.SYSTEM_CONFIG_CHANGE,
        user_id=session_or_error.get("user_id"),
        username=session_or_error.get("username"),
        resource_type="content_filter",
        details={"action": "add_keyword", "keyword": keyword},
        **client_info,
    )

    return jsonify({"success": True, "keyword": keyword})


# ============================================================================
# Content Filter Rules Management
# ============================================================================

from app.repositories.governance_repo import GovernanceRepository

governance_repo = GovernanceRepository()


@governance_bp.route("/filter-rules", methods=["GET"])
def api_get_filter_rules():
    """Get all content filter rules."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    rules = governance_repo.get_filter_rules()

    return jsonify(rules)


@governance_bp.route("/filter-rules", methods=["POST"])
def api_create_filter_rule():
    """Create a new content filter rule."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}
    pattern = data.get("pattern")
    rule_type = data.get("type", "keyword")
    severity = data.get("severity", "medium")
    action = data.get("action", "warn")
    description = data.get("description")
    is_enabled = data.get("is_enabled", True)

    if not pattern:
        return jsonify({"error": "Pattern is required"}), 400

    rule_id = governance_repo.create_filter_rule(
        pattern=pattern,
        rule_type=rule_type,
        severity=severity,
        action=action,
        description=description,
        is_enabled=is_enabled,
    )

    if rule_id:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="filter_rule",
            resource_id=str(rule_id),
            details={"action": "create", "pattern": pattern, "type": rule_type},
            **client_info,
        )

        return jsonify({"success": True, "id": rule_id}), 201

    return jsonify({"error": "Failed to create filter rule"}), 500


@governance_bp.route("/filter-rules/<int:rule_id>", methods=["PUT"])
def api_update_filter_rule(rule_id):
    """Update a content filter rule."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}

    success = governance_repo.update_filter_rule(
        rule_id=rule_id,
        pattern=data.get("pattern"),
        rule_type=data.get("type"),
        severity=data.get("severity"),
        action=data.get("action"),
        description=data.get("description"),
        is_enabled=data.get("is_enabled"),
    )

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="filter_rule",
            resource_id=str(rule_id),
            details={"action": "update", "changes": data},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to update filter rule"}), 500


@governance_bp.route("/filter-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_filter_rule(rule_id):
    """Delete a content filter rule."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    success = governance_repo.delete_filter_rule(rule_id)

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="filter_rule",
            resource_id=str(rule_id),
            details={"action": "delete"},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete filter rule"}), 500


# ============================================================================
# Security Settings
# ============================================================================


@governance_bp.route("/security-settings", methods=["GET"])
def api_get_security_settings():
    """Get security settings."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    settings = governance_repo.get_security_settings()

    return jsonify(settings)


@governance_bp.route("/security-settings", methods=["PUT"])
def api_update_security_settings():
    """Update security settings."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}

    success = governance_repo.update_security_settings(data)

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=session_or_error.get("user_id"),
            username=session_or_error.get("username"),
            resource_type="security_settings",
            details={"action": "update", "keys": list(data.keys())},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to update security settings"}), 500
