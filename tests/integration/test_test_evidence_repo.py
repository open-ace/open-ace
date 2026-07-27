"""Persistence contract tests for TestExecutionEvidence (#2046 Phase B).

Exercises the real SQLite ``test_execution_evidence`` table via the ``tmp_db``
fixture (authoritative schema), mirroring ``test_command_evidence_repo``:
upsert idempotency by ``(session_id, command_id)``, JSON selectors/coverage
scope round-trip, and query ordering.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    ParserConfidence,
    TestExecutionEvidence,
    parse_test_evidence,
)
from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
)
from app.repositories.test_evidence_repo import TestExecutionEvidenceRepository


@pytest.fixture
def repo(tmp_db):
    return TestExecutionEvidenceRepository(db=tmp_db)


def _te(command_id: str = "c1", **kwargs) -> TestExecutionEvidence:
    base = {
        "command_id": command_id,
        "session_id": "sess-1",
        "workflow_id": "wf-1",
        "milestone_id": "ms-1",
        "framework": "python",
        "passed": 3,
        "failed": None,
        "verdict": ExecutionVerdict.PASSED.value,
        "parser": "pytest",
        "parser_confidence": ParserConfidence.HIGH.value,
        "selectors": ["tests/a.py"],
        "coverage_scope": {"context": "python -m pytest", "selectors": ["tests/a.py"]},
    }
    base.update(kwargs)
    return TestExecutionEvidence(**base)


def test_upsert_insert_then_update_same_row(repo):
    row_id = repo.upsert(_te("c1"))
    assert row_id is not None
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 1
    assert rows[0].passed == 3

    # Re-upsert with a different verdict → overwrites in place, no new row.
    repo.upsert(_te("c1", passed=2, failed=1, verdict=ExecutionVerdict.FAILED.value))
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 1
    assert rows[0].failed == 1
    assert rows[0].verdict == ExecutionVerdict.FAILED.value


def test_upsert_distinct_commands_are_separate_rows(repo):
    repo.upsert(_te("c1"))
    repo.upsert(_te("c2", failed=1, passed=2, verdict=ExecutionVerdict.FAILED.value))
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 2


def test_query_by_session_returns_id_order(repo):
    repo.upsert(_te("c1"))
    repo.upsert(_te("c2"))
    repo.upsert(_te("c3"))
    rows = repo.query_by_session("sess-1")
    assert [r.command_id for r in rows] == ["c1", "c2", "c3"]


def test_query_by_milestone_filters(repo):
    repo.upsert(_te("c1", milestone_id="ms-1"))
    repo.upsert(_te("c2", milestone_id="ms-2"))
    rows = repo.query_by_milestone("wf-1", "ms-1")
    assert len(rows) == 1
    assert rows[0].command_id == "c1"


def test_from_row_decodes_json_selectors_and_scope(repo):
    repo.upsert(
        _te(
            "c1",
            selectors=["tests/a.py", "tests/b.py"],
            coverage_scope={
                "context": "python -m pytest",
                "selectors": ["tests/a.py", "tests/b.py"],
            },
        )
    )
    rows = repo.query_by_session("sess-1")
    assert rows[0].selectors == ["tests/a.py", "tests/b.py"]
    assert rows[0].coverage_scope["context"] == "python -m pytest"
    assert set(rows[0].coverage_scope["selectors"]) == {"tests/a.py", "tests/b.py"}


def test_parse_then_upsert_roundtrip(repo):
    ce = CommandExecutionEvidence(
        command_id="c1",
        session_id="sess-1",
        workflow_id="wf-1",
        milestone_id="ms-1",
        tool_name="Bash",
        shell_command="pytest tests/x.py",
        exit_code=0,
        output_excerpt="3 passed",
    )
    te = parse_test_evidence(ce, framework_hint="python")
    repo.upsert(te)
    rows = repo.query_by_session("sess-1")
    assert len(rows) == 1
    assert rows[0].verdict == ExecutionVerdict.PASSED.value
    assert rows[0].passed == 3
    assert rows[0].parser == "pytest"
    assert rows[0].parser_confidence == ParserConfidence.HIGH.value


def test_command_execution_id_persisted(repo):
    te = _te("c1")
    te.command_execution_id = 42
    repo.upsert(te)
    rows = repo.query_by_session("sess-1")
    assert rows[0].command_execution_id == 42


def test_to_dict_serializes_for_metadata():
    te = _te("c1")
    data = te.to_dict()
    assert data["command_id"] == "c1"
    assert data["verdict"] == ExecutionVerdict.PASSED.value
    assert data["coverage_scope"]["context"] == "python -m pytest"
