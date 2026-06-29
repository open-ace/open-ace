#!/usr/bin/env python3
"""
Open ACE - ZCode Fetcher

Fetches session data from the ZCode CLI SQLite database.

Unlike Claude/Codex/Qwen (which store sessions as JSONL files), ZCode stores
sessions relationally in ``~/.zcode/cli/db/db.sqlite`` with tables: session,
message, part, turn_usage. This fetcher reuses the ``ZcodeSession`` parser from
``remote-agent/session_sync.py`` so the parsing logic stays in one place, then
converts each parsed session into the same message-dict shape the other fetch
scripts use and writes it to daily_usage / daily_messages / agent_sessions /
session_messages — exactly mirroring ``scripts/fetch_codex.py``.

Only ``interactive`` (not ``subagent_child``) and non-archived sessions are
synced, matching ``SessionSyncService._scan_and_sync_zcode_db``.
"""

import argparse
import getpass
import json
import os
import platform
import socket
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, script_dir)
from shared import db
from shared.utils import update_session_last_seen, warn_if_skipped_message_has_text

# Import the ZcodeSession parser from remote-agent (single source of truth).
# remote-agent is a package (has __init__.py) but session_sync imports its
# sibling ``cli_adapters.codex_jsonl_parser`` as a top-level module, so we put
# the remote-agent dir on sys.path rather than importing it as a package.
_remote_agent_path = os.path.join(script_dir, "..", "remote-agent")
if _remote_agent_path not in sys.path:
    sys.path.insert(0, _remote_agent_path)
from session_sync import ZcodeSession  # noqa: E402

TOOL_NAME = "zcode"
# ZCode stores sessions in a SQLite database under ~/.zcode/cli/db/db.sqlite
ZCODE_DB_RELATIVE = os.path.join(".zcode", "cli", "db", "db.sqlite")
# Only sync interactive sessions (skip subagent_child) that are not archived,
# matching SessionSyncService._scan_and_sync_zcode_db.
CANDIDATE_SQL = (
    "SELECT id, time_updated, time_created FROM session "
    "WHERE task_type = 'interactive' AND time_archived IS NULL"
)


def get_default_sender_name(tool: str = TOOL_NAME) -> str:
    """Generate default sender name in format: {user}-{hostname}-{tool}."""
    user = getpass.getuser()
    hostname = socket.gethostname()
    return f"{user}-{hostname}-{tool}"


def find_all_zcode_db_paths() -> list[tuple[str, Path]]:
    """Find ZCode DB paths for all users on the system.

    Scans /home/*/.zcode/cli/db/db.sqlite (Linux) or
    /Users/*/.zcode/cli/db/db.sqlite (macOS).

    Returns:
        List of tuples: [(system_account, db_path), ...]
    """
    results: list[tuple[str, Path]] = []
    os_type = platform.system().lower()

    if os_type == "linux":
        home_base = Path("/home")
    elif os_type == "darwin":
        home_base = Path("/Users")
    else:
        # Windows or other — just use current user.
        db_path = Path.home() / ZCODE_DB_RELATIVE
        if db_path.is_file():
            results.append((getpass.getuser(), db_path))
        return results

    if not home_base.is_dir():
        return results

    for user_dir in home_base.iterdir():
        if not user_dir.is_dir():
            continue
        system_account = user_dir.name
        db_path = user_dir / ZCODE_DB_RELATIVE
        try:
            if db_path.is_file():
                results.append((system_account, db_path))
        except PermissionError:
            print(f"  Warning: Cannot access {db_path} (permission denied)")
            continue

    return results


def find_zcode_db_path() -> Optional[Path]:
    """Find the ZCode DB path for the current user."""
    db_path = Path.home() / ZCODE_DB_RELATIVE
    if db_path.is_file():
        return db_path
    return None


def _ms_to_date(ms: Optional[int]) -> str:
    """Convert epoch milliseconds to a YYYY-MM-DD date string (local time)."""
    if not ms:
        return "unknown"
    try:
        # Convert UTC timestamp to local time for date extraction
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "unknown"


