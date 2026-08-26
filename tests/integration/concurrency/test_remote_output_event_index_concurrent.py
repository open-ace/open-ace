"""Real-thread guard for the event-index race (PR #1790).

Two producers on two manager instances sharing one SQLite runtime DB buffer
output concurrently; the UNIQUE (session_id, event_index) constraint plus the
re-read-on-collision retry must assign every event a distinct index with no
gaps. SQLite incidentally serializes writers behind its whole-DB write lock —
this is a persistence-semantics guard (no lost events, contiguous indices),
not the deterministic race reproduction (that lives in
``tests/unit/test_remote_output_event_index_race.py``).
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

import app.modules.workspace.remote_agent_manager as ram_mod
from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.repositories import database as db_mod
from app.repositories.schema_init import load_schema_from_file

pytestmark = [pytest.mark.regression, pytest.mark.issue(1790)]


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    monkeypatch.setattr(RemoteAgentManager, "_start_retention_cleanup", lambda self: None)
    monkeypatch.setattr(RemoteAgentManager, "_start_pending_revoke_cleanup", lambda self: None)
    db_path = tmp_path / "remote_runtime.db"
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
    return db_path


def test_concurrent_buffer_output_assigns_distinct_event_indices(runtime_db):
    """Two producers buffering output for one session must each get distinct
    event_index values; no output event may be silently dropped."""
    db_path = runtime_db
    pod_a = RemoteAgentManager(db_path=str(db_path))
    pod_b = RemoteAgentManager(db_path=str(db_path))

    # Set batch size to 1 so every buffer_output triggers immediate flush
    # This matches the expected behavior from the original test
    pod_a.OUTPUT_BATCH_SIZE = 1
    pod_b.OUTPUT_BATCH_SIZE = 1

    n = 200
    barrier = threading.Barrier(2)

    def produce(pod, label):
        barrier.wait()  # maximize overlap
        for i in range(n):
            pod.buffer_output("session-race", {"stream": "stdout", "data": f"{label}-{i}"})

    t1 = threading.Thread(target=produce, args=(pod_a, "a"))
    t2 = threading.Thread(target=produce, args=(pod_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    conn = sqlite3.connect(f"file:{db_path}", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_index FROM remote_runtime_outputs WHERE session_id=? " "ORDER BY event_index",
        ("session-race",),
    ).fetchall()
    conn.close()
    indices = [r["event_index"] for r in rows]

    assert (
        len(indices) == 2 * n
    ), f"lost {2 * n - len(indices)} output events to swallowed IntegrityError"
    assert indices == list(range(1, 2 * n + 1)), "event_index collision or gap"
