"""
Open ACE - AI Computing Explorer - Governance Routes

API routes for enterprise governance features:
- Audit logging
- Quota management
- Content filtering
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    admin_required,
    auth_required,
    enforce_resource_tenant_scope,
    platform_admin_required,
    same_tenant_or_platform_admin,
    same_tenant_user_required,
)
from app.modules.governance.audit_logger import AuditAction, AuditLogger, get_action_categories
from app.modules.governance.content_filter_singleton import (
    get_content_filter,
    invalidate_content_filter_cache,
)
from app.modules.governance.quota_manager import QuotaManager
from app.repositories.governance_repo import GovernanceRepository
from app.utils.request_context import get_current_tenant_id

governance_bp = Blueprint("governance", __name__)
audit_logger = AuditLogger()
quota_manager = QuotaManager()
governance_repo = GovernanceRepository()
logger = logging.getLogger(__name__)


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
@admin_required
def api_get_audit_logs():
    """Get audit logs with filters."""

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
    # Include the entire end_date (until 23:59:59)
    end_time = (
        datetime.fromisoformat(end_date) + timedelta(days=1) - timedelta(seconds=1)
        if end_date
        else None
    )

    # Query logs
    logs = audit_logger.query(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        severity=severity,
        start_time=start_time,
        end_time=end_time,
        tenant_id=get_current_tenant_id(),
        limit=min(limit, 1000),  # Cap at 1000
        offset=offset,
    )

    # Get total count
    total = audit_logger.count(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        severity=severity,
        start_time=start_time,
        end_time=end_time,
        tenant_id=get_current_tenant_id(),
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
@admin_required
def api_audit_logs():
    """Get audit logs with filters (alias for /audit/logs)."""
    return api_get_audit_logs()


@governance_bp.route("/governance/audit-logs", methods=["GET"])
@admin_required
def api_governance_audit_logs():
    """Get audit logs with filters (full path alias for /audit/logs)."""
    return api_get_audit_logs()


@governance_bp.route("/audit-actions", methods=["GET"])
@admin_required
def api_audit_actions():
    """Get all audit action types with categories.

    Returns all AuditAction enum values organized by category,
    including i18n keys for internationalization.

    Returns:
        JSON response with:
        - actions: List of all action types with value, label, category, i18n_key
        - categories: List of category info with key, label, i18n_key
    """
    categories_data = get_action_categories()

    # Build flat list of all actions
    all_actions = []
    action_to_category: dict[str, str] = {}
    action_to_resource_types: dict[str, list[str]] = {}
    resource_to_categories: dict[str, list[str]] = {}
    for category_key, category_data in categories_data.items():
        resource_types = category_data.get("resource_types", [])
        for action in category_data["actions"]:
            action_to_category[action["value"]] = category_key
            action_to_resource_types[action["value"]] = resource_types
            all_actions.append(
                {
                    "value": action["value"],
                    "label": action["label"],
                    "category": category_key,
                    "i18n_key": action["i18n_key"],
                    "resource_types": resource_types,
                }
            )
        for resource_type in resource_types:
            resource_to_categories.setdefault(resource_type, []).append(category_key)

    # Build categories list
    categories = [
        {
            "key": key,
            "label": data["label"],
            "i18n_key": data["i18n_key"],
            "resource_types": data.get("resource_types", []),
        }
        for key, data in categories_data.items()
    ]

    return jsonify(
        {
            "actions": all_actions,
            "categories": categories,
            "actionToCategory": action_to_category,
            "actionToResourceTypes": action_to_resource_types,
            "resourceToCategories": resource_to_categories,
        }
    )


@governance_bp.route("/audit/logs/export", methods=["GET"])
@admin_required
def api_export_audit_logs():
    """Export audit logs."""

    # Get date range
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    format_type = request.args.get("format", "json")

    start_time = (
        datetime.fromisoformat(start_date)
        if start_date
        else datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    )
    # Include the entire end_date (until 23:59:59)
    end_time = (
        datetime.fromisoformat(end_date) + timedelta(days=1) - timedelta(seconds=1)
        if end_date
        else datetime.now(timezone.utc).replace(tzinfo=None)
    )

    # Export logs
    exported_data = audit_logger.export_logs(
        start_time=start_time,
        end_time=end_time,
        format=format_type,
        tenant_id=get_current_tenant_id(),
    )

    # Log the export action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_EXPORT,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="audit_logs",
        details={"format": format_type, "start": start_date, "end": end_date},
        tenant_id=get_current_tenant_id(),
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
                "exported_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }
        )


@governance_bp.route("/audit/user/<int:user_id>/activity", methods=["GET"])
@admin_required
@same_tenant_user_required
def api_user_activity(user_id):
    """Get activity summary for a user.

    The tenant_id passed below already narrows the query, but that only makes
    a foreign user look inactive. Deny the request outright instead.
    """

    days = request.args.get("days", default=30, type=int)

    activity = audit_logger.get_user_activity(user_id, days=days, tenant_id=get_current_tenant_id())

    return jsonify(activity)


# ============================================================================
# Quota Management Routes
# ============================================================================

# NOTE: /quota/status route is defined in quota_bp (app/routes/quota.py)
# to avoid duplicate route definitions and ensure consistent response format.


@governance_bp.route("/quota/status/all", methods=["GET"])
@admin_required
def api_all_quota_status():
    """Get quota status for all users (admin only)."""

    statuses = quota_manager.get_all_quota_statuses()

    return jsonify([s.to_dict() for s in statuses])


@governance_bp.route("/quota/check", methods=["POST"])
@auth_required
def api_check_quota():
    """Check if user has quota available."""
    user_id = g.user_id
    data = request.get_json() or {}

    tokens = data.get("tokens", 0)
    requests = data.get("requests", 1)

    result = quota_manager.check_quota(user_id, tokens, requests)

    return jsonify(result)


@governance_bp.route("/quota/alerts", methods=["GET"])
@admin_required
def api_get_quota_alerts():
    """Get quota alerts."""

    unacknowledged_only = request.args.get("unacknowledged_only", default=False, type=bool)
    limit = request.args.get("limit", default=100, type=int)

    # Get current user's tenant ID for tenant filtering
    tenant_id = get_current_tenant_id()

    # Tenant Admin: only query alerts for users in the same tenant
    # Platform Admin: tenant_id = None, query all alerts
    if tenant_id is not None:
        alerts = quota_manager.get_alerts_by_tenant(
            tenant_id=tenant_id,
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )
    else:
        alerts = quota_manager.get_all_alerts(
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )

    return jsonify([a.to_dict() for a in alerts])


@governance_bp.route("/quota/alerts/<int:alert_id>/acknowledge", methods=["POST"])
@admin_required
def api_acknowledge_alert(alert_id):
    """Acknowledge a quota alert."""

    # quota_alerts has no tenant column, so resolve the owning tenant through
    # the alert's user before enforcing the boundary. A missing alert or user
    # resolves to None, which denies a tenant admin (fail closed, and no
    # cross-tenant existence oracle) and lets a platform admin fall through to
    # the 404 below.
    from app.repositories.user_repo import UserRepository

    alert = quota_manager.get_alert(alert_id)
    target_user = UserRepository().get_user_by_id(alert.user_id) if alert else None
    denial = enforce_resource_tenant_scope(target_user.get("tenant_id") if target_user else None)
    if denial is not None:
        return denial

    if alert is None:
        return jsonify({"error": "Alert not found"}), 404

    user_id = g.user_id

    success = quota_manager.acknowledge_alert(alert_id, user_id)

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.QUOTA_ALERT,
            user_id=user_id,
            username=g.user.get("username"),
            resource_type="quota_alert",
            resource_id=str(alert_id),
            resource_name=f"Quota alert #{alert_id}",
            details={"action": "acknowledged"},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to acknowledge alert"}), 500


# ============================================================================
# Content Filter Routes
# ============================================================================


@governance_bp.route("/content/check", methods=["POST"])
@auth_required
def api_check_content():
    """Check content for sensitive information."""
    data = request.get_json() or {}
    content = data.get("content", "")

    # Get tenant-specific sensitive keyword config
    tenant_id = get_current_tenant_id()
    tenant_config = None
    if tenant_id:
        try:
            from app.repositories.tenant_repo import TenantRepository

            tenant_repo = TenantRepository()
            tenant = tenant_repo.get_by_id(tenant_id)
            if tenant and tenant.settings:
                tenant_config = {
                    "tenant_id": tenant_id,  # Issue #2789: Pass tenant_id for keyword loading
                    "block_sensitive_keyword": tenant.settings.block_sensitive_keyword,
                    "sensitive_keyword_match_mode": tenant.settings.sensitive_keyword_match_mode,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch tenant config for tenant {tenant_id}: {e}")

    result = get_content_filter().check_content(content, tenant_config=tenant_config)

    # Log if blocked
    if not result.passed:
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.CONTENT_BLOCKED,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="content",
            details={
                "risk_level": result.risk_level,
                "matched_rules": result.matched_rules,
            },
            **client_info,
        )

    return jsonify(result.to_dict())


@governance_bp.route("/content/filter/stats", methods=["GET"])
@admin_required
def api_filter_stats():
    """Get content filter statistics."""

    stats = get_content_filter().get_stats()

    return jsonify(stats)


@governance_bp.route("/content/filter/patterns", methods=["POST"])
@platform_admin_required
def api_add_pattern():
    """Add a custom content filter pattern.

    DEPRECATED: This endpoint is deprecated and will be removed in a future version.
    Use POST /api/filter-rules instead.

    Issue #3058: This endpoint does not persist patterns to database.
    """
    # Return deprecation warning
    return (
        jsonify(
            {
                "success": False,
                "error": "Deprecated",
                "message": "This endpoint is deprecated and will be removed in a future version.",
                "migration_guide": "Use POST /api/filter-rules instead.",
                "documentation": "/docs/api/migrations/filter-patterns-v2.md",
            }
        ),
        200,
        {
            "Deprecation": "true",
            "Sunset": "Sat, 01 Feb 2027 00:00:00 GMT",
            "Link": '</api/filter-rules>; rel="successor-version"',
        },
    )


@governance_bp.route("/content/filter/keywords", methods=["POST"])
@platform_admin_required
def api_add_keyword():
    """Add a custom sensitive keyword.

    DEPRECATED: This endpoint is deprecated and will be removed in a future version.
    Use POST /api/tenants/{tenant_id}/sensitive-keywords instead.

    Issue #2789: This endpoint does not persist keywords to database.
    """
    # Return deprecation warning
    return (
        jsonify(
            {
                "success": False,
                "error": "Deprecated",
                "message": "This endpoint is deprecated and will be removed in a future version.",
                "migration_guide": "Use POST /api/tenants/{tenant_id}/sensitive-keywords instead.",
                "documentation": "/docs/api/migrations/sensitive-keywords-v2.md",
            }
        ),
        200,
        {
            "Deprecation": "true",
            "Sunset": "Sat, 01 Nov 2026 00:00:00 GMT",
            "Link": '</api/tenants/{tenant_id}/sensitive-keywords>; rel="successor-version"',
        },
    )


# ============================================================================
# Content Filter Rules Management
# ============================================================================


# Valid enum values for filter rules
VALID_RULE_TYPES = {"keyword", "regex", "pii"}
VALID_SEVERITIES = {"low", "medium", "high"}
VALID_ACTIONS = {"warn", "block", "redact"}


def _validate_filter_rule_input(
    pattern: str,
    rule_type: str,
    severity: str,
    action: str,
) -> tuple[bool, str]:
    """
    Validate filter rule input parameters.

    Returns:
        Tuple of (is_valid, error_message).
    """
    # Validate pattern length
    if len(pattern) > 1000:
        return False, "Pattern too long (max 1000 chars)"

    # Validate enum values
    if rule_type not in VALID_RULE_TYPES:
        return (
            False,
            f"Invalid type '{rule_type}'. Must be one of: {', '.join(sorted(VALID_RULE_TYPES))}",
        )

    if severity not in VALID_SEVERITIES:
        return (
            False,
            f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(VALID_SEVERITIES))}",
        )

    if action not in VALID_ACTIONS:
        return (
            False,
            f"Invalid action '{action}'. Must be one of: {', '.join(sorted(VALID_ACTIONS))}",
        )

    # Validate regex pattern if type is 'regex'
    if rule_type == "regex":
        try:
            # Check for ReDoS patterns
            if re.search(r"\+.*\+", pattern) or re.search(r"\*.*\*", pattern):
                return False, "Nested quantifiers not allowed (ReDoS risk)"

            if re.search(r"\([^)]*\|[^)]*\)[+*]", pattern):
                return False, "Alternation with quantifiers not allowed (ReDoS risk)"

            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return False, f"Invalid regex pattern: {e}"

    return True, ""


@governance_bp.route("/filter-rules", methods=["GET"])
@admin_required
def api_get_filter_rules():
    """Get content filter rules with pagination and filtering.

    Query Parameters:
        limit: Maximum number of records (default 100, max 1000).
        offset: Number of records to skip (default 0).
        type: Filter by type (keyword, regex, pii).
        severity: Filter by severity (low, medium, high).
        is_enabled: Filter by enabled status (true/false).

    Returns:
        JSON response with rules list and pagination info.
    """
    # Get query parameters
    limit = min(request.args.get("limit", default=100, type=int), 1000)
    offset = request.args.get("offset", default=0, type=int)

    # Get filter parameters
    rule_type = request.args.get("type")
    severity = request.args.get("severity")
    is_enabled_str = request.args.get("is_enabled")

    # Parse is_enabled filter
    is_enabled = None
    if is_enabled_str is not None:
        is_enabled = is_enabled_str.lower() in ("true", "1", "yes")

    # Validate filter parameters if provided
    if rule_type is not None and rule_type not in VALID_RULE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid type '{rule_type}'. Must be one of: {', '.join(sorted(VALID_RULE_TYPES))}"
                }
            ),
            400,
        )

    if severity is not None and severity not in VALID_SEVERITIES:
        return (
            jsonify(
                {
                    "error": f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
                }
            ),
            400,
        )

    # Get rules from repository
    rules, total = governance_repo.get_filter_rules_paginated(
        limit=limit,
        offset=offset,
        rule_type=rule_type,
        severity=severity,
        is_enabled=is_enabled,
    )

    return jsonify(
        {
            "rules": rules,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@governance_bp.route("/filter-rules/<int:rule_id>", methods=["GET"])
@admin_required
def api_get_filter_rule(rule_id):
    """Get a specific filter rule.

    Args:
        rule_id: Rule ID from URL path.

    Returns:
        JSON response with rule details.
    """
    rule = governance_repo.get_filter_rule(rule_id)

    if rule is None:
        return jsonify({"error": "Filter rule not found"}), 404

    return jsonify(rule)


@governance_bp.route("/filter-rules", methods=["POST"])
@platform_admin_required
def api_create_filter_rule():
    """Create a new content filter rule (idempotent).

    If a rule with the same pattern already exists, returns the existing
    record with is_new=False instead of creating a duplicate.

    Request Body:
        pattern: Pattern to match (required).
        type: Type of pattern (default: "keyword", enum: keyword|regex|pii).
        severity: Severity level (default: "medium", enum: low|medium|high).
        action: Action to take (default: "warn", enum: warn|block|redact).
        description: Optional description.
        is_enabled: Whether rule is enabled (default: true).

    Returns:
        201: Created {"success": true, "id": int, "is_new": true}
        200: Already exists {"success": true, "id": int, "is_new": false}
        400: Validation failed {"error": "..."}
    """
    data = request.get_json() or {}
    pattern = data.get("pattern")
    rule_type = data.get("type", "keyword")
    severity = data.get("severity", "medium")
    action = data.get("action", "warn")
    description = data.get("description")
    is_enabled = data.get("is_enabled", True)

    if not pattern:
        return jsonify({"error": "Pattern is required"}), 400

    pattern = pattern.strip()
    if not pattern:
        return jsonify({"error": "Pattern cannot be empty"}), 400

    # Validate input
    is_valid, error_msg = _validate_filter_rule_input(pattern, rule_type, severity, action)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Create rule (idempotent)
    rule, is_new = governance_repo.create_filter_rule_idempotent(
        pattern=pattern,
        rule_type=rule_type,
        severity=severity,
        action=action,
        description=description,
        is_enabled=is_enabled,
    )

    if rule is None:
        return jsonify({"error": "Failed to create filter rule"}), 500

    rule_id = rule["id"]

    # Invalidate content filter cache
    invalidate_content_filter_cache()

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.SYSTEM_CONFIG_CHANGE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="filter_rule",
        resource_id=str(rule_id),
        resource_name=pattern,
        details={
            "action": "create",
            "pattern": pattern,
            "type": rule_type,
            "is_new": is_new,
        },
        **client_info,
    )

    response_data = {"success": True, "id": rule_id, "is_new": is_new}
    status_code = 201 if is_new else 200
    return jsonify(response_data), status_code


@governance_bp.route("/filter-rules/<int:rule_id>", methods=["PUT"])
@platform_admin_required
def api_update_filter_rule(rule_id):
    """Update a content filter rule.

    Args:
        rule_id: Rule ID from URL path.

    Request Body:
        pattern: New pattern (optional).
        type: New type (optional, enum: keyword|regex|pii).
        severity: New severity (optional, enum: low|medium|high).
        action: New action (optional, enum: warn|block|redact).
        description: New description (optional).
        is_enabled: New enabled status (optional).

    Returns:
        JSON response with success status.
    """
    # Check if rule exists
    existing = governance_repo.get_filter_rule(rule_id)
    if existing is None:
        return jsonify({"error": "Filter rule not found"}), 404

    data = request.get_json() or {}

    # Validate input if pattern or type is being updated
    new_pattern = data.get("pattern")
    new_type = data.get("type")
    new_severity = data.get("severity")
    new_action = data.get("action")

    # Validate and normalize pattern if being updated
    if new_pattern is not None:
        new_pattern = new_pattern.strip()
        if not new_pattern:
            return jsonify({"error": "Pattern cannot be empty"}), 400

    # Use existing values for validation if not provided
    validate_pattern = new_pattern if new_pattern is not None else existing.get("pattern", "")
    validate_type = new_type if new_type is not None else existing.get("type", "keyword")
    validate_severity = (
        new_severity if new_severity is not None else existing.get("severity", "medium")
    )
    validate_action = new_action if new_action is not None else existing.get("action", "warn")

    # Validate if any relevant field is being changed
    if new_pattern or new_type or new_severity or new_action:
        is_valid, error_msg = _validate_filter_rule_input(
            validate_pattern, validate_type, validate_severity, validate_action
        )
        if not is_valid:
            return jsonify({"error": error_msg}), 400

    success = governance_repo.update_filter_rule(
        rule_id=rule_id,
        pattern=new_pattern,
        rule_type=new_type,
        severity=new_severity,
        action=new_action,
        description=data.get("description"),
        is_enabled=data.get("is_enabled"),
    )

    if success:
        # Invalidate content filter cache
        invalidate_content_filter_cache()

        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="filter_rule",
            resource_id=str(rule_id),
            resource_name=data.get("pattern") or f"Rule #{rule_id}",
            details={"action": "update", "changes": data},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to update filter rule"}), 500


@governance_bp.route("/filter-rules/<int:rule_id>", methods=["DELETE"])
@platform_admin_required
def api_delete_filter_rule(rule_id):
    """Delete a content filter rule.

    Args:
        rule_id: Rule ID from URL path.

    Returns:
        JSON response with success status.
    """
    # Check if rule exists
    existing = governance_repo.get_filter_rule(rule_id)
    if existing is None:
        return jsonify({"error": "Filter rule not found"}), 404

    success = governance_repo.delete_filter_rule(rule_id)

    if success:
        # Invalidate content filter cache
        invalidate_content_filter_cache()

        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="filter_rule",
            resource_id=str(rule_id),
            resource_name=f"Rule #{rule_id}",
            details={"action": "delete"},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete filter rule"}), 500


# ============================================================================
# Security Settings
# ============================================================================


@governance_bp.route("/security-settings", methods=["GET"])
@admin_required
def api_get_security_settings():
    """Get security settings."""

    settings = governance_repo.get_security_settings()

    return jsonify(settings)


@governance_bp.route("/security-settings", methods=["PUT"])
@platform_admin_required
def api_update_security_settings():
    """Update security settings.

    security_settings has no tenant column -- it is global config (2FA toggle,
    password policy, login-attempt limit, IP whitelist, audit thresholds), so a
    write governs every tenant. Same reasoning as the content-filter mutations:
    a tenant admin must not rewrite platform-wide security posture. Reads
    (GET /security-settings) stay admin-level.
    """

    data = request.get_json() or {}

    success = governance_repo.update_security_settings(data)

    if success:
        # Log the action
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="security_settings",
            details={"action": "update", "keys": list(data.keys())},
            **client_info,
        )

        return jsonify({"success": True})

    return jsonify({"error": "Failed to update security settings"}), 500


# ============================================================================
# Password Policy (accessible to all authenticated users)
# ============================================================================


@governance_bp.route("/password-policy", methods=["GET"])
@auth_required
def api_get_password_policy():
    """Get password policy settings.

    Returns password policy fields for regular users to display
    password requirements in UI. This endpoint is accessible to
    all authenticated users, not just admins.

    Returns:
        JSON response with password policy fields:
        - password_min_length: Minimum password length
        - password_require_uppercase: Whether uppercase letters required
        - password_require_lowercase: Whether lowercase letters required
        - password_require_number: Whether numbers required
        - password_require_special: Whether special characters required
    """
    policy = governance_repo.get_password_policy()
    return jsonify(policy)


# ============================================================================
# Tenant Sensitive Keywords (Issue #2789)
# ============================================================================


@governance_bp.route("/tenants/<int:tenant_id>/sensitive-keywords", methods=["GET"])
@same_tenant_or_platform_admin
def api_get_tenant_keywords(tenant_id):
    """Get tenant sensitive keywords with pagination.

    Issue #2789: List all sensitive keywords for a specific tenant.

    Args:
        tenant_id: Tenant ID from URL path.

    Query Parameters:
        limit: Maximum number of records (default 100, max 1000).
        offset: Number of records to skip (default 0).
        is_enabled: Filter by enabled status (optional).

    Returns:
        JSON response with keywords list and pagination info.
    """
    # Get query parameters
    limit = min(request.args.get("limit", default=100, type=int), 1000)
    offset = request.args.get("offset", default=0, type=int)
    is_enabled_str = request.args.get("is_enabled")

    # Parse is_enabled filter
    is_enabled = None
    if is_enabled_str is not None:
        is_enabled = is_enabled_str.lower() in ("true", "1", "yes")

    # Get keywords from repository
    keywords = governance_repo.get_tenant_keywords(
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        is_enabled=is_enabled,
    )

    # Get total count
    total = governance_repo.get_tenant_keywords_count(
        tenant_id=tenant_id,
        is_enabled=is_enabled,
    )

    return jsonify(
        {
            "keywords": keywords,
            "total": total,
            "limit": limit,
            "offset": offset,
            "tenant_id": tenant_id,
        }
    )


@governance_bp.route("/tenants/<int:tenant_id>/sensitive-keywords", methods=["POST"])
@same_tenant_or_platform_admin
def api_create_tenant_keyword(tenant_id):
    """Create a tenant sensitive keyword.

    Issue #2789: Add a new sensitive keyword for a specific tenant.
    Idempotent: if keyword already exists, returns existing record.

    Args:
        tenant_id: Tenant ID from URL path.

    Request Body:
        keyword: The keyword to add (required).

    Returns:
        JSON response with created/existing keyword record.
        HTTP 201 if newly created, HTTP 200 if already exists.
    """
    data = request.get_json() or {}
    keyword = data.get("keyword")

    if not keyword or not keyword.strip():
        return jsonify({"error": "Keyword is required"}), 400

    keyword = keyword.strip()

    # Create keyword (idempotent)
    record, is_new = governance_repo.create_tenant_keyword(
        tenant_id=tenant_id,
        keyword=keyword,
        created_by=g.user_id,
    )

    if record is None:
        return jsonify({"error": "Failed to create keyword"}), 500

    # Increment version number for cache invalidation
    governance_repo.increment_tenant_keywords_version(tenant_id)

    # Invalidate cache in current process
    from app.modules.governance.content_filter_singleton import invalidate_tenant_keywords_cache

    invalidate_tenant_keywords_cache(tenant_id)

    # Add is_new flag to response
    record["is_new"] = is_new

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.SYSTEM_CONFIG_CHANGE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="tenant_sensitive_keyword",
        resource_id=str(record["id"]),
        resource_name=keyword,
        tenant_id=tenant_id,
        details={
            "action": "create",
            "keyword": keyword,
            "is_new": is_new,
        },
        **client_info,
    )

    status_code = 201 if is_new else 200
    return jsonify(record), status_code


@governance_bp.route(
    "/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>", methods=["PUT"]
)
@same_tenant_or_platform_admin
def api_update_tenant_keyword(tenant_id, keyword_id):
    """Update a tenant sensitive keyword.

    Issue #2789: Update an existing sensitive keyword (enable/disable).

    Args:
        tenant_id: Tenant ID from URL path.
        keyword_id: Keyword ID from URL path.

    Request Body:
        is_enabled: New enabled status (required).

    Returns:
        JSON response with success status.
    """
    data = request.get_json() or {}
    is_enabled = data.get("is_enabled")

    if is_enabled is None:
        return jsonify({"error": "is_enabled is required"}), 400

    # Check if keyword exists
    existing = governance_repo.get_tenant_keyword(tenant_id, keyword_id)
    if existing is None:
        return jsonify({"error": "Keyword not found"}), 404

    # Update keyword
    success = governance_repo.update_tenant_keyword(
        tenant_id=tenant_id,
        keyword_id=keyword_id,
        is_enabled=is_enabled,
    )

    if not success:
        return jsonify({"error": "Failed to update keyword"}), 500

    # Increment version number for cache invalidation
    governance_repo.increment_tenant_keywords_version(tenant_id)

    # Invalidate cache in current process
    from app.modules.governance.content_filter_singleton import invalidate_tenant_keywords_cache

    invalidate_tenant_keywords_cache(tenant_id)

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.SYSTEM_CONFIG_CHANGE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="tenant_sensitive_keyword",
        resource_id=str(keyword_id),
        resource_name=existing.get("keyword", ""),
        tenant_id=tenant_id,
        details={
            "action": "update",
            "keyword": existing.get("keyword"),
            "changes": {"is_enabled": is_enabled},
        },
        **client_info,
    )

    return jsonify({"success": True})


@governance_bp.route(
    "/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>", methods=["DELETE"]
)
@same_tenant_or_platform_admin
def api_delete_tenant_keyword(tenant_id, keyword_id):
    """Delete a tenant sensitive keyword.

    Issue #2789: Remove a sensitive keyword for a specific tenant.

    Args:
        tenant_id: Tenant ID from URL path.
        keyword_id: Keyword ID from URL path.

    Returns:
        JSON response with success status.
    """
    # Check if keyword exists
    existing = governance_repo.get_tenant_keyword(tenant_id, keyword_id)
    if existing is None:
        return jsonify({"error": "Keyword not found"}), 404

    keyword_name = existing.get("keyword", "")

    # Delete keyword
    success = governance_repo.delete_tenant_keyword(
        tenant_id=tenant_id,
        keyword_id=keyword_id,
    )

    if not success:
        return jsonify({"error": "Failed to delete keyword"}), 500

    # Increment version number for cache invalidation
    governance_repo.increment_tenant_keywords_version(tenant_id)

    # Invalidate cache in current process
    from app.modules.governance.content_filter_singleton import invalidate_tenant_keywords_cache

    invalidate_tenant_keywords_cache(tenant_id)

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.SYSTEM_CONFIG_CHANGE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="tenant_sensitive_keyword",
        resource_id=str(keyword_id),
        resource_name=keyword_name,
        tenant_id=tenant_id,
        details={
            "action": "delete",
            "keyword": keyword_name,
        },
        **client_info,
    )

    return jsonify({"success": True})
