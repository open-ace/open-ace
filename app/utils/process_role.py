"""
Open ACE - Process Role Detection

Provides process role detection for distinguishing between scheduler and web processes.

Issue #2820: Enables code to behave differently based on process role.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_process_role() -> str:
    """
    Determine current process role.

    Priority:
    1. SCHEDULER_MODE environment variable (production)
    2. Check if local scheduler is running (development fallback)

    Returns:
        "scheduler" or "web"
    """
    # Check environment variable first (production deployment)
    mode = os.environ.get("SCHEDULER_MODE", "web")
    if mode in ("scheduler", "web"):
        return mode

    # Fallback: check if local scheduler is running (development mode)
    try:
        from app.services.data_fetch_scheduler import scheduler

        if scheduler._running:
            return "scheduler"
    except Exception:
        pass

    return "web"


def is_scheduler_process() -> bool:
    """Check if current process is the scheduler process."""
    return get_process_role() == "scheduler"


def is_web_process() -> bool:
    """Check if current process is a web process."""
    return get_process_role() == "web"


def validate_process_role() -> tuple[bool, str]:
    """
    Validate that the process role matches the actual state.

    Returns:
        Tuple of (is_valid, message)
    """
    mode = os.environ.get("SCHEDULER_MODE", "web")

    if mode == "scheduler":
        # Scheduler process should have local scheduler running
        try:
            from app.services.data_fetch_scheduler import scheduler

            if not scheduler._running:
                return (
                    False,
                    "SCHEDULER_MODE=scheduler but local scheduler is not running. "
                    "This indicates a configuration error.",
                )
        except Exception as e:
            return False, f"SCHEDULER_MODE=scheduler but cannot import scheduler: {e}"

    elif mode == "web":
        # Web process should not have local scheduler running (expected)
        pass

    return True, "Process role validation passed"