"""Equivalence tests for MessageRepository.get_user_conversation_samples.

The method was rewritten from an N+1 per-session fetch into a single batch
query. These tests pin the application-layer grouping to the ORIGINAL
per-session semantics, including the two cases that would make a COALESCE
grouping key WRONG:

  * value collision — the same id appears as both an agent_session_id and a
    conversation_id (the row legitimately belongs to multiple sessions);
  * both fields populated — agent_session_id = S1 AND conversation_id = S2
    (the row belongs to BOTH S1 and S2).

Grouping must use the per-session ``(agent_session_id = S OR conversation_id
= S)`` predicate, NOT ``COALESCE(...)``.
"""

from unittest.mock import MagicMock

from app.repositories.message_repo import MessageRepository


def _repo(session_rows, msg_rows):
    """db.fetch_all returns session_rows (session_query) then msg_rows (batch)."""
    db = MagicMock()
    db.fetch_all.side_effect = [session_rows, msg_rows]
    return MessageRepository(db=db), db


class TestGetUserConversationSamples:
    def test_no_sessions_returns_empty(self):
        repo, db = _repo([], [])
        assert repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u") == []
        # Only the session_query is issued — no batch, no per-session calls.
        assert db.fetch_all.call_count == 1

    def test_distinct_sessions_grouped_and_nplus1_eliminated(self):
        sessions = [{"session_id": "s1"}, {"session_id": "s2"}]
        # Rows arrive in timestamp-ASC order (the ORDER BY of the batch query).
        rows = [
            {"agent_session_id": "s1", "conversation_id": None, "role": "user", "content": "a1"},
            {
                "agent_session_id": "s1",
                "conversation_id": None,
                "role": "assistant",
                "content": "b1",
            },
            {"agent_session_id": "s2", "conversation_id": None, "role": "user", "content": "a2"},
        ]
        repo, db = _repo(sessions, rows)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        assert result == [
            {
                "session_id": "s1",
                "messages": [
                    {"role": "user", "content": "a1"},
                    {"role": "assistant", "content": "b1"},
                ],
            },
            {"session_id": "s2", "messages": [{"role": "user", "content": "a2"}]},
        ]
        # N+1 eliminated: exactly 2 fetch_all calls (session_query + one batch).
        assert db.fetch_all.call_count == 2

    def test_value_collision_and_both_fields_populated(self):
        # session_ids = ["A", "B"]. Value "A" is BOTH an agent_session_id (R1)
        # and a conversation_id (R2). R3 has both fields set. Original per-
        # session predicate: session A matches R1/R2/R3, session B matches R3.
        sessions = [{"session_id": "A"}, {"session_id": "B"}]
        rows = [
            {"agent_session_id": "A", "conversation_id": None, "role": "user", "content": "m1"},
            {
                "agent_session_id": None,
                "conversation_id": "A",
                "role": "assistant",
                "content": "m2",
            },
            {"agent_session_id": "B", "conversation_id": "A", "role": "user", "content": "m3"},
        ]
        repo, _ = _repo(sessions, rows)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        assert result == [
            {
                "session_id": "A",
                "messages": [
                    {"role": "user", "content": "m1"},
                    {"role": "assistant", "content": "m2"},
                    {"role": "user", "content": "m3"},
                ],
            },
            {"session_id": "B", "messages": [{"role": "user", "content": "m3"}]},
        ]

    def test_same_id_in_both_fields_not_duplicated(self):
        # A row where agent_session_id == conversation_id == S must appear once.
        sessions = [{"session_id": "S"}]
        rows = [
            {"agent_session_id": "S", "conversation_id": "S", "role": "user", "content": "only"}
        ]
        repo, _ = _repo(sessions, rows)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        assert result == [
            {"session_id": "S", "messages": [{"role": "user", "content": "only"}]},
        ]

    def test_empty_session_dropped_order_preserved(self):
        sessions = [{"session_id": "empty"}, {"session_id": "full"}]
        rows = [
            {"agent_session_id": "full", "conversation_id": None, "role": "user", "content": "x"}
        ]
        repo, _ = _repo(sessions, rows)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        # "empty" has no rows -> dropped; session_query order otherwise kept.
        assert result == [{"session_id": "full", "messages": [{"role": "user", "content": "x"}]}]

    def test_messages_preserve_timestamp_order(self):
        sessions = [{"session_id": "s1"}]
        rows = [
            {"agent_session_id": "s1", "conversation_id": None, "role": "user", "content": "first"},
            {
                "agent_session_id": "s1",
                "conversation_id": None,
                "role": "assistant",
                "content": "second",
            },
            {"agent_session_id": "s1", "conversation_id": None, "role": "user", "content": "third"},
        ]
        repo, _ = _repo(sessions, rows)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        contents = [m["content"] for m in result[0]["messages"]]
        assert contents == ["first", "second", "third"]

    def test_batch_query_uses_in_list_and_binds_ids_twice(self):
        sessions = [{"session_id": "s1"}, {"session_id": "s2"}, {"session_id": "s3"}]
        repo, db = _repo(sessions, [])
        repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        batch_query, batch_params = db.fetch_all.call_args_list[1][0]
        assert "agent_session_id IN" in batch_query
        assert "conversation_id IN" in batch_query
        assert "role IN ('user', 'assistant')" in batch_query
        # One "?" per id per IN-list (SQLite placeholders; adapted to "%s" for
        # PG by Database.fetch_all), bound once for each field.
        assert batch_query.count("?") == 6
        assert tuple(batch_params) == ("s1", "s2", "s3", "s1", "s2", "s3")

    def test_batch_failure_falls_back_to_per_session(self):
        sessions = [{"session_id": "s1"}, {"session_id": "s2"}]
        db = MagicMock()
        # call 1: session_query -> sessions
        # call 2: batch -> raises (simulated decode failure)
        # call 3/4: per-session fallback (2-column SELECT rows)
        db.fetch_all.side_effect = [
            sessions,
            RuntimeError("boom"),
            [{"role": "user", "content": "a1"}],
            [{"role": "assistant", "content": "b2"}],
        ]
        repo = MessageRepository(db=db)
        result = repo.get_user_conversation_samples("2026-01-01", "2026-01-31", "u")
        assert result == [
            {"session_id": "s1", "messages": [{"role": "user", "content": "a1"}]},
            {"session_id": "s2", "messages": [{"role": "assistant", "content": "b2"}]},
        ]
        assert db.fetch_all.call_count == 4
