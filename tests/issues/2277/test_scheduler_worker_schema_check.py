"""scheduler_worker._check_schema_version must use SchemaCompatibilityService (#2330).

Issue #2330: scheduler_worker now uses SchemaCompatibilityService directly
instead of check_min_revision.main(). These tests verify that the service
correctly passes/fails compatibility checks.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# scheduler_worker.py has a module-level guard that sys.exit(1)s unless
# SCHEDULER_MODE=="scheduler". Set it before the deferred import in _run_check.
os.environ["SCHEDULER_MODE"] = "scheduler"


def _run_check() -> None:
    """Invoke _check_schema_version without running SchedulerWorker.__init__.

    The method only imports the check module and logs/exits — it touches no
    instance state — so an un-initialized instance is sufficient and avoids
    the heavy app-context construction of the full worker.
    """
    from app.scheduler_worker import SchedulerWorker

    instance = SchedulerWorker.__new__(SchedulerWorker)
    SchedulerWorker._check_schema_version(instance)


def test_schema_check_passes_when_compatible():
    """Compatible database → no exit, no raise."""
    # Mock SchemaCompatibilityService to return compatible result
    mock_result = MagicMock()
    mock_result.is_compatible = True
    mock_result.bypass_active = False
    mock_result.current_heads = ["test_revision"]
    mock_result.expected_head = "test_revision"

    with patch(
        "app.services.schema_compatibility_service.get_schema_compatibility_service"
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.check_database_compatibility.return_value = mock_result
        mock_get_service.return_value = mock_service

        _run_check()  # should not raise


def test_schema_check_exits_when_incompatible():
    """Incompatible database → SystemExit(1)."""
    from app.services.schema_compatibility_types import SchemaErrorCategory

    # Mock SchemaCompatibilityService to return incompatible result
    mock_result = MagicMock()
    mock_result.is_compatible = False
    mock_result.bypass_active = False
    mock_result.error_category = SchemaErrorCategory.BEHIND_HEAD
    mock_result.diagnostic_message = "Database schema is behind head"
    mock_result.missing_migrations = ["migration_1"]

    with patch(
        "app.services.schema_compatibility_service.get_schema_compatibility_service"
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.check_database_compatibility.return_value = mock_result
        mock_get_service.return_value = mock_service

        with pytest.raises(SystemExit) as exc_info:
            _run_check()
        assert exc_info.value.code == 1


def test_schema_check_passes_with_bypass():
    """Bypass active → no exit, continues with warning."""
    # Mock SchemaCompatibilityService to return bypass result
    mock_result = MagicMock()
    mock_result.is_compatible = False  # Incompatible but bypassed
    mock_result.bypass_active = True
    mock_result.bypass_reason = "Emergency bypass"

    with patch(
        "app.services.schema_compatibility_service.get_schema_compatibility_service"
    ) as mock_get_service:
        mock_service = MagicMock()
        mock_service.check_database_compatibility.return_value = mock_result
        mock_get_service.return_value = mock_service

        _run_check()  # should not raise due to bypass