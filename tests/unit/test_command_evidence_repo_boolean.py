"""Regression: boolean columns must be passed as Python bool, not int.

Postgres boolean columns reject integers (psycopg2 DatatypeMismatch); SQLite
accepts them, so CI on SQLite masked this and the command-evidence dual-write
silently never persisted any rows on Postgres (#2046 Phase A shadow gate)."""

from datetime import datetime, timezone

from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence
from app.repositories.command_evidence_repo import CommandExecutionEvidenceRepository


def _evidence() -> CommandExecutionEvidence:
    return CommandExecutionEvidence(
        command_id="c1",
        session_id="s1",
        workflow_id="w1",
        milestone_id="m1",
        tool_name="Bash",
        shell_command="pytest",
        tenant_id=1,
        timed_out=True,
        cancelled=False,
    )


def test_insert_params_pass_bool_for_boolean_columns():
    params = CommandExecutionEvidenceRepository._insert_params(
        _evidence(), None, datetime.now(timezone.utc)
    )
    # Position 15 = timed_out, 16 = cancelled (see _insert_params column order).
    assert isinstance(params[15], bool), f"timed_out must be bool, got {type(params[15]).__name__}"
    assert isinstance(params[16], bool), f"cancelled must be bool, got {type(params[16]).__name__}"


def test_update_params_pass_bool_for_boolean_columns():
    params = CommandExecutionEvidenceRepository._update_params(
        _evidence(), None, datetime.now(timezone.utc)
    )
    # Position 2 = timed_out, 3 = cancelled (see _update_params SET order).
    assert isinstance(params[2], bool), f"timed_out must be bool, got {type(params[2]).__name__}"
    assert isinstance(params[3], bool), f"cancelled must be bool, got {type(params[3]).__name__}"
