"""
Open ACE - Insights Routes

API routes for AI conversation insights report generation and management.

Issue #2738: Added date range validation.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required
from app.repositories.insights_repo import InsightsReportRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository
from app.services.insights_service import InsightsService
from app.utils.date_range_errors import get_error_message
from app.utils.validators import validate_date_range

logger = logging.getLogger(__name__)

insights_bp = Blueprint("insights", __name__)

user_repo = UserRepository()
message_repo = MessageRepository()
insights_repo = InsightsReportRepository()
insights_service = InsightsService(
    user_repo=user_repo,
    message_repo=message_repo,
    insights_repo=insights_repo,
)


@insights_bp.route("/insights/generate", methods=["POST"])
@auth_required
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

    Issue #2738: Added date range validation.
    """

    user_id = g.user_id

    try:
        # Parse date range from request body
        data = request.get_json(silent=True) or {}
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        language = data.get("language", "zh")  # Default to Chinese

        # Issue #2738: Validate date range
        is_valid, error_code, parsed_start, parsed_end = validate_date_range(start_date, end_date)
        if not is_valid:
            # error_code is guaranteed to be str when is_valid is False
            assert error_code is not None  # Type narrowing for mypy
            return (
                jsonify(
                    {
                        "success": False,
                        "error": get_error_message(error_code),
                        "error_code": error_code,
                    }
                ),
                400,
            )

        # Apply default values if both are missing
        if parsed_start is None:
            end = datetime.now(timezone.utc).replace(tzinfo=None)
            start = end - timedelta(days=7)
            start_date = start.strftime("%Y-%m-%d")
            end_date = end.strftime("%Y-%m-%d")
        else:
            # When parsed_start is not None, parsed_end is also not None
            assert parsed_end is not None  # Type narrowing for mypy
            start_date = parsed_start.strftime("%Y-%m-%d")
            end_date = parsed_end.strftime("%Y-%m-%d")

        # Generate insights
        report, error = insights_service.generate_insights(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            language=language,
        )

        if error:
            if error == "insufficient_data":
                return (
                    jsonify(
                        {
                            "error": "insufficient_data",
                            "message": "Not enough conversation data to generate insights. Please use AI tools more and try again.",
                        }
                    ),
                    200,
                )
            return jsonify({"error": error}), 500

        return jsonify(report)

    except Exception as e:
        logger.error(f"Error generating insights for user {user_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@insights_bp.route("/insights/history", methods=["GET"])
@auth_required
def get_history():
    """Get user's insights report history."""

    user_id = g.user_id

    try:
        reports = insights_repo.get_user_reports(user_id, limit=10)
        return jsonify({"reports": reports})
    except Exception as e:
        logger.error(f"Error getting insights history for user {user_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@insights_bp.route("/insights/<int:report_id>", methods=["DELETE"])
@auth_required
def delete_report(report_id: int):
    """Delete an insights report."""

    user_id = g.user_id

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
        return jsonify({"error": "Internal server error"}), 500
