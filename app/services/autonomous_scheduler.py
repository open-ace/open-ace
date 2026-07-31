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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

logger = logging.getLogger(__name__)

# Maximum concurrent workflow executions
MAX_CONCURRENT_WORKFLOWS = 3

# Issue #2020: the concurrency cap is configurable via the same
# agent-launcher.conf the launcher reads. The constant above is the default
# when the conf is absent or the key is missing/invalid.
_AGENT_LAUNCHER_CONF = os.environ.get("OPENACE_LAUNCHER_CONF", "/etc/openace/agent-launcher.conf")

# #2022 P6: periodic TTL reaper. A 'running' sandbox row the scheduler is not
# actively driving (not in _in_progress_ids) and has not been touched for longer
# than this TTL is treated as a live-process orphan and destroyed. 7200s is well
# past any agent step's wall-clock budget, so a legitimately in-flight task (held
# in _in_progress_ids, or freshly updated) is never reaped. The reaper cadence is
# how often the sweep runs inside _run_loop.
_SANDBOX_TTL_SECONDS = int(os.environ.get("OPENACE_SANDBOX_TTL_SECONDS", "7200"))
_SANDBOX_REAP_INTERVAL_SECONDS = float(os.environ.get("OPENACE_SANDBOX_REAP_INTERVAL", "300"))


