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


# ── 7. test_fresh_orchestrator_binds_gh_for_registry_handlers (P1-a) ─────────
#
# #2044 Phase B review regression: a FRESH orchestrator (real __init__ via
# _make_orchestrator, NOT __new__) calling advance() WITHOUT replacing the real
# registry handler must hand that handler a LIVE GitHubOps (deps.gh), not None.
#
# Root cause: __init__ sets self._gh = None and the scheduler's _advance_single
# builds a fresh orchestrator each tick. The production path
# advance() → resolve_phase_handler → _dispatch_phase → _build_phase_deps used
# gh=self._gh (None). The 3 test-compat shims (_do_development/_do_pr_review)
# did their own self._gh = self._get_gh() before reading it, so they masked the
# bug, but the REGISTRY production path (phases/*.handle) reads deps.gh directly
# → development crashed at gh.get_current_branch(); pr_review's branch probe
# swallowed the exception and mislabelled the workflow "No Changes Detected".
#
# Fix: _build_phase_deps binds gh lazily via _get_gh() when self._gh is None.


def test_fresh_orchestrator_binds_gh_for_registry_handlers():
    from app.modules.workspace.autonomous import phases as _phases
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    o = _make_orchestrator(_active_workflow(phase="development"))
    # Stub ONLY the worktree healers that run before dispatch. Do NOT stub
    # _get_gh — the bug is exactly that _build_phase_deps never calls it.
    for name in ("_ensure_worktree", "_reconcile_worktree_transition"):
        setattr(o, name, MagicMock(name=name))

    # Sentinel live GitHubOps the lazy binder should produce when self.workflow
    # is truthy (it is during advance). Patching _get_gh isolates the contract
    # from the real filesystem bind.
    sentinel_gh = MagicMock(spec=GitHubOps, name="bound_gh")
    captured: dict = {}

    def capturing_handler(ctx, deps):
        captured["gh"] = deps.gh
        return PhaseResult.completed(next_phase="pr_review", next_status="pr_review")

    saved = _phases.PHASE_HANDLERS.get("development")
    _phases.PHASE_HANDLERS["development"] = capturing_handler
    try:
        with patch.object(o, "_get_gh", return_value=sentinel_gh):
            o.advance()
    finally:
        if saved is None:
            _phases.PHASE_HANDLERS.pop("development", None)
        else:
            _phases.PHASE_HANDLERS["development"] = saved

    # The registry handler MUST receive a live GitHubOps, not None. On the
    # unfixed code deps.gh is None because _build_phase_deps uses self._gh
    # (set to None by __init__) instead of binding via _get_gh().
    assert captured.get("gh") is sentinel_gh, (
        "fresh orchestrator must bind deps.gh before dispatching a registry "
        f"handler; got {captured.get('gh')!r}"
    )


# ── 8. test_preparation_checkpoints_issue_id_before_branch_raise (P1-b) ──────
#
# #2044 Phase B review regression: in _do_preparation, when create_issue
# succeeds but a later step (branch/worktree creation) raises, the
# github_issue_number MUST already be persisted so the next scheduler tick does
# NOT call create_issue again (duplicate issue). Pre-migration these ids were
# written immediately after each side effect; the PhaseResult migration
# deferred them into a workflow_patch dict that's only committed at the end —
# so a raise after issue creation lost the number. The #2044 invariant ALLOWS
# immediate bookkeeping-field writes (only phase/status must flow through
# PhaseResult), so external-resource ids are immediate-checkpointed.


