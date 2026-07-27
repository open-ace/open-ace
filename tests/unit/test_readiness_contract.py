"""Contract tests for Phase B merge-readiness / actionable-CI evidence (issue #2045).

Phase B extends the verify-before-act contract from pure git/graph signals
(:mod:`evidence_service`) to composite GitHub-API signals: merge readiness
and the subset of CI failures worth spending an AI repair attempt on.

Covers:

* :func:`GitHubOps.get_branch_protection` — required-status-check discovery,
  distinguishing a 404 (branch has no protection → no required checks) from a
  403/permission error (must fail closed so the classifier returns
  ``indeterminate`` rather than guessing).
* :class:`ReadinessService.classify_merge_readiness` — 7-state tri-state
  classification (the historical incidents #1989/#1991/#1999/#1993 collapsed
  distinct states into one wrong action).
* :class:`ReadinessService.collect_actionable_ci_failures` — required/optional
  split via branch protection; optional-only failures do not consume a repair
  attempt, and pending/GitHub-cache states return ``indeterminate``.

Follows the style in ``test_autonomous_ci_guardrails.py``: construct a real
``GitHubOps("/tmp/repo")``, pin its owner/repo/host attrs, and patch
``_run_gh`` / ``_run_git`` to drive REST/git responses without network.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.readiness_service import ReadinessService


def _make_gh() -> GitHubOps:
    """A real GitHubOps pinned to owner/repo on github.com (no network).

    Mirrors the fixture style in ``test_autonomous_ci_guardrails.py``: the
    ``_owner_repo_resolved`` flag short-circuits ``_resolve_owner_repo`` so
    neither ``get_repo_name`` nor ``_gh_api_args`` shells out to git/gh.
    """
    gh = GitHubOps("/tmp/repo")
    gh._repo_slug = "owner/repo"
    gh._repo_host = "github.com"
    gh._owner_repo = "owner/repo"
    gh._owner_repo_resolved = True
    return gh


# ── GitHubOps.get_branch_protection ─────────────────────────────────────────


def test_get_branch_protection_returns_required_contexts():
    """200 + required_status_checks.contexts → the required check-name list."""
    gh = _make_gh()
    body = '{"required_status_checks":{"contexts":["ci-lint","ci-test"]}}'
    with patch.object(gh, "_run_gh", return_value=MagicMock(returncode=0, stdout=body, stderr="")):
        result = gh.get_branch_protection("main")
    assert result["required_status_checks"]["contexts"] == ["ci-lint", "ci-test"]


def test_get_branch_protection_merges_legacy_contexts_and_checks_array():
    """GitHub returns both legacy ``contexts`` and the newer ``checks[]``; merge."""
    gh = _make_gh()
    body = (
        '{"required_status_checks":{"contexts":["ci-lint"],'
        '"checks":[{"context":"ci-test"},{"context":"ci-build"}]}}'
    )
    with patch.object(gh, "_run_gh", return_value=MagicMock(returncode=0, stdout=body, stderr="")):
        result = gh.get_branch_protection("main")
    contexts = result["required_status_checks"]["contexts"]
    assert set(contexts) == {"ci-lint", "ci-test", "ci-build"}


def test_get_branch_protection_404_means_no_required_checks():
    """A branch with no protection rules is 404 → empty contexts, NOT an error.

    No required checks means every failing check is optional from the merge
    gate's perspective; surfacing 404 as a normal empty result lets the
    classifier proceed instead of fail-closing on a non-error.
    """
    gh = _make_gh()
    with patch.object(
        gh,
        "_run_gh",
        return_value=MagicMock(returncode=1, stdout="", stderr="HTTP 404: Not Found"),
    ):
        result = gh.get_branch_protection("main")
    assert result["required_status_checks"]["contexts"] == []


def test_get_branch_protection_permission_error_raises_for_fail_closed():
    """403/permission failure must raise so the classifier returns indeterminate.

    We cannot distinguish required from optional without protection data, so a
    permission error fails closed rather than guessing 'all optional' — which
    would silently drop real required-check failures from repair.
    """
    gh = _make_gh()
    with patch.object(
        gh,
        "_run_gh",
        return_value=MagicMock(returncode=1, stdout="", stderr="HTTP 403: Forbidden"),
    ):
        with pytest.raises(GitHubOpsError):
            gh.get_branch_protection("main")


def test_get_branch_protection_defaults_to_main_branch():
    gh = _make_gh()
    captured: dict = {}

    def fake_run(args, check=True, repo_scoped=True):
        captured["args"] = args
        return MagicMock(
            returncode=0, stdout='{"required_status_checks":{"contexts":[]}}', stderr=""
        )

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        gh.get_branch_protection()
    assert "branches/main/protection" in captured["args"][-1]


# ── ReadinessService.classify_merge_readiness (7-state) ─────────────────────


def _gh_for_classify(
    *,
    mergeable=True,
    mergeable_state="clean",
    checks=None,
    required=("ci-lint",),
    protection_fail=False,
    ancestry_rc=0,
    fetch_fail=False,
):
    """A MagicMock GitHubOps driving classify_merge_readiness without network.

    ``ancestry_rc`` controls the ``merge-base --is-ancestor`` returncode used
    to disambiguate a stale ``dirty`` cache (0=head contains main → stale,
    1=real divergence, 128+=inconclusive). Only consulted when
    ``mergeable_state == "dirty"``.
    """
    gh = MagicMock()
    gh.get_pr_merge_state.return_value = {
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
    }
    gh.get_pr_checks.return_value = list(checks or [])
    if protection_fail:
        gh.get_branch_protection.side_effect = GitHubOpsError("HTTP 403: Forbidden")
    else:
        gh.get_branch_protection.return_value = {
            "required_status_checks": {"contexts": list(required)}
        }

    def fake_git(args, check=True):
        if args and args[0] == "fetch" and fetch_fail:
            return MagicMock(returncode=2, stdout="", stderr="fetch error")
        if args and args[0] == "merge-base":
            return MagicMock(returncode=ancestry_rc, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    gh._run_git.side_effect = fake_git
    gh.resolve_commit.return_value = "main-sha"
    return gh


def test_classify_mergeable_when_clean_no_required_issues():
    gh = _gh_for_classify(
        mergeable=True,
        mergeable_state="clean",
        checks=[{"name": "ci-lint", "bucket": "pass"}],
        required=["ci-lint"],
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "mergeable"
    assert ev.verdict is Verdict.CONFIRMED
    assert ev.subject == "merge_readiness"


def test_classify_pending_required_checks():
    gh = _gh_for_classify(
        mergeable_state="clean",
        checks=[{"name": "ci-lint", "bucket": "pending"}],
        required=["ci-lint"],
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "pending_required_checks"
    assert ev.verdict is Verdict.INDETERMINATE


def test_classify_failing_required_checks():
    gh = _gh_for_classify(
        mergeable_state="clean",
        checks=[{"name": "ci-lint", "bucket": "fail"}],
        required=["ci-lint"],
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "failing_required_checks"
    assert ev.verdict is Verdict.REJECTED


def test_classify_failing_optional_is_non_blocking():
    """Optional-only failure with mergeable_state=unstable may proceed.

    Guards #1989/#2034: an optional failure must not consume a required-CI
    repair attempt or trigger the conflict resolver.
    """
    gh = _gh_for_classify(
        mergeable=True,
        mergeable_state="unstable",
        checks=[{"name": "ci-optional", "bucket": "fail"}],
        required=[],
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "failing_optional_checks"
    assert ev.verdict is Verdict.CONFIRMED


def test_classify_conflict_when_dirty_and_ancestry_rejected():
    """dirty + head does NOT contain main (merge-base rc=1) → real conflict."""
    gh = _gh_for_classify(
        mergeable=False,
        mergeable_state="dirty",
        checks=[],
        required=[],
        ancestry_rc=1,
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "conflict_confirmed"
    assert ev.verdict is Verdict.REJECTED


def test_classify_stale_dirty_reclassifies_to_mergeable():
    """dirty but head DOES contain main (rc=0) → stale cache, no issues → mergeable.

    This is the #1991/#1999 stale-dirty guard: a GitHub ``dirty`` cache right
    after a sync push must not trigger the conflict resolver when the branch
    already contains main.
    """
    gh = _gh_for_classify(
        mergeable=True,
        mergeable_state="dirty",
        checks=[],
        required=[],
        ancestry_rc=0,
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "mergeable"
    assert ev.verdict is Verdict.CONFIRMED


def test_classify_dirty_ancestry_indeterminate_fail_closed():
    """dirty + merge-base rc=128 (git error) → inconclusive → indeterminate."""
    gh = _gh_for_classify(
        mergeable=False,
        mergeable_state="dirty",
        checks=[],
        required=[],
        ancestry_rc=128,
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "indeterminate"
    assert ev.verdict is Verdict.INDETERMINATE


def test_classify_policy_blocked():
    gh = _gh_for_classify(mergeable=False, mergeable_state="blocked", checks=[], required=[])
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "policy_blocked"
    assert ev.verdict is Verdict.REJECTED


def test_classify_indeterminate_when_merge_state_api_fails():
    gh = _gh_for_classify()
    gh.get_pr_merge_state.side_effect = RuntimeError("api down")
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "indeterminate"
    assert ev.verdict is Verdict.INDETERMINATE


def test_classify_indeterminate_when_protection_inaccessible():
    """403 on branch protection → cannot tell required from optional → fail closed.

    Guards the alternative #1989 path: rather than guessing every failing
    check is optional, an un-verifiable protection state fails closed.
    """
    gh = _gh_for_classify(
        mergeable=True,
        mergeable_state="clean",
        checks=[{"name": "ci-lint", "bucket": "fail"}],
        protection_fail=True,
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "indeterminate"
    assert ev.verdict is Verdict.INDETERMINATE


def test_classify_pending_required_dominates_optional_failure():
    """Required pending + optional fail → pending_required_checks (required gate wins)."""
    gh = _gh_for_classify(
        mergeable_state="clean",
        checks=[
            {"name": "ci-lint", "bucket": "pending"},
            {"name": "ci-opt", "bucket": "fail"},
        ],
        required=["ci-lint"],
    )
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "head-sha")
    assert ev.classification == "pending_required_checks"


def test_classify_evidence_binds_to_verified_head():
    """Merge-readiness evidence must bind to the verified head SHA for audit."""
    gh = _gh_for_classify(mergeable=True, mergeable_state="clean", checks=[], required=[])
    ev = ReadinessService().classify_merge_readiness(gh, 42, "feat", "verified-sha-123")
    assert ev.commit_shas == ("verified-sha-123",)
    assert ev.to_dict()["classification"] == "mergeable"


# ── ReadinessService.collect_actionable_ci_failures ─────────────────────────


def _gh_for_collect(*, required=("ci-lint",), protection_fail=False, excerpts=None):
    """A MagicMock GitHubOps driving collect_actionable_ci_failures.

    ``excerpts`` maps a check name to either the excerpt string returned by
    ``get_check_failure_excerpt`` or an Exception to raise (simulating a log
    fetch failure). Unlisted names get ``"<name> log"``.
    """
    gh = MagicMock()
    if protection_fail:
        gh.get_branch_protection.side_effect = GitHubOpsError("HTTP 403: Forbidden")
    else:
        gh.get_branch_protection.return_value = {
            "required_status_checks": {"contexts": list(required)}
        }
    excerpts = excerpts or {}

    def fake_excerpt(check):
        name = check.get("name", "")
        if name in excerpts:
            val = excerpts[name]
            if isinstance(val, Exception):
                raise val
            return val
        return f"{name} log"

    gh.get_check_failure_excerpt.side_effect = fake_excerpt
    return gh


def test_collect_returns_required_failures_with_excerpt():
    gh = _gh_for_collect(required=["ci-lint"])
    failed = [{"name": "ci-lint", "bucket": "fail", "state": "failure"}]
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", failed)
    assert len(actionable) == 1
    assert actionable[0]["failure_excerpt"] == "ci-lint log"
    assert ev.classification == "actionable_required_failures"
    assert ev.verdict is Verdict.REJECTED


def test_collect_skips_optional_failures():
    """Optional failure → empty list + optional_only_no_repair (no repair attempt).

    Guards #1989/#2034: an optional failure must not consume a bounded repair.
    """
    gh = _gh_for_collect(required=["ci-lint"])
    failed = [{"name": "ci-optional", "bucket": "fail", "state": "failure"}]
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", failed)
    assert actionable == []
    assert ev.classification == "optional_only_no_repair"
    assert ev.verdict is Verdict.CONFIRMED


def test_collect_skips_cancelled_required():
    gh = _gh_for_collect(required=["ci-lint"])
    failed = [{"name": "ci-lint", "bucket": "fail", "state": "cancelled"}]
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", failed)
    assert actionable == []
    assert ev.classification == "no_actionable_failures"


def test_collect_indeterminate_when_protection_inaccessible():
    gh = _gh_for_collect(protection_fail=True)
    failed = [{"name": "ci-lint", "bucket": "fail", "state": "failure"}]
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", failed)
    assert actionable == []
    assert ev.classification == "indeterminate"
    assert ev.verdict is Verdict.INDETERMINATE


def test_collect_no_failures():
    gh = _gh_for_collect(required=["ci-lint"])
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", [])
    assert actionable == []
    assert ev.classification == "no_actionable_failures"
    assert ev.verdict is Verdict.CONFIRMED


def test_collect_binds_to_verified_head():
    gh = _gh_for_collect(required=["ci-lint"])
    failed = [{"name": "ci-lint", "bucket": "fail", "state": "failure"}]
    _, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "verified-head-9", failed)
    assert ev.commit_shas == ("verified-head-9",)
    assert ev.subject == "actionable_ci_failures"


def test_collect_excerpt_failure_keeps_check_with_empty_excerpt():
    """When get_check_failure_excerpt raises, the check is kept with empty excerpt.

    The caller's diagnostics-pending logic decides whether to wait for logs;
    collect never drops a required failure silently.
    """
    gh = _gh_for_collect(
        required=["ci-lint"], excerpts={"ci-lint": RuntimeError("log fetch failed")}
    )
    failed = [{"name": "ci-lint", "bucket": "fail", "state": "failure"}]
    actionable, ev = ReadinessService().collect_actionable_ci_failures(gh, 42, "head-sha", failed)
    assert len(actionable) == 1
    assert actionable[0]["failure_excerpt"] == ""
    assert ev.classification == "actionable_required_failures"
