#!/usr/bin/env python3
"""Unit tests for DingTalk user cache helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_dingtalk_user_cache():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "shared" / "dingtalk_user_cache.py"
    )
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("dingtalk_user_cache", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_get_dingtalk_access_token(monkeypatch):
    mod = load_dingtalk_user_cache()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"accessToken": "token-123"}

    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: FakeResponse())

    assert mod.get_dingtalk_access_token("app-key", "app-secret") == "token-123"


def test_get_user_display_name_caches_result(monkeypatch, tmp_path):
    mod = load_dingtalk_user_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_users.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "token-123")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "result": {"name": "Alice DingTalk"}}

    monkeypatch.setattr(mod.requests, "post", lambda *args, **kwargs: FakeResponse())

    assert mod.get_user_display_name("manager123", "app-key", "app-secret") == "Alice DingTalk"
    assert mod.get_user_display_name_from_cache("manager123") == "Alice DingTalk"
