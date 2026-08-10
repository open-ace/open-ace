"""
Open ACE - Session History Sync (Issue #24)

Synchronizes remote/terminal workspace sessions from the database into the
per-user qwen-code-webui history directories, so the webui "view conversation
history" view also lists remote workspace sessions (previously it only showed
container-local sessions from ``~/.qwen/projects/*/chats/*.jsonl``).

How it works:
- For every active user, query remote/terminal sessions from ``agent_sessions``.
- For each session, read ``session_messages`` and write a qwen-compatible JSONL
  at ``<webui_home>/.qwen/projects/<encoded>/chats/<session_id>.jsonl``.
- The webui project list validates the *decoded* project path exists inside the
  container (``simpleDecodeProjectPath``). Remote Windows paths (e.g.
  ``C:\\workspace``) encode to ``C--workspace`` and decode (fallback) to
  ``/C//workspace``, so we create that mirror directory for the project to show
  up.
- System-context user messages (Issue #28) are filtered out so the generated
  history never leaks Qwen internal prompts.
"""

import json
import logging
import os
import re
import threading
import time
import uuid as uuid_mod

logger = logging.getLogger(__name__)

# Workspace types that run on a remote machine and need mirroring into the
# webui history view. Local sessions are written by the qwen CLI itself.
REMOTE_WORKSPACE_TYPES = ("remote", "terminal")

# Default interval for the background sync loop (web workers).
WEBUI_HISTORY_SYNC_INTERVAL = int(os.environ.get("WEBUI_HISTORY_SYNC_INTERVAL", "300"))
_sync_thread: threading.Thread | None = None


def encode_project_path(project_path: str) -> str:
    """Mirror qwen-code-webui ``encodeProjectPath``: strip trailing '/', then
    replace every char outside [a-zA-Z0-9] with '-'.

    ``C:\\workspace`` -> ``C--workspace``; ``/workspace/admin`` ->
    ``-workspace-admin``.
    """
    s = (project_path or "").rstrip("/")
    return "".join("-" if not c.isalnum() else c for c in s)


def encode_openace_path(project_path: str) -> str:
    """Mirror the qwen-code-webui SPA ``ko()`` encoding used in open-ace
    integrated mode: drop a leading drive letter, drop leading slashes, then
    replace every char outside [a-zA-Z0-9] with '-' and prepend '-'.

    ``C:\\workspace`` -> ``--workspace``; ``/workspace/admin`` ->
    ``-workspace-admin`` (identical to ``encode_project_path`` for POSIX
    paths, so callers deduplicate).
    """
    s = re.sub(r"^[A-Za-z]:", "", project_path or "")
    s = re.sub(r"^/+", "", s)
    s = "".join("-" if not c.isalnum() else c for c in s)
    return "-" + s


def simple_decode_project_path(encoded: str) -> str:
    """Mirror qwen-code-webui ``simpleDecodeProjectPath``.

    ``-workspace-admin`` -> ``/workspace/admin``; ``C--workspace`` ->
    ``/C//workspace`` (equivalent to ``/C/workspace``).
    """
    decoded = encoded
    if decoded.startswith("-"):
        decoded = decoded[1:]
    decoded = decoded.replace("-", "/")
    return "/" + decoded


def _fetch_users() -> list[tuple[int, str]]:
    """Return [(user_id, system_account)] for active users."""
    from app.repositories.database import Database

    db = Database()
    rows = db.fetch_all(
        "SELECT id, system_account FROM users "
        "WHERE is_active AND system_account IS NOT NULL AND system_account != '' "
        "ORDER BY id"
    )
    return [(int(r["id"]), str(r["system_account"])) for r in rows]


def _fetch_remote_sessions(user_id: int) -> list[dict]:
    """Return remote/terminal sessions (with project_path) for a user."""
    from app.repositories.database import Database, adapt_sql

    db = Database()
    rows = db.fetch_all(
        adapt_sql(
            "SELECT session_id, project_path, tool_name FROM agent_sessions "
            "WHERE user_id = ? AND workspace_type IN ('remote', 'terminal') "
            "AND project_path IS NOT NULL AND project_path != ''"
        ),
        (user_id,),
    )
    return [dict(r) for r in rows]


def _fetch_messages(session_id: str) -> list[dict]:
    """Return transcript messages for a session, oldest first."""
    from app.repositories.database import Database, adapt_sql

    db = Database()
    rows = db.fetch_all(
        adapt_sql(
            "SELECT role, content, timestamp, external_message_id FROM session_messages "
            "WHERE session_id = ? AND content IS NOT NULL AND content != '' "
            "ORDER BY timestamp ASC"
        ),
        (session_id,),
    )
    return [dict(r) for r in rows]


def _format_ts(ts) -> str:
    """Normalise a DB timestamp to the ISO string the webui can parse."""
    ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    if ts_iso.endswith("+00:00"):
        ts_iso = ts_iso[:-6] + "Z"
    elif not ts_iso.endswith("Z"):
        ts_iso += "Z"
    return ts_iso


