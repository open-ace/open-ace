"""Unit tests for Session model."""

from datetime import datetime, timedelta

import pytest

from app.models.session import Session


class TestSession:
    """Test Session dataclass."""

    def test_create_with_defaults(self):
        s = Session()
        assert s.id is None
        assert s.user_id is None
        assert s.username == ""
        assert s.email is None
        assert s.role == "user"
        assert s.token == ""
        assert s.created_at is None
        assert s.expires_at is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        exp = datetime(2025, 6, 16, 10, 0, 0)
        s = Session(
            id=1,
            user_id=42,
            username="admin",
            email="admin@example.com",
            role="admin",
            token="abc123token",
            created_at=now,
            expires_at=exp,
        )
        assert s.id == 1
        assert s.user_id == 42
        assert s.username == "admin"
        assert s.email == "admin@example.com"
        assert s.role == "admin"
        assert s.token == "abc123token"
        assert s.created_at == now
        assert s.expires_at == exp

    def test_is_expired_with_future_expiry(self):
        future = datetime.utcnow() + timedelta(hours=1)
        s = Session(expires_at=future)
        assert s.is_expired() is False

    def test_is_expired_with_past_expiry(self):
        past = datetime.utcnow() - timedelta(hours=1)
        s = Session(expires_at=past)
        assert s.is_expired() is True

    def test_is_expired_with_none_expiry(self):
        s = Session(expires_at=None)
        assert s.is_expired() is False

    def test_is_admin_true(self):
        s = Session(role="admin")
        assert s.is_admin() is True

    def test_is_admin_false_user(self):
        s = Session(role="user")
        assert s.is_admin() is False

    def test_is_admin_false_other_role(self):
        s = Session(role="moderator")
        assert s.is_admin() is False

    def test_is_admin_default_role(self):
        s = Session()
        assert s.is_admin() is False

    def test_to_dict(self):
        now = datetime(2025, 3, 10, 12, 0, 0)
        exp = datetime(2025, 3, 11, 12, 0, 0)
        s = Session(
            id=5,
            user_id=10,
            username="testuser",
            email="test@test.com",
            role="user",
            token="tok-xyz",
            created_at=now,
            expires_at=exp,
        )
        d = s.to_dict()
        assert d["id"] == 5
        assert d["user_id"] == 10
        assert d["username"] == "testuser"
        assert d["email"] == "test@test.com"
        assert d["role"] == "user"
        assert d["token"] == "tok-xyz"
        assert d["created_at"] == "2025-03-10T12:00:00"
        assert d["expires_at"] == "2025-03-11T12:00:00"

    def test_to_dict_none_timestamps(self):
        s = Session(username="bob")
        d = s.to_dict()
        assert d["created_at"] is None
        assert d["expires_at"] is None

    def test_from_dict(self):
        data = {
            "id": 20,
            "user_id": 30,
            "username": "alice",
            "email": "alice@example.com",
            "role": "admin",
            "token": "token-alice",
            "created_at": "2025-07-01T09:00:00",
            "expires_at": "2025-07-02T09:00:00",
        }
        s = Session.from_dict(data)
        assert s.id == 20
        assert s.user_id == 30
        assert s.username == "alice"
        assert s.email == "alice@example.com"
        assert s.role == "admin"
        assert s.token == "token-alice"
        assert s.created_at == datetime(2025, 7, 1, 9, 0, 0)
        assert s.expires_at == datetime(2025, 7, 2, 9, 0, 0)

    def test_from_dict_defaults(self):
        data = {}
        s = Session.from_dict(data)
        assert s.id is None
        assert s.user_id is None
        assert s.username == ""
        assert s.email is None
        assert s.role == "user"
        assert s.token == ""
        assert s.created_at is None
        assert s.expires_at is None

    def test_from_dict_none_timestamps(self):
        data = {"created_at": None, "expires_at": None}
        s = Session.from_dict(data)
        assert s.created_at is None
        assert s.expires_at is None

    def test_roundtrip_to_dict_from_dict(self):
        now = datetime(2025, 12, 1, 8, 30, 0)
        exp = datetime(2025, 12, 2, 8, 30, 0)
        original = Session(
            id=100,
            user_id=200,
            username="roundtrip",
            email="rt@test.com",
            role="user",
            token="rt-token",
            created_at=now,
            expires_at=exp,
        )
        d = original.to_dict()
        restored = Session.from_dict(d)
        assert restored.id == original.id
        assert restored.user_id == original.user_id
        assert restored.username == original.username
        assert restored.email == original.email
        assert restored.role == original.role
        assert restored.token == original.token
