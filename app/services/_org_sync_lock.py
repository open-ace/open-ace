"""Shared helpers for org-sync advisory-lock self-healing (Issue #1827).

PostgreSQL session-level advisory locks (``pg_try_advisory_lock(bigint)``) guard
org sync (Feishu / DingTalk) against concurrent runs across gunicorn workers. A
*hung* sync holds its advisory lock indefinitely, which would wedge every future
sync. These helpers let an admin endpoint (or an optional watchdog) locate the
holder pid via ``pg_locks`` and forcibly release it with
``pg_terminate_backend``, and let the scheduler detect how long a sync has been
running so it can warn (or, opt-in, recover).

Key detail (the v2 review bug): a single-argument ``bigint`` advisory lock is
recorded in ``pg_locks`` split across two 32-bit columns -- the high 32 bits in
``classid`` and the low 32 bits in ``objid``. A key larger than ``2**32`` (all of
our sync-lock keys are) therefore has ``classid != 0``; querying
``classid = 0 AND objid = <full key>`` matches nothing. Use
:func:`split_advisory_key` to compute the two halves.

These functions are Postgres-only; on SQLite (single-process deployments) they
return empty/false so callers fall back to in-process state.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def split_advisory_key(key: int) -> tuple[int, int]:
    """Split a bigint advisory-lock key into its ``pg_locks`` ``(classid, objid)``.

    ``pg_try_advisory_lock(bigint)`` records the 64-bit key with the high 32
    bits in ``classid`` and the low 32 bits in ``objid``. For any key > 2**32
    (all org-sync keys) ``classid`` is therefore non-zero.
    """
    key = int(key)
    return (key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF


def _is_pg(db: Any) -> bool:
    return bool(getattr(db, "is_postgresql", False))


def get_running_sync_state(db: Any, key: int) -> dict[str, Any] | None:
    """Return ``{"pid", "hold_seconds"}`` for the backend holding ``key``, or None.

    Cross-process: reads shared ``pg_locks`` / ``pg_stat_activity``. ``hold_seconds``
    is approximated from the holder backend's ``state_change`` (the dedicated
    lock-holding connection sits idle for the whole critical section, so its
    ``state_change`` is ~when the sync started). Postgres only; None elsewhere.
    """
    if not _is_pg(db):
        return None
    hi, lo = split_advisory_key(key)
    row = db.fetch_one(
        """
        SELECT l.pid AS pid,
               EXTRACT(EPOCH FROM (now() - COALESCE(s.state_change, now()))) AS hold_seconds
        FROM pg_locks l
        LEFT JOIN pg_stat_activity s ON s.pid = l.pid
        WHERE l.locktype = 'advisory'
          AND l.classid = ?
          AND l.objid = ?
        """,
        (hi, lo),
    )
    if not row or row.get("pid") is None:
        return None
    hold = row.get("hold_seconds")
    try:
        hold_seconds = float(hold) if hold is not None else 0.0
    except (TypeError, ValueError):
        hold_seconds = 0.0
    return {"pid": int(row["pid"]), "hold_seconds": hold_seconds}


def current_backend_pid(db: Any) -> int | None:
    """Return this connection's backend pid on Postgres, else None."""
    if not _is_pg(db):
        return None
    row = db.fetch_one("SELECT pg_backend_pid() AS pid")
    if not row:
        return None
    pid = row.get("pid")
    return int(pid) if pid is not None else None


def find_lock_holder_pid(db: Any, key: int, exclude_pid: int | None = None) -> int | None:
    """Return the pid holding advisory lock ``key`` (excluding ``exclude_pid``)."""
    if not _is_pg(db):
        return None
    hi, lo = split_advisory_key(key)
    params: list[Any] = [hi, lo]
    exclude_clause = ""
    if exclude_pid is not None:
        exclude_clause = " AND l.pid <> ?"
        params.append(int(exclude_pid))
    row = db.fetch_one(
        f"""
        SELECT l.pid AS pid
        FROM pg_locks l
        WHERE l.locktype = 'advisory'
          AND l.classid = ?
          AND l.objid = ?
          {exclude_clause}
        """,
        tuple(params),
    )
    if not row or row.get("pid") is None:
        return None
    return int(row["pid"])


def force_release_lock(
    db: Any,
    key: int,
    exclude_pid: int | None = None,
    poll_attempts: int = 20,
    poll_interval: float = 0.25,
    sleep=time.sleep,
) -> bool:
    """Forcefully release advisory lock ``key`` by terminating its holder.

    Locates the holder pid via :func:`find_lock_holder_pid`, issues
    ``pg_terminate_backend``, then polls ``pg_locks`` until the lock row is gone
    (``pg_terminate_backend`` is asynchronous -- the holder only dies once it
    reaches a cancel point). Returns True if the lock is gone on return.

    ``exclude_pid`` defaults to the current backend so a worker never terminates
    itself. Postgres only; returns False on other backends.

    ``sleep`` is injectable so tests can avoid real waiting.
    """
    if not _is_pg(db):
        return False
    if exclude_pid is None:
        exclude_pid = current_backend_pid(db)
    pid = find_lock_holder_pid(db, key, exclude_pid=exclude_pid)
    if pid is None:
        # Nothing currently holds the lock (for this key, excluding self).
        return True
    try:
        db.execute("SELECT pg_terminate_backend(?)", (pid,))
    except Exception:
        logger.warning(
            "pg_terminate_backend(%s) failed for advisory key=%s", pid, key, exc_info=True
        )
        return False
    # pg_terminate_backend is async: wait until the holder actually releases.
    for _ in range(max(1, poll_attempts)):
        if find_lock_holder_pid(db, key, exclude_pid=exclude_pid) is None:
            return True
        sleep(poll_interval)
    logger.warning("advisory lock key=%s still held by pid=%s after terminate+poll", key, pid)
    return False
