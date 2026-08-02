"""Unit tests for schema_guard module.

Issue: #2190
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from app.repositories.schema_guard import (
    MIN_SUPPORTED_REVISION,
    SchemaCompatibilityError,
    check_schema_compatibility,
    get_database_revision,
    get_environment_mode,
    is_production_environment,
)


class TestGetDatabaseRevision:
    """Tests for get_database_revision function."""

    def test_fresh_database_no_alembic_table(self):
        """Test fresh database without alembic_version table."""
        # Create in-memory SQLite database
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            result = get_database_revision(conn)
            assert result is None

    def test_database_with_revision(self):
        """Test database with alembic_version table and revision."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create alembic_version table
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('test_revision')"))
            conn.commit()

            result = get_database_revision(conn)
            assert result == "test_revision"

    def test_database_empty_alembic_table(self):
        """Test database with alembic_version table but no rows."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create empty alembic_version table
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.commit()

            result = get_database_revision(conn)
            assert result is None


class TestCheckSchemaCompatibility:
    """Tests for check_schema_compatibility function."""

    def test_fresh_database_allowed(self):
        """Test that fresh database (no alembic_version) is allowed."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Should not raise
            check_schema_compatibility(conn)

    def test_compatible_version_passes(self):
        """Test that compatible version passes check."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create alembic_version with newer revision
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(
                sa.text(f"INSERT INTO alembic_version (version_num) VALUES ('{MIN_SUPPORTED_REVISION}')")
            )
            conn.commit()

            # Should not raise
            check_schema_compatibility(conn)

    def test_normal_version_after_baseline_passes(self):
        """Test that a normal timestamp version after baseline passes check."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('20260802_001')"))
            conn.commit()

            # Should not raise - this is a valid version after baseline
            check_schema_compatibility(conn)

    def test_older_timestamp_version_with_explicit_min(self):
        """Test that older timestamp version fails with explicit min_revision."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('20260701_001')"))
            conn.commit()

            # Should raise when checking against a newer timestamp revision
            with pytest.raises(SchemaCompatibilityError) as exc_info:
                check_schema_compatibility(conn, min_revision="20260801_001")

            assert exc_info.value.current_revision == "20260701_001"
            assert exc_info.value.min_revision == "20260801_001"

    def test_incompatible_version_raises(self):
        """Test that incompatible version raises error."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create alembic_version with old revision
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('old_revision')"))
            conn.commit()

            with pytest.raises(SchemaCompatibilityError) as exc_info:
                check_schema_compatibility(conn)

            assert exc_info.value.current_revision == "old_revision"
            assert exc_info.value.min_revision == MIN_SUPPORTED_REVISION

    def test_skip_check_bypass(self):
        """Test that skip_check bypasses validation."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create incompatible version
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('old_revision')"))
            conn.commit()

            # Should not raise with skip_check=True
            check_schema_compatibility(conn, skip_check=True)

    def test_environment_variable_bypass(self):
        """Test that OPENACE_SKIP_SCHEMA_CHECK bypasses validation."""
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            # Create incompatible version
            conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES ('old_revision')"))
            conn.commit()

            # Set environment variable
            with patch.dict(os.environ, {"OPENACE_SKIP_SCHEMA_CHECK": "true"}):
                # Should not raise
                check_schema_compatibility(conn)


class TestGetEnvironmentMode:
    """Tests for get_environment_mode function."""

    def test_explicit_production_mode(self):
        """Test explicit production mode setting."""
        with patch.dict(os.environ, {"OPENACE_PRODUCTION_MODE": "1"}, clear=False):
            result = get_environment_mode()
            assert result == "production"

    def test_flask_production_mode(self):
        """Test Flask production mode inference."""
        # Clear other env vars
        env = {
            "FLASK_ENV": "production",
        }
        with patch.dict(os.environ, env, clear=False):
            # Mock is_postgresql to return False (SQLite) so Flask_ENV takes effect
            with patch("app.repositories.database.is_postgresql", return_value=False):
                result = get_environment_mode()
                assert result == "production"

    def test_default_development_mode(self):
        """Test default development mode."""
        # Clear all production-related env vars
        env = {}
        with patch.dict(os.environ, env, clear=False):
            # Mock is_postgresql to return False (SQLite)
            with patch("app.repositories.database.is_postgresql", return_value=False):
                result = get_environment_mode()
                assert result == "development"


class TestIsProductionEnvironment:
    """Tests for is_production_environment function."""

    def test_production_returns_true(self):
        """Test production mode returns True."""
        with patch("app.repositories.schema_guard.get_environment_mode", return_value="production"):
            assert is_production_environment() is True

    def test_development_returns_false(self):
        """Test development mode returns False."""
        with patch("app.repositories.schema_guard.get_environment_mode", return_value="development"):
            assert is_production_environment() is False


class TestSchemaCompatibilityError:
    """Tests for SchemaCompatibilityError exception."""

    def test_error_message(self):
        """Test error message construction."""
        error = SchemaCompatibilityError(
            "Test error",
            current_revision="old_rev",
            min_revision="min_rev",
        )
        assert str(error) == "Test error"
        assert error.current_revision == "old_rev"
        assert error.min_revision == "min_rev"

    def test_error_without_revisions(self):
        """Test error without revision details."""
        error = SchemaCompatibilityError("Test error")
        assert str(error) == "Test error"
        assert error.current_revision is None
        assert error.min_revision == MIN_SUPPORTED_REVISION