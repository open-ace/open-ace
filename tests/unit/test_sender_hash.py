"""
Unit tests for sender_hash utility.

Issue #1573: PostgreSQL and SQLite statistics consistency
"""

import pytest

from app.utils.sender_hash import (
    EMPTY_SENDER_HASH,
    MAX_SENDER_LENGTH,
    compute_sender_hash,
    verify_hash_consistency,
)


class TestComputeSenderHash:
    """Tests for compute_sender_hash function."""

    def test_empty_string_returns_special_value(self):
        """Empty string should return EMPTY_SENDER_HASH (-2)."""
        assert compute_sender_hash("") == EMPTY_SENDER_HASH
        assert EMPTY_SENDER_HASH == -2

    def test_none_returns_special_value(self):
        """None should return EMPTY_SENDER_HASH (-2)."""
        assert compute_sender_hash(None) == EMPTY_SENDER_HASH

    def test_deterministic_hash(self):
        """Same input should always produce same output."""
        hash1 = compute_sender_hash("alice")
        hash2 = compute_sender_hash("alice")
        assert hash1 == hash2

    def test_different_senders_produce_different_hashes(self):
        """Different sender names should produce different hashes."""
        hash_alice = compute_sender_hash("alice")
        hash_bob = compute_sender_hash("bob")
        assert hash_alice != hash_bob

    def test_hash_is_negative(self):
        """All hashes should be negative to distinguish from positive user_id."""
        # Empty string case
        assert compute_sender_hash("") < 0

        # Normal cases
        assert compute_sender_hash("alice") < 0
        assert compute_sender_hash("bob") < 0
        assert compute_sender_hash("test_user") < 0

    def test_unicode_sender_handled_correctly(self):
        """Unicode characters should be handled correctly."""
        hash_unicode = compute_sender_hash("张三")
        assert isinstance(hash_unicode, int)
        assert hash_unicode < 0

        # Should be deterministic
        assert hash_unicode == compute_sender_hash("张三")

    def test_special_characters_handled(self):
        """Special characters should be handled correctly."""
        special_names = [
            "user@example.com",
            "user-name_with.special:chars",
            "user/name\\test",
            "user\ttab\nnewline",
        ]

        hashes = [compute_sender_hash(name) for name in special_names]

        # All should be negative integers
        for h in hashes:
            assert isinstance(h, int)
            assert h < 0

        # All should be different
        assert len(set(hashes)) == len(hashes)

    def test_long_string_truncation(self):
        """Strings longer than MAX_SENDER_LENGTH should be truncated."""
        long_string = "a" * 2000
        hash_long = compute_sender_hash(long_string)

        # Should still produce a valid hash
        assert isinstance(hash_long, int)
        assert hash_long < 0

        # Should match hash of truncated string
        expected_hash = compute_sender_hash("a" * MAX_SENDER_LENGTH)
        assert hash_long == expected_hash

    def test_custom_max_length(self):
        """Should respect custom max_length parameter."""
        short_string = "test"
        hash_short = compute_sender_hash(short_string, max_length=2)

        # Should match hash of first 2 characters only
        expected_hash = compute_sender_hash("te")
        assert hash_short == expected_hash

    def test_exact_max_length(self):
        """String at exactly MAX_SENDER_LENGTH should not be truncated."""
        exact_length_string = "a" * MAX_SENDER_LENGTH
        hash_exact = compute_sender_hash(exact_length_string)

        # Should be deterministic
        assert hash_exact == compute_sender_hash(exact_length_string)

    def test_hash_matches_expected_algorithm(self):
        """Hash should match PostgreSQL algorithm:
        -ABS(('0x' || LEFT(MD5(sender_name), 16))::BIT(64)::BIGINT)

        For "test":
        - MD5("test") = "098f6bcd4621d373cade4e832627b4f6"
        - LEFT(MD5, 16) = "098f6bcd4621d373"
        - As hex: 0x098f6bcd4621d373 = 688887797400064883
        - Negative: -688887797400064883
        """
        # This test verifies the algorithm matches PostgreSQL
        expected_hash = -688887797400064883
        actual_hash = compute_sender_hash("test")

        assert actual_hash == expected_hash


class TestVerifyHashConsistency:
    """Tests for verify_hash_consistency function."""

    def test_consistency_check_returns_true(self):
        """Consistency check should always return True for deterministic hash."""
        assert verify_hash_consistency("alice") is True
        assert verify_hash_consistency("") is True
        assert verify_hash_consistency("张三") is True

    def test_consistency_check_multiple_calls(self):
        """Multiple consistency checks should all pass."""
        test_names = ["alice", "bob", "test@example.com", "", "用户"]

        for name in test_names:
            assert verify_hash_consistency(name) is True


class TestHashConstants:
    """Tests for module constants."""

    def test_empty_sender_hash_value(self):
        """EMPTY_SENDER_HASH should be -2."""
        assert EMPTY_SENDER_HASH == -2

    def test_max_sender_length_value(self):
        """MAX_SENDER_LENGTH should be 1000."""
        assert MAX_SENDER_LENGTH == 1000


class TestHashDistribution:
    """Tests for hash distribution quality."""

    def test_hash_distribution_is_reasonable(self):
        """Hash values should be reasonably distributed (no obvious patterns)."""
        # Generate hashes for sequential names
        names = [f"user_{i}" for i in range(100)]
        hashes = [compute_sender_hash(name) for name in names]

        # All should be unique (no collisions expected for 100 values)
        assert len(set(hashes)) == 100

        # Values should be spread out (not all clustered together)
        hash_set = set(hashes)
        min_hash = min(hash_set)
        max_hash = max(hash_set)

        # Range should be reasonably wide (at least 10^15 for 100 values)
        hash_range = abs(max_hash - min_hash)
        assert hash_range > 10**15

    def test_no_common_collisions(self):
        """Common sender names should not collide."""
        common_names = [
            "alice",
            "bob",
            "charlie",
            "david",
            "eve",
            "frank",
            "grace",
            "henry",
            "ivy",
            "jack",
        ]

        hashes = [compute_sender_hash(name) for name in common_names]
        assert len(set(hashes)) == len(common_names)