"""Unit tests for validators."""

import pytest

from app.utils.validators import (
    validate_date,
    validate_email,
    validate_host_name,
    validate_password,
    validate_tool_name,
    validate_username,
)


class TestValidateDate:
    """Test validate_date function."""

    @pytest.mark.parametrize(
        "date_str,expected",
        [
            ("2024-01-15", True),
            ("0001-01-01", True),
            ("2024-12-31", True),
            ("2024-02-29", True),
            ("2026-05-23", True),
        ],
        ids=[
            "normal",
            "boundary_start",
            "end_of_year",
            "leap_year",
            "recent",
        ],
    )
    def test_valid_date(self, date_str, expected):
        assert validate_date(date_str) is expected

    @pytest.mark.parametrize(
        "date_str",
        [
            "",
            "2024/01/15",
            "2024.01.15",
            "2024-13-01",
            "2024-01-32",
            "2024-02-30",
            "2023-02-29",
            "not-a-date",
            "2024-01",
            "2024-01-15 10:30:00",
            "2024-1-15",
            "2024-01-5",
            " 2024-01-15 ",
            "2024-04-31",
        ],
        ids=[
            "empty_string",
            "wrong_format_slash",
            "wrong_format_dot",
            "month_out_of_range",
            "day_out_of_range",
            "feb_30",
            "non_leap_feb_29",
            "text",
            "partial",
            "with_time",
            "single_digit_month",
            "single_digit_day",
            "extra_whitespace",
            "april_31",
        ],
    )
    def test_invalid_date(self, date_str):
        assert validate_date(date_str) is False


class TestValidateToolName:
    """Test validate_tool_name function."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("mytool123", True),
            ("my_tool", True),
            ("my-tool", True),
            ("My_Tool-123", True),
            ("a", True),
            ("1", True),
            ("_", True),
            ("-", True),
        ],
        ids=[
            "alphanumeric",
            "with_underscore",
            "with_hyphen",
            "all_chars",
            "single_char",
            "single_digit",
            "underscore_only",
            "hyphen_only",
        ],
    )
    def test_valid_tool_name(self, name, expected):
        assert validate_tool_name(name) is expected

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "my tool",
            "my.tool",
            "tool@name",
            "tool/name",
            "tool名前",
            "tool\nname",
        ],
        ids=[
            "empty",
            "with_space",
            "with_dot",
            "with_special_chars",
            "with_slash",
            "with_cjk",
            "with_newline",
        ],
    )
    def test_invalid_tool_name(self, name):
        assert validate_tool_name(name) is False


class TestValidateHostName:
    """Test validate_host_name function."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("localhost", True),
            ("example.com", True),
            ("sub.example.com", True),
            ("my_host", True),
            ("my-host", True),
            ("my_host.example-domain.com", True),
            ("192.168.1.1", True),
            ("a", True),
        ],
        ids=[
            "simple",
            "with_dot",
            "with_subdomain",
            "with_underscore",
            "with_hyphen",
            "with_all_chars",
            "ip_like",
            "single_char",
        ],
    )
    def test_valid_host_name(self, name, expected):
        assert validate_host_name(name) is expected

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "my host",
            "host@domain",
            "host:8080",
            "host/path",
            "主机",
        ],
        ids=[
            "empty",
            "with_space",
            "with_at_sign",
            "with_colon",
            "with_slash",
            "with_cjk",
        ],
    )
    def test_invalid_host_name(self, name):
        assert validate_host_name(name) is False


class TestValidateUsername:
    """Test validate_username function."""

    @pytest.mark.parametrize(
        "username,expected",
        [
            ("user123", True),
            ("user_name", True),
            ("user-name", True),
            ("张三", True),
            ("䶿字", True),
            ("user名", True),
            ("ab", True),
            ("a" * 50, True),
            ("李小明", True),
        ],
        ids=[
            "alphanumeric",
            "with_underscore",
            "with_hyphen",
            "chinese_chars",
            "cjk_extension",
            "mixed_ascii_cjk",
            "two_chars",
            "50_chars",
            "all_chinese",
        ],
    )
    def test_valid_username(self, username, expected):
        assert validate_username(username) is expected

    @pytest.mark.parametrize(
        "username",
        [
            "",
            "a",
            "a" * 51,
            "user name",
            "user.name",
            "user@domain",
            "user!name",
            "user/name",
            "あい",
            "안녕",
        ],
        ids=[
            "empty",
            "too_short_one_char",
            "too_long_51_chars",
            "with_space",
            "with_dot",
            "with_at_sign",
            "with_special_chars",
            "with_slash",
            "japanese_hiragana",
            "korean",
        ],
    )
    def test_invalid_username(self, username):
        assert validate_username(username) is False


class TestValidateEmail:
    """Test validate_email function."""

    @pytest.mark.parametrize(
        "email,expected",
        [
            ("user@example.com", True),
            ("first.last@example.com", True),
            ("user+tag@example.com", True),
            ("user@my-domain.com", True),
            ("user@sub.example.com", True),
            ("user123@example456.com", True),
            ("user%name@example.com", True),
            ("user@example.co", True),
            ("user@example.museum", True),
            ("user_name@example.com", True),
        ],
        ids=[
            "simple",
            "dot_in_local",
            "with_plus",
            "hyphen_domain",
            "with_subdomain",
            "with_digits",
            "with_percent",
            "short_tld",
            "long_tld",
            "underscore_local",
        ],
    )
    def test_valid_email(self, email, expected):
        assert validate_email(email) is expected

    @pytest.mark.parametrize(
        "email",
        [
            "",
            "userexample.com",
            "user@",
            "@example.com",
            "user@example.",
            "user@example.c",
            "user @example.com",
            "user@@example.com",
            "user@exam!ple.com",
        ],
        ids=[
            "empty",
            "no_at",
            "no_domain",
            "no_local",
            "no_tld",
            "tld_too_short",
            "spaces",
            "double_at",
            "special_in_domain",
        ],
    )
    def test_invalid_email(self, email):
        assert validate_email(email) is False


class TestValidatePassword:
    """Test validate_password function."""

    @pytest.mark.parametrize(
        "password",
        [
            "12345678",
            "a" * 128,
            "MyP@ss123!",
            "abcdefgh",
        ],
        ids=[
            "8_chars",
            "long_password",
            "mixed_chars",
            "exactly_8_chars",
        ],
    )
    def test_valid_password(self, password):
        is_valid, msg = validate_password(password)
        assert is_valid is True
        assert msg is None

    @pytest.mark.parametrize(
        "password,expected_in_msg",
        [
            ("", "required"),
            ("1234567", "8"),
            ("a", "8"),
            ("a" * 129, "128"),
            ("x" * 200, "128"),
        ],
        ids=[
            "empty",
            "too_short_7_chars",
            "too_short_1_char",
            "too_long_129_chars",
            "too_long_200_chars",
        ],
    )
    def test_invalid_password(self, password, expected_in_msg):
        is_valid, msg = validate_password(password)
        assert is_valid is False
        assert expected_in_msg in msg.lower()
