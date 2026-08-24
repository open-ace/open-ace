"""
Issue #2735: Qwen JSONL Test Data Factory

Factory for creating Qwen session JSONL files for testing.
Supports:
- Complete message tree structures (with parentUuid)
- Tool call sequences (functionCall + tool_result)
- Thinking messages
- Edge cases: empty trees, broken references, multiple roots, cycles
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class QwenSessionFactory:
    """Factory for generating Qwen JSONL test data."""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def _generate_uuid(self) -> str:
        """Generate a unique UUID for a message."""
        return str(uuid.uuid4())[:8]

    def _get_timestamp(self, offset_seconds: int = 0) -> str:
        """Generate an ISO timestamp with optional offset."""
        dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=offset_seconds)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def create_user_message(
        self,
        text: str = "Hello",
        uuid_override: str | None = None,
        parent_uuid: str | None = None,
        timestamp_offset: int = 0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a user message entry."""
        return {
            "type": "user",
            "uuid": uuid_override or self._generate_uuid(),
            "parentUuid": parent_uuid,
            "timestamp": self._get_timestamp(timestamp_offset),
            "sessionId": session_id or f"session-{self._generate_uuid()}",
            "message": {
                "role": "user",
                "parts": [{"text": text}],
            },
        }

    def create_assistant_message(
        self,
        text: str = "Response",
        uuid_override: str | None = None,
        parent_uuid: str | None = None,
        timestamp_offset: int = 0,
        model: str = "qwen-max",
        prompt_tokens: int = 100,
        candidates_tokens: int = 50,
        total_tokens: int | None = None,
        cached_tokens: int = 0,
        thoughts_tokens: int = 0,
        function_calls: list[dict] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an assistant message entry with optional tool calls."""
        parts = [{"text": text}]
        if function_calls:
            for fc in function_calls:
                parts.append({"functionCall": fc})

        entry = {
            "type": "assistant",
            "uuid": uuid_override or self._generate_uuid(),
            "parentUuid": parent_uuid,
            "timestamp": self._get_timestamp(timestamp_offset),
            "sessionId": session_id,
            "model": model,
            "message": {
                "role": "assistant",
                "parts": parts,
            },
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": candidates_tokens,
                "totalTokenCount": total_tokens or (prompt_tokens + candidates_tokens),
                "cachedContentTokenCount": cached_tokens,
                "thoughtsTokenCount": thoughts_tokens,
            },
        }
        return entry

    def create_tool_result(
        self,
        tool_use_id: str,
        result: str = "Tool result",
        uuid_override: str | None = None,
        parent_uuid: str | None = None,
        timestamp_offset: int = 0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a tool_result message entry."""
        return {
            "type": "tool_result",
            "uuid": uuid_override or self._generate_uuid(),
            "parentUuid": parent_uuid,
            "timestamp": self._get_timestamp(timestamp_offset),
            "sessionId": session_id,
            "message": {
                "role": "tool",
                "parts": [
                    {
                        "tool_result": {
                            "tool_use_id": tool_use_id,
                            "content": result,
                        }
                    }
                ],
            },
        }

    def create_thinking_message(
        self,
        text: str = "Thinking...",
        uuid_override: str | None = None,
        parent_uuid: str | None = None,
        timestamp_offset: int = 0,
    ) -> dict[str, Any]:
        """Create a thinking message entry."""
        return {
            "type": "assistant",
            "uuid": uuid_override or self._generate_uuid(),
            "parentUuid": parent_uuid,
            "timestamp": self._get_timestamp(timestamp_offset),
            "thought": True,
            "message": {
                "role": "assistant",
                "parts": [{"text": text}],
            },
        }

    def create_simple_conversation(
        self, user_text: str = "Hello", assistant_text: str = "Hi there!"
    ) -> list[dict[str, Any]]:
        """Create a simple user -> assistant conversation."""
        user_msg = self.create_user_message(text=user_text, uuid_override="user-001")
        assistant_msg = self.create_assistant_message(
            text=assistant_text, uuid_override="assistant-001", parent_uuid="user-001"
        )
        return [user_msg, assistant_msg]

    def create_tool_call_sequence(
        self,
        tool_names: list[str] | None = None,
        user_text: str = "Run these tools",
    ) -> list[dict[str, Any]]:
        """Create a user -> assistant (with tool calls) -> tool_results sequence."""
        tool_names = tool_names or ["bash", "read"]
        session_id = f"session-{self._generate_uuid()}"

        user_msg = self.create_user_message(
            text=user_text, uuid_override="user-tool-001", session_id=session_id
        )

        function_calls = [{"name": name, "id": f"{name}-id"} for name in tool_names]
        assistant_msg = self.create_assistant_message(
            text="I'll run those tools for you.",
            uuid_override="assistant-tool-001",
            parent_uuid="user-tool-001",
            function_calls=function_calls,
            session_id=session_id,
        )

        entries = [user_msg, assistant_msg]
        for i, tool_name in enumerate(tool_names):
            tool_result = self.create_tool_result(
                tool_use_id=f"{tool_name}-id",
                result=f"Result from {tool_name}",
                uuid_override=f"tool-result-{i}",
                parent_uuid="assistant-tool-001",
                session_id=session_id,
            )
            entries.append(tool_result)

        return entries

    def create_deep_message_tree(self, depth: int = 4) -> list[dict[str, Any]]:
        """Create a deep message tree for testing tree traversal."""
        entries = []
        session_id = f"session-deep-{self._generate_uuid()}"

        # Root user message
        root = self.create_user_message(
            text="Root message", uuid_override="deep-root", session_id=session_id
        )
        entries.append(root)

        # Chain of assistant -> tool -> assistant -> tool ...
        parent_uuid = "deep-root"
        for i in range(depth):
            assistant_msg = self.create_assistant_message(
                text=f"Response level {i}",
                uuid_override=f"deep-assistant-{i}",
                parent_uuid=parent_uuid,
                session_id=session_id,
            )
            entries.append(assistant_msg)

            tool_result = self.create_tool_result(
                tool_use_id=f"tool-{i}",
                result=f"Tool result {i}",
                uuid_override=f"deep-tool-{i}",
                parent_uuid=f"deep-assistant-{i}",
                session_id=session_id,
            )
            entries.append(tool_result)

            parent_uuid = f"deep-tool-{i}"

        return entries

    def create_multiple_roots(self, num_roots: int = 2) -> list[dict[str, Any]]:
        """Create multiple root messages (multiple conversations in one file)."""
        entries = []
        for i in range(num_roots):
            user_msg = self.create_user_message(
                text=f"Conversation {i}", uuid_override=f"multi-root-{i}"
            )
            assistant_msg = self.create_assistant_message(
                text=f"Response {i}",
                uuid_override=f"multi-response-{i}",
                parent_uuid=f"multi-root-{i}",
            )
            entries.extend([user_msg, assistant_msg])
        return entries

    def create_broken_tree(self) -> list[dict[str, Any]]:
        """Create a message with a broken parent reference."""
        # Message references a parent that doesn't exist
        return [
            self.create_assistant_message(
                text="Orphan message",
                uuid_override="orphan-001",
                parent_uuid="non-existent-parent",
            )
        ]

    def create_cycle(self) -> list[dict[str, Any]]:
        """Create a cycle in the message tree: A -> B -> C -> A."""
        return [
            {
                "type": "user",
                "uuid": "cycle-a",
                "parentUuid": "cycle-c",  # Points to C, creating a cycle
                "timestamp": self._get_timestamp(0),
                "message": {"role": "user", "parts": [{"text": "A"}]},
            },
            {
                "type": "assistant",
                "uuid": "cycle-b",
                "parentUuid": "cycle-a",
                "timestamp": self._get_timestamp(1),
                "message": {"role": "assistant", "parts": [{"text": "B"}]},
            },
            {
                "type": "assistant",
                "uuid": "cycle-c",
                "parentUuid": "cycle-b",
                "timestamp": self._get_timestamp(2),
                "message": {"role": "assistant", "parts": [{"text": "C"}]},
            },
        ]

    def create_empty_file(self) -> list[dict[str, Any]]:
        """Create an empty entries list."""
        return []

    def write_jsonl(self, path: Path, entries: list[dict[str, Any]] | None = None) -> Path:
        """Write entries to a JSONL file."""
        entries = entries if entries is not None else self.entries
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def write_simple_session(self, output_dir: Path, session_id: str = "test-session") -> Path:
        """Write a simple session file to output_dir/{session_id}.jsonl."""
        entries = self.create_simple_conversation()
        return self.write_jsonl(output_dir / f"{session_id}.jsonl", entries)


# Convenience functions for tests
def create_simple_session(path: Path) -> Path:
    """Create a simple session file for testing."""
    factory = QwenSessionFactory()
    return factory.write_simple_session(path.parent, path.name)


def create_session_with_tools(path: Path, tool_count: int = 2) -> Path:
    """Create a session with multiple tool calls."""
    factory = QwenSessionFactory()
    tool_names = [f"tool-{i}" for i in range(tool_count)]
    entries = factory.create_tool_call_sequence(tool_names=tool_names)
    return factory.write_jsonl(path, entries)