def process_zcode_session(
    session_id: str,
    db_path: Path,
    hostname: str = "localhost",
    system_account: Optional[str] = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    """Parse one ZCode session into daily aggregates + fetch message dicts.

    Mirrors ``process_jsonl_file`` in fetch_codex.py: returns
    (daily_stats, messages, project_path).

    Args:
        session_id: ZCode session id.
        db_path: Path to the ZCode SQLite DB.
        hostname: Host name for this machine.
        system_account: System account (linux/mac username) for multi-user mode.

    Returns:
        tuple: (daily_stats dict keyed by date, messages list, project_path)
    """
    session = ZcodeSession(session_id, db_path)
    if not session.parse() or session.message_count == 0:
        return {}, [], None

    sender_name = (
        f"{system_account}-{hostname}-{TOOL_NAME}" if system_account else get_default_sender_name()
    )

    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )

    messages: list[dict[str, Any]] = []
    # Collect distinct dates (from message timestamps). request_count is
    # incremented per assistant message on its own date (mirrors
    # fetch_codex.py:331), so daily request volume reflects real turn counts.
    # Token totals are authoritative session-level values from the turn_usage
    # table (session.total_input_tokens / total_output_tokens); per-message
    # ``data.tokens`` is sparse, so we do NOT sum it (see review feedback —
    # that path under-counts). Tokens are attributed to the session's dominant
    # date below and injected into the first assistant message for
    # agent_sessions (codex pattern).
    session_dates: set[str] = set()
    for msg in session.messages:
        ts = msg.get("timestamp")
        date_key = _ms_to_date(_iso_to_ms(ts)) if ts else "unknown"
        session_dates.add(date_key)
        role = msg.get("role", "")
        if role == "assistant":
            daily[date_key]["request_count"] += 1

        model = msg.get("model")
        if model:
            daily[date_key]["models_used"].add(model)

        content_blocks = msg.get("content_blocks")
        messages.append(
            {
                "date": date_key,
                "tool_name": TOOL_NAME,
                "host_name": hostname,
                "message_id": msg.get("uuid") or f"zcode-{session_id[:8]}-{date_key}",
                "parent_id": None,
                "role": role,
                "content": msg.get("content", "") or "",
                "content_blocks": content_blocks,
                "full_entry": json.dumps(
                    {"session_id": session_id, "role": role, "model": model},
                    ensure_ascii=False,
                ),
                # Per-message tokens left at 0; session totals are injected
                # into the first assistant message below.
                "tokens_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "model": model,
                "timestamp": ts,
                "sender_id": system_account or "zcode_user",
                "sender_name": sender_name,
                "agent_session_id": session_id,
                "conversation_id": None,
                "project_path": session.project_path,
            }
        )

    # Attribute the authoritative session-level token totals to the dominant
    # date (the date with the most messages; ties broken by recency). This
    # keeps daily_usage aligned with the session totals rather than fragmenting
    # sparse per-message numbers.
    total_input = int(getattr(session, "total_input_tokens", 0) or 0)
    total_output = int(getattr(session, "total_output_tokens", 0) or 0)
    total_tokens = total_input + total_output
    dominant_date = _dominant_date(session_dates, messages)
    daily[dominant_date]["prompt_tokens"] += total_input
    daily[dominant_date]["candidates_tokens"] += total_output
    daily[dominant_date]["total_tokens"] += total_tokens
    # NOTE: request_count is NOT added here — it is incremented per assistant
    # message on its own date in the loop above (matches fetch_codex.py), so a
    # session reports its real assistant-turn count rather than collapsing to 1.

    # Inject session-level totals into the first assistant message so
    # update_agent_sessions_stats records the real total_tokens (codex pattern,
    # fetch_codex.py:648-654).
    if total_tokens > 0:
        for msg in messages:
            if msg.get("role") == "assistant":
                msg["tokens_used"] = total_tokens
                msg["input_tokens"] = total_input
                msg["output_tokens"] = total_output
                break

    return daily, messages, session.project_path


def _dominant_date(dates: set[str], messages: list[dict[str, Any]]) -> str:
    """Pick the date with the most messages, ties broken by recency."""
    if not dates:
        return "unknown"
    counts: dict[str, int] = defaultdict(int)
    last_ts: dict[str, str] = {}
    for msg in messages:
        d = msg.get("date", "unknown")
        counts[d] += 1
        ts = msg.get("timestamp") or ""
        if ts >= last_ts.get(d, ""):
            last_ts[d] = ts
    return max(counts, key=lambda d: (counts[d], last_ts.get(d, "")))


