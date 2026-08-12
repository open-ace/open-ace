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


# --- glm-5 prose-instead-of-json compliance (prod 2026-08-12): the verifier
# repeatedly emitted a markdown report ("## 验收总结 ✅9/10") and never produced
# the fenced JSON block, stranding workflows with infra_error "not valid JSON".
# The prompt must make JSON-only output unmissable and cap evidence so the JSON
# fits the output budget (avoids truncation too).


def test_prompt_leads_with_json_only_contract_before_snapshot():
    prompt = _prompt(
        AcceptanceSnapshot(required_paths=["app/x.py"], checklist=["done"], source="convention")
    )
    # A JSON-only mandate exists and appears BEFORE the acceptance snapshot dump
    # so glm sees the format contract first, not last.
    contract = "fenced JSON object"
    assert contract in prompt
    assert prompt.index(contract) < prompt.index("Acceptance snapshot")
    # Explicit prohibition of prose/preamble.
    lower = prompt.lower()
    assert "nothing else" in lower or "no prose" in lower


def test_prompt_caps_evidence_verbosity():
    # Verbose evidence (5-11 refs/verdict, long notes) ballooned the JSON and
    # exhausted the output budget mid-object. Cap refs + note length.
    prompt = _prompt(
        AcceptanceSnapshot(required_paths=["app/x.py"], checklist=["done"], source="convention")
    )
    lower = prompt.lower()
    assert "at most 2" in lower or "at most two" in lower
    assert "60 character" in lower or "60-char" in lower


def test_prompt_reinforces_json_only_at_end():
    prompt = _prompt(
        AcceptanceSnapshot(required_paths=["app/x.py"], checklist=["done"], source="convention")
    )
    # Trailing reinforcement so glm doesn't drift into prose at the end.
    assert "ONLY" in prompt
