"""External identity probe replay-protection nonces.

The runtime issues no DDL: this table comes from migrations / the
authoritative schema snapshots, so the application role needs DML
privileges only.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260921_001_external_identity_nonces"
down_revision = "20260921_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The schema.sql snapshots also define this table, so freshly
    # bootstrapped databases already have it; guard both creates against the
    # existing schema so `alembic upgrade head` after bootstrap is idempotent.
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "external_identity_nonces" in set(inspector.get_table_names()):
        existing = {idx["name"] for idx in inspector.get_indexes("external_identity_nonces")}
        for name, columns in (
            ("external_identity_nonces_key", ["issuer", "nonce"]),
            ("idx_external_identity_nonces_expires", ["expires_at"]),
        ):
            if name not in existing:
                op.create_index(
                    name, "external_identity_nonces", columns, unique=name.endswith("_key")
                )
        return
    op.create_table(
        "external_identity_nonces",
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "external_identity_nonces_key", "external_identity_nonces", ["issuer", "nonce"], unique=True
    )
    op.create_index(
        "idx_external_identity_nonces_expires", "external_identity_nonces", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_external_identity_nonces_expires", table_name="external_identity_nonces")
    op.drop_index("external_identity_nonces_key", table_name="external_identity_nonces")
    op.drop_table("external_identity_nonces")
