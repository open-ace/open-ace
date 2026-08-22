"""Unit tests for ContentFilter module."""

import re
import threading
from collections import OrderedDict
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


class TestCompiledPatternCache:
    """Test compiled pattern caching functionality (Issue #1906)."""

    def test_cache_initialization(self):
        """测试缓存属性正确初始化"""
        cf = ContentFilter()
        assert hasattr(cf, "_compiled_rules_cache")
        assert hasattr(cf, "_cache_lock")
        assert hasattr(cf, "_cache_hits")
        assert hasattr(cf, "_cache_misses")
        assert cf._cache_hits == 0
        assert cf._cache_misses == 0
        assert isinstance(cf._compiled_rules_cache, OrderedDict)

    def test_get_compiled_pattern_caches_pattern(self):
        """测试新模式正确编译并缓存"""
        cf = ContentFilter()
        pattern = r"\d+"
        compiled = cf._get_compiled_pattern(pattern, re.IGNORECASE)

        assert compiled is not None
        assert isinstance(compiled, type(re.compile(r"\d+")))
        assert (pattern, re.IGNORECASE) in cf._compiled_rules_cache
        assert cf._cache_misses == 1
        assert cf._cache_hits == 0

    def test_cache_hit_returns_same_object(self):
        """测试缓存命中时返回同一对象"""
        cf = ContentFilter()
        pattern = r"\d+"

        # First call - cache miss
        compiled1 = cf._get_compiled_pattern(pattern, re.IGNORECASE)
        assert cf._cache_misses == 1
        assert cf._cache_hits == 0

        # Second call - cache hit
        compiled2 = cf._get_compiled_pattern(pattern, re.IGNORECASE)
        assert cf._cache_hits == 1

        # Should be the exact same object
        assert compiled1 is compiled2

    def test_different_flags_cached_separately(self):
        """测试不同 flags 的正则独立缓存"""
        cf = ContentFilter()
        pattern = r"\d+"

        cf._get_compiled_pattern(pattern, re.IGNORECASE)
        cf._get_compiled_pattern(pattern, re.MULTILINE)
        cf._get_compiled_pattern(pattern, re.IGNORECASE | re.MULTILINE)

        # All should be cached separately
        assert cf._cache_misses == 3
        assert (pattern, re.IGNORECASE) in cf._compiled_rules_cache
        assert (pattern, re.MULTILINE) in cf._compiled_rules_cache
        assert (pattern, re.IGNORECASE | re.MULTILINE) in cf._compiled_rules_cache

    def test_invalidate_cache_clears_compiled_cache(self):
        """测试 invalidate_cache 清除编译缓存"""
        cf = ContentFilter()
        pattern = r"\d+"

        # Compile and cache a pattern
        cf._get_compiled_pattern(pattern, re.IGNORECASE)
        assert len(cf._compiled_rules_cache) == 1
        assert cf._cache_misses == 1

        # Invalidate cache
        cf.invalidate_cache()

        # Cache should be cleared and counters reset
        assert len(cf._compiled_rules_cache) == 0
        assert cf._cache_hits == 0
        assert cf._cache_misses == 0

    def test_invalid_pattern_returns_none(self):
        """测试无效正则返回 None 且不缓存"""
        cf = ContentFilter()
        invalid_pattern = r"[invalid("  # Unmatched parenthesis

        compiled = cf._get_compiled_pattern(invalid_pattern, re.IGNORECASE, rule_id="test-rule")

        assert compiled is None
        assert len(cf._compiled_rules_cache) == 0
        # Should count as miss for tracking purposes
        assert cf._cache_misses == 1

    def test_empty_pattern_returns_none(self):
        """测试空模式返回 None"""
        cf = ContentFilter()

        compiled = cf._get_compiled_pattern("", re.IGNORECASE)

        assert compiled is None
        assert len(cf._compiled_rules_cache) == 0

    def test_lru_eviction_removes_oldest(self):
        """测试 LRU 逐出移除最旧条目"""
        cf = ContentFilter(config={"max_compiled_cache_size": 2})

        # Add patterns: first, second
        cf._get_compiled_pattern(r"\d+", re.IGNORECASE)  # oldest
        cf._get_compiled_pattern(r"[a-z]+", re.IGNORECASE)  # newer
        assert len(cf._compiled_rules_cache) == 2

        # Add third pattern - should evict oldest
        cf._get_compiled_pattern(r"[A-Z]+", re.IGNORECASE)
        assert len(cf._compiled_rules_cache) == 2
        # Oldest should be evicted
        assert (r"\d+", re.IGNORECASE) not in cf._compiled_rules_cache
        # Second and newest should remain (LRU evicts only the oldest)
        assert (r"[a-z]+", re.IGNORECASE) in cf._compiled_rules_cache
        assert (r"[A-Z]+", re.IGNORECASE) in cf._compiled_rules_cache

    def test_lru_access_moves_to_end(self):
        """测试访问缓存条目时移动到末尾（LRU 更新）"""
        cf = ContentFilter(config={"max_compiled_cache_size": 3})

        # Add patterns
        cf._get_compiled_pattern(r"first", re.IGNORECASE)
        cf._get_compiled_pattern(r"second", re.IGNORECASE)
        cf._get_compiled_pattern(r"third", re.IGNORECASE)

        # Access first again - should move to end
        cf._get_compiled_pattern(r"first", re.IGNORECASE)

        # Add fourth - should evict second (oldest after first was accessed)
        cf._get_compiled_pattern(r"fourth", re.IGNORECASE)

        assert len(cf._compiled_rules_cache) == 3
        assert (r"first", re.IGNORECASE) in cf._compiled_rules_cache
        assert (r"second", re.IGNORECASE) not in cf._compiled_rules_cache  # evicted

    def test_get_stats_includes_cache_info(self):
        """测试 get_stats 返回缓存统计信息"""
        cf = ContentFilter()
        cf._get_compiled_pattern(r"\d+", re.IGNORECASE)
        cf._get_compiled_pattern(r"\d+", re.IGNORECASE)  # hit
        cf._get_compiled_pattern(r"[a-z]+", re.IGNORECASE)  # miss

        stats = cf.get_stats()

        assert "compiled_cache_size" in stats
        assert "compiled_cache_hits" in stats
        assert "compiled_cache_misses" in stats
        assert "compiled_cache_hit_rate" in stats
        assert "compiled_cache_max_size" in stats
        assert stats["compiled_cache_size"] == 2
        assert stats["compiled_cache_hits"] == 1
        assert stats["compiled_cache_misses"] == 2
        assert stats["compiled_cache_hit_rate"] == 33.33  # 1/(1+2) * 100

    def test_concurrent_access_thread_safe(self):
        """测试多线程并发访问无竞态条件"""
        cf = ContentFilter()
        pattern = r"\d+"
        errors = []
        results = []

        def compile_pattern():
            try:
                compiled = cf._get_compiled_pattern(pattern, re.IGNORECASE)
                results.append(compiled is not None)
            except (
                Exception
            ) as e:  # allow-swallow: collect per-thread errors; the driving test asserts errors is empty
                errors.append(e)

        # Create multiple threads
        threads = [threading.Thread(target=compile_pattern) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0
        # All threads should have successfully compiled
        assert all(results)
        # Cache should have exactly one entry
        assert len(cf._compiled_rules_cache) == 1

    def test_check_content_uses_cache(self):
        """测试 check_content 使用缓存而非重复编译"""
        # Setup mock governance_repo with regex rule
        mock_repo = MagicMock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": "test-rule",
                "pattern": r"\d+",
                "type": "regex",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
            }
        ]

        cf = ContentFilter(governance_repo=mock_repo)

        # First check - should compile and cache
        result1 = cf.check_content("Number: 123")
        assert result1.passed is True
        assert any(r["id"] == "test-rule" for r in result1.matched_rules)

        # Check cache stats
        initial_hits = cf._cache_hits

        # Second check with same pattern - should hit cache
        result2 = cf.check_content("Another: 456")
        assert result2.passed is True

        # Cache hits should have increased
        assert cf._cache_hits > initial_hits


