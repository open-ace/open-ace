"""Unit tests for the User model's dict (de)serialization.

Regression coverage for the Python 3.10 round-trip bug: User.to_dict emits UTC
'Z'-suffixed timestamps via ensure_utc_suffix, and User.from_dict must parse
them back on every supported Python version (3.10's datetime.fromisoformat
rejects a bare 'Z').
"""

from datetime import datetime

from app.models.user import User


class TestUserFromDictTimestamps:
    def test_from_dict_parses_z_suffixed_timestamps(self):
        """A 'Z'-suffixed timestamp from to_dict parses without raising."""
        data = {
            "id": 1,
            "username": "u",
            "email": "u@test.com",
            "role": "user",
            "created_at": "2025-12-01T08:30:00Z",
            "last_login": "2025-12-02T08:30:00Z",
        }
        user = User.from_dict(data)
        assert user.created_at is not None
        assert (user.created_at.year, user.created_at.month, user.created_at.day) == (
            2025,
            12,
            1,
        )
        assert user.last_login is not None

    def test_roundtrip_to_dict_from_dict(self):
        """to_dict -> from_dict survives the ensure_utc_suffix/parse_utc pair."""
        original = User(
            id=100,
            username="roundtrip",
            email="rt@test.com",
            role="user",
            created_at=datetime(2025, 12, 1, 8, 30, 0),
            last_login=datetime(2025, 12, 2, 8, 30, 0),
        )
        restored = User.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.username == original.username
        assert restored.email == original.email
        assert restored.role == original.role
        assert restored.created_at is not None
        assert restored.last_login is not None

    def test_from_dict_none_timestamps(self):
        """Missing/None timestamps stay None."""
        user = User.from_dict({"username": "u", "created_at": None, "last_login": None})
        assert user.created_at is None
        assert user.last_login is None
