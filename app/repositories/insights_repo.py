"""
Open ACE - Insights Report Repository

Repository for insights report CRUD operations.
"""

import json
import logging
from typing import Optional

from app.repositories.database import Database, is_postgresql

logger = logging.getLogger(__name__)


class InsightsReportRepository:
    """Repository for insights report data operations."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def save_report(
        self,
        user_id: int,
        start_date: str,
        end_date: str,
        report_data: dict,
        model: str,
    ) -> Optional[int]:
        """
        Save an insights report to the database.

        Args:
            user_id: User ID.
            start_date: Start date string (YYYY-MM-DD).
            end_date: End date string (YYYY-MM-DD).
            report_data: Structured report data from AI analysis.
            model: Model name used for generation.

        Returns:
            Optional[int]: Report ID or None on failure.
        """
        try:
            if is_postgresql():
                result = self.db.fetch_one(
                    """
                    INSERT INTO insights_reports
                    (user_id, start_date, end_date, overall_score, overall_assessment,
                     strengths, areas_for_improvement, suggestions, usage_summary,
                     model, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        user_id,
                        start_date,
                        end_date,
                        report_data.get("overall_score"),
                        report_data.get("overall_assessment"),
                        json.dumps(report_data.get("strengths", []), ensure_ascii=False),
                        json.dumps(
                            report_data.get("areas_for_improvement", []), ensure_ascii=False
                        ),
                        json.dumps(report_data.get("suggestions", []), ensure_ascii=False),
                        json.dumps(report_data.get("usage_summary", {}), ensure_ascii=False),
                        model,
                        report_data.get("raw_response"),
                    ),
                    commit=True,
                )
                return result["id"] if result else None
            else:
                self.db.execute(
                    """
                    INSERT INTO insights_reports
                    (user_id, start_date, end_date, overall_score, overall_assessment,
                     strengths, areas_for_improvement, suggestions, usage_summary,
                     model, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        start_date,
                        end_date,
                        report_data.get("overall_score"),
                        report_data.get("overall_assessment"),
                        json.dumps(report_data.get("strengths", []), ensure_ascii=False),
                        json.dumps(
                            report_data.get("areas_for_improvement", []), ensure_ascii=False
                        ),
                        json.dumps(report_data.get("suggestions", []), ensure_ascii=False),
                        json.dumps(report_data.get("usage_summary", {}), ensure_ascii=False),
                        model,
                        report_data.get("raw_response"),
                    ),
                )
                # Get the last inserted ID for SQLite
                result = self.db.fetch_one("SELECT last_insert_rowid() as id")
                return result["id"] if result else None
        except Exception as e:
            logger.error(f"Error saving insights report: {e}")
            return None

    def get_report(self, user_id: int, start_date: str, end_date: str) -> Optional[dict]:
        """
        Get an existing report for the given user and date range.

        Args:
            user_id: User ID.
            start_date: Start date string.
            end_date: End date string.

        Returns:
            Optional[Dict]: Report record or None.
        """
        query = """
            SELECT * FROM insights_reports
            WHERE user_id = ? AND start_date = ? AND end_date = ?
            ORDER BY created_at DESC
            LIMIT 1
        """
        return self.db.fetch_one(query, (user_id, start_date, end_date))

    def get_user_reports(self, user_id: int, limit: int = 10) -> list[dict]:
        """
        Get user's report history.

        Args:
            user_id: User ID.
            limit: Maximum number of reports to return.

        Returns:
            List[Dict]: List of report records.
        """
        query = """
            SELECT id, start_date, end_date, overall_score, created_at
            FROM insights_reports
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        return self.db.fetch_all(query, (user_id, limit))

    def delete_report(self, report_id: int, user_id: int) -> bool:
        """
        Delete a report (with user ownership verification).

        Args:
            report_id: Report ID.
            user_id: User ID (for ownership verification).

        Returns:
            bool: True if deleted successfully.
        """
        try:
            self.db.execute(
                "DELETE FROM insights_reports WHERE id = ? AND user_id = ?",
                (report_id, user_id),
            )
            return True
        except Exception as e:
            logger.error(f"Error deleting insights report {report_id}: {e}")
            return False

    def get_report_by_id(self, report_id: int, user_id: int) -> Optional[dict]:
        """
        Get a specific report by ID with user ownership verification.

        Args:
            report_id: Report ID.
            user_id: User ID.

        Returns:
            Optional[Dict]: Report record or None.
        """
        query = """
            SELECT * FROM insights_reports
            WHERE id = ? AND user_id = ?
        """
        return self.db.fetch_one(query, (report_id, user_id))
