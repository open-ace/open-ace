"""
Open ACE - Compliance Routes

API endpoints for compliance reporting and data retention management.
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decorators import (
    admin_required,
    enforce_requested_tenant_scope,
    enforce_resource_tenant_scope,
    resolve_tenant_scope,
    same_tenant_user_required,
)
from app.models.user import User
from app.modules.compliance.audit import AuditAnalyzer
from app.modules.compliance.report import ReportGenerator, ReportType
from app.modules.compliance.retention import DataRetentionManager
from app.modules.governance.audit_logger import AuditLogger
from app.repositories.database import Database
from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)

# Create blueprint
compliance_bp = Blueprint("compliance", __name__, url_prefix="/api/compliance")

# Services
report_generator = ReportGenerator()
_retention_manager = None

# Audit threshold settings cache (60s TTL, similar to auth_service pattern)
# Note: In multi-worker deployments (e.g. gunicorn with multiple workers),
# each worker maintains its own cache. Settings changes may take up to 60s
# to propagate across all workers.
_audit_settings_cache: dict = {}
_audit_settings_cache_time: float = 0
_AUDIT_SETTINGS_CACHE_TTL = 60

THRESHOLD_KEYS = [
    "audit_failed_login_threshold",
    "audit_rapid_action_threshold",
    "audit_off_hours_threshold",
    "audit_role_change_threshold",
    "audit_permission_change_threshold",
]


def _get_audit_settings() -> dict:
    """Load audit threshold settings with 60s in-memory cache."""
    global _audit_settings_cache, _audit_settings_cache_time
    now = time.time()
    if _audit_settings_cache and (now - _audit_settings_cache_time) < _AUDIT_SETTINGS_CACHE_TTL:
        return _audit_settings_cache

    repo = GovernanceRepository()
    all_settings = repo.get_security_settings()
    _audit_settings_cache = {k: all_settings[k] for k in THRESHOLD_KEYS if k in all_settings}
    _audit_settings_cache_time = now
    return _audit_settings_cache


def _get_audit_analyzer() -> AuditAnalyzer:
    """Create an AuditAnalyzer with current threshold settings."""
    settings = _get_audit_settings()
    return AuditAnalyzer(settings=settings)


def get_retention_manager():
    global _retention_manager
    if _retention_manager is None:
        _retention_manager = DataRetentionManager()
    return _retention_manager


def _current_tenant_id():
    """Return the authenticated user's tenant scope."""
    user = getattr(g, "user", None) or {}
    return user.get("tenant_id")


def _is_platform_admin():
    """Check if current user is platform admin.

    Platform admins can access data across all tenants.

    Returns:
        bool: True if user has platform_admin role (or legacy admin role).
    """
    from app.auth.permissions import is_platform_admin_role

    user = getattr(g, "user", None) or {}
    return is_platform_admin_role(user.get("role"))


# =============================================================================
# Report Generation Endpoints
# =============================================================================


@compliance_bp.route("/reports", methods=["GET"])
@admin_required
def list_reports():
    """List available report types."""
    report_types = [
        {
            "type": ReportType.USAGE_SUMMARY.value,
            "name": "Usage Summary",
            "description": "Summary of AI usage across the platform",
        },
        {
            "type": ReportType.USER_ACTIVITY.value,
            "name": "User Activity",
            "description": "User activity and engagement metrics",
        },
        {
            "type": ReportType.AUDIT_TRAIL.value,
            "name": "Audit Trail",
            "description": "Complete audit log trail",
        },
        {
            "type": ReportType.DATA_ACCESS.value,
            "name": "Data Access",
            "description": "Data access and export logs",
        },
        {
            "type": ReportType.SECURITY.value,
            "name": "Security Report",
            "description": "Security-related events and analysis",
        },
        {
            "type": ReportType.QUOTA_USAGE.value,
            "name": "Quota Usage",
            "description": "Quota usage and alerts",
        },
        {
            "type": ReportType.COMPREHENSIVE.value,
            "name": "Comprehensive Report",
            "description": "Complete compliance report with all sections",
        },
    ]

    return jsonify(
        {
            "report_types": report_types,
        }
    )