class TestCacheWithDatabaseRules:
    """Test cache behavior with database rules integration."""

    def test_cache_invalidation_on_rule_change(self):
        """测试规则变更后缓存正确失效"""
        mock_repo = MagicMock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": "rule-1",
                "pattern": r"\d+",
                "type": "regex",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
            }
        ]

        cf = ContentFilter(governance_repo=mock_repo)

        # First check
        cf.check_content("Test 123")
        assert cf._cache_misses >= 1

        # Simulate rule update - invalidate cache
        cf.invalidate_cache()

        # Update mock to return different rules
        mock_repo.get_filter_rules.return_value = [
            {
                "id": "rule-2",
                "pattern": r"[a-z]+",
                "type": "regex",
                "action": "block",
                "severity": "high",
                "is_enabled": True,
            }
        ]

        # Next check should use new rules
        result = cf.check_content("test")
        assert result.passed is False  # Blocked by new rule
        assert cf._cache_misses >= 1  # New misses after invalidation

    def test_multiple_rules_share_cache(self):
        """测试多个规则共享同一缓存"""
        mock_repo = MagicMock()
        mock_repo.get_filter_rules.return_value = [
            {
                "id": "rule-1",
                "pattern": r"\d+",
                "type": "regex",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
            },
            {
                "id": "rule-2",
                "pattern": r"[a-z]+",
                "type": "regex",
                "action": "warn",
                "severity": "medium",
                "is_enabled": True,
            },
        ]

        cf = ContentFilter(governance_repo=mock_repo)
        cf.check_content("test 123")

        # Both patterns should be cached
        assert len(cf._compiled_rules_cache) == 2

        # Second check should hit cache for both
        initial_hits = cf._cache_hits
        cf.check_content("another 456")
        assert cf._cache_hits > initial_hits


