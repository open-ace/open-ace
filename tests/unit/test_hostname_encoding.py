"""
Unit tests for hostname encoding validation (Issue #3081).

Tests the validate_string_encoding function to ensure it correctly
identifies valid UTF-8 strings and rejects mojibake patterns.
"""

import pytest

from app.routes.remote import validate_string_encoding


class TestValidateStringEncoding:
    """Test suite for validate_string_encoding function."""

    def test_valid_ascii_string(self):
        """Test that valid ASCII strings pass validation."""
        is_valid, error = validate_string_encoding("MyComputer", "hostname")
        assert is_valid is True
        assert error is None

    def test_valid_chinese_string(self):
        """Test that valid Chinese strings pass validation."""
        is_valid, error = validate_string_encoding("我的电脑", "hostname")
        assert is_valid is True
        assert error is None

    def test_valid_japanese_string(self):
        """Test that valid Japanese strings pass validation."""
        is_valid, error = validate_string_encoding("マイコンピュータ", "hostname")
        assert is_valid is True
        assert error is None

    def test_valid_korean_string(self):
        """Test that valid Korean strings pass validation."""
        is_valid, error = validate_string_encoding("내컴퓨터", "hostname")
        assert is_valid is True
        assert error is None

    def test_valid_mixed_language_string(self):
        """Test that valid mixed-language strings pass validation."""
        is_valid, error = validate_string_encoding("My电脑-测试", "hostname")
        assert is_valid is True
        assert error is None

    def test_valid_emoji_string(self):
        """Test that valid emoji strings pass validation."""
        is_valid, error = validate_string_encoding("💻MyComputer", "hostname")
        assert is_valid is True
        assert error is None

    def test_none_value_allowed(self):
        """Test that None values are allowed (field is optional)."""
        is_valid, error = validate_string_encoding(None, "hostname")
        assert is_valid is True
        assert error is None

    def test_empty_string_allowed(self):
        """Test that empty strings are allowed."""
        is_valid, error = validate_string_encoding("", "hostname")
        assert is_valid is True
        assert error is None

    def test_mojibake_double_question_mark_rejected(self):
        """Test that strings with double question mark pattern are rejected."""
        is_valid, error = validate_string_encoding("test?? value", "hostname")
        assert is_valid is False
        assert "mojibake" in error.lower()
        assert "hostname" in error

    def test_mojibake_replacement_character_rejected(self):
        """Test that strings with replacement character are rejected."""
        # Unicode replacement character (U+FFFD)
        replacement_char = chr(65533)
        is_valid, error = validate_string_encoding(f"test{replacement_char}value", "hostname")
        assert is_valid is False
        assert "mojibake" in error.lower()
        assert "hostname" in error

    def test_mojibake_triple_question_mark_rejected(self):
        """Test that strings with three or more consecutive question marks are rejected."""
        is_valid, error = validate_string_encoding("test??? value", "hostname")
        assert is_valid is False
        assert "mojibake" in error.lower()
        assert "hostname" in error

    def test_single_question_mark_allowed(self):
        """Test that single question mark is allowed (legitimate character)."""
        is_valid, error = validate_string_encoding("test?value", "hostname")
        assert is_valid is True
        assert error is None

    def test_double_question_mark_without_space_allowed(self):
        """Test that '??' without space before question mark is allowed (might be legitimate)."""
        # Note: The pattern is "?? " (double question with space), not just "??"
        is_valid, error = validate_string_encoding("test??value", "hostname")
        assert is_valid is True
        assert error is None

    def test_various_field_names(self):
        """Test that field name is correctly included in error message."""
        is_valid, error = validate_string_encoding("test?? value", "hostname")
        assert is_valid is False
        assert "hostname" in error

        is_valid, error = validate_string_encoding("test?? value", "machine_name")
        assert is_valid is False
        assert "machine_name" in error

    def test_long_valid_string(self):
        """Test that long valid strings are handled correctly."""
        long_string = "测试" * 50  # 100 Chinese characters
        is_valid, error = validate_string_encoding(long_string, "hostname")
        assert is_valid is True
        assert error is None

    def test_special_characters(self):
        """Test that valid special characters are allowed."""
        special_chars = "test-PC_123.local"
        is_valid, error = validate_string_encoding(special_chars, "hostname")
        assert is_valid is True
        assert error is None

    def test_unicode_special_characters(self):
        """Test that valid Unicode special characters are allowed."""
        # Various Unicode special characters
        unicode_special = "test™©®¶§"
        is_valid, error = validate_string_encoding(unicode_special, "hostname")
        assert is_valid is True
        assert error is None

    def test_real_world_examples(self):
        """Test real-world hostname examples."""
        # Real-world valid hostnames
        test_cases = [
            "WIN-PC1",
            "desktop-home",
            "laptop.work.local",
            "MACBOOK-PRO",
            "server01",
            "开发机",
            "测试服务器",
            "マイPC",
        ]

        for hostname in test_cases:
            is_valid, error = validate_string_encoding(hostname, "hostname")
            assert is_valid is True, f"Failed for hostname: {hostname}"
            assert error is None

    def test_real_world_mojibake_examples(self):
        """Test real-world mojibake patterns that should be rejected."""
        # Real-world mojibake patterns (as seen in Issue #3081)
        # Note: These patterns indicate corrupted encoding
        replacement_char = chr(65533)
        mojibake_patterns = [
            "test?? value",  # Double question with space
            "test??? value",  # Triple question
            f"test{replacement_char}value",  # Replacement character
        ]

        for hostname in mojibake_patterns:
            is_valid, error = validate_string_encoding(hostname, "hostname")
            assert is_valid is False, f"Should reject mojibake: {hostname}"
            assert error is not None


