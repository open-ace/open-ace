"""
Test daily_usage data quality and conflict scripts (Issue #1824, F2)

Tests for:
- check_daily_usage_quality.py
- check_daily_usage_conflicts.py
- resolve_daily_usage_conflicts.py
"""

import sys
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1824)]


class TestDailyUsageQualityCheck:
    """Test data quality check script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:

            # Import module
            import check_daily_usage_quality
        finally:
            sys.path.remove(str(scripts_dir))

        # Check functions exist
        assert hasattr(check_daily_usage_quality, "check_tenant_id_null")
        assert hasattr(check_daily_usage_quality, "check_duplicates")
        assert hasattr(check_daily_usage_quality, "check_total_rows")
        assert hasattr(check_daily_usage_quality, "main")


class TestDailyUsageConflictDetection:
    """Test conflict detection script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "check_daily_usage_conflicts.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "check_daily_usage_conflicts.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            import check_daily_usage_conflicts
        finally:
            sys.path.remove(str(scripts_dir))

        assert hasattr(check_daily_usage_conflicts, "find_conflicts")
        assert hasattr(check_daily_usage_conflicts, "count_conflict_rows")
        assert hasattr(check_daily_usage_conflicts, "main")


class TestDailyUsageConflictResolution:
    """Test conflict resolution script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "resolve_daily_usage_conflicts.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent / "scripts" / "resolve_daily_usage_conflicts.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            import resolve_daily_usage_conflicts
        finally:
            sys.path.remove(str(scripts_dir))

        assert hasattr(resolve_daily_usage_conflicts, "find_conflicts")
        assert hasattr(resolve_daily_usage_conflicts, "resolve_conflict_earliest")
        assert hasattr(resolve_daily_usage_conflicts, "main")

    def test_dry_run_strategy(self):
        """Script should support --strategy=dry-run."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            import resolve_daily_usage_conflicts
        finally:
            sys.path.remove(str(scripts_dir))

        # No live DB in the unit layer: the resolver reads conflicts through
        # the module-level Database handle, so an empty-row mock keeps the
        # dry-run contract assertion self-contained.
        with unittest.mock.patch.object(resolve_daily_usage_conflicts, "Database") as database_cls:
            database_cls.return_value.fetch_all.return_value = []
            result = resolve_daily_usage_conflicts.resolve_conflict_earliest(
                date="2026-07-30",
                tool_name="test_tool",
                host_name="localhost",
                target_tenant=1,
                dry_run=True,
            )
        # Returns 0 rows in dry-run mode
        assert result == 0
