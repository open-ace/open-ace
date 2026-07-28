"""#2044 Phase B acceptance tests — the six required invariants.

Each test here pins one acceptance criterion #2044 lists for the Phase B
refactor. Together with ``test_phase_result_is_only_phase_status_transition_path``
(the T3 AST guard in ``test_phase_host_contract.py``) these are the seven
issue-required tests. They are guard-style: a few express new invariants and
run red-then-green; the doc/AST scans pass-on-arrival (their job is to fail
loudly if a future change regresses the contract).

Naming mirrors the acceptance criteria exactly so a reviewer can map test ↔
criterion. Mocking follows ``tests/autonomous/test_orchestrator_characterization.py``
(patch Database/Repository/SessionManager/Runner at import time; drive
``o.repo`` as a MagicMock so no DB/git/agent work runs).
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import PHASE_ORDER, AutonomousOrchestrator
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext

# ── Helpers (mirror test_orchestrator_characterization.py) ───────────────────


def _make_orchestrator(workflow: dict) -> AutonomousOrchestrator:
    """Build an orchestrator whose repo is a fully-mocked MagicMock."""
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager"),
        patch("app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"),
    ):
        mock_repo = MagicMock()
        # ``get_workflow`` returns a FRESH copy each call so phases that re-read
        # ``self.workflow`` after a side effect see the scripted state.
        mock_repo.get_workflow.return_value = dict(workflow)
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(workflow.get("workflow_id", "wf-accept"))
        o.repo = mock_repo
    return o


def _active_workflow(phase: str = "planning", **overrides) -> dict:
    wf = {
        "workflow_id": "wf-accept",
        "status": "developing",
        "current_phase": phase,
        "project_path": "/srv/open-ace",
        "worktree_path": "/srv/open-ace/.worktrees/wf-accept",
        "branch_name": "auto-dev/wf-accept",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "dev_round": 1,
        "current_round": 0,
        "transient_retry_count": 0,
    }
    wf.update(overrides)
    return wf


def _stub_phase_neighbors(o: AutonomousOrchestrator) -> dict[str, MagicMock]:
    """Stub every advance()-adjacent helper so a registry handler can run in
    isolation. Returns the spy dict for assertion."""
    spies: dict[str, MagicMock] = {}
    for name in (
        "_ensure_worktree",
        "_reconcile_worktree_transition",
        "_get_gh",
    ):
        spy = MagicMock(name=name)
        spies[name] = spy
        setattr(o, name, spy)
    return spies


# ── 1. test_phase_failure_does_not_partially_commit_next_phase ────────────────
#
# Acceptance: "a phase that fails mid-body must NOT write the next phase's
# state before its own side effects are confirmed." The entrypoint-layer angle
# is pinned in test_orchestrator_characterization.TestCommitPhaseResult; this
# test covers the HANDLER layer — a migrated handler that does host calls
# mid-body then returns PhaseResult.failed must leave current_phase untouched.


def test_phase_failure_does_not_partially_commit_next_phase():
    o = _make_orchestrator(_active_workflow(phase="development"))
    # Spy the host aliases a handler reaches mid-body (so they record without
    # routing through the real _emit/_create_milestone JSON path). The point is
    # that a handler CAN do bookkeeping mid-body and STILL fail safely — the
    # PhaseResult.failed is what governs the phase/status write, not the host
    # calls.
    emit_spy = MagicMock(name="emit_phase_change")
    milestone_spy = MagicMock(name="create_milestone_idempotent")
    o.emit_phase_change = emit_spy  # type: ignore[method-assign]
    o.create_milestone_idempotent = milestone_spy  # type: ignore[method-assign]

    # A migrated handler that performs bookkeeping mid-body and THEN fails. It
    # must NOT stuff current_phase=pr_review into the patch (the S2 whitelist +
    # T3 guard would catch it; here we verify the entrypoint writes only
    # status=failed + error_message when the handler returns PhaseResult.failed).
    def fake_handler(ctx: WorkflowContext, deps) -> PhaseResult:
        deps.host.emit_phase_change({"phase": "development", "note": "mid-body"})
        deps.host.create_milestone_idempotent(
            phase="development",
            milestone_type="dev_started",
            status="failed",
            round_num=1,
            payload={},
        )
        return PhaseResult.failed(
            structured_error={"message": "tests failed mid-development"},
        )

    # Drive the handler through _build_phase_deps so it sees the spy host.
    ctx = o._build_workflow_context(o.workflow)
    deps = o._build_phase_deps()
    result = fake_handler(ctx, deps)
    o._commit_phase_result(result)

    # Mid-body host calls ran (the failure did not skip them).
    emit_spy.assert_called_once()
    milestone_spy.assert_called_once()
    # The LAST _update_workflow write must NOT advance current_phase.
    last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
    assert "current_phase" not in last_updates, (
        "a failed handler must not partially commit the next phase; got " f"{last_updates!r}"
    )
    assert last_updates["status"] == "failed"
    assert last_updates["error_message"] == "tests failed mid-development"


# ── 2. test_restart_reenters_from_persisted_phase_state ───────────────────────
#
# Acceptance: "a NEW orchestrator instance reading current_phase from the
# persisted workflow dispatches to the right phase." The general restart case
# is pinned in TestRestartReentry; this adds a migrated-phase-specific
# assertion: a persisted current_phase="merge" resolves the registry handler
# (phases/merge.py), NOT the legacy _do_merge shim.


def test_restart_reenters_from_persisted_phase_state():
    from app.modules.workspace.autonomous import phases as _phases

    persisted = _active_workflow(phase="merge", status="merging")
    o = _make_orchestrator(persisted)
    _stub_phase_neighbors(o)

    merge_handle_spy = MagicMock(name="phases.merge.handle")
    saved = _phases.PHASE_HANDLERS.get("merge")
    _phases.PHASE_HANDLERS["merge"] = merge_handle_spy
    # Also spy the legacy shim to assert advance() did NOT fall through to it.
    legacy_shim_spy = MagicMock(name="_do_merge")
    o._do_merge = legacy_shim_spy
    try:
        o.advance()
    finally:
        if saved is None:
            _phases.PHASE_HANDLERS.pop("merge", None)
        else:
            _phases.PHASE_HANDLERS["merge"] = saved

    # The registry handler dispatched.
    merge_handle_spy.assert_called_once()
    # The legacy _do_merge shim is NOT the production path post-migration —
    # advance() resolved via PHASE_HANDLERS and never called the shim.
    legacy_shim_spy.assert_not_called()
    # And resolve_phase_handler("merge") returns the registry entry, not None.
    assert _phases.resolve_phase_handler("merge") is not None


# ── 3. test_each_phase_declares_preconditions_and_postconditions ──────────────
#
# Acceptance: every phase documents all 8 contract fields. Guard-style: parses
# docs/architecture/autonomous-phase-contracts.md and asserts each of the 7
# phases has a section with all 8 field headers. Pass-on-arrival; fails loudly
# if a phase section is added/edited without documenting the contract.

_CONTRACT_FIELDS = (
    "preconditions",
    "inputs",
    "authoritative evidence",
    "side effects",
    "postconditions",
    "re-entry point",
    "recovery behavior",
    "terminal outcomes",
)
_CONTRACT_PHASES = (
    "preparation",
    "planning",
    "development",
    "pr_review",
    "report",
    "wait",
    "merge",
)
_CONTRACT_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "architecture" / "autonomous-phase-contracts.md"
)


def test_each_phase_declares_preconditions_and_postconditions():
    text = _CONTRACT_DOC.read_text()
    # Split into per-phase sections. A phase section header is a level-2
    # markdown header whose lowercased text equals the phase name (e.g.
    # "## merge"). The section runs until the next level-2 header or EOF.
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines)
            header = line[3:].strip().lower()
            # Drop trailing parenthetical / annotation, keep the phase token.
            header = re.split(r"[\s(]", header, maxsplit=1)[0]
            current_name = header if header in _CONTRACT_PHASES else None
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines)

    missing: list[str] = []
    for phase in _CONTRACT_PHASES:
        body = sections.get(phase)
        if body is None:
            missing.append(f"{phase}: no '## {phase}' section in {_CONTRACT_DOC.name}")
            continue
        # Each field is a bold markdown list-item header: "- **<field>**:".
        for field in _CONTRACT_FIELDS:
            pattern = rf"-\s*\*\*{re.escape(field)}\*\*"
            if not re.search(pattern, body, re.IGNORECASE):
                missing.append(f"{phase}: missing contract field '{field}'")
    assert not missing, (
        "each phase must document all 8 contract fields "
        f"({_CONTRACT_FIELDS}):\n  " + "\n  ".join(missing)
    )
    # Sanity: the canonical PHASE_ORDER covers the documented phases (a phase
    # added to the doc but not to PHASE_ORDER — or vice versa — is a drift).
    assert set(_CONTRACT_PHASES) <= set(PHASE_ORDER) | {"wait"}


# ── 4. test_pause_resume_shutdown_contract_survives_phase_adapter ─────────────
#
# Acceptance: a handler returning PhaseResult.pause → status=paused +
# paused_at written, current_phase unchanged; resume → next advance re-
# dispatches the SAME phase. Mirrors the existing pause/resume characterization
# but driven through the PhaseResult path (the only sanctioned transition).


def test_pause_resume_shutdown_contract_survives_phase_adapter():
    o = _make_orchestrator(_active_workflow(phase="merge", status="merging"))

    # A migrated merge handler signals a policy pause (e.g. merge-conflict fork).
    pause_result = PhaseResult.pause(structured_error={"reason": "conflict-fork"})
    o._commit_phase_result(pause_result)

    last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
    assert (
        "current_phase" not in last_updates
    ), "pause must not advance the phase (resume re-dispatches the same one)"
    assert last_updates["status"] == "paused"
    assert "paused_at" in last_updates

    # Resume: status flips back to active, current_phase STILL "merge" — the
    # next advance() cycle must dispatch the SAME phase, not skip forward.
    resumed = _active_workflow(phase="merge", status="merging")
    resumed["paused_at"] = last_updates["paused_at"]
    o2 = _make_orchestrator(resumed)
    _stub_phase_neighbors(o2)

    from app.modules.workspace.autonomous import phases as _phases

    merge_handle_spy = MagicMock(name="phases.merge.handle")
    saved = _phases.PHASE_HANDLERS.get("merge")
    _phases.PHASE_HANDLERS["merge"] = merge_handle_spy
    try:
        o2.advance()
    finally:
        if saved is None:
            _phases.PHASE_HANDLERS.pop("merge", None)
        else:
            _phases.PHASE_HANDLERS["merge"] = saved

    # The same phase dispatched again — resume re-enters where it paused.
    merge_handle_spy.assert_called_once()
    # The ctx passed in carried the persisted phase, confirming the re-entry
    # came from durable state, not in-memory.
    ctx_arg = merge_handle_spy.call_args.args[0]
    assert ctx_arg.workflow["current_phase"] == "merge"


# ── 5. test_session_line_topology_survives_refactor ───────────────────────────
#
# Acceptance: the main/review/test session binding topology must remain
# accessible via deps.host.session_offsets() consistently across handlers. The
# refactor extracted handlers into phases/*.py but did NOT change the
# _session_usage_offsets structure — this guards that.


def test_session_line_topology_survives_refactor():
    o = _make_orchestrator(_active_workflow(phase="development"))
    # Populate the topology the way the orchestrator does during a run: a dict
    # keyed by session_id, each value a usage-offset dict.
    o._session_usage_offsets = {
        "sess-main": {"input_tokens": 100, "output_tokens": 50},
        "sess-review": {"input_tokens": 30, "output_tokens": 10},
        "sess-test": {"input_tokens": 20, "output_tokens": 5},
    }

    # The host alias + WorkflowContext.session_bindings both surface it.
    deps = o._build_phase_deps()
    assert deps.host.session_offsets() is o._session_usage_offsets

    ctx = o._build_workflow_context(o.workflow)
    assert ctx.session_bindings is o._session_usage_offsets

    # Shape: a dict of dicts (the structure phases/pr_review.py and
    # phases/development.py rely on when they read usage offsets). The keys
    # are opaque session ids; the values are token-count dicts.
    topology = deps.host.session_offsets()
    assert isinstance(topology, dict)
    for session_id, offsets in topology.items():
        assert isinstance(session_id, str)
        assert isinstance(offsets, dict)
    # A handler reading a specific session line's offsets sees the persisted
    # counts (this is what the dev/review handlers do for usage reporting).
    assert topology["sess-main"]["input_tokens"] == 100


# ── 6. test_phase_handler_uses_services_instead_of_reimplementing_evidence_or_git_logic ─────────
#
# Acceptance: handlers must reach git via deps.git_workspace, evidence via
# deps.evidence, subprocess/HTTP via deps.host aliases — NOT by reimplementing
# shell git / subprocess / requests calls. AST guard (mirror T3 style): scan
# phases/*.py for forbidden raw patterns.

_PHASES_SRC_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "workspace" / "autonomous" / "phases"
)

# Forbidden call patterns: a handler must not spawn its own subprocess, open
# its own HTTP session, or shell out to git/gh — those go through the services.
_FORBIDDEN_PATTERNS = (
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\brequests\.(get|post|put|delete|patch|head|request)\s*\("),
    # Shell-string git/gh invocation (e.g. ``"git ..."`` / ``"gh ..."`` passed
    # to a runner) — handlers must use deps.git_workspace / deps.host aliases.
    # Match a string literal that starts with the command word.
    re.compile(r"['\"](?:git|gh)\s"),
)
# Aids: these are the sanctioned service surfaces a handler SHOULD use.
_SANCTIONED = ("deps.git_workspace", "deps.evidence", "deps.host", "deps.sandbox")


def test_phase_handler_uses_services_instead_of_reimplementing_evidence_or_git_logic():
    if not _PHASES_SRC_DIR.exists():
        pytest.skip("phases/ dir not present yet (pre-T10)")
    violations: list[str] = []
    for src in sorted(_PHASES_SRC_DIR.glob("*.py")):
        if src.name == "__init__.py":
            continue
        text = src.read_text()
        for pat in _FORBIDDEN_PATTERNS:
            for m in pat.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                snippet = text.splitlines()[lineno - 1].strip()
                violations.append(
                    f"{src.name}:{lineno}: forbidden pattern {m.pattern!r}: {snippet}"
                )
    assert not violations, (
        "phases/*.py must use deps.git_workspace / deps.evidence / deps.host "
        f"({_SANCTIONED}) instead of raw subprocess/requests/shell git:\n  "
        + "\n  ".join(violations)
    )
    # And no top-level ``import subprocess`` / ``import requests`` either.
    for src in sorted(_PHASES_SRC_DIR.glob("*.py")):
        if src.name == "__init__.py":
            continue
        tree = ast.parse(src.read_text(), filename=str(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in (
                        "subprocess",
                        "requests",
                    ), f"{src.name}: handlers must not import {alias.name} directly"