class TestValidationEdgeCases:
    """Edge case tests for validation."""

    def test_very_long_string(self):
        """Test very long string handling."""
        # Create a string near the maximum allowed length
        long_string = "a" * 250
        is_valid, error = validate_string_encoding(long_string, "hostname")
        assert is_valid is True
        assert error is None

    def test_string_with_newlines(self):
        """Test string with newlines."""
        multiline = "test\nvalue"
        is_valid, error = validate_string_encoding(multiline, "hostname")
        assert is_valid is True
        assert error is None

    def test_string_with_tabs(self):
        """Test string with tabs."""
        tabbed = "test\tvalue"
        is_valid, error = validate_string_encoding(tabbed, "hostname")
        assert is_valid is True
        assert error is None

    def test_mixed_mojibake_patterns(self):
        """Test string with multiple mojibake patterns."""
        # String with both replacement char and triple question
        replacement_char = chr(65533)
        mixed = f"test?? value{replacement_char}more"
        is_valid, error = validate_string_encoding(mixed, "hostname")
        assert is_valid is False
        assert error is not None


class TestValidationPerformance:
    """Performance tests for validation."""

    def test_validation_speed(self):
        """Test that validation is fast enough (< 10ms per call)."""
        import time

        # Test with a typical hostname
        hostname = "我的电脑"

        # Run validation 100 times and measure average time
        iterations = 100
        start_time = time.time()

        for _ in range(iterations):
            validate_string_encoding(hostname, "hostname")

        end_time = time.time()
        avg_time_ms = ((end_time - start_time) / iterations) * 1000

        # Should be much less than 10ms
        assert avg_time_ms < 10, f"Validation too slow: {avg_time_ms:.2f}ms per call"

    def test_validation_speed_mojibake(self):
        """Test that validation is fast even for mojibake detection."""
        import time

        # Test with mojibake pattern
        hostname = "test?? value"

        iterations = 100
        start_time = time.time()

        for _ in range(iterations):
            validate_string_encoding(hostname, "hostname")

        end_time = time.time()
        avg_time_ms = ((end_time - start_time) / iterations) * 1000

        # Should be much less than 10ms
        assert avg_time_ms < 10, f"Validation too slow: {avg_time_ms:.2f}ms per call"
