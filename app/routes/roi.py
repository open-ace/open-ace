"""
Open ACE - ROI API Routes

API endpoints for ROI analysis and cost optimization.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required, require_tenant_scope
from app.modules.analytics.cost_optimizer import CostOptimizer
from app.modules.analytics.roi_calculator import ROIAssumptions, ROICalculator

logger = logging.getLogger(__name__)

roi_bp = Blueprint("roi", __name__)


def _parse_positive_float_arg(name: str) -> tuple[Optional[float], Optional[str]]:
    """Parse a positive float query parameter."""
    raw_value = request.args.get(name)
    if raw_value is None or raw_value == "":
        return None, None
    try:
        parsed = float(raw_value)
    except ValueError:
        return None, f"{name} must be a positive number"
    if not math.isfinite(parsed) or parsed <= 0:
        return None, f"{name} must be a positive number"
    return parsed, None


def _build_roi_assumptions() -> tuple[Optional[ROIAssumptions], Optional[str]]:
    """Build ROI assumptions from request overrides."""
    hourly_labor_cost, error = _parse_positive_float_arg("hourly_labor_cost")
    if error:
        return None, error

    productivity_multiplier, error = _parse_positive_float_arg("productivity_multiplier")
    if error:
        return None, error

    avg_time_saved_per_request, error = _parse_positive_float_arg("avg_time_saved_per_request")
    if error:
        return None, error

    currency = request.args.get("currency")
    if currency is not None:
        currency = currency.strip().upper()
        if not currency:
            return None, "currency must not be empty"
        if len(currency) > 8:
            return None, "currency must be 8 characters or fewer"

    assumptions = ROIAssumptions.from_env().with_overrides(
        hourly_labor_cost=hourly_labor_cost,
        productivity_multiplier=productivity_multiplier,
        avg_time_saved_per_request=avg_time_saved_per_request,
        currency=currency,
    )
    return assumptions, None


@roi_bp.before_request
@auth_required
def _require_auth():
    pass


@roi_bp.before_request
def _require_tenant_scope():
    """Fail closed for non-admins with no tenant (Issue #1775 / #1780).

    Without this gate, ``_caller_tenant_id()`` returns ``None`` for a
    non-admin whose user row has no ``tenant_id``, and the calculator's
    ``_normalize_tenant_id(None)`` collapses to "no tenant filter" — a
    wildcard/global query that leaks cross-tenant ROI data. Admins keep
    global scope (``tenant_id=None``); tenant-scoped non-admins keep their
    tenant. Mirrors ``app/routes/usage.py``.
    """
    _, error = require_tenant_scope()
    if error is not None:
        return error


def _caller_tenant_id() -> Optional[int]:
    """Return the authenticated caller's tenant scope.

    Non-admins reaching this point are guaranteed to have a resolvable
    tenant (``_require_tenant_scope`` denies the request otherwise); admins
    may still be ``None`` (global scope). Reading from ``g.user.tenant_id``
    (rather than ``g.tenant_id``) keeps the source of truth identical to
    ``usage``/``projects`` so the gate and the read can never disagree.
    """
    user = getattr(g, "user", None) or {}
    return user.get("tenant_id")


# ==================== ROI Analysis ====================


@roi_bp.route("/roi", methods=["GET"])
def get_roi():
    """Get ROI metrics for a period."""
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)
        tool_name = request.args.get("tool_name")

        # Default to last 30 days if not specified
        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        assumptions, error = _build_roi_assumptions()
        if error:
            return jsonify({"success": False, "error": error}), 400

        calculator = ROICalculator(assumptions=assumptions)
        roi = calculator.calculate_roi(
            start_date, end_date, user_id, tool_name, tenant_id=_caller_tenant_id()
        )

        return jsonify({"success": True, "data": roi.to_dict() if roi else None})
    except Exception as e:
        logger.error(f"Error calculating ROI: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/trend", methods=["GET"])
def get_roi_trend():
    """Get ROI trend over months."""
    try:
        months = request.args.get("months", default=6, type=int)
        user_id = request.args.get("user_id", type=int)

        assumptions, error = _build_roi_assumptions()
        if error:
            return jsonify({"success": False, "error": error}), 400

        calculator = ROICalculator(assumptions=assumptions)
        trends = calculator.get_roi_trend(months, user_id, tenant_id=_caller_tenant_id())

        return jsonify({"success": True, "data": [t.to_dict() for t in trends]})
    except Exception as e:
        logger.error(f"Error getting ROI trend: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/by-tool", methods=["GET"])
def get_roi_by_tool():
    """Get ROI breakdown by tool."""
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        assumptions, error = _build_roi_assumptions()
        if error:
            return jsonify({"success": False, "error": error}), 400

        calculator = ROICalculator(assumptions=assumptions)
        roi_by_tool = calculator.get_roi_by_tool(
            start_date, end_date, tenant_id=_caller_tenant_id()
        )

        return jsonify({"success": True, "data": {k: v.to_dict() for k, v in roi_by_tool.items()}})
    except Exception as e:
        logger.error(f"Error getting ROI by tool: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/by-user", methods=["GET"])
def get_roi_by_user():
    """Get ROI breakdown by user."""
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        assumptions, error = _build_roi_assumptions()
        if error:
            return jsonify({"success": False, "error": error}), 400

        calculator = ROICalculator(assumptions=assumptions)
        roi_by_user = calculator.get_roi_by_user(
            start_date, end_date, tenant_id=_caller_tenant_id()
        )

        return jsonify(
            {"success": True, "data": {str(k): v.to_dict() for k, v in roi_by_user.items()}}
        )
    except Exception as e:
        logger.error(f"Error getting ROI by user: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/cost-breakdown", methods=["GET"])
def get_cost_breakdown():
    """Get detailed cost breakdown.

    Cost breakdown is derived purely from token usage and model pricing; it
    does not consume ROI assumptions, so assumption override params are not
    accepted here (they were previously parsed and silently discarded).
    """
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)

        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        calculator = ROICalculator()
        breakdown = calculator.get_cost_breakdown(
            start_date, end_date, user_id, tenant_id=_caller_tenant_id()
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "breakdown": [b.to_dict() for b in breakdown],
                    "total_cost": round(sum(b.total_cost for b in breakdown), 4),
                },
            }
        )
    except Exception as e:
        logger.error(f"Error getting cost breakdown: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/daily-costs", methods=["GET"])
def get_daily_costs():
    """Get daily cost data for charting.

    Daily costs are derived purely from token usage and default pricing; they
    do not consume ROI assumptions, so assumption override params are not
    accepted here (they were previously parsed and silently discarded).
    """
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)

        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        calculator = ROICalculator()
        daily_costs = calculator.get_daily_costs(
            start_date, end_date, user_id, tenant_id=_caller_tenant_id()
        )

        return jsonify({"success": True, "data": daily_costs})
    except Exception as e:
        logger.error(f"Error getting daily costs: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/roi/summary", methods=["GET"])
def get_roi_summary():
    """Get ROI summary statistics."""
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)

        if not start_date or not end_date:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            start_date = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            ).strftime("%Y-%m-%d")

        assumptions, error = _build_roi_assumptions()
        if error:
            return jsonify({"success": False, "error": error}), 400

        calculator = ROICalculator(assumptions=assumptions)
        summary = calculator.get_summary_stats(
            start_date, end_date, user_id, tenant_id=_caller_tenant_id()
        )

        return jsonify({"success": True, "data": summary})
    except Exception as e:
        logger.error(f"Error getting ROI summary: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Cost Optimization ====================


@roi_bp.route("/optimization/suggestions", methods=["GET"])
def get_optimization_suggestions():
    """Get cost optimization suggestions."""
    try:
        days = request.args.get("days", default=30, type=int)

        optimizer = CostOptimizer()
        suggestions = optimizer.analyze(days, tenant_id=_caller_tenant_id())

        return jsonify({"success": True, "data": [s.to_dict() for s in suggestions]})
    except Exception as e:
        logger.error(f"Error getting optimization suggestions: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/optimization/cost-trend", methods=["GET"])
def get_optimization_cost_trend():
    """Get cost trend for optimization analysis."""
    try:
        days = request.args.get("days", default=30, type=int)

        optimizer = CostOptimizer()
        trend = optimizer.get_cost_trend(days, tenant_id=_caller_tenant_id())

        return jsonify({"success": True, "data": trend})
    except Exception as e:
        logger.error(f"Error getting cost trend: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@roi_bp.route("/optimization/efficiency", methods=["GET"])
def get_efficiency_report():
    """Get efficiency analysis report."""
    try:
        days = request.args.get("days", default=30, type=int)

        optimizer = CostOptimizer()
        report = optimizer.get_efficiency_report(days, tenant_id=_caller_tenant_id())

        return jsonify({"success": True, "data": report})
    except Exception as e:
        logger.error(f"Error getting efficiency report: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
