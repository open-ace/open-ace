"""Worktree branch recovery after an agent run (#2271).

When an agent leaves the worktree on ``main`` (a read-only checkout — the
feature branch's commits are intact), the post-agent integrity guards and the
pr_review pre-push check fail-closed and permanently fail the workflow (212/208).
``_recover_worktree_branch`` adds a safe recovery: only when the change is
provably read-only does it check the feature branch back out; otherwise it
returns None so callers keep the existing fail-closed safety guard (#1611).

Safe-recovery predicate (all must hold):
  1. ``expected_branch`` ref is unchanged (``== before_head``) — agent didn't
     move the feature branch.
  2. Worktree is clean (``git status --porcelain`` empty) — staged/untracked
     changes would otherwise carry across ``checkout`` into the feature branch.
  3. Wrong-branch HEAD == ``before_main_head`` (the main HEAD captured BEFORE
     the agent ran) — agent didn't commit to / fetch-advance the wrong branch.
     Live ``origin/main`` is NOT trusted (an agent can ``update-ref`` it).
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2271)]


@dataclass
class _GitResult:
    stdout: str = ""
    returncode: int = 0


class _FakeGh:
    """Minimal gh stub for ``_recover_worktree_branch``'s git probes."""

    def __init__(self, *, feature_ref, current_head, porcelain, checkout_ok=True):
        self._feature_ref = feature_ref
        self._current_head = current_head
        self._porcelain = porcelain
        self._checkout_ok = checkout_ok
        self.checkout_args = None

    def _run_git(self, args, check=False):
        if args[0] == "rev-parse":
            return _GitResult(stdout=self._feature_ref)
        if args[0] == "status" and "--porcelain" in args:
            return _GitResult(stdout=self._porcelain)
        if args[0] == "checkout":
            self.checkout_args = args
            if not self._checkout_ok:
                raise RuntimeError("checkout failed")
            return _GitResult(stdout="")
        raise AssertionError(f"unexpected git call: {args}")

    def get_current_commit(self):
        return self._current_head


def _make_orchestrator():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"),
    ):
        return AutonomousOrchestrator("wf-test")


EXPECTED = "auto-dev/abc123"
BEFORE_HEAD = "feat-sha-111"
BEFORE_MAIN = "main-sha-000"


def test_recover_when_safe():
    """Feature ref unchanged + clean worktree + HEAD == pre-run main → recover."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head=BEFORE_MAIN, porcelain="")

    reason = orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, BEFORE_MAIN)

    assert reason  # non-empty → recovered
    assert gh.checkout_args == ["checkout", EXPECTED]


def test_fail_closed_when_feature_branch_moved():
    """Agent moved the feature branch ref → not read-only → refuse."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref="moved-sha", current_head=BEFORE_MAIN, porcelain="")

    assert orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, BEFORE_MAIN) is None
    assert gh.checkout_args is None  # did not checkout


def test_fail_closed_when_dirty_worktree():
    """Staged/untracked changes carry across checkout into the feature branch → refuse."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head=BEFORE_MAIN, porcelain=" M file.py\n")

    assert orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, BEFORE_MAIN) is None
    assert gh.checkout_args is None


def test_fail_closed_when_wrong_branch_head_mismatch():
    """Wrong-branch HEAD advanced past pre-run main (agent committed/fetched) → refuse."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head="newer-main-sha", porcelain="")

    assert orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, BEFORE_MAIN) is None
    assert gh.checkout_args is None


def test_fail_closed_when_missing_inputs():
    """No baseline to prove read-only-ness → fail closed."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head=BEFORE_MAIN, porcelain="")

    assert orch._recover_worktree_branch(gh, "", BEFORE_HEAD, BEFORE_MAIN) is None
    assert orch._recover_worktree_branch(gh, EXPECTED, "", BEFORE_MAIN) is None
    assert orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, "") is None
    assert gh.checkout_args is None


def test_fail_closed_when_checkout_raises():
    """If the recovery checkout itself fails, surface None (caller fail-closes)."""
    orch = _make_orchestrator()
    gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head=BEFORE_MAIN, porcelain="", checkout_ok=False)

    assert orch._recover_worktree_branch(gh, EXPECTED, BEFORE_HEAD, BEFORE_MAIN) is None


# ── central-guard wiring (_validate_repo_context_after_run) ────────────────


def _before_state():
    return {
        "context": {"repo_path": "/wf", "expected_branch": EXPECTED},
        "effective": {
            "head": BEFORE_HEAD,
            "main_head": BEFORE_MAIN,
            "repo_path": "/wf",
            "origin": "o",
            "git_dir": "g",
            "common_dir": "c",
        },
    }


def _after_state_on_main():
    # Worktree was left on main, but origin/git_dir/common_dir unchanged.
    return {
        "branch": "main",
        "head": BEFORE_MAIN,
        "top_level": "/wf",
        "origin": "o",
        "git_dir": "g",
        "common_dir": "c",
    }


def test_central_guard_recovers_when_safe():
    """Post-agent guard recovers (returns '') when the agent left the worktree
    on main read-only, instead of failing the workflow closed (#2271, 208)."""
    from unittest.mock import patch

    orch = _make_orchestrator()
    fake_gh = _FakeGh(feature_ref=BEFORE_HEAD, current_head=BEFORE_MAIN, porcelain="")
    with (
        patch.object(orch, "_capture_repo_state", return_value=_after_state_on_main()),
        patch(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            return_value=fake_gh,
        ),
    ):
        result = orch._validate_repo_context_after_run(_before_state(), system_account=None)

    assert result == ""  # recovered → no violation
    assert fake_gh.checkout_args == ["checkout", EXPECTED]


def test_central_guard_fails_closed_when_feature_branch_moved():
    """Feature branch ref moved → recovery refuses → guard returns the violation."""
    from unittest.mock import patch

    orch = _make_orchestrator()
    fake_gh = _FakeGh(feature_ref="moved-sha", current_head=BEFORE_MAIN, porcelain="")
    with (
        patch.object(orch, "_capture_repo_state", return_value=_after_state_on_main()),
        patch(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            return_value=fake_gh,
        ),
    ):
        result = orch._validate_repo_context_after_run(_before_state(), system_account=None)

    assert "Agent changed the workflow branch unexpectedly" in result
    assert fake_gh.checkout_args is None  # did not recover
