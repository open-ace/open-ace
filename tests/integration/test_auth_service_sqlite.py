"""Integration tests for auth_service against real SQLite database.

Tests DDL creation, login lockout recording, and clearing of failed logins.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services import auth_service
from app.services.auth_service import (
    _check_login_lockout,
    _clear_failed_logins,
    _record_failed_login,
    get_ddl_statements,
)


class TestDDL:
    """Tests for DDL statement creation."""

    def test_ddl_statements_create_tables(self, tmp_db):
        """Verify DDL statements can create login_attempts table."""
        ddl = get_ddl_statements()
        assert len(ddl) >= 1

        # Execute DDL
        conn = tmp_db.get_connection()
        try:
            cursor = conn.cursor()
            for sql in ddl:
                cursor.execute(sql)
            conn.commit()
        finally:
            conn.close()

        # Verify table exists
        assert tmp_db.table_exists("login_attempts") is True

    def test_ddl_idempotent(self, tmp_db):
        """DDL can be run multiple times without error."""
        ddl = get_ddl_statements()

        conn = tmp_db.get_connection()
        try:
            cursor = conn.cursor()
            for sql in ddl:
                cursor.execute(sql)
            conn.commit()
            # Run again
            for sql in ddl:
                cursor.execute(sql)
            conn.commit()
        finally:
            conn.close()

        assert tmp_db.table_exists("login_attempts") is True


class TestLoginLockout:
    """Tests for login lockout via login_attempts table."""

    def _setup_login_attempts(self, tmp_db):
        """Create the login_attempts table for testing."""
        conn = tmp_db.get_connection()
        try:
            cursor = conn.cursor()
            for sql in get_ddl_statements():
                cursor.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def _patch_db(self, tmp_db):
        """Patch Database() to use tmp_db's SQLite path.

        Auth service creates Database() with no args, which calls
        get_database_url() and may get a PostgreSQL URL.  Patch
        get_database_url to return the tmp SQLite URL.
        """
        import app.repositories.database as db_mod

        return patch.object(db_mod, "get_database_url", return_value=tmp_db.db_url)

    def test_no_lockout_initially(self, tmp_db):
        """New username has no lockout."""
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        with self._patch_db(tmp_db):
            is_locked, msg = _check_login_lockout("newuser")
            assert is_locked is False
            assert msg is None

    def test_record_failed_login_increments(self, tmp_db):
        """Recording failed login creates/increments attempt_count."""
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        with self._patch_db(tmp_db):
            _record_failed_login("testuser")

            row = tmp_db.fetch_one("SELECT * FROM login_attempts WHERE username = ?", ("testuser",))
            assert row is not None
            assert row["attempt_count"] == 1

            _record_failed_login("testuser")
            row = tmp_db.fetch_one("SELECT * FROM login_attempts WHERE username = ?", ("testuser",))
            assert row["attempt_count"] == 2

    def test_lockout_after_max_attempts(self, tmp_db):
        """Account is locked after max_login_attempts failed attempts.

        The threshold comes from security_settings which may be backed by
        a file (~/.open-ace/governance_settings.json).  We read the
        effective threshold to make the test environment-agnostic.
        """
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        with self._patch_db(tmp_db):
            # Read the effective max_login_attempts from settings
            max_attempts = auth_service._get_max_login_attempts()
            assert max_attempts > 0

            # Record max_attempts failed logins
            for _ in range(max_attempts):
                _record_failed_login("lockeduser")

            # Check lockout
            is_locked, msg = _check_login_lockout("lockeduser")
            assert is_locked is True
            assert "temporarily locked" in msg.lower()

    def test_no_lockout_before_threshold(self, tmp_db):
        """Account is NOT locked before reaching max attempts."""
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        with self._patch_db(tmp_db):
            # Record only 1 attempt (always below any reasonable threshold)
            _record_failed_login("notlocked")

            is_locked, msg = _check_login_lockout("notlocked")
            assert is_locked is False
            assert msg is None

    def test_clear_failed_logins(self, tmp_db):
        """Clearing failed logins removes the record."""
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        with self._patch_db(tmp_db):
            _record_failed_login("cleareduser")
            _record_failed_login("cleareduser")

            # Verify record exists
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", ("cleareduser",)
            )
            assert row is not None

            # Clear
            _clear_failed_logins("cleareduser")

            # Record should be gone
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", ("cleareduser",)
            )
            assert row is None

    def test_expired_lockout_allows_login(self, tmp_db):
        """Expired lockout is auto-cleaned and allows login."""
        self._setup_login_attempts(tmp_db)
        auth_service._security_settings_cache = {}

        # Manually insert an expired lockout
        expired_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
        tmp_db.execute(
            "INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES (?, ?, ?)",
            ("expireduser", 5, expired_time.isoformat()),
        )

        with self._patch_db(tmp_db):
            is_locked, msg = _check_login_lockout("expireduser")
            assert is_locked is False

            # Record should have been cleaned up
            row = tmp_db.fetch_one(
                "SELECT * FROM login_attempts WHERE username = ?", ("expireduser",)
            )
            assert row is None