def _build_jsonl(session_id: str, project_path: str, messages) -> list[str]:
    """Build qwen-compatible JSONL lines, filtering system-context (Issue #28)."""
    from scripts.shared.qwen_context import is_qwen_system_context

    lines = []
    for m in messages:
        role = m.get("role") or ""
        content = m.get("content") or ""
        if role == "user" and is_qwen_system_context(content):
            continue
        if role not in ("user", "assistant"):
            continue
        msg_uuid = m.get("external_message_id") or str(uuid_mod.uuid4())
        entry = {
            "uuid": msg_uuid,
            "parentUuid": None,
            "sessionId": session_id,
            "timestamp": _format_ts(m.get("timestamp")),
            "type": role,
            "cwd": project_path,
            "message": {"role": role, "parts": [{"text": content}]},
        }
        if role == "assistant":
            entry["message"]["id"] = msg_uuid
        lines.append(json.dumps(entry, ensure_ascii=False))
    return lines


def sync_remote_sessions_to_webui() -> dict:
    """Sync remote/terminal sessions from DB into per-user webui history dirs.

    Returns a small stats dict: {users, sessions, files_written, errors}.
    Only writes (idempotent overwrite); never deletes files so the qwen CLI's
    own local-session JSONL files are never touched.
    """
    stats = {"users": 0, "sessions": 0, "files_written": 0, "errors": 0}
    for user_id, system_account in _fetch_users():
        home = f"/home/{system_account}"
        if not os.path.isdir(home):
            logger.warning("session_history_sync: home %s missing for user %s", home, user_id)
            continue
        stats["users"] += 1
        for sess in _fetch_remote_sessions(user_id):
            session_id = sess["session_id"]
            project_path = sess.get("project_path") or ""
            try:
                # Write under every encoding the webui frontend may derive for
                # this project: the webui standard ``encodeProjectPath`` (e.g.
                # ``C--workspace``) and the open-ace integrated-mode SPA
                # ``ko()`` encoding (e.g. ``--workspace``). POSIX paths encode
                # identically, so deduplicate.
                encodings = sorted(
                    {
                        e
                        for e in (
                            encode_project_path(project_path),
                            encode_openace_path(project_path),
                        )
                        if e
                    }
                )
                if not encodings:
                    continue
                lines = _build_jsonl(session_id, project_path, _fetch_messages(session_id))
                if not lines:
                    continue
                std_encoded = encode_project_path(project_path)
                for encoded in encodings:
                    # Non-'/'-rooted projects (remote Windows paths) decode to a
                    # path that must exist in the container for the webui
                    # project list; create the mirror directory once.
                    if encoded == std_encoded and not encoded.startswith("-"):
                        try:
                            os.makedirs(simple_decode_project_path(encoded), exist_ok=True)
                        except OSError as e:
                            logger.warning(
                                "session_history_sync: mkdir mirror for %s failed: %s",
                                encoded,
                                e,
                            )
                    chats_dir = f"{home}/.qwen/projects/{encoded}/chats"
                    os.makedirs(chats_dir, exist_ok=True)
                    path = f"{chats_dir}/{session_id}.jsonl"
                    with open(path, "w", encoding="utf8") as f:
                        f.write("\n".join(lines) + "\n")
                stats["files_written"] += len(encodings)
                stats["sessions"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.warning(
                    "session_history_sync: failed for session %s: %s",
                    session_id[:8],
                    e,
                )
    logger.info("session_history_sync done: %s", stats)
    return stats


def _sync_loop(interval: int) -> None:
    """Background loop: sync immediately, then every ``interval`` seconds."""
    while True:
        try:
            sync_remote_sessions_to_webui()
        except Exception as e:  # pragma: no cover - loop must never die
            logger.warning("session_history_sync: loop iteration failed: %s", e)
        time.sleep(max(30, interval))


def start_webui_history_sync_loop(interval: int = WEBUI_HISTORY_SYNC_INTERVAL) -> None:
    """Start the background webui-history sync loop (idempotent).

    Runs in web workers as well (Issue #24): the sync is lightweight and
    idempotent, so a single dedicated daemon thread keeps the webui history
    current without depending on the scheduler worker (SCHEDULER_MODE=web
    does not start the DataFetchScheduler). In scheduler mode the same sync
    also runs via ``DataFetchScheduler._run_fetch``; both paths are safe to
    overlap.
    """
    global _sync_thread
    if _sync_thread is not None and _sync_thread.is_alive():
        return
    _sync_thread = threading.Thread(
        target=_sync_loop,
        args=(interval,),
        daemon=True,
        name="webui-history-sync",
    )
    _sync_thread.start()
    logger.info("webui history sync loop started (interval=%ss)", interval)