def get_max_concurrent_workflows() -> int:
    """Resolve the concurrency cap from agent-launcher.conf (default 3)."""
    try:
        from app.modules.workspace.autonomous.task_isolation import (
            read_agent_task_policy,
            resolve_agent_task_policy_path,
        )

        conf = resolve_agent_task_policy_path(os.environ.get("OPENACE_LAUNCHER_CONF"))
        if not conf:
            return MAX_CONCURRENT_WORKFLOWS
        return read_agent_task_policy(
            conf, concurrency_default=MAX_CONCURRENT_WORKFLOWS
        ).max_concurrent_workflows
    except Exception:
        return MAX_CONCURRENT_WORKFLOWS


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
# untouched. This scheduler owns autonomous workflow lifecycle enforcement;
# generic quota sweepers deliberately leave workflow sessions alone so they do
# not revoke an in-flight agent's LLM proxy token.
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
        # #2022 P6: RemoteSessionManager shared with the periodic reaper so a
        # remote orphan is destroyed by its persisted session id. Set by
        # init_autonomous_scheduler; read via getattr-default in the reaper so
        # construction paths that don't set it (tests, direct AutonomousScheduler())
        # degrade gracefully (local-only sweep).
        self.remote_session_manager: RemoteSessionManager | None = None

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
        """Stop scheduling and drain active orchestrators before interpreter exit."""
        self._stop_event.set()
        with self._orchestrator_lock:
            orchestrators = list(self._running_orchestrators.values())
        for orchestrator in orchestrators:
            try:
                orchestrator.prepare_for_shutdown()
            except Exception:
                logger.warning("Failed to prepare autonomous workflow for shutdown", exc_info=True)
        if self._thread:
            self._thread.join(timeout=20)
            if self._thread.is_alive():
                logger.warning("Autonomous scheduler did not drain before shutdown timeout")
        logger.info(
            "Autonomous scheduler stopped (%d active attempts interrupted)", len(orchestrators)
        )

    def get_running_orchestrator(self, workflow_id: str):
        """Get the currently running orchestrator for a workflow, if any."""
        with self._orchestrator_lock:
            return self._running_orchestrators.get(workflow_id)

    def clear_in_progress(self, workflow_id: str, wf: dict | None = None) -> None:
        """Clear stale in-progress state for a workflow.

        Called by the retry endpoint to ensure a retried workflow is not
        permanently skipped by the scheduler due to a stale ``_in_progress_ids``
        entry left behind by an orchestrator thread that exited without running
        its ``finally`` cleanup (e.g. hard crash, OOM kill). Also removes any
        orphaned orchestrator reference, clears git-conflict keys, and releases
        the DB-level lock so the next scheduler cycle can acquire a fresh lock.

        Args:
            workflow_id: The workflow to clear.
            wf: Optional workflow dict for computing conflict keys. When
                provided, also clears ``_in_progress_workspaces``,
                ``_in_progress_branches``, and ``_in_progress_batch_ids``.
        """
        # Remove any orphaned orchestrator reference. If the orchestrator is
        # genuinely still running, this is a no-op because the caller (retry
        # endpoint) already verified status == "failed" — a failed workflow has
        # no live orchestrator.
        with self._orchestrator_lock:
            self._running_orchestrators.pop(workflow_id, None)

        with self._in_progress_lock:
            self._in_progress_ids.discard(workflow_id)
            # Also clear git-conflict keys so _process_workflows doesn't skip
            # the retried workflow on workspace/branch/batch collision. The
            # workflow dict is available from the retry endpoint.
            if wf:
                workspace, branch = self._conflict_keys(wf)
                if workspace:
                    self._in_progress_workspaces.discard(workspace)
                if branch:
                    self._in_progress_branches.discard(branch)
                batch_id = wf.get("batch_id")
                if batch_id:
                    self._in_progress_batch_ids.discard(batch_id)

        # Release the DB-level lock so the next cycle can acquire it.
        try:
            from app.routes.autonomous import _get_repo

            repo = _get_repo()
            # Force-release regardless of owner — the previous owner is gone.
            conn = repo.db.get_connection()
            try:
                cursor = conn.cursor()
                import app.repositories.database as _db_mod

                cursor.execute(
                    _db_mod.adapt_sql(
                        """
                        UPDATE autonomous_workflows
                        SET locked_at = NULL, locked_by = NULL
                        WHERE workflow_id = ?
                        """
                    ),
                    (workflow_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning(
                "Failed to release DB lock for workflow %s during clear_in_progress",
                workflow_id[:8],
                exc_info=True,
            )

        logger.info("Cleared stale in-progress state for workflow %s", workflow_id[:8])

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

    def _workflow_blocked_by_conflict_locks(self, wf: dict) -> bool:
        """Return True if *wf* must be skipped this cycle due to a batch /
        workspace / branch conflict lock held by an in-progress workflow.

        Waiting workflows bypass all conflict locks (see ``_do_wait``'s
        no-git-mutation invariant) so they can resume even while a batch
        sibling is actively running. Extracted from ``_process_workflows`` so
        tests can assert against the real filter instead of re-implementing it
        (PR #2016 review suggestion #2).

        Thread-safety: reads ``_in_progress_batch_ids`` /
        ``_in_progress_workspaces`` / ``_in_progress_branches``, so the caller
        must hold ``self._in_progress_lock`` (as ``_process_workflows`` does)
        to avoid a TOCTOU race where a sibling workflow reserves/releases keys
        between this check and the reservation step.
        """
        is_waiting = wf.get("status") == "waiting"
        batch_id = wf.get("batch_id")
        if batch_id and batch_id in self._in_progress_batch_ids and not is_waiting:
            return True
        workspace, branch = self._conflict_keys(wf)
        if workspace and workspace in self._in_progress_workspaces and not is_waiting:
            return True
        if branch and branch in self._in_progress_branches and not is_waiting:
            return True
        return False

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
        # Seed so the first reap is delayed one interval — init_autonomous_scheduler
        # already ran the startup reconcile, so reaping on the very first poll
        # cycle would only repeat that no-op.
        last_reap_monotonic = time.monotonic()
        while not self._stop_event.is_set():
            try:
                self._process_workflows()
                # #2022 P6: periodically reap stuck 'running' sandbox rows the
                # scheduler is not driving. Bounded by _SANDBOX_REAP_INTERVAL
                # so the hot 10s poll loop does not hit the DB each cycle.
                now_monotonic = time.monotonic()
                if now_monotonic - last_reap_monotonic >= _SANDBOX_REAP_INTERVAL_SECONDS:
                    last_reap_monotonic = now_monotonic
                    try:
                        self._reap_stale_running_sandboxes()
                    except Exception as e:  # noqa: BLE001
                        logger.error("Sandbox TTL reap failed: %s", e, exc_info=True)
            except Exception as e:
                logger.error("Scheduler error: %s", e, exc_info=True)

            # Wait 10 seconds between checks (or stop signal)
            self._stop_event.wait(10)

    def _reap_stale_running_sandboxes(self, repo=None, *, now_epoch=None) -> None:
        """Reap stuck 'running' sandbox rows the scheduler isn't driving (#2022 P6).

        Catches live-process orphans the startup reconcile misses: a workflow
        whose ``sandbox_state`` still claims ``running`` but which the scheduler
        is not actively advancing (not in ``_in_progress_ids``) and has not been
        touched for longer than ``_SANDBOX_TTL_SECONDS``. The double guard
        (not-driven + stale) avoids reaping a long task in flight.

        Paused workflows are left alone — their sandbox is intentionally retained
        for a later resume (a paused workflow is not in ``_in_progress_ids`` by
        design, so the not-driven guard alone would mis-reap it; the status check
        excludes it). Remote orphans are stopped by their persisted session id;
        local rows are DB-reset only (the proc died).

        Accepts injected ``repo`` / ``now_epoch`` for hermetic testing.
        """
        if repo is None:
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository
            from app.repositories.database import Database

            repo = AutonomousWorkflowRepository(Database())
        if now_epoch is None:
            now_epoch = time.time()
        remote_session_manager = getattr(self, "remote_session_manager", None)

        with self._in_progress_lock:
            driven = set(self._in_progress_ids)

        ttl = _SANDBOX_TTL_SECONDS
        workflows = repo.get_workflows_with_active_sandbox()
        for wf in workflows:
            if wf.get("sandbox_state") != "running":
                continue  # only 'running' occupies resources right now
            if wf.get("status") == "paused":
                continue  # intentional; keep the sandbox for resume
            if wf.get("workflow_id") in driven:
                continue  # scheduler is actively advancing it
            updated = _parse_epoch(wf.get("updated_at"))
            if updated is None or now_epoch - updated < ttl:
                continue  # fresh (or no timestamp) — not stale yet
            _destroy_orphan_sandbox(wf, remote_session_manager)
            repo.update_workflow(
                wf["workflow_id"],
                {
                    "sandbox_state": "destroyed",
                    "sandbox_id": None,
                    "sandbox_remote_session_id": None,
                    "sandbox_last_error": (f"reaped by TTL sweep: running sandbox stale > {ttl}s"),
                },
            )
            logger.info(
                "Reaped stale running sandbox for workflow %s",
                wf.get("workflow_id", "")[:8],
            )

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
        # Waiting workflows bypass conflict locks (see _process_workflows).
        # Capture this so cleanup paths don't release another workflow's keys.
        #
        # Load-bearing invariant: this advance-time read of ``status == waiting``
        # agrees with the selection-time read in ``_process_workflows`` only
        # because the ``waiting -> developing/merging`` transition happens inside
        # ``advance()`` below — no other actor flips the status between selection
        # and this point. If that ever changes, the finally cleanup below could
        # release (or fail to release) the wrong workflow's conflict keys.
        was_waiting = bool(workflow and workflow.get("status") == "waiting")

        # Acquire DB-level distributed lock
        if not repo.acquire_lock(workflow_id, lock_owner):
            logger.debug("Workflow %s is locked by another instance, skipping", workflow_id[:8])
            with self._in_progress_lock:
                self._in_progress_ids.discard(workflow_id)
                if batch_id and not was_waiting:
                    self._in_progress_batch_ids.discard(batch_id)
                if workspace and not was_waiting:
                    self._in_progress_workspaces.discard(workspace)
                if branch and not was_waiting:
                    self._in_progress_branches.discard(branch)
            return workflow_id

        # Quota gate (fail-closed): a user over quota (or whose quota check
        # errored) must not advance. This scheduler is the lifecycle authority
        # for workflow-owned sessions, so it pauses before a new advance cycle
        # without revoking a proxy token out from under an in-flight agent.
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
            # stop() may race with this worker after its orchestrator snapshot.
            # Register first, then re-check the event so no new agent task can
            # start after graceful shutdown has begun.
            if self._stop_event.is_set():
                orchestrator.prepare_for_shutdown()
                return workflow_id
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
                if batch_id and not was_waiting:
                    self._in_progress_batch_ids.discard(batch_id)
                if workspace and not was_waiting:
                    self._in_progress_workspaces.discard(workspace)
                if branch and not was_waiting:
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
        # Retry Git cleanup for delivered-but-uncleaned workflows (#2043). Runs
        # each tick so transient cleanup failures converge without waiting for a
        # restart; honors per-workflow backoff via cleanup_next_retry_at. Reuses
        # the scheduler's own repo so test mocks of _get_repo stay effective.
        _retry_pending_git_cleanups(repo)

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
                # Waiting workflows only do a lightweight state transition in
                # _do_wait (DB update, no agent/git) — bypass batch/workspace/
                # branch conflict locks so they can resume even while a batch
                # sibling is still running. The lock re-applies on the next
                # cycle once the workflow leaves "waiting" status.
                if self._workflow_blocked_by_conflict_locks(wf):
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

        # Limit to the concurrency cap while reserving conflict keys inside
        # this same selection pass.  Filtering above only sees workflows that
        # were already running before this poll; without local reservations,
        # multiple newly auto-resumed siblings from one batch (or workflows
        # sharing a worktree/branch) can all enter ``to_process`` together.
        with self._in_progress_lock:
            slots_available = get_max_concurrent_workflows() - len(self._in_progress_ids)
            selected_batches: set[str] = set()
            selected_workspaces: set[str] = set()
            selected_branches: set[str] = set()
            to_process: list[dict] = []
            for wf in active:
                if len(to_process) >= max(0, slots_available):
                    break
                batch_id = wf.get("batch_id")
                workspace, branch = self._conflict_keys(wf)
                is_waiting = wf.get("status") == "waiting"
                if batch_id and batch_id in selected_batches and not is_waiting:
                    continue
                if workspace and workspace in selected_workspaces and not is_waiting:
                    continue
                if branch and branch in selected_branches and not is_waiting:
                    continue
                to_process.append(wf)
                if batch_id:
                    selected_batches.add(batch_id)
                if workspace:
                    selected_workspaces.add(workspace)
                if branch:
                    selected_branches.add(branch)

        if not to_process:
            return

        # Mark workflows, their batches, and git-conflict keys as in-progress
        with self._in_progress_lock:
            for wf in to_process:
                self._in_progress_ids.add(wf.get("workflow_id", ""))
                # Waiting workflows bypass conflict locks — don't reserve their
                # keys so we don't block other workflows, and don't release
                # another workflow's keys in _advance_single's finally.
                is_waiting = wf.get("status") == "waiting"
                batch_id = wf.get("batch_id")
                if batch_id and not is_waiting:
                    self._in_progress_batch_ids.add(batch_id)
                workspace, branch = self._conflict_keys(wf)
                if workspace and not is_waiting:
                    self._in_progress_workspaces.add(workspace)
                if branch and not is_waiting:
                    self._in_progress_branches.add(branch)

        with ThreadPoolExecutor(
            max_workers=min(get_max_concurrent_workflows(), len(to_process)),
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


def _reconcile_pending_transitions():
    """Recover interrupted merge-conflict worktree transitions (#2050).

    Walks every workflow with a non-NULL ``worktree_transition_state`` (left
    behind by a SIGKILL / restart mid-transition) and runs the same idempotent
    ``_reconcile_worktree_transition`` entry point that ``advance()`` uses, so
    startup, advance, and manual resume share one reconciler. Each workflow is
    isolated: a failure fails that workflow closed (recovery_failed) without
    blocking the others.
    """
    logger.info("Reconciling interrupted worktree transitions...")

    try:
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository
        from app.repositories.database import Database

        repo = AutonomousWorkflowRepository(Database())
        pending = repo.get_workflows_with_active_transition()
        if not pending:
            logger.info("No interrupted worktree transitions found")
            return

        for wf in pending:
            wf_id = wf.get("workflow_id")
            if not isinstance(wf_id, str) or not wf_id:
                continue
            try:
                orchestrator = AutonomousOrchestrator(wf_id)
                orchestrator._reconcile_worktree_transition(wf)
            except Exception as e:  # noqa: BLE001
                # The reconciler itself should fail closed internally; this catch
                # is a last resort so one bad workflow does not block the rest.
                logger.error(
                    "Reconcile raised for workflow %s: %s",
                    (wf_id or "")[:8],
                    e,
                    exc_info=True,
                )
                try:
                    repo.update_workflow(
                        wf_id,
                        {
                            "worktree_transition_state": "recovery_failed",
                            "transition_error": f"reconcile raised: {e}",
                            "status": "failed",
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.error("Could not persist recovery_failed for %s", (wf_id or "")[:8])

        logger.info("Reconciled %d interrupted worktree transition(s)", len(pending))
    except Exception as e:  # noqa: BLE001
        logger.error("Worktree transition reconcile sweep failed: %s", e, exc_info=True)


def _retry_pending_git_cleanups(repo=None):
    """Re-attempt post-merge Git cleanup for delivered workflows (#2043).

    Walks every ``status='completed'`` workflow with ``cleanup_status='pending'``
    and re-runs the idempotent ``_perform_git_cleanup``. Honors the per-workflow
    ``cleanup_next_retry_at`` backoff so a transient failure is not retried every
    tick. This is shared by the startup sweep and the periodic scheduler tick so
    both converge leaked worktrees/branches without a separate worker. Failures
    are isolated per workflow.

    ``repo`` lets the periodic tick reuse the scheduler's own (mock-friendly)
    repository; the startup sweep omits it and constructs one.
    """
    try:
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        if repo is None:
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository
            from app.repositories.database import Database

            repo = AutonomousWorkflowRepository(Database())
        pending = repo.get_workflows_pending_cleanup()
        if not pending:
            return

        now = datetime.now(timezone.utc)
        for wf in pending:
            wf_id = wf.get("workflow_id")
            if not isinstance(wf_id, str) or not wf_id:
                continue
            # Backoff: skip until cleanup_next_retry_at has passed.
            next_retry = wf.get("cleanup_next_retry_at") or ""
            if next_retry:
                try:
                    due = datetime.strptime(next_retry, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    due = now
                if due > now:
                    continue
            try:
                orchestrator = AutonomousOrchestrator(wf_id)
                orchestrator._perform_git_cleanup()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Git cleanup retry raised for workflow %s: %s",
                    (wf_id or "")[:8],
                    e,
                    exc_info=True,
                )

        logger.info("Processed %d pending Git cleanup(s)", len(pending))
    except Exception as e:  # noqa: BLE001
        logger.error("Git cleanup retry sweep failed: %s", e, exc_info=True)


def _parse_epoch(value: Any) -> float | None:
    """Coerce a workflow-row timestamp to epoch seconds (cross-DB tolerant).

    Postgres returns ``datetime``; SQLite may return a string or epoch. Returns
    ``None`` when the value is missing or unparseable so the caller can treat
    "no timestamp" as "not stale" (don't reap what we can't age).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        try:
            return float(value)
        except Exception:
            return None


def _destroy_orphan_sandbox(wf: dict, remote_session_manager: Any) -> None:
    """Best-effort real destroy of one orphan sandbox by persisted attribution (#2022 P6).

    Rebuilds the provider from the workflow row's ``sandbox_provider`` name and
    calls ``destroy_attribution`` with the persisted ids. The per-call provider
    instance that ran the task (and held its ``sandbox_id`` -> handle map) is
    gone after a restart, so ``destroy(handle)`` cannot resolve the session —
    only the persisted strings remain. Local/gVisor rows without an external id
    no-op (the proc died with the server); ``destroy_attribution`` swallows its
    own failures so a bad row never aborts the sweep.

    Scope firewall: this acts ONLY on the autonomous workflow row's own
    persisted ``sandbox_remote_session_id`` — it never enumerates or stops
    ordinary (non-autonomous) remote sessions.
    """
    provider_name = wf.get("sandbox_provider") or ""
    raw_sid = wf.get("sandbox_remote_session_id")
    remote_sid = raw_sid if isinstance(raw_sid, str) else ""
    raw_sandbox = wf.get("sandbox_id")
    sandbox_id = raw_sandbox if isinstance(raw_sandbox, str) else ""
    # Only remote_machine has an external resource still alive after a restart;
    # legacy/gVisor rows without an id have nothing to stop.
    if provider_name != "remote_machine" or not remote_sid:
        return
    try:
        from app.modules.workspace.autonomous.sandbox.registry import provider_for

        provider = provider_for(provider_name, remote_session_manager)
        provider.destroy_attribution(sandbox_id, remote_sid)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to destroy orphan sandbox %s for workflow %s: %s",
            sandbox_id,
            wf.get("workflow_id", "")[:8],
            e,
            exc_info=True,
        )


def _reconcile_orphan_sandboxes(repo=None, remote_session_manager=None):
    """Reset orphan sandbox state at startup (#2022 P2 state reset, P6 real destroy).

    Walks workflows whose ``sandbox_state`` claims an active sandbox
    (created/running/paused) but whose owning process is gone — the server
    crashed/restarted mid-task before the sandbox was destroyed. At startup
    nothing is running, so every active-sandbox-state row is an orphan.

    #2022 P6: real resource teardown now. A ``remote_machine`` orphan carries the
    persisted ``sandbox_remote_session_id`` (the manager row id written mid-run
    via ``on_sandbox_created``); the sweep rebuilds a provider via the registry
    and stops that session by id. Local/gVisor rows have no external id — the
    proc died with the server, so ``destroy_attribution`` is a no-op and the
    DB-reset is the real cleanup. Then reset state/generation/sandbox_id/
    remote_session_id so a second sweep is a no-op.
    """
    logger.info("Reconciling orphan sandbox state...")

    try:
        if repo is None:
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository
            from app.repositories.database import Database

            repo = AutonomousWorkflowRepository(Database())

        workflows = repo.get_workflows_with_active_sandbox()
        if not workflows:
            logger.info("No orphan sandbox state found")
            return

        for wf in workflows:
            _destroy_orphan_sandbox(wf, remote_session_manager)
            current_gen = int(wf.get("sandbox_generation") or 0)
            repo.update_workflow(
                wf["workflow_id"],
                {
                    "sandbox_state": "destroyed",
                    "sandbox_generation": current_gen + 1,
                    "sandbox_id": None,
                    "sandbox_remote_session_id": None,
                    "sandbox_last_error": ("reconciled at startup: orphan sandbox destroyed"),
                },
            )
            logger.info(
                "Reconciled orphan sandbox for workflow %s (generation %d -> %d)",
                wf["workflow_id"][:8],
                current_gen,
                current_gen + 1,
            )
    except Exception as e:  # noqa: BLE001
        logger.error("Sandbox reconciliation sweep failed: %s", e, exc_info=True)


def init_autonomous_scheduler():
    """Initialize and start the autonomous scheduler."""
    # Clean up orphaned processes from previous server run
    _cleanup_orphan_processes()
    # Recover any worktree transitions interrupted by a prior SIGKILL/restart
    _reconcile_pending_transitions()
    # Retry Git cleanup for workflows delivered but not yet cleaned up (#2043)
    _retry_pending_git_cleanups()
    # Reset orphan sandbox state from a prior crash/restart (#2022 P2/P6).
    # RemoteSessionManager is shared with the periodic reaper so a remote orphan
    # is actually stopped by its persisted session id, not just DB-reset.
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    remote_session_manager = RemoteSessionManager()
    _reconcile_orphan_sandboxes(remote_session_manager=remote_session_manager)

    scheduler = AutonomousScheduler.instance()
    scheduler.remote_session_manager = remote_session_manager
    scheduler.start()
