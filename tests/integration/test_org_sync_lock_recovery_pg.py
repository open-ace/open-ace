"""Real-PostgreSQL self-heal tests for org-sync advisory locks (Issue #1827).

These exercise the shared ``_org_sync_lock`` helpers against a live Postgres so
the cross-process self-heal story (WP-4/#5) is verified end-to-end:

  * a held advisory lock is observable via ``get_running_sync_state``;
  * ``split_advisory_key`` correctly addresses the ``pg_locks`` row (the v2-review
    bug: a single-argument bigint lock is split high/low-32 into classid/objid,
    so a key > 2**32 has classid != 0);
  * ``force_release_lock`` terminates the holder backend and polls until the lock
    row is gone, after which another backend can acquire the lock.

Marked ``postgres`` and skipped automatically (via the ``pg_db`` fixture) when no
live PostgreSQL server is reachable -- so this is safe to run in environments
without a DB.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.postgres


def _dingtalk_sync_lock_key():
    from app.services.dingtalk_org_sync import DingTalkOrgSyncService

    return DingTalkOrgSyncService._DB_SYNC_LOCK_KEY


def test_running_sync_state_is_none_when_lock_free(pg_db):
    """When nothing holds the advisory lock, get_running_sync_state returns None."""
    from app.services._org_sync_lock import get_running_sync_state

    # Use a key nothing else touches in this test DB.
    assert get_running_sync_state(pg_db, 9876543210987654) is None


def test_held_advisory_lock_is_observable_and_split_key_addresses_pg_locks(pg_db):
    """A held lock is found by get_running_sync_state, and the split (classid,
    objid) actually matches the pg_locks row -- proving the v2-review fix."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from app.services._org_sync_lock import get_running_sync_state, split_advisory_key

    key = _dingtalk_sync_lock_key()
    hi, lo = split_advisory_key(key)

    holder = psycopg2.connect(pg_db.db_url)
    holder.autocommit = True
    try:
        cur = holder.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        assert cur.fetchone()["pg_try_advisory_lock"] is True
        holder_pid = holder.get_backend_pid()

        # The split halves must address the real pg_locks row for this key.
        lock_row = pg_db.fetch_one(
            """
            SELECT classid, objid, pid
            FROM pg_locks
            WHERE locktype = 'advisory' AND classid = ? AND objid = ?
            """,
            (hi, lo),
        )
        assert lock_row is not None, "pg_locks must record the held advisory lock"
        assert int(lock_row["classid"]) == hi
        assert int(lock_row["objid"]) == lo
        assert int(lock_row["pid"]) == holder_pid

        state = get_running_sync_state(pg_db, key)
        assert state is not None
        assert state["pid"] == holder_pid
        assert state["hold_seconds"] >= 0
    finally:
        try:
            holder.cursor().execute("SELECT pg_advisory_unlock(%s)", (key,))
        except psycopg2.Error:
            pass
        holder.close()


def test_force_release_lock_terminates_holder_and_frees_lock(pg_db):
    """force_release_lock must kill the holder backend and poll until the lock is
    gone, after which another backend can acquire it."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from app.services._org_sync_lock import force_release_lock, get_running_sync_state

    key = _dingtalk_sync_lock_key()

    holder = psycopg2.connect(pg_db.db_url)
    holder.autocommit = True
    try:
        cur = holder.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        assert cur.fetchone()["pg_try_advisory_lock"] is True

        assert get_running_sync_state(pg_db, key) is not None

        released = force_release_lock(pg_db, key, poll_attempts=60, poll_interval=0.1)
        assert released is True, "force_release_lock must report the lock released"

        # The holder backend is dead; the lock row is gone.
        assert get_running_sync_state(pg_db, key) is None

        # Another backend can now acquire the same lock.
        reacquired = pg_db.fetch_one("SELECT pg_try_advisory_lock(%s) AS ok", (key,))
        assert reacquired is not None and reacquired["ok"] is True
        # Clean up the lock we just took so it doesn't leak across tests.
        pg_db.execute("SELECT pg_advisory_unlock(%s)", (key,))
    finally:
        try:
            holder.close()
        except psycopg2.Error:
            pass
