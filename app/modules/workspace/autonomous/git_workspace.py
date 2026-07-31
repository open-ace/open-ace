"""Open ACE — Autonomous Git Workspace Service (#2044 Phase B).

Extracted verbatim from ``AutonomousOrchestrator``: the worktree lifecycle /
recovery / cleanup methods delivered by #2041 (anomaly-safe conflict-worktree
switch), #2042 (authoritative-commit recovery) and #2043 (delivery-vs-cleanup
split + TTL reaper). Phase B moves them here so the orchestrator no longer
implements git-workspace detail (a #2044 Phase B acceptance criterion).

Behaviour is unchanged: the orchestrator keeps same-named delegating wrappers
(``_ensure_worktree`` etc.) so existing callers and tests that patch
``AutonomousOrchestrator._X`` still hit. The service holds a back-reference to
the orchestrator for repo/gh/workflow state and the small helpers these methods
call; a later cleanup can narrow this to explicit dependencies.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.modules.workspace.autonomous import orchestrator as _orchestrator_module
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import (
    AUTONOMOUS_CONTEXT,
    AUTONOMOUS_DEV_ALLOWED_TOOLS,
    _ReconcileFailed,
)
from app.repositories.user_repo import UserRepository

if TYPE_CHECKING:
    from app.modules.workspace.autonomous.github_ops import GitHubOps
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

logger = logging.getLogger(__name__)


def _GitHubOps(*args, **kwargs):
    """Resolve ``GitHubOps`` through the orchestrator module at call time.

    Tests patch ``app.modules.workspace.autonomous.orchestrator.GitHubOps``; a
    top-level ``from ...github_ops import GitHubOps`` would bind the unpatched
    class and miss the patch. Reading the attribute off the module each call
    preserves the original (orchestrator-inlined) name-resolution semantics.
    """
    return _orchestrator_module.GitHubOps(*args, **kwargs)


class GitWorkspaceService:
    """Worktree lifecycle / recovery / cleanup for ``AutonomousOrchestrator``.

    Extracted verbatim from the orchestrator in #2044 Phase B T5. The service
    holds a back-reference to the orchestrator (``self._orch``) for repo / gh /
    workflow state and the small helpers these methods call; the orchestrator
    keeps same-named delegating wrappers (``_ensure_worktree`` etc.) so callers
    and tests that patch ``AutonomousOrchestrator._X`` still hit. Behaviour is
    unchanged from the pre-extraction inline implementation.
    """

    def __init__(self, orchestrator: AutonomousOrchestrator):
        self._orch = orchestrator

    def ensure_worktree(self, wf: dict) -> str:
        """Guarantee the worktree dir + branch exist before a phase runs.

        Retrying/resuming a ``worktree``-strategy workflow after its dir was
        cleaned up (e.g. a previous failure removed it, or the machine
        rebooted) used to silently launch the agent against an empty path and
        fail with a JSONL-detection error (#814). Every downstream phase now
        calls this at entry so the environment self-heals:

        - normalizes a stale ``worktree_path`` still containing ``..`` so the
          DB and the on-disk dir agree;
        - recreates the worktree dir (reusing the branch if it still exists)
          when it is gone.

        Issue #1573: Added branch consistency verification when worktree exists.
        If the actual branch doesn't match expected branch_name, we attempt to
        recreate the worktree (after safety check for uncommitted changes).

        Returns the canonical worktree path. For non-worktree strategies, or
        when ``worktree_path`` is intentionally empty (merge cleanup / conflict
        resolution clears it), this is a no-op returning ``project_path``.
        """
        strategy = wf.get("branch_strategy", "new-branch")
        project_path = wf.get("project_path", "")
        worktree_path = wf.get("worktree_path", "")

        # An empty worktree_path is NOT the "dir gone, recreate it" case — it
        # is set deliberately by _do_merge final cleanup when the PR is merged
        # and the worktree is no longer needed. (_resolve_merge_conflicts
        # restores worktree_path in its finally block, so it no longer leaves
        # the field empty.) Treating an empty path as missing would fall back
        # to project_path as canonical and try `git worktree add <main_repo>`,
        # which fails and turns a retried merge into a hard failure. Only a
        # non-empty path whose dir is gone represents external loss (#814).
        if strategy != "worktree" or not project_path or not worktree_path:
            # Hard guard (#2050): a transition still in progress (anything other
            # than recovery_failed) must NOT fall back to project_path — that
            # would run a phase against the main checkout (HEAD=main). The
            # caller is required to reconcile first.
            ts = wf.get("worktree_transition_state")
            if ts and ts != "recovery_failed":
                raise RuntimeError(
                    "worktree transition in progress " f"(state={ts!r}); reconcile before execution"
                )
            return worktree_path or project_path  # type: ignore[no-any-return]

        canonical: str = os.path.realpath(worktree_path)
        # Resolve system_account up front so the validity check below can use
        # it: os.path.isfile() stats as the service user and raises
        # PermissionError under a user-private parent (700 home, Issue #1395).
        # Get system_account for multi-user permission isolation (Issue #1395)
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")
        main_gh = _GitHubOps(project_path, system_account=system_account)
        # Valid worktree: a .git FILE inside means git set it up (a plain
        # clone has a .git directory instead). If the stored path was
        # unnormalized (legacy ".."), persist the canonical form so JSONL
        # session detection matches Claude's encoding. file_only keeps the
        # original os.path.isfile() semantics (Issue #1395 review).
        if worktree_path and main_gh.path_exists_as_user(
            os.path.join(canonical, ".git"), file_only=True
        ):
            if canonical != worktree_path:
                self._orch._update_workflow({"worktree_path": canonical})

            # Issue #1573: Verify branch consistency when worktree exists.
            # Check that the worktree's actual branch matches the expected branch_name.
            expected_branch = wf.get("branch_name", "")
            if expected_branch:
                try:
                    wt_gh = _GitHubOps(canonical, system_account=system_account)
                    actual_branch = wt_gh.get_current_branch()
                    if actual_branch != expected_branch:
                        logger.error(
                            "Branch mismatch detected for workflow %s: expected=%s, actual=%s, worktree_path=%s",
                            self._orch._workflow_id[:8],
                            expected_branch,
                            actual_branch,
                            canonical,
                        )
                        # Safety check: refuse to delete worktree with uncommitted changes
                        if wt_gh.has_uncommitted_changes():
                            logger.error(
                                "Worktree %s has uncommitted changes, refusing to delete",
                                canonical,
                            )
                            self._orch._create_milestone(
                                phase=wf.get("current_phase", "preparation"),
                                milestone_type="branch_mismatch",
                                status="failed",
                                title=f"Branch mismatch with uncommitted changes: expected {expected_branch}, actual {actual_branch}",
                                error_message=f"Cannot recreate worktree: uncommitted changes detected on branch {actual_branch}",
                            )
                            raise GitHubOpsError(
                                f"Worktree branch mismatch ({actual_branch} != {expected_branch}) "
                                f"with uncommitted changes. Manual intervention required."
                            )
                        # Safe to delete - recreate worktree with correct branch
                        logger.warning(
                            "Attempting to recreate worktree %s on correct branch %s",
                            canonical,
                            expected_branch,
                        )
                        main_gh.remove_worktree(canonical)
                        # Recreate with correct branch. Issue #2042: restore to
                        # the authoritative head, not origin/main.
                        branch_check = main_gh._run_git(
                            ["show-ref", "--verify", "--quiet", f"refs/heads/{expected_branch}"],
                            check=False,
                        )
                        remote_check = main_gh._run_git(
                            [
                                "show-ref",
                                "--verify",
                                "--quiet",
                                f"refs/remotes/origin/{expected_branch}",
                            ],
                            check=False,
                        )
                        if branch_check.returncode == 0 or remote_check.returncode == 0:
                            main_gh._run_git(["worktree", "add", canonical, expected_branch])
                        else:
                            # Branch gone — fail closed without a verified head
                            # instead of silently rebuilding from origin/main.
                            # Route sibling calls through the orchestrator wrapper (not self.<public>)
                            # so tests that patch AutonomousOrchestrator._<name> still intercept them.
                            head_sha, decision, head_meta = self._orch._resolve_recovery_head(
                                main_gh, wf
                            )
                            if not head_sha:
                                self._orch._fail_recovery_closed(
                                    wf, canonical, decision, head_meta, "", ""
                                )
                                raise RuntimeError(
                                    f"Worktree branch-mismatch recovery fail-closed: {decision}"
                                )
                            main_gh._run_git(
                                ["worktree", "add", "-b", expected_branch, canonical, head_sha]
                            )
                        self._orch._create_milestone(
                            phase=wf.get("current_phase", "preparation"),
                            milestone_type="worktree_restored",
                            status="completed",
                            title=f"Worktree recreated on correct branch {expected_branch}",
                        )
                        logger.info(
                            "Worktree %s recreated on correct branch %s",
                            canonical,
                            expected_branch,
                        )
                        # Reset cached gh so it picks up the new worktree
                        self._orch._gh = None
                except GitHubOpsError:
                    raise
                except Exception as e:
                    logger.warning("Branch verification failed: %s", e)
            return canonical

        # Worktree missing — recreate at the authoritative trusted commit.
        # Issue #2042: never silently rebuild from the moving origin/main tip;
        # restore to the verified PR head, recorded expected_head_sha, or (last
        # resort) base_commit_sha. Divergence or missing evidence fails closed.
        branch_name = wf.get("branch_name") or f"auto-dev/{self._orch._workflow_id[:12]}"
        recovery_meta: dict = {}
        try:
            main_gh._run_git(["fetch", "origin", "main"])
            # Does the branch still exist locally or on origin?
            branch_check = main_gh._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                check=False,
            )
            remote_check = main_gh._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch_name}"],
                check=False,
            )
            branch_survives = branch_check.returncode == 0 or remote_check.returncode == 0
            # Resolve the authoritative recovery head regardless of whether the
            # branch survives, so we can also validate a surviving branch below.
            head_sha, decision, head_meta = self._orch._resolve_recovery_head(main_gh, wf)
            recovery_meta = head_meta
            if branch_survives:
                # Branch survives (local or remote) — attach a worktree to it
                # without recreating the branch. For a remote-only branch, git
                # auto-creates a local tracking branch in this step.
                main_gh._run_git(["worktree", "add", canonical, branch_name])
                # Issue #1999 guard: a surviving local branch may have drifted
                # ahead of the verified head (unpushed commits). If we have a
                # confirmed expected_head_sha and the attached HEAD does not
                # match, fail closed rather than continuing on a stale branch.
                if head_sha and decision in ("verified_pr_head", "expected_head"):
                    wt_gh = _GitHubOps(canonical)
                    try:
                        local_head = wt_gh.get_current_commit()
                    except Exception:
                        local_head = ""
                    if local_head and local_head != head_sha:
                        self._orch._fail_recovery_closed(
                            wf, canonical, decision, head_meta, local_head, head_sha
                        )
                        raise RuntimeError(
                            f"Worktree recovery fail-closed: local branch {local_head[:8]} "
                            f"diverges from verified head {head_sha[:8]} ({decision})"
                        )
            else:
                # Neither worktree nor branch — recreate from the authoritative
                # head, never from origin/main. No verified head → fail closed.
                if not head_sha:
                    self._orch._fail_recovery_closed(wf, canonical, decision, head_meta, "", "")
                    raise RuntimeError(f"Worktree recovery fail-closed: {decision}")
                main_gh._run_git(["worktree", "add", "-b", branch_name, canonical, head_sha])
        except GitHubOpsError as e:
            logger.error("Failed to recreate worktree at %s: %s", canonical, e)
            raise

        self._orch._update_workflow({"worktree_path": canonical, "branch_name": branch_name})
        self._orch._create_milestone(
            phase=wf.get("current_phase", "preparation"),
            milestone_type="worktree_restored",
            status="completed",
            title=f"Worktree restored at {os.path.basename(canonical)}",
            metadata=json.dumps(
                {
                    "decision": decision,
                    "head_sha": head_sha,
                    "evidence": recovery_meta,
                },
                ensure_ascii=False,
            ),
        )
        logger.info(
            "Restored worktree at %s on branch %s (decision=%s)", canonical, branch_name, decision
        )
        # Reset cached gh so it picks up the restored worktree path.
        self._orch._gh = None
        return canonical

    def fail_recovery_closed(
        self,
        wf: dict,
        canonical: str,
        decision: str,
        evidence_meta: dict,
        local_sha: str,
        expected_sha: str,
    ) -> None:
        """Record a fail-closed recovery outcome and mark the workflow failed.

        Issue #2042: divergence or missing evidence must pause the workflow
        rather than guess a recovery commit. Writes a ``recovery_evidence_missing``
        milestone with the full evidence trail so the failure is auditable.
        """
        logger.error(
            "Workflow %s: worktree recovery fail-closed (decision=%s, " "local=%s, expected=%s)",
            self._orch._workflow_id,
            decision,
            (local_sha or "<none>")[:8],
            (expected_sha or "<none>")[:8],
        )
        self._orch._update_workflow({"status": "failed"})
        self._orch._create_milestone(
            phase=wf.get("current_phase", "preparation"),
            milestone_type="recovery_evidence_missing",
            status="failed",
            title="Worktree recovery failed: no verified head",
            metadata=json.dumps(
                {
                    "decision": decision,
                    "local_sha": local_sha,
                    "expected_sha": expected_sha,
                    "evidence": evidence_meta,
                },
                ensure_ascii=False,
            ),
        )

    def get_preferred_worktree_path(self, wf: dict) -> str:
        """Return the canonical worktree path the workflow should reuse."""
        preferred = (wf.get("preferred_worktree_path") or "").strip()
        if preferred:
            return preferred
        current = (wf.get("worktree_path") or "").strip()
        if current:
            return current
        project_path = (wf.get("project_path") or "").strip()
        workflow_id = (wf.get("workflow_id") or self._orch._workflow_id or "").strip()
        if not project_path or not workflow_id:
            return ""
        return os.path.join(project_path, ".worktrees", workflow_id)

    def record_trusted_head(
        self, gh: GitHubOps, *, pushed: bool = False, sha: str | None = None
    ) -> str | None:
        """Persist the current HEAD as the workflow's last trusted commit.

        Called after every orchestrator-owned commit (and, when ``pushed=True``,
        after GitHub has accepted the push). Stores ``expected_head_sha`` so a
        later worktree recovery restores to this exact commit instead of falling
        back to ``origin/main`` (Issue #2042). Returns the recorded SHA, or None
        if HEAD could not be read (logged, never raises).

        ``sha`` lets a caller pass an already-read commit SHA instead of
        re-reading HEAD — avoids a redundant git call and keeps test
        ``side_effect`` sequences aligned when the caller already read the SHA.
        """
        if sha is None:
            try:
                sha = gh.get_current_commit()
            except Exception as e:
                logger.warning(
                    "Workflow %s: cannot read HEAD to record trusted head: %s",
                    self._orch._workflow_id,
                    e,
                )
                return None
        if sha:
            self._orch._update_workflow({"expected_head_sha": sha})
            logger.info(
                "Workflow %s: recorded trusted head %s (pushed=%s)",
                self._orch._workflow_id,
                sha[:8],
                pushed,
            )
        return sha

    def resolve_recovery_head(self, main_gh: GitHubOps, wf: dict) -> tuple[str | None, str, dict]:
        """Determine the authoritative commit to restore a missing worktree to.

        Four-state decision tree (Issue #2042 §3), fail-closed:

        1. Existing PR → ``resolve_verified_pr_head`` (external authority).
        2. No PR, has ``expected_head_sha`` → verify the object is local.
        3. No PR, no expected head, has ``base_commit_sha`` → verify local.
        4. None of the above, or any unverifiable/divergent signal → pause.

        Returns ``(head_sha_or_None, decision, evidence_metadata)``. A None head
        means indeterminate: the caller must fail closed, never guess. The
        ``evidence_metadata`` dict carries the :class:`Evidence` audit trail.
        """
        branch_name = wf.get("branch_name", "")
        pr_number = wf.get("github_pr_number")

        # 1. Existing PR — verified GitHub PR head is the external authority.
        if pr_number:
            ev = self._orch._evidence.resolve_verified_pr_head(main_gh, int(pr_number), branch_name)
            if ev.verdict is Verdict.CONFIRMED:
                return ev.commit_shas[0], "verified_pr_head", ev.to_dict()
            return (
                None,
                "indeterminate_pause",
                {
                    "decision": "indeterminate_pause",
                    "reason": f"PR head not verifiable: {ev.reason}",
                    "evidence": ev.to_dict(),
                },
            )

        # 2. No PR, but a trusted head was recorded.
        expected = wf.get("expected_head_sha")
        if expected:
            ev = self._orch._evidence.verify_commit_available(main_gh, expected, branch_name)
            if ev.verdict is Verdict.CONFIRMED:
                return expected, "expected_head", ev.to_dict()
            return (
                None,
                "indeterminate_pause",
                {
                    "decision": "indeterminate_pause",
                    "reason": f"expected_head_sha unavailable: {ev.reason}",
                    "evidence": ev.to_dict(),
                },
            )

        # 3. No trusted head yet — fall back to the immutable base.
        base = wf.get("base_commit_sha")
        if base:
            ev = self._orch._evidence.verify_commit_available(main_gh, base, branch_name)
            if ev.verdict is Verdict.CONFIRMED:
                return base, "base_commit", ev.to_dict()
            return (
                None,
                "indeterminate_pause",
                {
                    "decision": "indeterminate_pause",
                    "reason": f"base_commit_sha unavailable: {ev.reason}",
                    "evidence": ev.to_dict(),
                },
            )

        # 4. No evidence at all — must not guess.
        return (
            None,
            "indeterminate_pause",
            {
                "decision": "indeterminate_pause",
                "reason": "no PR, no expected_head_sha, no base_commit_sha",
                "evidence": {},
            },
        )

    def cleanup_worktree_and_branch(
        self,
        reason: str = "failed",
        *,
        remove_worktree: bool = True,
        remove_branch: bool = False,
    ) -> bool:
        """Remove the workflow's worktree dir and/or git branch.

        Extracted from ``_do_merge``'s post-merge cleanup (Issue #1831 finding
        #1) so the same path serves both completed and terminally-failed
        workflows. Previously a terminally-failed workflow left its worktree dir
        on disk indefinitely — a slow leak across retries.

        #1112 timing dimension / ``keep_for_debug`` default: by default this
        removes ONLY the worktree dir (cheap, and ``_ensure_worktree`` recreates
        it on the next cycle if the workflow is retried/resumed) and KEEPS the
        git branch. Deleting a branch whose PR is still open would orphan the PR,
        so branch deletion is reserved for the post-merge success path
        (``remove_branch=True``), where the PR is already merged.

        Dirty-worktree guard (review P1-b): on the terminal-FAILURE path
        (``reason="failed"``) a dirty worktree (uncommitted edits or untracked
        files) is retained rather than force-removed — the branch only holds
        committed content, so force-removing would discard working-tree state
        that ``_ensure_worktree`` cannot recreate on retry. The worktree and
        branch are kept on disk and the reason is appended to ``error_message``;
        this method returns ``False`` to signal nothing was reclaimed. The
        completed/merged path (``reason="completed"``) is expected clean and is
        reclaimed directly. Full reclamation of a retained dirty worktree is
        handled later by the #2043 reconciler or an operator, not here.

        Best-effort: failures are logged, never raised, so cleanup can't mask the
        failure that triggered it. ``worktree_path``/``branch_name`` are cleared
        in the DB only after the corresponding removal succeeds. Returns whether
        the removal completed without raising.
        """
        wf = self._orch.workflow or {}
        branch_name = wf.get("branch_name", "")
        worktree_path = wf.get("worktree_path", "")
        project_path = wf.get("project_path", "")
        if not worktree_path and not branch_name:
            return True
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            try:
                user = UserRepository().get_user_by_id(user_id)
                if user:
                    system_account = user.get("system_account")
            except Exception as e:
                logger.warning("Could not resolve system_account for cleanup: %s", e)
        try:
            if remove_worktree and worktree_path:
                # P1-b dirty guard — terminal-FAILURE path only (reason="failed").
                # A completed/merged workflow has committed its working state, so
                # its worktree is expected clean and is reclaimed directly. Only a
                # failure-path worktree is protected from force-removal: it may
                # hold uncommitted agent edits, failed-test fixes, conflict/
                # diagnostic state, or untracked artifacts the branch cannot
                # preserve and ``_ensure_worktree`` cannot recreate on retry.
                # Reclamation of a retained dirty worktree is left to the #2043
                # reconciler / an operator; this method only guarantees it does
                # not destroy state.
                if reason == "failed":
                    wt_gh = _GitHubOps(worktree_path, system_account=system_account)
                    if wt_gh.has_uncommitted_changes():
                        logger.warning(
                            "Keeping dirty worktree %s (workflow %s) for debug "
                            "after terminal failure — force-remove would lose "
                            "uncommitted/untracked state; branch=%s also retained",
                            worktree_path,
                            self._orch._workflow_id[:8],
                            branch_name or "(none)",
                        )
                        existing_err = wf.get("error_message") or ""
                        marker = f"[worktree kept at {worktree_path}"
                        if marker in existing_err:
                            # Already noted by a prior pass (e.g. a repeated
                            # advance on a still-failed workflow) — don't
                            # duplicate the retention note (review P2).
                            return False
                        note = f"{marker}: uncommitted changes preserved for debug]"
                        self._orch._update_workflow(
                            {"error_message": (existing_err + " " + note).strip()}
                            if existing_err
                            else {"error_message": note}
                        )
                        # Intentional retention: nothing was removed. Leave
                        # worktree_path/branch_name set so the retained state is
                        # visible to operators and a future #2043 reconciler.
                        return False
                # Must use the main repo's gh — a worktree can't remove itself.
                main_gh = _GitHubOps(project_path, system_account=system_account)
                main_gh.remove_worktree(worktree_path)
                self._orch._update_workflow({"worktree_path": ""})
            if remove_branch and branch_name:
                gh = _GitHubOps(project_path, system_account=system_account)
                result = gh.delete_branch(branch_name)
                # delete_branch returns a structured {local, remote, errors} dict
                # (#2043). 'absent' is success-equivalent; only 'failed' counts.
                if result.get("local") == "failed" or result.get("remote") == "failed":
                    failed_parts = [
                        p
                        for p, v in (
                            ("local", result.get("local")),
                            ("remote", result.get("remote")),
                        )
                        if v == "failed"
                    ]
                    raise GitHubOpsError(
                        f"Branch deletion failed ({'/'.join(failed_parts)}): "
                        + "; ".join(result.get("errors") or [])[:300]
                    )
                self._orch._update_workflow({"branch_name": ""})
        except GitHubOpsError as e:
            logger.warning("Cleanup (%s) failed: %s", reason, e)
            return False
        except Exception as e:
            logger.warning("Cleanup (%s) raised: %s", reason, e)
            return False
        return True

    def sync_worktree_to_pr_remote_head(self, wt_gh: GitHubOps, branch_name: str) -> None:
        """Sync a merge worktree to the PR branch's authoritative remote head.

        ``add_worktree`` checks out the *local* ``auto-dev/*`` ref, which can
        drift ahead of the remote tip: a prior merge/repair attempt may have
        advanced it locally (e.g. a merge commit that already contains main)
        without ever pushing. A later cycle then re-checks out that stale local
        branch, the merge into main becomes a no-op ("Already up to date"), and
        the resolver fails with "made no commit" even though the remote PR head
        is still behind main and genuinely needs the sync (workflow 1895).
        Fetch the remote branch and reset the local ref/HEAD to it so the merge
        starts from the real PR state. Failure is non-fatal: a fetch/reset error
        leaves the worktree at the local ref, preserving prior behavior.
        """
        try:
            wt_gh._run_git(["fetch", "origin", branch_name])
            remote_head = wt_gh.resolve_commit("FETCH_HEAD")
            wt_gh._run_git(["reset", "--hard", remote_head])
        except Exception as sync_exc:
            logger.warning(
                "Could not sync merge worktree to remote head of %s: %s",
                branch_name,
                sync_exc,
            )

    def resolve_merge_conflicts(self, gh: GitHubOps, branch_name: str, pr_number: int):
        """Resolve merge conflicts in an isolated worktree, push, and merge the PR.

        Previously this checked out the PR branch directly in the main repo,
        which polluted the shared working tree (``index.lock`` races with
        concurrent workflows, ``reset --hard`` clobbered in-flight resolution
        on scheduler re-entry). Now a throwaway worktree is created for the
        branch, all merge/resolve/push happens inside it, and it is removed in
        a ``finally`` — the main repo's index and HEAD are never touched.

        The workflow's own worktree is temporarily removed to free the branch
        for the temp worktree (git forbids the same branch in two worktrees),
        then **restored** in the ``finally`` block. Without restoration,
        subsequent phases (PR review push, CI repair re-entry) operate on the
        main repo (HEAD=main) and fail with a branch mismatch.
        """
        wf = self._orch.workflow or {}
        project_path = wf.get("project_path", "")
        worktree_path = wf.get("worktree_path", "")
        # Get system_account for multi-user permission isolation (Issue #1395)
        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)
            if user:
                system_account = user.get("system_account")

        # Git forbids checking out the same branch in two worktrees, so the
        # workflow's own worktree (if still present) must be removed first to
        # free the branch for the temp worktree below.
        #
        # Issue #2041: the whole transition (remove original → create temp →
        # resolve → remove temp → restore original) is one outer try/finally so
        # no step can leave the workflow operating on the main checkout. The DB
        # is cleared only AFTER git confirms removal, temp creation lives inside
        # the try, and a restore failure fails closed.
        main_gh = _GitHubOps(project_path, system_account=system_account)
        # Place the temp merge worktree inside the project's .worktrees/ dir
        # (the same convention as normal workflow worktrees — see
        # _get_preferred_worktree_path). The previous ../merge-{id} sibling
        # path failed on macOS with EPERM "could not create leading
        # directories" because the server process lacks TCC/write permission
        # to create new directories directly under ~/workspace (#1827).
        temp_wt_path = os.path.normpath(
            os.path.join(project_path, ".worktrees", f"merge-{self._orch._workflow_id[:8]}")
        )
        original_removed = False
        temp_created = False
        # The path to restore at the end. Prefer the live worktree_path, fall
        # back to preferred_worktree_path (it is what _ensure_worktree recreates
        # from if worktree_path is empty). Captured up front so the journal can
        # restore even if the process is SIGKILLed mid-transition (#2050).
        original_path_for_journal = worktree_path or wf.get("preferred_worktree_path") or ""
        try:
            if worktree_path:
                # Persist the removal intent BEFORE the git side effect so a
                # SIGKILL anywhere in this block leaves the journal able to
                # reconcile (#2050). transition_original_path records the path
                # to restore; transition_temp_path records the temp to tear down.
                self._orch._set_transition_state(
                    "removing_original",
                    original_path=original_path_for_journal,
                    temp_path=temp_wt_path,
                )
                # Fail closed on removal failure: only clear the DB once git has
                # actually freed the branch, and never assume removal succeeded.
                self._orch._remove_worktree_idempotent(main_gh, worktree_path)
                # One atomic write: clear worktree_path AND advance state so the
                # empty path is never observable without its transition context.
                self._orch._update_workflow(
                    {"worktree_path": "", "worktree_transition_state": "original_removed"}
                )
                # The caller's gh still points at the now-deleted worktree dir
                # as its cwd. Rebind it (and cached self._gh) to the main repo
                # so later cleanup doesn't run subprocess with a gone cwd (#1107).
                gh = _GitHubOps(project_path, system_account=system_account)
                self._orch._gh = gh
                original_removed = True

            # Create an isolated worktree for the existing PR branch. Use the
            # main repo's gh so the worktree is registered against the real .git.
            # Lives inside the try so a creation failure still triggers restore.
            main_gh.add_worktree(temp_wt_path, branch_name)
            logger.info("Created temporary merge worktree at %s", temp_wt_path)
            temp_created = True
            # Advance the journal: a successful add_worktree is the proof the
            # temp is attached. If the process dies here, reconcile re-queries
            # the registry to observe the temp and tear it down (#2050).
            self._orch._set_transition_state("temp_attached")

            # All subsequent git ops run inside the temp worktree.
            wt_gh = _GitHubOps(temp_wt_path, system_account=system_account)
            # Sync the checked-out branch to the PR's authoritative remote head
            # before merging main (see _sync_worktree_to_pr_remote_head).
            self._orch._sync_worktree_to_pr_remote_head(wt_gh, branch_name)
            original_pr_head = wt_gh.get_current_commit()
            conflict_ms_id = ""
            milestone_result = {}
            # Fetch latest main and merge into the branch.
            wt_gh._run_git(["fetch", "origin", "main"])
            # Worktrees share their common git dir, so another workflow can
            # move origin/main while this resolver spends minutes editing and
            # testing. FETCH_HEAD is the object guaranteed by the command
            # above; pin it for the merge and every later graph/scope gate.
            fetched_main_head = wt_gh.resolve_commit("FETCH_HEAD")
            merge_result = wt_gh._run_git(["merge", fetched_main_head], check=False)
            # git writes conflict summaries to STDOUT (not stderr), so we must
            # check both streams. Checking only stderr left stderr empty on a
            # real conflict and the code misclassified it as a "non-conflict"
            # failure, abandoning merge without ever invoking the AI resolver.
            combined_output = f"{merge_result.stdout}\n{merge_result.stderr}"
            if merge_result.returncode != 0:
                # Locale-dependent git builds may print "CONFLICT" translated.
                # The index is authoritative: any unmerged path has a U-stage
                # entry regardless of stdout/stderr language.
                has_conflict_marker = "CONFLICT" in combined_output
                unmerged_query_error = ""
                if not has_conflict_marker:
                    try:
                        unmerged_result = wt_gh._run_git(
                            ["diff", "--name-only", "--diff-filter=U"], check=False
                        )
                        unmerged_output = getattr(unmerged_result, "stdout", "")
                        has_conflict_marker = isinstance(unmerged_output, str) and bool(
                            unmerged_output.strip()
                        )
                    except Exception as exc:
                        unmerged_query_error = str(exc)
                if not has_conflict_marker:
                    detail = combined_output.strip() or f"exit code {merge_result.returncode}"
                    if unmerged_query_error:
                        detail += f"; unable to inspect unmerged index: {unmerged_query_error}"
                    raise GitHubOpsError(f"git merge failed (non-conflict): {detail}")

                initial_unmerged_paths = wt_gh.get_unmerged_paths()
                if not initial_unmerged_paths:
                    raise GitHubOpsError(
                        "git merge reported a conflict but the index has no unmerged paths"
                    )
                # Snapshot the complete conflicted index before exposing the
                # worktree to the agent. PATH wrappers are policy guidance,
                # not a security boundary: an agent or repository script can
                # invoke an absolute git binary or write the index indirectly.
                resolver_index_before = wt_gh.get_index_snapshot()

                # Ask AI agent to resolve conflicts inside the temp worktree.
                conflict_prompt = (
                    AUTONOMOUS_CONTEXT
                    + "当前分支与 main 存在合并冲突。请解决所有冲突文件中的冲突标记，"
                    "保留两边的有效修改。\n\n"
                    f"编排器已经把你放在唯一允许操作的临时 worktree：`{temp_wt_path}`。\n"
                    f"该 worktree 已检出目标分支 `{branch_name}` 并处于 merge conflict 状态。\n"
                    "禁止切换/checkout 其他分支，禁止调用 EnterWorktree，禁止到主仓或其他"
                    " worktree 查找冲突；直接处理当前目录的 U-stage 文件。\n\n"
                )
                # Issue reference is available in this method's scope
                issue_number = wf.get("github_issue_number") or self._orch.workflow.get(  # type: ignore[union-attr]
                    "github_issue_number"
                )
                if issue_number:
                    conflict_prompt += (
                        f"## 关联 Issue\n"
                        f"本任务关联 GitHub Issue #{issue_number}。\n"
                        f"冲突解决时请确保修改满足 Issue #{issue_number} 的所有需求。\n\n"
                    )
                conflict_prompt += (
                    "步骤：\n"
                    "1. 查看所有冲突文件：git diff --name-only --diff-filter=U\n"
                    "2. 逐个解决冲突标记（<<<<<<, ======, >>>>>>）\n"
                    "3. 运行测试验证冲突解决没有破坏功能（不能跳过）：\n"
                    "   - python -m pytest 或 python3 -m pytest\n"
                    "   - 如果有测试失败，分析原因并修复，然后重新测试\n"
                    "   - 特别注意：main 上的改动可能修改了函数签名/SQL/接口，\n"
                    "     冲突文件相关的测试也需要同步更新\n"
                    "   - 重复直到所有测试通过\n"
                    "4. 测试全部通过后直接返回总结；不要执行 git add、git commit 或 git push，\n"
                    "   暂存、提交与推送由编排器在校验冲突标记和提交图后完成。\n\n"
                    "## 总结报告（必须）\n"
                    "在回复末尾简要总结：\n"
                    "- 解决了哪些文件的冲突\n"
                    "- 是否执行了测试，测试结果如何（如 42 passed, 0 failed）\n"
                    "- 如果跳过了测试，说明原因\n"
                    "- 这个总结会显示在工作流的 timeline 中，供用户查看"
                )

                wf = self._orch.workflow or {}
                # _run_agent derives its authoritative repository path and the
                # prompt execution contract from ``wf``. Passing project_path
                # alone is insufficient: the workflow's cleared worktree_path
                # would otherwise override it back to the shared main repo.
                # Bind a per-call workflow snapshot to the isolated resolver
                # worktree without changing the persisted workflow row.
                conflict_wf = dict(wf)
                conflict_wf.update(
                    {
                        "branch_strategy": "worktree",
                        "worktree_path": temp_wt_path,
                        "branch_name": branch_name,
                    }
                )
                # Track this as its own milestone so conflict-resolution usage is
                # captured in phase_* (and thus workflow totals = SUM(phase_*)).
                conflict_ms = self._orch._create_milestone(
                    phase="merge",
                    dev_round=wf.get("dev_round", 1),
                    milestone_type="conflicts_resolved",
                    status="in_progress",
                    title=f"Resolving merge conflicts (PR #{pr_number})",
                )
                result = self._orch._run_agent(
                    wf=conflict_wf,
                    workflow_id=self._orch._workflow_id,
                    cli_tool=wf.get("cli_tool", "claude-code"),
                    model=wf.get("model", ""),
                    project_path=temp_wt_path,
                    prompt=conflict_prompt,
                    workspace_type=wf.get("workspace_type", "local"),
                    remote_machine_id=wf.get("remote_machine_id"),
                    permission_mode=wf.get("permission_mode", "auto-edit"),
                    allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(
                        wf.get("cli_tool", "claude-code"), []
                    ),
                    session_line="fresh",
                    milestone_id=conflict_ms.get("milestone_id", ""),
                )
                self._orch._accumulate_tokens(result)
                response_text = self._orch._artifact_text(result)
                conflict_ms_id = conflict_ms.get("milestone_id", "")
                milestone_result = {
                    "session_id": result.session_id,
                    "result_summary": response_text,
                    "tldr": self._orch._artifact_tldr(result),
                }
                if not result.success:
                    self._orch.repo.update_milestone(
                        conflict_ms_id,
                        {
                            **milestone_result,
                            "status": "failed",
                            "error_message": result.error or "Conflict resolution failed",
                        },
                    )
                    raise RuntimeError(f"Conflict resolution failed: {result.error}")

                try:
                    # The agent is intentionally edit/test-only: its command
                    # guard denies mutating git operations.  The trusted
                    # orchestrator owns staging and the merge commit after
                    # verifying both the working tree and branch identity.
                    current_branch = wt_gh.get_current_branch()
                    if not isinstance(current_branch, str) or current_branch != branch_name:
                        raise RuntimeError(
                            "Conflict resolution branch mismatch before commit: "
                            f"expected={branch_name!r}, actual={current_branch!r}"
                        )
                    unmerged_paths = wt_gh.get_unmerged_paths()
                    marker_paths = wt_gh.get_conflict_marker_paths(
                        sorted(set(initial_unmerged_paths) | set(unmerged_paths))
                    )
                    if marker_paths:
                        raise RuntimeError(
                            "Conflict resolver left conflict markers in: "
                            + ", ".join(marker_paths[:10])
                        )
                    agent_head = wt_gh.get_current_commit()
                    if agent_head != original_pr_head:
                        raise RuntimeError(
                            "Conflict resolver changed HEAD; commits and merge-state changes "
                            "are reserved for the orchestrator"
                        )
                    resolver_index_after = wt_gh.get_index_snapshot()
                    resolver_index_changes = wt_gh.get_index_changed_paths(
                        resolver_index_before, resolver_index_after
                    )
                    if resolver_index_changes:
                        raise RuntimeError(
                            "Conflict resolver changed the Git index; staging is reserved for "
                            "the orchestrator. Paths: " + ", ".join(resolver_index_changes[:10])
                        )
                    resolver_changed_paths = wt_gh.get_worktree_changed_paths()
                    resolver_scope_error = self._orch._scope_violation(resolver_changed_paths)
                    if resolver_scope_error:
                        raise RuntimeError(
                            "Conflict resolver scope rejected before staging: "
                            f"{resolver_scope_error}"
                        )
                    wt_gh.git_add_all()
                    remaining_unmerged = wt_gh.get_unmerged_paths()
                    if remaining_unmerged:
                        raise RuntimeError(
                            "Conflict resolver left unmerged paths after staging: "
                            + ", ".join(remaining_unmerged[:10])
                        )
                    wt_gh.git_commit(
                        f"merge: resolve conflicts for PR #{pr_number}", no_verify=True
                    )
                except Exception as exc:
                    self._orch.repo.update_milestone(
                        conflict_ms_id,
                        {
                            **milestone_result,
                            "status": "failed",
                            "error_message": str(exc),
                        },
                    )
                    raise

                # Do not mark the milestone complete until the shared
                # pre-push commit-graph postconditions below have passed.

            try:
                # Push the resolved branch. The new merge commit triggers a
                # fresh CI run, so _do_merge retries on the next scheduler
                # cycle once checks are green.
                # Fail closed on branch drift. Rewriting branch_name from the
                # current checkout could push an unrelated branch.
                current_branch = wt_gh.get_current_branch()
                if not isinstance(current_branch, str) or current_branch != branch_name:
                    raise RuntimeError(
                        "Conflict resolution branch mismatch before push: "
                        f"expected={branch_name!r}, actual={current_branch!r}"
                    )
                resolved_head = wt_gh.get_current_commit()
                if (
                    not isinstance(original_pr_head, str)
                    or not original_pr_head
                    or not isinstance(resolved_head, str)
                    or not resolved_head
                ):
                    raise RuntimeError("Unable to verify merge commit identity before push")
                if resolved_head == original_pr_head:
                    raise RuntimeError("Merge resolution made no commit; refusing unchanged push")

                pr_head_in_result = self._orch._ancestor_check(
                    wt_gh, original_pr_head, resolved_head
                )
                main_head_in_result = self._orch._ancestor_check(
                    wt_gh, fetched_main_head, resolved_head
                )
                if pr_head_in_result is not True or main_head_in_result is not True:
                    raise RuntimeError(
                        "Merge commit ancestry verification failed before push: "
                        f"pr_head={pr_head_in_result!r}, origin_main={main_head_in_result!r}"
                    )
                merge_scope_wf = dict(wf)
                merge_scope_wf["base_commit_sha"] = fetched_main_head
                # original_pr_head..resolved_head includes every upstream file
                # brought in by the merge.  That is not autonomous resolver
                # scope and can exceed the file cap on an old PR even when the
                # agent touched one conflict.  The resolver's actual edit set
                # was checked before staging above; this common gate now checks
                # the cumulative PR delta relative to the fetched main.
                scope_error = self._orch._validate_autonomous_change_scope(
                    wt_gh, merge_scope_wf, fetched_main_head, resolved_head
                )
                if scope_error:
                    raise RuntimeError(
                        f"Conflict resolution scope rejected before push: {scope_error}"
                    )
                wt_gh.git_push(branch=branch_name, force_with_lease=True)
                self._orch._record_trusted_head(wt_gh, pushed=True, sha=resolved_head)
            except Exception as exc:
                if conflict_ms_id:
                    self._orch.repo.update_milestone(
                        conflict_ms_id,
                        {
                            **milestone_result,
                            "status": "failed",
                            "error_message": str(exc),
                        },
                    )
                raise

            if conflict_ms_id:
                self._orch.repo.update_milestone(
                    conflict_ms_id,
                    {
                        **milestone_result,
                        "status": "completed",
                        "error_message": "",
                    },
                )
            self._orch._create_milestone(
                phase="merge",
                milestone_type="conflicts_pushed",
                status="completed",
                title=(
                    f"PR #{pr_number} conflicts resolved, waiting for CI to merge"
                    if conflict_ms_id
                    else f"PR #{pr_number} synchronized with main, waiting for CI"
                ),
            )
        finally:
            # Tear down the temp worktree if it was actually created, so it does
            # not leak and block future runs. Use the main repo's gh because a
            # worktree cannot remove itself. Skip when it was never created
            # (e.g. the original worktree removal failed first) to avoid a
            # spurious "failed to remove" warning on a path that doesn't exist.
            if temp_created:
                try:
                    main_gh.remove_worktree(temp_wt_path)
                    logger.info("Removed temporary merge worktree at %s", temp_wt_path)
                except GitHubOpsError as e:
                    logger.warning("Failed to remove temp worktree %s: %s", temp_wt_path, e)

            # Restore the workflow's original worktree so subsequent phases
            # (PR review push, CI repair, _do_merge re-entry) operate on the
            # isolated branch, not the main repo (HEAD=main). Without this,
            # _do_pr_review's pre-push branch check fails with
            # "expected auto-dev/xxx, actual main" and the workflow is stuck.
            #
            # Only restore if we actually removed it (Issue #2041): a failed
            # removal leaves the original worktree live in git, and an attempted
            # restore would error. A restore failure fails CLOSED — never let a
            # later phase run on the main checkout.
            if original_removed:
                # Mark the restore intent before doing git work, so a SIGKILL
                # during restore converges on the `restoring` state and the
                # reconcile resumes idempotently (#2050).
                self._orch._set_transition_state("restoring")
                try:
                    # If remove_worktree succeeded earlier, the dir is gone
                    # and we recreate it. If it failed, the dir is still
                    # there and add_worktree would error — check first.
                    git_file = os.path.join(worktree_path, ".git")
                    if not main_gh.path_exists_as_user(git_file, file_only=True):
                        main_gh.add_worktree(worktree_path, branch_name)
                    self._orch._verify_worktree_restored(main_gh, worktree_path, branch_name)
                    # Converge in a single write: restore worktree_path AND clear
                    # the journal together so they can never disagree (#2050).
                    self._orch._clear_transition_journal(worktree_path=worktree_path)
                    self._orch._gh = _GitHubOps(worktree_path, system_account=system_account)
                    logger.info("Restored workflow worktree at %s", worktree_path)
                except Exception as e:
                    logger.error(
                        "Failed to restore worktree %s after merge resolution: %s",
                        worktree_path,
                        e,
                        exc_info=True,
                    )
                    # Fail CLOSED and keep the journal + metadata for diagnosis.
                    self._orch._fail_transition_closed(
                        f"worktree restore failed after merge resolution: {e}",
                        error_message=(f"worktree restore failed after merge resolution: {e}"),
                    )
                    raise

    def clear_transition_journal(self, *, worktree_path: str | None = None) -> None:
        """Converge the journal to the stable state (#2050).

        Clears every journal field and optionally restores ``worktree_path`` in
        the SAME write, so the cleared path and the cleared state can never be
        observed separately.
        """
        updates: dict = {
            "worktree_transition_state": None,
            "transition_original_path": None,
            "transition_temp_path": None,
            "transition_error": None,
            "transition_started_at": None,
            "transition_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if worktree_path is not None:
            updates["worktree_path"] = worktree_path
        self._orch._update_workflow(updates)

    def verify_worktree_registered(self, gh: GitHubOps, path: str, branch: str) -> None:
        """Confirm ``path`` is registered in ``git worktree list`` on ``branch``.

        Distinct from ``_verify_worktree_restored`` only in naming; both check
        the registry, but this one is used to confirm the temp was actually
        attached before advancing the journal (#2050).

        Falls back to ``resolve_worktree_branch`` when the porcelain ``branch``
        field is transiently missing (same APFS lag as ``verify_worktree_restored``).
        """
        entries = gh.list_worktrees()
        if self._orch._is_registered_on_branch(entries, path, branch):
            return
        # Check if the entry exists but branch is transiently None (APFS lag).
        entry = next((w for w in entries if w.get("path") == path), None)
        if entry is not None and entry.get("branch") is None and not entry.get("detached"):
            resolved = gh.resolve_worktree_branch(path)
            if resolved is not None and resolved in (branch, f"refs/heads/{branch}"):
                logger.info(
                    "Worktree %s branch resolved via symbolic-ref fallback "
                    "(porcelain branch field was transiently missing)",
                    path,
                )
                return
        raise RuntimeError(
            f"worktree {path} not registered on branch {branch!r} (registry: {entries!r})"
        )

    def path_within_worktrees_root(self, path: str, project_path: str) -> bool:
        """True if ``path`` lives under ``<project_path>/.worktrees`` (#2050).

        Used by reconcile to refuse operating on foreign / out-of-root paths
        (fail closed instead of touching worktrees we cannot prove we own).
        """
        if not path or not project_path:
            return False
        try:
            root = os.path.normpath(os.path.join(project_path, ".worktrees"))
            return os.path.commonpath([root, os.path.normpath(path)]) == root
        except ValueError:
            # commonpath raises on different drives (Windows) / absolute mismatches.
            return False

    def reconcile_worktree_transition(self, wf: dict) -> None:
        """Recover an interrupted merge-conflict worktree transition (#2050).

        Single idempotent entry point. Combines the persisted transition intent
        (``worktree_transition_state`` + path fields) with the OBSERVED git
        registry / disk state to either restore the original worktree to a
        stable state, or fail the workflow closed. Never resets/deletes/recreates
        branch refs — that is #2042's head-authority job.

        Goal is to discard any half-finished temp worktree and get back to the
        stable original worktree so the next round can re-run merge/conflict from
        a known-good state. A SIGKILL cannot prove the temp's edits/commits/push
        were complete or trustworthy.
        """
        state = (wf.get("worktree_transition_state") or "").strip()
        if not state:
            return  # stable, nothing to reconcile
        if state == "recovery_failed":
            # Already failed closed; do not start an agent or touch git.
            logger.warning(
                "Workflow %s is in recovery_failed; skipping reconcile",
                self._orch._workflow_id[:8],
            )
            return

        project_path = wf.get("project_path", "") or ""
        branch_name = wf.get("branch_name", "") or ""
        original_path = (
            wf.get("transition_original_path")
            or wf.get("preferred_worktree_path")
            or wf.get("worktree_path")
            or ""
        )
        temp_path = wf.get("transition_temp_path") or ""

        # Fail closed if we cannot confirm the original path or its root. We
        # must know what to restore before touching git.
        if not original_path:
            self._orch._fail_transition_closed(
                "cannot reconcile: transition_original_path is empty "
                "and no preferred/worktree path is available"
            )
            return
        if not branch_name:
            self._orch._fail_transition_closed(
                "cannot reconcile: branch_name is empty, cannot verify worktree"
            )
            return
        # Refuse foreign roots: only operate inside the project's .worktrees dir.
        if not self._orch._path_within_worktrees_root(original_path, project_path):
            self._orch._fail_transition_closed(
                f"cannot reconcile: original_path {original_path!r} is outside "
                f"the project worktrees root"
            )
            return
        if temp_path and not self._orch._path_within_worktrees_root(temp_path, project_path):
            self._orch._fail_transition_closed(
                f"cannot reconcile: temp_path {temp_path!r} is outside "
                f"the project worktrees root"
            )
            return

        system_account = None
        user_id = wf.get("user_id")
        if user_id:
            try:
                from app.repositories.user_repo import UserRepository

                user = UserRepository().get_user_by_id(user_id)
                if user:
                    system_account = user.get("system_account")
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not resolve system_account for reconcile: %s", e)
        main_gh = _GitHubOps(project_path, system_account=system_account)

        try:
            entries = main_gh.list_worktrees()
        except Exception as e:  # noqa: BLE001
            # Cannot safely judge ownership without the registry → fail closed.
            self._orch._fail_transition_closed(f"git worktree list failed: {e}")
            return

        def _registered(path: str) -> bool:
            return any(w.get("path") == path for w in entries)

        def _on_branch(path: str) -> bool:
            return self._orch._is_registered_on_branch(entries, path, branch_name)

        try:
            if state == "removing_original":
                if _registered(original_path):
                    if _on_branch(original_path):
                        # Original never actually removed — nothing to undo.
                        self._orch._clear_transition_journal(worktree_path=original_path)
                        logger.info(
                            "Reconcile %s: original still registered; cleared journal",
                            state,
                        )
                        return
                    # Registered but wrong branch → ambiguous, fail closed.
                    self._orch._fail_transition_closed(
                        f"original worktree {original_path!r} registered on "
                        f"unexpected branch (expected {branch_name!r})"
                    )
                    return
                # Original gone → removal happened. Clean any temp, then restore.
                self._orch._reconcile_cleanup_temp_and_restore(
                    main_gh, entries, temp_path, original_path, branch_name, system_account
                )
                return

            if state in ("original_removed", "temp_attached"):
                # Either way: tear down any temp, then restore the original.
                self._orch._reconcile_cleanup_temp_and_restore(
                    main_gh, entries, temp_path, original_path, branch_name, system_account
                )
                return

            if state == "restoring":
                if _on_branch(original_path):
                    # Restore already completed before the SIGKILL; converge.
                    self._orch._clear_transition_journal(worktree_path=original_path)
                    self._orch._gh = _GitHubOps(original_path, system_account=system_account)
                    logger.info("Reconcile restoring: original already restored; converged")
                    return
                # Otherwise resume the idempotent restore (also cleans temp).
                self._orch._reconcile_cleanup_temp_and_restore(
                    main_gh, entries, temp_path, original_path, branch_name, system_account
                )
                return

            # Unknown state → fail closed rather than guess.
            self._orch._fail_transition_closed(
                f"cannot reconcile: unknown transition state {state!r}"
            )
        except _ReconcileFailed as e:
            self._orch._fail_transition_closed(str(e))
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Reconcile failed for %s: %s", self._orch._workflow_id[:8], e, exc_info=True
            )
            self._orch._fail_transition_closed(f"reconcile raised: {e}")

    def remove_worktree_idempotent(self, gh: GitHubOps, path: str) -> None:
        """Remove a worktree, treating "already absent" as success (Issue #2041).

        ``git worktree remove`` errors if the worktree is already gone (e.g. it
        was cleaned up externally or by a previous partial run). That is not a
        failure: the branch is already free. Only re-raise if the worktree is
        still registered, which means the removal genuinely failed and the
        branch is still occupied.
        """
        try:
            gh.remove_worktree(path)
        except GitHubOpsError as exc:
            # Probe the registry to distinguish "already gone" from a real
            # failure. If the probe itself errors, re-raise the ORIGINAL
            # removal error so it isn't masked by a less actionable one.
            try:
                still_registered = any(w.get("path") == path for w in gh.list_worktrees())
            except Exception:
                raise exc
            if still_registered:
                raise
            logger.info("Worktree %s already absent (treated as removed): %s", path, exc)

    def verify_worktree_restored(self, gh: GitHubOps, path: str, branch: str) -> None:
        """Post-restore verification (Issue #2041 acceptance #6).

        Confirms the restored worktree is registered in ``git worktree list`` on
        the expected branch. (The restore gate above already ensured the linked
        ``.git`` pointer exists before reaching here.) Any failure propagates so
        the caller can fail closed.

        On macOS APFS, ``git worktree list --porcelain`` can transiently omit
        the ``branch`` field right after ``git worktree add`` (the registry
        cache has not yet settled). When the entry exists but ``branch`` is
        ``None`` (and ``detached`` is not set), we fall back to
        ``GitHubOps.resolve_worktree_branch`` which reads the worktree's own
        HEAD via ``git symbolic-ref`` — the authoritative source, unaffected by
        the registry-cache lag. A *wrong* branch name (not ``None``) fails
        immediately without fallback, as it indicates a real misconfiguration.
        """
        entries = [w for w in gh.list_worktrees() if w.get("path") == path]
        if not entries:
            raise RuntimeError(f"restored worktree {path} missing from `git worktree list`")
        entry = entries[0]
        actual = entry.get("branch")
        # git may report either "branch" or "refs/heads/branch" depending on version.
        if actual in (branch, f"refs/heads/{branch}"):
            return
        # branch field missing AND not explicitly detached → APFS transient lag.
        # Fall back to the worktree's own HEAD (authoritative) before failing.
        if actual is None and not entry.get("detached"):
            resolved = gh.resolve_worktree_branch(path)
            if resolved is not None and resolved in (branch, f"refs/heads/{branch}"):
                logger.info(
                    "Worktree %s branch resolved via symbolic-ref fallback "
                    "(porcelain branch field was transiently missing)",
                    path,
                )
                return
            # resolved is None (detached/unreadable) or wrong branch → fail closed.
            raise RuntimeError(
                f"restored worktree {path} on wrong branch: "
                f"expected={branch!r}, actual={actual!r}"
                f" (symbolic-ref fallback returned {resolved!r})"
            )
        raise RuntimeError(
            f"restored worktree {path} on wrong branch: " f"expected={branch!r}, actual={actual!r}"
        )
