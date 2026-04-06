#!/usr/bin/env python3
"""
Open ACE - Quota Routes

API routes for quota checking and user usage status.
Used by Work mode to check if user can continue using workspace.
"""

import logging
import time
from datetime import datetime
from functools import lru_cache

from flask import Blueprint, g, jsonify, request

from app.modules.governance.quota_manager import QuotaManager
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# Token quotas are stored in M (millions) units
# Convert to actual tokens when comparing with usage
TOKEN_QUOTA_MULTIPLIER = 1_000_000

quota_bp = Blueprint("quota", __name__)
auth_service = AuthService()
user_repo = UserRepository()
usage_repo = UsageRepository()
quota_manager = QuotaManager()

# Cache for user usage data (10 minute TTL for better performance)
_usage_cache: dict = {}
_CACHE_TTL = 600  # 10 minutes


def _get_cached_user_usage(user_id: int, start_date: str, end_date: str):
    """
    Get cached user usage data with improved caching strategy.
    
    Uses user_id directly for cache key and leverages pre-aggregated
    user_daily_stats table for fast queries.
    """
    cache_key = f"{user_id}:{start_date}:{end_date}"
    now = time.time()

    if cache_key in _usage_cache:
        cached_data, cached_time = _usage_cache[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_data

    # Fetch fresh data using optimized method (user_daily_stats table)
    request_trend = usage_repo.get_user_request_trend_by_user_id(user_id, start_date, end_date)
    
    # Only cache if we got data
    if request_trend:
        _usage_cache[cache_key] = (request_trend, now)
    
    return request_trend


def _clear_user_usage_cache(user_id: Optional[int] = None):
    """Clear usage cache for a specific user or all users."""
    global _usage_cache
    if user_id is None:
        _usage_cache = {}
    else:
        _usage_cache = {
            k: v for k, v in _usage_cache.items()
            if not k.startswith(f"{user_id}:")
        }


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
                    "limit": monthly_token_quota * TOKEN_QUOTA_MULTIPLIER if monthly_token_quota else None,
                    "percentage": round((monthly_tokens / (monthly_token_quota * TOKEN_QUOTA_MULTIPLIER) * 100), 2)
                    if monthly_token_quota and monthly_token_quota > 0
                    else 0,
                    "over_quota": monthly_token_quota is not None
                    and monthly_tokens >= monthly_token_quota * TOKEN_QUOTA_MULTIPLIER,
                },
                "requests": {
                    "used": monthly_requests,
                    "limit": monthly_request_quota,
                    "percentage": round((monthly_requests / monthly_request_quota * 100), 2)
                    if monthly_request_quota and monthly_request_quota > 0
                    else 0,
                    "over_quota": monthly_request_quota is not None
                    and monthly_requests >= monthly_request_quota,
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

        username = user.get("username", "")
        # Use system_account for matching sender_name if available
        # sender_name format: {system_account}-{hostname}-{tool}
        system_account = user.get("system_account") or username

        # Get today's usage - filter by system_account prefix
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = usage_repo.get_request_stats_by_user(date=today, user_name=system_account)

        # Aggregate today's stats for this user
        today_requests = sum(stat.get("requests", 0) for stat in today_stats)
        today_tokens = sum(stat.get("tokens", 0) for stat in today_stats)

        # Get monthly usage - filter by system_account prefix
        now = datetime.now()
        monthly_request_stats = usage_repo.get_monthly_request_stats_by_user(
            now.year, now.month, user_name=system_account
        )

        monthly_requests = sum(stat.get("requests", 0) for stat in monthly_request_stats)
        monthly_tokens = sum(stat.get("tokens", 0) for stat in monthly_request_stats)

        # Build response
        # Token quotas are stored in M units, convert to actual tokens for display
        daily_token_quota = user.get("daily_token_quota")
        monthly_token_quota = user.get("monthly_token_quota")
        response = {
            "user": {
                "id": user_id,
                "username": username,
                "email": user.get("email"),
            },
            "daily": {
                "tokens": {
                    "used": today_tokens,
                    "limit": daily_token_quota * TOKEN_QUOTA_MULTIPLIER if daily_token_quota else None,
                },
                "requests": {
                    "used": today_requests,
                    "limit": user.get("daily_request_quota"),
                },
            },
            "monthly": {
                "tokens": {
                    "used": monthly_tokens,
                    "limit": monthly_token_quota * TOKEN_QUOTA_MULTIPLIER if monthly_token_quota else None,
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

        over_daily_token = daily_token_limit and today_tokens >= daily_token_limit * TOKEN_QUOTA_MULTIPLIER
        over_daily_request = daily_request_limit and today_requests >= daily_request_limit
        over_monthly_token = monthly_token_limit and monthly_tokens > monthly_token_limit * TOKEN_QUOTA_MULTIPLIER
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
    
    Optimizations:
    - Uses pre-aggregated user_daily_stats table for fast queries
    - Implements 10-minute caching to reduce database load
    - Defaults to 7 days instead of 30 for faster initial load
    """
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        # Get user info for limits
        user = user_repo.get_user_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        username = user.get("username", "")

        # Get date range from query params
        start_date = request.args.get("start")
        end_date = request.args.get("end")

        if not start_date or not end_date:
            # Default to last 7 days (faster than 30 days)
            from datetime import timedelta
            end = datetime.now()
            start = end - timedelta(days=7)
            start_date = start.strftime("%Y-%m-%d")
            end_date = end.strftime("%Y-%m-%d")

        # Get user's request trend using optimized method (with caching)
        request_trend = _get_cached_user_usage(user_id, start_date, end_date)

        response = {
            "user": {
                "id": user_id,
                "username": username,
            },
            "limits": {
                "daily_token": user.get("daily_token_quota") * TOKEN_QUOTA_MULTIPLIER if user.get("daily_token_quota") else None,
                "monthly_token": user.get("monthly_token_quota") * TOKEN_QUOTA_MULTIPLIER if user.get("monthly_token_quota") else None,
                "daily_request": user.get("daily_request_quota"),
                "monthly_request": user.get("monthly_request_quota"),
            },
            "usage": {
                "trend": request_trend,
            },
            "date_range": {
                "start": start_date,
                "end": end_date,
            },
            "cached": True,  # Indicate if data came from cache
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting usage for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@quota_bp.route("/quota/webui-check", methods=["GET"])
def webui_quota_check():
    """
    Check quota using webui token (called by webui backend middleware).

    This endpoint is similar to check_quota() but authenticates via webui token
    instead of session token. The load_user before_request will set g.user=None
    for webui tokens, but that's fine since we handle auth internally here.
    """
    auth_header = request.headers.get("Authorization", "")
    webui_token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""

    if not webui_token:
        return jsonify({"error": "Missing webui token"}), 401

    from app.services.webui_manager import get_webui_manager
    manager = get_webui_manager()
    valid, user_id, error = manager.validate_token(webui_token)

    if not valid:
        return jsonify({"error": f"Invalid token: {error}"}), 401

    try:
        status = quota_manager.get_user_quota_status(user_id, period="daily")
        user = user_repo.get_user_by_id(user_id)
        monthly_token_quota = user.get("monthly_token_quota") if user else None
        monthly_request_quota = user.get("monthly_request_quota") if user else None

        today = datetime.now()
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        month_end = today.strftime("%Y-%m-%d")
        monthly_token_usage = usage_repo.get_daily_range(month_start, month_end)
        monthly_tokens = sum(m.get("tokens_used", 0) for m in monthly_token_usage)
        monthly_requests = usage_repo.get_request_count_total(month_start, month_end)

        over_monthly_token = monthly_token_quota is not None and monthly_tokens >= monthly_token_quota * TOKEN_QUOTA_MULTIPLIER
        over_monthly_request = monthly_request_quota is not None and monthly_requests >= monthly_request_quota

        response = {
            "user_id": user_id,
            "daily": {
                "tokens": {
                    "used": status.tokens_used,
                    "limit": status.token_limit,
                    "percentage": round((status.tokens_used / status.token_limit * 100), 2) if status.token_limit and status.token_limit > 0 else 0,
                    "over_quota": status.is_over_token_quota,
                },
                "requests": {
                    "used": status.requests_used,
                    "limit": status.request_limit,
                    "percentage": round((status.requests_used / status.request_limit * 100), 2) if status.request_limit and status.request_limit > 0 else 0,
                    "over_quota": status.is_over_request_quota,
                },
            },
            "monthly": {
                "tokens": {"used": monthly_tokens, "limit": monthly_token_quota * TOKEN_QUOTA_MULTIPLIER if monthly_token_quota else None, "over_quota": over_monthly_token},
                "requests": {"used": monthly_requests, "limit": monthly_request_quota, "over_quota": over_monthly_request},
            },
            "can_use": not (status.is_over_token_quota or status.is_over_request_quota or over_monthly_token or over_monthly_request),
        }
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error in webui quota check for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500