"""Issue #2428: required-check discovery must see rulesets, not just classic protection.

``get_branch_protection`` queried only ``repos/{repo}/branches/{branch}/protection``.
That endpoint does not describe rulesets — the modern replacement for classic
branch protection — so on a ruleset-protected branch it reports nothing useful.
Measured against the live API with the token the autonomous workflow actually
uses:

    classic: rc=1  Resource not accessible by personal access token (HTTP 403)
    rules:   rc=0  [{"type":"required_status_checks", ...}]

The classic endpoint needs admin scope, so the workflow's PAT gets 403 there,
not the 404 one might assume. Both details matter:

* a 403 must NOT veto a successful ruleset read, or discovery stays broken
  exactly where it matters, and
* when neither source observed anything, the call must still raise — "no
  required checks" would be a guess, and guessing is what #1989 forbids.

Downstream, required-check discovery is what stops CI repair from spending its
bounded attempts on checks that do not gate the merge. Observed in production as
wf227 / issue #2328: "PR #2425 CI failed after 5 automatic repair rounds:
test (3.13)" — a check ``main`` does not require.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError

CLASSIC = "branches/main/protection"
RULES = "rules/branches/main"

RULESET_BODY = json.dumps(
    [
        {"type": "deletion"},
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "lint"},
                    {"context": "test (3.10)"},
                    {"context": "test (3.11)"},
                    {"context": "test (3.12)"},
                    {"context": "build"},
                ]
            },
        },
    ]
)


def _make_gh() -> GitHubOps:
    """A real GitHubOps pinned to owner/repo (no network).

    Mirrors the helper in ``tests/unit/test_readiness_contract.py``: the
    ``_owner_repo_resolved`` flag short-circuits ``_resolve_owner_repo`` so
    neither ``get_repo_name`` nor ``_gh_api_args`` shells out to git/gh.
    """
    gh = GitHubOps("/tmp/repo")
    gh._repo_slug = "owner/repo"
    gh._repo_host = "github.com"
    gh._owner_repo = "owner/repo"
    gh._owner_repo_resolved = True
    return gh


def _router(classic=None, rules=None):
    """Dispatch on the endpoint so each source can be simulated independently."""

    def fake_run(args, check=True, repo_scoped=True):
        url = args[-1]
        spec = classic if CLASSIC in url else rules if RULES in url else None
        if spec is None:
            raise AssertionError(f"unexpected endpoint: {url}")
        return MagicMock(
            returncode=spec.get("rc", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
        )

    return fake_run


def _contexts(gh: GitHubOps) -> list[str]:
    return gh.get_branch_protection("main")["required_status_checks"]["contexts"]


def test_ruleset_protected_branch_reports_its_required_checks():
    """The regression: classic 404s, the ruleset carries the real gate."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 0, "stdout": RULESET_BODY},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        contexts = _contexts(gh)
    assert contexts == ["lint", "test (3.10)", "test (3.11)", "test (3.12)", "build"], (
        "a ruleset-protected branch still looks unprotected — every failing check "
        "would be classified optional and CI repair would burn attempts on checks "
        "that do not block the merge"
    )


def test_both_mechanisms_are_unioned_without_duplicates():
    gh = _make_gh()
    router = _router(
        classic={
            "rc": 0,
            "stdout": '{"required_status_checks":{"contexts":["lint","legacy-only"]}}',
        },
        rules={"rc": 0, "stdout": RULESET_BODY},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        contexts = _contexts(gh)
    assert contexts.count("lint") == 1
    assert "legacy-only" in contexts
    assert "build" in contexts


def test_genuinely_unprotected_branch_still_reports_none():
    """Both sources 404 → empty list stays a valid answer, not an error."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        assert _contexts(gh) == []


def test_empty_ruleset_list_is_not_an_error():
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 0, "stdout": "[]"},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        assert _contexts(gh) == []


def test_ruleset_without_status_check_rules_yields_none():
    """A branch can carry rulesets that say nothing about checks."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 0, "stdout": json.dumps([{"type": "deletion"}, {"type": "non_fast_forward"}])},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        assert _contexts(gh) == []


def test_ruleset_permission_error_fails_closed():
    """#1989: an un-verifiable signal must never silently drive a merge decision.

    A 403 on the ruleset endpoint must raise so the classifier returns
    ``indeterminate``, rather than degrading to "no required checks" and
    declaring every failing check optional.
    """
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 1, "stderr": "gh: Forbidden (HTTP 403)"},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        with pytest.raises(GitHubOpsError):
            gh.get_branch_protection("main")


def test_classic_403_does_not_veto_a_successful_ruleset_read():
    """This is the production case, and mocks alone would never have caught it.

    The classic endpoint needs admin scope, so the autonomous workflow's own PAT
    gets 403 there while the rules endpoint returns 200. Treating that 403 as
    fatal leaves required-check discovery broken exactly where it matters.
    """
    gh = _make_gh()
    router = _router(
        classic={
            "rc": 1,
            "stderr": "gh: Resource not accessible by personal access token (HTTP 403)",
        },
        rules={"rc": 0, "stdout": RULESET_BODY},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        contexts = _contexts(gh)
    assert contexts == ["lint", "test (3.10)", "test (3.11)", "test (3.12)", "build"]


def test_blind_on_both_sources_fails_closed():
    """#1989: never guess. Nothing observed and a source was blind → raise."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Forbidden (HTTP 403)"},
        rules={"rc": 1, "stderr": "gh: Server Error (HTTP 500)"},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        with pytest.raises(GitHubOpsError):
            gh.get_branch_protection("main")


def test_classic_403_with_no_ruleset_rules_fails_closed():
    """A clean ruleset read of [] cannot rule out invisible classic protection."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Forbidden (HTTP 403)"},
        rules={"rc": 0, "stdout": "[]"},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        with pytest.raises(GitHubOpsError):
            gh.get_branch_protection("main")


def test_malformed_ruleset_payload_is_tolerated():
    """A dict instead of a list must not crash the merge gate."""
    gh = _make_gh()
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 0, "stdout": '{"unexpected": true}'},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        assert _contexts(gh) == []


def test_plain_string_contexts_in_ruleset_are_accepted():
    gh = _make_gh()
    body = json.dumps(
        [{"type": "required_status_checks", "parameters": {"required_status_checks": ["lint"]}}]
    )
    router = _router(
        classic={"rc": 1, "stderr": "gh: Not Found (HTTP 404)"},
        rules={"rc": 0, "stdout": body},
    )
    with patch.object(gh, "_run_gh", side_effect=router):
        assert _contexts(gh) == ["lint"]
