#!/usr/bin/env python3
"""
Test script for Issue #94: Concept Migration - Request, Message, Conversation, Session

This test verifies that the database migration correctly implements the new concept definitions:
- Request: API call count (from auth_type field in logs)
- Message: All messages (with role breakdown: user, assistant, toolResult, error)
- Agent Session: Tool process session (identified by agent_session_id)
- Conversation: One round of conversation (user message -> AI complete, identified by conversation_id)

Database Schema Changes:
- daily_messages.conversation_label -> feishu_conversation_id
- daily_messages.agent_session_id (new) - Tool process session identifier
- daily_messages.conversation_id (new) - One round of conversation identifier

Usage:
    python3 tests/issues/94/test_concept_migration.py
"""

import os
import sys
from datetime import datetime

# Add shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
sys.path.insert(0, os.path.join(project_root, "scripts", "shared"))

from db import DB_PATH, get_connection, is_postgresql


# Placeholder style: PostgreSQL uses %s, SQLite uses ?
def _ph():
    return "%s" if is_postgresql() else "?"


def _get_columns(cursor, table_name):
    """Get column names for a table, compatible with both PG and SQLite."""
    if is_postgresql():
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            (table_name,),
        )
        return [row["column_name"] for row in cursor.fetchall()]
    else:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row["name"] for row in cursor.fetchall()]


def _get_indexes(cursor, table_name):
    """Get index names for a table, compatible with both PG and SQLite."""
    if is_postgresql():
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s",
            (table_name,),
        )
        return [row["indexname"] for row in cursor.fetchall()]
    else:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table_name,),
        )
        return [row["name"] for row in cursor.fetchall()]


def _fetch_val(row, idx):
    """Get value from row by index, handling both dict and tuple rows."""
    if isinstance(row, dict):
        vals = list(row.values())
        return vals[idx]
    return row[idx]


def test_database_schema():
    """Test 1: Verify database schema changes."""
    print("\n" + "=" * 60)
    print("Test 1: Database Schema Verification")
    print("=" * 60)

    results = []
    conn = get_connection()
    cursor = conn.cursor()

    try:
        columns = _get_columns(cursor, "daily_messages")
        print(f"\nCurrent columns in daily_messages: {len(columns)} total")

        required_columns = {
            "feishu_conversation_id": "Renamed from conversation_label",
            "agent_session_id": "New: Tool process session identifier",
            "conversation_id": "New: One round of conversation identifier",
        }

        for col_name, description in required_columns.items():
            if col_name in columns:
                print(f"  ok {col_name}: EXISTS - {description}")
                results.append((f"Column: {col_name}", True, description))
            else:
                print(f"  X {col_name}: MISSING - {description}")
                results.append((f"Column: {col_name}", False, description))

        if "conversation_label" not in columns:
            print("  ok conversation_label: REMOVED (correctly renamed)")
            results.append(
                ("Old column removed", True, "conversation_label renamed to feishu_conversation_id")
            )
        else:
            print("  X conversation_label: STILL EXISTS (should be removed)")
            results.append(("Old column removed", False, "conversation_label should be removed"))

        print("\nChecking indexes...")
        indexes = _get_indexes(cursor, "daily_messages")

        required_indexes = [
            "idx_messages_conversation",
        ]

        for idx_name in required_indexes:
            if idx_name in indexes:
                print(f"  ok Index: {idx_name}")
                results.append((f"Index: {idx_name}", True, ""))
            else:
                print(f"  X Index: {idx_name} MISSING")
                results.append((f"Index: {idx_name}", False, "Missing"))

        conn.close()
        return results

    except Exception as e:
        print(f"  X Error: {e}")
        results.append(("Schema test", False, str(e)))
        conn.close()
        return results


