"""Route-layer Analysis tenant gate regression tests (Issue #1818 R4).

The round-2 cross-tenant-cache fix added tenant_id filtering to ROICalculator,
but analysis.py routes lacked the require_tenant_scope gate. Because the
underlying tables (daily_stats, hourly_stats, daily_messages) have no
tenant_id column, we cannot implement full tenant isolation. The short-term
fix is to gate the entire Analysis feature to admins only (fail-closed).

These tests pin the fail-closed behaviour at the Analysis route layer:

* non-admins WITHOUT a tenant are denied 403.
* non-admins WITH a tenant are denied 403 (feature limitation).
* admins keep global scope (allowed).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_DB_PATH = tempfile.mktemp(suffix="_tenant_1818.db")
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
    """Patch _load_user_from_token so the test_client bypasses real auth."""
    user = _user_dict(user_id, role, tenant_id)
    return (patch("app.auth.decorators._load_user_from_token", return_value=user),)


class TestAnalysisRouteTenantFailClosed(unittest.TestCase):
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

    def _authed_get(self, path, **auth):
        with ExitStack() as stack:
            for p in _mock_auth(**auth):
                stack.enter_context(p)
            return self.client.get(path)

    # --- non-admin WITHOUT a tenant must be DENIED (severe bug) ---

    def test_analysis_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/analysis/batch?start=2026-01-01&end=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 403)

    def test_analysis_key_metrics_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/analysis/key-metrics?start=2026-01-01&end=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 403)

    def test_analysis_user_ranking_denied_for_notenant_non_admin(self):
        resp = self._authed_get(
            "/api/analysis/user-ranking?start=2026-01-01&end=2026-01-31",
            user_id=11,
            role="user",
            tenant_id=None,
        )
        self.assertEqual(resp.status_code, 403)

    # --- non-admin WITH a tenant also denied (feature limitation) ---

    def test_analysis_denied_for_tenant_non_admin(self):
        """Tenant-scoped non-admin cannot access Analysis (table lacks tenant_id)."""
        resp = self._authed_get(
            "/api/analysis/batch?start=2026-01-01&end=2026-01-31",
            user_id=10,
            role="user",
            tenant_id=7,
        )
        self.assertEqual(resp.status_code, 403)

    # --- admins keep global scope (must NOT be locked out) ---

    def test_analysis_admin_allowed(self):
        resp = self._authed_get(
            "/api/analysis/batch?start=2026-01-01&end=2026-01-31",
            user_id=1,
            role="admin",
            tenant_id=None,
        )
        # 200 or 500 (DB empty) but NOT 403
        self.assertNotEqual(resp.status_code, 403)

    def test_analysis_key_metrics_admin_allowed(self):
        resp = self._authed_get(
            "/api/analysis/key-metrics?start=2026-01-01&end=2026-01-31",
            user_id=1,
            role="admin",
            tenant_id=None,
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_analysis_data_range_admin_allowed(self):
        resp = self._authed_get(
            "/api/analysis/data-range",
            user_id=1,
            role="admin",
            tenant_id=None,
        )
        self.assertNotEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()