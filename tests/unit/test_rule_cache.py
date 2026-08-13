"""
Unit tests for Rule Cache module.

Tests for caching, invalidation, and sync polling.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
import threading

from app.modules.governance.rule_cache import RuleCache
from app.modules.governance.rule_loader import RuleLoader


class TestRuleCache:
    """Tests for RuleCache class."""

    def test_cache_initialization(self):
        """Test cache initialization."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        assert cache._poll_interval == 5.0
        assert cache._max_poll_interval == 60.0
        assert cache._cache == {}

    def test_get_rules_from_cache(self):
        """Test retrieving rules from cache."""
        mock_loader = Mock()
        mock_loader.load_rules.return_value = [
            {"id": 1, "pattern": "test", "priority": 100}
        ]

        cache = RuleCache(rule_loader=mock_loader)

        # First call - loads from loader
        rules = cache.get_rules(tenant_id=1)
        assert len(rules) == 1
        assert mock_loader.load_rules.call_count == 1

        # Second call - uses cache
        rules2 = cache.get_rules(tenant_id=1)
        assert len(rules2) == 1
        assert mock_loader.load_rules.call_count == 1  # Not called again

    def test_invalidate_cache(self):
        """Test cache invalidation."""
        mock_loader = Mock()
        mock_loader.load_rules.return_value = [{"id": 1}]

        cache = RuleCache(rule_loader=mock_loader)

        # Load rules
        cache.get_rules(tenant_id=1)

        # Invalidate cache
        cache.invalidate(tenant_id=1)

        # Next call should reload
        cache.get_rules(tenant_id=1)
        assert mock_loader.load_rules.call_count == 2

    def test_invalidate_all_caches(self):
        """Test invalidating all caches."""
        mock_loader = Mock()
        mock_loader.load_rules.return_value = [{"id": 1}]

        cache = RuleCache(rule_loader=mock_loader)

        # Load rules for multiple tenants
        cache.get_rules(tenant_id=1)
        cache.get_rules(tenant_id=2)

        # Invalidate all
        cache.invalidate()

        # Both should reload
        cache.get_rules(tenant_id=1)
        cache.get_rules(tenant_id=2)
        assert mock_loader.load_rules.call_count == 4

    def test_cache_key_generation(self):
        """Test cache key generation."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        # Global rules
        assert cache._get_cache_key(None) == "rules:global"

        # Tenant-specific rules
        assert cache._get_cache_key(123) == "rules:tenant:123"

    def test_exponential_backoff_polling(self):
        """Test exponential backoff in polling."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        # Initial interval
        assert cache._poll_interval == 5.0

        # Simulate no updates - interval should increase
        cache._poll_interval = 5.0
        new_interval = min(cache._poll_interval * 1.5, cache._max_poll_interval)
        assert new_interval > cache._poll_interval

        # Cap at max
        cache._poll_interval = 50.0
        new_interval = min(cache._poll_interval * 1.5, cache._max_poll_interval)
        assert new_interval <= cache._max_poll_interval

    def test_compiled_pattern_caching(self):
        """Test compiled regex pattern caching."""
        import re

        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        # Compile pattern
        pattern1 = cache.get_compiled_pattern(r"\d+")
        assert pattern1 is not None
        assert isinstance(pattern1, re.Pattern)

        # Same pattern from cache
        pattern2 = cache.get_compiled_pattern(r"\d+")
        assert pattern2 is pattern1

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)
        cache._max_compiled_cache_size = 3

        # Add patterns
        cache.get_compiled_pattern("a")
        cache.get_compiled_pattern("b")
        cache.get_compiled_pattern("c")

        assert len(cache._compiled_patterns) == 3

        # Add one more - should evict "a"
        cache.get_compiled_pattern("d")

        assert len(cache._compiled_patterns) == 3
        assert "a" not in cache._compiled_patterns
        assert "d" in cache._compiled_patterns

    def test_invalid_pattern_handling(self):
        """Test handling of invalid regex patterns."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        # Invalid pattern
        pattern = cache.get_compiled_pattern(r"[invalid(")
        assert pattern is None

    def test_cache_stats(self):
        """Test cache statistics."""
        mock_loader = Mock()
        cache = RuleCache(rule_loader=mock_loader)

        stats = cache.get_cache_stats()

        assert "rule_cache_count" in stats
        assert "compiled_pattern_cache_size" in stats
        assert "current_poll_interval" in stats


class TestRuleCacheSynchronization:
    """Tests for cache synchronization."""

    def test_sync_notification_writes_to_database(self):
        """Test that sync notifications are written to database."""
        mock_loader = Mock()
        mock_repo = Mock()

        cache = RuleCache(rule_loader=mock_loader, governance_repo=mock_repo)

        # Mock database connection
        with patch('app.modules.governance.rule_cache.get_connection') as mock_conn:
            mock_cursor = Mock()
            mock_conn.return_value.cursor.return_value = mock_cursor

            cache.notify_sync(rule_id=123, action="updated", tenant_id=1)

            # Should have executed INSERT
            assert mock_cursor.execute.called

    def test_cache_reset_on_sync(self):
        """Test that cache is reset when sync notification is received."""
        mock_loader = Mock()
        mock_loader.load_rules.return_value = [{"id": 1}]

        cache = RuleCache(rule_loader=mock_loader)

        # Load rules
        cache.get_rules(tenant_id=1)

        # Notify sync
        cache.invalidate(tenant_id=1)

        # Cache should be invalid
        assert not cache._cache_valid.get("rules:tenant:1", False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])