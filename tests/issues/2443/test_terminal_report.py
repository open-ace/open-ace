"""Issue #2443 PR-A: make an exhausting CI-repair terminal `failed` visible.

The merge phase writes `status=failed` at ~12 CI-repair terminal sites without
ever posting to the issue, so a workflow that exhausts CI repair disappears
into the DB (the #2443 absorbing-failed gap). PR-A posts a structured terminal
report at each site — without changing the status machine — gated by an
idempotent milestone so scheduler restart/replay never double-posts.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.modules.workspace.autonomous.terminal_report_i18n import render_ci_repair_terminal_report

ORCH = Path(__file__).resolve().parents[3] / "app/modules/workspace/autonomous/orchestrator.py"
ORCH_SRC = ORCH.read_text(encoding="utf-8")


def test_render_contains_reason_attempts_pr_and_retry_entry():
    body = render_ci_repair_terminal_report(
        category="ci_repair_exhausted",
        reason="CI failed after 5 automatic repair rounds",
        attempts=5,
        pr_number=2436,
        failure_names="lint, test (3.11)",
        branch_name="auto-dev/xyz",
    )
    assert "CI failed after 5 automatic repair rounds" in body
    assert "5" in body
    assert "#2436" in body
    assert "lint, test (3.11)" in body
    assert "auto-dev/xyz" in body
    assert "retry" in body.lower()
    assert "POST /workflows" in body


def test_render_omits_optional_fields_when_empty():
    body = render_ci_repair_terminal_report(
        category="ci_repair_transient_exhausted",
        reason="transient API errors persisted",
        attempts=6,
        pr_number=1,
    )
    assert "transient API errors persisted" in body
    assert "Failing checks" not in body
    assert "Branch" not in body


def test_helper_defined_and_uses_idempotent_milestone_type():
    """The report-posted milestone must NOT use the ci_repair_ prefix.

    ``_create_milestone`` only matches *completed* milestones for non-ci_repair_
    types; ci_repair_ types match in_progress only. A completed
    ``terminal_report_posted`` milestone therefore dedups replay; a
    ``ci_repair_terminal_report_posted`` one would not.
    """
    assert re.search(r"def _emit_ci_repair_terminal_report\(", ORCH_SRC)
    assert '"terminal_report_posted"' in ORCH_SRC
    assert "ci_repair_terminal_report_posted" not in ORCH_SRC


def test_every_tier2_terminal_site_emits_a_report():
    """PR-C routed the 3 Tier1 exhaustion sites (MAX / no-change /
    unchanged-signature) through ``_escalate_ci_repair_exhaustion`` instead of
    emitting directly — under the dev-round cap they escalate to development
    (no report, by design); at the cap / for Tier2 they fall through to a
    report. So the direct ``_emit_ci_repair_terminal_report`` call sites are now
    the 8 Tier2 categories + 1 inside the escalation helper's Tier2 branch, plus
    the definition = 10. Tier1/Tier2 category coverage is locked in
    test_escalation.py; this guards that the report wiring itself survives."""
    calls = ORCH_SRC.count("_emit_ci_repair_terminal_report(")
    assert calls == 10, (
        f"expected 8 Tier2 direct + 1 escalation fallthrough + 1 def = 10, "
        f"found {calls}; a CI-repair terminal site may be missing its report"
    )
    assert "_escalate_ci_repair_exhaustion(" in ORCH_SRC
