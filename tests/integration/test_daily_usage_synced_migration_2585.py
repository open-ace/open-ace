#!/usr/bin/env python3
"""
Test for migration: Add daily_usage_synced field to agent_sessions table.

Issue: #2585
Migration: migrations/versions/20260820_003_add_daily_usage_synced.py

This test verifies the migration's upgrade and downgrade paths,
ensuring that the security/data-loss path (downgrade) works correctly.
"""

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

pytestmark = [pytest.mark.integration, pytest.mark.issue(2585)]


def run_migration_upgrade(conn, migration_module):
    """Run migration upgrade in proper Alembic context."""
    # Create migration context
    context = MigrationContext.configure(conn)

    # Create operations object
    operations = Operations(context)

    # Monkey-patch op module to use our operations
    import alembic.op as op_module

    # Store original functions
    original_add_column = op_module.add_column
    original_drop_column = op_module.drop_column
    original_create_index = op_module.create_index
    original_drop_index = op_module.drop_index
    original_batch_alter_table = op_module.batch_alter_table
    original_get_bind = op_module.get_bind

    # Replace with our operations
    op_module.add_column = operations.add_column
    op_module.drop_column = operations.drop_column
    op_module.create_index = operations.create_index
    op_module.drop_index = operations.drop_index
    op_module.batch_alter_table = operations.batch_alter_table
    op_module.get_bind = lambda: conn

    try:
        migration_module.upgrade()
    finally:
        # Restore original functions
        op_module.add_column = original_add_column
        op_module.drop_column = original_drop_column
        op_module.create_index = original_create_index
        op_module.drop_index = original_drop_index
        op_module.batch_alter_table = original_batch_alter_table
        op_module.get_bind = original_get_bind


def run_migration_downgrade(conn, migration_module):
    """Run migration downgrade in proper Alembic context."""
    # Create migration context
    context = MigrationContext.configure(conn)

    # Create operations object
    operations = Operations(context)

    # Monkey-patch op module to use our operations
    import alembic.op as op_module

    # Store original functions
    original_add_column = op_module.add_column
    original_drop_column = op_module.drop_column
    original_create_index = op_module.create_index
    original_drop_index = op_module.drop_index
    original_batch_alter_table = op_module.batch_alter_table
    original_get_bind = op_module.get_bind

    # Replace with our operations
    op_module.add_column = operations.add_column
    op_module.drop_column = operations.drop_column
    op_module.create_index = operations.create_index
    op_module.drop_index = operations.drop_index
    op_module.batch_alter_table = operations.batch_alter_table
    op_module.get_bind = lambda: conn

    try:
        migration_module.downgrade()
    finally:
        # Restore original functions
        op_module.add_column = original_add_column
        op_module.drop_column = original_drop_column
        op_module.create_index = original_create_index
        op_module.drop_index = original_drop_index
        op_module.batch_alter_table = original_batch_alter_table
        op_module.get_bind = original_get_bind


