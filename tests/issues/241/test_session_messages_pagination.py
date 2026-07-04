#!/usr/bin/env python3
"""
Tests for Issue #241 (#22): composite-key keyset pagination of session messages.

These exercise ``SessionManager.get_messages_page`` / ``count_messages`` — the
core pagination primitive — against a temporary SQLite database backed by the
authoritative schema (schema-sqlite.sql, which this change made timestamp
NOT NULL and added the composite index to).

Coverage mandated by the adversarial review:
  - cross-page continuity (no overlap, no gap, total order)
  - DESC-fetch-then-reverse returns the page *adjacent* to the cursor, not the
    globally-oldest rows (the bug ``WHERE id<cursor ORDER BY ts ASC`` had)
  - identical-timestamp rows broken by the ``id`` tiebreaker
  - NULL timestamps land in the most-recent page (NULLS FIRST safety net for
    pre-migration rows), never stranded past the cursor
  - limit clamping to [1, MAX]
  - milestone-aware conditional COUNT
  - empty session / default page
  - internal callers (get_session(include_messages=True)) still load the FULL
    history unchanged
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

# Make the project root importable when run directly.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import app.modules.workspace.session_manager as sm_module  # noqa: E402
from app.modules.workspace.session_manager import SessionManager  # noqa: E402


@pytest.fixture
def manager(monkeypatch):
    """A SessionManager backed by an isolated temp SQLite DB with real schema.

    The auto-dev sandbox defaults to PostgreSQL; we force SQLite here so each
    test gets a throwaway database and never touches shared state. We patch
    ``is_postgresql`` on the session_manager module (the same symbol
    ``_get_connection`` and ``_ensure_tables`` read), so both connection routing
    and schema loading agree on the dialect.
    """
    monkeypatch.setattr(sm_module, "is_postgresql", lambda *a, **k: False)

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    mgr = SessionManager(db_path=path)
    # Load the authoritative schema (creates session_messages with the new
    # NOT NULL timestamp + composite index, agent_sessions, etc.).
    mgr._ensure_tables()
    yield mgr
    # Cleanup: close any pooled handles and remove the file.
    try:
        os.unlink(path)
    except OSError:
        pass


def _create_session(mgr, session_id="sess-pagination", user_id=1):
    """Create a session and return its session_id string."""
    session = mgr.create_session(
        tool_name="qwen",
        user_id=user_id,
        session_type="chat",
        title="pagination test",
        session_id=session_id,
    )
    return session.session_id


def _add(mgr, session_id, ts, role="user", content=None, milestone_id="", count_usage=False):
    """Add one message with an explicit source timestamp (naive UTC datetime)."""
    return mgr.add_message(
        session_id,
        role=role,
        content=content or f"msg @{ts.isoformat()}",
        tokens_used=10,
        model="glm-5",
        milestone_id=milestone_id,
        count_usage=count_usage,
        timestamp=ts,
    )


def _ts(minute):
    """Deterministic naive-UTC timestamp varying by whole minute (0..59)."""
    return datetime(2026, 7, 4, 12, minute)


def _ts_seq(n):
    """Distinct naive-UTC timestamp for index n (>59 supported via minute+second)."""
    return datetime(2026, 7, 4, 12, n // 60, n % 60)


# ============================ Cross-page continuity ============================


class TestCrossPageContinuity:
    """Walking every page via the cursor must reconstruct the full history
    exactly once, in order, with no overlaps or gaps."""

    def test_full_walk_no_overlap_no_gap(self, manager):
        sid = _create_session(manager)
        # 10 messages, strictly increasing (timestamp, id).
        for m in range(10):
            _add(manager, sid, _ts(m))

        collected = []
        cursor = None
        pages = 0
        while True:
            page = manager.get_messages_page(
                sid,
                limit=3,
                before_timestamp=cursor["timestamp"] if cursor else None,
                before_id=cursor["id"] if cursor else None,
            )
            collected.extend(page["messages"])
            pages += 1
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
            assert cursor is not None, "has_more without a cursor would loop forever"

        # Every message seen exactly once (union == full set, no dupes).
        # Pages are appended newest-page-first, each internally oldest->newest,
        # so the collected sequence is NOT globally ascending — continuity is
        # proven by the union/dupe check, not by insertion order.
        ids = [msg.id for msg in collected]
        assert len(ids) == len(set(ids)), "duplicate message across pages"
        assert len(collected) == 10
        assert sorted(ids) == list(range(1, 11))
        # Reasonable page count (10 / 3 => 4 pages).
        assert pages == 4

    def test_has_more_flag_and_cursor_only_when_older_page_exists(self, manager):
        sid = _create_session(manager)
        for m in range(5):
            _add(manager, sid, _ts(m))

        # Full page of 5 fits exactly -> no older page.
        page = manager.get_messages_page(sid, limit=5)
        assert page["has_more"] is False
        assert page["next_cursor"] is None

        # Asking for fewer than total -> has_more, cursor points at oldest kept.
        page = manager.get_messages_page(sid, limit=3)
        assert page["has_more"] is True
        assert page["next_cursor"] is not None
        # Cursor is the oldest of the retained (newest 3 => ts minute 2).
        assert page["next_cursor"]["id"] == page["messages"][0].id


# ===================== DESC+reverse adjacency (the bug) =======================


class TestDescReverseAdjacency:
    """The page requested with a cursor must be the one *immediately older*,
    not the globally-oldest rows. This is the regression that the naive
    ``WHERE id < cursor ORDER BY timestamp ASC LIMIT n`` would produce."""

    def test_second_page_is_adjacent_not_oldest(self, manager):
        sid = _create_session(manager)
        for m in range(10):  # minutes 0..9
            _add(manager, sid, _ts(m))

        # Most-recent page (limit 3) => minutes 7, 8, 9 ascending.
        page1 = manager.get_messages_page(sid, limit=3)
        mins1 = [m.timestamp.minute for m in page1["messages"]]
        assert mins1 == [7, 8, 9]
        assert page1["next_cursor"]["timestamp"] == _ts(7).isoformat()

        # Next older page must be minutes 4, 5, 6 — adjacent — NOT 0, 1, 2.
        page2 = manager.get_messages_page(
            sid,
            limit=3,
            before_timestamp=page1["next_cursor"]["timestamp"],
            before_id=page1["next_cursor"]["id"],
        )
        mins2 = [m.timestamp.minute for m in page2["messages"]]
        assert mins2 == [4, 5, 6], f"expected the page adjacent to the cursor (4,5,6), got {mins2}"


# ========================== Same-timestamp tiebreak ===========================


class TestSameTimestampTiebreak:
    """When timestamps collide, ``id`` is the tiebreaker so the (timestamp, id)
    order stays total and the cursor splits the collision cleanly."""

    def test_identical_timestamps_ordered_by_id(self, manager):
        sid = _create_session(manager)
        same = _ts(5)
        # 6 messages sharing one timestamp; ids are assigned 1..6 in insert order.
        for i in range(6):
            _add(manager, sid, same, content=f"collision {i}")

        page1 = manager.get_messages_page(sid, limit=3)
        ids1 = [m.id for m in page1["messages"]]
        # Oldest-first within the collision => ascending id.
        assert ids1 == sorted(ids1)
        # Cursor is the smallest retained (timestamp, id) — id of the 4th-oldest.
        assert page1["next_cursor"]["timestamp"] == same.isoformat()

        page2 = manager.get_messages_page(
            sid,
            limit=3,
            before_timestamp=page1["next_cursor"]["timestamp"],
            before_id=page1["next_cursor"]["id"],
        )
        ids2 = [m.id for m in page2["messages"]]

        # No overlap across the collision boundary.
        assert set(ids1).isdisjoint(set(ids2))
        assert len(ids1) + len(ids2) == 6
        # Everything below the cursor id (older) is in page2.
        assert max(ids2) < min(ids1)


# ======================== NULL-timestamp safety net ===========================


class TestNullTimestampSafetyNet:
    """The ``timestamp`` column is NOT NULL at the schema level (the primary
    defense, enforced by this change + the backfill migration), AND the query
    orders NULLS FIRST so any pre-migration NULL row could never be stranded
    past the cursor. We assert both layers."""

    def test_schema_rejects_null_timestamp(self, manager):
        """The authoritative schema must forbid NULL timestamps."""
        sid = _create_session(manager)
        conn = sqlite3.connect(manager.db_path)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_messages (session_id, role, content, timestamp) "
                "VALUES (?, 'user', 'null ts row', NULL)",
                (sid,),
            )
        conn.close()

    def test_order_clause_puts_nulls_first(self, monkeypatch):
        """The defensive ORDER BY must surface NULLs in the most-recent page."""
        # SQLite branch: IS NULL boolean sorts first under DESC.
        monkeypatch.setattr(sm_module, "is_postgresql", lambda *a, **k: False)
        sqlite_clause = SessionManager._desc_nulls_first_order()
        assert "(timestamp IS NULL) DESC" in sqlite_clause

        # PostgreSQL branch: explicit NULLS FIRST.
        monkeypatch.setattr(sm_module, "is_postgresql", lambda *a, **k: True)
        pg_clause = SessionManager._desc_nulls_first_order()
        assert "NULLS FIRST" in pg_clause


# ============================== Limit clamping ===============================


class TestLimitClamping:
    def test_zero_or_negative_falls_back_to_default(self, manager):
        sid = _create_session(manager)
        for m in range(3):
            _add(manager, sid, _ts(m))

        page = manager.get_messages_page(sid, limit=0)
        assert len(page["messages"]) == 3  # all fit under default 100
        assert page["has_more"] is False

        page_neg = manager.get_messages_page(sid, limit=-5)
        assert len(page_neg["messages"]) == 3

    def test_over_max_clamped_to_max(self, manager):
        sid = _create_session(manager)
        for m in range(5):
            _add(manager, sid, _ts(m))

        # Asking for far more than MAX must not error and must clamp.
        page = manager.get_messages_page(sid, limit=10_000)
        assert len(page["messages"]) == 5
        # The fetch_n internally is min(limit, MAX)+1; with only 5 rows, no more.
        assert page["has_more"] is False

    def test_max_boundary_returns_exactly_max(self, manager):
        sid = _create_session(manager)
        # MAX + a few extras to force has_more at the cap.
        for n in range(SessionManager.MAX_MESSAGE_PAGE_SIZE + 3):
            _add(manager, sid, _ts_seq(n))

        page = manager.get_messages_page(sid, limit=SessionManager.MAX_MESSAGE_PAGE_SIZE)
        assert len(page["messages"]) == SessionManager.MAX_MESSAGE_PAGE_SIZE
        assert page["has_more"] is True
        assert page["next_cursor"] is not None


# ======================== Milestone-aware COUNT ==============================


class TestMilestoneCount:
    def test_count_messages_total_and_milestone_scoped(self, manager):
        sid = _create_session(manager)
        for m in range(4):
            _add(manager, sid, _ts(m), milestone_id="M1")
        for m in range(4, 7):
            _add(manager, sid, _ts(m), milestone_id="M2")
        _add(manager, sid, _ts(7), milestone_id="")

        assert manager.count_messages(sid) == 8
        assert manager.count_messages(sid, milestone_id="M1") == 4
        assert manager.count_messages(sid, milestone_id="M2") == 3

    def test_get_messages_page_respects_milestone_filter(self, manager):
        sid = _create_session(manager)
        for m in range(4):
            _add(manager, sid, _ts(m), milestone_id="M1")
        for m in range(4, 8):
            _add(manager, sid, _ts(m), milestone_id="M2")

        page = manager.get_messages_page(sid, limit=10, milestone_id="M1")
        assert all(getattr(m, "milestone_id", "") == "M1" for m in page["messages"])
        # Only the 4 M1 rows exist; the page reflects that, not the full 8.
        assert {m.id for m in page["messages"]} <= {
            m.id
            for m in manager.get_session(sid, include_messages=True).messages
            if getattr(m, "milestone_id", "") == "M1"
        }


# ============================ Empty / default ===============================


class TestEmptyAndDefaults:
    def test_empty_session_returns_empty_page(self, manager):
        sid = _create_session(manager)
        page = manager.get_messages_page(sid, limit=50)
        assert page["messages"] == []
        assert page["has_more"] is False
        assert page["next_cursor"] is None

    def test_default_limit_used_when_none(self, manager):
        sid = _create_session(manager)
        for m in range(3):
            _add(manager, sid, _ts(m))
        page = manager.get_messages_page(sid)  # no limit
        assert len(page["messages"]) == 3


# ============== Internal callers still load full history ====================


class TestInternalCallersUnaffected:
    """recover_session and autonomous paths go through get_session(include_messages=True)
    and must continue to receive the entire message history, not a page."""

    def test_get_session_full_history_when_requested(self, manager):
        sid = _create_session(manager)
        for n in range(150):  # well above the default page size of 100
            _add(manager, sid, _ts_seq(n))

        session = manager.get_session(sid, include_messages=True)
        assert session is not None
        assert len(session.messages) == 150, "internal full-load path must be unpaginated"

    def test_get_session_no_messages_by_default(self, manager):
        sid = _create_session(manager)
        for m in range(3):
            _add(manager, sid, _ts(m))

        session = manager.get_session(sid, include_messages=False)
        assert session.messages == []
