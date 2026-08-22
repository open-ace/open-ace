"""Scope gate tests (#2335) + three-branch refinement (#2982).

A required path missing from the cumulative base..merge diff is no longer an
unconditional REJECTED: a path TOUCHED by some commit in the range was
changed-then-lost (conflict-resolution loss) and stays REJECTED with that
evidence; a path never touched in the range is only REJECTED for authoritative
convention snapshots — for LLM-extracted snapshots it becomes an advisory
report entry excluded from aggregation.
"""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phases.acceptance_verification import run_scope_gate


def _gh(changed, touched=None):
    gh = MagicMock()
    gh.get_changed_files.return_value = changed
    gh.path_touched_in_range.return_value = touched or False
    return gh


def test_required_path_present_is_confirmed():
    gh = _gh(["app/services/retention.py", "README.md"])
    verdicts, advisory = run_scope_gate(gh, ["app/services/retention.py"], "base", "merge")
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED
    assert advisory == []


def test_required_path_missing_is_rejected_for_convention_snapshot():
    gh = _gh(["README.md"])
    verdicts, advisory = run_scope_gate(
        gh, ["app/services/retention.py"], "base", "merge", snapshot_source="convention"
    )
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "app/services/retention.py" in verdicts[0].item
    assert verdicts[0].evidence  # carries the missing-path ref
    assert advisory == []


def test_glob_matches_changed_path():
    gh = _gh(["app/services/retention.py", "app/services/legal.py"])
    verdicts, _ = run_scope_gate(gh, ["app/services/*.py"], "base", "merge")
    assert all(v.verdict is Verdict.CONFIRMED for v in verdicts)


def test_no_required_paths_returns_empty():
    gh = _gh([])
    verdicts, advisory = run_scope_gate(gh, [], "base", "merge")
    assert verdicts == []
    assert advisory == []


# -- touched-then-lost branch (#2944 pattern) ---------------------------------


def test_touched_but_absent_from_diff_is_rejected_with_lost_evidence():
    # #2944: a commit inside the range modified the path, conflict resolution
    # dropped it — the cumulative diff no longer contains it.
    gh = _gh(["README.md"], touched=True)
    verdicts, advisory = run_scope_gate(
        gh, ["frontend/QuotaAlerts.tsx"], "base", "merge", snapshot_source="llm"
    )
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "lost" in verdicts[0].evidence[0]["ref"]
    assert advisory == []


def test_touched_branch_applies_regardless_of_snapshot_source():
    gh = _gh([], touched=True)
    verdicts, _ = run_scope_gate(
        gh, ["app/services/x.py"], "base", "merge", snapshot_source="convention"
    )
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "lost" in verdicts[0].evidence[0]["ref"]


def test_glob_path_skips_history_probe_and_follows_source_branch():
    # git pathspec and fnmatch glob semantics differ, so glob-shaped paths
    # never hit the history probe; an LLM-snapshot glob miss is advisory.
    gh = _gh(["README.md"])
    verdicts, advisory = run_scope_gate(
        gh, ["app/services/*.py"], "base", "merge", snapshot_source="llm"
    )
    gh.path_touched_in_range.assert_not_called()
    assert verdicts == []
    assert [a["item"] for a in advisory] == ["app/services/*.py"]


# -- LLM-snapshot advisory branch (#2941/#2236/#2841 pattern) -----------------


def test_untouched_llm_snapshot_path_is_advisory_not_rejected():
    gh = _gh(["frontend/vitest.config.ts"], touched=False)
    verdicts, advisory = run_scope_gate(
        gh,
        ["frontend/vitest.config.ts", "frontend/src/utils/quotaFormatter.ts"],
        "base",
        "merge",
        snapshot_source="llm",
    )
    assert [v.item for v in verdicts] == ["frontend/vitest.config.ts"]
    assert verdicts[0].verdict is Verdict.CONFIRMED
    assert len(advisory) == 1
    assert advisory[0]["verdict"] == "advisory"
    assert advisory[0]["item"] == "frontend/src/utils/quotaFormatter.ts"
    assert advisory[0]["evidence"][0]["ref"].startswith("untouched:")


def test_untouched_convention_snapshot_path_stays_rejected():
    gh = _gh(["README.md"], touched=False)
    verdicts, advisory = run_scope_gate(
        gh, ["app/services/retention.py"], "base", "merge", snapshot_source="convention"
    )
    assert verdicts[0].verdict is Verdict.REJECTED
    assert advisory == []


# -- probe failures stay retryable-indeterminate ------------------------------


def test_changed_files_failure_returns_retryable_indeterminate():
    gh = MagicMock()
    gh.get_changed_files.side_effect = RuntimeError("boom")
    verdicts, advisory = run_scope_gate(gh, ["app/services/x.py"], "base", "merge")
    assert verdicts[0].verdict is Verdict.INDETERMINATE
    assert verdicts[0].retryable is True
    assert advisory == []


def test_history_probe_failure_returns_retryable_indeterminate():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["README.md"]
    gh.path_touched_in_range.side_effect = RuntimeError("boom")
    verdicts, advisory = run_scope_gate(gh, ["app/services/x.py"], "base", "merge")
    assert verdicts[0].verdict is Verdict.INDETERMINATE
    assert verdicts[0].retryable is True
    assert advisory == []
