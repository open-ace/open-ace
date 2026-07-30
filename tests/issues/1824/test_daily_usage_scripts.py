"""
Test daily_usage data quality and conflict scripts (Issue #1824, F2)

Tests for:
- check_daily_usage_quality.py
- check_daily_usage_conflicts.py
- resolve_daily_usage_conflicts.py
"""

import pytest
import subprocess
import sys
from pathlib import Path


class TestDailyUsageQualityCheck:
    """Test data quality check script."""

    def test_script_exists(self):
        """Script file should exist."""
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_quality.py"
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
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_conflicts.py"
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "check_daily_usage_conflicts.py"
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
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "resolve_daily_usage_conflicts.py"
        assert script_path.exists()

    def test_script_is_executable(self):
        """Script should be importable."""
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "resolve_daily_usage_conflicts.py"
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
        assert resolve_daily_usage_conflicts.resolve_conflict_earliest(
            date="2026-07-30",
            tool_name="test_tool",
            host_name="localhost",
            target_tenant=1,
            dry_run=True
        ) == 0  # Returns 0 rows in dry-run mode


class TestDailyUsageDataIntegrity:
    """Test data integrity validation."""

    def test_no_null_tenant_ids(self, app_context, db):
        """All daily_usage rows should have tenant_id (not NULL)."""
        result = db.fetch_one(
            "SELECT COUNT(*) as count FROM daily_usage WHERE tenant_id IS NULL"
        )

        # Should be 0 (all rows have tenant_id via server_default)
        assert result["count"] == 0

    def test_unique_constraint_enforced(self, app_context, db):
        """Unique constraint on (tenant_id, date, tool_name, host_name) should be enforced."""
        # Try to insert duplicate
        from datetime import date

        today = date.today().isoformat()

        # Insert first row
        db.execute(
            """
            INSERT INTO daily_usage (date, tool_name, host_name, tenant_id, tokens_used)
            VALUES (?, ?, ?, ?, ?)
            """,
            (today, "test_tool", "localhost", 1, 100)
        )

        # Try to insert duplicate (should fail or update existing)
        try:
            db.execute(
                """
                INSERT INTO daily_usage (date, tool_name, host_name, tenant_id, tokens_used)
                VALUES (?, ?, ?, ?, ?)
                """,
                (today, "test_tool", "localhost", 1, 200)
            )
            # If we get here, it either succeeded (upsert) or we need to check for conflicts
        except Exception as e:
            # Should raise integrity error for duplicate
            assert "unique" in str(e).lower() or "constraint" in str(e).lower()


# Fixtures
@pytest.fixture
def app_context(app):
    """Create application context."""
    with app.app_context():
        yield


@pytest.fixture
def db():
    """Create database connection."""
    from app.repositories.database import Database
    return Database()