class TestPiiFalsePositiveSuppression:
    """Built-in PII detectors must not flag the digit-runs autonomous prompts
    legitimately contain — 15-digit commit SHAs / timestamp-IDs (matched as
    Amex credit cards → critical → block) and dates like 2026-08-12 (matched as
    international phones). Regression for #2499 (glm-5 autonomous workflows
    403-blocked by the content filter)."""

    @pytest.mark.regression
    def test_credit_card_amex_sha_not_flagged(self):
        """A 15-digit commit SHA / timestamp-ID is NOT an Amex card (fails Luhn)
        and must not be blocked."""
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("Commit 140506586314800 applied")
        assert result.passed is True
        assert not any(r["type"] == "pii_credit_card_amex" for r in result.matched_rules)

    @pytest.mark.regression
    def test_credit_card_amex_real_card_still_flagged(self):
        """A valid-Luhn Amex test PAN is still detected + blocked."""
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("Card 378282246310005")
        assert result.passed is False
        assert any(r["type"] == "pii_credit_card_amex" for r in result.matched_rules)

    @pytest.mark.regression
    def test_credit_card_visa_real_card_still_flagged(self):
        """A valid-Luhn Visa test PAN is still detected (16-digit card type)."""
        cf = ContentFilter(config={"block_high_risk": True})
        result = cf.check_content("Card 4111-1111-1111-1111")
        assert result.passed is False
        assert any(r["type"] == "pii_credit_card" for r in result.matched_rules)

    @pytest.mark.regression
    def test_phone_intl_date_not_flagged(self):
        """A date like 2026-08-12 is not an international phone number."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 2026-08-12 by auto-dev")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    def test_phone_intl_slash_date_not_flagged(self):
        """Slash-separated dates (2026/08/12) are not phones either."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Built on 2026/08/12")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    def test_phone_intl_real_phone_still_flagged(self):
        """A real international phone number is still detected."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Call +86 138 0013 8000 now")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    # =========================================================================
    # Issue #2828: Compact date (YYYYMMDD) false positive suppression
    # =========================================================================

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_compact_date_not_flagged(self):
        """A compact date like 20260818 is not an international phone number."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20260818 by auto-dev")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_compact_date_adjacent_to_text(self):
        """Compact date adjacent to text (no space) is not a phone number."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Report20260818")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_invalid_month_still_flagged(self):
        """Invalid month (13) in compact date is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 20261301")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_zero_month_still_flagged(self):
        """Zero month (00) in compact date is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 20260012")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_invalid_day_still_flagged(self):
        """Invalid day (32) in compact date is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 20260132")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_feb_30_non_leap_still_flagged(self):
        """Feb 30 in non-leap year is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 20260230")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_year_below_min_still_flagged(self):
        """Year below 1900 is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 18991231")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_year_above_max_still_flagged(self):
        """Year above 2100 is still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 21010101")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_leap_year_feb_29_not_flagged(self):
        """Feb 29 in leap year (2024) is a valid date, not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20240229")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_non_leap_year_feb_29_still_flagged(self):
        """Feb 29 in non-leap year (2025) is invalid, still flagged as phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Value 20250229")
        assert any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_non_leap_year_feb_28_not_flagged(self):
        """Feb 28 in non-leap year is valid, not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20250228")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_year_min_boundary_not_flagged(self):
        """Year 1900 (minimum) with valid date is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 19000101")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_year_max_boundary_not_flagged(self):
        """Year 2100 (maximum, non-leap century year) with valid date is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 21001231")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_month_boundary_jan_not_flagged(self):
        """Month 01 (January) boundary with valid date is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20260101")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_month_boundary_dec_not_flagged(self):
        """Month 12 (December) boundary with valid date is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20261231")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_31_day_month_jan_not_flagged(self):
        """31-day month (January) with day 31 is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20260131")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_31_day_month_jul_not_flagged(self):
        """31-day month (July) with day 31 is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20260731")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)

    @pytest.mark.regression
    @pytest.mark.issue(2828)
    def test_phone_intl_31_day_month_aug_not_flagged(self):
        """31-day month (August) with day 31 is not a phone."""
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("Created 20260831")
        assert not any(r["type"] == "pii_phone_intl" for r in result.matched_rules)


