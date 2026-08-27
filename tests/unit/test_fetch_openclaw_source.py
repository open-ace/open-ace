#!/usr/bin/env python3
"""Issue #1829 — F1/F2/F5 source-detection & sender-resolution hardening for
scripts/fetch_openclaw.py.

F1: source detection anchored on structured signals only (no loose "钉钉"/
    "DingTalk" substring flips that triggered needless provider API + token
    fetches for non-matching sessions).
F2: sender fields read per-JSON-block with a source guard, so a quoted
    cross-source block (e.g. a Feishu ``ou_`` block pasted into a DingTalk
    session) can't be adopted as the DingTalk sender.
F5: sender-resolution coverage tallied by message_source at import end so the
    previously-silent "imported with sender_id=None" case is observable.

Hermeticity note (#1829 migration): the legacy quarantine copy of this file set
``os.environ["DATABASE_URL"]`` directly and ``importlib.reload``-ed ``shared.db``
per test. That is unnecessary here — these tests exercise pure helper functions
(no DB access), and the unit conftest autouse fixture already points
``DATABASE_URL`` at a per-test tmp SQLite file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1829)]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SHARED_DIR = SCRIPTS_DIR / "shared"


def _load_fetch_openclaw():
    """Load scripts/fetch_openclaw.py as an isolated module (no env writes).

    Follows the tests/unit loader precedent (spec_from_file_location at
    parents[2] depth). The autouse unit conftest fixture provides the isolated
    tmp-sqlite DATABASE_URL; no reload of ``shared.db`` is needed because the
    functions under test never touch the database.
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if str(SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(SHARED_DIR))

    spec = importlib.util.spec_from_file_location(
        "fetch_openclaw_1829", SCRIPTS_DIR / "fetch_openclaw.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# F1 — tightened source detection
# --------------------------------------------------------------------------- #
class TestF1SourceDetection:
    def test_mention_of_dingtalk_word_does_not_flip_source(self):
        """A user message that merely mentions 钉钉 / DingTalk must NOT be
        classified as dingtalk. Previously any literal substring flipped the
        source and triggered a needless provider API + access-token fetch."""
        mod = _load_fetch_openclaw()
        parsed = mod.extract_user_message_metadata(
            "我在用 DingTalk 和同事讨论，钉钉真好用\n\nConversation info"
        )
        assert parsed["message_source"] != "dingtalk"

    def test_structured_dingtalk_block_flips_source(self):
        mod = _load_fetch_openclaw()
        parsed = mod.extract_user_message_metadata(
            '{"message_source": "dingtalk", "sender_id": "u1"} real content'
        )
        assert parsed["message_source"] == "dingtalk"

    def test_mention_of_feishu_word_without_structured_signal_stays_openclaw(self):
        """Bare 'Feishu' prose must not flip the source; only the structured
        ``"message_source": "feishu"`` signal (or a conversation_label block) does."""
        mod = _load_fetch_openclaw()
        parsed = mod.extract_user_message_metadata("Tell Feishu team hello\n\nConversation info")
        assert parsed["message_source"] != "feishu"


# --------------------------------------------------------------------------- #
# F2 — per-block source guard
# --------------------------------------------------------------------------- #
class TestF2PerBlockSourceGuard:
    def test_cross_source_quoted_block_not_adopted(self):
        """A Feishu ``ou_`` block quoted inside a DingTalk session must NOT be
        adopted as the DingTalk sender (the original whole-text re.search bug)."""
        mod = _load_fetch_openclaw()
        text = (
            '{"message_source": "dingtalk", "sender_id": "dt_user", "label": "Alice"} '
            "quoted earlier: "
            '{"id": "ou_feishu_impersonator", "sender_id": "ou_attacker", "label": "Eve"}'
        )
        result = mod._extract_sender_from_json_blocks(text, expected_source="dingtalk")
        assert result["sender_id"] == "dt_user"
        assert result["sender_name"] == "Alice"

    def test_expected_source_none_resolves_from_first_block(self):
        """Step-6 fallback path: with no envelope-pinned source, the effective
        source is resolved from the first source-declaring block, then sender
        fields are read only from matching blocks."""
        mod = _load_fetch_openclaw()
        text = '{"message_source": "feishu", "sender_id": "ou_real", "label": "Bob"}'
        result = mod._extract_sender_from_json_blocks(text, expected_source=None)
        assert result["message_source"] == "feishu"
        assert result["sender_id"] == "ou_real"
        assert result["sender_name"] == "Bob"

    def test_non_sender_fields_read_from_any_block(self):
        """conversation_label / group_subject / is_group_chat are not sender
        identity fields, so they are read from any block (no cross-source
        contamination risk)."""
        mod = _load_fetch_openclaw()
        text = (
            '{"message_source": "dingtalk", "sender_id": "dt"} '
            '{"conversation_label": "chatabcd1234", "group_subject": "Team", "is_group_chat": 1}'
        )
        result = mod._extract_sender_from_json_blocks(text, expected_source="dingtalk")
        assert result["conversation_label"] == "chatabcd1234"
        assert result["group_subject"] == "Team"
        assert result["is_group_chat"] == 1


# --------------------------------------------------------------------------- #
# F5 — sender-resolution coverage summary
# --------------------------------------------------------------------------- #
class TestF5SenderResolutionSummary:
    def test_summary_counts_only_user_messages_by_source(self):
        mod = _load_fetch_openclaw()
        messages = [
            {
                "role": "user",
                "message_source": "dingtalk",
                "sender_id": "u1",
                "sender_name": "Alice",
            },
            {"role": "user", "message_source": "dingtalk", "sender_id": "u2"},  # name unresolved
            {"role": "user", "message_source": "dingtalk"},  # no sender_id
            {"role": "assistant", "message_source": "dingtalk", "sender_id": "u1"},  # ignored
            {"role": "user", "message_source": "feishu", "sender_id": "ou_x", "sender_name": "Bob"},
        ]
        summary = mod._summarize_sender_resolution(messages)
        dt = summary["dingtalk"]
        assert dt["total"] == 3
        assert dt["with_sender_id"] == 2
        assert dt["resolved"] == 1
        assert dt["unresolved"] == 1
        assert summary["feishu"]["total"] == 1
        assert summary["feishu"]["resolved"] == 1

    def test_summary_defaults_missing_source_to_openclaw(self):
        mod = _load_fetch_openclaw()
        summary = mod._summarize_sender_resolution(
            [{"role": "user", "sender_id": "u1", "sender_name": "u1"}]
        )
        assert "openclaw" in summary
        # sender_name == sender_id counts as not-resolved (no display name gained)
        assert summary["openclaw"]["resolved"] == 0
        assert summary["openclaw"]["with_sender_id"] == 1