class TestDailyUsageSyncedMigration:
    """Tests for daily_usage_synced migration to agent_sessions table."""

    @pytest.fixture
    def db_engine(self):
        """Create an in-memory SQLite database for testing."""
        engine = create_engine("sqlite:///:memory:")

        # Create minimal agent_sessions table schema (pre-migration state)
        with engine.connect() as conn:
            conn.execute(sa.text("""
                CREATE TABLE agent_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    cli_tool TEXT,
                    tenant_id INTEGER,
                    request_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
            """))
            conn.commit()

        yield engine
        engine.dispose()

    def test_upgrade_adds_daily_usage_synced_column(self, db_engine):
        """Test that upgrade adds daily_usage_synced column with correct defaults."""
        import importlib

        # Import migration module
        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        # Run upgrade
        with db_engine.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify column was added
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("agent_sessions")]

        assert "daily_usage_synced" in columns, "daily_usage_synced column should be added"

        # Verify column properties
        col_info = None
        for col in inspector.get_columns("agent_sessions"):
            if col["name"] == "daily_usage_synced":
                col_info = col
                break

        assert col_info is not None
        assert col_info["nullable"] is False, "Column should be NOT NULL"
        assert col_info["default"] is not None, "Column should have server default"

        # Verify partial index was created
        indexes = [idx["name"] for idx in inspector.get_indexes("agent_sessions")]
        assert "idx_agent_sessions_daily_usage_synced" in indexes, "Partial index should be created"

    def test_downgrade_removes_daily_usage_synced_column(self, db_engine):
        """Test that downgrade removes daily_usage_synced column and index.

        This is the security/data-loss path test - verifying that downgrade
        correctly removes the added column and index.
        """
        import importlib

        # Import migration module
        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        # Run upgrade first
        with db_engine.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify column exists after upgrade
        inspector = sa.inspect(db_engine)
        columns_before = [col["name"] for col in inspector.get_columns("agent_sessions")]
        assert "daily_usage_synced" in columns_before

        # Run downgrade
        with db_engine.connect() as conn:
            run_migration_downgrade(conn, migration_module)
            conn.commit()

        # Verify column was removed (security/data-loss verification)
        inspector = sa.inspect(db_engine)
        columns_after = [col["name"] for col in inspector.get_columns("agent_sessions")]

        assert (
            "daily_usage_synced" not in columns_after
        ), "daily_usage_synced should be removed on downgrade"

        # Verify index was removed
        indexes = [idx["name"] for idx in inspector.get_indexes("agent_sessions")]
        assert (
            "idx_agent_sessions_daily_usage_synced" not in indexes
        ), "Index should be removed on downgrade"

    def test_migration_is_idempotent(self, db_engine):
        """Test that running upgrade twice doesn't fail."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        with db_engine.connect() as conn:
            # Run upgrade twice (should not fail due to column_exists check)
            run_migration_upgrade(conn, migration_module)
            conn.commit()

            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify column still exists
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("agent_sessions")]
        assert "daily_usage_synced" in columns

    def test_downgrade_is_idempotent(self, db_engine):
        """Test that running downgrade twice doesn't fail."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        with db_engine.connect() as conn:
            # Upgrade first
            run_migration_upgrade(conn, migration_module)
            conn.commit()

            # Downgrade twice (should not fail)
            run_migration_downgrade(conn, migration_module)
            conn.commit()

            run_migration_downgrade(conn, migration_module)
            conn.commit()

        # Verify column is gone
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("agent_sessions")]
        assert "daily_usage_synced" not in columns


class TestDailyUsageSyncedDefault:
    """Test that default value is FALSE for existing rows."""

    @pytest.fixture
    def db_engine_with_data(self):
        """Create database with existing session data."""
        engine = create_engine("sqlite:///:memory:")

        with engine.connect() as conn:
            conn.execute(sa.text("""
                CREATE TABLE agent_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    cli_tool TEXT,
                    tenant_id INTEGER,
                    request_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
            """))
            # Insert test data
            conn.execute(
                sa.text(
                    "INSERT INTO agent_sessions (session_id, cli_tool, tenant_id, request_count) "
                    "VALUES ('sess-1', 'qwen-code', 1, 5)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO agent_sessions (session_id, cli_tool, tenant_id, request_count) "
                    "VALUES ('sess-2', 'qwen-code', 2, 10)"
                )
            )
            conn.commit()

        yield engine
        engine.dispose()

    def test_default_value_is_false(self, db_engine_with_data):
        """Test that existing rows get daily_usage_synced=FALSE by default."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        with db_engine_with_data.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify default value is FALSE for existing rows
        with db_engine_with_data.connect() as conn:
            result = conn.execute(
                sa.text("SELECT daily_usage_synced FROM agent_sessions WHERE session_id = 'sess-1'")
            )
            row = result.fetchone()
            # SQLite returns 0 for FALSE, PostgreSQL returns False
            # Both are falsy, so we check for falsy value
            assert row[0] in (0, False), "Default value should be FALSE (0 or False)"

    def test_existing_data_preserved(self, db_engine_with_data):
        """Test that existing session data is preserved during migration."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        with db_engine_with_data.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify data is still there
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM agent_sessions"))
            count = result.scalar()
            assert count == 2, "Existing sessions should be preserved"

            # Verify request_count is still correct
            result = conn.execute(
                sa.text("SELECT request_count FROM agent_sessions WHERE session_id = 'sess-1'")
            )
            row = result.fetchone()
            assert row[0] == 5, "Request count should be preserved"

    def test_downgrade_preserves_existing_data(self, db_engine_with_data):
        """Test that data is preserved during downgrade."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_003_add_daily_usage_synced"
        )

        # Run upgrade first
        with db_engine_with_data.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify data exists after upgrade
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM agent_sessions"))
            count_before = result.scalar()
            assert count_before == 2

        # Run downgrade
        with db_engine_with_data.connect() as conn:
            run_migration_downgrade(conn, migration_module)
            conn.commit()

        # Verify data is still preserved after downgrade
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM agent_sessions"))
            count_after = result.scalar()
            assert count_after == 2, "Data should be preserved after downgrade"

            # Verify request_count is still correct
            result = conn.execute(
                sa.text("SELECT request_count FROM agent_sessions WHERE session_id = 'sess-1'")
            )
            row = result.fetchone()
            assert row[0] == 5, "Request count should be preserved after downgrade"