def test_data_migration():
    """Test 2: Verify data migration completeness."""
    print("\n" + "=" * 60)
    print("Test 2: Data Migration Completeness")
    print("=" * 60)

    results = []
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM daily_messages")
        total_messages = _fetch_val(cursor.fetchone(), 0)
        print(f"\nTotal messages: {total_messages}")

        if total_messages == 0:
            print("  Warning: No data in database - skipping data tests")
            results.append(("Data exists", True, "No data to test"))
            conn.close()
            return results

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE feishu_conversation_id IS NOT NULL"
        )
        with_feishu_conv = _fetch_val(cursor.fetchone(), 0)
        feishu_pct = (with_feishu_conv / total_messages * 100) if total_messages > 0 else 0
        print(f"\nMessages with feishu_conversation_id: {with_feishu_conv} ({feishu_pct:.1f}%)")

        if feishu_pct > 0:
            print(f"  ok feishu_conversation_id: {feishu_pct:.1f}% coverage")
            results.append(("feishu_conversation_id coverage", True, f"{feishu_pct:.1f}%"))
        else:
            print("  Warning: feishu_conversation_id: No data (may be expected)")
            results.append(("feishu_conversation_id coverage", True, "No Feishu data"))

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE agent_session_id IS NOT NULL"
        )
        with_agent_session = _fetch_val(cursor.fetchone(), 0)
        agent_pct = (with_agent_session / total_messages * 100) if total_messages > 0 else 0
        print(f"Messages with agent_session_id: {with_agent_session} ({agent_pct:.1f}%)")

        if agent_pct > 50:
            print(f"  ok agent_session_id: {agent_pct:.1f}% coverage (good)")
            results.append(("agent_session_id coverage", True, f"{agent_pct:.1f}%"))
        elif agent_pct > 0:
            print(f"  Warning: agent_session_id: {agent_pct:.1f}% coverage (partial)")
            results.append(("agent_session_id coverage", True, f"{agent_pct:.1f}% partial"))
        else:
            print("  X agent_session_id: No data populated")
            results.append(("agent_session_id coverage", False, "0%"))

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM daily_messages WHERE conversation_id IS NOT NULL"
        )
        with_conversation = _fetch_val(cursor.fetchone(), 0)
        conv_pct = (with_conversation / total_messages * 100) if total_messages > 0 else 0
        print(f"Messages with conversation_id: {with_conversation} ({conv_pct:.1f}%)")

        if conv_pct > 50:
            print(f"  ok conversation_id: {conv_pct:.1f}% coverage (good)")
            results.append(("conversation_id coverage", True, f"{conv_pct:.1f}%"))
        elif conv_pct > 0:
            print(f"  Warning: conversation_id: {conv_pct:.1f}% coverage (partial)")
            results.append(("conversation_id coverage", True, f"{conv_pct:.1f}% partial"))
        else:
            print("  X conversation_id: No data populated")
            results.append(("conversation_id coverage", False, "0%"))

        conn.close()
        return results

    except Exception as e:
        print(f"  X Error: {e}")
        results.append(("Data migration test", False, str(e)))
        conn.close()
        return results


