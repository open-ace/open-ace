"""Migration coverage for webhook delivery cooldown columns (Issue #2063)."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

pytestmark = [pytest.mark.integration, pytest.mark.issue(2063)]


def _run_migration(conn, migration_module, direction: str):
    context = MigrationContext.configure(conn)
    operations = Operations(context)

    import alembic.op as op_module

    originals = {
        "add_column": op_module.add_column,
        "drop_column": op_module.drop_column,
        "create_index": op_module.create_index,
        "drop_index": op_module.drop_index,
        "batch_alter_table": op_module.batch_alter_table,
        "get_bind": op_module.get_bind,
    }
    op_module.add_column = operations.add_column
    op_module.drop_column = operations.drop_column
    op_module.create_index = operations.create_index
    op_module.drop_index = operations.drop_index
    op_module.batch_alter_table = operations.batch_alter_table
    op_module.get_bind = lambda: conn
    try:
        getattr(migration_module, direction)()
    finally:
        for name, original in originals.items():
            setattr(op_module, name, original)


def _create_prior_schema(conn):
    conn.execute(sa.text("""
        CREATE TABLE webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            webhook_url_hash TEXT,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            next_retry_at TIMESTAMP,
            last_error_type TEXT,
            last_error_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """))
    conn.commit()


def test_webhook_delivery_cooldown_migration_upgrade_and_downgrade():
    migration = importlib.import_module(
        "migrations.versions.20260824_001_add_webhook_delivery_cooldown"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        _create_prior_schema(conn)

        _run_migration(conn, migration, "upgrade")
        inspector = sa.inspect(conn)
        columns = {col["name"] for col in inspector.get_columns("webhook_deliveries")}
        indexes = {idx["name"] for idx in inspector.get_indexes("webhook_deliveries")}

        assert {
            "receiver_identity_hash",
            "cooldown_key",
            "cooldown_expires_at",
            "delivery_claim_token",
            "delivery_claim_expires_at",
        }.issubset(columns)
        assert {
            "idx_webhook_deliveries_cooldown_active",
            "idx_webhook_deliveries_cooldown_expiry",
            "idx_webhook_deliveries_receiver_identity",
        }.issubset(indexes)

        _run_migration(conn, migration, "downgrade")
        inspector = sa.inspect(conn)
        columns = {col["name"] for col in inspector.get_columns("webhook_deliveries")}
        indexes = {idx["name"] for idx in inspector.get_indexes("webhook_deliveries")}
        assert "cooldown_key" not in columns
        assert "idx_webhook_deliveries_cooldown_active" not in indexes
