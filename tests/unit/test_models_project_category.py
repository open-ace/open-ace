"""Unit tests for ProjectCategory model."""

from datetime import datetime, timezone

import pytest

from app.models.project_category import ProjectCategory


class TestProjectCategory:
    """Test ProjectCategory dataclass."""

    def test_create_with_defaults(self):
        pc = ProjectCategory()
        assert pc.id is None
        assert pc.name == ""
        assert pc.key_patterns == []
        assert pc.sort_order == 0
        assert pc.is_active is True
        assert pc.created_at is None
        assert pc.updated_at is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        later = datetime(2025, 6, 16, 10, 0, 0)
        pc = ProjectCategory(
            id=1,
            name="Production",
            key_patterns=["prod-*", "production-*"],
            sort_order=10,
            is_active=False,
            created_at=now,
            updated_at=later,
        )
        assert pc.id == 1
        assert pc.name == "Production"
        assert pc.key_patterns == ["prod-*", "production-*"]
        assert pc.sort_order == 10
        assert pc.is_active is False
        assert pc.created_at == now
        assert pc.updated_at == later

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        now = datetime(2025, 3, 10, 12, 0, 0)
        later = datetime(2025, 3, 11, 12, 0, 0)
        pc = ProjectCategory(
            id=5,
            name="Test Category",
            key_patterns=["test-*"],
            created_at=now,
            updated_at=later,
        )
        d = pc.to_dict()
        assert d["created_at"] == "2025-03-10T12:00:00Z"
        assert d["updated_at"] == "2025-03-11T12:00:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        now = datetime(2025, 1, 1, 12, 0, 0, 123456)
        pc = ProjectCategory(name="Test", created_at=now)
        d = pc.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        pc = ProjectCategory(name="Test", created_at=now)
        d = pc.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        pc = ProjectCategory(name="Test")
        d = pc.to_dict()
        assert d["created_at"] is None
        assert d["updated_at"] is None

    def test_from_dict_with_z_suffix(self):
        """Test from_dict can parse Z suffix timestamps (Python 3.10 compatibility)."""
        data = {
            "id": 10,
            "name": "Production",
            "key_patterns": ["prod-*"],
            "sort_order": 5,
            "is_active": True,
            "created_at": "2025-07-01T09:00:00Z",
            "updated_at": "2025-07-02T09:00:00Z",
        }
        pc = ProjectCategory.from_dict(data)
        assert pc.id == 10
        assert pc.name == "Production"
        assert pc.key_patterns == ["prod-*"]
        assert pc.sort_order == 5
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        assert pc.created_at.year == 2025
        assert pc.created_at.month == 7
        assert pc.created_at.day == 1
        assert pc.updated_at.year == 2025
        assert pc.updated_at.month == 7
        assert pc.updated_at.day == 2

    def test_from_dict_without_z_suffix(self):
        """Test from_dict can parse timestamps without Z suffix."""
        data = {
            "id": 10,
            "name": "Production",
            "created_at": "2025-07-01T09:00:00",
            "updated_at": "2025-07-02T09:00:00",
        }
        pc = ProjectCategory.from_dict(data)
        assert pc.created_at == datetime(2025, 7, 1, 9, 0, 0)
        assert pc.updated_at == datetime(2025, 7, 2, 9, 0, 0)

    def test_from_dict_key_patterns_as_json_string(self):
        """Test from_dict can parse key_patterns from JSON string."""
        data = {
            "id": 10,
            "name": "Production",
            "key_patterns": '["prod-*", "production-*"]',
        }
        pc = ProjectCategory.from_dict(data)
        assert pc.key_patterns == ["prod-*", "production-*"]

    def test_from_dict_key_patterns_as_list(self):
        """Test from_dict can parse key_patterns from list."""
        data = {
            "id": 10,
            "name": "Production",
            "key_patterns": ["prod-*", "production-*"],
        }
        pc = ProjectCategory.from_dict(data)
        assert pc.key_patterns == ["prod-*", "production-*"]

    def test_from_dict_none_timestamps(self):
        data = {"name": "Test", "created_at": None, "updated_at": None}
        pc = ProjectCategory.from_dict(data)
        assert pc.created_at is None
        assert pc.updated_at is None

    def test_roundtrip_to_dict_from_dict(self):
        """Test to_dict -> from_dict roundtrip preserves data."""
        now = datetime(2025, 10, 1, 8, 0, 0)
        later = datetime(2025, 10, 2, 8, 0, 0)
        original = ProjectCategory(
            id=20,
            name="Test Category",
            key_patterns=["test-*", "demo-*"],
            sort_order=15,
            is_active=True,
            created_at=now,
            updated_at=later,
        )
        d = original.to_dict()
        restored = ProjectCategory.from_dict(d)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.key_patterns == original.key_patterns
        assert restored.sort_order == original.sort_order
        assert restored.is_active == original.is_active
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        # Compare timestamp values instead of objects
        assert restored.created_at.year == original.created_at.year
        assert restored.created_at.month == original.created_at.month
        assert restored.created_at.day == original.created_at.day
        assert restored.updated_at.year == original.updated_at.year
        assert restored.updated_at.month == original.updated_at.month
        assert restored.updated_at.day == original.updated_at.day