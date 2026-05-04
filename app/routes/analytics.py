"""
Open ACE - AI Computing Explorer - Analytics Routes

API routes for usage analytics and reporting.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decorators import admin_required
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


@analytics_bp.route("/analytics/report", methods=["GET"])
@admin_required
def api_usage_report():
    """Generate a comprehensive usage report."""
    # Get date range
    end_date = request.args.get("end_date", datetime.utcnow().strftime("%Y-%m-%d"))
    days = request.args.get("days", default=30, type=int)

    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    include_trends = request.args.get("trends", "true").lower() == "true"
    include_anomalies = request.args.get("anomalies", "true").lower() == "true"

    # Generate report
    report = usage_analytics.generate_report(
        start_date=start_date,
        end_date=end_date,
        include_trends=include_trends,
        include_anomalies=include_anomalies,
    )

    # Log the action
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_VIEW,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="analytics_report",
        details={"start_date": start_date, "end_date": end_date, "days": days},
        **client_info,
    )

    return jsonify(report.to_dict())


@analytics_bp.route("/analytics/forecast", methods=["GET"])
@admin_required
def api_usage_forecast():
    """Get usage forecast."""
    days = request.args.get("days", default=7, type=int)

    forecast = usage_analytics.get_forecast(days=days)

    return jsonify(forecast)


@analytics_bp.route("/analytics/efficiency", methods=["GET"])
@admin_required
def api_efficiency_metrics():
    """Get efficiency metrics."""
    # Get date range
    end_date = request.args.get("end_date", datetime.utcnow().strftime("%Y-%m-%d"))
    days = request.args.get("days", default=30, type=int)

    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    metrics = usage_analytics.get_efficiency_metrics(start_date, end_date)

    return jsonify(metrics)


@analytics_bp.route("/analytics/export", methods=["GET"])
@admin_required
def api_export_analytics():
    """Export analytics data."""
    # Get parameters
    end_date = request.args.get("end_date", datetime.utcnow().strftime("%Y-%m-%d"))
    days = request.args.get("days", default=30, type=int)
    format_type = request.args.get("format", "json")

    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Generate report
    report = usage_analytics.generate_report(
        start_date=start_date, end_date=end_date, include_trends=True, include_anomalies=True
    )

    # Log the export
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.DATA_EXPORT,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="analytics",
        details={"format": format_type, "start_date": start_date, "end_date": end_date},
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
                "exported_at": datetime.utcnow().isoformat(),
                "format": format_type,
            }
        )
