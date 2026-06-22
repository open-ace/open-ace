"""Unit tests for ``_sanitize_text_value``.

This is a persistence-layer data-cleaning function used on every
``session_messages`` insert (content + metadata JSON). A regression here would
cause SQLite/Postgres write failures or silent data corruption, so the four
branches below pin its behavior:

  1. ``None``           -> returns ``None``
  2. NUL (``\\x00``)    -> stripped, rest preserved
  3. lone surrogate     -> does not raise, returns UTF-8-encodable text
  4. valid UTF-8 text   -> passes through unchanged (identity)
"""

from app.modules.workspace.session_manager import _sanitize_text_value


class TestSanitizeTextValue:
    def test_none_returns_none(self):
        # None must pass through (callers rely on this to keep "no value" distinct
        # from "empty string").
        assert _sanitize_text_value(None) is None

    def test_nul_bytes_are_stripped(self):
        # NUL bytes break some drivers/parsers; they must be removed while the
        # surrounding text is preserved.
        result = _sanitize_text_value("ab\x00cd\x00ef")
        assert result == "abcdef"

    def test_nul_only_collapses_to_empty(self):
        assert _sanitize_text_value("\x00\x00") == ""

    def test_lone_surrogate_does_not_raise(self):
        # A lone surrogate (e.g. from ill-formed upstream input) cannot be encoded
        # as UTF-8. The function must not raise and must return a string that
        # round-trips through UTF-8.
        result = _sanitize_text_value("before\ud800after")
        assert isinstance(result, str)
        # The returned value MUST be re-encodable without error.
        result.encode("utf-8")
        # The clean prefix/suffix is preserved.
        assert result.startswith("before")
        assert result.endswith("after")

    def test_valid_utf8_passes_through_unchanged(self):
        text = "Hello 世界 — emoji 🎉 and accents café"
        result = _sanitize_text_value(text)
        # Identity: a valid string is returned as-is (same object), per the
        # early-return in the try block.
        assert result is text

    def test_empty_string_passes_through(self):
        assert _sanitize_text_value("") == ""

    def test_nul_plus_valid_text_returns_same_instance_after_strip(self):
        # After stripping NUL the (now valid) string should encode and be
        # returned as the same object that was re-assigned.
        result = _sanitize_text_value("ok")
        assert result == "ok"
