"""External principal liveness against real row types.

The check selects two soft-delete columns; before aliasing, dict-style rows
(RealDictCursor on PostgreSQL, sqlite3.Row on SQLite) collapsed them into one
`deleted_at` key — on PostgreSQL keeping the tenant's value (a soft-deleted
user passed), on SQLite the user's (a soft-deleted tenant passed). These tests
execute the real SQL against real backends so column-name collisions surface.
"""

import sqlite3

import pytest

from app.modules.workspace.api_key_proxy import APIKeyProxyService

pytestmark = pytest.mark.postgres


def _insert_principal(conn, placeholder, *, user_deleted=None, tenant_deleted=None):
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO tenants (id, name, slug, status) "
        f"VALUES ({placeholder}, 'T', 't', 'active')",
        (1,),
    )
    cursor.execute(
        "INSERT INTO users (id, username, password_hash, tenant_id, is_active, deleted_at) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, TRUE, {placeholder})",
        (2, "u", "h", 1, user_deleted),
    )
    if tenant_deleted is not None:
        cursor.execute(f"UPDATE tenants SET deleted_at = {placeholder}", (tenant_deleted,))
    conn.commit()


def test_soft_deleted_user_rejected_on_postgres(pg_db):
    _insert_principal(pg_db.get_connection(), "%s", user_deleted="2026-01-01 00:00:00")
    svc = object.__new__(APIKeyProxyService)
    assert svc._external_principal_alive_with_conn(pg_db.get_connection(), 2, 1) is False


def test_healthy_principal_alive_on_postgres(pg_db):
    _insert_principal(pg_db.get_connection(), "%s")
    svc = object.__new__(APIKeyProxyService)
    assert svc._external_principal_alive_with_conn(pg_db.get_connection(), 2, 1) is True


def _sqlite_row_conn(tmp_path):
    db = tmp_path / "liveness.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(f"sqlite:///{db}", dialect="sqlite")
    return conn


def test_soft_deleted_user_rejected_on_sqlite_row(tmp_path):
    conn = _sqlite_row_conn(tmp_path)
    _insert_principal(conn, "?", user_deleted="2026-01-01 00:00:00")
    svc = object.__new__(APIKeyProxyService)
    assert svc._external_principal_alive_with_conn(conn, 2, 1) is False


def test_soft_deleted_tenant_rejected_on_sqlite_row(tmp_path):
    conn = _sqlite_row_conn(tmp_path)
    _insert_principal(conn, "?", tenant_deleted="2026-01-01 00:00:00")
    svc = object.__new__(APIKeyProxyService)
    assert svc._external_principal_alive_with_conn(conn, 2, 1) is False
