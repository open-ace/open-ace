"""
Open ACE Remote Agent - Claude Code Session Sync

Monitors ~/.claude/projects/ for session JSONL files created by Claude Code
running in the web terminal, parses session metadata and messages, and syncs
them to the Open-ACE server so they appear in the session history UI.

Runs as a background thread inside the agent daemon.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_adapters.codex_jsonl_parser import extract_codex_content_blocks, extract_codex_text

logger = logging.getLogger("openace-agent.session-sync")

# How often to scan for new/changed sessions (seconds)
SCAN_INTERVAL = 30

# Directories to watch
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

QWEN_DIR = Path.home() / ".qwen"
QWEN_PROJECTS_DIR = QWEN_DIR / "projects"

CODEX_DIR = Path.home() / ".codex"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"

# ZCode stores sessions in a SQLite database (not JSONL files like the other CLIs).
ZCODE_DIR = Path.home() / ".zcode"
ZCODE_DB_PATH = ZCODE_DIR / "cli" / "db" / "db.sqlite"
ZCODE_SYNC_TOOL_NAME = "zcode"

# File to track which sessions have already been synced
SYNC_STATE_FILE = Path.home() / ".open-ace-agent" / "session_sync_state.json"
ACTIVE_TERMINAL_FILE = Path.home() / ".open-ace-agent" / "active_terminal.json"
ACTIVE_TERMINAL_TTL_SECONDS = 12 * 3600


def _extract_text_only(content: Any) -> str:
    """Extract plain text from content for backward-compat display."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)
    return str(content) if content else ""


def _extract_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Extract full content blocks preserving structure (tool_use, thinking, tool_result)."""
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type in ("text", "tool_use", "thinking", "tool_result"):
                    blocks.append(block)
        return blocks
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content}]
    return []


class ClaudeSession:
    """Parsed Claude Code session from a JSONL file."""

    def __init__(self, session_id: str, jsonl_path: str):
        self.session_id = session_id
        self.jsonl_path = jsonl_path
        self.messages: list[dict[str, Any]] = []
        self.model: str | None = None
        self.project_path: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.message_count = 0
        self.first_timestamp: str | None = None
        self.last_timestamp: str | None = None

    def parse(self) -> bool:
        """Parse the JSONL file and extract metadata. Returns True if parseable."""
        try:
            with open(self.jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    self._process_entry(entry)

            # Extract project path from directory structure
            # ~/.claude/projects/<encoded-project-path>/chats/<session-id>.jsonl
            parts = Path(self.jsonl_path).parts
            try:
                projects_idx = parts.index("projects")
                if projects_idx + 1 < len(parts):
                    encoded = parts[projects_idx + 1]
                    # Decode project path: hyphens may represent special chars
                    self.project_path = encoded
            except (ValueError, IndexError):
                pass

            return self.message_count > 0

        except OSError as e:
            logger.debug("Failed to parse %s: %s", self.jsonl_path, e)
            return False

    def _process_entry(self, entry: dict[str, Any]) -> None:
        """Process a single JSONL entry.

        Claude Code JSONL uses type "user"/"assistant" (not "message")
        with fields nested under entry.message:
          entry.message.role, entry.message.content (list of blocks),
          entry.message.model, entry.message.usage
        """
        entry_type = entry.get("type")

        if entry_type in ("user", "assistant"):
            message = entry.get("message", {})
            if not isinstance(message, dict):
                return

            role = message.get("role", entry_type)
            if role not in ("user", "assistant"):
                return

            content_raw = message.get("content", "")
            content_blocks = _extract_content_blocks(content_raw)
            text_content = _extract_text_only(content_raw)

            self.message_count += 1
            msg: dict[str, Any] = {
                "role": role,
                "content": text_content,
                "content_blocks": content_blocks or None,
                "timestamp": entry.get("timestamp"),
                "model": message.get("model"),
                "uuid": entry.get("uuid"),
            }

            usage = message.get("usage")
            if isinstance(usage, dict) and role == "assistant":
                input_t = usage.get("input_tokens", 0)
                output_t = usage.get("output_tokens", 0)
                self.total_input_tokens += input_t
                self.total_output_tokens += output_t
                msg["usage"] = {"input_tokens": input_t, "output_tokens": output_t}

            self.messages.append(msg)

            ts = entry.get("timestamp")
            if ts:
                if not self.first_timestamp:
                    self.first_timestamp = ts
                self.last_timestamp = ts

            if not self.model and message.get("model"):
                self.model = message["model"]

        elif entry_type == "summary":
            pass

    def to_sync_payload(self, machine_id: str, terminal_id: str) -> dict[str, Any]:
        """Convert to the payload expected by the Open-ACE session-sync endpoint."""
        return {
            "session_id": self.session_id,
            "machine_id": machine_id,
            "terminal_id": terminal_id,
            "tool_name": "claude-code",
            "project_path": self.project_path,
            "model": self.model,
            "message_count": self.message_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "source": os.environ.get("OPEN_ACE_TERMINAL_SOURCE", "web_terminal"),
            "messages": self.messages,
        }


def _extract_qwen_text(parts: list[dict[str, Any]]) -> str:
    """Extract plain text from Qwen message parts."""
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "text" in part:
            texts.append(part.get("text", ""))
    return "\n".join(texts)


def _extract_qwen_content_blocks(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract structured content blocks from Qwen message parts."""
    blocks: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            blocks.append({"type": "thinking", "text": part.get("text", "")})
        elif "functionCall" in part:
            fc = part["functionCall"]
            blocks.append(
                {
                    "type": "tool_use",
                    "name": fc.get("name"),
                    "input": fc.get("args"),
                }
            )
        elif "functionResponse" in part:
            fr = part["functionResponse"]
            blocks.append(
                {
                    "type": "tool_result",
                    "name": fr.get("name"),
                    "output": fr.get("response"),
                }
            )
        elif "text" in part:
            blocks.append({"type": "text", "text": part["text"]})
    return blocks


