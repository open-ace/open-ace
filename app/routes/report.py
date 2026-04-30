#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Report Routes

API routes for reporting operations.
"""

import logging

from flask import Blueprint, jsonify, request

from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.message_service import MessageService
from app.services.usage_service import UsageService
from app.utils.helpers import get_days_ago, get_today

report_bp = Blueprint("report", __name__)
auth_service = AuthService()
usage_service = UsageService()
message_service = MessageService()
usage_repo = UsageRepository()
user_repo = UserRepository()
logger = logging.getLogger(__name__)


@report_bp.route("/report/my-usage", methods=["GET"])
def api_my_usage():
    """Get current user's usage report."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_auth, session_or_error = auth_service.require_auth(token)
    if not is_auth:
        return jsonify(session_or_error), 401

    # Get date range
    start_date = request.args.get("start", get_days_ago(30))
    end_date = request.args.get("end", get_today())

    # Get user info
    user_id = session_or_error.get("user_id")
    username = session_or_error.get("username")

    # Get user's system_account for daily_messages matching
    user = user_repo.get_user_by_id(user_id)
    system_account = (user.get("system_account") or user.get("username", "")) if user else username

    # Get combined usage data (local + remote)
    usage_data = usage_repo.get_user_daily_detail(
        user_id=user_id,
        system_account=system_account,
        start_date=start_date,
        end_date=end_date,
    )

    # Calculate totals
    total_tokens = sum(u.get("tokens_used", 0) for u in usage_data)
    total_input = sum(u.get("input_tokens", 0) for u in usage_data)
    total_output = sum(u.get("output_tokens", 0) for u in usage_data)
    total_requests = sum(u.get("request_count", 0) for u in usage_data)

    return jsonify(
        {
            "user_id": user_id,
            "username": username,
            "date_range": {"start": start_date, "end": end_date},
            "totals": {
                "tokens": total_tokens,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "requests": total_requests,
            },
            "daily_usage": usage_data,
        }
    )
