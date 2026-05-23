"""Unit tests for UserToolAccount model, get_tool_type_display, and TOOL_TYPES."""

from datetime import datetime

import pytest

from app.models.user_tool_account import TOOL_TYPES, UserToolAccount, get_tool_type_display


class TestUserToolAccount:
    """Test UserToolAccount dataclass."""

    def test_create_with_required_fields(self):
        uta = UserToolAccount(id=1, user_id=10, tool_account="alice_qwen")
        assert uta.id == 1
        assert uta.user_id == 10
        assert uta.tool_account == "alice_qwen"
        assert uta.tool_type is None
        assert uta.description is None
        assert uta.created_at is None
        assert uta.updated_at is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        uta = UserToolAccount(
            id=5,
            user_id=20,
            tool_account="bob_slack",
            tool_type="slack",
            description="Bob's Slack account",
            created_at=now,
            updated_at=now,
        )
        assert uta.id == 5
        assert uta.user_id == 20
        assert uta.tool_account == "bob_slack"
        assert uta.tool_type == "slack"
        assert uta.description == "Bob's Slack account"
        assert uta.created_at == now
        assert uta.updated_at == now

    def test_to_dict(self):
        now = datetime(2025, 8, 10, 14, 30, 0)
        uta = UserToolAccount(
            id=3,
            user_id=7,
            tool_account="charlie_feishu",
            tool_type="feishu",
            description="Charlie Feishu",
            created_at=now,
            updated_at=now,
        )
        d = uta.to_dict()
        assert d["id"] == 3
        assert d["user_id"] == 7
        assert d["tool_account"] == "charlie_feishu"
        assert d["tool_type"] == "feishu"
        assert d["description"] == "Charlie Feishu"
        assert d["created_at"] == "2025-08-10T14:30:00"
        assert d["updated_at"] == "2025-08-10T14:30:00"

    def test_to_dict_none_timestamps(self):
        uta = UserToolAccount(id=1, user_id=1, tool_account="test")
        d = uta.to_dict()
        assert d["created_at"] is None
        assert d["updated_at"] is None

    def test_to_dict_none_optional_fields(self):
        uta = UserToolAccount(id=1, user_id=1, tool_account="test")
        d = uta.to_dict()
        assert d["tool_type"] is None
        assert d["description"] is None

    def test_different_tool_types(self):
        for tool_type in ["qwen", "claude", "openclaw", "feishu", "slack"]:
            uta = UserToolAccount(id=1, user_id=1, tool_account="acc", tool_type=tool_type)
            assert uta.tool_type == tool_type


class TestGetToolTypeDisplay:
    """Test get_tool_type_display function."""

    def test_qwen(self):
        assert get_tool_type_display("qwen") == "Qwen"

    def test_claude(self):
        assert get_tool_type_display("claude") == "Claude"

    def test_openclaw(self):
        assert get_tool_type_display("openclaw") == "Openclaw"

    def test_feishu(self):
        assert get_tool_type_display("feishu") == "飞书"

    def test_slack(self):
        assert get_tool_type_display("slack") == "Slack"

    def test_other(self):
        assert get_tool_type_display("other") == "其他"

    def test_none_returns_default(self):
        assert get_tool_type_display(None) == "其他"

    def test_empty_string_returns_default(self):
        assert get_tool_type_display("") == "其他"

    def test_unknown_type_returns_input(self):
        assert get_tool_type_display("unknown_tool") == "unknown_tool"

    def test_custom_type_returns_input(self):
        assert get_tool_type_display("github") == "github"


class TestToolTypes:
    """Test TOOL_TYPES dictionary completeness."""

    def test_has_qwen(self):
        assert "qwen" in TOOL_TYPES

    def test_has_claude(self):
        assert "claude" in TOOL_TYPES

    def test_has_openclaw(self):
        assert "openclaw" in TOOL_TYPES

    def test_has_feishu(self):
        assert "feishu" in TOOL_TYPES

    def test_has_slack(self):
        assert "slack" in TOOL_TYPES

    def test_has_other(self):
        assert "other" in TOOL_TYPES

    def test_total_count(self):
        assert len(TOOL_TYPES) == 6

    def test_all_values_are_strings(self):
        for key, value in TOOL_TYPES.items():
            assert isinstance(value, str), f"TOOL_TYPES['{key}'] value is not a string"

    def test_all_keys_are_strings(self):
        for key in TOOL_TYPES:
            assert isinstance(key, str), f"TOOL_TYPES key {key!r} is not a string"

    def test_no_empty_display_names(self):
        for key, value in TOOL_TYPES.items():
            assert len(value) > 0, f"TOOL_TYPES['{key}'] has empty display name"
