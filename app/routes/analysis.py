"""
Open ACE - Analysis Routes

API routes for usage analysis and reporting.

Issue #2738: Added date range validation.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required
from app.models.user import User
from app.services.analysis_service import AnalysisService
from app.utils.date_range_errors import get_error_message
from app.utils.validators import validate_date_range

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

    Issue #2286: Accept legacy 'admin' role alongside 'platform_admin'.
    """
    user = getattr(g, "user", None) or {}
    is_admin = User.is_admin_role(user.get("role"))
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
    is_admin = User.is_admin_role(user.get("role"))
    tenant_id = user.get("tenant_id")

    # Admin has global access
    if is_admin:
        return None

    # Non-admin must have tenant_id
    if not tenant_id:
        return jsonify({"error": "Access denied: no tenant association"}), 403

    # Tenant-scoped user has access
    return None


def _validate_date_range_or_error(start_date: str | None, end_date: str | None):
    """Validate date range and return error response if invalid.

    Issue #2738: Helper function for analysis endpoints.

    Args:
        start_date: Start date string or None
        end_date: End date string or None

    Returns:
        On success: (start_date, end_date) as strings (may be None, None if input was None)
        On failure: (error_response, 400) where error_response is Flask Response object

    Note:
        analysis.py does NOT apply defaults in the route layer.
        Service layer handles defaults (get_batch_analysis uses get_days_ago(30)).
    """
    is_valid, error_code, parsed_start, parsed_end = validate_date_range(start_date, end_date)
    if not is_valid:
        # error_code is guaranteed to be str when is_valid is False
        # (validate_date_range contract: returns error_code: str on failure)
        assert error_code is not None  # Type narrowing for mypy
        return (
            jsonify(
                {"success": False, "error": get_error_message(error_code), "error_code": error_code}
            ),
            400,
        )
    # Return parsed values (None if both were missing) - Service layer handles defaults
    if parsed_start is None:
        return None, None
    # When parsed_start is not None, parsed_end is also not None
    # (validate_date_range contract: both dates parsed successfully on success)
    assert parsed_end is not None  # Type narrowing for mypy
    return parsed_start.strftime("%Y-%m-%d"), parsed_end.strftime("%Y-%m-%d")


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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

    result = analysis_service.get_user_segmentation(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,  # Issue #1852: Pass tenant filter
    )
    return jsonify(result)


@analysis_bp.route("/analysis/user-role-distribution")
def api_user_role_distribution():
    """Get user role distribution data.

    Issue #3079: Support role-based user grouping in trend analysis.

    Returns user counts by role group (admin, manager, user, unknown).
    """
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

    result = analysis_service.get_user_role_distribution(
        start_date=start_date,
        end_date=end_date,
        host_name=host,
        tenant_id=tenant_id,
    )
    return jsonify(result)


@analysis_bp.route("/analysis/tool-comparison")
def api_tool_comparison():
    """Get tool comparison data."""
    is_admin, tenant_id = _get_tenant_filter()
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    host = request.args.get("host")

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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

    # Issue #2738: Validate date range
    validated = _validate_date_range_or_error(start_date, end_date)
    # Check if validation failed: validated[1] is status code (int) instead of date string
    if validated[1] is not None and isinstance(validated[1], int):
        return validated[0], validated[1]
    start_date, end_date = validated

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
