"""Regression test for the no-tenant non-admin backfill (Issue #1888).

A non-admin user with ``tenant_id IS NULL`` is locked out of every
write/query endpoint by the fail-closed gate from Issue #1775
(``403 Tenant scope required``), yet can still log in and see an empty
project list (Issue #1859 exempts GET). The user is stranded with no
self-service recovery.

Migration ``20260720_001_backfill_notenant_users`` assigns such stranded
non-admins to the default tenant (id=1) so they become functional again.
Admins are left alone because ``NULL`` is legitimate for them (global
scope).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine

pytestmark = [pytest.mark.regression, pytest.mark.issue(1888)]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260720_001_backfill_notenant_users.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_backfill_notenant_users_1888", _MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def engine(tmp_path):
    """A throwaway SQLAlchemy engine with tenants/users seeded for backfill."""
    eng = create_engine(f"sqlite:///{tmp_path / 'backfill_1888.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE tenants (id INTEGER PRIMARY KEY, name TEXT, "
            "slug TEXT, status TEXT, plan TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, "
            "email TEXT, password_hash TEXT, role TEXT, tenant_id INTEGER)"
        )
        conn.exec_driver_sql(
            "INSERT INTO tenants (id, name, slug, status, plan) "
            "VALUES (1, 'Default', 'default', 'active', 'standard')"
        )
        conn.exec_driver_sql(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (1, 'admin', 'a@x', 'h', 'admin', NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (2, 'wd237', 'w@x', 'h', 'user', NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (3, 'tenant_user', 't@x', 'h', 'user', 7)"
        )
    yield eng
    eng.dispose()


def _run_backfill(engine):
    mod = _load_migration_module()
    with engine.begin() as conn:
        return mod.backfill_null_tenant_users(conn)


def _tenant_id(engine, user_id):
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT tenant_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row[0]


def test_non_admin_null_tenant_backfilled_to_default(engine):
    affected = _run_backfill(engine)
    assert affected == 1
    assert _tenant_id(engine, 2) == 1


def test_admin_null_tenant_preserved(engine):
    """Admins keep NULL tenant_id (global scope)."""
    _run_backfill(engine)
    assert _tenant_id(engine, 1) is None


def test_existing_tenant_assignment_preserved(engine):
    """A user already in tenant 7 is not moved to the default tenant."""
    _run_backfill(engine)
    assert _tenant_id(engine, 3) == 7


def test_no_default_tenant_is_noop(engine):
    """If the default tenant is missing, backfill is skipped (fail-safe)."""
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM tenants WHERE id = 1")
    affected = _run_backfill(engine)
    assert affected == 0
    assert _tenant_id(engine, 2) is None


def test_idempotent(engine):
    """Running the backfill twice does not move already-assigned users."""
    _run_backfill(engine)
    affected = _run_backfill(engine)
    assert affected == 0
    assert _tenant_id(engine, 2) == 1
