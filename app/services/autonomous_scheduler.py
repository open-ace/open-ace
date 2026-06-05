"""
Open ACE - Autonomous Development Scheduler

Background daemon thread that drives autonomous workflows forward.
Follows the same singleton pattern as DataFetchScheduler and
QuotaEnforcementScheduler.
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum concurrent workflow executions
MAX_CONCURRENT_WORKFLOWS = 3


class AutonomousScheduler:
    """Singleton scheduler that advances autonomous workflows."""

    _instance: Optional["AutonomousScheduler"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._execution_lock = threading.Lock()
        self._active_count = 0

    @classmethod
    def instance(cls) -> "AutonomousScheduler":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start(self):
        """Start the scheduler daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="autonomous-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Autonomous scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Autonomous scheduler stopped")

    def _run_loop(self):
        """Main loop: poll for active workflows and advance them."""
        while not self._stop_event.is_set():
            try:
                self._process_workflows()
            except Exception as e:
                logger.error("Scheduler error: %s", e, exc_info=True)

            # Wait 10 seconds between checks (or stop signal)
            self._stop_event.wait(10)

    def _process_workflows(self):
        """Find and process active workflows."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository()

        try:
            workflows = repo.get_active_workflows()
        except Exception as e:
            logger.error("Failed to query active workflows: %s", e)
            return

        # Filter out paused workflows
        active = [wf for wf in workflows if wf.get("status") != "paused"]

        if not active:
            return

        # Process workflows up to concurrency limit
        processed = 0
        for wf in active:
            if processed >= MAX_CONCURRENT_WORKFLOWS:
                break

            workflow_id = wf.get("workflow_id", "")
            try:
                orchestrator = AutonomousOrchestrator(workflow_id)
                orchestrator.advance()
                processed += 1
            except Exception as e:
                logger.error(
                    "Failed to advance workflow %s: %s",
                    workflow_id[:8],
                    e,
                    exc_info=True,
                )


def init_autonomous_scheduler():
    """Initialize and start the autonomous scheduler."""
    scheduler = AutonomousScheduler.instance()
    scheduler.start()
