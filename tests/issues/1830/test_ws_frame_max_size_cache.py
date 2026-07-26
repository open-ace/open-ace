"""Regression tests for issue #1830 — cache OPENACE_WS_MAX_MESSAGE_BYTES parsing.

``app.ws_frame.get_max_message_size`` is invoked once per ``recv_message``
call (and therefore once per inbound WebSocket frame message). The original
implementation re-parsed ``OPENACE_WS_MAX_MESSAGE_BYTES`` from the environment
on every call.

These tests lock in two properties of the self-invalidating cache (plan v4
Method B):

1. **Performance** — repeated reads under an unchanged environment value must
   not re-parse. The parse step is factored into ``_parse_max_message_size``
   *precisely so this can be observed* via ``monkeypatch``. Do NOT inline the
   parser back into ``get_max_message_size``: doing so makes the perf goal
   untestable (see S1).
2. **Self-invalidation** — when the environment value changes between calls,
   the cache must miss and re-parse, so the 559/1746 regression tests that
   mutate ``OPENACE_WS_MAX_MESSAGE_BYTES`` at runtime keep working with zero
   changes.

Cache state isolation
---------------------
This module installs its own autouse fixture that resets
``ws_frame._cache`` before every test. This is *orthogonal* to the global
``tests/conftest.py::_clear_cache`` autouse fixture (which clears
``get_cache()`` and ``_security_settings_cache`` but does NOT touch
``ws_frame``). The two fixtures clean different state and do not conflict.
"""

from __future__ import annotations

import pytest

import app.ws_frame as ws_frame

ENV_VAR = "OPENACE_WS_MAX_MESSAGE_BYTES"


@pytest.fixture(autouse=True)
def _reset_ws_frame_cache():
    """Reset the ws_frame parse cache before each test.

    Writing ``ws_frame._cache = None`` is a *module attribute assignment*
    (an attribute-access expression, not a bare-name assignment), so it needs
    no ``global`` declaration — unlike the bare ``_cache =`` assignment inside
    ``get_max_message_size``, which MUST declare ``global _cache`` or raise
    ``UnboundLocalError`` on the very first call.
    """
    ws_frame._cache = None
    yield
    # Leave the cache clean for any test that runs after this module in the
    # same process. Self-invalidation already makes a stale entry harmless,
    # but a clean slate avoids any confusion while debugging failures.
    ws_frame._cache = None


def _counting_parse():
    """Wrap ``_parse_max_message_size`` with a call counter.

    Returns ``(wrapper, state)`` where ``state["count"]`` tracks how many
    times the parser actually ran. A cache *hit* must NOT increment the
    counter.
    """
    original = ws_frame._parse_max_message_size
    state = {"count": 0}

    def wrapper(raw: str) -> int:
        state["count"] += 1
        return original(raw)

    return wrapper, state


class TestCacheHitSkipsReparse:
    """Performance goal: identical env value → parser runs at most once."""

    def test_two_calls_parse_once(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "100")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        first = ws_frame.get_max_message_size()
        second = ws_frame.get_max_message_size()

        assert first == 100
        assert second == 100
        assert state["count"] == 1

    def test_legal_value_cached_across_many_calls(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "2048")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        results = [ws_frame.get_max_message_size() for _ in range(10)]

        assert all(r == 2048 for r in results)
        assert state["count"] == 1

    def test_non_numeric_fallback_is_cached(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "abc")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        first = ws_frame.get_max_message_size()
        second = ws_frame.get_max_message_size()

        assert first == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert second == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        # Fallback result must be cached — second call must not re-parse.
        assert state["count"] == 1

    def test_zero_or_negative_fallback_is_cached(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "0")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert state["count"] == 1

    def test_empty_string_fallback_is_cached(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert state["count"] == 1


class TestSelfInvalidation:
    """Method B core: env change between calls → cache miss + re-parse."""

    def test_value_change_reparses(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "100")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        assert ws_frame.get_max_message_size() == 100
        assert state["count"] == 1

        monkeypatch.setenv(ENV_VAR, "200")
        assert ws_frame.get_max_message_size() == 200
        assert state["count"] == 2

    def test_legal_then_illegal_transitions_to_fallback(self, monkeypatch):
        # Legitimate value, cached.
        monkeypatch.setenv(ENV_VAR, "100")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)
        assert ws_frame.get_max_message_size() == 100

        # Switch to an illegal value — must miss, re-parse, and fall back.
        monkeypatch.setenv(ENV_VAR, "abc")
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert state["count"] == 2

        # The fallback result is itself cached: a third call with the same
        # illegal value must NOT re-parse.
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE
        assert state["count"] == 2

    def test_illegal_then_legal_invalidates_fallback_cache(self, monkeypatch):
        # Illegal value → fallback cached.
        monkeypatch.setenv(ENV_VAR, "abc")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE

        # Switch back to a legitimate value — fallback cache must be
        # invalidated so the real value takes effect.
        monkeypatch.setenv(ENV_VAR, "100")
        assert ws_frame.get_max_message_size() == 100
        assert state["count"] == 2

    def test_whitespace_only_then_value_reparses(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "   ")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)
        assert ws_frame.get_max_message_size() == ws_frame.DEFAULT_MAX_MESSAGE_SIZE

        monkeypatch.setenv(ENV_VAR, "100")
        assert ws_frame.get_max_message_size() == 100
        assert state["count"] == 2


class TestCacheKeyStripsWhitespace:
    """R2: cache key is the *stripped* raw string (matches the original code
    that called ``.strip()`` before parsing)."""

    def test_surrounding_whitespace_parses_to_value(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "  100  ")
        assert ws_frame.get_max_message_size() == 100

    def test_stripped_equivalents_share_cache_entry(self, monkeypatch):
        # " 100 " and "100" both strip to "100" → the second call must hit
        # the cache populated by the first and NOT re-parse.
        monkeypatch.setenv(ENV_VAR, " 100 ")
        wrapper, state = _counting_parse()
        monkeypatch.setattr(ws_frame, "_parse_max_message_size", wrapper)

        assert ws_frame.get_max_message_size() == 100
        assert state["count"] == 1

        monkeypatch.setenv(ENV_VAR, "100")
        assert ws_frame.get_max_message_size() == 100
        # Stripped keys are equal → still one parse.
        assert state["count"] == 1


class TestValueConsistency:
    """G1/G3: within a stable environment the cap is stable across calls
    (locks the consistency status quo that Method B deliberately keeps)."""

    def test_repeated_calls_return_identical_value(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "4096")
        values = {ws_frame.get_max_message_size() for _ in range(15)}
        assert values == {4096}

    def test_no_env_returns_default_consistently(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        values = {ws_frame.get_max_message_size() for _ in range(15)}
        assert values == {ws_frame.DEFAULT_MAX_MESSAGE_SIZE}

    def test_default_matches_documented_cap(self):
        assert ws_frame.DEFAULT_MAX_MESSAGE_SIZE == 8 * 1024 * 1024
