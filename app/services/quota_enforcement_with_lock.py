"""
Wrapper for quota enforcement scheduler with distributed lock (Issue #2187).

This module provides a distributed lock wrapper for quota enforcement.
"""

import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def run_enforcement_with_lock(scheduler_instance):
    """Run quota enforcement with distributed lock.

    Args:
        scheduler_instance: QuotaEnforcementScheduler instance
    """
    from app.repositories.database import Database
    from app.services.leader_election import LeaderElectionClient

    # Acquire distributed lock
    db = Database()
    client = LeaderElectionClient("quota_enforcement", db, strategy="heartbeat", lock_timeout=1800)

    if not client.try_acquire_leadership():
        logger.info("Quota enforcement skipped - not leader")
        client.record_run("skipped")
        return

    start_time = time.time()
    status = "completed"
    error_message = None

    try:
        # Call original enforcement logic
        scheduler_instance._run_enforcement_original()
    except Exception as e:
        status = "failed"
        error_message = str(e)
        logger.error(f"Quota enforcement check failed: {e}")

    # Record execution
    duration_ms = int((time.time() - start_time) * 1000)
    client.record_run(status, duration_ms, error_message)
    client.release_leadership()