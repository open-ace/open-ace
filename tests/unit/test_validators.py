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

    # --- Valid inputs ---

    def test_valid_date_normal(self):
        assert validate_date("2024-01-15") is True

    def test_valid_date_boundary_start(self):
        assert validate_date("0001-01-01") is True

    def test_valid_date_end_of_year(self):
        assert validate_date("2024-12-31") is True

    def test_valid_date_leap_year(self):
        assert validate_date("2024-02-29") is True

    def test_valid_date_recent(self):
        assert validate_date("2026-05-23") is True

    # --- Invalid inputs ---

    def test_invalid_date_empty_string(self):
        assert validate_date("") is False

    def test_invalid_date_none_like_empty(self):
        assert validate_date("") is False

    def test_invalid_date_wrong_format_slash(self):
        assert validate_date("2024/01/15") is False

    def test_invalid_date_wrong_format_dot(self):
        assert validate_date("2024.01.15") is False

    def test_invalid_date_month_out_of_range(self):
        assert validate_date("2024-13-01") is False

    def test_invalid_date_day_out_of_range(self):
        assert validate_date("2024-01-32") is False

    def test_invalid_date_feb_30(self):
        assert validate_date("2024-02-30") is False

    def test_invalid_date_non_leap_feb_29(self):
        assert validate_date("2023-02-29") is False

    def test_invalid_date_text(self):
        assert validate_date("not-a-date") is False

    def test_invalid_date_partial(self):
        assert validate_date("2024-01") is False

    def test_invalid_date_with_time(self):
        assert validate_date("2024-01-15 10:30:00") is False

    def test_invalid_date_single_digit_month(self):
        assert validate_date("2024-1-15") is False

    def test_invalid_date_single_digit_day(self):
        assert validate_date("2024-01-5") is False

    def test_invalid_date_extra_whitespace(self):
        assert validate_date(" 2024-01-15 ") is False

    def test_invalid_date_april_31(self):
        assert validate_date("2024-04-31") is False


class TestValidateToolName:
    """Test validate_tool_name function."""

    # --- Valid inputs ---

    def test_valid_alphanumeric(self):
        assert validate_tool_name("mytool123") is True

    def test_valid_with_underscore(self):
        assert validate_tool_name("my_tool") is True

    def test_valid_with_hyphen(self):
        assert validate_tool_name("my-tool") is True

    def test_valid_all_chars(self):
        assert validate_tool_name("My_Tool-123") is True

    def test_valid_single_char(self):
        assert validate_tool_name("a") is True

    def test_valid_single_digit(self):
        assert validate_tool_name("1") is True

    def test_valid_underscore_only(self):
        assert validate_tool_name("_") is True

    def test_valid_hyphen_only(self):
        assert validate_tool_name("-") is True

    # --- Invalid inputs ---

    def test_invalid_empty_string(self):
        assert validate_tool_name("") is False

    def test_invalid_with_space(self):
        assert validate_tool_name("my tool") is False

    def test_invalid_with_dot(self):
        assert validate_tool_name("my.tool") is False

    def test_invalid_with_special_chars(self):
        assert validate_tool_name("tool@name") is False

    def test_invalid_with_slash(self):
        assert validate_tool_name("tool/name") is False

    def test_invalid_with_cjk(self):
        assert validate_tool_name("tool名前") is False

    def test_invalid_with_newline(self):
        assert validate_tool_name("tool\nname") is False


class TestValidateHostName:
    """Test validate_host_name function."""

    # --- Valid inputs ---

    def test_valid_simple(self):
        assert validate_host_name("localhost") is True

    def test_valid_with_dot(self):
        assert validate_host_name("example.com") is True

    def test_valid_with_subdomain(self):
        assert validate_host_name("sub.example.com") is True

    def test_valid_with_underscore(self):
        assert validate_host_name("my_host") is True

    def test_valid_with_hyphen(self):
        assert validate_host_name("my-host") is True

    def test_valid_with_all_chars(self):
        assert validate_host_name("my_host.example-domain.com") is True

    def test_valid_ip_like(self):
        assert validate_host_name("192.168.1.1") is True

    def test_valid_single_char(self):
        assert validate_host_name("a") is True

    # --- Invalid inputs ---

    def test_invalid_empty(self):
        assert validate_host_name("") is False

    def test_invalid_with_space(self):
        assert validate_host_name("my host") is False

    def test_invalid_with_at_sign(self):
        assert validate_host_name("host@domain") is False

    def test_invalid_with_colon(self):
        assert validate_host_name("host:8080") is False

    def test_invalid_with_slash(self):
        assert validate_host_name("host/path") is False

    def test_invalid_with_cjk(self):
        assert validate_host_name("主机") is False


