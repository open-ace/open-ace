"""
Test for Issue #1904: Sensitive keyword filtering improvements.

This test verifies:
1. Word boundary matching for sensitive keywords
2. Configurable block/warn behavior via tenant_config
3. Backward compatibility (no arguments = instance defaults)
"""

import pytest

from app.modules.governance.content_filter import ContentFilter


class TestWordBoundaryMatching:
    """Test suite for word boundary matching."""

    def test_pure_letter_keyword_matches_word_boundary(self):
        """Verify pure letter keywords match with word boundary."""
        content_filter = ContentFilter()

        # Should match: password is a standalone word
        result = content_filter.check_content("my password is xxx")
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0
        assert "password" in sensitive_matches[0]["keywords"]

    def test_pure_letter_keyword_not_match_in_word(self):
        """Verify pure letter keywords don't match inside other words."""
        content_filter = ContentFilter()

        # Should NOT match: secret is part of SECRET_KEY
        result = content_filter.check_content("SECRET_KEY=value")
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        # 'secret' should not match because it's part of SECRET_KEY
        # Note: This test verifies the word_boundary mode is working
        if sensitive_matches:
            # If matched, verify secret is not in the matches
            assert "secret" not in sensitive_matches[0]["keywords"]

    def test_underscore_keyword_matches_word_boundary(self):
        """Verify underscore keywords match with non-alphanumeric boundaries."""
        content_filter = ContentFilter()

        # Should match: api_key is surrounded by spaces
        result = content_filter.check_content("my api_key is xxx")
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0
        assert "api_key" in sensitive_matches[0]["keywords"]

    def test_underscore_keyword_in_identifier_matches(self):
        """Verify underscore keywords match inside identifiers (by design).

        Note: Non-alphanumeric boundary mode allows matching keywords in
        compound identifiers like "my_api_key_value". This is intentional
        to catch sensitive keywords even when embedded in code.
        """
        content_filter = ContentFilter()

        # Will match: api_key appears in my_api_key_value
        # This is by design to catch potential sensitive data exposure
        result = content_filter.check_content("my_api_key_value")
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        # The keyword is found, which is the expected behavior
        assert len(sensitive_matches) > 0
        assert "api_key" in sensitive_matches[0]["keywords"]

    def test_chinese_context_keyword_matching(self):
        """Verify keywords are matched in Chinese context.

        Note: Chinese characters are non-alphanumeric, so word boundary
        regex should match keywords surrounded by Chinese characters.
        """
        content_filter = ContentFilter()

        # Explicit test with spaces to ensure matching
        result = content_filter.check_content("我的 password 是密码")
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0
        assert "password" in sensitive_matches[0]["keywords"]


class TestConfigurableBlockBehavior:
    """Test suite for configurable block/warn behavior."""

    def test_block_mode_blocks_request(self):
        """Verify block_sensitive_keyword=True blocks the request."""
        content_filter = ContentFilter()

        result = content_filter.check_content(
            "my password is xxx",
            tenant_config={
                "block_sensitive_keyword": True,
                "sensitive_keyword_match_mode": "word_boundary",
            },
        )

        assert result.passed is False
        assert result.action == "block"

    def test_warn_mode_passes_request(self):
        """Verify block_sensitive_keyword=False passes the request."""
        content_filter = ContentFilter()

        result = content_filter.check_content(
            "my password is xxx",
            tenant_config={
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "word_boundary",
            },
        )

        assert result.passed is True
        assert result.action in ["warn", "none"]

    def test_default_mode_warns_not_blocks(self):
        """Verify default behavior is warn, not block."""
        content_filter = ContentFilter()

        result = content_filter.check_content("my password is xxx")

        # Default should be warn (passed=True)
        assert result.passed is True


class TestMatchModeSwitching:
    """Test suite for match mode switching."""

    def test_word_boundary_mode(self):
        """Verify word_boundary mode uses word boundaries."""
        content_filter = ContentFilter()

        result = content_filter.check_content(
            "SECRET_KEY=value",
            tenant_config={
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "word_boundary",
            },
        )

        # In word_boundary mode, 'secret' should not match SECRET_KEY
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        if sensitive_matches:
            assert "secret" not in sensitive_matches[0]["keywords"]

    def test_substring_mode_matches_inside_words(self):
        """Verify substring mode matches inside other words."""
        content_filter = ContentFilter()

        result = content_filter.check_content(
            "SECRET_KEY=value",
            tenant_config={
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "substring",
            },
        )

        # In substring mode, 'secret' should match SECRET_KEY
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0
        assert "secret" in sensitive_matches[0]["keywords"]


