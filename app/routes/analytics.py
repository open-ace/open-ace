"""
Open ACE - AI Computing Explorer - Analytics Routes

API routes for usage analytics and reporting.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decorators import any_admin_required, resolve_admin_tenant_scope
from app.modules.analytics.usage_analytics import UsageAnalytics
from app.modules.governance.audit_logger import AuditAction, AuditLogger

analytics_bp = Blueprint("analytics", __name__)
usage_analytics = UsageAnalytics()
audit_logger = AuditLogger()
logger = logging.getLogger(__name__)


def get_client_info():
    """Get client IP and user agent."""
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }


def validate_forecast_days(days_str: str | None) -> tuple[int, dict | None]:
    """
    Validate forecast days parameter.

    Args:
        days_str: Raw days parameter from request.

    Returns:
        Tuple of (validated_days, error_response).
        If validation fails, error_response contains 400 response dict.
    """
    if days_str is None:
        return 7, None

    try:
        days = int(days_str)
    except ValueError:
        return 7, {
            "error": "invalid_parameter",
            "message": "days must be an integer",
            "parameter": "days",
            "received": days_str,
            "valid_range": "1-90",
        }

    if days < 1 or days > 90:
        return 7, {
            "error": "invalid_parameter",
            "message": "days must be between 1 and 90",
            "parameter": "days",
            "received": days,
            "valid_range": "1-90",
        }

    return days, None


def parse_date_range():
    """
    Parse date range from request parameters.

    Priority:
    1. Explicit start_date + end_date
    2. end_date + days (calculated from end_date backwards)
    3. Default: end_date=today, days=30

    Returns:
        tuple: (start_date: str, end_date: str, days: int)
    """
    # Get end_date (default to today)
    end_date = request.args.get(
        "end_date", datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    )

    # Get days parameter with validation
    days = request.args.get("days", default=30, type=int)
    if days <= 0:
        days = 1
    if days > 365:
        days = 365

    # Priority: use explicit start_date if provided
    start_date = request.args.get("start_date")

    if start_date:
        # Validate start_date <= end_date, swap if needed
        if start_date > end_date:
            start_date, end_date = end_date, start_date
    else:
        # No start_date provided: calculate from end_date - days
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=days)
        start_date = start_dt.strftime("%Y-%m-%d")

    return start_date, end_date, days


@analytics_bp.route("/analytics/report", methods=["GET"])
@any_admin_required
def api_usage_report():
    """Generate a comprehensive usage report.

    Issue #3245: Tenant isolation for report.
    """
    # Get date range using shared parser
    start_date, end_date, days = parse_date_range()

    include_trends = request.args.get("trends", "true").lower() == "true"
    include_anomalies = request.args.get("anomalies", "true").lower() == "true"

    # Resolve tenant scope for data isolation
    tenant_id, denial = resolve_admin_tenant_scope()
    if denial:
        return denial  # 403 for tenant_admin without tenant_id

    # Generate report with tenant isolation
    report = usage_analytics.generate_report(
        start_date=start_date,
        end_date=end_date,
        include_trends=include_trends,
        include_anomalies=include_anomalies,
        tenant_id=tenant_id,
    )

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_VIEW,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="analytics_report",
        details={
            "start_date": start_date,
            "end_date": end_date,
            "days": days,
            "tenant_id": tenant_id,
        },
        **client_info,
    )

    return jsonify(report.to_dict())


@analytics_bp.route("/analytics/forecast", methods=["GET"])
@analytics_bp.route("/analysis/forecast", methods=["GET"])
@any_admin_required
def api_usage_forecast():
    """Get usage forecast.

    Issue #3245: Tenant isolation for forecast API.

    Authorization:
    - tenant_admin: Only sees data for own tenant
    - platform_admin: Sees global data (all tenants)
    - admin (legacy): Same as platform_admin
    """
    days, error = validate_forecast_days(request.args.get("days"))
    if error:
        return jsonify(error), 400

    # Resolve tenant scope for data isolation
    tenant_id, denial = resolve_admin_tenant_scope()
    if denial:
        return denial  # 403 for tenant_admin without tenant_id

    # tenant_id is None for platform_admin (global access)
    # tenant_id is int for tenant_admin (tenant-scoped access)
    forecast = usage_analytics.get_forecast(days=days, tenant_id=tenant_id)

    # Log platform admin global access for audit
    if tenant_id is None and g.user_role in ("admin", "platform_admin"):
        audit_logger.log_action(
            action=AuditAction.ADMIN_CROSS_TENANT_ACCESS,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="forecast",
            details={
                "days": days,
                "scope": "global",
                "actor_tenant_id": g.tenant_id,
            },
        )

    return jsonify(forecast)


@analytics_bp.route("/analytics/efficiency", methods=["GET"])
@any_admin_required
def api_efficiency_metrics():
    """Get efficiency metrics.

    Issue #3245: Tenant isolation for efficiency metrics.
    """
    # Get date range using shared parser
    start_date, end_date, days = parse_date_range()

    # Resolve tenant scope for data isolation
    tenant_id, denial = resolve_admin_tenant_scope()
    if denial:
        return denial  # 403 for tenant_admin without tenant_id

    # tenant_id is None for platform_admin (global access)
    # tenant_id is int for tenant_admin (tenant-scoped access)
    metrics = usage_analytics.get_efficiency_metrics(start_date, end_date, tenant_id=tenant_id)

    return jsonify(metrics)


@analytics_bp.route("/analytics/export", methods=["GET"])
@any_admin_required
def api_export_analytics():
    """Export analytics data.

    Issue #3245: Tenant isolation for export.
    """
    # Get date range using shared parser
    start_date, end_date, days = parse_date_range()
    format_type = request.args.get("format", "json")

    # Resolve tenant scope for data isolation
    tenant_id, denial = resolve_admin_tenant_scope()
    if denial:
        return denial  # 403 for tenant_admin without tenant_id

    # Generate report with tenant isolation
    report = usage_analytics.generate_report(
        start_date=start_date,
        end_date=end_date,
        include_trends=True,
        include_anomalies=True,
        tenant_id=tenant_id,
    )

    # Log the export
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_EXPORT,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="analytics",
        details={
            "format": format_type,
            "start_date": start_date,
            "end_date": end_date,
            "tenant_id": tenant_id,
        },
        **client_info,
    )

    if format_type == "csv":
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        # Write summary
        writer.writerow(["Usage Analytics Report"])
        writer.writerow(["Period", f"{start_date} to {end_date}"])
        writer.writerow([])

        # Write summary statistics
        writer.writerow(["Summary"])
        writer.writerow(["Total Tokens", report.total_tokens])
        writer.writerow(["Total Input Tokens", report.total_input_tokens])
        writer.writerow(["Total Output Tokens", report.total_output_tokens])
        writer.writerow(["Total Requests", report.total_requests])
        writer.writerow(["Unique Tools", report.unique_tools])
        writer.writerow(["Unique Hosts", report.unique_hosts])
        writer.writerow(["Daily Average Tokens", round(report.daily_average_tokens, 2)])
        writer.writerow(["Peak Day", report.peak_day or "N/A"])
        writer.writerow(["Peak Tokens", report.peak_tokens])
        writer.writerow([])

        # Write tool breakdown
        writer.writerow(["Breakdown by Tool"])
        writer.writerow(["Tool", "Tokens", "Input", "Output", "Requests", "Days Active"])
        for tool, data in report.breakdown_by_tool.items():
            writer.writerow(
                [
                    tool,
                    data.get("tokens", 0),
                    data.get("input_tokens", 0),
                    data.get("output_tokens", 0),
                    data.get("requests", 0),
                    data.get("days_active", 0),
                ]
            )
        writer.writerow([])

        # Write host breakdown
        writer.writerow(["Breakdown by Host"])
        writer.writerow(["Host", "Tokens", "Requests", "Days Active"])
        for host, data in report.breakdown_by_host.items():
            writer.writerow(
                [host, data.get("tokens", 0), data.get("requests", 0), data.get("days_active", 0)]
            )
        writer.writerow([])

        # Write trends
        if report.trends:
            writer.writerow(["Trends"])
            writer.writerow(
                ["Metric", "Direction", "Change %", "Current", "Previous", "Confidence"]
            )
            for trend in report.trends:
                writer.writerow(
                    [
                        trend.metric,
                        trend.direction,
                        f"{trend.change_percentage}%",
                        trend.current_value,
                        trend.previous_value,
                        trend.confidence,
                    ]
                )
        writer.writerow([])

        # Write anomalies
        if report.anomalies:
            writer.writerow(["Anomalies"])
            writer.writerow(
                ["Type", "Metric", "Date", "Expected", "Actual", "Deviation %", "Severity"]
            )
            for anomaly in report.anomalies:
                writer.writerow(
                    [
                        anomaly.type,
                        anomaly.metric,
                        anomaly.date,
                        round(anomaly.expected_value),
                        round(anomaly.actual_value),
                        f"{anomaly.deviation_percentage}%",
                        anomaly.severity,
                    ]
                )

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=analytics_{start_date}_{end_date}.csv"
            },
        )

    else:
        # JSON export
        return jsonify(
            {
                "report": report.to_dict(),
                "exported_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "format": format_type,
            }
        )
