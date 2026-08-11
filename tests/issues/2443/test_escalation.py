"""Issue #2443 PR-C: route CI-repair exhaustion through tiered escalation.

PR-A made an exhausting CI-repair terminal ``failed`` visible (a report on the
issue). PR-B reset the full CI-repair counter set on retry. But the exhaustion
itself was still *terminal* — a workflow whose PR hit ``MAX_CI_REPAIR_ATTEMPTS``
(or whose failure signature refused to change, or whose agent kept producing no
changes) silently died in ``failed`` even though the PR branch and CI logs were
fully recoverable. A fresh development pass (re-derived implementation, aware of
the prior CI failure via the preserved ``ci_repair_context``) can still succeed.

PR-C splits the exhaustion sites into two tiers:

* **Tier1** (recoverable: PR branch present, failure actionable) —
  ``ci_repair_exhausted`` (MAX), ``ci_repair_no_change_exhausted``,
  ``ci_repair_signature_unchanged``. While ``merge_fail_dev_rounds`` is under
  ``MAX_MERGE_FAIL_DEV_ROUNDS``, rebase the existing PR branch onto main, reset
  the full counter set, and re-enter *development* on the SAME PR/branch
  (``ci_repair_context`` preserved → the dev prompt's ``_get_ci_repair_prompt``
  surfaces it). At the cap, fall through to Tier2.
* **Tier2** (truly stuck / unrecoverable) — transient/diagnostics exhaustion,
  env mismatch, context overflow, branch drift, push failure, agent failure.
  These keep PR-A's behavior: terminal report + ``failed``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import (
    MAX_CI_REPAIR_ATTEMPTS,
    MAX_CI_REPAIR_TRANSIENT_RETRIES,
    MAX_MERGE_FAIL_DEV_ROUNDS,
    AutonomousOrchestrator,
)

ORCH = Path(__file__).resolve().parents[3] / "app/modules/workspace/autonomous/orchestrator.py"
ORCH_SRC = ORCH.read_text(encoding="utf-8")

AUTONOMOUS_ROUTE = Path(__file__).resolve().parents[3] / "app/routes/autonomous.py"
ROUTE_SRC = AUTONOMOUS_ROUTE.read_text(encoding="utf-8")

TIER1_CATEGORIES = [
    "ci_repair_exhausted",  # MAX_CI_REPAIR_ATTEMPTS reached
    "ci_repair_no_change_exhausted",
    "ci_repair_signature_unchanged",
]
TIER2_CATEGORIES = [
    "ci_repair_environment_mismatch",
    "ci_repair_context_overflow",
    "ci_repair_branch_changed",
    "ci_repair_push_failed",
    "ci_repair_terminal",
    "ci_repair_agent_failed",
    "ci_repair_transient_exhausted",
    "ci_repair_diagnostics_exhausted",
]


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-2443",
        "user_id": 1,
        "status": "merging",
        "current_phase": "merge",
        "dev_round": 1,
        "ci_repair_attempts": 0,
        "ci_repair_transient_retries": 0,
        "ci_repair_no_change_retries": 0,
        "ci_diagnostics_attempts": 0,
        "last_ci_failure_signature": "",
        "last_ci_failure_head_sha": "",
        "ci_repair_context": "",
        "merge_fail_dev_rounds": 0,
        "branch_name": "auto-dev/wf-2443",
        "branch_strategy": "worktree",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._sync_failed_pr_with_main = MagicMock(return_value=False)
    return orch, mock_repo


_FAILED_CHECKS = [
    {
        "name": "lint",
        "state": "failure",
        "bucket": "fail",
        "link": "https://github.com/open-ace/open-ace/runs/123",
    }
]


def _gh(head_sha="sha-new", excerpt=""):
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = head_sha
    gh.get_check_failure_excerpt.return_value = excerpt
    return gh


# ── Tier1 behavioral: exhaustion escalates to a fresh development round ──


def test_max_exhaustion_enters_dev_round_under_cap():
    """MAX_CI_REPAIR_ATTEMPTS reached (Tier1) → re-enter development, not fail."""
    wf = _make_workflow(
        ci_repair_attempts=MAX_CI_REPAIR_ATTEMPTS,  # next_attempt = MAX+1
        merge_fail_dev_rounds=0,
        ci_repair_context="prior ci failure context",
    )
    orch, mock_repo = _make_orchestrator(wf)
    orch._emit_ci_repair_terminal_report = MagicMock()  # Tier1 must not report
    orch._get_gh = MagicMock(return_value=_gh())

    orch._start_ci_repair_round(wf, 2400, _FAILED_CHECKS)

    orch._emit_ci_repair_terminal_report.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    developing = [u for u in updates if u.get("status") == "developing"]
    assert developing, "MAX exhaustion (Tier1, under cap) must escalate to development"
    u = developing[-1]
    assert u["current_phase"] == "development"
    assert u["dev_round"] == 2
    assert u["merge_fail_dev_rounds"] == 1
    # Full counter reset — a fresh dev round must not inherit stale counts/signature.
    assert u["ci_repair_attempts"] == 0
    assert u["ci_repair_transient_retries"] == 0
    assert u["ci_repair_no_change_retries"] == 0
    assert u["ci_diagnostics_attempts"] == 0
    assert u["last_ci_failure_signature"] == ""
    assert u["last_ci_failure_head_sha"] == ""
    assert u["error_message"] == ""
    # ci_repair_context preserved (NOT cleared) so _get_ci_repair_prompt surfaces
    # the prior CI failure in the new development prompt.
    assert "ci_repair_context" not in u
    assert not any(uu.get("status") == "failed" for uu in updates)


def test_merge_fail_dev_round_cap_falls_through_to_terminal():
    """At the dev-round cap, a Tier1 exhaustion falls through to Tier2 (report + failed)."""
    wf = _make_workflow(
        ci_repair_attempts=MAX_CI_REPAIR_ATTEMPTS,
        merge_fail_dev_rounds=MAX_MERGE_FAIL_DEV_ROUNDS,  # at cap → Tier2
    )
    orch, mock_repo = _make_orchestrator(wf)
    reported = []
    orch._emit_ci_repair_terminal_report = lambda *a, **k: reported.append(k)
    orch._get_gh = MagicMock(return_value=_gh())

    orch._start_ci_repair_round(wf, 2400, _FAILED_CHECKS)

    assert reported, "cap reached → Tier2 must post a terminal report"
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in updates)
    assert not any(u.get("status") == "developing" for u in updates)


def test_signature_unchanged_with_excerpt_is_tier1():
    """A meaningful, unchanged failure signature (Tier1) escalates to dev, not fail."""
    excerpt = "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
    digest = hashlib.sha256(
        AutonomousOrchestrator._normalize_failure_excerpt(excerpt).encode()
    ).hexdigest()[:12]
    sig = f"lint::{digest}"
    wf = _make_workflow(
        ci_repair_attempts=1,  # next_attempt = 2, well under MAX
        merge_fail_dev_rounds=0,
        last_ci_failure_signature=sig,
        last_ci_failure_head_sha="sha-old",
    )
    orch, mock_repo = _make_orchestrator(wf)
    orch._emit_ci_repair_terminal_report = MagicMock()
    orch._get_gh = MagicMock(return_value=_gh(head_sha="sha-new", excerpt=excerpt))

    orch._start_ci_repair_round(wf, 2400, _FAILED_CHECKS)

    orch._emit_ci_repair_terminal_report.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(
        u.get("status") == "developing" for u in updates
    ), "meaningful signature-unchanged exhaustion is Tier1 → must escalate to dev"
    assert not any(u.get("status") == "failed" for u in updates)


def test_no_branch_routes_to_tier2_even_for_tier1_category():
    """No branch_name = cannot reuse the PR → Tier1 category falls through to Tier2."""
    wf = _make_workflow(
        ci_repair_attempts=MAX_CI_REPAIR_ATTEMPTS,
        merge_fail_dev_rounds=0,
        branch_name="",  # no recoverable PR branch
    )
    orch, mock_repo = _make_orchestrator(wf)
    reported = []
    orch._emit_ci_repair_terminal_report = lambda *a, **k: reported.append(k)
    orch._get_gh = MagicMock(return_value=_gh())

    orch._start_ci_repair_round(wf, 2400, _FAILED_CHECKS)

    assert reported, "no branch_name → Tier2 terminal report even for a Tier1 category"
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in updates)
    assert not any(u.get("status") == "developing" for u in updates)


# ── Tier2 behavioral: genuinely-stuck exhaustion stays terminal ──


def test_transient_exhausted_is_tier2():
    """Transient-API exhaustion is Tier2: report + failed, never escalates."""
    wf = _make_workflow(
        error_message="CI repair deferred: transient API error - 503 upstream",
        ci_repair_transient_retries=MAX_CI_REPAIR_TRANSIENT_RETRIES,
        merge_fail_dev_rounds=0,
    )
    orch, mock_repo = _make_orchestrator(wf)
    orch._emit_ci_repair_terminal_report = MagicMock()
    orch._get_gh = MagicMock(return_value=_gh())

    orch._start_ci_repair_round(wf, 2400, _FAILED_CHECKS)

    orch._emit_ci_repair_terminal_report.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in updates)
    assert not any(u.get("status") == "developing" for u in updates)


# ── Wiring: tier routing is locked into the source ──


def test_escalation_helper_and_constants_are_defined():
    assert re.search(r"def _escalate_ci_repair_exhaustion\(", ORCH_SRC)
    assert re.search(r"\bMAX_MERGE_FAIL_DEV_ROUNDS\b\s*=\s*\d", ORCH_SRC)
    assert re.search(r"_CI_REPAIR_TIER1_CATEGORIES\b", ORCH_SRC)


def test_tier1_categories_route_through_escalation_helper():
    """The 3 Tier1 exhaustion sites call _escalate_ci_repair_exhaustion, not a
    direct _emit + _update_workflow(failed)."""
    for cat in TIER1_CATEGORIES:
        assert f'category="{cat}"' in ORCH_SRC, f"Tier1 category {cat} missing"
    # 3 call sites + 1 definition.
    assert ORCH_SRC.count("_escalate_ci_repair_exhaustion(") >= 4


def test_tier2_categories_still_report_and_fail_directly():
    """The 8 Tier2 sites keep PR-A's direct report + failed (no regression)."""
    for cat in TIER2_CATEGORIES:
        assert f'category="{cat}"' in ORCH_SRC, f"Tier2 category {cat} missing"


def test_retry_resets_merge_fail_dev_rounds():
    """retry_workflow must zero merge_fail_dev_rounds so a retried workflow gets a
    fresh Tier1 escalation budget (mirrors PR-B's full counter reset)."""
    match = re.search(
        r"retry_updates\s*=\s*\{(?P<body>.*?)\n    _get_repo\(\)\.update_workflow",
        ROUTE_SRC,
        re.S,
    )
    assert match, "retry_updates block not found in autonomous.py"
    body = match.group("body")
    assert re.search(
        r'"merge_fail_dev_rounds"\s*:\s*0\b', body
    ), "retry_updates must reset merge_fail_dev_rounds to 0"
