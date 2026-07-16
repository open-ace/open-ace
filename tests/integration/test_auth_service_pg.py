"""Integration tests for auth_service against real PostgreSQL database."""

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
# CI runs `pytest -m 'not postgres'` so these are excluded; locally they
# auto-skip via the pg_db fixture when no server is reachable.
pytestmark = pytest.mark.postgres

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import auth_service
from app.services.auth_service import (
    _check_login_lockout,
    _clear_failed_logins,
    _record_failed_login,
)


class TestLoginLockout:
    """Tests for login lockout via PostgreSQL."""

    # login_attempts table is created by pg_db fixture via load_schema_from_file()

    def _patch_db(self, pg_db):
        import app.repositories.database as db_mod

        return patch.object(db_mod, "get_database_url", return_value=pg_db.db_url)

    def test_no_lockout_initially(self, pg_db):
        auth_service._security_settings_cache.clear()

        with self._patch_db(pg_db):
            is_locked, msg = _check_login_lockout("newuser")
            assert is_locked is False
            assert msg is None

    def test_record_failed_login_increments(self, pg_db):
        auth_service._security_settings_cache.clear()

        with self._patch_db(pg_db):
            _record_failed_login("testuser")
            row = pg_db.fetch_one("SELECT * FROM login_attempts WHERE username = %s", ("testuser",))
            assert row is not None
            assert row["attempt_count"] == 1

            _record_failed_login("testuser")
            row = pg_db.fetch_one("SELECT * FROM login_attempts WHERE username = %s", ("testuser",))
            assert row["attempt_count"] == 2

    def test_lockout_after_max_attempts(self, pg_db):
        auth_service._security_settings_cache.clear()

        with self._patch_db(pg_db):
            max_attempts = auth_service._get_max_login_attempts()
            assert max_attempts > 0

            for _ in range(max_attempts):
                _record_failed_login("lockeduser")

            is_locked, msg = _check_login_lockout("lockeduser")
            assert is_locked is True
            assert "temporarily locked" in msg.lower()

    def test_clear_failed_logins(self, pg_db):
        auth_service._security_settings_cache.clear()

        with self._patch_db(pg_db):
            _record_failed_login("cleareduser")
            _record_failed_login("cleareduser")

            _clear_failed_logins("cleareduser")

            row = pg_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = %s", ("cleareduser",)
            )
            assert row is None
