"""Integration tests for the CommandExecutionEvidence store (#2046 Phase A).

Exercises the real SQLite ``command_execution_evidence`` table via the
``tmp_db`` fixture (authoritative schema), asserting upsert idempotency,
tool_use/tool_result pairing, and missing-result handling.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.command_evidence import CommandExecutionEvidence
from app.modules.workspace.autonomous.command_evidence.recorder import (
    CommandEvidenceRecorder,
    compare_verdicts,
)
from app.repositories.command_evidence_repo import CommandExecutionEvidenceRepository


@pytest.fixture
def repo(tmp_db):
    return CommandExecutionEvidenceRepository(db=tmp_db)


@pytest.fixture
def recorder(repo):
    # Sync writer so record_* calls are durable immediately (no flush needed).
    return CommandEvidenceRecorder(repo=repo)


def test_tool_use_then_result_upserts_same_row(repo, recorder):
    recorder.record_tool_use(
        command_id="tu1",
        session_id="sess-1",
        workflow_id="wf-1",
        milestone_id="ms-1",
        tool_name="Bash",
        shell_command="pytest",
    )
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 1
    seeded_id = rows[0].id
    assert rows[0].exit_code is None
    assert rows[0].tool_name == "Bash"
    assert rows[0].shell_command == "pytest"

    recorder.record_tool_result(
        command_id="tu1", session_id="sess-1", exit_code=0, output_excerpt="3 passed"
    )
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 1  # upsert, not insert
    assert rows[0].id == seeded_id
    assert rows[0].exit_code == 0
    assert rows[0].terminal_reason == "completed"
    assert rows[0].output_excerpt == "3 passed"
    assert rows[0].stdout_digest is not None
    # tool_use identity fields preserved on the result upsert
    assert rows[0].tool_name == "Bash"
    assert rows[0].shell_command == "pytest"


def test_duplicate_execution_event_is_idempotent(repo, recorder):
    """#2046 acceptance: duplicate provider events must not duplicate rows."""
    for _ in range(3):
        recorder.record_tool_result(
            command_id="tu1", session_id="sess-2", exit_code=0, output_excerpt="ok"
        )
    assert len(repo.query_by_session("sess-2")) == 1


def test_command_id_pairs_with_unique_terminal_state(repo, recorder):
    """Each command_id is one evidence row with one terminal state."""
    for cid, code in (("a", 0), ("b", 1), ("c", 0)):
        recorder.record_tool_use(command_id=cid, session_id="sess-3", tool_name="Bash")
        recorder.record_tool_result(
            command_id=cid, session_id="sess-3", exit_code=code, output_excerpt="x"
        )
    rows = repo.query_by_session("sess-3")
    assert {r.command_id for r in rows} == {"a", "b", "c"}
    by_id = {r.command_id: r for r in rows}
    assert by_id["a"].exit_code == 0
    assert by_id["b"].exit_code == 1


def test_unpaired_tool_use_records_missing_result(repo, recorder):
    """A tool_use with no paired tool_result → missing_result terminal."""
    recorder.emit_from_event_log(
        session_id="sess-4",
        workflow_id="wf-4",
        event_log=[
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
            },
        ],
    )
    rows = repo.query_by_session("sess-4")
    assert len(rows) == 1
    assert rows[0].terminal_reason == "missing_result"
    assert rows[0].exit_code is None


def test_emit_from_event_log_pairs_tool_use_and_result(repo, recorder):
    recorder.emit_from_event_log(
        session_id="sess-5",
        workflow_id="wf-5",
        event_log=[
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
            },
            {"type": "tool_result", "tool_use_id": "tu1", "exit_code": 0, "text": "5 passed"},
        ],
    )
    rows = repo.query_by_session("sess-5")
    assert len(rows) == 1
    assert rows[0].exit_code == 0
    assert rows[0].terminal_reason == "completed"
    assert rows[0].output_excerpt == "5 passed"


def test_query_by_milestone(repo, recorder):
    recorder.record_tool_use(
        command_id="tu1",
        session_id="sess-6",
        workflow_id="wf-6",
        milestone_id="ms-6",
        tool_name="Bash",
    )
    rows = repo.query_by_milestone("wf-6", "ms-6")
    assert len(rows) == 1
    assert rows[0].milestone_id == "ms-6"


def test_shadow_comparison_reads_persisted_rows(repo, recorder):
    """The shadow path reads evidence from the store, not the event_log."""
    recorder.emit_from_event_log(
        session_id="sess-7",
        workflow_id="wf-7",
        event_log=[
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
            },
            {"type": "tool_result", "tool_use_id": "tu1", "exit_code": 1, "text": "1 failed"},
        ],
    )
    rows = repo.query_by_session("sess-7")
    # Heuristic said pass but evidence says failed → divergence (the #1967 case)
    result = compare_verdicts(heuristic_passed=True, evidence_rows=rows)
    assert result["divergence"] is True
    assert result["structured_verdict"] == "failed"


# ---------------------------------------------------------------------------
# Orchestrator-level shadow wiring
# ---------------------------------------------------------------------------


