#!/usr/bin/env python3
"""TDD tests for DingTalk import-cache hardening (PR #1768 findings).

Covers:
- access_token must NOT travel in the URL query string (use a header)
- userid-keyed cache must invalidate on unionid mismatch (recycled identity)
- errors logged, not printed (and request URL/token redacted on failure)
- fetch_openclaw sender resolution: plain ``userid:`` DingTalk messages resolve
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import sys
from pathlib import Path


def load_dingtalk_user_cache():
    module_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "shared" / "dingtalk_user_cache.py"
    )
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("dingtalk_user_cache", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_dingtalk_group_cache():
    module_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "shared" / "dingtalk_group_cache.py"
    )
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("dingtalk_group_cache", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_user_info_request_uses_header_not_url_token(monkeypatch, tmp_path):
    """access_token must be sent in a header, never in the request URL query string."""
    mod = load_dingtalk_user_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_users.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "secret-token-XYZ")

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "result": {"name": "Alice"}}

    def fake_post(url, *args, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return FakeResponse()

    monkeypatch.setattr(mod.requests, "post", fake_post)

    mod.get_user_info("manager123", "app-key", "app-secret")

    # The token must NOT appear in the URL query string.
    assert (
        "access_token=" not in captured["url"]
    ), f"access_token leaked into URL query string: {captured['url']}"
    assert "secret-token-XYZ" not in captured["url"]
    # The token must be carried in an Authorization (or x-acs-dingtalk-access-token) header.
    headers = captured.get("headers") or {}
    header_values = " ".join(str(v) for v in headers.values())
    assert (
        "secret-token-XYZ" in header_values
    ), f"access_token not found in request headers: {headers}"


def test_group_info_request_uses_header_not_url_token(monkeypatch, tmp_path):
    """Group cache must send access_token via header, not query params."""
    mod = load_dingtalk_group_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_groups.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "secret-token-GROUP")

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "name": "Engineering"}

    def fake_get(url, *args, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["params"] = kwargs.get("params")
        return FakeResponse()

    monkeypatch.setattr(mod.requests, "get", fake_get)

    mod.get_group_info("chatabcd1234", "app-key", "app-secret")

    params = captured.get("params") or {}
    assert "access_token" not in params, f"access_token leaked into request params: {params}"
    assert "secret-token-GROUP" not in captured["url"]
    headers = captured.get("headers") or {}
    header_values = " ".join(str(v) for v in headers.values())
    assert "secret-token-GROUP" in header_values


def test_userid_cache_ttl_is_shortened_to_limit_recycle_window(monkeypatch, tmp_path):
    """Because DingTalk recycles userids, the userid-keyed cache TTL must be materially
    shorter than the original 1h so a recycled id cannot be served stale for long.
    """
    mod = load_dingtalk_user_cache()
    # The original TTL was 3600s (1 hour). A recycled-userid defense requires a much
    # shorter window.
    assert (
        mod.CACHE_TTL <= 600
    ), f"userid-keyed cache TTL too long for recycled-id defense: {mod.CACHE_TTL}s"


def test_userid_cache_stores_stable_identity_and_invalidates_on_mismatch(monkeypatch, tmp_path):
    """Cache keyed by userid must (a) store a stable identity (unionid) and (b) when a
    refresh reveals the upstream identity changed for the same userid (recycled id),
    discard the stale entry and serve the fresh data.
    """
    mod = load_dingtalk_user_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_users.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "tok")

    # Seed the cache with a STALE entry: same userid, but an old unionid + expired TTL
    # so the refresh path runs and must detect the changed identity.
    import time

    stale_cached_at = time.time() - (mod.CACHE_TTL + 60)
    mod.save_cache(
        {
            "users": {
                "manager123": {
                    "data": {"name": "Old Owner", "unionid": "U_OLD"},
                    "cached_at": stale_cached_at,
                    "identity": "U_OLD",
                }
            }
        }
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "result": {"name": "New Person", "unionid": "U_NEW"}}

    monkeypatch.setattr(mod.requests, "post", lambda *a, **kw: FakeResponse())

    info = mod.get_user_info("manager123", "app-key", "app-secret")
    assert info is not None
    # The fresh (recycled-identity) data must be served.
    assert info.get("name") == "New Person"
    assert info.get("unionid") == "U_NEW"

    # The persisted cache entry must now record the NEW identity, not the stale one.
    cache = mod.load_cache()
    entry = cache["users"]["manager123"]
    assert (
        entry.get("identity") == "U_NEW"
    ), f"cache did not update stored identity to U_NEW: {entry}"


def test_user_cache_errors_logged_not_printed(monkeypatch, tmp_path, caplog):
    """Errors must go through logging (not bare print) and never echo the token URL."""
    mod = load_dingtalk_user_cache()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_FILE", tmp_path / "dingtalk_users.json")
    monkeypatch.setattr(mod, "get_dingtalk_access_token", lambda *_: "secret-token-LEAK")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod.requests, "post", boom)

    with caplog.at_level(logging.ERROR, logger=mod.__name__):
        # Redirect stdout to detect stray print() usage.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = mod.get_user_info("manager123", "app-key", "app-secret")

    assert result is None
    printed = buf.getvalue()
    # The token must never reach stdout/stderr.
    assert "secret-token-LEAK" not in printed, f"token printed to stdout: {printed!r}"
    # An error must be emitted through logging (logger.error/exception).
    assert any(
        "user info" in rec.message.lower() or "dingtalk" in rec.message.lower()
        for rec in caplog.records
    ), f"expected a logged error; got records={[r.message for r in caplog.records]}"


# ---- fetch_openclaw sender resolution gap ----


def load_fetch_openclaw():
    module_path = Path(__file__).resolve().parents[3] / "scripts" / "fetch_openclaw.py"
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("fetch_openclaw", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_dingtalk_simple_userid_message_resolves_sender():
    """A plain DingTalk userid-prefixed message must produce sender_id + message_source=dingtalk.

    Real DingTalk userids do NOT match the ou_/on_/oc_/U[A-Z0-9]+ prefixes used by the
    current simple_match branch, so these messages silently get sender_id=None.
    """
    mod = load_fetch_openclaw()
    # A message that is shaped like a DingTalk user message: userid prefix + source hint.
    text = (
        '"message_source": "dingtalk"\n'
        '"sender_id": "manager789"\n'
        "manager789: please review the deployment plan"
    )
    meta = mod.extract_user_message_metadata(text)
    assert meta["message_source"] == "dingtalk"
    # The sender must resolve (not None) for a dingtalk-shaped message.
    assert (
        meta.get("sender_id") == "manager789"
    ), f"sender_id not resolved for DingTalk message: {meta}"
