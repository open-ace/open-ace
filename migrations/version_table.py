"""Helpers for Alembic's version tracking table."""

from __future__ import annotations

from typing import Any

from alembic.ddl.impl import DefaultImpl
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table

VERSION_NUM_LENGTH = 64
_PATCHED = False


def build_version_table(
    version_table: str,
    version_table_schema: str | None,
    version_table_pk: bool,
) -> Table:
    """Build an Alembic version table that can hold long revision identifiers."""
    table = Table(
        version_table,
        MetaData(),
        Column("version_num", String(VERSION_NUM_LENGTH), nullable=False),
        schema=version_table_schema,
    )
    if version_table_pk:
        table.append_constraint(PrimaryKeyConstraint("version_num", name=f"{version_table}_pkc"))
    return table


def _version_table_impl(
    self: DefaultImpl,
    *,
    version_table: str,
    version_table_schema: str | None,
    version_table_pk: bool,
    **kw: Any,
) -> Table:
    return build_version_table(version_table, version_table_schema, version_table_pk)


def install_wide_version_table() -> None:
    """Patch Alembic so fresh databases use a wider version_num column."""
    global _PATCHED
    if _PATCHED:
        return

    DefaultImpl.version_table_impl = _version_table_impl
    _PATCHED = True
