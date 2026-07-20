"""
Test for Issue #1898: Content filter false positives blocking normal messages.

This test verifies that the content filter configuration has been adjusted
to only log matches without blocking, preventing false positives from
blocking legitimate messages containing port numbers, timestamps, etc.
"""
import pytest
from app.modules.governance.content_filter import ContentFilter
from app.modules.workspace.llm_proxy_handler import _get_content_filter


class TestContentFilterFix:
    """Test suite for content filter fix."""

    def test_content_filter_config_no_block(self):
        """Verify that content filter is configured to not block by default."""
        content_filter = _get_content_filter()

        # Check that the filter is enabled
        assert content_filter.enabled is True

        # Check that block_high_risk is False (key fix)
        assert content_filter.block_high_risk is False

        # Check that redact_pii is False
        assert content_filter.redact_pii is False

        # Check that log_matches is True (audit still works)
        assert content_filter.log_matches is True

    def test_port_number_not_blocked(self):
        """Verify that port numbers are no longer blocked."""
        content_filter = _get_content_filter()

        # Test port numbers that were previously false positives
        test_cases = [
            "端口: 1763",
            "Port: 1888",
            "Using port 3100",
            "Server running on 5000",
        ]

        for content in test_cases:
            result = content_filter.check_content(content)
            # Should match rules but not block
            assert result.passed is True, f"Content should pass: {content}"
            assert result.action != "block", f"Action should not be 'block': {content}"

    def test_timestamp_not_blocked(self):
        """Verify that timestamps are no longer blocked."""
        content_filter = _get_content_filter()

        test_cases = [
            "时间: 2024-01-01",
            "Created at: 2026-07-20",
            "Timestamp: 1784529847",
        ]

        for content in test_cases:
            result = content_filter.check_content(content)
            assert result.passed is True, f"Timestamp should pass: {content}"
            assert result.action != "block", f"Action should not be 'block': {content}"

    def test_simple_hi_not_blocked(self):
        """Verify that simple 'hi' message is not blocked."""
        content_filter = _get_content_filter()

        result = content_filter.check_content("hi")
        assert result.passed is True
        assert result.action == "none"
        assert len(result.matched_rules) == 0

    def test_matches_still_logged(self):
        """Verify that matches are still logged for audit purposes."""
        content_filter = _get_content_filter()

        # Content that would previously trigger a block
        content = "端口: 1763, SECRET_KEY=xxx"

        result = content_filter.check_content(content)

        # Should pass (not block)
        assert result.passed is True
        assert result.action != "block"

        # But should still have matched rules (for audit)
        assert len(result.matched_rules) > 0, "Matches should still be detected for audit"

    def test_sensitive_keywords_not_blocked(self):
        """Verify that sensitive keywords don't cause blocking."""
        content_filter = _get_content_filter()

        test_cases = [
            "SECRET_KEY=xxx",
            "API_KEY=yyy",
            "password=123",
            "credential data",
        ]

        for content in test_cases:
            result = content_filter.check_content(content)
            # Should match but not block
            assert result.passed is True, f"Should pass: {content}"
            # Might have matches, but action should not be 'block'
            if result.matched_rules:
                assert result.action != "block", f"Action should not be 'block': {content}"

    def test_real_pii_still_detected(self):
        """Verify that real PII is still detected (just not blocked)."""
        content_filter = _get_content_filter()

        # Real email address
        result = content_filter.check_content("Contact: user@example.com")
        assert result.passed is True  # Should pass (not blocked)
        # But should have detected it
        assert len(result.matched_rules) > 0, "PII should still be detected"

        # Real US phone number
        result = content_filter.check_content("Phone: +1-202-555-0123")
        assert result.passed is True
        # Should have detected it
        assert len(result.matched_rules) > 0, "Phone should still be detected"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])