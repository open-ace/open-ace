"""Merge multiple heads into single revision.

Revision ID: 20260623_merge_heads
Revises: 20260622_001_session_message_source, 026_add_tool_account_mapping_rules
Create Date: 2026-06-23

Merge point that collapses the two heads created when the tool_account_mapping_rules
migration branched independently while session_message_source continued from the
earlier merge point. This is a topological merge only -- no schema changes.
"""

from typing import Sequence, Union

revision: str = "20260623_merge_heads"
down_revision: Union[str, None] = (
    "20260622_001_session_message_source",
    "026_add_tool_account_mapping_rules",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    pass


def downgrade() -> None:
    """Downgrade database schema."""
    pass