class TestIsCompactDate:
    """Unit tests for _is_compact_date static method (Issue #2828)."""

    def test_valid_compact_date(self):
        """Valid compact date returns True."""
        assert ContentFilter._is_compact_date("20260818") is True

    def test_not_8_digits_returns_false(self):
        """7-digit string returns False."""
        assert ContentFilter._is_compact_date("2026081") is False

    def test_9_digits_returns_false(self):
        """9-digit string returns False."""
        assert ContentFilter._is_compact_date("202608181") is False

    def test_non_digit_returns_false(self):
        """Non-digit string returns False."""
        assert ContentFilter._is_compact_date("2026081a") is False

    def test_leap_year_feb_29_returns_true(self):
        """Leap year (2024) Feb 29 returns True."""
        assert ContentFilter._is_compact_date("20240229") is True

    def test_non_leap_year_feb_29_returns_false(self):
        """Non-leap year (2025) Feb 29 returns False."""
        assert ContentFilter._is_compact_date("20250229") is False

    def test_century_year_2100_feb_28_returns_true(self):
        """Century year 2100 (non-leap) Feb 28 returns True."""
        assert ContentFilter._is_compact_date("21000228") is True

    def test_century_year_2100_feb_29_returns_false(self):
        """Century year 2100 (non-leap) Feb 29 returns False."""
        assert ContentFilter._is_compact_date("21000229") is False

    def test_leap_century_2000_feb_29_returns_true(self):
        """Leap century year 2000 (divisible by 400) Feb 29 returns True."""
        assert ContentFilter._is_compact_date("20000229") is True

    def test_invalid_month_13_returns_false(self):
        """Invalid month 13 returns False."""
        assert ContentFilter._is_compact_date("20261301") is False

    def test_invalid_month_00_returns_false(self):
        """Invalid month 00 returns False."""
        assert ContentFilter._is_compact_date("20260012") is False

    def test_invalid_day_32_returns_false(self):
        """Invalid day 32 returns False."""
        assert ContentFilter._is_compact_date("20260132") is False

    def test_apr_31_returns_false(self):
        """April 31 (30-day month) returns False."""
        assert ContentFilter._is_compact_date("20260431") is False

    def test_year_below_min_returns_false(self):
        """Year below 1900 returns False."""
        assert ContentFilter._is_compact_date("18991231") is False

    def test_year_above_max_returns_false(self):
        """Year above 2100 returns False."""
        assert ContentFilter._is_compact_date("21010101") is False

    def test_year_min_1900_returns_true(self):
        """Year 1900 (minimum) returns True for valid date."""
        assert ContentFilter._is_compact_date("19000101") is True

    def test_year_max_2100_returns_true(self):
        """Year 2100 (maximum) returns True for valid date."""
        assert ContentFilter._is_compact_date("21001231") is True


