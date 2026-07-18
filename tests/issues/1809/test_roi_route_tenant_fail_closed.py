"""Route-layer ROI tenant-isolation regression tests (PR #1809 严重#1).

The round-2 cross-tenant-cache fix added ``tenant_id`` filtering to the
``ROICalculator`` cached read paths, but ``app/routes/roi.py`` lacked the
``require_tenant_scope`` gate that every other tenant-aware blueprint
(usage, projects, governance, compliance) installs in ``before_request``.

A NON-ADMIN user with no ``tenant_id`` (e.g. a misconfigured DB row) hit
``_caller_tenant_id() -> g.tenant_id -> None``; ``_normalize_tenant_id(None)``
collapses to ``None`` which the calculator treats as a wildcard/global
filter. The cross-tenant cache leak that PR #1780 fixed at the calculator
layer therefore re-opened at the route layer for this one caller class.

These tests pin the fail-closed behaviour at the ROI route layer:

* non-admins WITHOUT a tenant are denied 403 (the bug);
* admins keep global scope (``tenant_id=None`` passed through);
* tenant-scoped non-admins have their ``tenant_id`` forwarded verbatim
  to ``ROICalculator`` (cache key + DB filter), not collapsed to ``None``.

Mirrors ``tests/issues/1775/test_route_tenant_fail_closed.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


_DB_PATH = tempfile.mktemp(suffix="_tenant_1809.db")
_DB_URL = f"sqlite:///{_DB_PATH}"


def _install_db_url():
    """Point db_mod at the shared test DB and disable SQL adaptation."""
    import app.repositories.database as db_mod

    orig = db_mod.adapt_sql
    orig_get_database_url = db_mod.get_database_url
    db_mod.adapt_sql = lambda sql: sql
    db_mod.get_database_url = lambda: _DB_URL
    return orig, orig_get_database_url, db_mod


def _make_client():
    import app.repositories.database as db_mod
    from app import create_app

    app = create_app({"TESTING": True})
    db = db_mod.Database(db_url=_DB_URL)
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
    return client, db_mod


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
    """Patch _load_user_from_token so the test_client bypasses real auth.

    ``app/routes/roi.py`` calls ``auth_required`` directly (it does not bind
    ``_load_user_from_token`` into its own namespace like projects.py does),
    so a single patch on the auth module is enough.
    """
    user = _user_dict(user_id, role, tenant_id)
    return (patch("app.auth.decorators._load_user_from_token", return_value=user),)


class TestRoiRouteTenantFailClosed(unittest.TestCase):
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
        self.client, _ = _make_client()

    # --- non-admin WITHOUT a tenant must be DENIED (the severe bug) ---

    def _authed_get(self, path, **auth):
        with ExitStack() as stack:
            for p in _mock_auth(**auth):
                stack.enter_context(p)
            return self.client.get(path)

    def test_roi_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/roi?start_date=2026-01-01&end_date=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        # Bug on round-2 branch: returns 200 with all-tenant/global ROI.
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 403)

    def test_roi_summary_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/roi/summary?start_date=2026-01-01&end_date=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 403)

    def test_roi_by_user_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/roi/by-user?start_date=2026-01-01&end_date=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 403)

    # --- admins keep global scope (must NOT be locked out) ---

    def test_roi_admin_keeps_global(self):
        resp = self._authed_get(
            "/api/roi?start_date=2026-01-01&end_date=2026-01-31",
            user_id=1,
            role="admin",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 200)

    def test_roi_summary_admin_keeps_global(self):
        resp = self._authed_get(
            "/api/roi/summary?start_date=2026-01-01&end_date=2026-01-31",
            user_id=1,
            role="admin",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 200)

    # --- tenant-scoped non-admins keep their tenant (allowed, isolated) ---

    def test_roi_tenant_user_allowed(self):
        resp = self._authed_get(
            "/api/roi?start_date=2026-01-01&end_date=2026-01-31",
            user_id=10,
            role="user",
            tenant_id=7,
        )
        self.assertEqual(resp.status_code, 200)

    # --- tenant_id is forwarded VERBATIM to ROICalculator (not collapsed) ---

    def test_tenant_id_forwarded_to_calculator(self):
        """g.tenant_id==7 must reach calculate_roi(tenant_id=7), not None."""
        import app.routes.roi as roi_mod
        from app.modules.analytics.roi_calculator import ROICalculator

        captured = {}

        orig_calc = ROICalculator.calculate_roi

        def _spy(self, *args, **kwargs):
            captured["tenant_id"] = kwargs.get("tenant_id")
            return orig_calc(self, *args, **kwargs)

        with ExitStack() as stack:
            for p in _mock_auth(user_id=10, role="user", tenant_id=7):
                stack.enter_context(p)
            stack.enter_context(patch.object(ROICalculator, "calculate_roi", _spy))
            resp = self.client.get("/api/roi?start_date=2026-01-01&end_date=2026-01-31")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            captured.get("tenant_id"),
            7,
            "tenant-scoped caller's tenant_id must be forwarded to the calculator, "
            "not collapsed to None (which would query all tenants).",
        )

    def test_admin_global_tenant_id_forwarded_as_none(self):
        """Admin with no tenant_id forwards None (global) to the calculator."""
        from app.modules.analytics.roi_calculator import ROICalculator

        captured = {}
        orig_calc = ROICalculator.calculate_roi

        def _spy(self, *args, **kwargs):
            captured["tenant_id"] = kwargs.get("tenant_id")
            return orig_calc(self, *args, **kwargs)

        with ExitStack() as stack:
            for p in _mock_auth(user_id=1, role="admin", tenant_id=None):
                stack.enter_context(p)
            stack.enter_context(patch.object(ROICalculator, "calculate_roi", _spy))
            resp = self.client.get("/api/roi?start_date=2026-01-01&end_date=2026-01-31")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(captured.get("tenant_id"))


if __name__ == "__main__":
    unittest.main()