def test_preparation_checkpoints_issue_id_before_branch_raise():
    from app.modules.workspace.autonomous.github_ops import GitHubOpsError

    # No github_issue_number yet, requirements_text present → issue gets created.
    wf = _active_workflow(
        phase="preparation",
        status="preparing",
        is_new_project=False,
        branch_strategy="worktree",
        requirements_text="build the thing",
        github_issue_number=None,
    )
    wf["project_path"] = "/srv/open-ace"

    o = _make_orchestrator(wf)
    # Stub the git/workspace healers and event emission that aren't under test.
    o._reconcile_worktree_transition = MagicMock(name="reconcile")
    o._emit = MagicMock(name="_emit")
    o._create_milestone = MagicMock(name="_create_milestone")

    # gh.create_issue returns a number; the branch-creation path raises.
    fake_gh = MagicMock(name="gh")
    fake_gh.create_issue.return_value = {"number": 4242, "url": "https://x/4242"}

    # Drive every _run_git the branch-creation path makes (fetch, worktree
    # prune, two show-ref probes for surviving branches, rev-parse base) so the
    # code reaches create_worktree, which raises a transient blip AFTER issue
    # creation succeeded.
    def run_git(args, **kw):
        rc = MagicMock(returncode=0, stdout="", stderr="")
        if "show-ref" in args:
            rc.returncode = 1  # branch + remote don't exist → create fresh
        elif "rev-parse" in args:
            rc.stdout = "deadbeef"
        return rc

    fake_gh._run_git.side_effect = run_git
    fake_gh.list_worktrees.return_value = []
    fake_gh.path_exists_as_user.return_value = False
    fake_gh.create_worktree.side_effect = GitHubOpsError("transient branch blip")

    persisted_updates: list[dict] = []
    real_update = o._update_workflow

    def recording_update(updates):
        persisted_updates.append(dict(updates))
        return real_update(updates)

    o._update_workflow = recording_update  # type: ignore[method-assign]

    with (
        patch.object(o, "_get_gh", return_value=fake_gh),
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=fake_gh),
    ):
        # advance() runs preparation then its exception handler on the raise.
        with pytest.raises(GitHubOpsError):
            o.advance()

    # The github_issue_number MUST have been persisted BEFORE the raise. On the
    # unfixed code the number sat in the deferred workflow_patch dict that was
    # never committed (the raise skipped _commit_phase_result), so a re-entry
    # would see github_issue_number=None and call create_issue again.
    persisted_numbers = [
        u.get("github_issue_number") for u in persisted_updates if "github_issue_number" in u
    ]
    assert 4242 in persisted_numbers, (
        "github_issue_number must be persisted immediately after create_issue "
        "succeeds so a later raise cannot cause a duplicate issue on re-entry; "
        f"persisted updates were {persisted_updates!r}"
    )


# ── 9. test_failed_phase_result_preserves_error_message (P2) ────────────────
#
# #2044 Phase B review regression: when a workflow re-entered advance() after a
# transient retry (transient_retry_count > 0) and the phase then returns
# PhaseResult.failed, the just-written error_message MUST survive. The post-
# dispatch transient-reset block unconditionally ran
# _update_workflow({"error_message": ""}) on a non-pause result, wiping the
# failure reason and leaving an undiagnosable status=failed. The pause outcome
# was already gated (returns early); failed is now gated too.


def test_failed_phase_result_preserves_error_message():
    from app.modules.workspace.autonomous import phases as _phases

    wf = _active_workflow(phase="development", transient_retry_count=2)
    o = _make_orchestrator(wf)
    # Stub the worktree healers; this test is about the post-dispatch reset.
    for name in ("_ensure_worktree", "_reconcile_worktree_transition"):
        setattr(o, name, MagicMock(name=name))

    def failing_handler(ctx, deps):
        return PhaseResult.failed(structured_error={"message": "boom"})

    saved = _phases.PHASE_HANDLERS.get("development")
    _phases.PHASE_HANDLERS["development"] = failing_handler
    try:
        o.advance()
    finally:
        if saved is None:
            _phases.PHASE_HANDLERS.pop("development", None)
        else:
            _phases.PHASE_HANDLERS["development"] = saved

    # The failed PhaseResult committed status=failed + error_message="boom" via
    # _commit_phase_result. The post-dispatch transient-reset block must NOT
    # have run _update_workflow({"error_message": ""}) after it — that would
    # wipe the failure reason and leave an undiagnosable status=failed. The
    # LAST persisted update_workflow write must therefore still carry "boom".
    all_updates = [c.args[1] for c in o.repo.update_workflow.call_args_list]
    failed_writes = [u for u in all_updates if u.get("status") == "failed"]
    assert failed_writes, f"expected a status=failed write; got {all_updates!r}"
    assert failed_writes[-1]["error_message"] == "boom"
    # And no subsequent write cleared it (the bug: transient-reset appended
    # {"error_message": ""} after the failed write).
    last = all_updates[-1]
    assert last.get("error_message") != "", (
        "transient-retry reset wiped the failed PhaseResult's error_message; "
        f"final update was {last!r}, all updates {all_updates!r}"
    )
