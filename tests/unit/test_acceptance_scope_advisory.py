"""#2982: added/modified git status parsing, range-history probe, and the
advisory presentation layer (scope entries excluded from aggregation but
visible to humans), plus the cross-issue checklist filter."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.phases.acceptance_verification import (
    _acceptance_summary,
    _failed_items,
    _format_report_comment,
    _validate_extracted_snapshot,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2982)]


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


# -- github_ops: status-aware diff + range history -----------------------------


class TestGetChangedFilesWithStatus:
    def test_plain_statuses_map_to_paths(self):
        gh = GitHubOps("/tmp/repo")
        gh._run_git = MagicMock(
            return_value=_completed("A\tapp/new.py\nM\tapp/old.py\nD\tgone.py\n")
        )
        assert gh.get_changed_files_with_status("b", "m") == [
            ("A", "app/new.py"),
            ("M", "app/old.py"),
            ("D", "gone.py"),
        ]
        gh._run_git.assert_called_once_with(["diff", "-M", "--name-status", "b", "m"])

    def test_rename_similarity_score_keeps_letter_and_destination(self):
        gh = GitHubOps("/tmp/repo")
        gh._run_git = MagicMock(return_value=_completed("R087\tapp/old_name.py\tapp/new_name.py\n"))
        assert gh.get_changed_files_with_status("b", "m") == [("R", "app/new_name.py")]

    def test_unparsable_lines_are_skipped_not_raised(self):
        # A parse failure inside an acceptance gate must not escalate into a
        # workflow-level infra retry; skip with a warning instead.
        gh = GitHubOps("/tmp/repo")
        gh._run_git = MagicMock(return_value=_completed("garbage-no-tabs\nM\tok.py\n\n"))
        assert gh.get_changed_files_with_status("b", "m") == [("M", "ok.py")]


class TestPathTouchedInRange:
    def test_uses_full_history_pathspec(self):
        gh = GitHubOps("/tmp/repo")
        gh._run_git = MagicMock(return_value=_completed("abc123\ndef456\n"))
        assert gh.path_touched_in_range("base", "merge", "app/x.py") is True
        gh._run_git.assert_called_once_with(
            ["log", "--full-history", "--format=%H", "base..merge", "--", "app/x.py"]
        )

    def test_empty_output_is_false(self):
        gh = GitHubOps("/tmp/repo")
        gh._run_git = MagicMock(return_value=_completed(""))
        assert gh.path_touched_in_range("base", "merge", "app/x.py") is False


# -- advisory presentation ------------------------------------------------------


def _report(scope_entries):
    return {
        "status": "indeterminate",
        "scope": scope_entries,
        "gates": [],
        "verifier": [],
    }


def test_advisory_entries_are_not_failed_items():
    report = _report(
        [
            {"item": "a.py", "verdict": "confirmed", "evidence": []},
            {"item": "b.py", "verdict": "advisory", "evidence": []},
            {"item": "c.py", "verdict": "rejected", "evidence": []},
        ]
    )
    failed = [entry["item"] for _, entry in _failed_items(report)]
    assert failed == ["c.py"]


def test_comment_lists_advisory_section_when_no_failures():
    report = _report([{"item": "b.py", "verdict": "advisory", "evidence": []}])
    comment = _format_report_comment(report)
    assert "Advisory (not counted in verdict)" in comment
    assert "b.py" in comment
    # Advisory-only must not render as a rejection list.
    assert "Rejected / missing" not in comment


def test_comment_keeps_failure_list_and_advisory_section_together():
    report = _report(
        [
            {"item": "c.py", "verdict": "rejected", "evidence": []},
            {"item": "b.py", "verdict": "advisory", "evidence": []},
        ]
    )
    report["status"] = "rejected"
    comment = _format_report_comment(report)
    assert "Rejected / missing" in comment
    assert "Advisory (not counted in verdict)" in comment


def test_summary_names_advisory_items_when_nothing_failed():
    report = _report([{"item": "b.py", "verdict": "advisory", "evidence": []}])
    assert "advisory: b.py" in _acceptance_summary("indeterminate", report)


def test_summary_prefers_failed_names_over_advisory():
    report = _report(
        [
            {"item": "c.py", "verdict": "rejected", "evidence": []},
            {"item": "b.py", "verdict": "advisory", "evidence": []},
        ]
    )
    summary = _acceptance_summary("rejected", report)
    assert "not-verified: c.py" in summary
    assert "advisory" not in summary


# -- cross-issue checklist filter (#2841 contamination) -------------------------


def _payload(**overrides):
    base = {
        "required_paths": ["app/x.py"],
        "checklist": ["keep this item"],
        "non_scope": [],
        "closure_constraints": False,
    }
    base.update(overrides)
    return base


def test_other_issue_prefixed_items_are_dropped():
    snap = _validate_extracted_snapshot(
        _payload(
            checklist=[
                "Issue #2841: centralized useAdminTenant hook created",
                "Issue #2953: useSafeWorkspaceState hook validates tabs state",
                "keep this item",
            ]
        ),
        issue_number=2841,
    )
    assert snap.checklist == [
        "Issue #2841: centralized useAdminTenant hook created",
        "keep this item",
    ]


def test_trailing_issue_reference_is_kept():
    snap = _validate_extracted_snapshot(
        _payload(checklist=["测试 mock 位置正确修复（Issue #2883）"]),
        issue_number=2828,
    )
    assert snap.checklist == ["测试 mock 位置正确修复（Issue #2883）"]


def test_all_items_filtered_out_is_extraction_failure():
    with pytest.raises(ValueError, match="no verifiable criteria"):
        _validate_extracted_snapshot(
            _payload(required_paths=[], checklist=["Issue #2953: something else"]),
            issue_number=2841,
        )


def test_filter_inactive_without_issue_number():
    snap = _validate_extracted_snapshot(_payload(checklist=["Issue #2953: x"]))
    assert snap.checklist == ["Issue #2953: x"]
