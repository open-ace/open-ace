#!/usr/bin/env python3
"""
Open ACE - Compliance Routes

API endpoints for compliance reporting and data retention management.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request

from app.modules.compliance.audit import AuditAnalyzer
from app.modules.compliance.report import ReportGenerator, ReportType
from app.modules.compliance.retention import DataRetentionManager
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# Create blueprint
compliance_bp = Blueprint("compliance", __name__, url_prefix="/api/compliance")

# Services
report_generator = ReportGenerator()
audit_analyzer = AuditAnalyzer()
retention_manager = DataRetentionManager()
auth_service = AuthService()


def _require_admin():
    """Require admin authentication."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    is_admin, result = auth_service.require_admin(token)

    if not is_admin:
        return False, jsonify({"error": result.get("error", "Admin access required")}), 403

    return True, result, None


# =============================================================================
# Report Generation Endpoints
# =============================================================================


@compliance_bp.route("/reports", methods=["GET"])
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
def generate_report():
    """Generate a compliance report (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

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
        generated_by=result.get("user_id"),
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
def list_saved_reports():
    """List saved reports (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

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
def get_saved_report(report_id: str):
    """Get a saved report (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

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
def analyze_patterns():
    """Analyze audit patterns (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    days = request.args.get("days", 30, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    patterns = audit_analyzer.analyze_patterns(start_time=start_time)

    return jsonify(patterns)


@compliance_bp.route("/audit/anomalies", methods=["GET"])
def detect_anomalies():
    """Detect audit anomalies (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    days = request.args.get("days", 7, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    anomalies = audit_analyzer.detect_anomalies(start_time=start_time)

    return jsonify(
        {
            "anomalies": [a.__dict__ for a in anomalies],
            "count": len(anomalies),
        }
    )


@compliance_bp.route("/audit/user/<int:user_id>/profile", methods=["GET"])
def get_user_profile(user_id: int):
    """Get user behavior profile (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    days = request.args.get("days", 30, type=int)

    profile = audit_analyzer.get_user_behavior_profile(user_id, days=days)

    return jsonify(profile)


@compliance_bp.route("/audit/security-score", methods=["GET"])
def get_security_score():
    """Get security score (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    days = request.args.get("days", 30, type=int)
    start_time = datetime.utcnow() - timedelta(days=days)

    score = audit_analyzer.generate_security_score(start_time=start_time)

    return jsonify(score)


# =============================================================================
# Data Retention Endpoints
# =============================================================================


@compliance_bp.route("/retention/rules", methods=["GET"])
def get_retention_rules():
    """Get data retention rules (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    rules = retention_manager.get_all_rules()

    return jsonify(
        {
            "rules": {k: v.to_dict() for k, v in rules.items()},
        }
    )


@compliance_bp.route("/retention/rules", methods=["PUT"])
def set_retention_rule():
    """Set a data retention rule (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    data_type = data.get("data_type")
    retention_days = data.get("retention_days")
    action = data.get("action", "delete")

    if not data_type or retention_days is None:
        return jsonify({"error": "data_type and retention_days are required"}), 400

    retention_manager.set_rule(data_type, retention_days, action)

    return jsonify(
        {
            "message": f"Retention rule set for {data_type}",
            "rule": retention_manager.get_rule(data_type).to_dict(),
        }
    )


@compliance_bp.route("/retention/cleanup", methods=["POST"])
def run_retention_cleanup():
    """Run data retention cleanup (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    dry_run = request.args.get("dry_run", "false").lower() == "true"

    report = retention_manager.run_cleanup(dry_run=dry_run)

    return jsonify(report.to_dict())


@compliance_bp.route("/retention/history", methods=["GET"])
def get_retention_history():
    """Get retention cleanup history (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    limit = request.args.get("limit", 30, type=int)

    history = retention_manager.get_retention_history(limit=limit)

    return jsonify(
        {
            "history": history,
            "count": len(history),
        }
    )


@compliance_bp.route("/retention/storage", methods=["GET"])
def estimate_storage():
    """Estimate storage usage (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    estimates = retention_manager.estimate_storage()

    return jsonify(estimates)


@compliance_bp.route("/retention/status", methods=["GET"])
def get_retention_status():
    """Get data retention compliance status (admin only)."""
    is_admin, result, error_response = _require_admin()
    if not is_admin:
        return error_response

    status = retention_manager.get_compliance_status()

    return jsonify(status)


def register_compliance_routes(app):
    """Register compliance routes with the Flask app."""
    app.register_blueprint(compliance_bp)
