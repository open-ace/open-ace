"""Unit tests for ContentFilter module."""

from unittest.mock import MagicMock

import pytest

from app.modules.governance.content_filter import (
    ContentFilter,
    ContentType,
    FilterResult,
    RiskLevel,
)


class TestContentFilter:
    """Test ContentFilter."""

    def test_check_content_clean(self):
        cf = ContentFilter()
        result = cf.check_content("Hello, this is a normal message.")
        assert result.passed is True
        assert result.risk_level == "low"

    def test_check_content_disabled(self):
        cf = ContentFilter(config={"enabled": False})
        result = cf.check_content("SSN: 123-45-6789")
        assert result.passed is True

    def test_check_content_empty(self):
        cf = ContentFilter()
        result = cf.check_content("")
        assert result.passed is True

    def test_check_content_none(self):
        cf = ContentFilter()
        result = cf.check_content(None)
        assert result.passed is True

    def test_detect_email(self):
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Contact me at test@example.com")
        # Email is medium risk, not blocked by default (only high/critical blocked)
        assert result.passed is True
        assert any(r["type"] == "pii_email" for r in result.matched_rules)

    def test_detect_ssn(self):
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("My SSN is 123-45-6789")
        # SSN is critical risk, blocked when block_high_risk=True
        assert result.passed is False
        assert any(r["type"] == "pii_ssn" for r in result.matched_rules)

    def test_detect_credit_card(self):
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("Card: 4111-1111-1111-1111")
        # Credit card is critical risk, blocked when block_high_risk=True
        assert result.passed is False
        assert any(r["type"] == "pii_credit_card" for r in result.matched_rules)

    def test_detect_phone(self):
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Call me at 555-123-4567")
        # Phone is medium risk, not blocked by default
        assert result.passed is True
        assert any(r["type"] in ("pii_phone_us", "pii_phone_intl") for r in result.matched_rules)

    def test_detect_sensitive_keyword(self):
        cf = ContentFilter()
        result = cf.check_content("The password is secret123")
        # sensitive_keyword is now warn-only (Issue #1898), does not block
        assert result.passed is True
        assert result.risk_level in ("medium", "high")
        assert result.action == "none"  # warn-only, no blocking
        assert any(r["type"] == "sensitive_keyword" for r in result.matched_rules)

    def test_redaction_enabled(self):
        cf = ContentFilter(config={"redact_pii": True, "block_high_risk": False})
        result = cf.check_content("Email: test@example.com and SSN: 123-45-6789")
        assert result.redacted_content is not None
        assert "test@example.com" not in result.redacted_content

    def test_add_custom_pattern(self):
        cf = ContentFilter()
        cf.add_custom_pattern("employee_id", r"EMP-\d{6}", risk="medium")
        result = cf.check_content("My ID is EMP-123456")
        # Custom pattern is medium risk, not blocked by default
        assert result.passed is True
        assert any(r["type"] == "employee_id" for r in result.matched_rules)

    def test_add_custom_keyword(self):
        cf = ContentFilter()
        cf.add_custom_keyword("confidential")
        result = cf.check_content("This is confidential information")
        # sensitive_keyword is now warn-only (Issue #1898), does not block
        assert result.passed is True
        assert result.action == "none"  # warn-only, no blocking
        assert any(r["type"] == "sensitive_keyword" for r in result.matched_rules)

    def test_get_stats(self):
        cf = ContentFilter(custom_patterns={"test": r"\d+"}, custom_keywords=["secret"])
        stats = cf.get_stats()
        assert stats["enabled"] is True
        assert stats["keyword_count"] >= 1
        assert stats["pattern_count"] >= 1

    def test_filter_result_to_dict(self):
        fr = FilterResult(
            passed=True, risk_level="low", matched_rules=[], redacted_content=None, suggestion=None
        )
        d = fr.to_dict()
        assert d["passed"] is True
        assert "timestamp" in d

    def test_risk_level_enum(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.CRITICAL.value == "critical"

    def test_content_type_enum(self):
        assert ContentType.PII_EMAIL.value == "pii_email"
        assert ContentType.PII_SSN.value == "pii_ssn"
