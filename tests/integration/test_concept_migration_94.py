"""Integration tests for Issue #94: Concept Migration — Request, Message, Conversation, Session.

Verifies that the legacy-scripts database schema correctly implements the new
concept definitions:
- Request: API call count (from auth_type field in logs)
- Message: All messages (with role breakdown: user, assistant, tool, error)
- Agent Session: Tool process session (identified by agent_session_id)
- Conversation: One round of conversation (user message -> AI complete,
  identified by conversation_id)

Schema under test:
- daily_messages.conversation_label -> feishu_conversation_id
- daily_messages.agent_session_id (new) - Tool process session identifier
- daily_messages.conversation_id (new) - One round of conversation identifier

R4 conversion (batch 16, #2429), migrated from
tests/issues/94/test_concept_migration.py: the legacy script was a zero-assert
print-and-return data audit over whatever live ``daily_messages`` the ambient
environment pointed at (its "No data → True" passes were vacuous). The legacy
DB seam is now pinned to a throwaway SQLite file (``_db_url_cache`` override —
the conftest ``tmp_db`` fixture does not apply to scripts/shared/db), seeded
with representative daily_messages rows, and every tally is a real assert.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager

import pytest

from scripts.shared import db as db_module

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.issue(94)]


@contextmanager
def _temp_sqlite_db():
    """Pin the legacy scripts/shared/db seam at a throwaway SQLite file.

    get_connection() resolves its target through _get_db_url() (cached), so
    patching DB_PATH alone never redirects anything — the legacy audit ran
    against whatever ambient database the environment configured (a local
    PostgreSQL in dev, the shared lane DB in CI). Overriding the cache pins
    the connection to a temp file regardless of ambient config (#2457 pattern,
    see tests/unit/test_admin_user_management_52.py).
    """
    temp_dir = tempfile.mkdtemp()
    original_db_path = db_module.DB_PATH
    original_db_dir = db_module.DB_DIR
    original_url_cache = db_module._db_url_cache
    db_module.DB_PATH = os.path.join(temp_dir, "test.db")
    db_module.DB_DIR = temp_dir
    db_module._db_url_cache = f"sqlite:///{db_module.DB_PATH}"
    try:
        yield
    finally:
        db_module.DB_PATH = original_db_path
        db_module.DB_DIR = original_db_dir
        db_module._db_url_cache = original_url_cache
        shutil.rmtree(temp_dir)


# Representative daily_messages workload mirroring what the legacy audit
# analyzed: three conversations (two codex sessions, one claude session) with
# user -> assistant (-> tool) chains, plus standalone rows carrying only
# feishu_conversation_id (or nothing) so coverage percentages are partial,
# the way they are in production data.
_SEED_DATE = "2026-08-27"
_SEED_ROWS = [
    # conv-001 / codex_s1: user -> assistant -> tool chain (feishu on first two)
    ("m1", None, "user", "conv-001", "codex_s1", "oc_feishu_a", "codex", "2026-08-27T10:00:00"),
    (
        "m2",
        "m1",
        "assistant",
        "conv-001",
        "codex_s1",
        "oc_feishu_a",
        "codex",
        "2026-08-27T10:01:00",
    ),
    ("m3", "m2", "tool", "conv-001", "codex_s1", None, "codex", "2026-08-27T10:02:00"),
    # conv-002 / codex_s2: user -> assistant chain (feishu on both)
    ("m4", None, "user", "conv-002", "codex_s2", "oc_feishu_b", "codex", "2026-08-27T11:00:00"),
    (
        "m5",
        "m4",
        "assistant",
        "conv-002",
        "codex_s2",
        "oc_feishu_b",
        "codex",
        "2026-08-27T11:05:00",
    ),
    # conv-003 / claude_s1: user -> assistant chain (no feishu)
    ("m6", None, "user", "conv-003", "claude_s1", None, "claude", "2026-08-27T12:00:00"),
    ("m7", "m6", "assistant", "conv-003", "claude_s1", None, "claude", "2026-08-27T12:03:00"),
    # standalone rows: feishu-only, and a bare message
    ("m8", None, "user", None, None, "oc_feishu_c", "codex", "2026-08-27T13:00:00"),
    ("m9", None, "user", None, None, None, "codex", "2026-08-27T13:30:00"),
]
_TOTAL = len(_SEED_ROWS)
_WITH_FEISHU = 5  # m1, m2, m4, m5, m8
_WITH_AGENT_SESSION = 7  # m1..m7
_WITH_CONVERSATION = 7  # m1..m7


@pytest.fixture
def seeded_db():
    """Pinned + initialized + seeded daily_messages database."""
    with _temp_sqlite_db():
        db_module.init_database()
        conn = db_module.get_connection()
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO daily_messages
                (date, tool_name, message_id, parent_id, role, content,
                 conversation_id, agent_session_id, feishu_conversation_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    _SEED_DATE,
                    row[6],
                    row[0],
                    row[1],
                    row[2],
                    f"content of {row[0]}",
                    row[3],
                    row[4],
                    row[5],
                    row[7],
                )
                for row in _SEED_ROWS
            ],
        )
        conn.commit()
        conn.close()
        yield


def _columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row["name"] for row in cursor.fetchall()]


def _indexes(cursor, table_name):
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table_name,)
    )
    return [row["name"] for row in cursor.fetchall()]


def test_database_schema(seeded_db):
    """Test 1: schema carries the renamed/new concept columns and index."""
    conn = db_module.get_connection()
    cursor = conn.cursor()

    columns = _columns(cursor, "daily_messages")

    # renamed + new concept columns all present
    assert "feishu_conversation_id" in columns, "feishu_conversation_id missing (rename target)"
    assert "agent_session_id" in columns, "agent_session_id missing"
    assert "conversation_id" in columns, "conversation_id missing"
    # old column correctly removed by the rename
    assert "conversation_label" not in columns, "conversation_label should be gone"

    indexes = _indexes(cursor, "daily_messages")
    assert "idx_messages_conversation" in indexes, "idx_messages_conversation missing"

    conn.close()


def test_data_migration(seeded_db):
    """Test 2: seeded concept identifiers cover the message population."""
    conn = db_module.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS cnt FROM daily_messages")
    total = cursor.fetchone()["cnt"]
    assert total == _TOTAL

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM daily_messages WHERE feishu_conversation_id IS NOT NULL"
    )
    with_feishu = cursor.fetchone()["cnt"]
    assert with_feishu == _WITH_FEISHU
    feishu_pct = with_feishu / total * 100
    assert feishu_pct > 0, f"feishu_conversation_id coverage: {feishu_pct:.1f}%"

    cursor.execute("SELECT COUNT(*) AS cnt FROM daily_messages WHERE agent_session_id IS NOT NULL")
    agent_pct = cursor.fetchone()["cnt"] / total * 100
    assert agent_pct > 50, f"agent_session_id coverage too low: {agent_pct:.1f}%"

    cursor.execute("SELECT COUNT(*) AS cnt FROM daily_messages WHERE conversation_id IS NOT NULL")
    conv_pct = cursor.fetchone()["cnt"] / total * 100
    assert conv_pct > 50, f"conversation_id coverage too low: {conv_pct:.1f}%"

    conn.close()


def test_concept_definitions(seeded_db):
    """Test 3: agent sessions aggregate multiple messages; roles break down."""
    conn = db_module.get_connection()
    cursor = conn.cursor()

    # Agent Session concept: sessions group multiple messages
    cursor.execute("""
        SELECT agent_session_id, COUNT(*) AS msg_count
        FROM daily_messages
        WHERE agent_session_id IS NOT NULL
        GROUP BY agent_session_id
        ORDER BY msg_count DESC
        LIMIT 5
        """)
    sessions = cursor.fetchall()
    assert sessions, "no agent session data found"
    assert sessions[0]["msg_count"] > 1, "agent sessions should contain multiple messages"
    assert sessions[0]["agent_session_id"] == "codex_s1"

    # Conversation concept: conversations carry messages
    cursor.execute("""
        SELECT conversation_id, COUNT(*) AS msg_count
        FROM daily_messages
        WHERE conversation_id IS NOT NULL
        GROUP BY conversation_id
        ORDER BY msg_count DESC
        LIMIT 5
        """)
    conversations = cursor.fetchall()
    assert conversations, "no conversation data found"
    assert conversations[0]["msg_count"] >= 1, "conversations appear empty"
    assert conversations[0]["conversation_id"] == "conv-001"

    # Message role breakdown includes both user and assistant
    cursor.execute("""
        SELECT role, COUNT(*) AS count
        FROM daily_messages
        WHERE role IS NOT NULL
        GROUP BY role
        ORDER BY count DESC
        """)
    role_names = [row["role"] for row in cursor.fetchall()]
    assert "user" in role_names, f"user role missing: {role_names}"
    assert "assistant" in role_names, f"assistant role missing: {role_names}"

    conn.close()


def test_conversation_structure(seeded_db):
    """Test 4: a conversation is a user message -> AI response chain."""
    conn = db_module.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT conversation_id, COUNT(*) AS msg_count
        FROM daily_messages
        WHERE conversation_id IS NOT NULL
        GROUP BY conversation_id
        HAVING COUNT(*) >= 2
        ORDER BY msg_count DESC
        LIMIT 1
        """)
    result = cursor.fetchone()
    assert result is not None, "no conversation with multiple messages found"
    conv_id = result["conversation_id"]

    cursor.execute(
        """
        SELECT message_id, parent_id, role, content
        FROM daily_messages
        WHERE conversation_id = ?
        ORDER BY timestamp ASC
        """,
        (conv_id,),
    )
    messages = cursor.fetchall()
    assert len(messages) >= 2

    roles = [msg["role"] for msg in messages]
    assert "user" in roles, f"conversation {conv_id} has no user message"
    assert "assistant" in roles, f"conversation {conv_id} has no assistant message"

    # the chain is ordered and linked: first a root user message, then the
    # assistant reply whose parent is that user message
    first, second = messages[0], messages[1]
    assert first["role"] == "user" and first["parent_id"] is None
    assert second["role"] == "assistant"
    assert second["parent_id"] == first["message_id"]

    conn.close()


def test_session_agent_mapping(seeded_db):
    """Test 5: agent_session_id prefixes map onto their tool_name."""
    conn = db_module.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT agent_session_id, tool_name
        FROM daily_messages
        WHERE agent_session_id IS NOT NULL
        LIMIT 10
        """)
    mappings = cursor.fetchall()
    assert mappings, "no agent session data found"

    valid_pattern = 0
    for mapping in mappings:
        session_id = mapping["agent_session_id"]
        tool_name = mapping["tool_name"]
        assert "_" in str(session_id), f"invalid session pattern: {session_id}"
        assert (
            session_id.split("_")[0] == tool_name
        ), f"session {session_id} does not map to tool {tool_name}"
        valid_pattern += 1

    assert valid_pattern == len(mappings) > 0

    conn.close()