def _iso_to_ms(ts: Optional[str]) -> Optional[int]:
    """Best-effort convert an ISO-8601 timestamp to epoch milliseconds."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _resolve_user_id_from_sender(cursor, sender_name: str, all_users_cache: list) -> Optional[int]:
    """Resolve user_id from sender_name (format: {user}-{hostname}-{tool}).

    Identical to fetch_codex._resolve_user_id_from_sender: try rsplit first,
    then longest-prefix match against all users.

    Args:
        cursor: Database cursor.
        sender_name: Sender name string, e.g. 'rhuang-Host-zcode'.
        all_users_cache: Cached list of all user rows.

    Returns:
        user_id or None.
    """
    if not sender_name:
        return None

    from shared.db import _execute, _placeholder

    placeholder = _placeholder()

    # Try rsplit first (works when hostname has no hyphens)
    candidate = sender_name.rsplit("-", 2)[0]
    user_sql = (
        f"SELECT id FROM users WHERE system_account = {placeholder} OR username = {placeholder}"
    )
    _execute(cursor, user_sql, (candidate, candidate))
    user_row = cursor.fetchone()
    if user_row:
        return user_row["id"] if isinstance(user_row, dict) else user_row[0]

    # Fallback: longest-prefix match against all users
    candidates = sorted(
        ((u["id"], f) for u in all_users_cache for f in (u["system_account"], u["username"]) if f),
        key=lambda x: len(x[1]),
        reverse=True,
    )
    for uid, field in candidates:
        if sender_name.startswith(field + "-"):
            return uid

    return None


def update_agent_sessions_stats(messages: list) -> int:
    """Update agent_sessions / session_messages from collected ZCode messages.

    Mirrors ``fetch_codex.update_agent_sessions_stats``. Groups messages by
    agent_session_id, upserts agent_sessions (tool_name='zcode',
    session_type='session', status='completed'), and inserts messages into
    session_messages (dedup by session_id + role + timestamp).

    Args:
        messages: List of message dicts with agent_session_id and tokens_used.

    Returns:
        Number of sessions updated/inserted.
    """
    from shared.db import _column_exists, _execute, _placeholder, escape_like, get_connection

    session_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "message_count": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models": set(),
            "messages": [],
            "last_timestamp": None,
            # Model on the message with the strictly-greatest timestamp
            # (most-recently-used); see update_session_last_seen. NOT lexicographic.
            "last_model": None,
            "project_path": None,
            "seen_message_ids": set(),
            "seen_request_ids": set(),
        }
    )

    for index, msg in enumerate(messages):
        sid = msg.get("agent_session_id")
        if not sid:
            continue

        role = msg.get("role", "")
        tokens = msg.get("tokens_used", 0) or 0
        model = msg.get("model")
        msg_id = msg.get("message_id")
        message_identity = f"{role}:{msg_id}" if msg_id else f"message_row:{sid}:{index}"
        if message_identity not in session_stats[sid]["seen_message_ids"]:
            session_stats[sid]["seen_message_ids"].add(message_identity)
            session_stats[sid]["message_count"] += 1
            session_stats[sid]["total_tokens"] += tokens

        if role == "assistant":
            request_identity = f"message_id:{msg_id}" if msg_id else f"assistant_row:{sid}:{index}"
            if request_identity not in session_stats[sid]["seen_request_ids"]:
                session_stats[sid]["seen_request_ids"].add(request_identity)
                session_stats[sid]["request_count"] += 1

        if model:
            session_stats[sid]["models"].add(model)

        session_stats[sid]["messages"].append(msg)

        if not session_stats[sid]["project_path"] and msg.get("project_path"):
            session_stats[sid]["project_path"] = msg["project_path"]

        # Advance last-seen timestamp + model together (shared logic).
        update_session_last_seen(session_stats[sid], msg.get("timestamp"), model)

    if not session_stats:
        return 0

    updated = 0
    messages_inserted = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    placeholder = _placeholder()

    conn = get_connection()
    cursor = conn.cursor()
    has_external_message_id = _column_exists(cursor, "session_messages", "external_message_id")
    has_source = _column_exists(cursor, "session_messages", "source")
    has_structured_session_messages = has_external_message_id and has_source

    _all_users_cache: list = []

    def _get_all_users() -> list:
        if not _all_users_cache:
            _execute(cursor, "SELECT id, system_account, username FROM users")
            _all_users_cache.extend(cursor.fetchall())
        return _all_users_cache

    try:
        for session_id, stats in session_stats.items():
            try:
                check_sql = (
                    f"SELECT id, user_id, session_type FROM agent_sessions "
                    f"WHERE session_id = {placeholder}"
                )
                _execute(cursor, check_sql, (session_id,))
                session_row = cursor.fetchone()
                _is_new_session = False

                session_type = ""
                if session_row:
                    session_type = (
                        session_row["session_type"]
                        if isinstance(session_row, dict) or hasattr(session_row, "keys")
                        else session_row[2]
                    ) or ""
                if session_type == "workflow":
                    continue

                if not session_row:
                    first_msg = stats["messages"][0] if stats["messages"] else {}

                    host_name = first_msg.get("host_name", "localhost")
                    sender_name = first_msg.get("sender_name", "")
                    project_path = stats["project_path"] or first_msg.get("project_path", "")

                    user_id = _resolve_user_id_from_sender(cursor, sender_name, _get_all_users())

                    # Strip the "sess_" prefix before building the short id so the
                    # title matches the frontend's default-title pattern
                    # (^[a-z]+ - [a-f0-9]{8}$), same as claude/codex.
                    short_id = session_id[5:] if session_id.startswith("sess_") else session_id
                    title = f"zcode - {short_id[:8]}"

                    insert_sql = f"""
                        INSERT INTO agent_sessions
                        (session_id, session_type, title, tool_name, host_name, user_id, status, project_path,
                         message_count, total_tokens, request_count, model, created_at, updated_at)
                        VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                {placeholder}, {placeholder}, {placeholder}, {placeholder})
                    """
                    model = stats["last_model"]
                    _execute(
                        cursor,
                        insert_sql,
                        (
                            session_id,
                            "session",
                            title,
                            TOOL_NAME,
                            host_name,
                            user_id,
                            "completed",
                            project_path,
                            stats["message_count"],
                            stats["total_tokens"],
                            stats["request_count"],
                            model,
                            now,
                            now,
                        ),
                    )
                    updated += 1
                    _is_new_session = True

                if not _is_new_session:
                    model = stats["last_model"]
                    session_updated_at = stats["last_timestamp"] or now
                    sql = f"""
                        UPDATE agent_sessions
                        SET message_count = CASE WHEN COALESCE(message_count, 0) > {placeholder} THEN COALESCE(message_count, 0) ELSE {placeholder} END,
                            total_tokens = CASE WHEN COALESCE(total_tokens, 0) > {placeholder} THEN COALESCE(total_tokens, 0) ELSE {placeholder} END,
                            request_count = CASE WHEN COALESCE(request_count, 0) > {placeholder} THEN COALESCE(request_count, 0) ELSE {placeholder} END,
                            model = COALESCE(model, {placeholder}),
                            updated_at = {placeholder}
                        WHERE session_id = {placeholder}
                    """
                    _execute(
                        cursor,
                        sql,
                        (
                            stats["message_count"],
                            stats["message_count"],
                            stats["total_tokens"],
                            stats["total_tokens"],
                            stats["request_count"],
                            stats["request_count"],
                            model,
                            session_updated_at,
                            session_id,
                        ),
                    )

                    if cursor.rowcount > 0:
                        updated += 1

                # Insert messages into session_messages table (dedup by role + timestamp)
                for msg in stats["messages"]:
                    try:
                        msg_id = msg.get("message_id")
                        timestamp = msg.get("timestamp") or now

                        if msg_id and has_external_message_id:
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND external_message_id = {placeholder}
                            """
                            _execute(cursor, check_sql, (session_id, msg.get("role"), str(msg_id)))
                        elif msg_id:
                            escaped_msg_id = escape_like(str(msg_id))
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND metadata LIKE {placeholder}
                                ESCAPE '\\'
                            """
                            _execute(
                                cursor,
                                check_sql,
                                (
                                    session_id,
                                    msg.get("role"),
                                    f'%"message_id": "{escaped_msg_id}"%',
                                ),
                            )
                        else:
                            check_sql = f"""
                                SELECT id, content FROM session_messages
                                WHERE session_id = {placeholder}
                                AND role = {placeholder}
                                AND timestamp = {placeholder}
                            """
                            _execute(cursor, check_sql, (session_id, msg.get("role"), timestamp))
                        existing = cursor.fetchone()

                        if not existing:
                            metadata = {
                                "message_id": msg_id,
                                "project_path": msg.get("project_path"),
                                "source": "fetch_zcode",
                                "external_message_id": str(msg_id) if msg_id else "",
                                "content_blocks": msg.get("content_blocks"),
                            }
                            if has_structured_session_messages:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp,
                                     source_timestamp, metadata, milestone_id, source,
                                     external_message_id, content_blocks)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
                                _execute(
                                    cursor,
                                    insert_sql,
                                    (
                                        session_id,
                                        msg.get("role"),
                                        msg.get("content", ""),
                                        msg.get("tokens_used", 0) or 0,
                                        msg.get("model"),
                                        timestamp,
                                        timestamp,
                                        json.dumps(metadata, ensure_ascii=False),
                                        "",
                                        "fetch_zcode",
                                        str(msg_id) if msg_id else "",
                                        (
                                            json.dumps(
                                                msg.get("content_blocks"), ensure_ascii=False
                                            )
                                            if msg.get("content_blocks")
                                            else None
                                        ),
                                    ),
                                )
                            elif has_source:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp, metadata, source)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
                                _execute(
                                    cursor,
                                    insert_sql,
                                    (
                                        session_id,
                                        msg.get("role"),
                                        msg.get("content", ""),
                                        msg.get("tokens_used", 0) or 0,
                                        msg.get("model"),
                                        timestamp,
                                        json.dumps(metadata, ensure_ascii=False),
                                        "fetch_zcode",
                                    ),
                                )
                            else:
                                insert_sql = f"""
                                    INSERT INTO session_messages
                                    (session_id, role, content, tokens_used, model, timestamp, metadata)
                                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                                """
                                _execute(
                                    cursor,
                                    insert_sql,
                                    (
                                        session_id,
                                        msg.get("role"),
                                        msg.get("content", ""),
                                        msg.get("tokens_used", 0) or 0,
                                        msg.get("model"),
                                        timestamp,
                                        json.dumps(metadata, ensure_ascii=False),
                                    ),
                                )
                            messages_inserted += 1
                        else:
                            # Observability (#723): warn if a skipped dup line
                            # carries text the stored row lacks (would be lost).
                            warn_if_skipped_message_has_text(
                                existing, msg, session_id, msg_id, "fetch_zcode"
                            )
                    except Exception as e:
                        print(f"  Warning: Failed to insert message for {session_id}: {e}")

            except Exception as e:
                print(f"  Warning: Failed to update session {session_id}: {e}")

        conn.commit()

    finally:
        cursor.close()
        conn.close()

    print(f"  Updated {updated} session statistics, inserted {messages_inserted} messages")
    return updated


def _iter_candidate_sessions(db_path: Path, days: int, recent: bool) -> list[tuple[str, int]]:
    """Return [(session_id, time_updated_ms)] candidates from a ZCode DB.

    Filters by --days and --recent (only sessions updated today), using the
    ``time_updated`` epoch-millisecond column. Opens the DB read-only (URI mode)
    so we never block ZCode's WAL writer — same approach as ZcodeSession._connect.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"  Cannot open ZCode DB {db_path}: {e}")
        return []

    cutoff_ms = 0
    if days > 0:
        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    if recent:
        # Use local midnight to match the sibling fetch scripts
        # (fetch_codex.py:594-598 uses local-naive midnight), so --recent syncs
        # align with the user's notion of "today" rather than UTC rollover.
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_ms = max(cutoff_ms, int(today_start.timestamp() * 1000))

    candidates: list[tuple[str, int]] = []
    try:
        rows = conn.execute(CANDIDATE_SQL).fetchall()
        for session_id, time_updated, _time_created in rows:
            updated_ms = time_updated or 0
            if cutoff_ms and updated_ms and updated_ms < cutoff_ms:
                continue
            candidates.append((session_id, updated_ms))
    except sqlite3.DatabaseError as e:
        print(f"  ZCode DB query failed for {db_path}: {e}")
    finally:
        conn.close()

    return candidates


