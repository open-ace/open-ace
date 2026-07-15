"""
Open ACE - Autonomous Development Scheduler

Background daemon thread that drives autonomous workflows forward.
Uses ThreadPoolExecutor for concurrent workflow processing.
Follows the same singleton pattern as DataFetchScheduler.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

logger = logging.getLogger(__name__)

# Maximum concurrent workflow executions
MAX_CONCURRENT_WORKFLOWS = 3

# Active workflow statuses for user concurrent limit check.
# Includes 'waiting' because waiting workflows still occupy user's active slots.
ACTIVE_WORKFLOW_STATUSES = {
    "pending",
    "preparing",
    "planning",
    "developing",
    "pr_review",
    "reporting",
    "waiting",
    "merging",
}

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

# Prefix written to error_message when a workflow is paused because its owner
# exceeded quota. The scheduler auto-resumes only workflows paused with this
# prefix — a user's manual pause (error_message empty / different text) is left
# untouched. Autonomous agents bypass the LLM proxy (local mode connects to the
# model API directly), so the proxy's 429 can't enforce quota for them; the
# scheduler gate below is the enforcement point.
QUOTA_PAUSE_REASON_PREFIX = "Quota exceeded"


def _is_quota_paused(wf: dict) -> bool:
    """Whether a paused workflow was paused by the quota gate.

    Distinguishes quota-paused (auto-resumable) from a user's manual pause
    (must stay paused until the user resumes). Uses the ``error_message``
    prefix so no new DB column / migration is needed; the message is already
    rendered by the timeline banner.
    """
    return wf.get("status") == "paused" and (wf.get("error_message") or "").startswith(
        QUOTA_PAUSE_REASON_PREFIX
    )


class AutonomousScheduler:
    """Singleton scheduler that advances autonomous workflows."""

    _instance: AutonomousScheduler | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._in_progress_ids: set[str] = set()
        self._in_progress_batch_ids: set[str] = set()  # Track batches being processed
        # Git-conflict keys being processed. workspace = worktree_path (fork /
        # worktree strategy — isolated git working tree) else project_path
        # (new-branch / same-branch — shares the main repo dir). branch tracks
        # branch_name so batch workflows sharing a branch still serialize.
        # See #1002: deduping on project_path alone starved forked children.
        self._in_progress_workspaces: set[str] = set()
        self._in_progress_branches: set[str] = set()
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

    @staticmethod
    def _conflict_keys(wf: dict) -> tuple[str, str]:
        """Git-conflict identity for a workflow: ``(workspace, branch)``.

        ``workspace`` is the actual git working tree the workflow mutates:
        ``worktree_path`` when set (fork / ``worktree`` strategy — an isolated
        worktree) else ``project_path`` (``new-branch`` / ``same-branch`` — the
        main repo dir, shared). Two workflows conflict if they share a
        workspace OR a non-empty branch (git forbids the same branch in two
        worktrees, and batch workflows can be assigned the same branch). #1002.

        Issue #1573: For workflows in preparation phase without a branch_name,
        use a temporary key based on workflow_id to ensure conflict checking works
        even before preparation creates the branch.
        """
        workspace = wf.get("worktree_path") or wf.get("project_path") or ""
        branch = wf.get("branch_name") or ""

        # Fallback: if branch is empty and workflow is in preparation phase,
        # use workflow_id as temporary key to ensure conflict checking works.
        # This prevents multiple preparation-phase workflows from running concurrently.
        if not branch and wf.get("current_phase") == "preparation":
            wf_id = wf.get("workflow_id", "")
            if wf_id:
                branch = f"preparation-{wf_id[:8]}"

        return workspace, branch

    def _reclaim_paused_slots(self, repo) -> None:
        """Release git-conflict keys held by workflows that have since been paused.

        Pausing SIGSTOPs the agent but leaves its orchestrator's ``advance()``
        blocked on the frozen process, so the ``finally`` that clears its
        workspace/branch/batch keys never runs. A forked child sharing the
        parent's ``project_path`` is then starved indefinitely (#1002).

        We release the paused workflow's *conflict keys* (so the fork can run)
        but keep its ``workflow_id`` in ``_in_progress_ids`` — that prevents the
        scheduler from double-advancing it on resume (the in-flight advance()
        owns the resumption). The frozen parent does no git work, so concurrent
        fork execution is safe.

        Caveat — resume window: ``resume_workflow`` only SIGCONTs + flips
        status; it does NOT re-acquire the workspace/branch key. Between resume
        and the in-flight ``advance()`` returning, the parent's workspace key is
        absent from the set. If a *new-branch* workflow sharing the parent's
        ``project_path`` starts in that window it could run concurrently and
        race on the main repo dir. Forks (separate worktree) are unaffected.
        The window is bounded by the agent finishing its resumed work, and in
        practice the parent's own ``advance()`` re-checks git state on resume.
        Acceptable for now; flagged in #1002 review.
        """
        with self._in_progress_lock:
            if not self._in_progress_ids:
                return
            ids_snapshot = list(self._in_progress_ids)

        paused: list[dict] = []
        for wid in ids_snapshot:
            try:
                w = repo.get_workflow(wid)
            except Exception:
                w = None
            if w and w.get("status") == "paused":
                paused.append(w)

        if not paused:
            return

        with self._in_progress_lock:
            for w in paused:
                # NOTE: deliberately keep workflow_id in _in_progress_ids to
                # block a double-advance race when the frozen agent is resumed.
                workspace, branch = self._conflict_keys(w)
                if workspace:
                    self._in_progress_workspaces.discard(workspace)
                if branch:
                    self._in_progress_branches.discard(branch)
                batch_id = w.get("batch_id")
                if batch_id:
                    self._in_progress_batch_ids.discard(batch_id)
        logger.info(
            "Reclaimed git-conflict slots for paused workflows: %s",
            [w.get("workflow_id", "")[:8] for w in paused],
        )

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

        # Get workflow's batch_id and git-conflict keys for cleanup
        workflow = repo.get_workflow(workflow_id)
        batch_id = workflow.get("batch_id") if workflow else None
        workspace, branch = self._conflict_keys(workflow) if workflow else ("", "")

        # Acquire DB-level distributed lock
        if not repo.acquire_lock(workflow_id, lock_owner):
            logger.debug("Workflow %s is locked by another instance, skipping", workflow_id[:8])
            with self._in_progress_lock:
                self._in_progress_ids.discard(workflow_id)
                if batch_id:
                    self._in_progress_batch_ids.discard(batch_id)
                if workspace:
                    self._in_progress_workspaces.discard(workspace)
                if branch:
                    self._in_progress_branches.discard(branch)
            return workflow_id

        # Quota gate (fail-closed): a user over quota (or whose quota check
        # errored) must not advance. Local autonomous agents bypass the LLM
        # proxy, so this scheduler gate — not the proxy's 429 — is what bounds
        # them.
        #
        # Enforcement granularity is between advance() cycles, not mid-step:
        # while an agent is mid-flight, advance() is blocked and workflow_id
        # stays in _in_progress_ids, so _process_workflows won't re-enter here
        # until that advance() returns (its finally has already torn the
        # orchestrator down). A local agent call can't be metered mid-flight,
        # so when this gate trips it pauses before the *next* advance — the
        # already-running step completes (bounded by its step timeout) first.
        # NB: this lives INSIDE the try/finally below so the early-return paths
        # still release the DB lock and in-progress slot — otherwise a
        # quota-paused workflow would hold both forever.
        orchestrator = None
        try:
            owner_id = workflow.get("user_id") if workflow else None
            if owner_id is not None:
                try:
                    from app.modules.governance.quota_manager import QuotaManager

                    quota_result = QuotaManager().check_quota(int(owner_id))
                    if not quota_result["allowed"]:
                        self._pause_for_quota(
                            repo, workflow_id, quota_result["reason"] or "Quota exceeded"
                        )
                        return workflow_id
                except Exception as exc:
                    logger.error(
                        "Quota pre-check failed (fail-closed), pausing %s: %s",
                        workflow_id[:8],
                        exc,
                    )
                    self._pause_for_quota(repo, workflow_id, "Quota check unavailable")
                    return workflow_id

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
                if workspace:
                    self._in_progress_workspaces.discard(workspace)
                if branch:
                    self._in_progress_branches.discard(branch)
        return workflow_id

    @staticmethod
    def _batch_has_running_workflow(batch_workflows: list[dict]) -> bool:
        """Whether a batch currently has a workflow actively executing."""
        return any(wf.get("status") in RUNNING_BATCH_STATUSES for wf in batch_workflows)

    def _pause_for_quota(self, repo, workflow_id: str, reason: str) -> None:
        """Pause a workflow because its owner exceeded quota (or the check failed).

        Writes the reason to ``error_message`` with the ``QUOTA_PAUSE_REASON_PREFIX``
        so the auto-resume scan can later distinguish it from a user's manual
        pause, and so the timeline banner surfaces why it stopped. The gate fires
        between advance() cycles (no orchestrator is mid-flight at this point),
        so ``_pause_running_task`` is a defensive no-op in the normal path; it
        only matters for the rare race where a prior cycle's agent is still
        draining when the pause lands.
        """
        from app.routes.autonomous import _emit_event_safe, _pause_running_task

        # Normalize so error_message always starts with QUOTA_PAUSE_REASON_PREFIX
        # (the auto-resume predicate keys on it) but avoid the doubled-up
        # "Quota exceeded: Token quota exceeded: …" the banner would otherwise
        # show. check_quota returns "<X> quota exceeded. Used: …"; collapse the
        # redundant "quota exceeded" (and its trailing punctuation) so the marker
        # prefix isn't repeated and the banner reads cleanly, e.g.
        # "Quota exceeded: Token. Used: 950000/1000000".
        normalized = (reason or "Quota exceeded").strip()
        if normalized.lower().startswith(QUOTA_PAUSE_REASON_PREFIX.lower()):
            full_reason = normalized  # already starts with the marker
        else:
            collapsed = re.sub(
                r"\s*quota\s+exceeded\s*[.,:;]?\s*",
                ". ",
                normalized,
                flags=re.IGNORECASE,
            ).strip(" .")
            full_reason = f"{QUOTA_PAUSE_REASON_PREFIX}: {collapsed}"
        try:
            _pause_running_task(workflow_id)
        except Exception as e:
            logger.warning("Failed to pause agent task for %s: %s", workflow_id[:8], e)
        try:
            repo.update_workflow(
                workflow_id,
                {
                    "status": "paused",
                    "paused_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "error_message": full_reason,
                },
            )
            _emit_event_safe(
                workflow_id,
                "status_change",
                {"status": "paused", "reason": full_reason},
            )
            logger.info("Workflow %s paused for quota: %s", workflow_id[:8], full_reason)
        except Exception as e:
            logger.error("Failed to persist quota pause for %s: %s", workflow_id[:8], e)

    def _auto_resume_quota_paused(self, repo) -> None:
        """Resume workflows paused by the quota gate once the owner's quota recovers.

        Only workflows paused *by the quota gate* (``error_message`` carries the
        ``QUOTA_PAUSE_REASON_PREFIX``) are considered — a user's manual pause is
        never auto-resumed. fail-closed: if the recovery check itself errors, the
        workflow stays paused and is retried on a later cycle.
        """
        from app.modules.governance.quota_manager import QuotaManager
        from app.routes.autonomous import PHASE_TO_STATUS, _emit_event_safe

        try:
            # Filter in SQL (status + error_message prefix) so the scan doesn't
            # grow with the full paused set; _is_quota_paused below is defense-in-depth.
            paused = repo.get_paused_workflows(QUOTA_PAUSE_REASON_PREFIX)
        except Exception as e:
            logger.error("Failed to query paused workflows for quota resume: %s", e)
            return

        for wf in paused:
            if not _is_quota_paused(wf):
                continue
            owner_id = wf.get("user_id")
            if not owner_id:
                continue
            try:
                allowed = QuotaManager().check_quota(int(owner_id))["allowed"]
            except Exception:
                # fail-closed: leave it paused, retry next cycle.
                continue
            if not allowed:
                continue

            phase = wf.get("current_phase", "preparation")
            status = PHASE_TO_STATUS.get(phase, "pending")
            try:
                repo.update_workflow(
                    wf["workflow_id"],
                    {"status": status, "paused_at": None, "error_message": ""},
                )
                _emit_event_safe(wf["workflow_id"], "status_change", {"status": status})
                logger.info(
                    "Auto-resumed quota-paused workflow %s (quota recovered)",
                    wf["workflow_id"][:8],
                )
            except Exception as e:
                logger.error(
                    "Failed to auto-resume quota-paused workflow %s: %s",
                    wf["workflow_id"][:8],
                    e,
                )

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
        # Resume workflows the quota gate paused once the owner's quota recovers.
        # Runs before the active scan so a freshly-resumed workflow can be picked
        # up in the same cycle.
        self._auto_resume_quota_paused(repo)
        # Release git-conflict keys held by workflows paused mid-advance, so a
        # forked child sharing the parent's project_path isn't starved (#1002).
        self._reclaim_paused_slots(repo)

        try:
            workflows = repo.get_active_workflows()
        except Exception as e:
            logger.error("Failed to query active workflows: %s", e)
            return

        # Filter out paused, already-in-progress workflows, batch workflows
        # whose batch is already being processed, and workflows whose git
        # working tree (worktree_path or project_path) OR branch is already
        # being processed by another workflow.
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
                # git-conflict guard: same working tree OR same branch (#1002)
                workspace, branch = self._conflict_keys(wf)
                if workspace and workspace in self._in_progress_workspaces:
                    continue
                if branch and branch in self._in_progress_branches:
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

        # Mark workflows, their batches, and git-conflict keys as in-progress
        with self._in_progress_lock:
            for wf in to_process:
                self._in_progress_ids.add(wf.get("workflow_id", ""))
                batch_id = wf.get("batch_id")
                if batch_id:
                    self._in_progress_batch_ids.add(batch_id)
                workspace, branch = self._conflict_keys(wf)
                if workspace:
                    self._in_progress_workspaces.add(workspace)
                if branch:
                    self._in_progress_branches.add(branch)

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
