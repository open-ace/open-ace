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
import time
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
