"""
Test for Issue #1898: Sensitive keyword false positives blocking normal messages.

This test verifies that sensitive_keyword is downgraded to warn-only,
not block, to prevent false positives from blocking legitimate messages
containing words like "password" or "secret" in non-sensitive contexts.
"""

import pytest

from app.modules.governance.content_filter import ContentFilter


class TestSensitiveKeywordFix:
    """Test suite for sensitive_keyword downgrade fix."""

    def test_sensitive_keyword_does_not_block(self):
        """Verify that sensitive_keyword matches do NOT block requests."""
        content_filter = ContentFilter()

        # Test various sensitive keywords
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

            # Should NOT block
            assert result.passed is True, f"Should pass: {content}"
            assert result.action != "block", f"Action should not be 'block': {content}"

            # Should warn or none (not block)
            assert result.action in [
                "warn",
                "none",
            ], f"Action should be warn or none: {content}"

    def test_sensitive_keyword_risk_is_medium(self):
        """Verify that sensitive_keyword is now classified as medium risk."""
        content_filter = ContentFilter()

        result = content_filter.check_content("my password is xxx")

        # Find the sensitive_keyword rule
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0

        # Verify risk is medium
        sensitive_rule = sensitive_matches[0]
        assert sensitive_rule.get("risk") == "medium"

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
        """Verify that critical PII + sensitive_keyword still blocks.

        Regression test for PR #1903 review feedback:
        - SSN (critical) + password should still block
        - Credit card (critical) + password should still block
        """
        content_filter = ContentFilter()

        # SSN + password should still block (critical takes precedence)
        result = content_filter.check_content("SSN: 123-45-6789 and my password is secret")
        assert result.passed is False, "Critical PII + keyword should still block"
        assert result.action == "block", "Critical PII should keep action='block'"
        assert result.risk_level == "critical"

        # Credit card + password should still block
        result = content_filter.check_content(
            "Credit card: 4111-1111-1111-1111, password is secret123"
        )
        assert result.passed is False, "Critical PII + keyword should still block"
        assert result.action == "block"

    def test_pii_medium_only_redacts(self):
        """Verify that medium-risk PII only triggers redact, not block."""
        content_filter = ContentFilter()

        # PII that should only redact
        result = content_filter.check_content("Port: 1763, Email: test@example.com")

        # Should pass (redact only)
        assert result.passed is True
        # Should have PII matches
        assert len(result.matched_rules) > 0

    def test_sensitive_keyword_plus_pii_does_not_block(self):
        """Verify that sensitive_keyword + PII together don't block."""
        content_filter = ContentFilter()

        # Both sensitive_keyword and PII
        result = content_filter.check_content("my password is xxx, Port: 1763")

        # Should pass because sensitive_keyword is now warn-only
        assert result.passed is True
        assert result.action != "block"

    def test_only_hi_passes(self):
        """Verify that simple 'hi' message passes without any matches."""
        content_filter = ContentFilter()

        result = content_filter.check_content("hi")
        assert result.passed is True
        assert result.action == "none"
        assert len(result.matched_rules) == 0

    def test_audit_log_still_records_sensitive_keyword(self):
        """Verify that sensitive_keyword matches are still logged for audit."""
        # Use config to enable log_matches
        config = {"enabled": True, "log_matches": True}
        content_filter = ContentFilter(config=config)

        result = content_filter.check_content("my password is xxx")

        # Should have matched rules (for audit)
        assert len(result.matched_rules) > 0
        sensitive_matches = [
            r for r in result.matched_rules if r.get("type") == "sensitive_keyword"
        ]
        assert len(sensitive_matches) > 0

        # Should have recorded the keywords
        assert "keywords" in sensitive_matches[0]
        assert "password" in sensitive_matches[0]["keywords"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
