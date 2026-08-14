"""Add unique index on prompt_templates.name.

Issue #2577: Featured/recommended prompt templates were missing because
seed_default_templates() used a read-then-write check that raced under
concurrent workers, producing duplicate rows by name. A unique index on
``prompt_templates(name)`` makes seeding idempotent at the DB layer.

This migration:
- De-duplicates existing rows by name (keeps MIN(id), deletes the rest) so
  the unique index can be created even on databases with pre-existing dupes.
- Creates the unique index ``idx_prompt_templates_name``.

Revision ID: 20260814_004
Revises: 20260814_003
Create Date: 2026-08-14

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260814_004"
down_revision = "20260814_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """De-duplicate by name, then add the unique index."""
    # De-duplicate: keep MIN(id) per name, delete the duplicate rows. This is a
    # data migration, so use raw SQL via op.execute() (portable across SQLite
    # and PostgreSQL). id is NOT NULL (primary key), so NOT IN is safe here.
    op.execute("""
        DELETE FROM prompt_templates
        WHERE id NOT IN (
            SELECT MIN(id) FROM prompt_templates GROUP BY name
        )
        """)

    # Use IF NOT EXISTS to handle cases where schema.sql already created this
    # index (e.g. a database bootstrapped via load_schema_from_file before
    # migrations run). Mirrors the IF NOT EXISTS semantics in _ensure_tables().
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_templates_name ON prompt_templates (name)"
    )


def downgrade() -> None:
    """Drop the unique index (may fail if duplicates exist)."""
    op.execute("DROP INDEX IF EXISTS idx_prompt_templates_name")
