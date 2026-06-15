"""merge_063_064

Revision ID: 23a7a564f5d8
Revises: 060_fix_agent_identity_boolean, 20260615_063_fix_boolean_retroactive
Create Date: 2026-06-15 18:25:06.849906

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "23a7a564f5d8"
down_revision: Union[str, None] = (
    "060_fix_agent_identity_boolean",
    "20260615_063_fix_boolean_retroactive",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    pass


def downgrade() -> None:
    """Downgrade database schema."""
    pass
