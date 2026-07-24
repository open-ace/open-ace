#!/usr/bin/env python3
"""Issue #1829 — F4: process-local DingTalk access-token caching with the real
``expireIn``.

Previously every uncached user lookup fetched a fresh access token, so importing
N new users triggered N oauth2/accessToken calls (which DingTalk rate-limits).
The cache keys on ``(app_key, app_secret)`` and lives until the API's real
``expireIn`` (minus a safety margin) elapses, so N lookups make at most one call.
"""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_DIR = REPO_ROOT / "scripts" / "shared"


def _load_cache_mod():
    if str(SHARED_DIR) not in sys.path:
        sys.path.insert(0, str(SHARED_DIR))
    import dingtalk_user_cache as mod

    return importlib.reload(mod)


def _resp(token: str, expire_in: int):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"accessToken": token, "expireIn": expire_in}
    return r


class TestF4AccessTokenCache:
    def test_repeated_lookups_make_one_api_call(self):
        mod = _load_cache_mod()
        mod._access_token_cache.clear()
        with patch.object(mod.requests, "post", return_value=_resp("tok-1", 7200)) as mock_post:
            tokens = [mod.get_dingtalk_access_token("key", "secret") for _ in range(5)]
        assert all(t == "tok-1" for t in tokens)
        assert mock_post.call_count == 1

    def test_cached_expiry_uses_real_expirein(self):
        """The slot TTL must derive from the API's real ``expireIn``, not a
        hardcoded constant. expireIn=1000, margin=300 → ~700s window."""
        mod = _load_cache_mod()
        mod._access_token_cache.clear()
        before = time.time()
        with patch.object(mod.requests, "post", return_value=_resp("tok", 1000)):
            mod.get_dingtalk_access_token("k", "s")
        token, expires_at = mod._access_token_cache[("k", "s")]
        assert token == "tok"
        # ~700s from `before`, definitely not a hardcoded 7200.
        assert before + 600 < expires_at < before + 800

    def test_failed_fetch_is_not_cached(self):
        """A failed fetch must not poison the cache: the next attempt still
        calls the API (leaves the slot empty for retry)."""
        mod = _load_cache_mod()
        mod._access_token_cache.clear()
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("boom")
        with patch.object(mod.requests, "post", return_value=resp) as mock_post:
            assert mod.get_dingtalk_access_token("k", "s") is None
            assert mod.get_dingtalk_access_token("k", "s") is None
        assert ("k", "s") not in mod._access_token_cache
        assert mock_post.call_count == 2

    def test_distinct_credentials_cached_separately(self):
        mod = _load_cache_mod()
        mod._access_token_cache.clear()
        with patch.object(
            mod.requests,
            "post",
            side_effect=[_resp("a", 7200), _resp("b", 7200), _resp("a", 7200), _resp("b", 7200)],
        ) as mock_post:
            assert mod.get_dingtalk_access_token("k1", "s1") == "a"
            assert mod.get_dingtalk_access_token("k2", "s2") == "b"
            assert mod.get_dingtalk_access_token("k1", "s1") == "a"  # cached
            assert mod.get_dingtalk_access_token("k2", "s2") == "b"  # cached
        assert mock_post.call_count == 2

    def test_public_signature_unchanged_returns_str_or_none(self):
        """Existing callers (get_user_info, group cache wrapper) rely on the
        ``str | None`` return; F4 kept the signature and moved caching inside."""
        mod = _load_cache_mod()
        mod._access_token_cache.clear()
        with patch.object(mod.requests, "post", return_value=_resp("tok", 7200)):
            result = mod.get_dingtalk_access_token("k", "s")
        assert isinstance(result, str)
