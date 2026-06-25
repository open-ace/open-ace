"""Tests for fetch_claude message-id merge (issue #723, group A).

Claude Code writes one logical assistant message as SEVERAL JSONL lines
(thinking, then text, then tool_use) that reuse the same ``message.id``.
Before the fix, fetch_claude inserted one row per line and deduped by
``(session_id, role, message_id)``, so the thinking line won the INSERT and
the text line (the final answer / review conclusion) was dropped as a dup.

These tests cover both the merge helper directly and the end-to-end
``process_jsonl_file`` path.
"""

import json
import sys
from pathlib import Path

# scripts/ is not a package; put the repo's scripts dir on sys.path for import.
# Test file lives at <repo>/tests/issues/723/ -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_claude import _merge_messages_by_id, process_jsonl_file  # noqa: E402

# ── _merge_messages_by_id (unit) ──────────────────────────────────────


class TestMergeMessagesById:
    def test_thinking_and_text_lines_merge_into_one(self):
        """A thinking line + a text line sharing message_id become ONE message
        whose content_blocks carry both blocks and content is the text."""
        thinking = {
            "agent_session_id": "s1",
            "role": "assistant",
            "message_id": "msg_1",
            "content": "[]",  # thinking-only line extracts no text
            "content_blocks": [{"type": "thinking", "thinking": "Let me think..."}],
            "tokens_used": 100,
            "input_tokens": 80,
            "output_tokens": 20,
            "timestamp": "2026-06-24T12:37:00Z",
        }
        text = {
            "agent_session_id": "s1",
            "role": "assistant",
            "message_id": "msg_1",
            "content": json.dumps(["## 审查结论 方案核心架构成立"], ensure_ascii=False),
            "content_blocks": [{"type": "text", "text": "## 审查结论 方案核心架构成立"}],
            "tokens_used": 100,  # claude repeats usage on each block-line
            "input_tokens": 80,
            "output_tokens": 20,
            "timestamp": "2026-06-24T12:39:00Z",
        }
        merged = _merge_messages_by_id([thinking, text])

        assert len(merged) == 1, "thinking+text with same message_id must merge to 1"
        m = merged[0]
        assert m["message_id"] == "msg_1"
        # content_blocks carry BOTH thinking and text
        types = [b["type"] for b in m["content_blocks"]]
        assert "thinking" in types and "text" in types
        # content is the text line, not the empty thinking line
        assert "审查结论" in m["content"]
        # tokens: max within group (not summed) to avoid double counting
        assert m["tokens_used"] == 100
        assert m["input_tokens"] == 80

    def test_distinct_message_ids_stay_separate(self):
        """Different message_ids are NOT merged."""
        a = {
            "agent_session_id": "s1",
            "role": "assistant",
            "message_id": "m_a",
            "content": "[]",
            "content_blocks": [],
            "tokens_used": 0,
        }
        b = {
            "agent_session_id": "s1",
            "role": "assistant",
            "message_id": "m_b",
            "content": json.dumps(["second"]),
            "content_blocks": [{"type": "text", "text": "second"}],
            "tokens_used": 0,
        }
        merged = _merge_messages_by_id([a, b])
        assert len(merged) == 2

    def test_different_roles_with_same_id_stay_separate(self):
        """(session, role, message_id) is the key — same id, different role = 2 rows."""
        u = {"agent_session_id": "s1", "role": "user", "message_id": "x", "content": "hi"}
        a = {"agent_session_id": "s1", "role": "assistant", "message_id": "x", "content": "[]"}
        merged = _merge_messages_by_id([u, a])
        assert len(merged) == 2

    def test_empty_list_passthrough(self):
        assert _merge_messages_by_id([]) == []

    def test_order_preserved(self):
        """Merged output preserves first-occurrence order of groups."""
        msgs = [
            {"agent_session_id": "s1", "role": "assistant", "message_id": "m2", "content": "[]"},
            {"agent_session_id": "s1", "role": "assistant", "message_id": "m1", "content": "[]"},
            {
                "agent_session_id": "s1",
                "role": "assistant",
                "message_id": "m2",
                "content": json.dumps(["m2 text"]),
                "content_blocks": [{"type": "text", "text": "m2 text"}],
            },
        ]
        merged = _merge_messages_by_id(msgs)
        # m2 first (seen first), then m1
        assert [m["message_id"] for m in merged] == ["m2", "m1"]
        assert "m2 text" in merged[0]["content"]


# ── process_jsonl_file (integration) ──────────────────────────────────


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


class TestProcessJsonlFileMerge:
    def test_assistant_text_survives_when_thinking_line_first(self, tmp_path):
        """End-to-end: a real-shaped assistant message (thinking line then text
        line, same message.id) must yield ONE message row whose content has the
        final text — the regression that lost review conclusions."""
        mid = "msg_20260624203902ff56c7472512449f"
        entries = [
            # thinking line
            {
                "type": "assistant",
                "uuid": "u-thinking",
                "timestamp": "2026-06-24T12:39:00.000Z",
                "sessionId": "s-jsonl",
                "message": {
                    "id": mid,
                    "role": "assistant",
                    "model": "claude-sonnet",
                    "content": [{"type": "thinking", "thinking": "I now have enough context."}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            # text line (the final answer) — SAME message.id
            {
                "type": "assistant",
                "uuid": "u-text",
                "timestamp": "2026-06-24T12:39:05.000Z",
                "sessionId": "s-jsonl",
                "message": {
                    "id": mid,
                    "role": "assistant",
                    "model": "claude-sonnet",
                    "content": [{"type": "text", "text": "## 审查结论\n方案核心架构正确且成立"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ]
        jsonl = _write_jsonl(tmp_path / "s-jsonl.jsonl", entries)

        _daily, messages = process_jsonl_file(jsonl)

        # Both lines share message_id → exactly one merged message.
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert (
            len(assistant_msgs) == 1
        ), f"expected 1 merged assistant message, got {len(assistant_msgs)}"
        m = assistant_msgs[0]
        assert m["message_id"] == mid
        # The final text MUST survive (regression guard).
        assert "审查结论" in m["content"]
        assert "方案核心架构正确且成立" in m["content"]
        # Both blocks present.
        block_types = [b["type"] for b in (m.get("content_blocks") or [])]
        assert "thinking" in block_types and "text" in block_types
