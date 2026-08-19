"""Unit tests for ToolAccountConflict model."""

from datetime import datetime, timezone

import pytest

from app.models.tool_account_conflict import (
    BackfillLog,
    MappingMigrationStatus,
    ToolAccountConflict,
)


class TestToolAccountConflict:
    """Test ToolAccountConflict dataclass."""

    def test_create_with_required_fields(self):
        c = ToolAccountConflict(id=1, mapping_id=42, conflict_type="type")
        assert c.id == 1
        assert c.mapping_id == 42
        assert c.conflict_type == "type"
        assert c.expected_value is None
        assert c.actual_value is None
        assert c.detected_at is None
        assert c.resolved_at is None
        assert c.resolved_by is None
        assert c.resolution_action is None
        assert c.details is None

    def test_create_with_all_fields(self):
        detected = datetime(2025, 6, 15, 10, 0, 0)
        resolved = datetime(2025, 6, 16, 10, 0, 0)
        c = ToolAccountConflict(
            id=1,
            mapping_id=42,
            conflict_type="owner",
            expected_value="user1",
            actual_value="user2",
            detected_at=detected,
            resolved_at=resolved,
            resolved_by=5,
            resolution_action="confirmed",
            details='{"key": "value"}',
        )
        assert c.conflict_type == "owner"
        assert c.expected_value == "user1"
        assert c.actual_value == "user2"
        assert c.detected_at == detected
        assert c.resolved_at == resolved
        assert c.resolved_by == 5
        assert c.resolution_action == "confirmed"
        assert c.details == '{"key": "value"}'

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        detected = datetime(2025, 3, 10, 12, 0, 0)
        resolved = datetime(2025, 3, 11, 12, 0, 0)
        c = ToolAccountConflict(
            id=1,
            mapping_id=42,
            conflict_type="tenant",
            detected_at=detected,
            resolved_at=resolved,
        )
        d = c.to_dict()
        assert d["detected_at"] == "2025-03-10T12:00:00Z"
        assert d["resolved_at"] == "2025-03-11T12:00:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        detected = datetime(2025, 1, 1, 12, 0, 0, 123456)
        c = ToolAccountConflict(id=1, mapping_id=42, conflict_type="type", detected_at=detected)
        d = c.to_dict()
        assert d["detected_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        detected = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        c = ToolAccountConflict(id=1, mapping_id=42, conflict_type="type", detected_at=detected)
        d = c.to_dict()
        assert d["detected_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        c = ToolAccountConflict(id=1, mapping_id=42, conflict_type="type")
        d = c.to_dict()
        assert d["detected_at"] is None
        assert d["resolved_at"] is None


class TestBackfillLog:
    """Test BackfillLog dataclass."""

    def test_create_with_required_fields(self):
        b = BackfillLog(id=1, mapping_id=42, backfilled_count=100)
        assert b.id == 1
        assert b.mapping_id == 42
        assert b.backfilled_count == 100
        assert b.first_date is None
        assert b.last_date is None
        assert b.started_at is None
        assert b.completed_at is None
        assert b.status == "completed"

    def test_create_with_all_fields(self):
        started = datetime(2025, 6, 15, 10, 0, 0)
        completed = datetime(2025, 6, 16, 10, 0, 0)
        b = BackfillLog(
            id=1,
            mapping_id=42,
            backfilled_count=500,
            first_date="2025-01-01",
            last_date="2025-06-30",
            started_at=started,
            completed_at=completed,
            status="completed",
        )
        assert b.backfilled_count == 500
        assert b.first_date == "2025-01-01"
        assert b.last_date == "2025-06-30"
        assert b.started_at == started
        assert b.completed_at == completed

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        started = datetime(2025, 3, 10, 12, 0, 0)
        completed = datetime(2025, 3, 11, 12, 0, 0)
        b = BackfillLog(
            id=1,
            mapping_id=42,
            backfilled_count=100,
            started_at=started,
            completed_at=completed,
        )
        d = b.to_dict()
        assert d["started_at"] == "2025-03-10T12:00:00Z"
        assert d["completed_at"] == "2025-03-11T12:00:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        started = datetime(2025, 1, 1, 12, 0, 0, 123456)
        b = BackfillLog(id=1, mapping_id=42, backfilled_count=100, started_at=started)
        d = b.to_dict()
        assert d["started_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        b = BackfillLog(id=1, mapping_id=42, backfilled_count=100, started_at=started)
        d = b.to_dict()
        assert d["started_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        b = BackfillLog(id=1, mapping_id=42, backfilled_count=100)
        d = b.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None


class TestMappingMigrationStatus:
    """Test MappingMigrationStatus dataclass."""

    def test_create_with_required_fields(self):
        m = MappingMigrationStatus(id=1, migration_name="test_migration")
        assert m.id == 1
        assert m.migration_name == "test_migration"
        assert m.status == "pending"
        assert m.last_processed_id is None
        assert m.total_count is None
        assert m.processed_count == 0
        assert m.started_at is None
        assert m.completed_at is None
        assert m.error_message is None

    def test_create_with_all_fields(self):
        started = datetime(2025, 6, 15, 10, 0, 0)
        completed = datetime(2025, 6, 16, 10, 0, 0)
        m = MappingMigrationStatus(
            id=1,
            migration_name="backfill_messages",
            status="completed",
            last_processed_id=1000,
            total_count=5000,
            processed_count=5000,
            started_at=started,
            completed_at=completed,
            error_message=None,
        )
        assert m.status == "completed"
        assert m.last_processed_id == 1000
        assert m.total_count == 5000
        assert m.processed_count == 5000
        assert m.started_at == started
        assert m.completed_at == completed

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        started = datetime(2025, 3, 10, 12, 0, 0)
        completed = datetime(2025, 3, 11, 12, 0, 0)
        m = MappingMigrationStatus(
            id=1,
            migration_name="test_migration",
            started_at=started,
            completed_at=completed,
        )
        d = m.to_dict()
        assert d["started_at"] == "2025-03-10T12:00:00Z"
        assert d["completed_at"] == "2025-03-11T12:00:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        started = datetime(2025, 1, 1, 12, 0, 0, 123456)
        m = MappingMigrationStatus(id=1, migration_name="test", started_at=started)
        d = m.to_dict()
        assert d["started_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        m = MappingMigrationStatus(id=1, migration_name="test", started_at=started)
        d = m.to_dict()
        assert d["started_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        m = MappingMigrationStatus(id=1, migration_name="test")
        d = m.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None

    def test_to_dict_error_message(self):
        m = MappingMigrationStatus(
            id=1,
            migration_name="test",
            status="failed",
            error_message="Connection timeout",
        )
        d = m.to_dict()
        assert d["error_message"] == "Connection timeout"