def _make_orchestrator_with_recorder(tmp_db, evidence_rows_by_session):
    """Build a minimal orchestrator whose shadow path uses a real recorder.

    ``evidence_rows_by_session`` pre-seeds the store so ``query_by_session``
    returns the rows the test wants without driving a full agent run. Also
    injects the recorder as the process-wide singleton so the orchestrator's
    lazy ``get_command_evidence_recorder()`` resolves to it. Caller must reset
    ``rec_mod._recorder_singleton = None`` after the test.
    """
    from unittest.mock import MagicMock

    import app.modules.workspace.autonomous.command_evidence.recorder as rec_mod
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    db_repo = CommandExecutionEvidenceRepository(db=tmp_db)
    for rows in evidence_rows_by_session.values():
        for row in rows:
            db_repo.upsert(row)

    recorder = CommandEvidenceRecorder(repo=db_repo)
    rec_mod._recorder_singleton = recorder

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-shadow"
    orch.repo = MagicMock()
    return orch


def test_orchestrator_shadow_creates_divergence_event(tmp_db):
    """Divergence (heuristic pass ≠ evidence failed) persists a workflow event."""
    import app.modules.workspace.autonomous.command_evidence.recorder as rec_mod
    from app.modules.workspace.autonomous.models import AgentTaskResult

    rows = [
        CommandExecutionEvidence(
            command_id="c1", session_id="sess-shadow", exit_code=1, terminal_reason="completed"
        ),
    ]
    orch = _make_orchestrator_with_recorder(tmp_db, {"sess-shadow": rows})
    try:
        result = AgentTaskResult(session_id="sess-shadow")
        orch._shadow_compare_evidence(
            test_result=result, milestone_id="ms-shadow", heuristic_passed=True
        )
        # create_event must have been called with the divergence event type.
        calls = orch.repo.create_event.call_args_list
        assert any(
            c.args[0].get("event_type") == "evidence_shadow_divergence" for c in calls
        ), calls
        # The persisted event_data records both verdicts.
        event_payload = calls[-1].args[0]
        assert event_payload["workflow_id"] == "wf-shadow"
        assert "heuristic_verdict" in event_payload["event_data"]
    finally:
        rec_mod._recorder_singleton = None


def test_orchestrator_shadow_creates_no_event_on_agreement(tmp_db):
    """When heuristic and structured verdicts agree, no event is written."""
    import app.modules.workspace.autonomous.command_evidence.recorder as rec_mod
    from app.modules.workspace.autonomous.models import AgentTaskResult

    rows = [
        CommandExecutionEvidence(
            command_id="c1", session_id="sess-agree", exit_code=0, terminal_reason="completed"
        ),
    ]
    orch = _make_orchestrator_with_recorder(tmp_db, {"sess-agree": rows})
    try:
        result = AgentTaskResult(session_id="sess-agree")
        orch._shadow_compare_evidence(
            test_result=result, milestone_id="ms-agree", heuristic_passed=True
        )
        orch.repo.create_event.assert_not_called()
    finally:
        rec_mod._recorder_singleton = None


def test_orchestrator_shadow_pass_without_evidence_creates_divergence(tmp_db):
    """Agent prose pass with no evidence rows → divergence event (#1967 core)."""
    import app.modules.workspace.autonomous.command_evidence.recorder as rec_mod
    from app.modules.workspace.autonomous.models import AgentTaskResult

    orch = _make_orchestrator_with_recorder(tmp_db, {})
    try:
        result = AgentTaskResult(session_id="sess-none")
        orch._shadow_compare_evidence(
            test_result=result, milestone_id="ms-none", heuristic_passed=True
        )
        calls = orch.repo.create_event.call_args_list
        assert any(
            c.args[0].get("event_type") == "evidence_shadow_divergence" for c in calls
        ), calls
    finally:
        rec_mod._recorder_singleton = None


@pytest.fixture
def pg_repo(pg_db):
    return CommandExecutionEvidenceRepository(db=pg_db)


@pytest.mark.postgres
def test_record_tool_use_persists_with_boolean_columns_on_postgres(pg_repo):
    """Regression: int params for boolean columns fail on Postgres
    (psycopg2 DatatypeMismatch). Without the fix this row never persisted —
    command_execution_evidence stayed empty and the #2046 shadow gate always
    returned NOT_RUN. This test reproduces it on real Postgres (skips when PG
    is unreachable, per the pg_db fixture)."""
    recorder = CommandEvidenceRecorder(repo=pg_repo)  # sync writer (durable immediately)
    recorder.record_tool_use(
        command_id="pg-bool-1",
        session_id="pg-bool-sess",
        workflow_id="w",
        milestone_id="m",
        tool_name="Bash",
        shell_command="pytest",
    )
    rows = pg_repo.query_by_session("pg-bool-sess")
    assert len(rows) == 1, "row did not persist — boolean INSERT failed on Postgres"
    assert rows[0].timed_out is False
    assert rows[0].cancelled is False
