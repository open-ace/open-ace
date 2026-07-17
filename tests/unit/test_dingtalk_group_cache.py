#!/usr/bin/env python3
"""Unit tests for DingTalk group cache helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_dingtalk_group_cache():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "shared" / "dingtalk_group_cache.py"
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("dingtalk_group_cache", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_extract_chat_id():
    mod = load_dingtalk_group_cache()

    assert mod.extract_chat_id("chatabcd1234") == "chatabcd1234"
    assert mod.extract_chat_id("conversation_label=chatXYZ_456") == "chatXYZ_456"
    assert mod.extract_chat_id("cid123456") is None


def test_get_group_name_from_conversation_label(monkeypatch, tmp_path):
    mod = load_dingtalk_group_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_groups.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "token-123")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "name": "Engineering Group"}

    monkeypatch.setattr(mod.requests, "get", lambda *args, **kwargs: FakeResponse())

    label = "conversation_label=chatabcd1234"
    assert mod.get_group_name_from_conversation_label(label, "app-key", "app-secret") == "Engineering Group"
    assert mod.get_group_name_from_conversation_label(label, "app-key", "app-secret") == "Engineering Group"