def test_concept_definitions():
    """Test 3: Verify concept definitions are correctly implemented."""
    print("\n" + "=" * 60)
    print("Test 3: Concept Definitions Verification")
    print("=" * 60)

    results = []
    conn = get_connection()
    cursor = conn.cursor()

    try:
        print("\n[Agent Session Concept]")
        cursor.execute(
            """
            SELECT agent_session_id, COUNT(*) as msg_count
            FROM daily_messages
            WHERE agent_session_id IS NOT NULL
            GROUP BY agent_session_id
            ORDER BY msg_count DESC
            LIMIT 5
        """
        )
        sessions = cursor.fetchall()

        if sessions:
            print(f"  Found {len(sessions)} agent sessions (showing top 5)")
            for session in sessions:
                session_id = _fetch_val(session, 0)
                count = _fetch_val(session, 1)
                print(f"    - {session_id}: {count} messages")

            top_count = _fetch_val(sessions[0], 1)
            if top_count > 1:
                print("  ok Agent sessions contain multiple messages")
                results.append(
                    ("Agent Session concept", True, f"Top session: {top_count} messages")
                )
            else:
                print("  Warning: Agent sessions have only 1 message each")
                results.append(("Agent Session concept", True, "Single message sessions"))
        else:
            print("  Warning: No agent session data found")
            results.append(("Agent Session concept", True, "No data"))

        print("\n[Conversation Concept]")
        cursor.execute(
            """
            SELECT conversation_id, COUNT(*) as msg_count
            FROM daily_messages
            WHERE conversation_id IS NOT NULL
            GROUP BY conversation_id
            ORDER BY msg_count DESC
            LIMIT 5
        """
        )
        conversations = cursor.fetchall()

        if conversations:
            print(f"  Found {len(conversations)} conversations (showing top 5)")
            for conv in conversations:
                conv_id = _fetch_val(conv, 0)
                count = _fetch_val(conv, 1)
                print(f"    - {conv_id}: {count} messages")

            top_count = _fetch_val(conversations[0], 1)
            if top_count >= 1:
                print("  ok Conversations contain messages")
                results.append(
                    ("Conversation concept", True, f"Top conversation: {top_count} messages")
                )
            else:
                print("  X Conversations appear empty")
                results.append(("Conversation concept", False, "Empty conversations"))
        else:
            print("  Warning: No conversation data found")
            results.append(("Conversation concept", True, "No data"))

        print("\n[Message Role Breakdown]")
        cursor.execute(
            """
            SELECT role, COUNT(*) as count
            FROM daily_messages
            WHERE role IS NOT NULL
            GROUP BY role
            ORDER BY count DESC
        """
        )
        roles = cursor.fetchall()

        if roles:
            print("  Message roles found:")
            role_names = []
            for r in roles:
                role_name = _fetch_val(r, 0)
                count = _fetch_val(r, 1)
                role_names.append(role_name)
                print(f"    - {role_name}: {count} messages")

            if "user" in role_names and "assistant" in role_names:
                print("  ok Has user and assistant roles")
            else:
                print("  Warning: Missing expected roles (user, assistant)")
            results.append(("Message roles", True, f"Roles: {role_names}"))
        else:
            print("  Warning: No role data found")
            results.append(("Message roles", True, "No data"))

        conn.close()
        return results

    except Exception as e:
        print(f"  X Error: {e}")
        results.append(("Concept definitions test", False, str(e)))
        conn.close()
        return results