class TestTenantKeywordsCache:
    """Tests for tenant keywords cache functionality (Issue #2789)."""

    def test_get_tenant_id_from_tenant_config(self):
        """Should extract tenant_id from tenant_config."""
        cf = ContentFilter()
        tenant_id = cf._get_tenant_id({"tenant_id": 123}, None)
        assert tenant_id == 123

    def test_get_tenant_id_from_context(self):
        """Should extract tenant_id from context."""
        cf = ContentFilter()
        tenant_id = cf._get_tenant_id(None, {"tenant_id": 456})
        assert tenant_id == 456

    def test_get_tenant_id_tenant_config_priority(self):
        """Should prefer tenant_config over context."""
        cf = ContentFilter()
        tenant_id = cf._get_tenant_id({"tenant_id": 123}, {"tenant_id": 456})
        assert tenant_id == 123

    def test_get_tenant_id_returns_none_when_not_available(self):
        """Should return None when tenant_id not available."""
        cf = ContentFilter()
        tenant_id = cf._get_tenant_id(None, None)
        assert tenant_id is None

    def test_invalidate_tenant_keywords_cache(self):
        """Should clear cache for specific tenant."""
        cf = ContentFilter()
        # Populate cache
        cf._tenant_keywords_cache[1] = ["secret"]
        cf._tenant_keywords_version[1] = 1
        cf._tenant_keywords_cache_time[1] = 100.0

        # Invalidate
        cf.invalidate_tenant_keywords_cache(1)

        assert 1 not in cf._tenant_keywords_cache
        assert 1 not in cf._tenant_keywords_version
        assert 1 not in cf._tenant_keywords_cache_time

    def test_invalidate_tenant_keywords_cache_other_tenant_unchanged(self):
        """Should not affect other tenant's cache."""
        cf = ContentFilter()
        cf._tenant_keywords_cache[1] = ["secret1"]
        cf._tenant_keywords_cache[2] = ["secret2"]

        cf.invalidate_tenant_keywords_cache(1)

        assert 1 not in cf._tenant_keywords_cache
        assert cf._tenant_keywords_cache[2] == ["secret2"]

    def test_check_tenant_keywords_word_boundary(self):
        """Should detect tenant keywords with word boundary matching."""
        cf = ContentFilter()
        keywords = ["公司机密", "secret"]

        result = cf._check_tenant_keywords(
            content="This is 公司机密 information",
            tenant_keywords=keywords,
            match_mode="word_boundary",
        )

        assert "公司机密" in result

    def test_check_tenant_keywords_substring(self):
        """Should detect tenant keywords with substring matching."""
        cf = ContentFilter()
        keywords = ["公司机密"]

        result = cf._check_tenant_keywords(
            content="This is 公司机密文件", tenant_keywords=keywords, match_mode="substring"
        )

        assert "公司机密" in result

    def test_check_tenant_keywords_empty_list(self):
        """Should return empty set when no tenant keywords."""
        cf = ContentFilter()
        result = cf._check_tenant_keywords(
            content="secret password", tenant_keywords=[], match_mode="word_boundary"
        )
        assert result == set()

    def test_check_content_without_tenant_id_uses_builtin_only(self):
        """Should only use builtin keywords when no tenant_id."""
        cf = ContentFilter()
        result = cf.check_content("This is password info")

        # Should match builtin "password"
        assert any(
            r["type"] == "sensitive_keyword" and r["source"] == "builtin"
            for r in result.matched_rules
        )

    def test_check_content_with_tenant_id_loads_tenant_keywords(self):
        """Should load tenant keywords when tenant_id is provided."""
        mock_repo = MagicMock()
        mock_repo.get_enabled_tenant_keywords.return_value = ["公司机密"]
        mock_repo.get_tenant_keywords_version.return_value = 1

        cf = ContentFilter(governance_repo=mock_repo)

        result = cf.check_content(
            content="This is 公司机密",
            tenant_config={"tenant_id": 1},
        )

        # Should match tenant keyword
        tenant_keyword_matches = [
            r
            for r in result.matched_rules
            if r.get("type") == "sensitive_keyword" and r.get("source") == "tenant"
        ]
        assert len(tenant_keyword_matches) > 0


class TestTenantKeywordsCacheTTL:
    """Tests for tenant keywords cache TTL (Issue #2789)."""

    def test_cache_ttl_initialization(self):
        """Should initialize cache TTL from config."""
        cf = ContentFilter(config={"tenant_keywords_cache_ttl": 600})
        assert cf._tenant_keywords_cache_ttl == 600

    def test_cache_ttl_default(self):
        """Should use default TTL of 300 seconds."""
        cf = ContentFilter()
        assert cf._tenant_keywords_cache_ttl == 300


class TestNoTenantUserBehavior:
    """Tests for no-tenant user behavior (Issue #2789)."""

    def test_no_tenant_user_uses_builtin_keywords_only(self):
        """When tenant_id is None, should only use builtin keywords."""
        cf = ContentFilter()

        # Simulate no tenant context
        result = cf.check_content(
            content="This is password info",
            tenant_config=None,
            context=None,
        )

        # Should only match builtin keywords
        keyword_matches = [r for r in result.matched_rules if r.get("type") == "sensitive_keyword"]
        builtin_matches = [r for r in keyword_matches if r.get("source") == "builtin"]
        tenant_matches = [r for r in keyword_matches if r.get("source") == "tenant"]

        assert len(builtin_matches) > 0
        assert len(tenant_matches) == 0

    def test_tenant_config_without_tenant_id(self):
        """Should handle tenant_config without tenant_id field."""
        cf = ContentFilter()

        result = cf.check_content(
            content="This is password",
            tenant_config={
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "word_boundary",
            },
        )

        # Should use builtin keywords only
        keyword_matches = [r for r in result.matched_rules if r.get("type") == "sensitive_keyword"]
        assert all(r.get("source") == "builtin" for r in keyword_matches)
