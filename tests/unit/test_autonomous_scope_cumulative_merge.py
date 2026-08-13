"""Regression tests for cumulative scope guard across an upstream merge.

When an autonomous workflow that is many commits behind ``main`` syncs via a
merge-upstream commit, the merge-commit exclusion rewrites the *current-round*
base to the merge-base so upstream files are not counted against that round.
The same rewrite must also apply to the *cumulative* range; otherwise the
stale ``base_commit_sha`` (the branch-creation point) produces a cumulative
diff that includes the merge's hundreds of upstream files and trips a false
"scope exceeded".
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _make_orchestrator():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._update_workflow = MagicMock()
    return orch


def test_cumulative_range_uses_merge_base_after_upstream_merge(monkeypatch):
    """A merge-upstream commit must exclude upstream files from BOTH ranges.

    Reproduces the prod symptom: a workflow far behind main merges upstream,
    producing 194 changed files against the stale branch-creation base but
    only ~50 real autonomous files against the merge-base. The guard must not
    flag the merge's upstream files as scope exceeded.
    """
    # Lower the cap so the cumulative upstream file count (194) would trip the
    # guard if the stale base were used, while the real delta (12) stays clean.
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 60)

    orch = _make_orchestrator()
    gh = MagicMock()

    # The workflow was created far behind main; its persisted base is the old
    # branch-creation point (stale once main advanced by hundreds of commits).
    stale_branch_creation_base = "stale-base-aaa"
    wf = {"base_commit_sha": stale_branch_creation_base}

    # commit_after is a merge commit that brought main into the PR branch.
    # The merge-base with main is the real autonomous branch point.
    effective_merge_base = "merge-base-bbb"

    def fake_run_git(args, check=True, **kwargs):
        # rev-parse FETCH_HEAD during the merge-commit branch, plus the
        # merge-base probe used to derive the effective round base.
        if args[0] == "merge-base":
            return MagicMock(returncode=0, stdout=f"{effective_merge_base}\n")
        # fetch origin main
        if args[0] == "fetch":
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    gh._run_git.side_effect = fake_run_git
    gh.resolve_commit.return_value = "fetched-main-head"

    # Against the effective merge-base, the real autonomous delta is small.
    # Against the stale branch-creation base, the upstream merge's 194 files
    # would be counted (the false positive).
    def fake_changed_files(base, after):
        if base == effective_merge_base:
            return [f"app/real_{i}.py" for i in range(12)]
        if base == stale_branch_creation_base:
            return [f"upstream/file_{i}.py" for i in range(194)]
        raise AssertionError(f"unexpected base passed to get_changed_files: {base!r}")

    gh.get_changed_files.side_effect = fake_changed_files

    with patch.object(orch, "_is_merge_commit", return_value=True):
        reason = orch._validate_autonomous_change_scope(
            gh, wf, "original-round-base", "merge-commit-head"
        )

    # The guard must not flag the merge's upstream files as scope exceeded.
    assert reason == "", f"expected no scope violation, got: {reason}"
    # The cumulative range must have used the effective merge-base, not the
    # stale branch-creation base.
    used_bases = [call.args[0] for call in gh.get_changed_files.call_args_list]
    assert stale_branch_creation_base not in used_bases, (
        "cumulative range used the stale branch-creation base; upstream merge "
        "files would be mis-counted"
    )
    assert effective_merge_base in used_bases


def test_genuinely_oversized_autonomous_change_is_still_blocked(monkeypatch):
    """A real autonomous delta over the cap must still be flagged, even with a merge."""
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 60)

    orch = _make_orchestrator()
    gh = MagicMock()

    effective_merge_base = "merge-base-bbb"
    # Stale base equals the effective base here (no cumulative gap), so the
    # only range checked is the real autonomous delta, which is oversized.
    wf = {"base_commit_sha": effective_merge_base}

    def fake_run_git(args, check=True, **kwargs):
        if args[0] == "merge-base":
            return MagicMock(returncode=0, stdout=f"{effective_merge_base}\n")
        return MagicMock(returncode=0, stdout="")

    gh._run_git.side_effect = fake_run_git
    gh.resolve_commit.return_value = "fetched-main-head"

    # 80 real autonomous files — genuinely over the 60-file cap.
    gh.get_changed_files.return_value = [f"app/real_{i}.py" for i in range(80)]

    with patch.object(orch, "_is_merge_commit", return_value=True):
        reason = orch._validate_autonomous_change_scope(
            gh, wf, "original-round-base", "merge-commit-head"
        )

    assert "scope exceeded" in reason
    assert "80 files changed" in reason


def test_non_merge_round_keeps_cumulative_base_unchanged(monkeypatch):
    """A normal dev round with no merge must behave exactly as before."""
    import app.modules.workspace.autonomous.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_AUTONOMOUS_CHANGED_FILES", 60)

    orch = _make_orchestrator()
    gh = MagicMock()

    cumulative_base = "real-base-ccc"
    wf = {"base_commit_sha": cumulative_base}

    # Not a merge commit — so no merge-base rewrite should occur.
    gh.get_changed_files.side_effect = [
        ["app/a.py", "app/b.py"],  # current round
        ["app/a.py", "app/b.py", "app/c.py"],  # cumulative
    ]

    with patch.object(orch, "_is_merge_commit", return_value=False):
        reason = orch._validate_autonomous_change_scope(gh, wf, "round-base", "head")

    assert reason == ""
    # Cumulative range still uses the persisted base_commit_sha unchanged.
    used_bases = [call.args[0] for call in gh.get_changed_files.call_args_list]
    assert used_bases == ["round-base", cumulative_base]
