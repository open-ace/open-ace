"""merge_041_064

Revision ID: 7bcf07ee658e
Revises: 041_normalize_tool_result_role, 20260618_064_fix_quota_unit_inconsistency
Create Date: 2026-06-21 19:35:47

Merge point that collapses the two heads created when the tool-result role
normalization migration (``041_normalize_tool_result_role``) was attached to
the ``040_normalize_tool_names_case_insensitive`` lineage while
``20260618_064_fix_quota_unit_inconsistency`` branched independently off
``20260615_063_fix_boolean_retroactive``. Without this merge ``alembic heads``
reports two heads and ``alembic upgrade head`` is ambiguous. This is a
topological merge only -- no schema changes.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "7bcf07ee658e"
down_revision: Union[str, None] = (
    "041_normalize_tool_result_role",
    "20260618_064_fix_quota_unit_inconsistency",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    pass


def downgrade() -> None:
    """Downgrade database schema."""
    pass
