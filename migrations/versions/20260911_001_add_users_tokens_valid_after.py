"""Add users.tokens_valid_after (webui URL-token invalidation stamp)

Issue #3379 (review round 2, R-5): WebUI URL tokens are stateless — v1
tokens carry no TTL at all and v2 tokens live for the configured TTL — so
tokens leaked before a deactivation stayed usable (URL_TOKEN_ALLOWED_PATHS
admits /api/admin/ routes) and a reactivation resurrected all of them. The
new nullable UTC timestamp column is stamped when an account is deactivated
(PUT is_active=false) or soft-deleted (DELETE); token validation then
refuses v2 tokens whose embedded mint time predates the stamp and v1 tokens
outright while the stamp is set.

Semantics (declared residual, also in the PR description): reactivation and
restore deliberately do NOT clear the stamp — old leaked URLs stay dead and
the restored user mints fresh tokens on their next /user-url.

Revision ID: 20260911_001_add_users_tokens_valid_after
Revises: 20260907_001_add_session_daily_usage_table
Create Date: 2026-09-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260911_001_add_users_tokens_valid_after"
down_revision = "20260907_001_add_session_daily_usage_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable tokens_valid_after timestamp to users."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("users")}

        if "tokens_valid_after" not in existing_columns:
            op.add_column(
                "users",
                sa.Column(
                    "tokens_valid_after",
                    sa.DateTime(),
                    nullable=True,
                    comment=(
                        "UTC timestamp; WebUI URL tokens minted before it are "
                        "invalid. Stamped on deactivation/soft-delete, never "
                        "cleared on reactivation/restore (Issue #3379 R-5)."
                    ),
                ),
            )


def downgrade() -> None:
    """Remove the tokens_valid_after column from users."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("users")}

        if "tokens_valid_after" in existing_columns:
            op.drop_column("users", "tokens_valid_after")
