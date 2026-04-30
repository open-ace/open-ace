#!/usr/bin/env python3
"""
Open ACE - Insights Routes

API routes for AI conversation insights report generation and management.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request

from app.repositories.insights_repo import InsightsReportRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.insights_service import InsightsService

logger = logging.getLogger(__name__)

insights_bp = Blueprint("insights", __name__)

auth_service = AuthService()
user_repo = UserRepository()
message_repo = MessageRepository()
insights_repo = InsightsReportRepository()
insights_service = InsightsService(
    user_repo=user_repo,
    message_repo=message_repo,
    insights_repo=insights_repo,
)


@insights_bp.before_request
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


@insights_bp.route("/insights/generate", methods=["POST"])
def generate_report():
    """
    Generate or retrieve a cached insights report.

    Request body (optional):
        {
            "start_date": "2026-04-09",
            "end_date": "2026-04-16"
        }

    Defaults to last 7 days if no dates provided.
    If a report already exists for the date range, returns cached version.
    """
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        # Parse date range from request body
        data = request.get_json(silent=True) or {}
        start_date = data.get("start_date")
        end_date = data.get("end_date")

        if not start_date or not end_date:
            end = datetime.now()
            start = end - timedelta(days=7)
            start_date = start.strftime("%Y-%m-%d")
            end_date = end.strftime("%Y-%m-%d")

        # Generate insights
        report, error = insights_service.generate_insights(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

        if error:
            if error == "insufficient_data":
                return jsonify({
                    "error": "insufficient_data",
                    "message": "Not enough conversation data to generate insights. Please use AI tools more and try again.",
                }), 200
            return jsonify({"error": error}), 500

        return jsonify(report)

    except Exception as e:
        logger.error(f"Error generating insights for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@insights_bp.route("/insights/history", methods=["GET"])
def get_history():
    """Get user's insights report history."""
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        reports = insights_repo.get_user_reports(user_id, limit=10)
        return jsonify({"reports": reports})
    except Exception as e:
        logger.error(f"Error getting insights history for user {user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@insights_bp.route("/insights/<int:report_id>", methods=["DELETE"])
def delete_report(report_id: int):
    """Delete an insights report."""
    is_auth, user_or_error = require_auth()
    if not is_auth:
        return jsonify(user_or_error), 401

    user_id = user_or_error["id"]

    try:
        # Verify ownership before deleting
        report = insights_repo.get_report_by_id(report_id, user_id)
        if not report:
            return jsonify({"error": "Report not found"}), 404

        success = insights_repo.delete_report(report_id, user_id)
        if success:
            return jsonify({"message": "Report deleted successfully"})
        return jsonify({"error": "Failed to delete report"}), 500
    except Exception as e:
        logger.error(f"Error deleting insights report {report_id}: {e}")
        return jsonify({"error": str(e)}), 500
