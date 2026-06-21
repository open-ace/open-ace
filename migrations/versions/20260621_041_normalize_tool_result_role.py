"""Normalize tool-result message roles to canonical ``tool``

Revision ID: 041_normalize_tool_result_role
Revises: 040_normalize_tool_names_case_insensitive
Create Date: 2026-06-21

Two parallel write paths persisted different spellings for tool-result
messages in ``daily_messages``:

- Claude/OpenClaw intake (``scripts/fetch_openclaw`` / ``fetch_claude``)
  wrote ``role = 'toolResult'``.
- The live autonomous agent path
  (``agent_runner`` -> ``session_manager.add_message(role="tool")``)
  wrote ``role = 'tool'``.

Downstream consumers (the conversation-detail role filter, message
statistics, latency curve) only compared against one spelling, so
conversations produced by the "other" path surfaced as "no messages found"
when filtered by ToolResult.

This migration is a one-time, idempotent data cleanup that collapses every
tool-result spelling (``toolResult``, ``tool_result``, plus case/whitespace
drift) to the canonical ``tool`` across the tables that persist a ``role``
column. Write-side normalization now happens at the intake boundary
(``message_repo.save_message`` / the remote mirror path), so this migration
only backfills historical rows. Each UPDATE only touches rows that still need
fixing, so it is safe to re-run.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "041_normalize_tool_result_role"
down_revision: Union[str, None] = "040_normalize_tool_names_case_insensitive"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

# Tables that persist a message role column.
TABLES = (
    "daily_messages",
    "session_messages",
)

# Variant spellings observed across write paths. Matched case- and
# whitespace-insensitively; all collapse to the canonical "tool".
CANONICAL_ROLE = "tool"
_TOOL_ROLE_ALIASES = ("toolresult", "tool_result")


def upgrade() -> None:
    """Collapse tool-result role spellings to the canonical ``tool``."""
    for table in TABLES:
        # Map the known aliases (matched case- and whitespace-insensitively)
        # to the canonical role. Only touches rows that still need fixing.
        for alias in _TOOL_ROLE_ALIASES:
            op.execute(
                sa.text(
                    f"UPDATE {table} SET role = :canonical " f"WHERE LOWER(TRIM(role)) = :alias"
                ).bindparams(
                    sa.bindparam("canonical", CANONICAL_ROLE), sa.bindparam("alias", alias)
                )
            )


def downgrade() -> None:
    # Data normalization is not meaningfully reversible; original spellings
    # are gone once collapsed. Intentionally a no-op.
    pass