def fetch_and_save(
    days: int = 7,
    hostname: Optional[str] = None,
    multi_user_mode: bool = False,
    recent: bool = False,
) -> bool:
    """Fetch ZCode usage and save to database.

    Args:
        days: Number of days to look back (based on session time_updated).
        hostname: Optional host name to identify this machine.
        multi_user_mode: If True, scan all users' ZCode DBs.
        recent: If True, only process sessions updated today.

    Returns:
        True if successful, False otherwise.
    """
    from shared import db as db_mod
    from shared import utils

    if hostname is None:
        config = utils.load_config()
        hostname = config.get("host_name", "localhost")

    aggregated: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )

    all_messages: list[dict[str, Any]] = []

    # Discover ZCode DBs to scan.
    db_targets: list[tuple[Optional[str], Path]] = []
    if multi_user_mode:
        print("Multi-user mode: scanning all users' ZCode databases...")
        db_targets = find_all_zcode_db_paths()
        if not db_targets:
            print("No ZCode databases found for any user.")
            return False
        print(f"Found {len(db_targets)} users with ZCode data:")
        for system_account, db_path in db_targets:
            print(f"  - {system_account}: {db_path}")
    else:
        db_path = find_zcode_db_path()
        if not db_path:
            print("Error: Cannot find ZCode DB (~/.zcode/cli/db/db.sqlite).")
            return False
        db_targets = [(None, db_path)]

    total_sessions = 0
    for system_account, db_path in db_targets:
        label = system_account or "current user"
        print(f"\nProcessing ZCode DB for {label}: {db_path}")
        candidates = _iter_candidate_sessions(db_path, days, recent)
        print(f"  Found {len(candidates)} candidate session(s)")

        for session_id, _updated_ms in candidates:
            daily, messages, _project = process_zcode_session(
                session_id, db_path, hostname, system_account
            )
            if not messages:
                continue

            total_sessions += 1
            for date, stats in daily.items():
                aggregated[date]["prompt_tokens"] += stats["prompt_tokens"]
                aggregated[date]["candidates_tokens"] += stats["candidates_tokens"]
                aggregated[date]["total_tokens"] += stats["total_tokens"]
                aggregated[date]["request_count"] += stats["request_count"]
                aggregated[date]["models_used"].update(stats["models_used"])
            all_messages.extend(messages)

    print(f"\nProcessed {total_sessions} sessions, {len(all_messages)} messages")

    # Save daily_usage.
    saved = 0
    for date, stats in aggregated.items():
        if date and date != "unknown":
            total = stats["total_tokens"]
            if db_mod.save_usage(
                date=date,
                tool_name=TOOL_NAME,
                host_name=hostname,
                tokens_used=total,
                input_tokens=stats["prompt_tokens"],
                output_tokens=stats["candidates_tokens"],
                cache_tokens=stats["cached_tokens"],
                request_count=stats["request_count"],
                models_used=sorted(stats["models_used"]),
            ):
                saved += 1
            print(f"  {date}: {total:,} tokens, {stats['request_count']} requests")

    print(f"\nSaved {saved} days of ZCode usage data")

    # Save messages (idempotent UPSERT into daily_messages).
    if all_messages:
        print("Saving messages to database...")
        saved_count = db_mod.save_messages_batch(all_messages, batch_size=500)
        print(f"Saved {saved_count} messages")

        # Update agent_sessions / session_messages (non-critical).
        try:
            print("Updating agent_sessions statistics...")
            update_agent_sessions_stats(all_messages)
        except Exception as e:
            print(f"Warning: Failed to update agent session stats: {e}")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch ZCode CLI session data")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back")
    parser.add_argument("--hostname", help="Host name to identify this machine")
    parser.add_argument(
        "--multi-user",
        action="store_true",
        help="Scan all users' ZCode databases (requires root/admin privileges)",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json file (useful when running as root via sudo)",
    )
    parser.add_argument(
        "--recent",
        action="store_true",
        help="Only process sessions updated today (for scheduler use)",
    )
    args = parser.parse_args()

    # If --config is specified, use it to get database URL
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            with open(config_path) as f:
                config_data = json.load(f)
            db_config = config_data.get("database", {})
            db_url = db_config.get("url")
            # Only set if not already configured (Docker provides DATABASE_URL)
            if db_url and not os.environ.get("DATABASE_URL"):
                os.environ["DATABASE_URL"] = db_url
                print(f"Using database from config: {db_config.get('type', 'postgresql')}")

    db.init_database()
    success = fetch_and_save(
        days=args.days,
        hostname=args.hostname,
        multi_user_mode=args.multi_user,
        recent=args.recent,
    )
    sys.exit(0 if success else 1)
