"""
Open ACE - AI Computing Explorer - Usage Routes

API routes for usage data operations.
"""

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required, require_tenant_scope
from app.services.summary_service import SummaryService
from app.services.usage_service import UsageService
from app.utils.helpers import get_days_ago, get_today

usage_bp = Blueprint("usage", __name__)
usage_service = UsageService()
summary_service = SummaryService()


@usage_bp.before_request
@auth_required
def _require_auth():
    pass


@usage_bp.before_request
def _require_tenant_scope():
    """Fail closed for non-admins with no tenant (Issue #1775).

    Without this gate, ``_current_tenant_id()`` returns ``None`` and the
    repository layer treats it as a wildcard/global filter, leaking
    cross-tenant usage data to a no-tenant non-admin. Admins keep global
    scope; tenant-scoped non-admins keep their tenant.
    """
    _, error = require_tenant_scope()
    if error is not None:
        return error


def _current_tenant_id():
    """Return the authenticated user's tenant scope.

    Non-admins reaching this point are guaranteed to have a resolvable
    tenant (``_require_tenant_scope`` denies the request otherwise); admins
    may still be ``None`` (global scope).
    """
    user = getattr(g, "user", None) or {}
    return user.get("tenant_id")


@usage_bp.route("/summary")
def api_summary():
    """Get summary statistics for all tools from pre-aggregated summary table."""
    host = request.args.get("host")
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    tenant_id = _current_tenant_id()

    # No date parameters -> use pre-aggregated usage_summary table (fast) only
    # for global scope. Tenant-scoped users query the tenant-aware repo path.
    if not start_date and not end_date and tenant_id is None:
        # Check if summary needs refresh and refresh if stale
        if summary_service.needs_refresh():
            summary_service.refresh_summary()

        summary = summary_service.get_summary(host_name=host)
    else:
        summary = usage_service.get_usage_summary(
            host_name=host,
            start_date=start_date,
            end_date=end_date,
            tenant_id=tenant_id,
        )

    return jsonify(summary)


@usage_bp.route("/summary/refresh", methods=["POST"])
def api_refresh_summary():
    """Refresh summary data from daily_messages table."""
    host = request.args.get("host")
    if _current_tenant_id() is not None:
        return (
            jsonify({"status": "error", "message": "Tenant-scoped summary refresh is automatic"}),
            403,
        )
    success = summary_service.refresh_summary(host_name=host)
    if success:
        return jsonify({"status": "success", "message": "Summary refreshed"})
    else:
        return jsonify({"status": "error", "message": "Failed to refresh summary"}), 500


@usage_bp.route("/today")
def api_today():
    """Get today's usage for all tools, merged by tool_name."""
    host = request.args.get("host")
    tool = request.args.get("tool")
    result = usage_service.get_today_usage(
        tool_name=tool,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(result)


@usage_bp.route("/tool/<tool_name>/<int:days>")
def api_tool_usage(tool_name, days):
    """Get usage for a specific tool over N days."""
    host = request.args.get("host")
    entries = usage_service.get_tool_usage(
        tool_name,
        days,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/date/<date_str>")
def api_date_usage(date_str):
    """Get usage for a specific date."""
    host = request.args.get("host")
    tool = request.args.get("tool")
    entries = usage_service.get_date_usage(
        date_str,
        tool_name=tool,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/range")
def api_range_usage():
    """Get usage for a date range."""
    start_date = request.args.get("start", get_days_ago(7))
    end_date = request.args.get("end", get_today())
    tool = request.args.get("tool")
    host = request.args.get("host")

    entries = usage_service.get_range_usage(
        start_date,
        end_date,
        tool_name=tool,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/tools")
def api_tools():
    """Get list of all tools."""
    tools = usage_service.get_all_tools(tenant_id=_current_tenant_id())
    return jsonify(tools)


@usage_bp.route("/hosts")
def api_hosts():
    """Get list of all hosts from pre-aggregated summary table."""
    tenant_id = _current_tenant_id()
    # Ensure summary is up to date for global scope
    if tenant_id is None and summary_service.needs_refresh():
        summary_service.refresh_summary()

    if tenant_id is None:
        hosts = summary_service.get_all_hosts()
    else:
        hosts = usage_service.get_all_hosts(tenant_id=tenant_id)
    return jsonify(hosts)


@usage_bp.route("/trend")
def api_trend():
    """Get usage trend data aggregated by date for charts."""
    from app.repositories.daily_stats_repo import DailyStatsRepository

    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())
    host = request.args.get("host")
    tenant_id = _current_tenant_id()

    # Ensure daily_stats is up to date
    daily_stats_repo = DailyStatsRepository()
    if tenant_id is None and daily_stats_repo.needs_refresh():
        daily_stats_repo.refresh_stats()

    entries = usage_service.get_trend_data(
        start_date,
        end_date,
        host_name=host,
        tenant_id=tenant_id,
    )
    return jsonify(entries)


# ==================== Request Statistics APIs ====================


@usage_bp.route("/request/today")
def api_request_today():
    """Get today's request statistics with total and by-tool breakdown."""
    from app.repositories.usage_repo import UsageRepository

    host = request.args.get("host")
    usage_repo = UsageRepository()
    stats = usage_repo.get_today_request_stats(host_name=host, tenant_id=_current_tenant_id())
    return jsonify(stats)


@usage_bp.route("/request/trend")
def api_request_trend():
    """Get request trend data aggregated by date for charts."""
    from app.repositories.usage_repo import UsageRepository

    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())
    host = request.args.get("host")

    usage_repo = UsageRepository()
    entries = usage_repo.get_request_trend_data(
        start_date,
        end_date,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/request/by-tool")
def api_request_by_tool():
    """Get request trend data aggregated by date and tool for charts."""
    from app.repositories.usage_repo import UsageRepository

    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())
    host = request.args.get("host")

    usage_repo = UsageRepository()
    entries = usage_repo.get_request_trend_by_tool(
        start_date,
        end_date,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/request/by-user")
def api_request_by_user():
    """Get request statistics grouped by user (sender_name) for today."""
    from app.repositories.usage_repo import UsageRepository

    date = request.args.get("date")  # Optional, defaults to today
    host = request.args.get("host")

    usage_repo = UsageRepository()
    stats = usage_repo.get_request_stats_by_user(
        date=date,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(stats)


@usage_bp.route("/request/user/<user_name>/trend")
def api_user_request_trend(user_name):
    """Get request trend data for a specific user."""
    from app.repositories.usage_repo import UsageRepository

    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())
    host = request.args.get("host")

    usage_repo = UsageRepository()
    entries = usage_repo.get_user_request_trend(
        user_name,
        start_date,
        end_date,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/request/monthly")
def api_request_monthly():
    """Get monthly request statistics grouped by user."""
    from datetime import datetime

    from app.repositories.usage_repo import UsageRepository

    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    host = request.args.get("host")

    usage_repo = UsageRepository()
    stats = usage_repo.get_monthly_request_stats_by_user(
        year,
        month,
        host_name=host,
        tenant_id=_current_tenant_id(),
    )
    return jsonify(stats)
