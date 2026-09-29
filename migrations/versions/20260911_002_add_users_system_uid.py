"""Add users.system_uid (OS account uid pinning, Issue #3390)

On container recreation (image upgrade, ``compose up -d --force-recreate``)
the container starts from a fresh /etc/passwd while the volume directories
keep their numeric owners. The entrypoint's DB sync re-useradds only the
ACTIVE users without specifying a uid, so the re-assignment runs 1001..N
sequentially and a DEACTIVATED user's old uid necessarily lands on some
active account — numerically handing that account the deactivated user's
0700 /home/<user> and /workspace/<user> (#3374's "A cannot enter B's private
area", triggered by every image upgrade).

Fix (issue option 1): persist each user's OS uid. ``users.system_uid`` is
recorded when the account is created (app-side ``ensure_system_user`` and
the entrypoint sync both write it back) and reused via ``useradd -u`` on
every re-creation, so uids are stable across container recreation. The
entrypoint additionally keeps placeholder accounts
(``useradd -u <uid> -s /usr/sbin/nologin``) for deactivated/soft-deleted
users so a recorded uid can never be reassigned.

Deliberately NO backfill: existing rows have no recorded uid (the value was
never persisted anywhere), so guessing one would be worse than NULL. Rows
keep NULL until their account is next created/re-synced, at which point the
assigned uid is recorded and pinned from then on.

Declared residual (NOT time-bounded — review on #3390): a legacy
DEACTIVATED/soft-deleted user has no pin and is never re-synced, so their
volume dirs stay on an orphan uid indefinitely, and any future UNPINNED
useradd (a new account's first creation, before its record-back) can land
on that number and inherit those directories numerically — once recorded,
that assignment becomes the new user's pin. Known closure (follow-up, not
implemented here): a first-boot volume scan (``stat -c %u`` over /home/* and
the workspace base dirs) could adopt observed orphan uids as pins so they
can never be handed out again.

Revision ID: 20260911_002_add_users_system_uid
Revises: 20260911_001_add_users_tokens_valid_after
Create Date: 2026-09-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260911_002_add_users_system_uid"
down_revision = "20260911_001_add_users_tokens_valid_after"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable system_uid integer column to users."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("users")}

        if "system_uid" not in existing_columns:
            op.add_column(
                "users",
                sa.Column(
                    "system_uid",
                    sa.Integer(),
                    nullable=True,
                    comment=(
                        "Pinned OS uid for the account's system user; passed to "
                        "useradd -u on (re)creation so uids survive container "
                        "recreation and a deactivated user's uid is never "
                        "reassigned (Issue #3390)."
                    ),
                ),
            )


def downgrade() -> None:
    """Remove the system_uid column from users."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("users")}

        if "system_uid" in existing_columns:
            op.drop_column("users", "system_uid")
