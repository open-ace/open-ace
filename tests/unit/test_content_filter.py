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
        assert result.action == "warn"  # warn-only, generates audit log but no blocking
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
        assert result.action == "warn"  # warn-only, generates audit log but no blocking
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


class TestRiskLevelUpdate:
    """Test _update_risk_level static method."""

    def test_risk_upgrade_low_to_critical(self):
        """测试风险级别正常升级路径"""
        # low → medium → high → critical
        result = ContentFilter._update_risk_level("low", "medium")
        assert result == "medium"
        result = ContentFilter._update_risk_level("medium", "high")
        assert result == "high"
        result = ContentFilter._update_risk_level("high", "critical")
        assert result == "critical"

    def test_risk_no_downgrade(self):
        """测试风险级别不会降级"""
        # critical should not be downgraded by high or medium
        assert ContentFilter._update_risk_level("critical", "high") == "critical"
        assert ContentFilter._update_risk_level("critical", "medium") == "critical"
        assert ContentFilter._update_risk_level("critical", "low") == "critical"
        # high should not be downgraded by medium or low
        assert ContentFilter._update_risk_level("high", "medium") == "high"
        assert ContentFilter._update_risk_level("high", "low") == "high"

    def test_risk_same_level(self):
        """测试相同级别的处理"""
        assert ContentFilter._update_risk_level("high", "high") == "high"
        assert ContentFilter._update_risk_level("critical", "critical") == "critical"

    def test_risk_invalid_level_ignored(self):
        """测试无效风险级别被忽略（含空字符串、None、未知级别）"""
        # Invalid new_risk should not override valid current
        assert ContentFilter._update_risk_level("high", "unknown") == "high"
        assert ContentFilter._update_risk_level("medium", "") == "medium"
        assert ContentFilter._update_risk_level("high", None) == "high"
        assert ContentFilter._update_risk_level("critical", "invalid") == "critical"
        # Invalid current with valid new_risk should return new_risk
        assert ContentFilter._update_risk_level("unknown", "high") == "high"
        assert ContentFilter._update_risk_level("", "medium") == "medium"

    def test_risk_out_of_order_upgrade(self):
        """测试乱序升级（直接跳级）"""
        # low → critical directly
        assert ContentFilter._update_risk_level("low", "critical") == "critical"
        # medium → critical (skipping high)
        assert ContentFilter._update_risk_level("medium", "critical") == "critical"


class TestActionUpdate:
    """Test _update_action static method."""

    def test_action_upgrade_order(self):
        """测试 action 升级顺序：none → redact → warn → block"""
        assert ContentFilter._update_action("none", "redact") == "redact"
        assert ContentFilter._update_action("redact", "warn") == "warn"
        assert ContentFilter._update_action("warn", "block") == "block"

    def test_action_no_downgrade(self):
        """测试 action 不会降级"""
        assert ContentFilter._update_action("block", "warn") == "block"
        assert ContentFilter._update_action("block", "redact") == "block"
        assert ContentFilter._update_action("block", "none") == "block"
        assert ContentFilter._update_action("warn", "redact") == "warn"
        assert ContentFilter._update_action("warn", "none") == "warn"
        assert ContentFilter._update_action("redact", "none") == "redact"

    def test_action_same_level(self):
        """测试相同 action 的处理"""
        assert ContentFilter._update_action("block", "block") == "block"
        assert ContentFilter._update_action("warn", "warn") == "warn"

    def test_action_invalid_level_ignored(self):
        """测试无效 action 级别被忽略"""
        assert ContentFilter._update_action("warn", "unknown") == "warn"
        assert ContentFilter._update_action("redact", "") == "redact"
        assert ContentFilter._update_action("block", None) == "block"
        # Invalid current with valid new_action should return new_action
        assert ContentFilter._update_action("unknown", "block") == "block"
        assert ContentFilter._update_action("", "warn") == "warn"

    def test_action_out_of_order_upgrade(self):
        """测试 action 直接跳级升级"""
        assert ContentFilter._update_action("none", "block") == "block"
        assert ContentFilter._update_action("redact", "block") == "block"


