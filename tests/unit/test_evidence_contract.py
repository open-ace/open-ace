"""Contract tests for the Phase A verify-before-act evidence layer (issue #2045).

These cover the five Phase-A acceptance criteria that the EvidenceService must
satisfy before any orchestrator path consumes it for irreversible git ops:

* PR head is fetched and verified local before use.
* Ancestry distinguishes a definitive False from an indeterminate None.
* Commit availability has distinct confirmed/rejected/indeterminate states.
* Remote branch mismatch is indeterminate (never silently picks one head).
* Evidence metadata records source, SHAs, and verification method.

Tests follow the style in ``test_autonomous_ci_guardrails.py``: local imports,
``MagicMock`` for ``GitHubOps``, and ``_run_git`` returncode control for the
``cat-file -e`` / ``merge-base --is-ancestor`` / ``fetch`` probes.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.evidence_service import EvidenceService


def _gh_with_git(returncodes_by_cmd):
    """Build a MagicMock GitHubOps whose ``_run_git`` returncode depends on args.

    ``returncodes_by_cmd`` maps a discriminator (first arg element, e.g.
    ``"cat-file"`` or ``"merge-base"`` or ``"fetch"``) to the returncode to
    return for that command. Unknown commands default to rc=0. This is more
    robust than call-order indexing because verify_commit_available interleaves
    cat-file / fetch / cat-file.
    """
    gh = MagicMock()

    def fake_run_git(args, check=True):
        key = args[0] if args else ""
        rc = returncodes_by_cmd.get(key, 0)
        return MagicMock(returncode=rc, stdout="", stderr="")

    gh._run_git.side_effect = fake_run_git
    return gh


# ── verify_commit_available ────────────────────────────────────────────────


def test_commit_available_confirmed_when_object_present():
    """cat-file -e succeeds immediately → CONFIRMED without fetching."""
    gh = _gh_with_git({"cat-file": 0})  # cat-file -e succeeds
    svc = EvidenceService()
    ev = svc.verify_commit_available(gh, "abc123", "feat")
    assert ev.verdict is Verdict.CONFIRMED
    assert ev.source == "local_object_db"
    assert ev.commit_shas == ("abc123",)
    assert ev.verification_method == "cat-file -e"


def test_commit_available_confirmed_after_fetch():
    """Object absent locally but resolves after fetch → CONFIRMED, source git_fetch."""
    # cat-file -e fails, fetch (rc irrelevant), second cat-file -e succeeds.
    gh = _gh_with_git({"cat-file": 1})
    # Override: first cat-file fails, second succeeds — simulate via side_effect list.
    gh._run_git.side_effect = [
        MagicMock(returncode=1, stdout="", stderr=""),  # first cat-file -e
        MagicMock(returncode=0, stdout="", stderr=""),  # fetch origin feat
        MagicMock(returncode=0, stdout="", stderr=""),  # second cat-file -e
    ]
    svc = EvidenceService()
    ev = svc.verify_commit_available(gh, "abc123", "feat")
    assert ev.verdict is Verdict.CONFIRMED
    assert ev.source == "git_fetch"
    assert "fetch origin feat" in ev.verification_method


def test_commit_available_rejected_when_absent_after_fetch():
    """Object absent even after fetch → REJECTED (definitive, not indeterminate)."""
    # cat-file -e fails before AND after fetch.
    gh = MagicMock()
    gh._run_git.side_effect = [
        MagicMock(returncode=1),  # first cat-file -e
        MagicMock(returncode=0),  # fetch origin feat
        MagicMock(returncode=1),  # second cat-file -e
    ]
    svc = EvidenceService()
    ev = svc.verify_commit_available(gh, "abc123", "feat")
    assert ev.verdict is Verdict.REJECTED
    assert ev.reason


# ── verify_branch_contains (ancestry) ──────────────────────────────────────


def test_ancestry_confirmed_when_base_is_ancestor():
    """merge-base rc=0 → CONFIRMED (base is ancestor of head)."""
    # cat-file -e head succeeds (0), then merge-base rc=0.
    gh = _gh_with_git({"cat-file": 0, "merge-base": 0})
    svc = EvidenceService()
    ev = svc.verify_branch_contains(gh, head="head1", base="base1", branch_name="feat")
    assert ev.verdict is Verdict.CONFIRMED


def test_ancestry_false_and_indeterminate_are_distinct():
    """rc=1 (REJECTED) vs rc=128 (INDETERMINATE) must not collapse to the same value.

    This is the core Phase A acceptance criterion: a definitive 'no ancestry' is
    a commit-graph answer the caller acts on, while a git error must fail closed.
    """
    svc = EvidenceService()
    # cat-file -e head succeeds, merge-base rc=1 → REJECTED.
    ev_rejected = svc.verify_branch_contains(
        _gh_with_git({"cat-file": 0, "merge-base": 1}),
        head="h",
        base="b",
        branch_name="feat",
    )
    # cat-file -e head succeeds, merge-base rc=128 → INDETERMINATE.
    ev_indeterminate = svc.verify_branch_contains(
        _gh_with_git({"cat-file": 0, "merge-base": 128}),
        head="h",
        base="b",
        branch_name="feat",
    )
    assert ev_rejected.verdict is Verdict.REJECTED
    assert ev_indeterminate.verdict is Verdict.INDETERMINATE
    assert ev_rejected.verdict is not ev_indeterminate.verdict
    # bool|None mapping must also differ (False vs None).
    assert ev_rejected.verdict.to_bool_or_none() is False
    assert ev_indeterminate.verdict.to_bool_or_none() is None


def test_ancestry_indeterminate_when_head_object_missing():
    """Head unavailable after fetch → INDETERMINATE (probe cannot run)."""
    # cat-file -e head fails before AND after fetch; merge-base never runs.
    gh = MagicMock()
    gh._run_git.side_effect = [
        MagicMock(returncode=1),  # first cat-file -e head
        MagicMock(returncode=0),  # fetch origin feat
        MagicMock(returncode=1),  # second cat-file -e head
    ]
    svc = EvidenceService()
    ev = svc.verify_branch_contains(gh, head="h", base="b", branch_name="feat")
    assert ev.verdict is Verdict.INDETERMINATE
    assert "head object unavailable" in ev.reason


# ── verify_remote_branch_state ─────────────────────────────────────────────


def test_remote_branch_state_confirmed_when_heads_match():
    gh = _gh_with_git({"fetch": 0})  # fetch succeeds
    gh.resolve_commit.return_value = "remote1"
    svc = EvidenceService()
    ev = svc.verify_remote_branch_state(gh, "feat", "remote1")
    assert ev.verdict is Verdict.CONFIRMED


def test_remote_branch_state_mismatch_is_indeterminate_not_silent():
    """Heads differ → INDETERMINATE; never silently trust one side.

    The whole point of issue #2045: a mismatch does not tell us which SHA is
    authoritative, so the caller must re-derive expected_head rather than pick.
    """
    gh = _gh_with_git({"fetch": 0})  # fetch succeeds
    gh.resolve_commit.return_value = "actual"
    svc = EvidenceService()
    ev = svc.verify_remote_branch_state(gh, "feat", "expected")
    assert ev.verdict is Verdict.INDETERMINATE
    assert "actual" in ev.reason and "expected" in ev.reason


def test_remote_branch_state_indeterminate_when_fetch_fails():
    gh = _gh_with_git({"fetch": 2})  # fetch fails
    svc = EvidenceService()
    ev = svc.verify_remote_branch_state(gh, "feat", "expected")
    assert ev.verdict is Verdict.INDETERMINATE
    assert "fetch failed" in ev.reason


# ── resolve_verified_pr_head (composite) ───────────────────────────────────


def test_api_pr_head_is_fetched_and_verified_before_use():
    """PR head SHA from the API is CONFIRMED only after local object verification.

    Guards the historical bug where ``get_pr_head_sha`` returned an API SHA that
    was never fetched, so ``merge-base`` later failed with 'no commit'.
    """
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "api-sha"
    # cat-file -e succeeds (object already local).
    gh._run_git.return_value = MagicMock(returncode=0, stdout="", stderr="")
    svc = EvidenceService()
    ev = svc.resolve_verified_pr_head(gh, 42, "feat")
    assert ev.verdict is Verdict.CONFIRMED
    assert ev.subject == "pr_head"
    assert ev.commit_shas == ("api-sha",)
    gh.get_pr_head_sha.assert_called_once_with(42)


def test_api_pr_head_indeterminate_when_object_unverifiable():
    """API SHA that cannot be resolved locally → INDETERMINATE, not CONFIRMED."""
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "api-sha"
    # cat-file -e fails both before and after fetch.
    gh._run_git.side_effect = [
        MagicMock(returncode=1),  # first cat-file -e
        MagicMock(returncode=0),  # fetch origin feat
        MagicMock(returncode=1),  # second cat-file -e
    ]
    svc = EvidenceService()
    ev = svc.resolve_verified_pr_head(gh, 42, "feat")
    assert ev.verdict is Verdict.INDETERMINATE
    assert ev.commit_shas == ("api-sha",)


def test_api_pr_head_indeterminate_on_api_error():
    """get_pr_head_sha raising → INDETERMINATE, never an unhandled exception."""
    gh = MagicMock()
    gh.get_pr_head_sha.side_effect = RuntimeError("api down")
    svc = EvidenceService()
    ev = svc.resolve_verified_pr_head(gh, 42, "feat")
    assert ev.verdict is Verdict.INDETERMINATE
    assert "API error" in ev.reason


# ── evidence metadata ──────────────────────────────────────────────────────


def test_evidence_metadata_records_source_sha_and_method():
    """Evidence.to_dict() carries the audit fields required by #2045 Phase A."""
    gh = _gh_with_git({"cat-file": 0})  # cat-file -e succeeds
    svc = EvidenceService()
    ev = svc.verify_commit_available(gh, "abc123", "feat")
    data = ev.to_dict()
    assert data["source"] == "local_object_db"
    assert data["subject"] == "commit_availability"
    assert data["verdict"] == "confirmed"
    assert data["verification_method"] == "cat-file -e"
    assert data["commit_shas"] == ["abc123"]
    assert "observed_at" in data and "verified_at" in data
    assert data["reason"]


def test_verdict_to_bool_or_none_mapping():
    """Tri-state maps cleanly to the legacy bool|None contract."""
    assert Verdict.CONFIRMED.to_bool_or_none() is True
    assert Verdict.REJECTED.to_bool_or_none() is False
    assert Verdict.INDETERMINATE.to_bool_or_none() is None


def test_evidence_is_frozen():
    """Evidence is immutable so it can be safely cached/shared."""
    gh = _gh_with_git({"cat-file": 0})
    svc = EvidenceService()
    ev = svc.verify_commit_available(gh, "abc123", "feat")
    assert isinstance(ev, Evidence)
    with pytest.raises((AttributeError, Exception)):
        ev.reason = "mutated"
