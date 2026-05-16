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
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("openace-agent.session-sync")

# How often to scan for new/changed sessions (seconds)
SCAN_INTERVAL = 30

# Directories to watch
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

# File to track which sessions have already been synced
SYNC_STATE_FILE = Path.home() / ".open-ace-agent" / "session_sync_state.json"


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
        """Process a single JSONL entry."""
        entry_type = entry.get("type")

        if entry_type == "message":
            role = entry.get("role", "")
            if role in ("user", "assistant"):
                self.message_count += 1
                msg = {
                    "role": role,
                    "content": self._extract_content(entry),
                    "timestamp": entry.get("timestamp"),
                    "model": entry.get("model"),
                }
                self.messages.append(msg)

                # Track timestamps
                ts = entry.get("timestamp")
                if ts:
                    if not self.first_timestamp:
                        self.first_timestamp = ts
                    self.last_timestamp = ts

                # Track model
                if not self.model and entry.get("model"):
                    self.model = entry["model"]

                # Track token usage from assistant messages
                if role == "assistant" and "usage" in entry:
                    usage = entry["usage"]
                    self.total_input_tokens += usage.get("input_tokens", 0)
                    self.total_output_tokens += usage.get("output_tokens", 0)

        elif entry_type == "summary":
            # Conversation summary/compression event
            pass

    def _extract_content(self, entry: dict[str, Any]) -> str:
        """Extract text content from a message entry."""
        content = entry.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Content blocks: extract text from text blocks
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts)
        return str(content)

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
            "messages": self.messages[-50:],  # Last 50 messages only
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
        try:
            SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SYNC_STATE_FILE.write_text(json.dumps({"synced_files": list(self._synced_files)}))
        except OSError as e:
            logger.warning("Failed to save sync state: %s", e)

    def _run_loop(self) -> None:
        """Main sync loop."""
        while self._running:
            try:
                self._scan_and_sync()
            except Exception as e:
                logger.error("Session sync error: %s", e)
            time.sleep(SCAN_INTERVAL)

    def _scan_and_sync(self) -> None:
        """Scan for session files and sync new/changed ones."""
        if not CLAUDE_PROJECTS_DIR.exists():
            return

        synced_count = 0
        for jsonl_path in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
            path_str = str(jsonl_path)

            # Skip already-synced files (unless modified)
            mtime = jsonl_path.stat().st_mtime
            last_sync = self._last_sync_times.get(path_str, 0)

            if path_str in self._synced_files and mtime <= last_sync:
                continue

            # Extract session ID from filename
            session_id = jsonl_path.stem

            session = ClaudeSession(session_id, path_str)
            if session.parse() and session.message_count > 0:
                # Find terminal_id for this sync
                terminal_id = getattr(self, "_active_terminal_id", "")
                payload = session.to_sync_payload(self._config.machine_id, terminal_id)

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
            logger.info("Synced %d Claude Code sessions", synced_count)
