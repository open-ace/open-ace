"""
Open ACE - Scheduler Run Status Module

Provides unified status computation for DataFetchScheduler.
Issue #2822: Ensures consistency between in-memory summary and persisted run status.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Environment variable to enable/disable partial status (for rollback)
ENABLE_PARTIAL_STATUS = os.environ.get("ENABLE_PARTIAL_STATUS", "true").lower() == "true"

# Status constants
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Valid status values
VALID_STATUSES = {STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED, STATUS_SKIPPED}


def validate_status(status: str) -> bool:
    """Validate that a status string is recognized.

    Args:
        status: Status string to validate.

    Returns:
        True if valid, False otherwise.
    """
    return status in VALID_STATUSES


def _make_structured_error_message(
    tools_total: int,
    tools_failed: int,
    failed_tools: list[str],
) -> str:
    """Create a structured JSON error message for partial failures.

    Args:
        tools_total: Total number of tools executed.
        tools_failed: Number of tools that failed.
        failed_tools: List of tool names that failed.

    Returns:
        JSON string with structured error information.
    """
    message = f"Partial failure: {', '.join(failed_tools)}"
    error_data = {
        "type": "partial_failure",
        "tools_total": tools_total,
        "tools_failed": tools_failed,
        "failed_tools": failed_tools,
        "message": message,
    }
    return json.dumps(error_data)


def compute_data_fetch_status(
    results: dict[str, Any] | None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Compute data fetch run status from results.

    This function provides a unified status computation logic to ensure
    consistency between the in-memory summary (_last_result_summary) and
    the persisted run status (record_run).

    Args:
        results: Results from run_fetch_scripts(). None indicates an exception
            was raised. An empty dict {} indicates no scripts were available.

    Returns:
        A tuple of (status, error_message, result_summary):
        - status: "completed", "partial", "failed", or "skipped"
        - error_message: Human-readable error description (structured JSON for partial)
        - result_summary: Dict with status, tools_total, tools_failed, failed_tools

    Note:
        When ENABLE_PARTIAL_STATUS=false (rollback mode), the persisted status
        is "completed" but the memory summary still contains "partial" to ensure
        API consumers get accurate information.
    """
    # Handle None results (exception during fetch)
    if results is None:
        status = STATUS_FAILED
        error_message = "Data fetch encountered an unexpected error"
        result_summary = {
            "status": STATUS_FAILED,
            "error": "unexpected_error",
        }
        return status, error_message, result_summary

    # Handle skipped results (concurrent fetch already running)
    if isinstance(results, dict) and results.get("_skipped"):
        status = STATUS_SKIPPED
        error_message = "Concurrent data fetch already running"
        result_summary = {"status": STATUS_SKIPPED}
        return status, error_message, result_summary

    # Handle empty results (no scripts available)
    if not results:
        status = STATUS_COMPLETED
        error_message = None
        result_summary = {
            "status": STATUS_COMPLETED,
            "tools_total": 0,
            "tools_failed": 0,
        }
        return status, error_message, result_summary

    # Check per-tool results
    failed_tools = [k for k, v in results.items() if not v.get("success", False)]
    tools_total = len(results)
    tools_failed = len(failed_tools)

    # All tools failed
    if tools_failed == tools_total:
        status = STATUS_FAILED
        error_message = "All fetch scripts failed"
        result_summary = {
            "status": STATUS_FAILED,
            "tools_total": tools_total,
            "tools_failed": tools_failed,
            "failed_tools": failed_tools,
        }
        return status, error_message, result_summary

    # Partial failure (some succeeded, some failed)
    if failed_tools:
        # Memory summary always shows partial for accuracy
        memory_status = STATUS_PARTIAL
        memory_error = _make_structured_error_message(tools_total, tools_failed, failed_tools)

        # Persisted status depends on rollback switch
        if ENABLE_PARTIAL_STATUS:
            persist_status = STATUS_PARTIAL
            persist_error = memory_error
        else:
            # Rollback mode: persist as completed but keep memory accurate
            persist_status = STATUS_COMPLETED
            persist_error = memory_error

        result_summary = {
            "status": memory_status,  # Always accurate in memory
            "tools_total": tools_total,
            "tools_failed": tools_failed,
            "failed_tools": failed_tools,
        }
        return persist_status, persist_error, result_summary

    # All tools succeeded
    status = STATUS_COMPLETED
    error_message = None
    result_summary = {
        "status": STATUS_COMPLETED,
        "tools_total": tools_total,
        "tools_failed": 0,
    }
    return status, error_message, result_summary