class QwenSession:
    """Parsed Qwen Code session from a JSONL file."""

    def __init__(self, session_id: str, jsonl_path: str):
        self.session_id = session_id
        self.jsonl_path = jsonl_path
        self.messages: list[dict[str, Any]] = []
        self.model: str | None = None
        self.project_path: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.message_count = 0
        self.first_timestamp: str | None = None
        self.last_timestamp: str | None = None

    def parse(self) -> bool:
        """Parse the JSONL file and extract metadata. Returns True if parseable."""
        try:
            with open(self.jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    self._process_entry(entry)

            # Extract project path from directory structure
            # ~/.qwen/projects/<encoded-project-path>/chats/<session-id>.jsonl
            parts = Path(self.jsonl_path).parts
            try:
                projects_idx = parts.index("projects")
                if projects_idx + 1 < len(parts):
                    self.project_path = parts[projects_idx + 1]
            except (ValueError, IndexError):
                pass

            return self.message_count > 0

        except OSError as e:
            logger.debug("Failed to parse %s: %s", self.jsonl_path, e)
            return False

    def _process_entry(self, entry: dict[str, Any]) -> None:
        """Process a single Qwen JSONL entry.

        Qwen JSONL uses type "user"/"assistant"/"tool_result"/"system"
        with message.parts (not message.content) and top-level usageMetadata.
        Assistant role is "model" (not "assistant").
        """
        entry_type = entry.get("type")

        if entry_type in ("user", "assistant"):
            message = entry.get("message", {})
            if not isinstance(message, dict):
                return

            role = (
                "assistant" if message.get("role") == "model" else message.get("role", entry_type)
            )
            if role not in ("user", "assistant"):
                return

            parts = message.get("parts", [])
            if not isinstance(parts, list):
                parts = []
            text_content = _extract_qwen_text(parts)
            content_blocks = _extract_qwen_content_blocks(parts)

            self.message_count += 1
            msg: dict[str, Any] = {
                "role": role,
                "content": text_content,
                "content_blocks": content_blocks or None,
                "timestamp": entry.get("timestamp"),
                "model": entry.get("model"),
                "uuid": entry.get("uuid"),
            }

            if entry_type == "assistant":
                usage_meta = entry.get("usageMetadata")
                if isinstance(usage_meta, dict):
                    # Deduct cached tokens to avoid inflating input count
                    # (matches fetch_qwen.py actual_input_tokens logic)
                    prompt_tokens = usage_meta.get("promptTokenCount", 0)
                    cached_tokens = usage_meta.get("cachedContentTokenCount", 0)
                    input_t = max(0, prompt_tokens - cached_tokens)
                    output_t = usage_meta.get("candidatesTokenCount", 0)
                    self.total_input_tokens += input_t
                    self.total_output_tokens += output_t
                    msg["usage"] = {"input_tokens": input_t, "output_tokens": output_t}

            self.messages.append(msg)

            ts = entry.get("timestamp")
            if ts:
                if not self.first_timestamp:
                    self.first_timestamp = ts
                self.last_timestamp = ts

            if not self.model and entry.get("model"):
                self.model = entry["model"]

        elif entry_type == "tool_result":
            message = entry.get("message", {})
            if not isinstance(message, dict):
                return

            parts = message.get("parts", [])
            if not isinstance(parts, list):
                parts = []
            content_blocks = _extract_qwen_content_blocks(parts)
            text_content = _extract_qwen_text(parts)

            self.message_count += 1
            msg = {
                "role": "system",  # map to system for frontend compatibility
                "content": text_content,
                "content_blocks": content_blocks or None,
                "timestamp": entry.get("timestamp"),
                "model": entry.get("model"),
                "uuid": entry.get("uuid"),
            }
            self.messages.append(msg)

            ts = entry.get("timestamp")
            if ts:
                if not self.first_timestamp:
                    self.first_timestamp = ts
                self.last_timestamp = ts

    def to_sync_payload(self, machine_id: str, terminal_id: str) -> dict[str, Any]:
        """Convert to the payload expected by the Open-ACE session-sync endpoint."""
        return {
            "session_id": self.session_id,
            "machine_id": machine_id,
            "terminal_id": terminal_id,
            "tool_name": "qwen-code",
            "project_path": self.project_path,
            "model": self.model,
            "message_count": self.message_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "source": os.environ.get("OPEN_ACE_TERMINAL_SOURCE", "web_terminal"),
            "messages": self.messages,
        }


class CodexSession:
    """Parsed Codex CLI session from a JSONL file."""

    def __init__(self, session_id: str, jsonl_path: str):
        self.session_id = session_id
        self.jsonl_path = jsonl_path
        self.messages: list[dict[str, Any]] = []
        self.model: str | None = None
        self.project_path: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.message_count = 0
        self.first_timestamp: str | None = None
        self.last_timestamp: str | None = None

    def parse(self) -> bool:
        """Parse the Codex JSONL file and extract metadata."""
        try:
            events = []
            with open(self.jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            # First pass: extract session_meta and token counts
            accumulated_tokens = {"input": 0, "output": 0}
            for event in events:
                etype = event.get("type", "")
                payload = event.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                if etype == "session_meta":
                    self.session_id = payload.get("id", self.session_id)
                    self.project_path = payload.get("cwd", self.project_path)

                elif etype == "turn_context":
                    m = payload.get("model")
                    if m:
                        self.model = m

                elif etype == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info", {})
                    if isinstance(info, dict):
                        last_usage = info.get("last_token_usage", {})
                        if isinstance(last_usage, dict):
                            accumulated_tokens["input"] += last_usage.get("input_tokens", 0)
                            accumulated_tokens["output"] += last_usage.get("output_tokens", 0)

            self.total_input_tokens = accumulated_tokens["input"]
            self.total_output_tokens = accumulated_tokens["output"]

            # Second pass: extract messages
            for event in events:
                etype = event.get("type", "")
                payload = event.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                ts = event.get("timestamp", "")

                if etype == "response_item":
                    self._process_response_item(payload, ts)
                elif etype == "event_msg":
                    msg_type = payload.get("type", "")
                    if msg_type == "user_message":
                        text = payload.get("content", "")
                        if text:
                            self._add_message("user", text, [{"type": "text", "text": text}], ts)
                    elif msg_type == "agent_message":
                        text = payload.get("content", "")
                        if text:
                            self._add_message(
                                "assistant", text, [{"type": "text", "text": text}], ts
                            )

            return self.message_count > 0

        except OSError as e:
            logger.debug("Failed to parse Codex session %s: %s", self.jsonl_path, e)
            return False

    def _process_response_item(self, payload: dict[str, Any], ts: str) -> None:
        """Process a Codex response_item event."""
        ptype = payload.get("type", "")
        role = None

        if ptype == "message":
            msg_role = payload.get("role", "")
            if msg_role == "user":
                role = "user"
            elif msg_role == "assistant":
                role = "assistant"
            else:
                logger.debug("Skipping response_item with unknown role: %s", msg_role)
                return
        elif ptype in ("function_call", "custom_tool_call"):
            role = "assistant"
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            role = "system"
        elif ptype == "reasoning":
            summary = payload.get("summary", [])
            if not summary:
                return
            role = "assistant"
        else:
            logger.debug("Skipping response_item with unknown ptype: %s", ptype)
            return

        text = extract_codex_text(payload)
        blocks = extract_codex_content_blocks(payload)
        self._add_message(role, text, blocks or None, ts)

    def _add_message(self, role: str, content: str, content_blocks, ts: str) -> None:
        """Add a parsed message."""
        self.message_count += 1
        self.messages.append(
            {
                "role": role,
                "content": content,
                "content_blocks": content_blocks,
                "timestamp": ts,
                "model": self.model,
            }
        )
        if ts:
            if not self.first_timestamp:
                self.first_timestamp = ts
            self.last_timestamp = ts

    def to_sync_payload(self, machine_id: str, terminal_id: str) -> dict[str, Any]:
        """Convert to the payload expected by the Open-ACE session-sync endpoint."""
        return {
            "session_id": self.session_id,
            "machine_id": machine_id,
            "terminal_id": terminal_id,
            "tool_name": "codex",
            "project_path": self.project_path,
            "model": self.model,
            "message_count": self.message_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "source": os.environ.get("OPEN_ACE_TERMINAL_SOURCE", "web_terminal"),
            "messages": self.messages,
        }


class ZcodeSession:
    """Parsed ZCode session read from the ZCode SQLite database.

    Unlike the Claude/Qwen/Codex parsers (which read JSONL files), ZCode stores
    sessions relationally in ``~/.zcode/cli/db/db.sqlite``. This class queries
    the ``session``, ``message``, ``part`` and ``turn_usage`` tables to reconstruct
    a sync payload with the same shape the server expects.

    Only ``interactive`` sessions are parsed; ``subagent_child`` rows are skipped
    by the scanner.
    """

    def __init__(self, session_id: str, db_path: str | Path):
        self.session_id = session_id
        self.db_path = str(db_path)
        self.messages: list[dict[str, Any]] = []
        self.model: str | None = None
        self.project_path: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.message_count = 0
        self.first_timestamp: str | None = None
        self.last_timestamp: str | None = None

    def _connect(self) -> sqlite3.Connection:
        """Open a read-only connection to avoid blocking ZCode's WAL writer."""
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def parse(self) -> bool:
        """Query the DB and populate message/token metadata. Returns True if usable."""
        try:
            conn = self._connect()
        except sqlite3.Error as e:
            logger.debug("Cannot open ZCode DB %s: %s", self.db_path, e)
            return False

        try:
            # 1. Session metadata (directory, timestamps).
            row = conn.execute(
                "SELECT directory, time_created, time_updated " "FROM session WHERE id = ?",
                (self.session_id,),
            ).fetchone()
            if not row:
                return False
            self.project_path = row[0]
            created_ms = row[1]
            updated_ms = row[2]
            if created_ms:
                self.first_timestamp = _ms_to_iso(created_ms)
            if updated_ms:
                self.last_timestamp = _ms_to_iso(updated_ms)

            # 2. Messages ordered by time, with their text parts aggregated.
            msg_rows = conn.execute(
                "SELECT m.id, m.time_created, m.data, "
                "GROUP_CONCAT(p.data) AS parts_data "
                "FROM message m LEFT JOIN part p ON p.message_id = m.id "
                "WHERE m.session_id = ? "
                "GROUP BY m.id ORDER BY m.time_created",
                (self.session_id,),
            ).fetchall()

            for msg_id, time_created, data_json, parts_json in msg_rows:
                self._process_message(msg_id, time_created, data_json, parts_json)

            # 3. Authoritative token totals from turn_usage.
            tu = conn.execute(
                "SELECT COALESCE(SUM(input_tokens), 0), "
                "COALESCE(SUM(output_tokens), 0) "
                "FROM turn_usage WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            if tu:
                self.total_input_tokens = tu[0] or 0
                self.total_output_tokens = tu[1] or 0

            return self.message_count > 0
        except sqlite3.DatabaseError as e:
            logger.debug("Failed to parse ZCode session %s: %s", self.session_id[:8], e)
            return False
        finally:
            conn.close()

    def _process_message(
        self,
        msg_id: str,
        time_created: int,
        data_json: str | None,
        parts_json: str | None,
    ) -> None:
        """Build one sync message from a message row + its aggregated parts."""
        data: dict[str, Any] = {}
        if data_json:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                data = {}

        role = data.get("role")
        if role not in ("user", "assistant"):
            return

        # Concatenate text content from the message's parts.
        text_content = ""
        if parts_json:
            text_content = self._extract_parts_text(parts_json)

        # Fallback: some user messages carry inline text.
        if not text_content:
            inline = data.get("text") or data.get("content")
            if isinstance(inline, str):
                text_content = inline

        # Skip empty messages (server drops them anyway).
        if not text_content.strip():
            return

        model = data.get("modelID")
        if not model:
            model_obj = data.get("model")
            model = model_obj.get("modelId") if isinstance(model_obj, dict) else None
        timestamp = _ms_to_iso(time_created) if time_created else None

        msg: dict[str, Any] = {
            "role": role,
            "content": text_content,
            "content_blocks": None,
            "timestamp": timestamp,
            "model": model,
            "uuid": msg_id,
        }

        tokens = data.get("tokens")
        if isinstance(tokens, dict) and role == "assistant":
            input_t = tokens.get("input", 0)
            output_t = tokens.get("output", 0)
            msg["usage"] = {"input_tokens": input_t, "output_tokens": output_t}

        self.messages.append(msg)
        self.message_count += 1

        if not self.model and model:
            self.model = model

        if timestamp:
            if not self.first_timestamp:
                self.first_timestamp = timestamp
            self.last_timestamp = timestamp

    @staticmethod
    def _extract_parts_text(parts_json: str) -> str:
        """Extract concatenated text from a GROUP_CONCAT blob of part.data rows.

        Each part.data is a standalone JSON object; GROUP_CONCAT joins them with
        commas. We use ``json.JSONDecoder.raw_decode`` to consume one object at a
        time, which correctly handles braces/quotes INSIDE string values
        (unbalanced braces in code text, nested JSON, regex, etc.). A naive
        brace-depth walker cannot tell a brace inside a JSON string from a
        structural one and loses text when string values contain unbalanced
        braces — so we deliberately do NOT use one.

        If a single part is malformed, we resync at the next top-level object
        boundary (rather than aborting) so one bad part does not discard the
        rest of the conversation.
        """
        decoder = json.JSONDecoder()
        texts: list[str] = []
        length = len(parts_json)
        idx = 0
        while idx < length:
            # Skip whitespace and the commas GROUP_CONCAT inserts between objects.
            while idx < length and parts_json[idx] in " \t\n\r,":
                idx += 1
            if idx >= length:
                break
            try:
                obj, end = decoder.raw_decode(parts_json, idx)
            except json.JSONDecodeError:
                # Malformed part — resync past it to the next top-level object
                # so subsequent valid parts are still recovered.
                nxt = ZcodeSession._next_object_start(parts_json, idx)
                if nxt <= idx:
                    break
                idx = nxt
                continue
            idx = end
            if isinstance(obj, dict) and obj.get("type") == "text":
                t = obj.get("text")
                if isinstance(t, str):
                    texts.append(t)
        return "".join(texts)

    @staticmethod
    def _next_object_start(s: str, i: int) -> int:
        """Return the index just past the next top-level comma at/after ``i``.

        Tracks string and escape state plus brace depth so braces/commas that
        sit inside JSON string values do not fool the scan. Used to resync
        after a malformed concatenated object. Returns ``len(s)`` when no
        further top-level comma exists.
        """
        in_string = False
        escaped = False
        depth = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    if depth > 0:
                        depth -= 1
                elif ch == "," and depth == 0:
                    return i + 1
            i += 1
        return n

    def to_sync_payload(self, machine_id: str, terminal_id: str) -> dict[str, Any]:
        """Convert to the payload expected by the Open-ACE session-sync endpoint."""
        return {
            "session_id": self.session_id,
            "machine_id": machine_id,
            "terminal_id": terminal_id,
            "tool_name": ZCODE_SYNC_TOOL_NAME,
            "project_path": self.project_path,
            "model": self.model,
            "message_count": self.message_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "source": os.environ.get("OPEN_ACE_TERMINAL_SOURCE", "web_terminal"),
            "messages": self.messages,
        }


def _ms_to_iso(ms: int | None) -> str | None:
    """Convert epoch milliseconds to an ISO-8601 string."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class SessionSyncService:
    """
    Background service that scans for Claude Code session files and syncs
    them to the Open-ACE server.
    """

    def __init__(self, http_send_fn, config):
        self._http_send = http_send_fn
        self._config = config
        self._synced_files: set[str] = set()
        self._last_sync_times: dict[str, float] = {}
        self._running = False
        self._active_terminal_id: str = ""

    def start(self) -> None:
        """Start the sync service in a background thread."""
        import threading

        self._running = True
        self._load_state()

        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.setName("session-sync")
        thread.start()
        logger.info("Session sync service started")

    def stop(self) -> None:
        """Stop the sync service."""
        self._running = False

    def notify_terminal_active(self, terminal_id: str) -> None:
        """Called when a terminal is active — triggers immediate scan."""
        self._active_terminal_id = terminal_id

    def _get_active_terminal_context(self) -> tuple[str, str]:
        """Return the active terminal id and source for session attribution."""
        env_terminal_id = os.environ.get("OPEN_ACE_TERMINAL_ID", "")
        env_source = os.environ.get("OPEN_ACE_TERMINAL_SOURCE", "")
        if env_terminal_id:
            return env_terminal_id, env_source or "web_terminal"

        try:
            data = json.loads(ACTIVE_TERMINAL_FILE.read_text(encoding="utf-8"))
            updated_at = float(data.get("updated_at") or 0)
            if time.time() - updated_at <= ACTIVE_TERMINAL_TTL_SECONDS:
                terminal_id = str(data.get("terminal_id") or "")
                source = str(data.get("source") or "ssh_cli")
                if terminal_id:
                    return terminal_id, source
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Failed to read active terminal metadata: %s", e)

        return self._active_terminal_id, "web_terminal"

    def _load_state(self) -> None:
        """Load previously synced file state from disk."""
        try:
            if SYNC_STATE_FILE.exists():
                data = json.loads(SYNC_STATE_FILE.read_text())
                self._synced_files = set(data.get("synced_files", []))
                logger.info("Loaded sync state: %d files already synced", len(self._synced_files))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load sync state: %s", e)

    def _save_state(self) -> None:
        """Persist sync state to disk."""
        import tempfile

        try:
            SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {"synced_files": list(self._synced_files)}
            dir_path = SYNC_STATE_FILE.parent
            fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.rename(tmp_path, SYNC_STATE_FILE)
            except Exception:
                os.unlink(tmp_path)
                raise
        except OSError as e:
            logger.warning("Failed to save sync state: %s", e)

    def _run_loop(self) -> None:
        """Main sync loop."""
        while self._running:
            try:
                self._scan_and_sync()
                self._scan_and_sync_zcode_db()
                self._cleanup_synced_files()
            except Exception as e:
                logger.error("Session sync error: %s", e)
            time.sleep(SCAN_INTERVAL)

    def _cleanup_synced_files(self, max_entries: int = 500) -> None:
        """Trim _synced_files set to prevent unbounded growth."""
        if len(self._synced_files) > max_entries:
            # Keep up to max_entries arbitrary entries (set is unordered)
            excess = len(self._synced_files) - max_entries
            to_remove = list(self._synced_files)[:excess]
            for f in to_remove:
                self._synced_files.discard(f)
            logger.info("Cleaned up %d stale synced file entries", excess)

    def _scan_and_sync_zcode_db(self) -> None:
        """Scan the ZCode SQLite DB for interactive sessions and sync new/changed ones.

        ZCode stores sessions in a relational DB rather than JSONL files, so this
        does not use the rglob scan. Dedup keys are ``zcode:<session_id>`` strings
        stored in the same _synced_files / _last_sync_times state as JSONL syncs.
        A session is re-synced when its ``time_updated`` column advances past the
        last sync time.
        """
        if not ZCODE_DB_PATH.exists():
            return

        # Discover candidate sessions (interactive, not archived).
        try:
            conn = sqlite3.connect(f"file:{ZCODE_DB_PATH}?mode=ro", uri=True)
        except sqlite3.Error as e:
            logger.debug("Cannot open ZCode DB for scan: %s", e)
            return

        candidates: list[tuple[str, int]] = []
        try:
            rows = conn.execute(
                "SELECT id, time_updated FROM session "
                "WHERE task_type = 'interactive' AND time_archived IS NULL"
            ).fetchall()
            candidates = [(r[0], r[1] or 0) for r in rows]
        except sqlite3.DatabaseError as e:
            logger.debug("ZCode DB query failed: %s", e)
            return
        finally:
            conn.close()

        synced_count = 0
        for session_id, time_updated_ms in candidates:
            dedup_key = f"zcode:{session_id}"
            last_sync = self._last_sync_times.get(dedup_key, 0)
            # time_updated is epoch ms; compare against last sync (epoch s).
            if dedup_key in self._synced_files and time_updated_ms / 1000 <= last_sync:
                continue

            session = ZcodeSession(session_id, ZCODE_DB_PATH)
            if not session.parse() or session.message_count == 0:
                continue

            terminal_id, source = self._get_active_terminal_context()
            payload = session.to_sync_payload(self._config.machine_id, terminal_id)
            payload["source"] = source

            try:
                result = self._http_send(
                    {
                        "type": "session_sync",
                        "machine_id": self._config.machine_id,
                        **payload,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("ZCode session sync send failed for %s: %s", session_id[:8], e)
                continue

            if result:
                self._synced_files.add(dedup_key)
                self._last_sync_times[dedup_key] = time.time()
                synced_count += 1

        if synced_count > 0:
            self._save_state()
            logger.info("Synced %d ZCode sessions", synced_count)

    def _scan_and_sync(self) -> None:
        """Scan for session files and sync new/changed ones."""
        scan_dirs = [
            (CLAUDE_PROJECTS_DIR, ClaudeSession),
            (QWEN_PROJECTS_DIR, QwenSession),
            (CODEX_SESSIONS_DIR, CodexSession),
        ]

        synced_count = 0
        for scan_dir, session_cls in scan_dirs:
            if not scan_dir.exists():
                continue

            for jsonl_path in scan_dir.rglob("*.jsonl"):
                path_str = str(jsonl_path)

                # Skip already-synced files (unless modified)
                mtime = jsonl_path.stat().st_mtime
                last_sync = self._last_sync_times.get(path_str, 0)

                if path_str in self._synced_files and mtime <= last_sync:
                    continue

                session_id = jsonl_path.stem

                session = session_cls(session_id, path_str)
                if session.parse() and session.message_count > 0:
                    terminal_id, source = self._get_active_terminal_context()
                    payload = session.to_sync_payload(self._config.machine_id, terminal_id)
                    payload["source"] = source

                    result = self._http_send(
                        {
                            "type": "session_sync",
                            "machine_id": self._config.machine_id,
                            **payload,
                        }
                    )

                    if result:
                        self._synced_files.add(path_str)
                        self._last_sync_times[path_str] = time.time()
                        synced_count += 1

        if synced_count > 0:
            self._save_state()
            logger.info("Synced %d sessions (Claude/Qwen)", synced_count)
