#!/usr/bin/env python3
"""
Open ACE - Quota Routes

API routes for quota checking and user usage status.
Used by Work mode to check if user can continue using workspace.
"""

import logging
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from app.modules.governance.quota_manager import QuotaManager
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

quota_bp = Blueprint("quota", __name__)
auth_service = AuthService()
user_repo = UserRepository()
usage_repo = UsageRepository()
quota_manager = QuotaManager()


@quota_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if token:
        session = auth_service.get_session(token)
        if session:
            g.user = {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "role": session.get("role"),
            }
        else:
            g.user = None
    else:
        g.user = None


def require_auth():
    """Require authentication and return user info."""
    if not hasattr(g, "user") or not g.user:
        return False, {"error": "Authentication required"}
    return True, g.user


@quota_bp.route("/quota/check", methods=["GET"])
def check_quota():
    """
    Check if the current user has quota available.

    Returns quota status including:
    - Token usage and limits (daily/monthly)
    - Request usage and limits (daily/monthly)
    - Whether user is over quota

    This is the main API for Work mode to determine if workspace should be disabled.
    """
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        # Get quota status from QuotaManager
        status = quota_manager.get_user_quota_status(user_id, period="daily")

        # Also get monthly stats
        user = user_repo.get_user_by_id(user_id)
        monthly_token_quota = user.get("monthly_token_quota") if user else None
        monthly_request_quota = user.get("monthly_request_quota") if user else None

        # Calculate monthly usage
        today = datetime.now()
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        month_end = today.strftime("%Y-%m-%d")

        # Get monthly token usage from daily_messages
        monthly_token_usage = usage_repo.get_daily_range(month_start, month_end)
        monthly_tokens = sum(m.get("tokens_used", 0) for m in monthly_token_usage)

        # Get monthly request usage from daily_usage
        monthly_requests = usage_repo.get_request_count_total(month_start, month_end)

        # Build response
        response = {
            "user_id": user_id,
            "username": user_or_error.get("username"),
            "daily": {
                "tokens": {
                    "used": status.tokens_used,
                    "limit": status.token_limit,
                    "percentage": round((status.tokens_used / status.token_limit * 100), 2)
                    if status.token_limit and status.token_limit > 0
                    else 0,
                    "over_quota": status.is_over_token_quota,
                },
                "requests": {
                    "used": status.requests_used,
                    "limit": status.request_limit,
                    "percentage": round((status.requests_used / status.request_limit * 100), 2)
                    if status.request_limit and status.request_limit > 0
                    else 0,
                    "over_quota": status.is_over_request_quota,
                },
            },
            "monthly": {
                "tokens": {
                    "used": monthly_tokens,
                    "limit": monthly_token_quota,
                    "percentage": round((monthly_tokens / monthly_token_quota * 100), 2)
                    if monthly_token_quota and monthly_token_quota > 0
                    else 0,
                    "over_quota": monthly_token_quota is not None
                    and monthly_tokens > monthly_token_quota,
                },
                "requests": {
                    "used": monthly_requests,
                    "limit": monthly_request_quota,
                    "percentage": round((monthly_requests / monthly_request_quota * 100), 2)
                    if monthly_request_quota and monthly_request_quota > 0
                    else 0,
                    "over_quota": monthly_request_quota is not None
                    and monthly_requests > monthly_request_quota,
                },
            },
            "can_use": not (status.is_over_token_quota or status.is_over_request_quota),
            "alerts": [a.to_dict() for a in status.alerts] if status.alerts else [],
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error checking quota for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@quota_bp.route("/quota/status", methods=["GET"])
def get_quota_status():
    """
    Get detailed quota status for the current user.

    This is a simpler version that just returns the essential info
    for the user to see their usage in Work mode.
    """
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        # Get user info
        user = user_repo.get_user_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Get today's usage
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = usage_repo.get_request_stats_by_user(date=today)

        # Aggregate today's stats for this user
        today_requests = 0
        today_tokens = 0
        username = user.get("username", "")

        for stat in today_stats:
            if stat.get("user") == username:
                today_requests += stat.get("requests", 0)
                today_tokens += stat.get("tokens", 0)

        # Get monthly usage
        now = datetime.now()
        month_start = now.replace(day=1).strftime("%Y-%m-%d")
        month_end = now.strftime("%Y-%m-%d")

        monthly_request_stats = usage_repo.get_monthly_request_stats_by_user(
            now.year, now.month
        )

        monthly_requests = 0
        monthly_tokens = 0
        for stat in monthly_request_stats:
            if stat.get("user") == username:
                monthly_requests += stat.get("requests", 0)
                monthly_tokens += stat.get("tokens", 0)

        # Build response
        response = {
            "user": {
                "id": user_id,
                "username": username,
                "email": user.get("email"),
            },
            "daily": {
                "tokens": {
                    "used": today_tokens,
                    "limit": user.get("daily_token_quota"),
                },
                "requests": {
                    "used": today_requests,
                    "limit": user.get("daily_request_quota"),
                },
            },
            "monthly": {
                "tokens": {
                    "used": monthly_tokens,
                    "limit": user.get("monthly_token_quota"),
                },
                "requests": {
                    "used": monthly_requests,
                    "limit": user.get("monthly_request_quota"),
                },
            },
        }

        # Check if over quota
        daily_token_limit = user.get("daily_token_quota")
        daily_request_limit = user.get("daily_request_quota")
        monthly_token_limit = user.get("monthly_token_quota")
        monthly_request_limit = user.get("monthly_request_quota")

        over_daily_token = daily_token_limit and today_tokens > daily_token_limit
        over_daily_request = daily_request_limit and today_requests > daily_request_limit
        over_monthly_token = monthly_token_limit and monthly_tokens > monthly_token_limit
        over_monthly_request = monthly_request_limit and monthly_requests > monthly_request_limit

        response["over_quota"] = {
            "daily_token": over_daily_token,
            "daily_request": over_daily_request,
            "monthly_token": over_monthly_token,
            "monthly_request": over_monthly_request,
            "any": any([
                over_daily_token,
                over_daily_request,
                over_monthly_token,
                over_monthly_request,
            ]),
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting quota status for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@quota_bp.route("/quota/usage/me", methods=["GET"])
def get_my_usage():
    """
    Get detailed usage data for the current user.

    This provides historical usage data for the user to view
    their usage patterns in Work mode.
    """
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]
    username = user_or_error.get("username", "")

    try:
        # Get date range from query params
        start_date = request.args.get("start")
        end_date = request.args.get("end")

        if not start_date or not end_date:
            # Default to last 30 days
            from datetime import timedelta
            end = datetime.now()
            start = end - timedelta(days=30)
            start_date = start.strftime("%Y-%m-%d")
            end_date = end.strftime("%Y-%m-%d")

        # Get user's request trend
        request_trend = usage_repo.get_user_request_trend(
            username, start_date, end_date
        )

        # Get user info for limits
        user = user_repo.get_user_by_id(user_id)

        response = {
            "user": {
                "id": user_id,
                "username": username,
            },
            "limits": {
                "daily_token": user.get("daily_token_quota") if user else None,
                "monthly_token": user.get("monthly_token_quota") if user else None,
                "daily_request": user.get("daily_request_quota") if user else None,
                "monthly_request": user.get("monthly_request_quota") if user else None,
            },
            "usage": {
                "trend": request_trend,
            },
            "date_range": {
                "start": start_date,
                "end": end_date,
            },
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting usage for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500