"""Issue #2428: CI repair must only spend its budget on merge-blocking checks.

``phases/merge.py`` started a repair round for every failing check:

    failed = [c for c in checks if c.get("bucket") == "fail"]
    if failed:
        deps.host.start_ci_repair_round(wf, pr_number, failed)

``MAX_CI_REPAIR_ATTEMPTS`` is 5, so a couple of non-blocking failures could
exhaust the budget and kill the workflow. wf227 (issue #2328) died reporting
``test (3.13)`` — a check ``main`` does not require.

``ReadinessService.collect_actionable_ci_failures`` already implemented this
split, citing the #1989/#2034 lesson, but nothing ever called it.

The fallback direction matters: when the required set cannot be determined the
filter must degrade to the old "repair everything" behaviour. Deferring (the
ReadinessService ``indeterminate`` semantic) would stall the workflow forever.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.phases.merge import _blocking_failures

REQUIRED = ["lint", "test (3.10)", "test (3.11)", "test (3.12)", "build"]


def _gh(required=REQUIRED, raises: Exception | None = None):
    gh = MagicMock()
    if raises is not None:
        gh.get_branch_protection.side_effect = raises
    else:
        gh.get_branch_protection.return_value = {
            "required_status_checks": {"contexts": list(required)}
        }
    return gh


def _check(name: str, bucket: str = "fail") -> dict:
    return {"name": name, "bucket": bucket}


def test_non_required_failures_do_not_trigger_repair():
    """The wf227 shape: only optional checks are red."""
    checks = [
        _check("test (3.13)"),
        _check("postgres-test"),
        _check("schema-sync"),
        _check("lint", bucket="pass"),
    ]
    assert _blocking_failures(_gh(), checks, 2425, "main") == [], (
        "a repair round would be started — and an attempt consumed — for checks "
        "that do not block the merge"
    )


def test_required_failures_are_returned():
    checks = [_check("lint"), _check("test (3.13)")]
    result = _blocking_failures(_gh(), checks, 1, "main")
    assert [c["name"] for c in result] == ["lint"]


def test_mixed_failures_pass_only_the_blocking_ones_to_the_agent():
    """The repair agent should not be handed noise it cannot act on usefully."""
    checks = [
        _check("test (3.13)"),
        _check("build"),
        _check("Critical PR E2E"),
        _check("test (3.11)"),
    ]
    names = [c["name"] for c in _blocking_failures(_gh(), checks, 1, "main")]
    assert names == ["build", "test (3.11)"]


def test_passing_and_pending_checks_are_never_returned():
    checks = [
        _check("lint", bucket="pass"),
        _check("build", bucket="pending"),
        _check("test (3.10)", bucket="fail"),
    ]
    names = [c["name"] for c in _blocking_failures(_gh(), checks, 1, "main")]
    assert names == ["test (3.10)"]


def test_no_failures_short_circuits_without_an_api_call():
    """Do not pay for a protection lookup when there is nothing to filter."""
    gh = _gh()
    assert _blocking_failures(gh, [_check("lint", bucket="pass")], 1, "main") == []
    gh.get_branch_protection.assert_not_called()


def test_protection_lookup_failure_degrades_to_repairing_everything():
    """Must not stall: deferring forever is worse than repairing too much."""
    checks = [_check("test (3.13)"), _check("lint")]
    result = _blocking_failures(_gh(raises=RuntimeError("HTTP 403")), checks, 1, "main")
    assert [c["name"] for c in result] == ["test (3.13)", "lint"]


def test_empty_required_set_degrades_to_repairing_everything():
    """An unprotected branch has no gate; do not conclude nothing needs repair."""
    checks = [_check("test (3.13)"), _check("lint")]
    result = _blocking_failures(_gh(required=[]), checks, 1, "main")
    assert [c["name"] for c in result] == ["test (3.13)", "lint"]
