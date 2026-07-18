"""Round-2 review hardening tests for Feishu org-sync (PR #1806).

These tests address the round-2 review finding that the previous
``pg_try_advisory_xact_lock`` implementation was ineffective: the lock was
released as soon as ``Database.fetch_one`` committed and returned its pooled
connection, i.e. *before* the ``yield`` critical section began. They verify the
new session-level ``pg_advisory_lock``/``pg_advisory_unlock`` implementation
truly provides cross-process mutual exclusion by holding a dedicated
connection across the entire critical section.
"""

from __future__ import annotations

import threading

import pytest

from app.services.feishu_org_sync import FeishuOrgSyncService


class _FakeCursor:
    """Cursor whose ``execute`` records advisory-lock calls and returns a
    caller-configurable result via ``fetchone``.
    """

    def __init__(self, fake_conn, result=None):
        self._fake_conn = fake_conn
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql_norm = " ".join(str(sql).split())
        if "pg_try_advisory_lock" in sql_norm:
            self._fake_conn.lock_calls.append(("try", tuple(params or ())))
            self._result = (self._fake_conn.try_result,)
        elif "pg_advisory_unlock" in sql_norm:
            self._fake_conn.unlock_calls.append(("unlock", tuple(params or ())))
            self._result = (True,)
        else:
            self._fake_conn.other_calls.append((sql_norm, tuple(params or ())))

    def fetchone(self):
        return self._result


class _FakePgConnection:
    """A fake psycopg2-like connection that records advisory lock activity and
    whether it has been released back to the pool.
    """

    def __init__(self, try_result=True):
        self.try_result = try_result
        self.lock_calls: list = []
        self.unlock_calls: list = []
        self.other_calls: list = []
        self.commits = 0
        self.released = False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1


class _FakePostgresDatabase:
    """Fake Database that advertises itself as Postgres and hands out a single
    dedicated connection for the duration of a critical section, plus records
    when that connection is released back to the pool.
    """

    def __init__(self, try_result=True):
        self.is_postgresql = True
        self._conn = _FakePgConnection(try_result=try_result)

    def get_connection(self):
        return self._conn


def _fake_release(conn):
    """Replacement for release_postgresql_connection that records release."""
    conn.released = True


def _make_service(try_result=True):
    db = _FakePostgresDatabase(try_result=try_result)
    service = FeishuOrgSyncService.__new__(FeishuOrgSyncService)
    service.db = db
    return service, db


# Severe#1: The lock must be held across the entire yield critical section on a
# SINGLE dedicated connection; unlock must only happen AFTER the body runs, and
# the connection must not be released before then.
def test_sync_lock_holds_connection_across_critical_section(monkeypatch):
    service, db = _make_service(try_result=True)
    monkeypatch.setattr("app.services.feishu_org_sync.release_postgresql_connection", _fake_release)

    timeline = []

    with service._acquire_sync_lock():
        # While inside the critical section the advisory lock must have been
        # acquired on the dedicated connection...
        assert db._conn.lock_calls, "pg_try_advisory_lock must be called on entry"
        # ...the connection must still be alive (not released) ...
        assert not db._conn.released, "lock connection must NOT be released before yield"
        # ...and unlock must NOT have run yet (this is the bug being fixed:
        # the old transaction-level lock was already gone at this point).
        assert not db._conn.unlock_calls, "pg_advisory_unlock must not run before the body"
        timeline.append("body-ran")

    # After the critical section: unlock must run, then the connection released.
    assert timeline == ["body-ran"]
    assert db._conn.unlock_calls, "pg_advisory_unlock must run after the body"
    assert db._conn.released, "lock connection must be released after unlock"


# Severe#1: Mutual exclusion must actually block -- when the lock is already
# held by another worker (pg_try_advisory_lock returns False), the second
# caller must NOT enter the critical section and must raise.
def test_sync_lock_blocks_when_lock_taken(monkeypatch):
    service, db = _make_service(try_result=False)
    monkeypatch.setattr("app.services.feishu_org_sync.release_postgresql_connection", _fake_release)

    body_ran = False
    with pytest.raises(RuntimeError, match="already running"):
        with service._acquire_sync_lock():
            body_ran = True

    assert body_ran is False, "critical section must not run when lock unavailable"
    # Even on the failure path the connection must be cleaned up.
    assert db._conn.released, "lock connection must be released on failure path too"
    # No unlock should be issued for a lock we never acquired.
    assert not db._conn.unlock_calls


# Severe#1: Even if the body raises, the session-level lock must still be
# released (so a crash does not permanently wedge sync).
def test_sync_lock_unlocks_on_body_exception(monkeypatch):
    service, db = _make_service(try_result=True)
    monkeypatch.setattr("app.services.feishu_org_sync.release_postgresql_connection", _fake_release)

    with pytest.raises(ValueError, match="boom"):
        with service._acquire_sync_lock():
            raise ValueError("boom")

    assert db._conn.unlock_calls, "pg_advisory_unlock must run even if body raises"
    assert db._conn.released, "lock connection must be released after a body exception"


# Severe#1: Cross-process exclusion is the whole point. Simulate two separate
# processes (each with its own class-level threading.Lock so the in-process
# fence does not pre-serialize them) sharing one Postgres advisory lock state,
# and verify the second worker cannot run its body while the first holds it.
def test_sync_lock_provides_real_mutual_exclusion(monkeypatch):
    import app.services.feishu_org_sync as feishu_mod

    class _SharedServer:
        def __init__(self):
            self.holder_active = False

    server = _SharedServer()

    class _ConcurrentFakeDb(_FakePostgresDatabase):
        def get_connection(self):
            # Each worker gets its own connection, but try_lock consults the
            # shared server state: succeeds only if no one currently holds.
            conn = _FakePgConnection(try_result=not server.holder_active)
            return conn

    def _concurrent_release(conn):
        conn.released = True

    monkeypatch.setattr(feishu_mod, "release_postgresql_connection", _concurrent_release)

    # Distinct subclasses so each "process" has its own class-level threading
    # lock -- mirroring two separate worker processes. The only thing standing
    # between them must be the session-level advisory lock.
    class _Worker1Service(FeishuOrgSyncService):
        _sync_lock = threading.Lock()

    class _Worker2Service(FeishuOrgSyncService):
        _sync_lock = threading.Lock()

    worker2_entered = {"value": False}
    holder_ready = threading.Event()
    worker2_done = threading.Event()

    def worker1():
        db1 = _ConcurrentFakeDb()
        s1 = _Worker1Service.__new__(_Worker1Service)
        s1.db = db1
        with s1._acquire_sync_lock():
            server.holder_active = True
            holder_ready.set()  # signal worker2 it can now attempt
            worker2_done.wait(timeout=5)  # hold until worker2 finished trying
            server.holder_active = False

    def worker2():
        holder_ready.wait(timeout=5)  # only attempt once worker1 holds
        db2 = _ConcurrentFakeDb()
        s2 = _Worker2Service.__new__(_Worker2Service)
        s2.db = db2
        try:
            with s2._acquire_sync_lock():
                worker2_entered["value"] = True  # must NOT happen
        except RuntimeError:
            pass  # expected: refused because worker1 holds the lock
        finally:
            worker2_done.set()

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not worker2_entered["value"], (
        "second worker must be blocked from the critical section while the "
        "first holds the session-level advisory lock"
    )
