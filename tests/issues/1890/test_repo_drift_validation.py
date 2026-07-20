"""Regression tests for repo-drift validation (#1890).

The post-run guardrail in ``_validate_repo_context_after_run`` flags any case
where the main repo HEAD moved during an agent run while the worktree HEAD did
not. Issue #1890 exposed that this also fires for a benign external ``git pull``
on the main repo (a fast-forward), which is common on shared dev machines.

Covers:
  - External ``git pull`` fast-forward on main during an agent run → allowed.
  - Real escape (non-fast-forward main rewrite) → still blocked.
  - Worktree HEAD also moved → allowed (pre-existing behaviour preserved).
  - Fast-forward probe itself raises → conservatively allowed.
"""

import os
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

MAIN_REPO = "/srv/open-ace"
WORKTREE = "/srv/open-ace/.worktrees/wf-1890"

# Short SHAs used as HEAD values in the scenarios below.
MAIN_BEFORE = "93f4753ae15c5e7cb0dec5c5bbfe5971e93dfec9"
MAIN_AFTER_FF = "a7409645a750904ecf1d313aa412169db41c4866"  # descendant of MAIN_BEFORE
MAIN_AFTER_ESCAPE = "ff00bad0000000000000000000000000000bad00"  # not a descendant
WORKTREE_HEAD = "a7409645a750904ecf1d313aa412169db41c4866"


def _make_orchestrator():
    wf = {
        "workflow_id": "wf-1890",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/wf-1890",
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
            "expected_branch": "auto-dev/wf-1890",
        },
        "effective": {
            "repo_path": WORKTREE,
            "top_level": WORKTREE,
            "branch": "auto-dev/wf-1890",
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
    is_ancestor=True,
    is_ancestor_raises=False,
):
    """Patch GitHubOps so _capture_repo_state returns scripted heads.

    - Worktree repo (repo_path == WORKTREE) → effective_head, branch auto-dev/wf-1890.
    - Main repo (repo_path == MAIN_REPO) → after_main_head, branch main.
    - merge-base --is-ancestor exits 0 when is_ancestor else 1, unless
      is_ancestor_raises (then _run_git raises) to test probe-failure handling.
    """

    def factory(repo_path, system_account=None):
        gh = MagicMock()
        if os.path.realpath(repo_path) == os.path.realpath(WORKTREE):
            gh.get_current_branch.return_value = "auto-dev/wf-1890"
            gh.get_current_commit.return_value = effective_head
        else:  # main repo
            gh.get_current_branch.return_value = "main"
            gh.get_current_commit.return_value = after_main_head

        def run_git(args, check=True):
            if args[:2] == ["merge-base", "--is-ancestor"]:
                if is_ancestor_raises:
                    raise RuntimeError("probe boom")
                return MagicMock(returncode=0 if is_ancestor else 1)
            # rev-parse --show-toplevel used by _capture_repo_state
            if args and args[0] == "rev-parse":
                return MagicMock(stdout=repo_path)
            return MagicMock(stdout="", returncode=0)

        gh._run_git.side_effect = run_git
        return gh

    monkeypatch.setattr("app.modules.workspace.autonomous.orchestrator.GitHubOps", factory)


class TestRepoDriftValidation:
    def test_external_pull_fast_forward_is_allowed(self, monkeypatch):
        # Regression core: main HEAD fast-forwarded (external git pull) while
        # the worktree stayed put — must NOT be treated as an escape.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_FF,
            effective_head=WORKTREE_HEAD,
            is_ancestor=True,  # MAIN_BEFORE is an ancestor of MAIN_AFTER_FF
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""

    def test_non_fast_forward_main_rewrite_is_blocked(self, monkeypatch):
        # main HEAD changed to a commit that is NOT a descendant of the before
        # commit (history rewrite / branch switch) — real escape, still blocked.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_ESCAPE,
            effective_head=WORKTREE_HEAD,
            is_ancestor=False,
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
            after_main_head=MAIN_AFTER_FF,
            effective_head="cccccccc000000000000000000000000000000",  # worktree moved
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""

    def test_fast_forward_probe_failure_is_allowed(self, monkeypatch):
        # If the merge-base probe itself raises (detached HEAD, shallow clone,
        # etc.) we must NOT escalate a transient probe error into a workflow
        # failure — conservatively allow.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_FF,
            effective_head=WORKTREE_HEAD,
            is_ancestor_raises=True,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)

        assert o._validate_repo_context_after_run(before, system_account=None) == ""
