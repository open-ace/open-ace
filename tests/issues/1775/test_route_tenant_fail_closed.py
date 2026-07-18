"""Route-layer tenant-isolation regression tests (Issue #1775).

PR #1775 introduced ``_current_tenant_id()`` on the governance, usage,
compliance, and projects blueprints.  When a NON-ADMIN user has no
``tenant_id`` the helper returns ``None`` and the repository layer treats
``None`` as "no filter" (wildcard / global).  A no-tenant non-admin could
therefore read CROSS-TENANT audit logs, usage data, and projects.

These tests pin the fail-closed behaviour at the route layer:
non-admins without a resolvable tenant are denied (403 / empty), while
admins keep global scope and tenant-scoped non-admins keep their tenant.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


_DB_PATH = tempfile.mktemp(suffix="_tenant_1775.db")
_DB_URL = f"sqlite:///{_DB_PATH}"


def _install_db_url():
    """Point db_mod at the shared test DB and disable SQL adaptation.

    The route blueprints construct module-level singletons
    (``usage_service``, ``project_repo``) whose ``Database()`` is bound at
    import time.  We additionally rebind those singletons' ``db`` handles
    in :func:`_make_client` so the test is immune to import ordering when
    the full suite runs alongside other tests that swap the DB URL.
    """
    import app.repositories.database as db_mod

    orig = db_mod.adapt_sql
    orig_get_database_url = db_mod.get_database_url
    db_mod.adapt_sql = lambda sql: sql
    db_mod.get_database_url = lambda: _DB_URL
    return orig, orig_get_database_url, db_mod


def _rebind_singletons(db):
    """Point the route-blueprint module singletons at ``db`` and return the
    saved ``db`` values so the caller can restore them in tearDown."""
    import app.routes.projects as projects_mod
    import app.routes.usage as usage_mod

    saved = (
        usage_mod.usage_service.usage_repo.db,
        projects_mod.project_repo.db,
        projects_mod.user_repo.db,
    )
    usage_mod.usage_service.usage_repo.db = db
    projects_mod.project_repo.db = db
    projects_mod.user_repo.db = db
    return saved


def _restore_singletons(saved):
    import app.routes.projects as projects_mod
    import app.routes.usage as usage_mod

    usage_db, projects_db, user_db = saved
    usage_mod.usage_service.usage_repo.db = usage_db
    projects_mod.project_repo.db = projects_db
    projects_mod.user_repo.db = user_db


def _make_client():
    import app.repositories.database as db_mod
    from app import create_app

    app = create_app({"TESTING": True})
    db = db_mod.Database(db_url=_DB_URL)
    # Rebind the route-blueprint module singletons to this live DB so the
    # test is independent of whichever DB URL was active at import time.
    saved = _rebind_singletons(db)
    # Seed a couple of users in different tenants so that wildcard queries
    # would surface cross-tenant data if the route fails open.
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users")
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "admin", "admin@test.com", "hash", "admin", None),
        )
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (10, "tenant_user", "tu@test.com", "hash", "user", 7),
        )
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, role, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (11, "notenant_user", "nt@test.com", "hash", "user", None),
        )
        conn.commit()

    client = app.test_client()
    client.set_cookie("session_token", "test-token")
    return client, db_mod, saved


def _user_dict(user_id=1, role="admin", tenant_id=None):
    username = "admin" if role == "admin" else ("tenant_user" if tenant_id else "notenant_user")
    return {
        "id": user_id,
        "username": username,
        "email": f"{username}@test.com",
        "role": role,
        "tenant_id": tenant_id,
    }


def _mock_auth(user_id=1, role="admin", tenant_id=None):
    """Patch _load_user_from_token in every importer so the test_client
    bypasses real auth. projects.py binds the name into its own namespace,
    so both patches are required."""
    user = _user_dict(user_id, role, tenant_id)
    return (
        patch("app.auth.decorators._load_user_from_token", return_value=user),
        patch("app.routes.projects._load_user_from_token", return_value=user),
    )


class TestRouteTenantFailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig, cls.orig_get_database_url, cls.db_mod = _install_db_url()

    @classmethod
    def tearDownClass(cls):
        cls.db_mod.adapt_sql = cls.orig
        cls.db_mod.get_database_url = cls.orig_get_database_url
        try:
            os.unlink(_DB_PATH)
        except OSError:
            pass

    def setUp(self):
        self.client, _, self.saved_singletons = _make_client()

    def tearDown(self):
        _restore_singletons(self.saved_singletons)

    # --- non-admin WITHOUT a tenant must be denied (the bug) ---

    def _authed_get(self, path, **auth):
        """GET path with _load_user_from_token patched across importers."""
        with ExitStack() as stack:
            for p in _mock_auth(**auth):
                stack.enter_context(p)
            return self.client.get(path)

    def test_usage_tools_denied_for_notenant_non_admin(self):
        resp = self._authed_get("/api/tools", user_id=11, role="user", tenant_id=None)
        # Bug on main: returns 200 with cross-tenant/global tool list.
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 403)

    def test_usage_today_denied_for_notenant_non_admin(self):
        resp = self._authed_get("/api/today", user_id=11, role="user", tenant_id=None)
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 403)

    def test_projects_denied_for_notenant_non_admin(self):
        resp = self._authed_get("/api/projects", user_id=11, role="user", tenant_id=None)
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 403)

    # --- admins keep global scope (must NOT be locked out) ---

    def test_usage_tools_admin_keeps_global(self):
        resp = self._authed_get("/api/tools", user_id=1, role="admin", tenant_id=None)
        self.assertEqual(resp.status_code, 200)

    def test_projects_admin_keeps_global(self):
        resp = self._authed_get("/api/projects", user_id=1, role="admin", tenant_id=None)
        # Admin with no projects -> 200 with empty list (NOT 403).
        self.assertEqual(resp.status_code, 200)

    # --- tenant-scoped non-admins keep their tenant (not denied) ---

    def test_usage_tools_tenant_user_allowed(self):
        resp = self._authed_get("/api/tools", user_id=10, role="user", tenant_id=7)
        self.assertEqual(resp.status_code, 200)

    def test_projects_tenant_user_allowed(self):
        resp = self._authed_get("/api/projects", user_id=10, role="user", tenant_id=7)
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
