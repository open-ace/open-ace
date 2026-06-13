"""
Open ACE - Autonomous Development Scheduler

Background daemon thread that drives autonomous workflows forward.
Uses ThreadPoolExecutor for concurrent workflow processing.
Follows the same singleton pattern as DataFetchScheduler.
"""

from __future__ import annotations

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

logger = logging.getLogger(__name__)

# Maximum concurrent workflow executions
MAX_CONCURRENT_WORKFLOWS = 3
RUNNING_BATCH_STATUSES = {
    "pending",
    "preparing",
    "planning",
    "developing",
    "pr_review",
    "reporting",
    "merging",
}
QUEUE_ADVANCE_STATUSES = {"waiting", "completed", "failed", "planning_timeout"}
QUEUE_BLOCKING_STATUSES = {"paused", "cancelled"}


class AutonomousScheduler:
    """Singleton scheduler that advances autonomous workflows."""

    _instance: AutonomousScheduler | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._in_progress_ids: set[str] = set()
        self._in_progress_batch_ids: set[str] = set()  # Track batches being processed
        self._in_progress_paths: set[str] = set()  # Track project paths being processed
        self._in_progress_lock = threading.Lock()
        self._running_orchestrators: dict[str, AutonomousOrchestrator] = {}
        self._orchestrator_lock = threading.Lock()

    @classmethod
    def instance(cls) -> AutonomousScheduler:
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

    def get_running_orchestrator(self, workflow_id: str):
        """Get the currently running orchestrator for a workflow, if any."""
        with self._orchestrator_lock:
            return self._running_orchestrators.get(workflow_id)

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
        from app.routes.autonomous import _get_repo

        # Unique lock owner: hostname + thread name
        lock_owner = f"{socket.gethostname()}/{threading.current_thread().name}"
        repo = _get_repo()

        # Get workflow's batch_id and project_path for cleanup
        workflow = repo.get_workflow(workflow_id)
        batch_id = workflow.get("batch_id") if workflow else None
        project_path = workflow.get("project_path", "") if workflow else ""

        # Acquire DB-level distributed lock
        if not repo.acquire_lock(workflow_id, lock_owner):
            logger.debug("Workflow %s is locked by another instance, skipping", workflow_id[:8])
            with self._in_progress_lock:
                self._in_progress_ids.discard(workflow_id)
                if batch_id:
                    self._in_progress_batch_ids.discard(batch_id)
                if project_path:
                    self._in_progress_paths.discard(project_path)
            return workflow_id

        orchestrator = None
        try:
            orchestrator = AutonomousOrchestrator(workflow_id)
            with self._orchestrator_lock:
                self._running_orchestrators[workflow_id] = orchestrator
            orchestrator.advance()
        except Exception as e:
            logger.error(
                "Failed to advance workflow %s: %s",
                workflow_id[:8],
                e,
                exc_info=True,
            )
        finally:
            with self._orchestrator_lock:
                self._running_orchestrators.pop(workflow_id, None)
            # Safety net: clear stale agent_pid if orchestrator failed to clean up
            try:
                wf_check = repo.get_workflow(workflow_id)
                if wf_check and wf_check.get("agent_pid"):
                    repo.update_workflow(
                        workflow_id,
                        {
                            "agent_pid": None,
                            "agent_session_id": "",
                        },
                    )
            except Exception:
                pass
            # Release DB lock
            try:
                repo.release_lock(workflow_id, lock_owner)
            except Exception:
                logger.warning("Failed to release lock for workflow %s", workflow_id[:8])
            with self._in_progress_lock:
                self._in_progress_ids.discard(workflow_id)
                if batch_id:
                    self._in_progress_batch_ids.discard(batch_id)
                if project_path:
                    self._in_progress_paths.discard(project_path)
        return workflow_id

    @staticmethod
    def _batch_has_running_workflow(batch_workflows: list[dict]) -> bool:
        """Whether a batch currently has a workflow actively executing."""
        return any(wf.get("status") in RUNNING_BATCH_STATUSES for wf in batch_workflows)

    def _promote_queued_workflows(self, repo) -> None:
        """Promote the next queued workflow in each eligible batch."""
        from app.routes.autonomous import _emit_event_safe

        try:
            queued_workflows = repo.get_queued_workflows()
        except Exception as e:
            logger.error("Failed to query queued workflows: %s", e)
            return

        seen_batches: set[str] = set()
        for workflow in queued_workflows:
            batch_id = workflow.get("batch_id") or ""
            if not batch_id or batch_id in seen_batches:
                continue
            seen_batches.add(batch_id)

            batch_workflows = repo.list_batch_workflows(batch_id)
            if not batch_workflows or self._batch_has_running_workflow(batch_workflows):
                continue

            queued_index = next(
                (
                    index
                    for index, item in enumerate(batch_workflows)
                    if item.get("workflow_id") == workflow.get("workflow_id")
                ),
                None,
            )
            if queued_index is None:
                continue
            if queued_index == 0:
                repo.update_workflow(workflow["workflow_id"], {"status": "pending"})
                _emit_event_safe(workflow["workflow_id"], "status_change", {"status": "pending"})
                continue

            previous_workflow = batch_workflows[queued_index - 1]
            previous_status = previous_workflow.get("status")
            if previous_status in QUEUE_BLOCKING_STATUSES or previous_status == "queued":
                continue
            if previous_status not in QUEUE_ADVANCE_STATUSES:
                continue

            repo.update_workflow(workflow["workflow_id"], {"status": "pending"})
            _emit_event_safe(workflow["workflow_id"], "status_change", {"status": "pending"})

    def _process_workflows(self):
        """Find and process active workflows using thread pool for concurrency.

        For batch workflows, ensures only one workflow per batch is processed at a time.
        Additionally, ensures only one workflow per project_path is processed at a time
        to prevent git conflicts when multiple workflows share the same project directory.
        """
        from app.routes.autonomous import _get_repo

        repo = _get_repo()
        self._promote_queued_workflows(repo)

        try:
            workflows = repo.get_active_workflows()
        except Exception as e:
            logger.error("Failed to query active workflows: %s", e)
            return

        # Filter out paused, already-in-progress workflows, batch workflows
        # whose batch is already being processed, and workflows whose
        # project_path is already being processed by another workflow
        with self._in_progress_lock:
            active = []
            for wf in workflows:
                if wf.get("status") == "paused":
                    continue
                if wf.get("workflow_id", "") in self._in_progress_ids:
                    continue
                # For batch workflows, check if the batch is already being processed
                batch_id = wf.get("batch_id")
                if batch_id and batch_id in self._in_progress_batch_ids:
                    continue
                # Check if the project_path is already being processed
                project_path = wf.get("project_path", "")
                if project_path and project_path in self._in_progress_paths:
                    continue
                active.append(wf)

        if not active:
            return

        active.sort(
            key=lambda wf: (
                1 if wf.get("status") == "waiting" else 0,
                wf.get("created_at") or "",
            )
        )

        # Limit to concurrency cap, accounting for already-running workflows
        with self._in_progress_lock:
            slots_available = MAX_CONCURRENT_WORKFLOWS - len(self._in_progress_ids)
        to_process = active[: max(0, slots_available)]

        if not to_process:
            return

        # Mark workflows, their batches, and project_paths as in-progress
        with self._in_progress_lock:
            for wf in to_process:
                self._in_progress_ids.add(wf.get("workflow_id", ""))
                batch_id = wf.get("batch_id")
                if batch_id:
                    self._in_progress_batch_ids.add(batch_id)
                project_path = wf.get("project_path", "")
                if project_path:
                    self._in_progress_paths.add(project_path)

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


