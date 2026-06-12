"""
Unit tests for ReportGenerator save_report and get_saved_reports methods.

Tests cover:
- save_report returns True on success
- save_report returns False on database error
- get_saved_reports raises exception on database error (not returns empty list)
- Report generation API error handling for save failures
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, patch
import json

from app.modules.compliance.report import (
    ReportGenerator,
    ComplianceReport,
    ReportMetadata,
    ReportType,
)


@pytest.fixture
def sample_report_metadata():
    """Create sample report metadata."""
    return ReportMetadata(
        report_id="test-report-save-001",
        report_type=ReportType.USAGE_SUMMARY.value,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        period_start=datetime(2024, 1, 1),
        period_end=datetime(2024, 1, 31),
        generated_by=1,
    )


@pytest.fixture
def sample_report(sample_report_metadata):
    """Create sample compliance report."""
    return ComplianceReport(
        metadata=sample_report_metadata,
        summary={
            "period": {"start": "2024-01-01", "end": "2024-01-31", "days": 31},
            "totals": {
                "tokens": 10000,
                "requests": 500,
                "tools_used": 3,
                "unique_users": 10,
            },
        },
        details=[
            {
                "date": "2024-01-01",
                "tool_name": "claude",
                "total_requests": 10,
            },
        ],
        compliance_checks=[
            {
                "check_id": "data_retention",
                "name": "Data Retention Policy",
                "status": "pass",
                "message": "Data retention policy is being followed",
            },
        ],
        recommendations=["Test recommendation"],
    )


class TestSaveReport:
    """Test save_report method behavior."""

    def test_save_report_returns_true_on_success(self, sample_report):
        """Test that save_report returns True when database save succeeds."""
        # Mock database connection
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.is_postgresql = False

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.save_report(sample_report)
        assert result is True
        mock_cursor.execute.assert_called()  # Verify SQL executed

    def test_save_report_returns_false_on_database_error(self, sample_report):
        """Test that save_report returns False when database operation fails."""
        # Mock database connection that raises exception
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.is_postgresql = False

        # Simulate database error during insert
        mock_cursor.execute.side_effect = Exception("Database connection failed")

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.save_report(sample_report)
        assert result is False

    def test_save_report_returns_false_on_connection_error(self, sample_report):
        """Test that save_report returns False when database connection fails."""
        # Mock database that fails to connect
        mock_db = Mock()
        mock_db.connection.side_effect = Exception("Failed to connect to database")

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.save_report(sample_report)
        assert result is False

    def test_save_report_creates_table_if_not_exists(self, sample_report):
        """Test that save_report creates table if it doesn't exist."""
        mock_db = Mock()
        mock_conn = MagicMock()
        mock_cursor = Mock()
        mock_db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.is_postgresql = False

        generator = ReportGenerator()
        generator.db = mock_db

        generator.save_report(sample_report)

        # Verify CREATE TABLE IF NOT EXISTS was called
        calls = mock_cursor.execute.call_args_list
        create_table_call = calls[0]
        assert "CREATE TABLE IF NOT EXISTS compliance_reports" in create_table_call[0][0]


