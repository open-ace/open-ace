"""
Open ACE - Compliance Routes

API endpoints for compliance reporting and data retention management.
"""

import logging
import time
from datetime import datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.compliance.audit import AuditAnalyzer
from app.modules.compliance.report import ReportGenerator, ReportType
from app.modules.compliance.retention import DataRetentionManager
from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)

# Create blueprint
compliance_bp = Blueprint("compliance", __name__, url_prefix="/api/compliance")

# Services
report_generator = ReportGenerator()
_retention_manager = None

# Audit threshold settings cache (60s TTL, similar to auth_service pattern)
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
    """Generate a compliance report (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    report_type = data.get("report_type")
    if not report_type:
        return jsonify({"error": "report_type is required"}), 400

    # Parse date range
    period_start = data.get("period_start")
    period_end = data.get("period_end")

    if period_start:
        period_start = datetime.fromisoformat(period_start)
    else:
        period_start = datetime.utcnow() - timedelta(days=30)

    if period_end:
        period_end = datetime.fromisoformat(period_end)
    else:
        period_end = datetime.utcnow()

    # Generate report
    report = report_generator.generate_report(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        generated_by=g.user_id,
        tenant_id=data.get("tenant_id"),
        filters=data.get("filters"),
    )

    # Save report
    report_generator.save_report(report)

    # Return format
    output_format = data.get("format", "json")

    if output_format == "csv":
        return Response(
            report.to_csv(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=compliance_report_{report.metadata.report_id}.csv"
            },
        )

    return jsonify(report.to_dict())


@compliance_bp.route("/reports/saved", methods=["GET"])
@admin_required
def list_saved_reports():
    """List saved reports (admin only)."""

    report_type = request.args.get("report_type")
    tenant_id = request.args.get("tenant_id", type=int)
    limit = request.args.get("limit", 50, type=int)

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


@compliance_bp.route("/reports/<report_id>", methods=["GET"])
@admin_required
def get_saved_report(report_id: str):
    """Get a saved report (admin only)."""

    report = report_generator.get_saved_report(report_id)

    if not report:
        return jsonify({"error": "Report not found"}), 404

    # Check format
    output_format = request.args.get("format", "json")

    if output_format == "csv":
        return Response(
            report.to_csv(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=compliance_report_{report_id}.csv"
            },
        )

    return jsonify(report.to_dict())


# =============================================================================
# Audit Analysis Endpoints
# =============================================================================


@compliance_bp.route("/audit/patterns", methods=["GET"])
@admin_required
def analyze_patterns():
    """Analyze audit patterns (admin only)."""

    days = request.args.get("days", 30, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    patterns = _get_audit_analyzer().analyze_patterns(start_time=start_time)

    return jsonify(patterns)


@compliance_bp.route("/audit/anomalies", methods=["GET"])
@admin_required
def detect_anomalies():
    """Detect audit anomalies (admin only)."""

    days = request.args.get("days", 7, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    anomalies = _get_audit_analyzer().detect_anomalies(start_time=start_time)

    def serialize_anomaly(a):
        d = a.__dict__.copy()
        for key in ("first_seen", "last_seen"):
            if isinstance(d.get(key), datetime):
                d[key] = d[key].isoformat()
        return d

    return jsonify(
        {
            "anomalies": [serialize_anomaly(a) for a in anomalies],
            "count": len(anomalies),
        }
    )


@compliance_bp.route("/audit/user/<int:user_id>/profile", methods=["GET"])
@admin_required
def get_user_profile(user_id: int):
    """Get user behavior profile (admin only)."""

    days = request.args.get("days", 30, type=int)

    profile = _get_audit_analyzer().get_user_behavior_profile(user_id, days=days)

    return jsonify(profile)


@compliance_bp.route("/audit/security-score", methods=["GET"])
@admin_required
def get_security_score():
    """Get security score (admin only)."""

    days = request.args.get("days", 30, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    score = _get_audit_analyzer().generate_security_score(start_time=start_time)

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

    # Validate: only allow known threshold keys, values must be positive integers
    updates = {}
    for key in THRESHOLD_KEYS:
        if key in data:
            val = data[key]
            if not isinstance(val, (int, float)) or val < 1:
                return jsonify({"error": f"{key} must be a positive number"}), 400
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


def register_compliance_routes(app):
    """Register compliance routes with the Flask app."""
    app.register_blueprint(compliance_bp)
