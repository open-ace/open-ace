"""Unit tests for Usage Dedup Module (Issue #2184)."""

import time

import pytest

from app.modules.workspace.usage_dedup import (
    UsageDedupCache,
    get_dedup_cache,
    reset_dedup_cache_for_tests,
)
from app.modules.workspace.usage_evidence import UsageEvidence


class TestUsageDedupCache:
    """Test usage deduplication cache."""

    def setup_method(self):
        """Reset dedup cache before each test."""
        reset_dedup_cache_for_tests()

    def test_check_and_record_new_entry(self):
        """Test recording new entry."""
        cache = UsageDedupCache()

        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
            request_id="req-456",
        )

        result = cache.check_and_record(evidence)

        assert result is None  # No duplicate

    def test_dedup_by_request_id(self):
        """Test deduplication by request_id."""
        cache = UsageDedupCache()

        evidence1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            session_id="sess-123",
            request_id="req-789",
        )

        evidence2 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            session_id="sess-123",
            request_id="req-789",  # Same request_id
        )

        # First should succeed
        result1 = cache.check_and_record(evidence1)
        assert result1 is None

        # Second should be detected as duplicate
        result2 = cache.check_and_record(evidence2)
        assert result2 is not None
        assert result2.request_id == "req-789"

    def test_dedup_by_composite_key(self):
        """Test deduplication by composite key (no request_id)."""
        cache = UsageDedupCache()

        evidence1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
        )

        evidence2 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
        )

        # First should succeed
        result1 = cache.check_and_record(evidence1)
        assert result1 is None

        # Second should be detected as duplicate (same composite key)
        result2 = cache.check_and_record(evidence2)
        assert result2 is not None

    def test_no_dedup_different_tokens(self):
        """Test no deduplication for different token counts."""
        cache = UsageDedupCache()

        evidence1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            session_id="sess-123",
        )

        evidence2 = UsageEvidence(
            input_tokens=2000,  # Different input
            output_tokens=500,
            provider="openai",
            session_id="sess-123",
        )

        # Both should succeed (different tokens)
        result1 = cache.check_and_record(evidence1)
        result2 = cache.check_and_record(evidence2)

        assert result1 is None
        assert result2 is None

    def test_no_dedup_different_session(self):
        """Test no deduplication for different sessions."""
        cache = UsageDedupCache()

        evidence1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            session_id="sess-123",
        )

        evidence2 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="openai",
            session_id="sess-456",  # Different session
        )

        # Both should succeed (different sessions)
        result1 = cache.check_and_record(evidence1)
        result2 = cache.check_and_record(evidence2)

        assert result1 is None
        assert result2 is None

    def test_get_stats(self):
        """Test getting cache statistics."""
        cache = UsageDedupCache()

        # Add some entries
        for i in range(5):
            ev = UsageEvidence(
                input_tokens=100 * i,
                output_tokens=50 * i,
                provider="openai",
                session_id=f"sess-{i}",
                request_id=f"req-{i}",
            )
            cache.check_and_record(ev)

        stats = cache.get_stats()

        assert "request_id_cache_size" in stats
        assert "composite_cache_size" in stats
        assert stats["request_id_cache_size"] == 5

    def test_clear(self):
        """Test clearing cache."""
        cache = UsageDedupCache()

        # Add entry
        ev = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
            request_id="req-456",
        )
        cache.check_and_record(ev)

        # Clear
        cache.clear()

        stats = cache.get_stats()
        assert stats["request_id_cache_size"] == 0
        assert stats["composite_cache_size"] == 0

    def test_ttl_expiry(self):
        """Test TTL expiry (using short TTL for test)."""
        cache = UsageDedupCache(maxsize=100, ttl=0.1)  # 0.1 second TTL

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
            request_id="req-456",
        )

        # Record entry
        cache.check_and_record(evidence)

        # Immediately check - should be duplicate
        result1 = cache.check_and_record(evidence)
        assert result1 is not None

        # Wait for TTL
        time.sleep(0.2)

        # After TTL - should not be duplicate
        result2 = cache.check_and_record(evidence)
        assert result2 is None


class TestGetDedupCache:
    """Test singleton dedup cache."""

    def setup_method(self):
        """Reset dedup cache before each test."""
        reset_dedup_cache_for_tests()

    def test_singleton(self):
        """Test that get_dedup_cache returns singleton."""
        cache1 = get_dedup_cache()
        cache2 = get_dedup_cache()

        assert cache1 is cache2

    def test_reset_for_tests(self):
        """Test resetting singleton for tests."""
        cache1 = get_dedup_cache()

        # Add entry
        ev = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
        )
        cache1.check_and_record(ev)

        # Reset
        reset_dedup_cache_for_tests()

        # Get new instance
        cache2 = get_dedup_cache()

        # Should be different instance
        assert cache1 is not cache2
        stats = cache2.get_stats()
        assert stats["request_id_cache_size"] == 0