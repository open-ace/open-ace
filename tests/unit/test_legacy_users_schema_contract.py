"""Contract: the authoritative schema-load path yields a complete users table.

Legacy tests/issues fixtures used to hand-CREATE an old users table (no
deleted_at), then call load_schema_from_file — whose CREATE TABLE IF NOT EXISTS
is a no-op on an existing table, so CREATE INDEX idx_users_deleted fails (the
113-entry deleted_at baseline cluster, #2457 phase 2). Fix = load the
authoritative schema on an empty DB first. PG-independent (explicit sqlite path
+ dialect).
"""

import sqlite3

import pytest

from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file


def _users_columns(db_url: str) -> set[str]:
    conn = Database(db_url=db_url).get_connection()
    try:
        rows = conn.execute("PRAGMA table_info(users)").fetchall()
    finally:
        conn.close()
    return {r[1] for r in rows}  # PRAGMA table_info col 1 = column name


def test_load_schema_creates_users_with_deleted_at(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'contract.db'}"
    load_schema_from_file(db_url=db_url, dialect="sqlite")
    cols = _users_columns(db_url)
    assert "deleted_at" in cols, "authoritative users table must have deleted_at"
    # Pin a few other authoritative columns so a schema-file regression is caught.
    for required in ("username", "password_hash", "role", "tenant_id"):
        assert required in cols


def test_hand_created_old_users_table_is_not_backfilled(tmp_path):
    """Documents the escape hatch: hand-CREATE users WITHOUT deleted_at, then
    load_schema RAISES (the idx_users_deleted index cannot be built) — it does
    not silently add the missing column. This is why fixtures must load the
    authoritative schema on an empty DB first, not hand-CREATE an old users
    table.
    """
    path = tmp_path / "trap.db"
    conn = sqlite3.connect(path)
    # Every authoritative users column EXCEPT deleted_at, so the only missing
    # piece load_schema needs is deleted_at itself.
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, "
        "role TEXT, is_active INTEGER, tenant_id INTEGER)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        load_schema_from_file(db_url=f"sqlite:///{path}", dialect="sqlite")
