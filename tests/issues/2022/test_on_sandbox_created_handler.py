"""#2022 P6.4 — orchestrator ``_on_sandbox_created`` persists mid-run sandbox state.

The callback fires right after the provider execs; the handler writes a
``sandbox_state='running'`` row + attribution so a crash between exec and task
completion leaves an orphan the startup reconciler can destroy by id.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


class _FakeRepo:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def update_workflow(self, wf_id: str, updates: dict) -> None:
        self.updates.append((wf_id, dict(updates)))


def _make_orch() -> AutonomousOrchestrator:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = _FakeRepo()
    orch._workflow_id = "wf-123"
    return orch


def test_on_sandbox_created_writes_running_state_for_remote() -> None:
    orch = _make_orch()
    orch._on_sandbox_created("s1", "sandbox-abc", "remote_machine", "remote-42")
    assert orch.repo.updates == [
        (
            "wf-123",
            {
                "sandbox_state": "running",
                "sandbox_id": "sandbox-abc",
                "sandbox_provider": "remote_machine",
                "sandbox_remote_session_id": "remote-42",
            },
        )
    ]


def test_on_sandbox_created_omits_remote_id_for_local() -> None:
    orch = _make_orch()
    orch._on_sandbox_created("s1", "sandbox-abc", "legacy_posix", None)
    wf_id, updates = orch.repo.updates[0]
    assert wf_id == "wf-123"
    assert updates["sandbox_state"] == "running"
    assert updates["sandbox_id"] == "sandbox-abc"
    assert updates["sandbox_provider"] == "legacy_posix"
    # Local has no remote_session_id — the key must be absent so a NULL is not
    # written over a real value, and the column stays clean for local rows.
    assert "sandbox_remote_session_id" not in updates


def test_on_sandbox_created_best_effort_repo_failure() -> None:
    # A repo failure must not propagate to the run path (mirrors _on_pid_registered).

    class _BoomRepo:
        def update_workflow(self, *a, **k) -> None:
            raise RuntimeError("db down")

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = _BoomRepo()
    orch._workflow_id = "wf-123"
    orch._on_sandbox_created("s1", "sb", "legacy_posix", None)  # no raise
