"""Normalize tool_name case- and whitespace-insensitively across all tables

Revision ID: 040_normalize_tool_names_case_insensitive
Revises: 23a7a564f5d8
Create Date: 2026-06-18

ROI cost-breakdown (and other aggregates) showed duplicate qwen slices
because variant tool names -- ``qwen-code``/``qwen-code-cli`` alongside the
canonical ``qwen``, and ``claude-code`` alongside ``claude`` -- kept being
written by intake paths that did not normalize. Migrations 038/039 cleaned
the canonical aliases once but new rows re-contaminated ``daily_messages``,
``hourly_stats`` and ``agent_sessions``.

This migration is a one-time, idempotent data cleanup that:
  1. Maps every known alias to its canonical name (case-insensitive match).
  2. Collapses residual case drift and surrounding whitespace on any
     remaining value, so future aggregates can never split on casing again.

Write-side normalization now happens at the intake boundary
(``save_usage`` / ``save_message`` / ``create_session``), so this migration
only needs to backfill the historical rows. Each UPDATE only touches rows
that still need fixing, so it is safe to re-run.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "040_normalize_tool_names_case_insensitive"
down_revision: Union[str, None] = "23a7a564f5d8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

# Every table that persists a tool_name column.
TABLES = (
    "daily_usage",
    "daily_messages",
    "daily_stats",
    "hourly_stats",
    "usage_summary",
    "agent_sessions",
)

# Known alias -> canonical (matched case- and whitespace-insensitively).
# Mirrors app/utils/tool_names.py::CANONICAL_TOOL_NAMES.
ALIAS_MAP = {
    "qwen-code": "qwen",
    "qwen-code-cli": "qwen",
    "claude-code": "claude",
    "codex-cli": "codex",
    "zcode-code": "zcode",
    "zcode-cli": "zcode",
}


def upgrade() -> None:
    # Use bind parameters so the alias/canonical literals are passed safely
    # regardless of the active DB (PostgreSQL/SQLite) placeholder style.
    for table in TABLES:
        for alias, canonical in ALIAS_MAP.items():
            op.execute(
                sa.text(
                    f"UPDATE {table} SET tool_name = :canonical "
                    f"WHERE LOWER(TRIM(tool_name)) = :alias"
                ).bindparams(sa.bindparam("canonical", canonical), sa.bindparam("alias", alias))
            )

        # Collapse residual case drift + surrounding whitespace on every other
        # value (e.g. "Qwen" -> "qwen"). No-op for already-canonical rows.
        op.execute(
            sa.text(
                f"UPDATE {table} SET tool_name = LOWER(TRIM(tool_name)) "
                f"WHERE tool_name IS NOT NULL AND tool_name <> LOWER(TRIM(tool_name))"
            )
        )


def downgrade() -> None:
    # Data normalization is not meaningfully reversible; original variants are
    # gone once collapsed. Intentionally a no-op.
    pass