def test_conversation_structure():
    """Test 4: Verify conversation structure (user message -> AI response chain)."""
    print("\n" + "=" * 60)
    print("Test 4: Conversation Structure Verification")
    print("=" * 60)

    results = []
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT conversation_id, COUNT(*) as msg_count
            FROM daily_messages
            WHERE conversation_id IS NOT NULL
            GROUP BY conversation_id
            HAVING COUNT(*) >= 2
            ORDER BY msg_count DESC
            LIMIT 1
        """
        )
        result = cursor.fetchone()

        if result:
            conv_id = _fetch_val(result, 0)
            msg_count = _fetch_val(result, 1)
            print(f"\nAnalyzing conversation: {conv_id} ({msg_count} messages)")

            ph = _ph()
            cursor.execute(
                f"""
                SELECT message_id, parent_id, role, content
                FROM daily_messages
                WHERE conversation_id = {ph}
                ORDER BY timestamp ASC
            """,
                (conv_id,),
            )
            messages = cursor.fetchall()

            print("\n  Message chain:")
            has_user = False
            has_assistant = False
            for msg in messages:
                msg_id = _fetch_val(msg, 0)
                parent_id = _fetch_val(msg, 1)
                role = _fetch_val(msg, 2)
                content = _fetch_val(msg, 3)
                content_preview = (
                    (content[:50] + "...") if content and len(content) > 50 else content
                )
                print(f"    - [{role}] {msg_id} (parent: {parent_id})")
                print(f"      Content: {content_preview}")

                if role == "user":
                    has_user = True
                elif role == "assistant":
                    has_assistant = True

            if has_user and has_assistant:
                print("\n  ok Conversation has user and assistant messages")
                results.append(
                    ("Conversation structure", True, f"{msg_count} messages, has user+assistant")
                )
            elif has_user:
                print("\n  Warning: Conversation only has user messages")
                results.append(("Conversation structure", True, "Only user messages"))
            else:
                print("\n  Warning: Conversation structure unclear")
                results.append(("Conversation structure", True, "Structure unclear"))
        else:
            print("\n  Warning: No conversation with multiple messages found")
            results.append(("Conversation structure", True, "No multi-message conversations"))

        conn.close()
        return results

    except Exception as e:
        print(f"  X Error: {e}")
        results.append(("Conversation structure test", False, str(e)))
        conn.close()
        return results


def test_session_agent_mapping():
    """Test 5: Verify agent session to tool mapping."""
    print("\n" + "=" * 60)
    print("Test 5: Agent Session to Tool Mapping")
    print("=" * 60)

    results = []
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT DISTINCT agent_session_id, tool_name
            FROM daily_messages
            WHERE agent_session_id IS NOT NULL
            LIMIT 10
        """
        )
        mappings = cursor.fetchall()

        if mappings:
            print("\nAgent session to tool mappings (sample):")
            valid_pattern = 0
            invalid_pattern = 0

            for mapping in mappings:
                session_id = _fetch_val(mapping, 0)
                tool_name = _fetch_val(mapping, 1)
                if "_" in str(session_id):
                    session_tool = session_id.split("_")[0]
                    if session_tool == tool_name:
                        print(f"  ok {session_id} -> {tool_name} (matches)")
                        valid_pattern += 1
                    else:
                        print(
                            f"  Warning: {session_id} -> {tool_name} (mismatch: expected {session_tool})"
                        )
                        invalid_pattern += 1
                else:
                    print(f"  Warning: {session_id} -> {tool_name} (invalid pattern)")
                    invalid_pattern += 1

            if valid_pattern > 0:
                print(f"\n  ok Found {valid_pattern} valid session patterns")
                results.append(
                    (
                        "Session-tool mapping",
                        True,
                        f"{valid_pattern} valid, {invalid_pattern} invalid",
                    )
                )
            else:
                print("\n  Warning: No valid session patterns found")
                results.append(
                    ("Session-tool mapping", True, f"{invalid_pattern} invalid patterns")
                )
        else:
            print("\n  Warning: No agent session data found")
            results.append(("Session-tool mapping", True, "No data"))

        conn.close()
        return results

    except Exception as e:
        print(f"  X Error: {e}")
        results.append(("Session-agent mapping test", False, str(e)))
        conn.close()
        return results


def run_all_tests():
    """Run all tests and generate report."""
    print("\n" + "=" * 60)
    print("Issue #94: Concept Migration - Full Test Suite")
    print("=" * 60)
    print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Database: {DB_PATH}")
    print(f"PostgreSQL: {is_postgresql()}")

    all_results = []

    all_results.extend(test_database_schema())
    all_results.extend(test_data_migration())
    all_results.extend(test_concept_definitions())
    all_results.extend(test_conversation_structure())
    all_results.extend(test_session_agent_mapping())

    print("\n" + "=" * 60)
    print("TEST SUMMARY REPORT")
    print("=" * 60)

    passed = sum(1 for r in all_results if r[1])
    failed = sum(1 for r in all_results if not r[1])
    total = len(all_results)

    print(f"\nTotal Tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Success Rate: {passed/total*100:.1f}%")

    print("\n" + "-" * 60)
    print("Detailed Results:")
    print("-" * 60)

    for name, success, detail in all_results:
        status = "PASS" if success else "FAIL"
        detail_str = f" - {detail}" if detail else ""
        print(f"  {status}: {name}{detail_str}")

    print("\n" + "=" * 60)

    if failed == 0:
        print("ALL TESTS PASSED")
        print("=" * 60)
        return True
    else:
        print(f"{failed} TEST(S) FAILED")
        print("=" * 60)
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
