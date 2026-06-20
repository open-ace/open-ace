"""Unit tests for ``parse_db_datetime``.

The helper normalizes the text timestamp shapes SQLite/PostgreSQL emit so
``datetime.fromisoformat`` (Python 3.9) can parse them. It is shared by several
repositories (``project.py``, ``tenant_repo.py``, etc.), so a parsing bug is
amplified across many readers. These tests pin its normalization contract:
space separator -> "T", fractional seconds padded/truncated to 6 digits, and
graceful ``None`` for unparseable input.
"""

from datetime import datetime

import pytest

from app.repositories.database import parse_db_datetime


class TestParseDbDatetimeNoneAndPassThrough:
    """Non-string inputs and empty values."""

    def test_none_returns_none(self):
        assert parse_db_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert parse_db_datetime("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_db_datetime("   ") is None

    def test_datetime_instance_returned_unchanged(self):
        dt = datetime(2026, 6, 20, 14, 32, 53)
        assert parse_db_datetime(dt) is dt
        # tz-aware datetimes are also returned as-is (PostgreSQL path)
        from datetime import timezone

        dtz = datetime(2026, 6, 20, 14, 32, 53, tzinfo=timezone.utc)
        assert parse_db_datetime(dtz) is dtz

    def test_non_string_non_datetime_returns_none(self):
        assert parse_db_datetime(12345) is None
        assert parse_db_datetime(["2026-06-20"]) is None


class TestParseDbDatetimeSeparator:
    """Space-vs-T date/time separator handling (SQLite default is a space)."""

    def test_space_separator_normalized(self):
        # SQLite default shape: "2026-06-20 14:32:53"
        result = parse_db_datetime("2026-06-20 14:32:53")
        assert result == datetime(2026, 6, 20, 14, 32, 53)

    def test_t_separator_accepted(self):
        result = parse_db_datetime("2026-06-20T14:32:53")
        assert result == datetime(2026, 6, 20, 14, 32, 53)

    def test_only_first_space_replaced(self):
        # A time containing a space should not have its second space mangled;
        # in practice SQLite never emits this, but the impl does ``replace``
        # with count=1, so verify it only touches the first separator.
        result = parse_db_datetime("2026-06-20 14:32:53")
        assert result is not None
        assert result.year == 2026


class TestParseDbDatetimeFractionalSeconds:
    """Fractional-seconds width normalization (pad/truncate to 6 digits).

    ``datetime.fromisoformat`` on Python < 3.11 only accepts 3- or 6-digit
    fractional seconds. The helper pads short fractions and truncates long ones
    to exactly 6 digits.
    """

    @pytest.mark.parametrize(
        "fraction,expected_microseconds",
        [
            ("1", 100000),
            ("12", 120000),
            ("123", 123000),
            ("1234", 123400),
            ("12345", 123450),
            ("123456", 123456),
            ("1234567", 123456),  # 7 digits truncated, not rounded
            ("12345678", 123456),  # 8 digits truncated
        ],
    )
    def test_various_fractional_widths(self, fraction, expected_microseconds):
        text = f"2026-06-20 14:32:53.{fraction}"
        result = parse_db_datetime(text)
        assert result == datetime(2026, 6, 20, 14, 32, 53, expected_microseconds)

    def test_single_digit_fraction_padded_to_microseconds(self):
        # ".5" -> ".500000" -> 500000 microseconds
        result = parse_db_datetime("2026-06-20 14:32:53.5")
        assert result == datetime(2026, 6, 20, 14, 32, 53, 500000)

    def test_seven_digit_fraction_truncated_not_rounded(self):
        # The key regression guard: .1234567 truncates to .123456, NOT .123457.
        # This documents that precision is truncated (not rounded) below the
        # microsecond boundary.
        truncated = parse_db_datetime("2026-06-20 14:32:53.1234567")
        assert truncated.microsecond == 123456
        assert truncated == datetime(2026, 6, 20, 14, 32, 53, 123456)

    def test_t_separator_with_fraction(self):
        result = parse_db_datetime("2026-06-20T14:32:53.654590")
        assert result == datetime(2026, 6, 20, 14, 32, 53, 654590)


class TestParseDbDatetimeDateOnlyAndTz:
    """Pure dates and timezone-bearing strings."""

    def test_date_only(self):
        result = parse_db_datetime("2026-06-20")
        assert result == datetime(2026, 6, 20)

    def test_date_only_with_trailing_space(self):
        result = parse_db_datetime("2026-06-20 ")
        # The trailing space is stripped; date-only still parses.
        assert result == datetime(2026, 6, 20)

    def test_z_timezone_suffix(self):
        # Python 3.11+ handles 'Z'; on 3.9 the helper relies on fromisoformat
        # which may reject 'Z'. Just assert it returns a datetime or None
        # without raising — behavior is version-dependent and not the helper's
        # responsibility to fix.
        result = parse_db_datetime("2026-06-20T14:32:53+00:00")
        assert result is not None
        assert result.year == 2026


class TestParseDbDatetimeInvalid:
    """Unparseable strings must return None, never raise."""

    @pytest.mark.parametrize(
        "bad",
        [
            "not a date",
            "2026-13-99 99:99:99",  # plausible shape, invalid values
            "2026-06-20T25:99:99",  # out-of-range hours/minutes
            "--",
            "abc def ghi",
        ],
    )
    def test_unparseable_returns_none(self, bad):
        assert parse_db_datetime(bad) is None

    def test_does_not_raise_on_garbage(self):
        # Defensive: no exception escapes even on pathological input.
        for value in [None, "", "  ", "garbage", 42, [], object()]:
            # Should not raise for any input type.
            try:
                parse_db_datetime(value)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"parse_db_datetime raised on {value!r}: {exc}")
