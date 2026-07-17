"""Add tenant scope to projects

Revision ID: 20260717_003_add_project_tenant_scope
Revises: 20260717_002_add_workspace_session_tenant_scope
Create Date: 2026-07-17

Issue: #1760

Persist tenant attribution on projects so project lookup, sharing, and path
uniqueness can be enforced inside the database instead of relying on caller-
supplied user filters alone.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_003_add_project_tenant_scope"
down_revision: str | None = "20260717_002_add_workspace_session_tenant_scope"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Persist tenant_id on projects and rebuild path indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    project_columns = _column_names(inspector, "projects")
    if "tenant_id" not in project_columns:
        op.add_column(
            "projects",
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        )

    conn.execute(
        sa.text(
            """
            UPDATE projects
            SET tenant_id = COALESCE(
                (SELECT users.tenant_id FROM users WHERE users.id = projects.created_by),
                (
                    SELECT users.tenant_id
                    FROM user_projects
                    INNER JOIN users ON users.id = user_projects.user_id
                    WHERE user_projects.project_id = projects.id
                      AND users.tenant_id IS NOT NULL
                    ORDER BY user_projects.id ASC
                    LIMIT 1
                ),
                tenant_id,
                1
            )
            """
        )
    )

    project_indexes = _index_names(inspector, "projects")
    if "idx_projects_tenant_created_by" not in project_indexes:
        op.create_index(
            "idx_projects_tenant_created_by",
            "projects",
            ["tenant_id", "created_by"],
            unique=False,
        )

    if "idx_projects_path" in project_indexes:
        op.drop_index("idx_projects_path", table_name="projects")
    op.create_index("idx_projects_path", "projects", ["tenant_id", "path"], unique=False)

    if "uq_projects_path" in project_indexes:
        op.drop_index("uq_projects_path", table_name="projects")
    op.create_index(
        "uq_projects_path",
        "projects",
        ["tenant_id", "path"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    """Remove tenant scope from projects and restore legacy indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    project_indexes = _index_names(inspector, "projects")

    if "uq_projects_path" in project_indexes:
        op.drop_index("uq_projects_path", table_name="projects")
    op.create_index(
        "uq_projects_path",
        "projects",
        ["path"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active IS TRUE"),
    )

    project_indexes = _index_names(sa.inspect(conn), "projects")
    if "idx_projects_path" in project_indexes:
        op.drop_index("idx_projects_path", table_name="projects")
    op.create_index("idx_projects_path", "projects", ["path"], unique=False)

    project_indexes = _index_names(sa.inspect(conn), "projects")
    if "idx_projects_tenant_created_by" in project_indexes:
        op.drop_index("idx_projects_tenant_created_by", table_name="projects")

    project_columns = _column_names(sa.inspect(conn), "projects")
    if "tenant_id" in project_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("projects", "tenant_id")
        else:
            with op.batch_alter_table("projects") as batch_op:
                batch_op.drop_column("tenant_id")
