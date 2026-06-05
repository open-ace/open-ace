"""
Open ACE - Autonomous Development Scheduler

Background daemon thread that drives autonomous workflows forward.
Uses ThreadPoolExecutor for concurrent workflow processing.
Follows the same singleton pattern as DataFetchScheduler.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self._in_progress_ids: set[str] = set()
        self._in_progress_lock = threading.Lock()

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

    def _advance_single(self, workflow_id: str) -> str:
        """Advance a single workflow. Returns workflow_id for tracking."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        try:
            orchestrator = AutonomousOrchestrator(workflow_id)
            orchestrator.advance()
        except Exception as e:
            logger.error(
                "Failed to advance workflow %s: %s",
                workflow_id[:8],
                e,
                exc_info=True,
            )
        finally:
            with self._in_progress_lock:
                self._in_progress_ids.discard(workflow_id)
        return workflow_id

    def _process_workflows(self):
        """Find and process active workflows using thread pool for concurrency."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository()

        try:
            workflows = repo.get_active_workflows()
        except Exception as e:
            logger.error("Failed to query active workflows: %s", e)
            return

        # Filter out paused and already-in-progress workflows
        with self._in_progress_lock:
            active = [
                wf
                for wf in workflows
                if wf.get("status") != "paused"
                and wf.get("workflow_id", "") not in self._in_progress_ids
            ]

        if not active:
            return

        # Limit to concurrency cap, accounting for already-running workflows
        with self._in_progress_lock:
            slots_available = MAX_CONCURRENT_WORKFLOWS - len(self._in_progress_ids)
        to_process = active[: max(0, slots_available)]

        if not to_process:
            return

        # Mark as in-progress and submit
        with self._in_progress_lock:
            for wf in to_process:
                self._in_progress_ids.add(wf.get("workflow_id", ""))

        with ThreadPoolExecutor(
            max_workers=min(MAX_CONCURRENT_WORKFLOWS, len(to_process)),
            thread_name_prefix="auto-wf",
        ) as executor:
            futures = {
                executor.submit(self._advance_single, wf.get("workflow_id", "")): wf
                for wf in to_process
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    wf = futures[future]
                    logger.error(
                        "Workflow %s future error: %s",
                        wf.get("workflow_id", "")[:8],
                        e,
                    )


def init_autonomous_scheduler():
    """Initialize and start the autonomous scheduler."""
    scheduler = AutonomousScheduler.instance()
    scheduler.start()
