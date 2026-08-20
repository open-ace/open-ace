"""Unit tests for validators.

Issue #2738: Added tests for date range validation.
"""

import pytest

from app.utils.validators import (
    validate_date,
    validate_date_range,
    validate_email,
    validate_host_name,
    validate_password,
    validate_project_name,
    validate_time_window,
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

    @pytest.mark.parametrize(
        "password,policy_settings,expected_valid",
        [
            # No policy - basic validation only
            ("12345678", None, True),
            ("abcdefgh", None, True),
            # Policy with min_length
            ("12345678", {"password_min_length": 10}, False),
            ("1234567890", {"password_min_length": 10}, True),
            # An admin-configured minimum below the default 8 is respected
            # (no hardcoded floor overrides the policy).
            ("123456", {"password_min_length": 6}, True),
            ("12345", {"password_min_length": 6}, False),
            # A negative / zero misconfiguration is floored to 1 rather than
            # disabling the check or breaking validation.
            ("ValidPass1", {"password_min_length": -100}, True),
            # Policy requiring uppercase
            ("abcdefgh", {"password_require_uppercase": True}, False),
            ("Abcdefgh", {"password_require_uppercase": True}, True),
            # Policy requiring lowercase
            ("ABCDEFGH", {"password_require_lowercase": True}, False),
            ("ABCDefgh", {"password_require_lowercase": True}, True),
            # Policy requiring number
            ("abcdefgh", {"password_require_number": True}, False),
            ("abcdefg1", {"password_require_number": True}, True),
            # Policy requiring special character
            ("abcdefgh", {"password_require_special": True}, False),
            ("abcdefg!", {"password_require_special": True}, True),
            # Combined policy requirements
            (
                "Abcdefg1",
                {
                    "password_require_uppercase": True,
                    "password_require_lowercase": True,
                    "password_require_number": True,
                },
                True,
            ),
            (
                "Abcdefg!",
                {
                    "password_require_uppercase": True,
                    "password_require_lowercase": True,
                    "password_require_special": True,
                },
                True,
            ),
            (
                "abcdefg1",
                {
                    "password_require_uppercase": True,
                    "password_require_number": True,
                },
                False,
            ),  # Missing uppercase
            # Full policy requirements
            (
                "Abcdefg1!",
                {
                    "password_min_length": 8,
                    "password_require_uppercase": True,
                    "password_require_lowercase": True,
                    "password_require_number": True,
                    "password_require_special": True,
                },
                True,
            ),
            (
                "Abcdefg1",
                {
                    "password_min_length": 8,
                    "password_require_uppercase": True,
                    "password_require_lowercase": True,
                    "password_require_number": True,
                    "password_require_special": True,
                },
                False,
            ),  # Missing special
        ],
        ids=[
            "no_policy_basic_valid",
            "no_policy_basic_valid_2",
            "min_length_10_fail",
            "min_length_10_pass",
            "min_length_below_default_pass",
            "min_length_below_default_fail",
            "negative_min_length_floored",
            "require_uppercase_fail",
            "require_uppercase_pass",
            "require_lowercase_fail",
            "require_lowercase_pass",
            "require_number_fail",
            "require_number_pass",
            "require_special_fail",
            "require_special_pass",
            "combined_upper_lower_number_pass",
            "combined_upper_lower_special_pass",
            "combined_missing_uppercase_fail",
            "full_policy_pass",
            "full_policy_missing_special_fail",
        ],
    )
    def test_password_with_policy(self, password, policy_settings, expected_valid):
        is_valid, msg = validate_password(password, policy_settings=policy_settings)
        assert is_valid is expected_valid
        if not expected_valid:
            assert msg is not None

    @pytest.mark.parametrize(
        "min_length_value",
        ["oops", None, "", [], {"nested": 1}],
        ids=["non_numeric_string", "none", "empty_string", "list", "dict"],
    )
    def test_malformed_policy_min_length_falls_back_to_default(self, min_length_value):
        """A malformed password_min_length must not raise; it falls back to 8.

        Guards the try/except in validate_password so an admin misconfiguration
        (None / non-numeric / wrong type) surfaces a clean validation result
        instead of a 500.
        """
        policy = {"password_min_length": min_length_value}
        # 8 chars satisfies the fallback minimum of 8.
        is_valid, _ = validate_password("12345678", policy_settings=policy)
        assert is_valid is True
        # 7 chars fails the fallback minimum of 8.
        is_valid, msg = validate_password("1234567", policy_settings=policy)
        assert is_valid is False
        assert "8" in msg


# ==================== Date Range Validation Tests (Issue #2738) ====================


class TestValidateDateRange:
    """Test validate_date_range function."""

    def test_both_missing_returns_none(self):
        """Both dates missing should return (True, None, None, None)."""
        is_valid, error_code, start, end = validate_date_range(None, None)
        assert is_valid is True
        assert error_code is None
        assert start is None
        assert end is None

    def test_only_start_provided(self):
        """Only start_date provided should return incomplete_date_range error."""
        is_valid, error_code, start, end = validate_date_range("2026-01-01", None)
        assert is_valid is False
        assert error_code == "incomplete_date_range"
        assert start is None
        assert end is None

    def test_only_end_provided(self):
        """Only end_date provided should return incomplete_date_range error."""
        is_valid, error_code, start, end = validate_date_range(None, "2026-01-31")
        assert is_valid is False
        assert error_code == "incomplete_date_range"
        assert start is None
        assert end is None

    def test_invalid_format_slash(self):
        """Invalid date format with slash should return invalid_date_format error."""
        is_valid, error_code, start, end = validate_date_range("2026/01/01", "2026-01-31")
        assert is_valid is False
        assert error_code == "invalid_date_format"

    def test_invalid_format_invalid_calendar(self):
        """Invalid calendar date should return invalid_date_format error."""
        is_valid, error_code, start, end = validate_date_range("2026-02-30", "2026-03-01")
        assert is_valid is False
        assert error_code == "invalid_date_format"

    def test_start_equals_end(self):
        """Same start and end date should be valid (single day query)."""
        is_valid, error_code, start, end = validate_date_range("2026-01-15", "2026-01-15")
        assert is_valid is True
        assert error_code is None
        assert str(start) == "2026-01-15"
        assert str(end) == "2026-01-15"

    def test_start_after_end(self):
        """Start date after end date should return invalid_date_order error."""
        is_valid, error_code, start, end = validate_date_range("2026-01-31", "2026-01-01")
        assert is_valid is False
        assert error_code == "invalid_date_order"
        assert start is None
        assert end is None

    def test_exceeds_max_days(self):
        """Range exceeding max_days should return date_range_exceeded error."""
        # 366 days span to exceed 365 max
        is_valid, error_code, start, end = validate_date_range(
            "2025-01-01", "2026-01-02", max_days=365
        )
        assert is_valid is False
        assert error_code == "date_range_exceeded"

    def test_exactly_max_days(self):
        """Range exactly at max_days should be valid."""
        is_valid, error_code, start, end = validate_date_range(
            "2025-01-01", "2025-12-31", max_days=365  # 364 days difference
        )
        assert is_valid is True
        assert error_code is None

    def test_future_date_rejected(self):
        """Future dates should be rejected when allow_future=False."""
        is_valid, error_code, start, end = validate_date_range(
            "2027-01-01", "2027-01-31", allow_future=False
        )
        assert is_valid is False
        assert error_code == "future_date_not_allowed"

    def test_future_date_allowed(self):
        """Future dates should be allowed when allow_future=True."""
        is_valid, error_code, start, end = validate_date_range(
            "2027-01-01", "2027-01-31", allow_future=True
        )
        assert is_valid is True
        assert error_code is None

    def test_extreme_date_min(self):
        """Minimum valid date should work."""
        is_valid, error_code, start, end = validate_date_range("0001-01-01", "0001-01-02")
        assert is_valid is True
        assert error_code is None

    def test_extreme_date_max(self):
        """Maximum valid date should work."""
        # Extreme dates are in the future, so allow_future=True is needed
        is_valid, error_code, start, end = validate_date_range(
            "9999-12-30", "9999-12-31", allow_future=True
        )
        assert is_valid is True
        assert error_code is None

    def test_valid_range(self):
        """Valid date range should pass."""
        is_valid, error_code, start, end = validate_date_range("2026-01-01", "2026-01-31")
        assert is_valid is True
        assert error_code is None
        assert str(start) == "2026-01-01"
        assert str(end) == "2026-01-31"

    # Priority tests: format check before future check
    def test_format_priority_over_future(self):
        """Invalid date format should return invalid_date_format, not future_date_not_allowed."""
        is_valid, error_code, start, end = validate_date_range("2027-02-30", "2027-03-01")
        assert is_valid is False
        assert error_code == "invalid_date_format"

    def test_order_priority_over_future(self):
        """Invalid order should return invalid_date_order before checking future."""
        is_valid, error_code, start, end = validate_date_range("2027-01-31", "2027-01-01")
        assert is_valid is False
        assert error_code == "invalid_date_order"


class TestValidateTimeWindow:
    """Test validate_time_window function."""

    def test_months_zero(self):
        """months=0 should return invalid_time_window error."""
        is_valid, error_code, value = validate_time_window(0, "months")
        assert is_valid is False
        assert error_code == "invalid_time_window"
        assert value is None

    def test_months_negative(self):
        """Negative months should return invalid_time_window error."""
        is_valid, error_code, value = validate_time_window(-1, "months")
        assert is_valid is False
        assert error_code == "invalid_time_window"

    def test_months_exceeds_max(self):
        """months exceeding max should return invalid_time_window error."""
        is_valid, error_code, value = validate_time_window(25, "months", max_val=24)
        assert is_valid is False
        assert error_code == "invalid_time_window"

    def test_months_at_min(self):
        """months=1 should be valid."""
        is_valid, error_code, value = validate_time_window(1, "months")
        assert is_valid is True
        assert error_code is None
        assert value == 1

    def test_months_at_max(self):
        """months=24 should be valid."""
        is_valid, error_code, value = validate_time_window(24, "months", max_val=24)
        assert is_valid is True
        assert error_code is None
        assert value == 24

    def test_days_zero(self):
        """days=0 should return invalid_time_window error."""
        is_valid, error_code, value = validate_time_window(0, "days")
        assert is_valid is False
        assert error_code == "invalid_time_window"

    def test_days_exceeds_max(self):
        """days exceeding max should return invalid_time_window error."""
        is_valid, error_code, value = validate_time_window(366, "days", max_val=365)
        assert is_valid is False
        assert error_code == "invalid_time_window"

    def test_days_at_min(self):
        """days=1 should be valid."""
        is_valid, error_code, value = validate_time_window(1, "days")
        assert is_valid is True
        assert error_code is None
        assert value == 1

    def test_custom_bounds(self):
        """Custom min_val and max_val should work."""
        is_valid, error_code, value = validate_time_window(5, "custom", min_val=1, max_val=10)
        assert is_valid is True
        assert value == 5

        is_valid, error_code, value = validate_time_window(11, "custom", min_val=1, max_val=10)
        assert is_valid is False
        assert error_code == "invalid_time_window"


# ==================== Project Name Validation Tests (Issue #2897) ====================


class TestValidateProjectName:
    """Test validate_project_name function."""

    # Valid project names
    @pytest.mark.parametrize(
        "name",
        [
            "myproject",
            "my-project",
            "my_project",
            "MyProject123",
            "project 2024",
            "项目名称",
            "项目_测试",
            "项目-测试",
            "项目 2024",
            "My项目2024",
            "a",
            "a" * 255,
            "2024-08-20 Project",
            "Test_Project-2024",
        ],
        ids=[
            "simple",
            "with_hyphen",
            "with_underscore",
            "alphanumeric",
            "with_space",
            "chinese",
            "chinese_underscore",
            "chinese_hyphen",
            "chinese_space",
            "mixed_ascii_cjk",
            "single_char",
            "max_length_255",
            "date_and_text",
            "all_allowed_chars",
        ],
    )
    def test_valid_project_name(self, name):
        """Valid project names should pass validation."""
        is_valid, msg = validate_project_name(name)
        assert is_valid is True
        assert msg is None

    # Empty name is valid (name is optional)
    def test_empty_name_is_valid(self):
        """Empty name should be valid (name is optional)."""
        is_valid, msg = validate_project_name("")
        assert is_valid is True
        assert msg is None

    def test_none_name_is_valid(self):
        """None name should be valid (name is optional)."""
        is_valid, msg = validate_project_name(None)
        assert is_valid is True
        assert msg is None

    # Invalid project names - XSS and path injection
    @pytest.mark.parametrize(
        "name,expected_substring",
        [
            ("<script>alert('xss')</script>", "letters, numbers"),
            ("测试/路径", "letters, numbers"),
            ('"引号"测试', "letters, numbers"),
            ("项目\\路径", "letters, numbers"),
            ("project<path", "letters, numbers"),
            ("project>path", "letters, numbers"),
            ("project&path", "letters, numbers"),
            ("project'path", "letters, numbers"),
            ('project"path', "letters, numbers"),
            ("project(path)", "letters, numbers"),
            ("project[path]", "letters, numbers"),
            ("project{path}", "letters, numbers"),
            ("project;path", "letters, numbers"),
            ("project:path", "letters, numbers"),
            ("project,path", "letters, numbers"),
            ("project.path", "letters, numbers"),
            ("project?path", "letters, numbers"),
            ("project!path", "letters, numbers"),
            ("project@path", "letters, numbers"),
            ("project#path", "letters, numbers"),
            ("project$path", "letters, numbers"),
            ("project%path", "letters, numbers"),
            ("project^path", "letters, numbers"),
            ("project*path", "letters, numbers"),
            ("project~path", "letters, numbers"),
            ("project`path", "letters, numbers"),
            ("project|path", "letters, numbers"),
            ("a" * 256, "255"),
        ],
        ids=[
            "xss_script_tag",
            "path_separator_slash_chinese",
            "double_quotes_chinese",
            "backslash_chinese",
            "angle_bracket_open",
            "angle_bracket_close",
            "ampersand",
            "single_quote",
            "double_quote",
            "parentheses",
            "square_brackets",
            "curly_braces",
            "semicolon",
            "colon",
            "comma",
            "dot",
            "question_mark",
            "exclamation_mark",
            "at_sign",
            "hash",
            "dollar",
            "percent",
            "caret",
            "asterisk",
            "tilde",
            "backtick",
            "pipe",
            "too_long_256_chars",
        ],
    )
    def test_invalid_project_name(self, name, expected_substring):
        """Invalid project names should fail validation."""
        is_valid, msg = validate_project_name(name)
        assert is_valid is False
        assert msg is not None
        assert expected_substring in msg

    # Edge cases
    def test_whitespace_only(self):
        """Whitespace-only name should be invalid."""
        is_valid, msg = validate_project_name("   ")
        assert is_valid is False
        assert msg is not None
        assert "empty or whitespace" in msg

    def test_leading_trailing_spaces(self):
        """Leading/trailing spaces should be stripped; stripped name should pass."""
        is_valid, msg = validate_project_name("  valid project  ")
        assert is_valid is True
        assert msg is None

    def test_multiple_consecutive_spaces(self):
        """Multiple consecutive spaces should be valid."""
        is_valid, msg = validate_project_name("project  with   spaces")
        assert is_valid is True
        assert msg is None

    def test_newline_in_name(self):
        """Newline in name should be invalid."""
        is_valid, msg = validate_project_name("project\nname")
        assert is_valid is False
        assert msg is not None

    def test_tab_in_name(self):
        """Tab in name should be invalid."""
        is_valid, msg = validate_project_name("project\tname")
        assert is_valid is False
        assert msg is not None

    def test_unicode_null_byte(self):
        """Null byte in name should be invalid."""
        is_valid, msg = validate_project_name("project\x00name")
        assert is_valid is False
        assert msg is not None

    def test_unicode_nbsp_rejected(self):
        """Unicode non-breaking space (\\xa0) should be invalid."""
        is_valid, msg = validate_project_name("project\xa0name")
        assert is_valid is False
        assert msg is not None

    def test_unicode_width_spaces_rejected(self):
        """Unicode various width spaces should be invalid."""
        for ws_char in ["\u2000", "\u2001", "\u200a", "\u3000"]:
            is_valid, msg = validate_project_name(f"project{ws_char}name")
            assert is_valid is False, f"Expected {repr(ws_char)} to be rejected"
            assert msg is not None

    # Security test cases
    @pytest.mark.parametrize(
        "name",
        [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert('xss')>",
            "<svg onload=alert('xss')>",
            "javascript:alert('xss')",
            "data:text/html,<script>alert('xss')</script>",
            "&#x3C;script&#x3E;alert('xss')&#x3C;/script&#x3E;",
            "<SCRIPT>alert('xss')</SCRIPT>",
            "<ScRiPt>alert('xss')</ScRiPt>",
        ],
        ids=[
            "script_tag",
            "img_onerror",
            "svg_onload",
            "javascript_protocol",
            "data_url",
            "html_entities",
            "uppercase_script",
            "mixed_case_script",
        ],
    )
    def test_xss_attacks_blocked(self, name):
        """XSS attack vectors should be blocked."""
        is_valid, msg = validate_project_name(name)
        assert is_valid is False
        assert msg is not None

    @pytest.mark.parametrize(
        "name",
        [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "/etc/passwd",
            "C:\\Windows\\System32",
            "project/../../../etc/passwd",
            "project\\..\\..\\..\\etc\\passwd",
        ],
        ids=[
            "path_traversal_unix",
            "path_traversal_windows",
            "absolute_path_unix",
            "absolute_path_windows",
            "mixed_path_traversal_unix",
            "mixed_path_traversal_windows",
        ],
    )
    def test_path_injection_blocked(self, name):
        """Path injection attack vectors should be blocked."""
        is_valid, msg = validate_project_name(name)
        assert is_valid is False
        assert msg is not None
