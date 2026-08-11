"""Regression: timeline event emission must survive datetime in the payload.

Workflow patches can carry raw DB timestamp fields (e.g. ``verification_started_at``
is a ``timestamp with time zone`` column that psycopg2 returns as ``datetime``).
When such a patch is emitted as a timeline event, ``json.dumps`` used to raise
``TypeError: Object of type datetime is not JSON serializable`` and fail the
whole phase — caught on b48179df at acceptance_verification (PR #2465 merged
fine; only the post-merge verification event crashed).
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2480)]


def _make_orch():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator("wf-test")
    orch.repo = MagicMock()
    orch.repo.create_event.return_value = {"id": 1}
    orch.emitter = MagicMock()
    return orch


def test_emit_serializes_datetime_in_event_data():
    """_emit must not raise when event data carries a raw datetime."""
    orch = _make_orch()
    data = {
        "status": "confirmed",
        "verification_started_at": datetime(2026, 8, 10, 5, 13, tzinfo=timezone.utc),
    }

    orch._emit("status_change", data)

    orch.repo.create_event.assert_called_once()
    event_data = orch.repo.create_event.call_args[0][0]["event_data"]
    parsed = json.loads(event_data)  # round-trips cleanly into valid JSON
    assert parsed["status"] == "confirmed"
    assert "2026" in parsed["verification_started_at"]


def test_emit_serializes_nested_datetime():
    """A datetime nested inside the payload must also survive (defense in depth)."""
    orch = _make_orch()
    data = {"patch": {"completed_at": datetime(2026, 8, 10, 5, 13, tzinfo=timezone.utc)}}

    orch._emit("patch_applied", data)

    event_data = orch.repo.create_event.call_args[0][0]["event_data"]
    parsed = json.loads(event_data)
    assert "2026" in parsed["patch"]["completed_at"]
