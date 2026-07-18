"""Regression tests for the event-index read-modify-write race in
``RemoteAgentManager._persist_output`` (PR #1790).

Two concurrent producers buffering output for one session both read the same
``MAX(event_index) + 1`` and the second INSERT violates
``uq_remote_runtime_outputs_session_index UNIQUE (session_id, event_index)``.
On current main the resulting IntegrityError is swallowed at debug level and the
output chunk is silently dropped.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

import pytest

from app.modules.workspace import remote_agent_manager as ram_mod
from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.repositories.schema_init import load_schema_from_file


class _UniqueViolationError(Exception):
    """Stand-in for a psycopg2 UniqueViolation that the production code must retry on."""

    pgcode = "23505"  # matches psycopg2.errors.UniqueViolation.pgcode


class _MockCursor:
    """Cursor that deterministically reproduces the race.

    The first two concurrent ``SELECT MAX`` calls both return the *same* stale
    value, and the INSERT that follows raises a uniqueness violation when it
    tries to reuse an already-allocated index. This mirrors what Postgres does
    under two overlapping producers without any row lock, and does NOT depend on
    SQLite's whole-DB write lock (which would incidentally hide the bug).
    """

    def __init__(self, taken_indices: set[int], max_index: int = 0) -> None:
        self._taken = taken_indices
        self._max = max_index

    def execute(self, sql: str, params=()):  # noqa: D401
        upper = sql.strip().upper()
        if upper.startswith("SELECT") and "MAX(EVENT_INDEX)" in upper:
            # Stale read: every caller sees the same next index.
            self._max = self._max  # noqa: B018 - intentionally not advanced
            self._result = [{"next_index": self._max + 1}]
        elif upper.startswith("INSERT INTO REMOTE_RUNTIME_OUTPUTS"):
            # params = (session_id, event_index, stream, payload, created_at, expires_at)
            event_index = int(params[1])
            if event_index in self._taken:
                # Collision: another producer already holds this index, so the
                # committed MAX has advanced past it. Reflect that so a re-read
                # yields a fresh index (mirrors the real DB after a retry).
                self._max = max(self._max, event_index)
                raise _UniqueViolationError(f"duplicate (session_id, event_index={event_index})")
            self._taken.add(event_index)
            self._max = max(self._max, event_index)
            self._result = []
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def close(self):  # noqa: D401
        return None


class _MockConnection:
    def __init__(self, cursor: _MockCursor) -> None:
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _MockDatabase:
    """Database stub whose ``connection()`` yields a shared non-serializing cursor."""

    def __init__(self) -> None:
        self.cursor = _MockCursor(taken_indices=set(), max_index=0)

    @contextmanager
    def connection(self):
        yield _MockConnection(self.cursor)


def _make_manager(monkeypatch) -> RemoteAgentManager:
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    # Real on-disk DB just so RemoteAgentManager construction succeeds; _persist_output
    # is the only path exercised here, and we redirect it to the mock below.
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "runtime.db"
    load_schema_from_file(db_url=f"sqlite:///{tmp}", dialect="sqlite")
    mgr = RemoteAgentManager(db_path=str(tmp))
    mgr.db = _MockDatabase()
    return mgr


def test_persist_output_does_not_drop_event_on_unique_violation(monkeypatch):
    """A uniqueness collision must be retried, never silently dropped.

    The mock cursor hands the SAME next_index to two sequential _persist_output
    calls and raises a uniqueness violation on the colliding INSERT. After the
    fix, _persist_output must re-read MAX+1 and succeed; on buggy main it swallows
    the error and the event is lost.
    """
    mgr = _make_manager(monkeypatch)
    mgr.buffer_output("session-race", {"stream": "stdout", "data": "first"})
    # Force the second call to collide with the first's index (1) before the retry.
    # _make_manager starts max_index=0 so first call took index 1; reset the cursor's
    # max to 0 so the second call also computes next_index=1 and collides.
    mgr.db.cursor._max = 0  # type: ignore[attr-defined]

    # After the fix this must NOT raise and must persist successfully.
    mgr.buffer_output("session-race", {"stream": "stdout", "data": "second"})

    taken = mgr.db.cursor._taken  # type: ignore[attr-defined]
    assert {1, 2} == taken, f"expected both indices persisted, got {sorted(taken)}"


def test_persist_output_propagates_unexpected_errors(monkeypatch):
    """Truly unexpected errors must not be swallowed at debug level.

    A non-uniqueness failure (e.g. the DB disappearing) should surface rather than
    be silently logged. On buggy main ``except Exception`` swallows everything.
    """

    class _ExplodingCursor(_MockCursor):
        def execute(self, sql, params=()):
            raise RuntimeError("connection lost")

    mgr = _make_manager(monkeypatch)
    mgr.db.cursor = _ExplodingCursor(taken_indices=set())  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError):
        mgr.buffer_output("session-race", {"stream": "stdout", "data": "boom"})


# ---------------------------------------------------------------------------
# Secondary test: real SQLite concurrency (incidentally serialized, but a guard).
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    db_path = tmp_path / "remote_runtime.db"
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
    return db_path


def test_concurrent_buffer_output_assigns_distinct_event_indices(runtime_db):
    """Two producers buffering output for one session must each get distinct
    event_index values; no output event may be silently dropped."""
    db_path = runtime_db
    pod_a = RemoteAgentManager(db_path=str(db_path))
    pod_b = RemoteAgentManager(db_path=str(db_path))

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
