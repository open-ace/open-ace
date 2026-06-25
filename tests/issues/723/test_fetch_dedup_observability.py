"""Tests for fetch dedup observability (issue #723, group A follow-up).

`warn_if_skipped_message_has_text` is the regression guard that detects when a
fetched JSONL line is dropped by the ``(session_id, role, message_id)`` dedup
but carries real text the stored row lacks — the exact data-loss signature that
lost claude review conclusions (one message.id split into a thinking line + a
text line; thinking won the insert, text was dropped as a dup).

It applies to all fetchers (claude/qwen/codex/zcode). Tools whose JSONL gives
every line a distinct id (qwen, codex) never trigger it in practice; this test
locks the detection logic so a future format change is caught.
"""

import json
import logging
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.utils import warn_if_skipped_message_has_text  # noqa: E402


def _caplog_handler(caplog):
    """Make the fetch_dedup logger propagate so caplog can capture it."""
    logger = logging.getLogger("fetch_dedup")
    logger.setLevel(logging.WARNING)
    return logger


class TestWarnIfSkippedMessageHasText:
    def test_warns_when_skipped_line_has_text_not_in_stored_row(self, caplog):
        """The #723 signature: existing row is thinking-only, skipped line has
        the final text answer → must warn (would be lost)."""
        _caplog_handler(caplog)
        existing = (1, "[]")  # thinking-only row, content is empty array
        incoming = {
            "content": json.dumps(["## 审查结论 方案成立"], ensure_ascii=False),
        }
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(existing, incoming, "sess-1", "msg_1", "fetch_claude")
        assert any(
            "text NOT in the stored row" in r.message for r in caplog.records
        ), "should warn when skipped line carries text the stored row lacks"

    def test_no_warn_when_stored_row_already_has_the_text(self, caplog):
        """If the existing row already contains the text, the skip is benign
        (genuine re-fetch of the same content)."""
        _caplog_handler(caplog)
        existing = (1, json.dumps(["the answer"], ensure_ascii=False))
        incoming = {"content": json.dumps(["the answer"], ensure_ascii=False)}
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(existing, incoming, "sess-1", "msg_1", "fetch_qwen")
        assert not any("text NOT in the stored row" in r.message for r in caplog.records)

    def test_no_warn_when_skipped_line_has_no_text(self, caplog):
        """A skipped thinking-only/tool line with no text is a normal dedup."""
        _caplog_handler(caplog)
        existing = (1, json.dumps(["the answer"], ensure_ascii=False))
        incoming = {"content": "[]"}  # thinking-only, no real text
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(existing, incoming, "sess-1", "msg_1", "fetch_codex")
        assert not any("text NOT in the stored row" in r.message for r in caplog.records)

    def test_existing_row_none_with_text_warns(self, caplog):
        """When existing_row is None (shouldn't happen on the skip path, but be
        safe), an incoming text line is treated as 'not stored' and warns —
        the helper must not NPE on None."""
        _caplog_handler(caplog)
        incoming = {"content": "some text"}
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(None, incoming, "sess-1", "msg_1", "fetch_zcode")
        assert any("text NOT in the stored row" in r.message for r in caplog.records)

    def test_warn_message_names_the_source_and_ids(self, caplog):
        """The warning should include the fetcher source + ids for traceability."""
        _caplog_handler(caplog)
        existing = (1, "")
        incoming = {"content": "lost answer"}
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(
                existing, incoming, "sess-xyz", "msg-abc", "fetch_claude"
            )
        msgs = [r.message for r in caplog.records]
        assert any("fetch_claude" in m for m in msgs)
        assert any("msg-abc" in m for m in msgs)

    def test_handles_plain_string_content(self, caplog):
        """qwen user messages store content as a plain string, not JSON array."""
        _caplog_handler(caplog)
        existing = (1, "")
        incoming = {"content": "a user message that would be lost"}
        with caplog.at_level(logging.WARNING, logger="fetch_dedup"):
            warn_if_skipped_message_has_text(existing, incoming, "sess-1", "msg_1", "fetch_qwen")
        assert any("text NOT in the stored row" in r.message for r in caplog.records)

    def test_never_raises_on_bad_input(self, caplog):
        """Observability must never break a fetch run — bad inputs are swallowed."""
        _caplog_handler(caplog)
        # Pass deliberately weird inputs.
        warn_if_skipped_message_has_text("not-a-row", {"content": None}, "s", "m", "x")
        warn_if_skipped_message_has_text((1,), {}, "s", "m", "x")
        warn_if_skipped_message_has_text(None, None, "s", "m", "x")
        # No exception raised == pass.
