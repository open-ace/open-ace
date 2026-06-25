"""
Open ACE - Run Timeline Module

Persisted provenance timeline for remote agent sessions: one durable run
record per session plus an append-only event stream and durable permission
approvals. The whole feature is self-contained so it can be removed or
re-implemented behind an external API with minimal scarring in existing code
(see plan §0, §2.1).
"""

from app.modules.workspace.run_timeline.models import AgentApproval, AgentRun, RunEvent
from app.modules.workspace.run_timeline.recorder import (
    DbRunRecorder,
    NullRunRecorder,
    RunRecorder,
    get_run_recorder,
)
from app.repositories.database import is_postgresql


def get_ddl_statements():
    """Return DDL statements for the run-timeline tables.

    Uses database-appropriate syntax based on is_postgresql().

    Note: The canonical schema definition lives in the Alembic migration:
    migrations/versions/<ts>_add_run_timeline_tables.py
    Changes to the schema must be reflected in BOTH places, and the generated
    snapshots schema/schema-postgres.sql + schema/schema-sqlite.sql must be
    regenerated via scripts/rebuild_schema_snapshots.py (they are committed
    artifacts, never hand-authored).
    """
    use_pg = is_postgresql()
    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    return [
        f"""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id {pk_type},
            run_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            user_id INTEGER,
            tenant_id INTEGER,
            machine_id TEXT,
            tool_name TEXT,
            provider TEXT,
            cli_tool TEXT,
            model TEXT,
            status TEXT DEFAULT 'active',
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            total_tokens INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_session_id ON agent_runs (session_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id ON agent_runs (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (status)",
        f"""
        CREATE TABLE IF NOT EXISTS agent_run_events (
            id {pk_type},
            run_id TEXT,
            session_id TEXT,
            event_type TEXT NOT NULL DEFAULT '',
            event_subtype TEXT,
            role TEXT,
            content TEXT,
            tool_name TEXT,
            provider TEXT,
            model TEXT,
            key_id TEXT,
            user_id INTEGER,
            tenant_id INTEGER,
            machine_id TEXT,
            metadata TEXT,
            event_ts TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # Composite index backs the per-session chronological query + cursor.
        "CREATE INDEX IF NOT EXISTS idx_run_events_session_id ON agent_run_events (session_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON agent_run_events (run_id)",
        "CREATE INDEX IF NOT EXISTS idx_run_events_event_type ON agent_run_events (event_type)",
        # created_at index reserved for the retention/prune job (phase 6).
        "CREATE INDEX IF NOT EXISTS idx_run_events_created_at ON agent_run_events (created_at)",
        f"""
        CREATE TABLE IF NOT EXISTS agent_approvals (
            id {pk_type},
            request_id TEXT NOT NULL UNIQUE,
            run_id TEXT,
            session_id TEXT,
            tool_name TEXT,
            request_subtype TEXT,
            request_details TEXT,
            status TEXT DEFAULT 'pending',
            decision TEXT,
            decided_by INTEGER,
            decided_by_name TEXT,
            decision_metadata TEXT,
            requested_at TIMESTAMP,
            decided_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_agent_approvals_session_id ON agent_approvals (session_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_approvals_run_id ON agent_approvals (run_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_approvals_status ON agent_approvals (status)",
    ]


__all__ = [
    "AgentRun",
    "RunEvent",
    "AgentApproval",
    "RunRecorder",
    "DbRunRecorder",
    "NullRunRecorder",
    "get_run_recorder",
    "get_ddl_statements",
]
