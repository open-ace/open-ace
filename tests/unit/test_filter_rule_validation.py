"""Tests for filter rule input validation.

Tests for validating filter rule input including:
- Enum value validation (type, severity, action)
- Regex syntax validation
- ReDoS protection
"""

import pytest

from app.routes.governance import (
    VALID_ACTIONS,
    VALID_RULE_TYPES,
    VALID_SEVERITIES,
    _validate_filter_rule_input,
)


class TestFilterRuleValidation:
    """Tests for filter rule input validation."""

    # -------------------------------------------------------------------------
    # Valid enum values
    # -------------------------------------------------------------------------

    def test_valid_rule_types(self):
        """Verify valid rule types."""
        assert {"keyword", "regex", "pii"} == VALID_RULE_TYPES

    def test_valid_severities(self):
        """Verify valid severities."""
        assert {"low", "medium", "high"} == VALID_SEVERITIES

    def test_valid_actions(self):
        """Verify valid actions."""
        assert {"warn", "block", "redact"} == VALID_ACTIONS

    # -------------------------------------------------------------------------
    # Valid input
    # -------------------------------------------------------------------------

    def test_valid_keyword_pattern(self):
        """Keyword pattern should be valid."""
        is_valid, error = _validate_filter_rule_input(
            pattern="secret",
            rule_type="keyword",
            severity="high",
            action="block",
        )
        assert is_valid is True
        assert error == ""

    def test_valid_regex_pattern(self):
        """Valid regex pattern should pass."""
        is_valid, error = _validate_filter_rule_input(
            pattern=r"api[_-]?key\w*[a-zA-Z0-9]+",
            rule_type="regex",
            severity="high",
            action="block",
        )
        assert is_valid is True
        assert error == ""

    def test_valid_pii_pattern(self):
        """PII pattern should be valid."""
        is_valid, error = _validate_filter_rule_input(
            pattern="email",
            rule_type="pii",
            severity="medium",
            action="redact",
        )
        assert is_valid is True
        assert error == ""

    # -------------------------------------------------------------------------
    # Invalid enum values
    # -------------------------------------------------------------------------

    def test_invalid_type(self):
        """Invalid type should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="test",
            rule_type="invalid",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Invalid type" in error
        assert "invalid" in error

    def test_invalid_severity(self):
        """Invalid severity should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="test",
            rule_type="keyword",
            severity="critical",
            action="warn",
        )
        assert is_valid is False
        assert "Invalid severity" in error
        assert "critical" in error

    def test_invalid_action(self):
        """Invalid action should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="test",
            rule_type="keyword",
            severity="medium",
            action="delete",
        )
        assert is_valid is False
        assert "Invalid action" in error
        assert "delete" in error

    # -------------------------------------------------------------------------
    # Pattern length validation
    # -------------------------------------------------------------------------

    def test_pattern_too_long(self):
        """Pattern exceeding 1000 chars should fail."""
        long_pattern = "a" * 1001
        is_valid, error = _validate_filter_rule_input(
            pattern=long_pattern,
            rule_type="keyword",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "too long" in error

    def test_pattern_exactly_1000_chars(self):
        """Pattern of exactly 1000 chars should pass."""
        pattern_1000 = "a" * 1000
        is_valid, error = _validate_filter_rule_input(
            pattern=pattern_1000,
            rule_type="keyword",
            severity="medium",
            action="warn",
        )
        assert is_valid is True

    # -------------------------------------------------------------------------
    # Regex syntax validation
    # -------------------------------------------------------------------------

    def test_invalid_regex_unmatched_bracket(self):
        """Invalid regex with unmatched bracket should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="[a-z",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Invalid regex" in error

    def test_invalid_regex_unmatched_paren(self):
        """Invalid regex with unmatched parenthesis should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="(a|b",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Invalid regex" in error

    def test_invalid_regex_bad_escape(self):
        """Invalid regex with bad escape should fail."""
        is_valid, error = _validate_filter_rule_input(
            pattern="\\x",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Invalid regex" in error

    # -------------------------------------------------------------------------
    # ReDoS protection
    # -------------------------------------------------------------------------

    def test_redos_nested_plus_quantifiers(self):
        """Nested + quantifiers should be rejected."""
        is_valid, error = _validate_filter_rule_input(
            pattern="a++",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Nested quantifiers" in error
        assert "ReDoS" in error

    def test_redos_nested_star_quantifiers(self):
        """Nested * quantifiers should be rejected."""
        is_valid, error = _validate_filter_rule_input(
            pattern="a**",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Nested quantifiers" in error
        assert "ReDoS" in error

    def test_redos_alternation_with_quantifier(self):
        """Alternation with quantifier should be rejected."""
        is_valid, error = _validate_filter_rule_input(
            pattern="(a|b)+",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Alternation with quantifiers" in error
        assert "ReDoS" in error

    def test_redos_alternation_with_star(self):
        """Alternation with star quantifier should be rejected."""
        is_valid, error = _validate_filter_rule_input(
            pattern="(a|b)*",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is False
        assert "Alternation with quantifiers" in error

    def test_valid_alternation_no_quantifier(self):
        """Alternation without quantifier should pass."""
        is_valid, error = _validate_filter_rule_input(
            pattern="(a|b)",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is True

    def test_valid_quantifier_no_alternation(self):
        """Quantifier without alternation should pass."""
        is_valid, error = _validate_filter_rule_input(
            pattern="a+",
            rule_type="regex",
            severity="medium",
            action="warn",
        )
        assert is_valid is True

    # -------------------------------------------------------------------------
    # Keyword type doesn't validate regex
    # -------------------------------------------------------------------------

    def test_keyword_type_skips_regex_validation(self):
        """Keyword type should not validate regex syntax."""
        # This would fail as regex but passes as keyword
        is_valid, error = _validate_filter_rule_input(
            pattern="[a-z",
            rule_type="keyword",
            severity="medium",
            action="warn",
        )
        assert is_valid is True

    def test_keyword_type_skips_redos_check(self):
        """Keyword type should not check for ReDoS."""
        is_valid, error = _validate_filter_rule_input(
            pattern="a++",
            rule_type="keyword",
            severity="medium",
            action="warn",
        )
        assert is_valid is True
