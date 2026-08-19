"""Unit tests for User model."""

from datetime import datetime, timezone

import pytest

from app.models.user import Permission, User, UserQuota


class TestPermission:
    """Test Permission dataclass."""

    def test_create(self):
        p = Permission(resource="projects", action="read")
        assert p.resource == "projects"
        assert p.action == "read"


class TestUser:
    """Test User dataclass."""

    def test_create_with_defaults(self):
        u = User()
        assert u.id is None
        assert u.username == ""
        assert u.email == ""
        assert u.password_hash == ""
        assert u.role == "user"
        assert u.is_active is True
        assert u.created_at is None
        assert u.last_login is None
        assert u.permissions == []
        assert u.tenant_id is None
        assert u.daily_token_quota is None
        assert u.monthly_token_quota is None
        assert u.daily_request_quota is None
        assert u.monthly_request_quota is None
        assert u.must_change_password is False
        assert u.avatar_url is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        last = datetime(2025, 6, 16, 10, 0, 0)
        u = User(
            id=1,
            username="admin",
            email="admin@example.com",
            password_hash="hashed",
            role="admin",
            is_active=False,
            created_at=now,
            last_login=last,
            permissions=[Permission("projects", "admin")],
            tenant_id=5,
            daily_token_quota=10000,
            monthly_token_quota=100000,
            daily_request_quota=100,
            monthly_request_quota=1000,
            must_change_password=True,
            avatar_url="https://example.com/avatar.png",
        )
        assert u.id == 1
        assert u.username == "admin"
        assert u.email == "admin@example.com"
        assert u.password_hash == "hashed"
        assert u.role == "admin"
        assert u.is_active is False
        assert u.created_at == now
        assert u.last_login == last
        assert len(u.permissions) == 1
        assert u.tenant_id == 5
        assert u.daily_token_quota == 10000
        assert u.must_change_password is True
        assert u.avatar_url == "https://example.com/avatar.png"

    def test_to_dict_datetime_fields_have_z_suffix(self):
        """Test that datetime fields in to_dict() have UTC 'Z' suffix (Issue #2765)."""
        now = datetime(2025, 3, 10, 12, 0, 0)
        last = datetime(2025, 3, 11, 12, 0, 0)
        u = User(
            id=5,
            username="testuser",
            email="test@test.com",
            role="user",
            created_at=now,
            last_login=last,
        )
        d = u.to_dict()
        assert d["created_at"] == "2025-03-10T12:00:00Z"
        assert d["last_login"] == "2025-03-11T12:00:00Z"

    def test_to_dict_datetime_with_microseconds(self):
        """Test datetime with microseconds gets Z suffix."""
        now = datetime(2025, 1, 1, 12, 0, 0, 123456)
        u = User(created_at=now)
        d = u.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00.123456Z"

    def test_to_dict_datetime_with_utc_timezone(self):
        """Test datetime with UTC timezone preserves +00:00."""
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        u = User(created_at=now)
        d = u.to_dict()
        assert d["created_at"] == "2025-01-01T12:00:00+00:00"

    def test_to_dict_none_timestamps(self):
        u = User(username="bob")
        d = u.to_dict()
        assert d["created_at"] is None
        assert d["last_login"] is None

    def test_from_dict_with_z_suffix(self):
        """Test from_dict can parse Z suffix timestamps (Python 3.10 compatibility)."""
        data = {
            "id": 20,
            "username": "alice",
            "email": "alice@example.com",
            "role": "admin",
            "created_at": "2025-07-01T09:00:00Z",
            "last_login": "2025-07-02T09:00:00Z",
        }
        u = User.from_dict(data)
        assert u.id == 20
        assert u.username == "alice"
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        assert u.created_at.year == 2025
        assert u.created_at.month == 7
        assert u.created_at.day == 1
        assert u.created_at.hour == 9
        assert u.created_at.minute == 0
        assert u.last_login.year == 2025
        assert u.last_login.month == 7
        assert u.last_login.day == 2

    def test_from_dict_without_z_suffix(self):
        """Test from_dict can parse timestamps without Z suffix."""
        data = {
            "id": 20,
            "username": "alice",
            "created_at": "2025-07-01T09:00:00",
            "last_login": "2025-07-02T09:00:00",
        }
        u = User.from_dict(data)
        assert u.created_at == datetime(2025, 7, 1, 9, 0, 0)
        assert u.last_login == datetime(2025, 7, 2, 9, 0, 0)

    def test_from_dict_none_timestamps(self):
        data = {"created_at": None, "last_login": None}
        u = User.from_dict(data)
        assert u.created_at is None
        assert u.last_login is None

    def test_roundtrip_to_dict_from_dict(self):
        """Test to_dict -> from_dict roundtrip preserves data."""
        now = datetime(2025, 12, 1, 8, 30, 0)
        last = datetime(2025, 12, 2, 8, 30, 0)
        original = User(
            id=100,
            username="roundtrip",
            email="rt@test.com",
            role="admin",
            created_at=now,
            last_login=last,
            tenant_id=5,
            daily_token_quota=10000,
        )
        d = original.to_dict()
        restored = User.from_dict(d)
        assert restored.id == original.id
        assert restored.username == original.username
        assert restored.email == original.email
        assert restored.role == original.role
        # parse_db_datetime returns timezone-aware datetime for Z suffix
        # Compare timestamp values instead of objects
        assert restored.created_at.year == original.created_at.year
        assert restored.created_at.month == original.created_at.month
        assert restored.created_at.day == original.created_at.day
        assert restored.created_at.hour == original.created_at.hour
        assert restored.created_at.minute == original.created_at.minute
        assert restored.last_login.year == original.last_login.year
        assert restored.last_login.month == original.last_login.month
        assert restored.last_login.day == original.last_login.day
        assert restored.tenant_id == original.tenant_id
        assert restored.daily_token_quota == original.daily_token_quota

    def test_is_admin_true(self):
        u = User(role="admin")
        assert u.is_admin() is True

    def test_is_admin_false_user(self):
        u = User(role="user")
        assert u.is_admin() is False


class TestUserQuota:
    """Test UserQuota dataclass."""

    def test_create_with_defaults(self):
        uq = UserQuota(user_id=1, date="2025-01-01")
        assert uq.user_id == 1
        assert uq.date == "2025-01-01"
        assert uq.tokens_used == 0
        assert uq.requests_made == 0

    def test_is_over_daily_token_quota_false(self):
        uq = UserQuota(user_id=1, date="2025-01-01", daily_token_quota=1000)
        uq.tokens_used = 500
        assert uq.is_over_daily_token_quota() is False

    def test_is_over_daily_token_quota_true(self):
        uq = UserQuota(user_id=1, date="2025-01-01", daily_token_quota=1000)
        uq.tokens_used = 1500
        assert uq.is_over_daily_token_quota() is True

    def test_is_over_daily_token_quota_no_limit(self):
        uq = UserQuota(user_id=1, date="2025-01-01", daily_token_quota=None)
        uq.tokens_used = 1500
        assert uq.is_over_daily_token_quota() is False