"""
Open ACE - Analysis Routes

API routes for usage analysis and reporting.
"""

from __future__ import annotations


from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required
from app.services.analysis_service import AnalysisService

analysis_bp = Blueprint("analysis", __name__)
analysis_service = AnalysisService()


@analysis_bp.before_request
@auth_required
def _require_auth():
    pass


def _get_tenant_filter() -> tuple[bool, int | None]:
    """Get tenant filter parameters for the current request (Issue #1852).

    Returns:
        tuple: (is_admin, tenant_id)
        - is_admin: True if user is admin (global scope)
        - tenant_id: The tenant_id to filter by, or None for admin/invalid
    """
    user = getattr(g, "user", None) or {}
    is_admin = user.get("role") == "admin"
    tenant_id = user.get("tenant_id")

    # Fail closed: non-admin without tenant_id cannot access tenant-scoped data
    if not is_admin and not tenant_id:
        return False, None

    # Admin gets global scope (tenant_id = None)
    if is_admin:
        return True, None

    return False, tenant_id


@analysis_bp.before_request
def _check_tenant_access():
    """Check tenant access for non-admin users (Issue #1852).

    - Admins: global scope (no tenant filter)
    - Non-admins with tenant_id: tenant-scoped access
    - Non-admins without tenant_id: 403 (fail closed)
    """
    user = getattr(g, "user", None) or {}
    is_admin = user.get("role") == "admin"
    tenant_id = user.get("tenant_id")

    # Admin has global access
    if is_admin:
        return None

    # Non-admin must have tenant_id
    if not tenant_id:
        return jsonify({"error": "Access denied: no tenant association"}), 403

    # Tenant-scoped user has access
    return None


@analysis_bp.route("/analysis/batch")
def api_batch_analysis():
    """Get all analysis data in a single request for better performance.

    This endpoint combines multiple analysis queries into a single request,
    reducing network overhead and allowing for shared data fetching.
    """
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    # Get all data in one call
    result = analysis_service.get_batch_analysis(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/key-metrics")
def api_key_metrics():
    """Get key metrics for the dashboard."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    metrics = analysis_service.get_key_metrics(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(metrics)


@analysis_bp.route("/analysis/hourly-usage")
def api_hourly_usage():
    """Get hourly usage breakdown."""
    is_admin, tenant_id = _get_tenant_filter()
    date = request.args.get("date")
    tool = request.args.get("tool")
    host = request.args.get("host")

    result = analysis_service.get_hourly_usage(
        date=date,
        tool_name=tool,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/daily-hourly-usage")
def api_daily_hourly_usage():
    """Get daily and hourly usage patterns."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    result = analysis_service.get_daily_hourly_usage(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/peak-usage")
def api_peak_usage():
    """Get peak usage periods."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    result = analysis_service.get_peak_usage(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/user-ranking")
def api_user_ranking():
    """Get user ranking by token usage."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")
    limit = request.args.get("limit", 10, type=int)

    result = analysis_service.get_user_ranking(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        limit=limit,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/conversation-stats")
def api_conversation_stats():
    """Get conversation statistics."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    result = analysis_service.get_conversation_stats(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/user-segmentation")
def api_user_segmentation():
    """Get user segmentation data."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    result = analysis_service.get_user_segmentation(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/tool-comparison")
def api_tool_comparison():
    """Get tool comparison data."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    result = analysis_service.get_tool_comparison(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/anomaly-detection")
def api_anomaly_detection():
    """Get anomaly detection results."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")
    anomaly_type = request.args.get("type")
    severity = request.args.get("severity")

    result = analysis_service.detect_anomalies(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        anomaly_type=anomaly_type,
        severity=severity,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/anomaly-trend")
def api_anomaly_trend():
    """Get anomaly trend over time."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")
    anomaly_type = request.args.get("type")
    severity = request.args.get("severity")

    result = analysis_service.get_anomaly_trend(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        anomaly_type=anomaly_type,
        severity=severity,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/data-range")
def api_data_range():
    """Get the global data range (min and max dates) for the "All" quick-range.

    Returns the system's actual data span (from daily_stats) so the frontend
    can populate the "All" date-range button with real bounds instead of a
    hardcoded window. May return null when there is no data.
    """
    is_admin, tenant_id = _get_tenant_filter()
    result = analysis_service.get_data_range(tenant_id=tenant_id)
    return jsonify(result)


@analysis_bp.route("/analysis/recommendations")
def api_recommendations():
    """Get usage optimization recommendations."""
    is_admin, tenant_id = _get_tenant_filter()
    host = request.args.get("host")

    result = analysis_service.get_recommendations(
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify({"recommendations": result})
