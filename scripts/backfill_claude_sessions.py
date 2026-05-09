"""
DEPRECATED: This script is no longer needed.

Historical Claude sessions are now handled by `fetch_claude.py --days N`
which correctly reads JSONL files and creates agent_sessions with full
message content. This backfill script created sessions with empty content
because it generated MD5-based fake session IDs that could not be matched
back to daily_messages for content retrieval.

The empty sessions it created have been cleaned up from the database.
Do not run this script again.
"""

import hashlib
import json
import os
import sys
from collections import defaultdict

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from shared import db


def backfill_sessions():
    """Backfill agent_sessions from historical Claude daily_messages."""
    db.init_database()

    from shared.db import _execute, _placeholder, get_connection

    conn = get_connection()
    cursor = conn.cursor()
    p = _placeholder()

    _execute(
        cursor,
        """
        SELECT id, date, tool_name, host_name, message_id, role,
               tokens_used, model, timestamp, sender_name,
               conversation_id, project_path
        FROM daily_messages
        WHERE tool_name = 'claude'
          AND agent_session_id IS NULL
        ORDER BY timestamp ASC
    """,
    )
    rows = cursor.fetchall()
    print(f"Found {len(rows)} claude messages without agent_session_id")

    if not rows:
        print("Nothing to backfill.")
        conn.close()
        return

    session_groups = defaultdict(list)
    for row in rows:
        rd = dict(row)
        sender = rd.get("sender_name", "unknown")
        date = rd.get("date", "unknown")
        conv_id = rd.get("conversation_id")
        session_key = conv_id if conv_id else f"claude_{sender}_{date}"
        session_groups[session_key].append(rd)

    print(f"Grouped into {len(session_groups)} sessions")

    updated_sessions = 0
    updated_messages = 0
    inserted_sm = 0

    for session_key, msgs in session_groups.items():
        h = hashlib.md5(session_key.encode()).hexdigest()
        session_id = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

        first_msg = msgs[0]
        sender_name = first_msg.get("sender_name", "")
        system_account = sender_name.split("-")[0] if sender_name else "unknown"
        host_name = first_msg.get("host_name", "localhost")
        project_path = first_msg.get("project_path", "")

        _execute(
            cursor,
            f"SELECT id FROM users WHERE system_account = {p} OR username = {p}",
            (system_account, system_account),
        )
        user_row = cursor.fetchone()
        user_id = user_row["id"] if user_row else None

        message_count = len(msgs)
        total_tokens = sum(m.get("tokens_used", 0) or 0 for m in msgs)
        request_count = sum(1 for m in msgs if m.get("role") == "assistant")
        models = {m.get("model") for m in msgs if m.get("model")}
        model = sorted(models)[0] if models else None

        timestamps = [m.get("timestamp") for m in msgs if m.get("timestamp")]
        first_ts = min(timestamps) if timestamps else None
        last_ts = max(timestamps) if timestamps else None

        _execute(cursor, f"SELECT id FROM agent_sessions WHERE session_id = {p}", (session_id,))
        existing = cursor.fetchone()

        title = f"claude - {session_id[:8]}"

        if not existing:
            _execute(
                cursor,
                """INSERT INTO agent_sessions
                (session_id, session_type, title, tool_name, host_name, user_id,
                 status, project_path, message_count, total_tokens, request_count,
                 model, created_at, updated_at)
                VALUES ({},{},{},{},{},{},{},{},{},{},{},{},{},{})""".format(*([p] * 14)),
                (
                    session_id,
                    "chat",
                    title,
                    "claude",
                    host_name,
                    user_id,
                    "completed",
                    project_path,
                    message_count,
                    total_tokens,
                    request_count,
                    model,
                    first_ts,
                    last_ts,
                ),
            )
            updated_sessions += 1
        else:
            _execute(
                cursor,
                f"""UPDATE agent_sessions
                SET message_count = GREATEST(COALESCE(message_count, 0), {p}),
                    total_tokens = GREATEST(COALESCE(total_tokens, 0), {p}),
                    request_count = GREATEST(COALESCE(request_count, 0), {p}),
                    model = COALESCE(model, {p}),
                    updated_at = {p}
                WHERE session_id = {p}""",
                (message_count, total_tokens, request_count, model, last_ts, session_id),
            )

        msg_ids = [m.get("id") for m in msgs if m.get("id")]
        if msg_ids:
            plist = ",".join([p] * len(msg_ids))
            _execute(
                cursor,
                f"UPDATE daily_messages SET agent_session_id = {p} WHERE id IN ({plist}) AND agent_session_id IS NULL",
                [session_id] + msg_ids,
            )
            updated_messages += cursor.rowcount

        for m in msgs:
            ts = m.get("timestamp")
            if not ts:
                continue
            try:
                _execute(
                    cursor,
                    f"SELECT id FROM session_messages WHERE session_id={p} AND role={p} AND timestamp={p}",
                    (session_id, m.get("role"), ts),
                )
                if not cursor.fetchone():
                    metadata = {
                        "message_id": m.get("message_id"),
                        "project_path": m.get("project_path"),
                        "backfilled": True,
                    }
                    _execute(
                        cursor,
                        """INSERT INTO session_messages
                        (session_id, role, content, tokens_used, model, timestamp, metadata)
                        VALUES ({},{},{},{},{},{},{})""".format(*([p] * 7)),
                        (
                            session_id,
                            m.get("role"),
                            (m.get("content") or "")[:1000],
                            m.get("tokens_used", 0) or 0,
                            m.get("model"),
                            ts,
                            json.dumps(metadata),
                        ),
                    )
                    inserted_sm += 1
            except Exception:
                pass

        conn.commit()

    cursor.close()
    conn.close()

    print("")
    print("Backfill complete:")
    print(f"  Sessions created/updated: {updated_sessions}")
    print(f"  Messages updated: {updated_messages}")
    print(f"  Session messages inserted: {inserted_sm}")


if __name__ == "__main__":
    backfill_sessions()
