"""Unit tests for placeholder value detection."""

import pytest

from app.utils.placeholder import is_placeholder_value


class TestIsPlaceholderValue:
    """Tests for is_placeholder_value function."""

    def test_feishu_app_id_placeholder(self):
        """Feishu app_id placeholder should be detected."""
        assert is_placeholder_value("<FEISHU_APP_ID>") is True

    def test_feishu_app_secret_placeholder(self):
        """Feishu app_secret placeholder should be detected."""
        assert is_placeholder_value("<FEISHU_APP_SECRET>") is True

    def test_dingtalk_app_key_placeholder(self):
        """DingTalk app_key placeholder should be detected."""
        assert is_placeholder_value("<DINGTALK_APP_KEY>") is True

    def test_dingtalk_app_secret_placeholder(self):
        """DingTalk app_secret placeholder should be detected."""
        assert is_placeholder_value("<DINGTALK_APP_SECRET>") is True

    def test_generic_app_id_placeholder(self):
        """Generic APP_ID placeholder should be detected."""
        assert is_placeholder_value("<APP_ID>") is True

    def test_generic_app_secret_placeholder(self):
        """Generic APP_SECRET placeholder should be detected."""
        assert is_placeholder_value("<APP_SECRET>") is True

    def test_your_app_id_placeholder(self):
        """your_app_id placeholder should be detected."""
        assert is_placeholder_value("your_app_id") is True

    def test_your_app_secret_placeholder(self):
        """your_app_secret placeholder should be detected."""
        assert is_placeholder_value("your_app_secret") is True

    def test_cli_placeholder(self):
        """CLI placeholder format should be detected."""
        assert is_placeholder_value("cli_xxxxxxxxxxxxxxxx") is True

    def test_real_value_not_placeholder(self):
        """Real credential values should not be detected as placeholder."""
        assert is_placeholder_value("cli_a1b2c3d4e5f6g7h8") is False
        assert is_placeholder_value("test-app-id") is False
        assert is_placeholder_value("my_real_app_key_12345") is False

    def test_empty_string_not_placeholder(self):
        """Empty string should return False."""
        assert is_placeholder_value("") is False

    def test_case_insensitive(self):
        """Placeholder detection should be case insensitive."""
        assert is_placeholder_value("<FEISHU_APP_ID>") is True
        assert is_placeholder_value("<feishu_app_id>") is True
        assert is_placeholder_value("<Feishu_App_Id>") is True
        assert is_placeholder_value("YOUR_APP_ID") is True
        assert is_placeholder_value("Your_App_Id") is True
