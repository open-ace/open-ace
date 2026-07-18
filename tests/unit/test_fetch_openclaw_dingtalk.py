#!/usr/bin/env python3
"""Focused DingTalk coverage for scripts/fetch_openclaw.py."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SHARED_DIR = SCRIPTS_DIR / "shared"


def load_fetch_openclaw(tmp_db_url: str):
    os.environ["DATABASE_URL"] = tmp_db_url

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if str(SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(SHARED_DIR))

    from shared import db as db_mod

    importlib.reload(db_mod)

    spec = importlib.util.spec_from_file_location(
        "fetch_openclaw_under_test", SCRIPTS_DIR / "fetch_openclaw.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_extract_user_message_metadata_detects_dingtalk(tmp_path):
    mod = load_fetch_openclaw(f"sqlite:///{tmp_path / 'ace.db'}")

    parsed = mod.extract_user_message_metadata(
        'DingTalk relay text\n\nConversation info\n{"message_source":"dingtalk","sender_id":"user123","conversation_label":"chatabcd1234"}'
    )

    assert parsed["message_source"] == "dingtalk"
    assert parsed["sender_id"] == "user123"
    assert parsed["conversation_label"] == "chatabcd1234"
    assert parsed["cleaned_content"] == "DingTalk relay text"


def test_process_jsonl_file_resolves_dingtalk_names(monkeypatch, tmp_path):
    mod = load_fetch_openclaw(f"sqlite:///{tmp_path / 'ace.db'}")
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-17T02:00:00Z",
                "type": "message",
                "message": {
                    "id": "msg-1",
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "DingTalk relay text\n\nConversation info\n"
                                '{"message_source":"dingtalk","sender_id":"user123","conversation_label":"chatabcd1234","is_group_chat":true}'
                            ),
                        }
                    ],
                    "usage": {"input": 12, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        mod.utils,
        "load_config",
        lambda: {"dingtalk": {"app_key": "app-key", "app_secret": "app-secret"}},
    )
    monkeypatch.setattr(
        mod.dingtalk_user_cache,
        "get_user_display_name_from_cache",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        mod.dingtalk_user_cache,
        "get_user_display_name",
        lambda user_id, app_key, app_secret: "Alice DingTalk",
    )
    monkeypatch.setattr(
        mod.dingtalk_group_cache,
        "get_group_name_from_conversation_label",
        lambda label, app_key, app_secret: "Engineering Group",
    )

    daily, messages = mod.process_jsonl_file(jsonl, "localhost", "openclaw", "tester")

    assert daily["2026-07-17"]["input_tokens"] == 12
    assert messages[0]["message_source"] == "dingtalk"
    assert messages[0]["sender_name"] == "Alice DingTalk"
    assert messages[0]["group_subject"] == "Engineering Group"
    assert messages[0]["feishu_conversation_id"] == "chatabcd1234"
