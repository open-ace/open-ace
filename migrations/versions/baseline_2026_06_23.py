"""baseline_2026_06_23

Revision ID: baseline_2026_06_23
Revises:
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op

from migrations.baseline import BASELINE_REVISION, execute_sql_script, read_baseline_schema, table_exists

# revision identifiers, used by Alembic.
revision: str = BASELINE_REVISION
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Bootstrap a fresh database from the 2026-06-23 baseline snapshot."""
    connection = op.get_bind()
    if table_exists(connection, "users"):
        return

    execute_sql_script(connection, read_baseline_schema(connection.dialect.name))


def downgrade() -> None:
    """Downgrade is intentionally unsupported for the baseline snapshot."""
    pass