@compliance_bp.route("/reports", methods=["POST"])
@admin_required
def generate_report():
    """
    Generate a compliance report (admin only).

    Issue #2180: Tenant isolation for compliance reports.
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    report_type = data.get("report_type")
    if not report_type:
        return jsonify({"error": "report_type is required"}), 400

    # Issue #2180: Role-based tenant isolation
    caller_tenant_id, is_admin = resolve_tenant_scope()
    user_role = g.user.get("role")
    target_tenant_id = caller_tenant_id

    # Platform admin can request cross-tenant reports with explicit tenant_id
    # Issue #2332: Use centralized permission check with strict mode support
    from app.auth.permissions import is_platform_admin_role

    if is_platform_admin_role(user_role) and data.get("tenant_id") is not None:
        requested_tenant_id = data["tenant_id"]
        # Validate tenant exists
        db = Database()
        tenant_row = db.fetch_one("SELECT id FROM tenants WHERE id = ?", (requested_tenant_id,))
        if not tenant_row:
            return jsonify({"error": f"Tenant {requested_tenant_id} not found"}), 404
        target_tenant_id = requested_tenant_id
        # Log cross-tenant operation
        logger.info(
            "Platform admin %s generating report for tenant %s",
            g.user.get("id"),
            target_tenant_id,
        )
    # Tenant admin can only generate reports for their own tenant. Reuse the
    # shared request-scope guard: it normalizes the body value (a string "1"
    # must not read as != int 1), denies naming another tenant outright, and
    # rejects a tenant admin with no tenant -- consistent with the hardened
    # list/read siblings (get_saved_reports / get_saved_report).
    elif user_role == "tenant_admin":
        target_tenant_id, denial = enforce_requested_tenant_scope(data.get("tenant_id"))
        if denial is not None:
            return denial
    # Legacy admin: backward compatibility
    # - With tenant_id: scoped to that tenant (like tenant_admin)
    # - Without tenant_id: global access (like platform_admin)
    elif User.is_admin_role(user_role):
        if caller_tenant_id is not None:
            # Scoped to caller's tenant
            target_tenant_id = caller_tenant_id
        else:
            # Global access, but require explicit tenant_id for clarity
            # If no tenant_id provided, default to tenant 1 for backward compatibility
            target_tenant_id = data.get("tenant_id", 1)

    # Parse date range
    period_start = data.get("period_start")
    period_end = data.get("period_end")

    if period_start:
        period_start = datetime.fromisoformat(period_start)
    else:
        period_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)

    if period_end:
        period_end = datetime.fromisoformat(period_end)
    else:
        period_end = datetime.now(timezone.utc).replace(tzinfo=None)

    # Generate report with validated tenant_id
    report = report_generator.generate_report(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        generated_by=g.user_id,
        tenant_id=target_tenant_id,
        filters=data.get("filters"),
    )

    # Save report
    saved = report_generator.save_report(report)
    if not saved:
        logger.error(f"Failed to save report {report.metadata.report_id}")
        return jsonify({"error": "Failed to save report to database"}), 500

    # Return format
    output_format = data.get("format", "json")
    language = data.get("language", "en")

    # Generate filename with timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"compliance_report_{report_type}_{timestamp}"

    if output_format == "csv":
        # Add UTF-8 BOM for Excel compatibility
        csv_content = report.to_csv()
        response_content = b"\xef\xbb\xbf" + csv_content.encode("utf-8")
        return Response(
            response_content,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
        )

    if output_format == "html":
        # Log report generation action with validated tenant_id
        try:
            audit_logger = AuditLogger()
            audit_logger.log(
                action="generate_report",
                user_id=g.user_id,
                resource_type="compliance_report",
                resource_id=report.metadata.report_id,
                resource_name=report_type,
                tenant_id=target_tenant_id,
                details={
                    "report_type": report_type,
                    "format": output_format,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "caller_tenant_id": caller_tenant_id,
                    "target_tenant_id": target_tenant_id,
                },
            )
        except Exception:
            pass  # Don't fail report generation if audit logging fails

        html_content = report.to_html(language=language)
        response = Response(
            html_content,
            mimetype="text/html",
            headers={
                "Content-Disposition": f"attachment; filename={filename}.html",
                # Security: Content-Security-Policy header
                "Content-Security-Policy": "default-src 'self'; script-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
            },
        )
        return response

    if output_format == "excel":
        # Log report generation action with validated tenant_id
        try:
            audit_logger = AuditLogger()
            audit_logger.log(
                action="generate_report",
                user_id=g.user_id,
                resource_type="compliance_report",
                resource_id=report.metadata.report_id,
                resource_name=report_type,
                tenant_id=target_tenant_id,
                details={
                    "report_type": report_type,
                    "format": output_format,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "caller_tenant_id": caller_tenant_id,
                    "target_tenant_id": target_tenant_id,
                },
            )
        except Exception:
            pass

        excel_content = report.to_excel(language=language)
        response = Response(
            excel_content,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}.xlsx",
            },
        )
        return response

    return jsonify(report.to_dict())


@compliance_bp.route("/reports/saved", methods=["GET"])
@admin_required
def list_saved_reports():
    """List saved reports the caller may see.

    The query's ``tenant_id`` went straight to the repository, so a tenant
    admin could name another tenant -- or omit it entirely and get every
    tenant's reports. Reading a single report by id
    (``GET /reports/<report_id>``) is confined in :func:`get_saved_report` via
    a per-resource owner lookup (``enforce_resource_tenant_scope``).
    """

    report_type = request.args.get("report_type")
    tenant_id, denial = enforce_requested_tenant_scope(request.args.get("tenant_id"))
    if denial is not None:
        return denial
    limit = request.args.get("limit", 50, type=int)

    try:
        reports = report_generator.get_saved_reports(
            report_type=report_type,
            tenant_id=tenant_id,
            limit=limit,
        )

        return jsonify(
            {
                "reports": reports,
                "count": len(reports),
            }
        )
    except Exception as e:
        logger.error(f"Failed to list saved reports: {e}")
        return jsonify({"error": "Failed to query saved reports from database"}), 500


@compliance_bp.route("/reports/<report_id>", methods=["GET"])
@admin_required
def get_saved_report(report_id: str):
    """Get a saved report (admin only)."""

    report = report_generator.get_saved_report(report_id)
    # compliance_reports carries a tenant_id; confine the read to the caller's
    # tenant. A missing report resolves to None, which denies a tenant admin
    # (no cross-tenant existence oracle) and falls through to the 404 for a
    # platform admin.
    denial = enforce_resource_tenant_scope(report.metadata.tenant_id if report else None)
    if denial is not None:
        return denial

    if not report:
        return jsonify({"error": "Report not found"}), 404

    # Check format
    output_format = request.args.get("format", "json")
    language = request.args.get("language", "en")

    # Generate filename with timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"compliance_report_{report_id}_{timestamp}"

    if output_format == "csv":
        # Add UTF-8 BOM for Excel compatibility
        csv_content = report.to_csv()
        response_content = b"\xef\xbb\xbf" + csv_content.encode("utf-8")
        return Response(
            response_content,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
        )

    if output_format == "html":
        html_content = report.to_html(language=language)
        response = Response(
            html_content,
            mimetype="text/html",
            headers={
                "Content-Disposition": f"attachment; filename={filename}.html",
                "Content-Security-Policy": "default-src 'self'; script-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
            },
        )
        return response

    if output_format == "excel":
        excel_content = report.to_excel(language=language)
        response = Response(
            excel_content,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}.xlsx",
            },
        )
        return response

    return jsonify(report.to_dict())


# =============================================================================
# Audit Analysis Endpoints
# =============================================================================


def _validate_days(default: int = 30, min_val: int = 1, max_val: int = 365) -> int:
    """Validate and clamp the ``days`` query parameter.

    Returns a clamped integer in [min_val, max_val].  Non-integer or missing
    values fall back to *default*.
    """
    raw = request.args.get("days", None)
    if raw is None:
        return default
    try:
        days = int(raw)
    except (ValueError, TypeError):
        return default
    return max(min_val, min(days, max_val))


@compliance_bp.route("/audit/patterns", methods=["GET"])
@admin_required
def analyze_patterns():
    """Analyze audit patterns (admin only).

    Issue #2748: Tenant isolation for audit pattern analysis.
    Platform admins can access cross-tenant (tenant_id=None).
    """
    tenant_id = _current_tenant_id()
    if tenant_id is None and not _is_platform_admin():
        return jsonify({"error": "Tenant ID required"}), 400

    days = _validate_days(default=30)
    start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    patterns = _get_audit_analyzer().analyze_patterns(start_time=start_time, tenant_id=tenant_id)

    return jsonify(patterns)


@compliance_bp.route("/audit/anomalies", methods=["GET"])
@admin_required
def detect_anomalies():
    """Detect audit anomalies (admin only).

    Issue #2748: Tenant isolation for anomaly detection.
    Platform admins can access cross-tenant (tenant_id=None).
    """
    tenant_id = _current_tenant_id()
    if tenant_id is None and not _is_platform_admin():
        return jsonify({"error": "Tenant ID required"}), 400

    days = _validate_days(default=7)
    start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    anomalies = _get_audit_analyzer().detect_anomalies(start_time=start_time, tenant_id=tenant_id)

    # Load status info for all anomalies, keyed by anomaly_id
    statuses = _get_anomaly_statuses()

    def serialize_anomaly(a):
        d = a.__dict__.copy()
        for key in ("first_seen", "last_seen"):
            if isinstance(d.get(key), datetime):
                d[key] = d[key].isoformat()
        # Attach status info using the stable anomaly_id
        status_row = statuses.get(a.anomaly_id) if a.anomaly_id else None
        d["status"] = status_row["status"] if status_row else "pending"
        d["processed_at"] = status_row["processed_at"] if status_row else None
        d["processed_by"] = status_row["processed_by"] if status_row else None
        return d

    return jsonify(
        {
            "anomalies": [serialize_anomaly(a) for a in anomalies],
            "count": len(anomalies),
        }
    )


@compliance_bp.route("/audit/user/<int:user_id>/profile", methods=["GET"])
@admin_required
@same_tenant_user_required
def get_user_profile(user_id: int):
    """Get user behavior profile (admin only).

    Issue #2748: Tenant isolation for user behavior profile.
    get_user_behavior_profile applies no tenant filter of its own, so the
    boundary has to be enforced here.
    Platform admins can access cross-tenant (tenant_id=None).
    """
    tenant_id = _current_tenant_id()
    if tenant_id is None and not _is_platform_admin():
        return jsonify({"error": "Tenant ID required"}), 400

    days = _validate_days(default=30)

    profile = _get_audit_analyzer().get_user_behavior_profile(user_id, days=days, tenant_id=tenant_id)

    return jsonify(profile)


@compliance_bp.route("/audit/security-score", methods=["GET"])
@admin_required
def get_security_score():
    """Get security score (admin only).

    Issue #2748: Tenant isolation for security score generation.
    Platform admins can access cross-tenant (tenant_id=None).
    Accepts pre-computed anomalies via the ``precomputed_anomalies`` parameter
    of ``generate_security_score`` so that the front-end can request anomalies
    and security-score without duplicating the anomaly detection work
    (Issue #2750).  When called standalone the endpoint computes anomalies
    itself.
    """
    tenant_id = _current_tenant_id()
    if tenant_id is None and not _is_platform_admin():
        return jsonify({"error": "Tenant ID required"}), 400

    days = _validate_days(default=30)
    start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    analyzer = _get_audit_analyzer()

    # Compute anomalies once and pass them to the scorer so we don't re-run
    # detection inside generate_security_score.
    anomalies = analyzer.detect_anomalies(start_time=start_time, tenant_id=tenant_id)
    # Pass statuses so processed/ignored anomalies are excluded from deduction
    statuses = _get_anomaly_statuses()
    score = analyzer.generate_security_score(
        start_time=start_time,
        tenant_id=tenant_id,
        precomputed_anomalies=anomalies,
        anomaly_statuses=statuses,
    )

    return jsonify(score)


@compliance_bp.route("/audit/thresholds", methods=["GET"])
@admin_required
def get_audit_thresholds():
    """Get audit anomaly detection thresholds (admin only)."""
    settings = _get_audit_settings()
    return jsonify(settings)


@compliance_bp.route("/audit/thresholds", methods=["PUT"])
@admin_required
def update_audit_thresholds():
    """Update audit anomaly detection thresholds (admin only)."""
    global _audit_settings_cache, _audit_settings_cache_time

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Validate: only allow known threshold keys, values must be positive integers (1-10000)
    updates = {}
    for key in THRESHOLD_KEYS:
        if key in data:
            val = data[key]
            if not isinstance(val, (int, float)) or val < 1 or val > 10000:
                return jsonify({"error": f"{key} must be a number between 1 and 10000"}), 400
            updates[key] = int(val)

    if not updates:
        return jsonify({"error": "No valid threshold keys provided"}), 400

    repo = GovernanceRepository()
    success = repo.update_security_settings(updates)

    if success:
        # Invalidate cache
        _audit_settings_cache = {}
        _audit_settings_cache_time = 0
        return jsonify({"success": True, "updated": updates})

    return jsonify({"error": "Failed to update thresholds"}), 500


# =============================================================================
# Data Retention Endpoints
# =============================================================================


@compliance_bp.route("/retention/rules", methods=["GET"])
@admin_required
def get_retention_rules():
    """Get data retention rules (admin only)."""

    rules = get_retention_manager().get_all_rules()

    return jsonify(
        {
            "rules": {k: v.to_dict() for k, v in rules.items()},
        }
    )


@compliance_bp.route("/retention/rules", methods=["PUT"])
@admin_required
def set_retention_rule():
    """Set a data retention rule (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    data_type = data.get("data_type")
    retention_days = data.get("retention_days")
    action = data.get("action", "delete")

    # Validate action value
    VALID_ACTIONS = ["delete", "archive", "anonymize"]
    if action not in VALID_ACTIONS:
        return jsonify({"error": f"action must be one of: {', '.join(VALID_ACTIONS)}"}), 400

    if not data_type or retention_days is None:
        return jsonify({"error": "data_type and retention_days are required"}), 400

    get_retention_manager().set_rule(data_type, retention_days, action)

    return jsonify(
        {
            "message": f"Retention rule set for {data_type}",
            "rule": get_retention_manager().get_rule(data_type).to_dict(),
        }
    )


@compliance_bp.route("/retention/cleanup", methods=["POST"])
@admin_required
def run_retention_cleanup():
    """Run data retention cleanup (admin only)."""

    dry_run = request.args.get("dry_run", "false").lower() == "true"

    report = get_retention_manager().run_cleanup(dry_run=dry_run)

    return jsonify(report.to_dict())


@compliance_bp.route("/retention/history", methods=["GET"])
@admin_required
def get_retention_history():
    """Get retention cleanup history (admin only)."""

    limit = request.args.get("limit", 30, type=int)

    history = get_retention_manager().get_retention_history(limit=limit)

    return jsonify(
        {
            "history": history,
            "count": len(history),
        }
    )


@compliance_bp.route("/retention/storage", methods=["GET"])
@admin_required
def estimate_storage():
    """Estimate storage usage (admin only)."""

    estimates = get_retention_manager().estimate_storage()

    return jsonify(estimates)


@compliance_bp.route("/retention/status", methods=["GET"])
@admin_required
def get_retention_status():
    """Get data retention compliance status (admin only)."""

    status = get_retention_manager().get_compliance_status()

    return jsonify(status)


def _anomaly_hash(anomaly_type: str, affected_users: list) -> str:
    """Generate a legacy hash for identifying an anomaly group.

    .. deprecated::
        This function is kept for backward compatibility only.  New code
        should use the stable ``anomaly_id`` attached to each
        ``AnomalyDetection`` instance, which incorporates time-bucket and
        tenant information.
    """
    key = f"{anomaly_type}:{','.join(str(u) for u in sorted(affected_users or []))}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _get_anomaly_statuses() -> dict:
    """Load all anomaly statuses keyed by ``anomaly_id``.

    The table is queried for both the new ``anomaly_id`` column and the
    legacy ``affected_users_hash`` column.  New rows (with anomaly_id) are
    keyed by anomaly_id; legacy rows (with empty anomaly_id) are skipped
    because their coarse identity cannot be reliably mapped to individual
    anomaly instances.
    """
    db = Database()
    try:
        rows = db.fetch_all(
            "SELECT anomaly_id, anomaly_type, affected_users_hash, status, "
            "processed_by, processed_at FROM anomaly_status"
        )
        result: dict = {}
        for r in rows:
            aid = r.get("anomaly_id")
            if aid:
                result[aid] = r
            # Legacy rows without anomaly_id are intentionally ignored so
            # they do not pollute new anomaly instances.
        return result
    except Exception as e:
        logger.error(f"Failed to load anomaly statuses: {e}")
        return {}


@compliance_bp.route("/audit/anomalies/status", methods=["POST"])
@admin_required
def update_anomaly_status():
    """Update anomaly status (admin only).

    Preferred body::

        { "anomaly_id": str, "status": "processed"|"ignored" }

    Legacy body (still accepted for backward compatibility)::

        { "anomaly_type": str, "affected_users": list[int], "status": ... }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    anomaly_id = data.get("anomaly_id")
    anomaly_type = data.get("anomaly_type")
    affected_users = data.get("affected_users", [])
    new_status = data.get("status")

    if new_status not in ("processed", "ignored"):
        return jsonify({"error": "Invalid parameters"}), 400

    # Prefer anomaly_id; fall back to legacy type+users hash
    if not anomaly_id:
        if not anomaly_type:
            return jsonify({"error": "anomaly_id or anomaly_type required"}), 400
        anomaly_id = _anomaly_hash(anomaly_type, affected_users)

    user_id = g.user.get("id") if hasattr(g, "user") else None
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    db = Database()
    try:
        db.execute(
            "INSERT INTO anomaly_status "
            "(anomaly_id, anomaly_type, affected_users_hash, status, processed_by, processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (anomaly_id) "
            "DO UPDATE SET status = ?, processed_by = ?, processed_at = ?",
            (
                anomaly_id,
                anomaly_type or "",
                _anomaly_hash(anomaly_type or "", affected_users),
                new_status,
                user_id,
                now,
                new_status,
                user_id,
                now,
            ),
        )
        return jsonify({"success": True, "status": new_status, "anomaly_id": anomaly_id})
    except Exception as e:
        logger.error(f"Failed to update anomaly status: {e}")
        return jsonify({"error": "Database error"}), 500


def register_compliance_routes(app):
    """Register compliance routes with the Flask app."""
    app.register_blueprint(compliance_bp)
