"""
Open ACE - AI Computing Explorer - Usage Routes

API routes for usage data operations.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import _log_cross_tenant_operation, auth_required, require_tenant_scope
from app.auth.permissions import is_platform_admin_role
from app.services.summary_service import SummaryService
from app.services.usage_service import UsageService
from app.utils.helpers import get_days_ago, get_today
from app.utils.request_context import get_current_tenant_id

logger = logging.getLogger(__name__)

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

    Without this gate, ``get_current_tenant_id()`` returns ``None`` and the
    repository layer treats it as a wildcard/global filter, leaking
    cross-tenant usage data to a no-tenant non-admin. Admins keep global
    scope; tenant-scoped non-admins keep their tenant.
    """
    _, error = require_tenant_scope()
    if error is not None:
        return error


@usage_bp.route("/summary")
def api_summary():
    """Get summary statistics for all tools from pre-aggregated summary table."""
    host = request.args.get("host")
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    tenant_id = get_current_tenant_id()

    # Issue #2821: 预聚合路径选择逻辑
    # - 平台管理员始终可访问全局预聚合路径（无论 tenant_id 是否为空）
    # - 全局范围用户（tenant_id is None）也走预聚合路径
    # - 租户范围用户（tenant_id 非空且非平台管理员）走查询路径
    if not start_date and not end_date:
        if is_platform_admin_role(g.user_role) or tenant_id is None:
            # 预聚合路径：使用 summary_service.get_summary()
            # Check if summary needs refresh and refresh if stale
            if summary_service.needs_refresh():
                summary_service.refresh_summary()

            summary = summary_service.get_summary(host_name=host)
        else:
            # 查询路径：使用 usage_service.get_usage_summary()
            # Issue #2821: 明确传 None，避免空字符串导致意外行为
            summary = usage_service.get_usage_summary(
                host_name=host,
                start_date=None,
                end_date=None,
                tenant_id=tenant_id,
            )
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
    """Refresh summary data from daily_messages table.

    Issue #2821: 使用基于角色的授权判断，而非 tenant_id is not None。
    平台管理员（无论 tenant_id 是否为空）都可执行全局刷新。
    """
    host = request.args.get("host")

    # Issue #2821: 使用基于角色的授权判断
    # 平台管理员（无论 tenant_id 是否为空）都可执行全局刷新
    user_role = getattr(g, "user_role", None)

    if not is_platform_admin_role(user_role):
        # 非平台管理员，拒绝访问
        if user_role == "tenant_admin":
            return (
                jsonify({
                    "status": "error",
                    "message": "Tenant-scoped summary refresh is automatic"
                }),
                403,
            )
        else:
            # 普通用户或其他角色
            return (
                jsonify({
                    "status": "error",
                    "message": "Platform admin access required"
                }),
                403,
            )

    # 平台管理员：执行刷新
    success = summary_service.refresh_summary(host_name=host)

    if success:
        # Issue #2821: 平台管理员带 tenant_id 执行全局刷新时，记录审计日志
        user_tenant_id = getattr(g, "tenant_id", None)
        if user_tenant_id is not None:
            _log_summary_refresh_audit(user_tenant_id, host)

        return jsonify({"status": "success", "message": "Summary refreshed"})
    else:
        return jsonify({"status": "error", "message": "Failed to refresh summary"}), 500


def _log_summary_refresh_audit(actor_tenant_id: int, host: str | None) -> None:
    """记录平台管理员执行全局摘要刷新的审计日志。

    Issue #2821: 平台管理员带 tenant_id 执行全局刷新时记录审计。

    Args:
        actor_tenant_id: 平台管理员的 tenant_id
        host: 刷新的 host 参数（如有）
    """
    try:
        _log_cross_tenant_operation(
            actor_user_id=g.user_id,
            actor_tenant_id=actor_tenant_id,
            target_tenant_id=None,  # 全局操作
            action=f"POST /api/summary/refresh (host={host or 'all'})",
        )
    except Exception as e:
        logger.warning("Failed to log summary refresh audit: %s", e)


@usage_bp.route("/today")
def api_today():
    """Get today's usage for all tools, merged by tool_name."""
    host = request.args.get("host")
    tool = request.args.get("tool")
    result = usage_service.get_today_usage(
        tool_name=tool,
        host_name=host,
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
    )
    return jsonify(entries)


@usage_bp.route("/tools")
def api_tools():
    """Get list of all tools."""
    tools = usage_service.get_all_tools(tenant_id=get_current_tenant_id())
    return jsonify(tools)


@usage_bp.route("/hosts")
def api_hosts():
    """Get list of all hosts from pre-aggregated summary table.

    Issue #2821: 主机列表路径选择
    - 平台管理员始终可访问全局主机列表
    - 全局范围用户也访问全局主机列表
    - 租户范围用户使用租户过滤
    """
    tenant_id = get_current_tenant_id()
    # Ensure summary is up to date
    if summary_service.needs_refresh():
        summary_service.refresh_summary()

    # Issue #2821: 使用基于角色的路径选择
    if is_platform_admin_role(g.user_role) or tenant_id is None:
        # 平台管理员或全局范围 → 全局主机列表
        hosts = summary_service.get_all_hosts()
    else:
        # 租户范围 → 租户过滤
        hosts = usage_service.get_all_hosts(tenant_id=tenant_id)
    return jsonify(hosts)


@usage_bp.route("/trend")
def api_trend():
    """Get usage trend data aggregated by date for charts."""
    from app.repositories.daily_stats_repo import DailyStatsRepository

    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())
    host = request.args.get("host")
    tenant_id = get_current_tenant_id()

    # Ensure daily_stats is up to date
    daily_stats_repo = DailyStatsRepository()
    if daily_stats_repo.needs_refresh():
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
    """Get today's request statistics with total and by-tool breakdown.

    Issue #2773: Added _meta field to document statistics definition.
    """
    from app.constants.request_stats_meta import REQUEST_STATS_META
    from app.repositories.usage_repo import UsageRepository

    host = request.args.get("host")
    usage_repo = UsageRepository()
    stats = usage_repo.get_today_request_stats(host_name=host, tenant_id=get_current_tenant_id())

    # Add metadata field to document statistics definition (Issue #2773)
    stats["_meta"] = REQUEST_STATS_META

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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
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
        tenant_id=get_current_tenant_id(),
    )
    return jsonify(stats)
