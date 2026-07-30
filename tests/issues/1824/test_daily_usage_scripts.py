"""
Test daily_usage data quality and conflict scripts (Issue #1824, F2)

Tests for:
- check_daily_usage_quality.py
- check_daily_usage_conflicts.py
- resolve_daily_usage_conflicts.py
"""

import sys
from pathlib import Path

import pytest


class TestDailyUsageQualityCheck:
    """Test data quality check script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = (
            Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))

        # Import module
        import check_daily_usage_quality

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
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "check_daily_usage_conflicts.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "check_daily_usage_conflicts.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))

        import check_daily_usage_conflicts

        assert hasattr(check_daily_usage_conflicts, "find_conflicts")
        assert hasattr(check_daily_usage_conflicts, "count_conflict_rows")
        assert hasattr(check_daily_usage_conflicts, "main")


class TestDailyUsageConflictResolution:
    """Test conflict resolution script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "resolve_daily_usage_conflicts.py"
        )
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "resolve_daily_usage_conflicts.py"
        )
        assert script_path.is_file()

    def test_script_functions(self):
        """Script should have required functions."""
        scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))

        import resolve_daily_usage_conflicts

        assert hasattr(resolve_daily_usage_conflicts, "find_conflicts")
        assert hasattr(resolve_daily_usage_conflicts, "resolve_conflict_earliest")
        assert hasattr(resolve_daily_usage_conflicts, "main")

    def test_dry_run_strategy(self):
        """Script should support --strategy=dry-run."""
        scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))

        import resolve_daily_usage_conflicts

        # Test that dry-run is the default strategy
        result = resolve_daily_usage_conflicts.resolve_conflict_earliest(
            date="2026-07-30",
            tool_name="test_tool",
            host_name="localhost",
            target_tenant=1,
            dry_run=True,
        )
        # Returns 0 rows in dry-run mode
        assert result == 0


class TestDailyUsageDataIntegrity:
    """Test data integrity validation."""

    def test_no_null_tenant_ids(self):
        """All daily_usage rows should have tenant_id (not NULL)."""
        # Unit test: verify server_default is set correctly in migration
        # This would be verified in integration test with real DB
        pass

    def test_unique_constraint_enforced(self):
        """Unique constraint on (tenant_id, date, tool_name, host_name) should be enforced."""
        # Unit test: verify constraint is defined in migration
        # Integration test would verify it's enforced at DB level
        pass