class TestConfigBoundary:
    """Test configuration-related boundary conditions."""

    def test_block_high_risk_false(self):
        """测试 block_high_risk=False 时高风险内容不自动 block"""
        cf = ContentFilter(config={"block_high_risk": False, "redact_pii": True})
        # SSN is critical risk, but should not block when block_high_risk=False
        result = cf.check_content("My SSN is 123-45-6789")
        assert result.passed is True
        # Should still redact when redact_pii=True
        assert result.action == "redact"

    def test_redact_pii_false(self):
        """测试 redact_pii=False 时PII内容不自动 redact"""
        cf = ContentFilter(config={"block_high_risk": False, "redact_pii": False})
        # Email is medium risk
        result = cf.check_content("Contact me at test@example.com")
        assert result.passed is True
        assert result.action == "none"

    def test_both_config_false(self):
        """测试两者均为False时的行为"""
        cf = ContentFilter(config={"block_high_risk": False, "redact_pii": False})
        # SSN is critical risk
        result = cf.check_content("My SSN is 123-45-6789")
        # Should not block or redact, but risk level is still critical
        assert result.passed is True
        assert result.action == "none"
        assert result.risk_level == "critical"

    def test_block_high_risk_true_with_medium_risk(self):
        """测试 block_high_risk=True 时中等风险内容不 block"""
        cf = ContentFilter(config={"block_high_risk": True, "redact_pii": True})
        # Email is medium risk, should not block
        result = cf.check_content("Contact me at test@example.com")
        assert result.passed is True
        assert result.action == "redact"


class TestCombinedScenarios:
    """Test combined scenarios with multiple risk/action sources."""

    def test_combined_risk_sources(self):
        """测试数据库规则 + 内置PII + 关键词组合场景"""
        cf = ContentFilter(config={"block_high_risk": True})
        # SSN (critical) + password keyword (medium)
        result = cf.check_content("My SSN is 123-45-6789 and my password is secret")
        assert result.passed is False  # Blocked by SSN (critical)
        assert result.risk_level == "critical"
        assert result.action == "block"
        # Should have both SSN and keyword matches
        assert any(r["type"] == "pii_ssn" for r in result.matched_rules)
        assert any(r["type"] == "sensitive_keyword" for r in result.matched_rules)

    def test_multiple_pii_types(self):
        """测试多种PII类型同时检测"""
        cf = ContentFilter(config={"block_high_risk": True, "redact_pii": True})
        result = cf.check_content("Email: test@example.com, Phone: 555-123-4567, SSN: 123-45-6789")
        assert result.passed is False  # Blocked by SSN
        assert result.risk_level == "critical"
        assert result.action == "block"
        # Should detect all three
        assert any(r["type"] == "pii_email" for r in result.matched_rules)
        assert any(r["type"] in ("pii_phone_us", "pii_phone_intl") for r in result.matched_rules)
        assert any(r["type"] == "pii_ssn" for r in result.matched_rules)

    def test_risk_upgrade_with_multiple_matches(self):
        """测试多个匹配项时风险级别正确升级"""
        cf = ContentFilter(config={"block_high_risk": False, "redact_pii": True})
        # Email (medium) first, then SSN (critical) should upgrade to critical
        result = cf.check_content("Email: test@example.com and SSN: 123-45-6789")
        assert result.risk_level == "critical"
        # But action should be redact (since block_high_risk=False)
        assert result.action == "redact"

    def test_action_upgrade_precedence(self):
        """测试 action 升级的优先级"""
        cf = ContentFilter()
        # Keyword (warn) + SSN with block_high_risk=True should result in block
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("password is secret and SSN: 123-45-6789")
        assert result.action == "block"  # Block takes precedence over warn