class TestValidateUsername:
    """Test validate_username function."""

    # --- Valid inputs ---

    def test_valid_alphanumeric(self):
        assert validate_username("user123") is True

    def test_valid_with_underscore(self):
        assert validate_username("user_name") is True

    def test_valid_with_hyphen(self):
        assert validate_username("user-name") is True

    def test_valid_chinese_chars(self):
        assert validate_username("张三") is True  # Chinese name

    def test_valid_cjk_extension(self):
        assert validate_username("䶿字") is True  # CJK Extension A chars (needs >= 2 chars)

    def test_valid_mixed_ascii_cjk(self):
        assert validate_username("user名") is True

    def test_valid_two_chars(self):
        assert validate_username("ab") is True

    def test_valid_50_chars(self):
        assert validate_username("a" * 50) is True

    def test_valid_all_chinese(self):
        assert validate_username("李小明") is True

    # --- Invalid inputs ---

    def test_invalid_empty(self):
        assert validate_username("") is False

    def test_invalid_too_short_one_char(self):
        assert validate_username("a") is False

    def test_invalid_too_long_51_chars(self):
        assert validate_username("a" * 51) is False

    def test_invalid_with_space(self):
        assert validate_username("user name") is False

    def test_invalid_with_dot(self):
        assert validate_username("user.name") is False

    def test_invalid_with_at_sign(self):
        assert validate_username("user@domain") is False

    def test_invalid_with_special_chars(self):
        assert validate_username("user!name") is False

    def test_invalid_with_slash(self):
        assert validate_username("user/name") is False

    def test_invalid_japanese_hiragana(self):
        # Hiragana is outside the CJK ranges defined in the regex
        assert validate_username("あい") is False

    def test_invalid_korean(self):
        # Korean Hangul is outside the CJK ranges defined in the regex
        assert validate_username("안녕") is False


class TestValidateEmail:
    """Test validate_email function."""

    # --- Valid inputs ---

    def test_valid_simple(self):
        assert validate_email("user@example.com") is True

    def test_valid_with_dot_in_local(self):
        assert validate_email("first.last@example.com") is True

    def test_valid_with_plus(self):
        assert validate_email("user+tag@example.com") is True

    def test_valid_with_hyphen_domain(self):
        assert validate_email("user@my-domain.com") is True

    def test_valid_with_subdomain(self):
        assert validate_email("user@sub.example.com") is True

    def test_valid_with_digits(self):
        assert validate_email("user123@example456.com") is True

    def test_valid_with_percent(self):
        assert validate_email("user%name@example.com") is True

    def test_valid_short_tld(self):
        assert validate_email("user@example.co") is True

    def test_valid_long_tld(self):
        assert validate_email("user@example.museum") is True

    def test_valid_with_underscore_local(self):
        assert validate_email("user_name@example.com") is True

    # --- Invalid inputs ---

    def test_invalid_empty(self):
        assert validate_email("") is False

    def test_invalid_no_at(self):
        assert validate_email("userexample.com") is False

    def test_invalid_no_domain(self):
        assert validate_email("user@") is False

    def test_invalid_no_local(self):
        assert validate_email("@example.com") is False

    def test_invalid_no_tld(self):
        assert validate_email("user@example.") is False

    def test_invalid_tld_too_short(self):
        assert validate_email("user@example.c") is False

    def test_invalid_spaces(self):
        assert validate_email("user @example.com") is False

    def test_invalid_double_at(self):
        assert validate_email("user@@example.com") is False

    def test_invalid_special_in_domain(self):
        assert validate_email("user@exam!ple.com") is False


class TestValidatePassword:
    """Test validate_password function."""

    # --- Valid inputs ---

    def test_valid_8_chars(self):
        is_valid, msg = validate_password("12345678")
        assert is_valid is True
        assert msg is None

    def test_valid_long_password(self):
        is_valid, msg = validate_password("a" * 128)
        assert is_valid is True
        assert msg is None

    def test_valid_mixed_chars(self):
        is_valid, msg = validate_password("MyP@ss123!")
        assert is_valid is True
        assert msg is None

    def test_valid_exactly_8_chars(self):
        is_valid, msg = validate_password("abcdefgh")
        assert is_valid is True
        assert msg is None

    def test_valid_exactly_128_chars(self):
        is_valid, msg = validate_password("a" * 128)
        assert is_valid is True
        assert msg is None

    # --- Invalid inputs ---

    def test_invalid_empty(self):
        is_valid, msg = validate_password("")
        assert is_valid is False
        assert "required" in msg.lower()

    def test_invalid_none_like_empty(self):
        is_valid, msg = validate_password("")
        assert is_valid is False

    def test_invalid_too_short_7_chars(self):
        is_valid, msg = validate_password("1234567")
        assert is_valid is False
        assert "8" in msg

    def test_invalid_too_short_1_char(self):
        is_valid, msg = validate_password("a")
        assert is_valid is False
        assert "8" in msg

    def test_invalid_too_long_129_chars(self):
        is_valid, msg = validate_password("a" * 129)
        assert is_valid is False
        assert "128" in msg

    def test_invalid_too_long_200_chars(self):
        is_valid, msg = validate_password("x" * 200)
        assert is_valid is False
        assert "128" in msg
