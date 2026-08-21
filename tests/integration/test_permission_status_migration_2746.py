#!/usr/bin/env python3
"""
Test for migration: Add permission_status and permission_task_id to projects table.

Issue: #2746
Migration: migrations/versions/20260820_001_add_permission_status_to_projects.py

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

pytestmark = [pytest.mark.integration, pytest.mark.issue(2746)]


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
    original_create_index = op_module.create_index
    original_drop_column = op_module.drop_column
    original_drop_index = op_module.drop_index
    original_get_bind = op_module.get_bind
    original_batch_alter_table = op_module.batch_alter_table

    # Replace with our operations
    op_module.add_column = operations.add_column
    op_module.create_index = operations.create_index
    op_module.drop_column = operations.drop_column
    op_module.drop_index = operations.drop_index
    op_module.get_bind = lambda: conn
    op_module.batch_alter_table = operations.batch_alter_table

    try:
        migration_module.upgrade()
    finally:
        # Restore original functions
        op_module.add_column = original_add_column
        op_module.create_index = original_create_index
        op_module.drop_column = original_drop_column
        op_module.drop_index = original_drop_index
        op_module.get_bind = original_get_bind
        op_module.batch_alter_table = original_batch_alter_table


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
    original_create_index = op_module.create_index
    original_drop_column = op_module.drop_column
    original_drop_index = op_module.drop_index
    original_get_bind = op_module.get_bind
    original_batch_alter_table = op_module.batch_alter_table

    # Replace with our operations
    op_module.add_column = operations.add_column
    op_module.create_index = operations.create_index
    op_module.drop_column = operations.drop_column
    op_module.drop_index = operations.drop_index
    op_module.get_bind = lambda: conn
    op_module.batch_alter_table = operations.batch_alter_table

    try:
        migration_module.downgrade()
    finally:
        # Restore original functions
        op_module.add_column = original_add_column
        op_module.create_index = original_create_index
        op_module.drop_column = original_drop_column
        op_module.drop_index = original_drop_index
        op_module.get_bind = original_get_bind
        op_module.batch_alter_table = original_batch_alter_table


class TestPermissionStatusMigration:
    """Tests for permission_status migration to projects table."""

    @pytest.fixture
    def db_engine(self):
        """Create an in-memory SQLite database for testing."""
        engine = create_engine("sqlite:///:memory:")

        # Create minimal projects table schema (pre-migration state)
        with engine.connect() as conn:
            conn.execute(sa.text("""
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT,
                    description text,
                    created_by integer,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    is_shared INTEGER DEFAULT 0 NOT NULL,
                    tenant_id integer DEFAULT 1 NOT NULL
                )
            """))
            conn.commit()

        yield engine

        engine.dispose()

    def test_upgrade_adds_permission_columns(self, db_engine):
        """Test that upgrade adds permission_status and permission_task_id columns."""
        import importlib

        # Import migration module
        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
        )

        # Run upgrade in proper Alembic context
        with db_engine.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify columns were added
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("projects")]

        assert "permission_status" in columns, "permission_status column should be added"
        assert "permission_task_id" in columns, "permission_task_id column should be added"

        # Verify index was created
        indexes = [idx["name"] for idx in inspector.get_indexes("projects")]
        assert "idx_projects_permission_status" in indexes, "index should be created"

    def test_downgrade_removes_permission_columns(self, db_engine):
        """Test that downgrade removes permission_status and permission_task_id columns.

        This is the security/data-loss path test - verifying that downgrade
        correctly removes the added columns and index.
        """
        import importlib

        # Import migration module
        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
        )

        # Run upgrade first
        with db_engine.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify columns exist after upgrade
        inspector = sa.inspect(db_engine)
        columns_before = [col["name"] for col in inspector.get_columns("projects")]
        assert "permission_status" in columns_before
        assert "permission_task_id" in columns_before

        # Run downgrade
        with db_engine.connect() as conn:
            run_migration_downgrade(conn, migration_module)
            conn.commit()

        # Verify columns were removed (security/data-loss verification)
        inspector = sa.inspect(db_engine)
        columns_after = [col["name"] for col in inspector.get_columns("projects")]

        assert (
            "permission_status" not in columns_after
        ), "permission_status should be removed on downgrade"
        assert (
            "permission_task_id" not in columns_after
        ), "permission_task_id should be removed on downgrade"

        # Verify index was removed
        indexes = [idx["name"] for idx in inspector.get_indexes("projects")]
        assert (
            "idx_projects_permission_status" not in indexes
        ), "index should be removed on downgrade"

    def test_migration_is_idempotent(self, db_engine):
        """Test that running upgrade twice doesn't fail."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
        )

        with db_engine.connect() as conn:
            # Run upgrade twice (should not fail due to column_exists check)
            run_migration_upgrade(conn, migration_module)
            conn.commit()

            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify columns still exist
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("projects")]
        assert "permission_status" in columns
        assert "permission_task_id" in columns

    def test_downgrade_is_idempotent(self, db_engine):
        """Test that running downgrade twice doesn't fail."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
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

        # Verify columns are gone
        inspector = sa.inspect(db_engine)
        columns = [col["name"] for col in inspector.get_columns("projects")]
        assert "permission_status" not in columns
        assert "permission_task_id" not in columns


class TestPermissionStatusDataPreservation:
    """Test that data is preserved during migration."""

    @pytest.fixture
    def db_engine_with_data(self):
        """Create database with existing project data."""
        engine = create_engine("sqlite:///:memory:")

        with engine.connect() as conn:
            conn.execute(sa.text("""
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT,
                    tenant_id integer DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL
                )
            """))
            # Insert test data
            conn.execute(
                sa.text(
                    "INSERT INTO projects (path, name, tenant_id) VALUES "
                    "('/path/to/project1', 'Project 1', 1), "
                    "('/path/to/project2', 'Project 2', 2)"
                )
            )
            conn.commit()

        yield engine
        engine.dispose()

    def test_upgrade_preserves_existing_data(self, db_engine_with_data):
        """Test that upgrade doesn't lose existing project data."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
        )

        with db_engine_with_data.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify data is still there
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM projects"))
            count = result.scalar()
            assert count == 2, "Existing projects should be preserved"

            # Verify new columns are NULL by default
            result = conn.execute(
                sa.text("SELECT permission_status, permission_task_id FROM projects WHERE id = 1")
            )
            row = result.fetchone()
            assert row[0] is None, "permission_status should be NULL for existing rows"
            assert row[1] is None, "permission_task_id should be NULL for existing rows"

    def test_downgrade_preserves_existing_data(self, db_engine_with_data):
        """Test that data is preserved during downgrade."""
        import importlib

        migration_module = importlib.import_module(
            "migrations.versions.20260820_001_add_permission_status_to_projects"
        )

        # Run upgrade first
        with db_engine_with_data.connect() as conn:
            run_migration_upgrade(conn, migration_module)
            conn.commit()

        # Verify data exists after upgrade
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM projects"))
            count_before = result.scalar()
            assert count_before == 2

        # Run downgrade
        with db_engine_with_data.connect() as conn:
            run_migration_downgrade(conn, migration_module)
            conn.commit()

        # Verify data is still preserved after downgrade
        with db_engine_with_data.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM projects"))
            count_after = result.scalar()
            assert count_after == 2, "Data should be preserved after downgrade"
