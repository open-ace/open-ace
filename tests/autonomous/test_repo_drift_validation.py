"""Regression tests for the repo-drift post-run guardrail.

``_validate_repo_context_after_run`` flags any case where the main repo HEAD
moved during an agent run while the worktree HEAD did not. That signal alone
also fires for a benign external ``git pull`` on the main repo, which is common
on shared dev machines. The guardrail distinguishes the two by requiring BOTH
that main moved *forward* (``before`` is an ancestor of ``after``) AND that the
new HEAD came from the remote (``after`` is an ancestor of ``origin/main``).

Covers:
  - External ``git pull`` (forward + on origin/main) → allowed.
  - Agent local commit on main (forward, NOT on origin/main) → blocked.
  - Main reset to an older remote commit (NOT forward, on origin/main) → blocked.
  - Non-fast-forward main rewrite (NOT forward, NOT on origin/main) → blocked.
  - Worktree HEAD also moved → allowed (pre-existing behaviour preserved).
  - Benign-pull probe itself raises → conservatively allowed.
"""

import os
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

MAIN_REPO = "/srv/open-ace"
WORKTREE = "/srv/open-ace/.worktrees/wf-drift"

# SHAs used as HEAD values in the scenarios below.
MAIN_BEFORE = "93f4753ae15c5e7cb0dec5c5bbfe5971e93dfec9"
MAIN_AFTER_PULL = "a7409645a750904ecf1d313aa412169db41c4866"  # forward + on origin/main
MAIN_AFTER_LOCAL = "a1b2c3d40000000000000000000000000000aaaa"  # forward, NOT on origin/main
MAIN_AFTER_ROLLBACK = (
    "522e220a0000000000000000000000000000000"  # older remote commit (reset target)
)
MAIN_AFTER_REWRITE = "ff00bad0000000000000000000000000000bad00"  # history rewrite
WORKTREE_HEAD = "a7409645a750904ecf1d313aa412169db41c4866"


def _make_orchestrator():
    wf = {
        "workflow_id": "wf-drift",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/wf-drift",
        "worktree_path": WORKTREE,
        "project_path": MAIN_REPO,
        "workspace_type": "local",
    }
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    return o


def _before_state(main_head, effective_head=WORKTREE_HEAD):
    """Build the dict shape produced by _snapshot_repo_context (worktree)."""
    return {
        "context": {
            "strategy": "worktree",
            "project_path": MAIN_REPO,
            "worktree_path": WORKTREE,
            "repo_path": WORKTREE,
            "expected_branch": "auto-dev/wf-drift",
        },
        "effective": {
            "repo_path": WORKTREE,
            "top_level": WORKTREE,
            "branch": "auto-dev/wf-drift",
            "head": effective_head,
        },
        "main": {
            "repo_path": MAIN_REPO,
            "top_level": MAIN_REPO,
            "branch": "main",
            "head": main_head,
        },
    }


