"""Contract tests for the Phase B (#2044) handler decoupling layer."""

from __future__ import annotations

from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps, PhaseHandler, PhaseHost


def test_phase_host_protocol_has_narrow_surface():
    """A handler must not depend on the orchestrator concrete class; it sees
    only these members of PhaseHost."""
    for method in [
        "emit_phase_change",
        "emit_status_change",
        "session_offsets",
        "cancellation",
        "create_milestone_idempotent",
        # Merge-phase helpers (#2044 Phase B T10): orchestrator-private methods
        # exposed on the host so phases/merge.py can call them without a
        # concrete orchestrator reference. See phase_host.py docstring.
        "validate_pre_merge_change_scope",
        "sync_failed_pr_with_main",
        "branch_contains_main",
        "start_ci_repair_round",
        "perform_git_cleanup",
        "resolve_merge_conflicts",
    ]:
        assert hasattr(PhaseHost, method), f"PhaseHost missing {method}"
    # annotation-only data member lives in __annotations__, not dir()
    assert "workflow_id" in PhaseHost.__annotations__


def test_phase_deps_carries_services_not_orchestrator():
    """PhaseDeps fields are service objects + host, never the orchestrator."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(PhaseDeps)}
    assert field_names == {
        "host",
        "gh",
        "git_workspace",
        "evidence",
        "sandbox",
        "repo",
        "agent_runner",
    }


def test_phase_handler_protocol_has_handle_taking_ctx_deps():
    assert hasattr(PhaseHandler, "handle")


import ast
from pathlib import Path

_PHASES_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "workspace" / "autonomous" / "phases"
)
_FORBIDDEN_WRITES = {"current_phase", "status", "completed_at", "paused_at"}


def _phase_status_write_violations(tree: ast.AST):
    """Yield (lineno, name) for any direct write of a forbidden field inside
    phases/ — either a dict-key assignment (patch['status'] = ...) or a literal
    _update_workflow({'status': ...}) call."""
    for node in ast.walk(tree):
        # dict-key writes: Assign(target=Subscript(slice=Constant(value=...)))
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                key = None
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant):
                    key = tgt.slice.value
                if isinstance(key, str) and key in _FORBIDDEN_WRITES:
                    yield (node.lineno, key)
        # _update_workflow({...}) with a forbidden literal key
        if isinstance(node, ast.Call):
            fn = node.func
            # Match any attribute call whose attr ends in "update_workflow"
            # (covers self._update_workflow, deps.repo.update_workflow,
            # repo.update_workflow) plus the bare _update_workflow local name.
            is_update = (isinstance(fn, ast.Attribute) and fn.attr.endswith("update_workflow")) or (
                isinstance(fn, ast.Name) and fn.id == "_update_workflow"
            )
            if is_update and node.args:
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if isinstance(k, ast.Constant) and k.value in _FORBIDDEN_WRITES:
                                yield (node.lineno, k.value)


def test_git_workspace_service_class_exists_and_has_lifecycle_methods():
    from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService

    expected = [
        "fail_recovery_closed",
        "ensure_worktree",
        "get_preferred_worktree_path",
        "record_trusted_head",
        "resolve_recovery_head",
        "cleanup_worktree_and_branch",
        "sync_worktree_to_pr_remote_head",
        "resolve_merge_conflicts",
        "clear_transition_journal",
        "verify_worktree_registered",
        "path_within_worktrees_root",
        "reconcile_worktree_transition",
        "remove_worktree_idempotent",
        "verify_worktree_restored",
    ]
    for method in expected:
        assert hasattr(GitWorkspaceService, method), f"GitWorkspaceService missing {method}"


def test_build_phase_deps_populates_sandbox_provider():
    """#2044 Phase B T13: ``deps.sandbox`` must be a SandboxProvider, not None.

    The runner owns per-task provider selection (local Legacy / remote
    RemoteMachine); the orchestrator exposes its default — the same instance
    the runner uses for local spawns — so handlers that need to introspect
    capabilities or build a spec get a live provider. Before T13,
    ``_build_phase_deps`` read ``getattr(self, "_sandbox_provider", None)`` and
    the orchestrator had no such attribute, so ``deps.sandbox`` was always None.
    """
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator as Orch
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider

    orch = Orch.__new__(Orch)
    # Minimal stubs: the property reads runner._sandbox_provider; a __new__-built
    # orchestrator has no runner, so it must fall back to LegacyPosixProvider
    # (mirrors _git_workspace/_evidence lazy-attach behavior).
    orch._runner = None
    provider = orch._sandbox_provider
    assert isinstance(
        provider, LegacyPosixProvider
    ), f"_sandbox_provider property must return a provider, got {type(provider)}"
    # The full PhaseDeps path must also surface it.
    orch._gh = None
    orch._git_workspace_instance = None
    orch._evidence_instance = None
    orch.repo = None
    deps = orch._build_phase_deps()
    assert isinstance(
        deps.sandbox, LegacyPosixProvider
    ), f"_build_phase_deps must surface _sandbox_provider, got {deps.sandbox!r}"


def test_build_phase_deps_surfaces_runner_injected_provider():
    """#2044 T13: when a runner carries an injected SandboxProvider (tests/
    remote), ``_sandbox_provider`` exposes the SAME instance the runner will
    use for local spawns — handler-built specs and the spawn path agree.
    """
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator as Orch
    from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider

    orch = Orch.__new__(Orch)

    class _RunnerStub:
        _sandbox_provider = FakeSandboxProvider()

    orch._runner = _RunnerStub()  # type: ignore[assignment]
    orch._gh = None
    orch._git_workspace_instance = None
    orch._evidence_instance = None
    orch.repo = None
    deps = orch._build_phase_deps()
    assert deps.sandbox is _RunnerStub._sandbox_provider


def test_phases_dir_does_not_directly_write_phase_status_fields():
    """#2044 Phase B invariant: phases/*.py must not write current_phase/status/
    completed_at/paused_at directly — those flow only through PhaseResult."""
    if not _PHASES_DIR.exists():
        return  # nothing migrated yet; vacuously green until T10+
    violations = []
    for src in _PHASES_DIR.glob("*.py"):
        if src.name == "__init__.py":
            continue
        tree = ast.parse(src.read_text(), filename=str(src))
        for lineno, key in _phase_status_write_violations(tree):
            violations.append(f"{src.name}:{lineno} writes forbidden field {key!r}")
    assert not violations, "phase/status writes must go through PhaseResult:\n  " + "\n  ".join(
        violations
    )
