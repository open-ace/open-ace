"""Regression: the verifier prompt must demand snapshot extraction when empty.

When an issue has no structured acceptance criteria (a bug report like #2394),
the persisted snapshot is empty (source="missing", no required_paths/checklist),
so acceptance_verification asks the verifier agent to EXTRACT the criteria and
return them in the ``snapshot`` field. The old prompt buried this requirement
in a trailing parenthetical — glm-5 ignored it, omitted the snapshot, and
burned the infra-retry budget (b48179df, #30). The prompt must make the
extraction requirement prominent and unmissable when the snapshot is empty,
and stay quiet about it when criteria already exist.
"""

import pytest

from app.modules.workspace.autonomous.acceptance_snapshot import AcceptanceSnapshot
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

pytestmark = [pytest.mark.regression]

_MARKER = "REQUIRED — Extract the acceptance snapshot"


def _prompt(snapshot) -> str:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    return orch._build_verification_prompt(snapshot, "merge-sha", "base-sha", 2394)


def test_prompt_demands_extraction_when_snapshot_empty():
    snap = AcceptanceSnapshot()  # empty; source="missing", no paths/checklist
    prompt = _prompt(snap)
    assert _MARKER in prompt
    assert "MUST" in prompt
    # The schema glm-5 must populate, so it knows the shape to return.
    assert "required_paths" in prompt
    assert "checklist" in prompt


def test_prompt_does_not_demand_extraction_when_snapshot_present():
    snap = AcceptanceSnapshot(
        required_paths=["app/utils/datetime_utils.py"],
        checklist=["ensure_utc_suffix handles datetime input"],
        source="convention",
    )
    prompt = _prompt(snap)
    assert _MARKER not in prompt


def test_prompt_fenced_snapshot_token_is_valid_json_both_cases():
    # The fenced template's `snapshot` value must be a valid JSON token (null or
    # an object), never prose glued into the value slot — glm-5 copies the
    # template, and a broken token is exactly the unparseable output #29 fixed.
    empty_prompt = _prompt(AcceptanceSnapshot())
    full_prompt = _prompt(
        AcceptanceSnapshot(required_paths=["app/x.py"], checklist=["done"], source="convention")
    )
    assert '"snapshot": null}' in full_prompt
    assert '"snapshot": {"required_paths":' in empty_prompt
    # No prose leaked into the value position.
    assert '"snapshot": null ' not in full_prompt
    assert '"snapshot": the' not in empty_prompt