class TestGetSavedReports:
    """Test get_saved_reports method behavior."""

    def test_get_saved_reports_returns_list_on_success(self):
        """Test that get_saved_reports returns list when query succeeds."""
        mock_db = Mock()
        mock_rows = [
            {
                "report_id": "report-001",
                "report_type": "usage_summary",
                "generated_at": "2024-01-31 00:00:00",
                "period_start": "2024-01-01 00:00:00",
                "period_end": "2024-01-31 00:00:00",
            },
            {
                "report_id": "report-002",
                "report_type": "usage_summary",
                "generated_at": "2024-02-01 00:00:00",
                "period_start": "2024-01-02 00:00:00",
                "period_end": "2024-02-01 00:00:00",
            },
        ]
        mock_db.fetch_all.return_value = mock_rows

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_reports()
        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_saved_reports_raises_exception_on_database_error(self):
        """Test that get_saved_reports raises exception when database query fails."""
        # This is the KEY test - verify it raises exception, NOT returns empty list
        mock_db = Mock()
        mock_db.fetch_all.side_effect = Exception("Database query failed")

        generator = ReportGenerator()
        generator.db = mock_db

        # Should raise exception, NOT return empty list []
        with pytest.raises(Exception) as exc_info:
            generator.get_saved_reports()

        assert "Database query failed" in str(exc_info.value)

    def test_get_saved_reports_returns_empty_list_when_no_data(self):
        """Test that get_saved_reports returns empty list when query succeeds but no data."""
        mock_db = Mock()
        mock_db.fetch_all.return_value = []  # Query succeeds, returns no data

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_reports()
        assert isinstance(result, list)
        assert len(result) == 0  # Empty list for "no data", not for "error"

    def test_get_saved_reports_filters_by_report_type(self):
        """Test that get_saved_reports filters by report_type."""
        mock_db = Mock()
        mock_rows = [
            {
                "report_id": "report-001",
                "report_type": "usage_summary",
                "generated_at": "2024-01-31 00:00:00",
                "period_start": "2024-01-01 00:00:00",
                "period_end": "2024-01-31 00:00:00",
            },
        ]
        mock_db.fetch_all.return_value = mock_rows

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_reports(report_type="usage_summary")

        # Verify query was called with correct parameters
        call_args = mock_db.fetch_all.call_args
        assert "report_type = ?" in call_args[0][0]
        assert "usage_summary" in call_args[0][1]

    def test_get_saved_reports_filters_by_tenant_id(self):
        """Test that get_saved_reports filters by tenant_id."""
        mock_db = Mock()
        mock_rows = []
        mock_db.fetch_all.return_value = mock_rows

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_reports(tenant_id=123)

        # Verify query was called with correct parameters
        call_args = mock_db.fetch_all.call_args
        assert "tenant_id = ?" in call_args[0][0]
        assert 123 in call_args[0][1]


class TestGetSavedReport:
    """Test get_saved_report method behavior."""

    def test_get_saved_report_returns_report_on_success(self):
        """Test that get_saved_report reconstructs report from saved data."""
        mock_db = Mock()

        # Create a valid report JSON
        report_data = {
            "metadata": {
                "report_id": "report-001",
                "report_type": "usage_summary",
                "generated_at": "2024-01-31T00:00:00",
                "period_start": "2024-01-01T00:00:00",
                "period_end": "2024-01-31T00:00:00",
                "generated_by": 1,
                "tenant_id": None,
                "filters": {},
            },
            "summary": {"totals": {"tokens": 100}},
            "details": [],
            "compliance_checks": [],
            "recommendations": ["Test"],
        }

        mock_db.fetch_one.return_value = {"report_data": json.dumps(report_data)}

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_report("report-001")
        assert result is not None
        assert isinstance(result, ComplianceReport)
        assert result.metadata.report_id == "report-001"

    def test_get_saved_report_returns_none_when_not_found(self):
        """Test that get_saved_report returns None when report not found."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_report("non-existent-id")
        assert result is None

    def test_get_saved_report_returns_none_on_json_parse_error(self):
        """Test that get_saved_report returns None when JSON parsing fails."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"report_data": "invalid json"}

        generator = ReportGenerator()
        generator.db = mock_db

        result = generator.get_saved_report("report-bad-json")
        assert result is None


class TestReportGeneratorErrorHandling:
    """Test that error handling follows the expected pattern."""

    def test_save_report_logs_error_on_failure(self, sample_report):
        """Test that save_report logs error when database operation fails."""
        mock_db = Mock()
        mock_db.connection.side_effect = Exception("Database error")

        generator = ReportGenerator()
        generator.db = mock_db

        with patch("app.modules.compliance.report.logger") as mock_logger:
            result = generator.save_report(sample_report)
            assert result is False
            # Verify error was logged
            mock_logger.error.assert_called()
            assert "Failed to save report" in mock_logger.error.call_args[0][0]

    def test_get_saved_reports_logs_error_on_failure(self):
        """Test that get_saved_reports logs error before raising exception."""
        mock_db = Mock()
        mock_db.fetch_all.side_effect = Exception("Database error")

        generator = ReportGenerator()
        generator.db = mock_db

        with patch("app.modules.compliance.report.logger") as mock_logger:
            with pytest.raises(Exception):
                generator.get_saved_reports()

            # Verify error was logged
            mock_logger.error.assert_called()
            assert "Failed to query saved reports" in mock_logger.error.call_args[0][0]
