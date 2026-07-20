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
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "migrations",
    "versions",
    "20260720_001_backfill_notenant_users.py",
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_backfill_notenant_users_1888", _MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BackfillNoTenantUsersMigrationTest(unittest.TestCase):
    """Exercise the migration's backfill helper against a throwaway SQLite DB."""

    def setUp(self):
        from sqlalchemy import create_engine

        self._db_path = tempfile.mktemp(suffix="_backfill_1888.db")
        self._engine = create_engine(f"sqlite:///{self._db_path}")
        with self._engine.begin() as conn:
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

    def tearDown(self):
        self._engine.dispose()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _run_backfill(self):
        mod = _load_migration_module()
        with self._engine.begin() as conn:
            return mod.backfill_null_tenant_users(conn)

    def _tenant_id(self, user_id):
        with self._engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT tenant_id FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return row[0]

    def test_non_admin_null_tenant_backfilled_to_default(self):
        affected = self._run_backfill()
        self.assertEqual(affected, 1)
        self.assertEqual(self._tenant_id(2), 1)

    def test_admin_null_tenant_preserved(self):
        """Admins keep NULL tenant_id (global scope)."""
        self._run_backfill()
        self.assertIsNone(self._tenant_id(1))

    def test_existing_tenant_assignment_preserved(self):
        """A user already in tenant 7 is not moved to the default tenant."""
        self._run_backfill()
        self.assertEqual(self._tenant_id(3), 7)

    def test_no_default_tenant_is_noop(self):
        """If the default tenant is missing, backfill is skipped (fail-safe)."""
        with self._engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM tenants WHERE id = 1")
        affected = self._run_backfill()
        self.assertEqual(affected, 0)
        self.assertIsNone(self._tenant_id(2))

    def test_idempotent(self):
        """Running the backfill twice does not move already-assigned users."""
        self._run_backfill()
        affected = self._run_backfill()
        self.assertEqual(affected, 0)
        self.assertEqual(self._tenant_id(2), 1)


if __name__ == "__main__":
    unittest.main()