def _cleanup_orphan_processes():
    """Kill orphaned agent processes from previous server runs.

    Scans DB for workflows with a non-null agent_pid and active status,
    kills those processes, and resets the workflow status to paused.
    """
    import os
    import signal
    import time
    from datetime import datetime, timezone

    logger.info("Checking for orphaned agent processes...")

    try:
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository
        from app.repositories.database import Database

        repo = AutonomousWorkflowRepository(Database())
        workflows = repo.get_workflows_with_active_pid()

        if not workflows:
            logger.info("No orphaned processes found")
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cleaned = 0
        for wf in workflows:
            pid = wf.get("agent_pid")
            if not pid or not isinstance(pid, int):
                continue

            # Check if process still exists
            try:
                os.kill(pid, 0)  # signal 0 = existence check
            except (ProcessLookupError, OSError):
                # Process already dead, just clean up DB
                repo.update_workflow(
                    wf["workflow_id"],
                    {
                        "agent_pid": None,
                        "agent_session_id": "",
                    },
                )
                logger.info(
                    "Cleaned up stale PID %d for workflow %s (process already dead)",
                    pid,
                    wf["workflow_id"][:8],
                )
                continue

            # Process is still alive — kill it
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.killpg(pgid, 0)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                cleaned += 1
                logger.warning(
                    "Killed orphan process PID=%d for workflow %s (status=%s)",
                    pid,
                    wf["workflow_id"][:8],
                    wf.get("status"),
                )
            except (ProcessLookupError, OSError) as e:
                logger.info("Orphan PID %d already gone: %s", pid, e)

            # Reset workflow to paused (safe default, user can resume)
            repo.update_workflow(
                wf["workflow_id"],
                {
                    "agent_pid": None,
                    "agent_session_id": "",
                    "status": "paused",
                    "paused_at": now,
                },
            )

        if cleaned:
            logger.info("Cleaned up %d orphaned agent processes", cleaned)
    except Exception as e:
        logger.error("Orphan process cleanup failed: %s", e, exc_info=True)


def init_autonomous_scheduler():
    """Initialize and start the autonomous scheduler."""
    # Clean up orphaned processes from previous server run
    _cleanup_orphan_processes()

    scheduler = AutonomousScheduler.instance()
    scheduler.start()