class TestConfigValidation:
    """Test suite for configuration validation."""

    def test_invalid_block_value_uses_default(self):
        """Verify invalid block_sensitive_keyword uses default."""
        content_filter = ContentFilter()

        # Invalid value should use default (False)
        result = content_filter.check_content(
            "my password is xxx",
            tenant_config={
                "block_sensitive_keyword": "invalid",
                "sensitive_keyword_match_mode": "word_boundary",
            },
        )

        # Should not block (default is False)
        assert result.passed is True

    def test_invalid_match_mode_uses_default(self):
        """Verify invalid match_mode uses default."""
        content_filter = ContentFilter()

        # Invalid mode should use default (word_boundary)
        result = content_filter.check_content(
            "SECRET_KEY=value",
            tenant_config={
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "invalid",
            },
        )

        # Should use word_boundary, so 'secret' should not match
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        if sensitive_matches:
            assert "secret" not in sensitive_matches[0]["keywords"]

    def test_none_config_uses_instance_defaults(self):
        """Verify None tenant_config uses instance defaults."""
        content_filter = ContentFilter()

        result = content_filter.check_content("my password is xxx", tenant_config=None)

        # Should use instance defaults (block=False)
        assert result.passed is True


class TestBackwardCompatibility:
    """Test suite for backward compatibility."""

    def test_no_arguments_still_works(self):
        """Verify check_content() without tenant_config still works."""
        content_filter = ContentFilter()

        # Should not raise any errors
        result = content_filter.check_content("my password is xxx")

        assert result.passed is True
        assert len(result.matched_rules) > 0

    def test_context_argument_still_works(self):
        """Verify check_content() with only context still works."""
        content_filter = ContentFilter()

        # Should not raise any errors
        result = content_filter.check_content(
            "my password is xxx", context={"user_id": 1}
        )

        assert result.passed is True


class TestRegression:
    """Regression tests for Issue #1898."""

    def test_sensitive_keyword_does_not_block_by_default(self):
        """Verify sensitive_keyword does NOT block requests by default."""
        content_filter = ContentFilter()

        test_cases = [
            "my password is xxx",
            "the secret is yyy",
            "API_KEY=zzz",
            "credential data",
        ]

        for content in test_cases:
            result = content_filter.check_content(content)

            # Should match sensitive_keyword
            assert len(result.matched_rules) > 0, f"Should match rules: {content}"
            sensitive_matches = [
                r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
            ]
            assert len(sensitive_matches) > 0, f"Should match sensitive_keyword: {content}"

            # Should NOT block (default behavior)
            assert result.passed is True, f"Should pass: {content}"
            assert result.action != "block", f"Action should not be 'block': {content}"

    def test_real_high_risk_pii_still_blocks(self):
        """Verify that real high-risk PII (SSN, credit card) still blocks."""
        content_filter = ContentFilter()

        # Test SSN (should block)
        result = content_filter.check_content("SSN: 123-45-6789")
        assert result.passed is False
        assert result.action == "block"

        # Test credit card (should block)
        result = content_filter.check_content("Credit card: 1234-5678-9012-3456")
        assert result.passed is False
        assert result.action == "block"

    def test_critical_pii_plus_keyword_still_blocks(self):
        """Verify that critical PII + sensitive_keyword still blocks."""
        content_filter = ContentFilter()

        # SSN + password should still block (critical takes precedence)
        result = content_filter.check_content("SSN: 123-45-6789 and my password is secret")
        assert result.passed is False, "Critical PII + keyword should still block"
        assert result.action == "block"
        assert result.risk_level == "critical"

    def test_simple_hi_passes(self):
        """Verify that simple 'hi' message passes without any matches."""
        content_filter = ContentFilter()

        result = content_filter.check_content("hi")
        assert result.passed is True
        assert result.action == "none"
        assert len(result.matched_rules) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
