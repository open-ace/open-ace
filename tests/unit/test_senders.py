"""Unit tests for sender validation utility."""

from app.utils.senders import is_valid_sender


class TestIsValidSender:
    """Tests for is_valid_sender function."""

    def test_is_valid_sender_normal_name(self):
        assert is_valid_sender("alice") is True

    def test_is_valid_sender_chinese_name(self):
        assert is_valid_sender("张三") is True

    def test_is_valid_sender_empty(self):
        assert is_valid_sender("") is False

    def test_is_valid_sender_none_like(self):
        # Function accepts str, but empty string should be False
        assert is_valid_sender("") is False

    def test_is_valid_sender_ou_short(self):
        """Short ou_ names (length <= 10) should be valid."""
        assert is_valid_sender("ou_abc") is True  # length 6
        assert is_valid_sender("ou_1234567") is True  # length 10
        assert is_valid_sender("ou_") is True  # length 3

    def test_is_valid_sender_ou_long(self):
        """Long ou_ names (length > 10) should be invalid (Feishu Open IDs)."""
        assert is_valid_sender("ou_1234567890") is False  # length 13
        assert is_valid_sender("ou_3e479c7f81f8674741d778e8f838f8ed") is False

    def test_is_valid_sender_ou_exactly_10(self):
        """Edge case: ou_ prefix with total length exactly 10 should be valid."""
        # "ou_1234567" = 10 chars
        assert is_valid_sender("ou_1234567") is True

    def test_is_valid_sender_ou_exactly_11(self):
        """Edge case: ou_ prefix with total length 11 should be invalid."""
        # "ou_12345678" = 11 chars
        assert is_valid_sender("ou_12345678") is False

    def test_is_valid_sender_webui_format(self):
        """WebUI format (system_account-hostname-tool) should be valid."""
        assert is_valid_sender("user1-host1-claude") is True