def _install_fake_gh(
    monkeypatch,
    *,
    after_main_head,
    effective_head=WORKTREE_HEAD,
    moved_forward=True,
    after_on_remote=True,
    probe_raises=False,
):
    """Patch GitHubOps so _capture_repo_state returns scripted heads.

    - Worktree repo (repo_path == WORKTREE) → effective_head, branch auto-dev/wf-drift.
    - Main repo (repo_path == MAIN_REPO) → after_main_head, branch main.
    - ``fetch origin main`` → success (or raises when probe_raises).
    - ``merge-base --is-ancestor before after`` → exit 0 iff moved_forward
      (main moved forward; False for reset/rollback/rewrite).
    - ``merge-base --is-ancestor after origin/main`` → exit 0 iff
      after_on_remote (after came from the remote).
    """

    def factory(repo_path, system_account=None):
        gh = MagicMock()
        if os.path.realpath(repo_path) == os.path.realpath(WORKTREE):
            gh.get_current_branch.return_value = "auto-dev/wf-drift"
            gh.get_current_commit.return_value = effective_head
        else:  # main repo
            gh.get_current_branch.return_value = "main"
            gh.get_current_commit.return_value = after_main_head

        def run_git(args, check=True):
            # probe_raises only breaks the benign-pull probe (fetch +
            # merge-base), NOT the rev-parse/branch/commit probes used by
            # _capture_repo_state — otherwise the after-snapshot itself fails
            # before the probe is ever reached.
            if probe_raises and (
                args[:3] == ["fetch", "origin", "main"]
                or args[:2] == ["merge-base", "--is-ancestor"]
            ):
                raise RuntimeError("probe boom")
            if args[:3] == ["fetch", "origin", "main"]:
                return MagicMock(returncode=0)
            if args[:2] == ["merge-base", "--is-ancestor"]:
                # Distinguish the two probes by argument order:
                #   before→after  (forward check)    args[2]=before, args[3]=after
                #   after→origin  (remote check)     args[2]=after,  args[3]="origin/main"
                if len(args) >= 4 and args[3] == "origin/main":
                    return MagicMock(returncode=0 if after_on_remote else 1)
                return MagicMock(returncode=0 if moved_forward else 1)
            # rev-parse --show-toplevel used by _capture_repo_state
            if args and args[0] == "rev-parse":
                return MagicMock(stdout=repo_path)
            return MagicMock(stdout="", returncode=0)

        gh._run_git.side_effect = run_git
        return gh

    monkeypatch.setattr("app.modules.workspace.autonomous.orchestrator.GitHubOps", factory)


class TestRepoDriftValidation:
    def test_external_pull_is_allowed(self, monkeypatch):
        # Regression core: main HEAD moved forward to a commit already on
        # origin/main (external git pull) while the worktree stayed put →
        # NOT an escape.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_PULL,
            effective_head=WORKTREE_HEAD,
            moved_forward=True,
            after_on_remote=True,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""

    def test_agent_local_commit_on_main_is_blocked(self, monkeypatch):
        # Agent ran a plain `git commit` on main. The new HEAD is forward from
        # before but is NOT on origin/main (not pushed) → must be blocked.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_LOCAL,
            effective_head=WORKTREE_HEAD,
            moved_forward=True,
            after_on_remote=False,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        err = o._validate_repo_context_after_run(before, system_account=None)
        assert "Detected commits on the main repository" in err

    def test_main_reset_to_older_remote_commit_is_blocked(self, monkeypatch):
        # The case the single remote-membership check missed: agent ran
        # `reset --hard <older-remote-commit>`. The new HEAD IS on origin/main
        # (it's an old remote commit) but main did NOT move forward — this is a
        # non-fast-forward rollback that must be blocked.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_ROLLBACK,
            effective_head=WORKTREE_HEAD,
            moved_forward=False,
            after_on_remote=True,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        err = o._validate_repo_context_after_run(before, system_account=None)
        assert "Detected commits on the main repository" in err

    def test_non_fast_forward_main_rewrite_is_blocked(self, monkeypatch):
        # main HEAD changed to a commit that neither moves forward nor is on
        # origin/main (history rewrite / branch switch) — real escape, blocked.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_REWRITE,
            effective_head=WORKTREE_HEAD,
            moved_forward=False,
            after_on_remote=False,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        err = o._validate_repo_context_after_run(before, system_account=None)
        assert "Detected commits on the main repository" in err

    def test_worktree_also_moved_is_allowed(self, monkeypatch):
        # Pre-existing behaviour: if the worktree HEAD also moved, the
        # four-part condition never trips — validation passes regardless of
        # what main did. Guards against regressing the original logic.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_PULL,
            effective_head="cccccccc000000000000000000000000000000",  # worktree moved
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""

    def test_benign_pull_probe_failure_is_allowed(self, monkeypatch):
        # If the fetch / merge-base probe itself raises (network, missing ref)
        # we must NOT escalate a transient probe error into a workflow failure
        # — conservatively allow.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_PULL,
            effective_head=WORKTREE_HEAD,
            probe_raises=True,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""
