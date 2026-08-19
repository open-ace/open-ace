"""Unit tests for ToolAccountMappingRule model."""

from datetime import datetime, timezone

import pytest

from app.models.tool_account_mapping_rule import MatchType, ToolAccountMappingRule


class TestMatchType:
    """Test MatchType enum."""

    def test_exact_value(self):
        assert MatchType.EXACT.value == "exact"

    def test_prefix_value(self):
        assert MatchType.PREFIX.value == "prefix"

    def test_suffix_value(self):
        assert MatchType.SUFFIX.value == "suffix"

    def test_contains_value(self):
        assert MatchType.CONTAINS.value == "contains"

    def test_regex_value(self):
        assert MatchType.REGEX.value == "regex"


class TestToolAccountMappingRule:
    """Test ToolAccountMappingRule dataclass."""

    def test_create_with_required_fields(self):
        r = ToolAccountMappingRule(id=1, user_id=42, pattern="alice-*")
        assert r.id == 1
        assert r.user_id == 42
        assert r.pattern == "alice-*"
        assert r.match_type == "exact"
        assert r.tool_type is None
        assert r.priority == 0
        assert r.is_auto is True
        assert r.is_active is True
        assert r.description is None
        assert r.created_at is None
        assert r.updated_at is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        later = datetime(2025, 6, 16, 10, 0, 0)
        r = ToolAccountMappingRule(
            id=1,
            user_id=42,
            pattern="alice-*",
            match_type="prefix",
            tool_type="qwen",
            priority=10,
            is_auto=False,
            is_active=False,
            description="Test rule",
            created_at=now,
            updated_at=later,
        )
        assert r.match_type == "prefix"
        assert r.tool_type == "qwen"
        assert r.priority == 10
        assert r.is_auto is False
        assert r.is_active is False
        assert r.description == "Test rule"
        assert r.created_at == now
        assert r.updated_at == later

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        now = datetime(2025, 3, 10, 14, 30, 0)
        later = datetime(2025, 3, 11, 14, 30, 0)
        r = ToolAccountMappingRule(
            id=7,
            user_id=42,
            pattern="test-*",
            created_at=now,
            updated_at=later,
        )
        d = r.to_dict()
        assert d["created_at"] == "2025-03-10T14:30:00Z"
        assert d["updated_at"] == "2025-03-11T14:30:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        now = datetime(2025, 1, 1, 12, 0, 0, 123456)
        r = ToolAccountMappingRule(id=1, user_id=1, pattern="test", created_at=now)
        d = r.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        r = ToolAccountMappingRule(id=1, user_id=1, pattern="test", created_at=now)
        d = r.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        r = ToolAccountMappingRule(id=1, user_id=42, pattern="test")
        d = r.to_dict()
        assert d["created_at"] is None
        assert d["updated_at"] is None

    def test_from_dict_with_z_suffix(self):
        """Test from_dict can parse Z suffix timestamps (Python 3.10 compatibility)."""
        data = {
            "id": 15,
            "user_id": 42,
            "pattern": "test-*",
            "match_type": "prefix",
            "created_at": "2025-07-20T09:00:00Z",
            "updated_at": "2025-07-21T09:00:00Z",
        }
        r = ToolAccountMappingRule.from_dict(data)
        assert r.id == 15
        assert r.user_id == 42
        assert r.pattern == "test-*"
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        assert r.created_at.year == 2025
        assert r.created_at.month == 7
        assert r.created_at.day == 20
        assert r.updated_at.year == 2025
        assert r.updated_at.month == 7
        assert r.updated_at.day == 21

    def test_from_dict_without_z_suffix(self):
        """Test from_dict can parse timestamps without Z suffix."""
        data = {
            "id": 15,
            "user_id": 42,
            "pattern": "test-*",
            "created_at": "2025-07-20T09:00:00",
            "updated_at": "2025-07-21T09:00:00",
        }
        r = ToolAccountMappingRule.from_dict(data)
        assert r.created_at == datetime(2025, 7, 20, 9, 0, 0)
        assert r.updated_at == datetime(2025, 7, 21, 9, 0, 0)

    def test_from_dict_none_timestamps(self):
        data = {"id": 1, "user_id": 42, "pattern": "test", "created_at": None, "updated_at": None}
        r = ToolAccountMappingRule.from_dict(data)
        assert r.created_at is None
        assert r.updated_at is None

    def test_roundtrip_to_dict_from_dict(self):
        """Test to_dict -> from_dict roundtrip preserves data."""
        now = datetime(2025, 9, 1, 12, 0, 0)
        later = datetime(2025, 9, 2, 12, 0, 0)
        original = ToolAccountMappingRule(
            id=99,
            user_id=42,
            pattern="test-*",
            match_type="prefix",
            tool_type="qwen",
            priority=5,
            is_auto=True,
            is_active=True,
            description="Test rule",
            created_at=now,
            updated_at=later,
        )
        d = original.to_dict()
        restored = ToolAccountMappingRule.from_dict(d)
        assert restored.id == original.id
        assert restored.user_id == original.user_id
        assert restored.pattern == original.pattern
        assert restored.match_type == original.match_type
        assert restored.tool_type == original.tool_type
        assert restored.priority == original.priority
        assert restored.is_auto == original.is_auto
        assert restored.is_active == original.is_active
        assert restored.description == original.description
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        # Compare timestamp values instead of objects
        assert restored.created_at.year == original.created_at.year
        assert restored.created_at.month == original.created_at.month
        assert restored.created_at.day == original.created_at.day
        assert restored.updated_at.year == original.updated_at.year
        assert restored.updated_at.month == original.updated_at.month
        assert restored.updated_at.day == original.updated_at.day

    def test_matches_exact(self):
        r = ToolAccountMappingRule(id=1, user_id=42, pattern="alice", match_type="exact")
        assert r.matches("alice") is True
        assert r.matches("alice2") is False
        assert r.matches("bob") is False

    def test_matches_prefix(self):
        r = ToolAccountMappingRule(id=1, user_id=42, pattern="alice-*", match_type="prefix")
        assert r.matches("alice-test") is True
        assert r.matches("alice-123") is True
        assert r.matches("bob") is False

    def test_matches_inactive_rule(self):
        r = ToolAccountMappingRule(
            id=1, user_id=42, pattern="alice", match_type="exact", is_active=False
        )
        assert r.matches("alice") is False
