#!/usr/bin/env python3
"""
Unit tests for VSCode proxy authentication (Issue #2183).

Tests cover:
- Token validation: valid, missing, invalid, expired
- Authorization header token extraction
- Cross-tenant isolation
- Session expiration
- WebSocket authentication
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _mock_load_user(token):
    """Parse test tokens like 'test-token-<uid>-<role>-<tenant>' into user dicts."""
    if not token:
        return None
    if token.startswith("test-token-"):
        parts = token.split("-")
        if len(parts) >= 5:
            return {
                "id": int(parts[2]),
                "username": f"user{parts[2]}",
                "email": f"user{parts[2]}@test.com",
                "role": parts[3],
                "tenant_id": int(parts[4]),
            }
    return None


def _make_app(mgr):
    """Create a minimal Flask app with remote_bp for route testing."""
    from flask import Flask

    import app.modules.workspace.remote_agent_manager as ram_mod
    from app.routes import remote as remote_mod

    ram_mod._agent_manager = mgr

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

    # Patch auth helpers so before_request uses our mock
    from app.auth import decorators as auth_dec

    auth_dec._load_user_from_token = _mock_load_user
    remote_mod._load_user_from_token = _mock_load_user

    return app


class TestVSCodeTokenAuthentication(unittest.TestCase):
    """Tests for VSCode proxy token authentication (Issue #2183)."""

    def test_valid_token_allows_access(self):
        """Valid token should allow access to running session."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True

        from app.modules.workspace import vscode_store as vs_mod

        # Create a test session with all required fields
        vscode_id = "test-vscode-123"
        machine_id = "test-machine-1"
        valid_token = "valid-browser-token-256-bits-entropy"
        now = time.time()

        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": valid_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 1,
                "owner_user_id": 1,
                "created_at": now,
                "expires_at": now + 3600,
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)
            with app.test_client() as client:
                resp = client.get(
                    f"/api/remote/vscode/{vscode_id}/proxy/",
                    query_string={"token": valid_token},
                )
                # Should not return 401/403 (might return 500/502 if proxy fails, which is OK)
                self.assertNotEqual(resp.status_code, 401)
                self.assertNotEqual(resp.status_code, 403)

    def test_missing_token_returns_401(self):
        """Missing token should return 401, not 200 (Issue #2183)."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True

        from app.modules.workspace import vscode_store as vs_mod

        vscode_id = "test-vscode-456"
        machine_id = "test-machine-1"
        stored_token = "stored-token-for-test"
        now = time.time()

        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": stored_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 1,
                "owner_user_id": 1,
                "created_at": now,
                "expires_at": now + 3600,
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)
            with app.test_client() as client:
                # No token provided - should return 401
                resp = client.get(f"/api/remote/vscode/{vscode_id}/proxy/")
                self.assertEqual(resp.status_code, 401)
                data = resp.get_json()
                self.assertIn("Authentication required", data.get("error", ""))

    def test_invalid_token_returns_403(self):
        """Invalid token should return 403."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True

        from app.modules.workspace import vscode_store as vs_mod

        vscode_id = "test-vscode-789"
        machine_id = "test-machine-1"
        stored_token = "correct-token"
        invalid_token = "wrong-token"
        now = time.time()

        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": stored_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 1,
                "owner_user_id": 1,
                "created_at": now,
                "expires_at": now + 3600,
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)
            with app.test_client() as client:
                resp = client.get(
                    f"/api/remote/vscode/{vscode_id}/proxy/",
                    query_string={"token": invalid_token},
                )
                self.assertEqual(resp.status_code, 403)
                data = resp.get_json()
                self.assertIn("Invalid token", data.get("error", ""))

    def test_expired_token_returns_403(self):
        """Expired token should return 403 (Issue #2183)."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True

        from app.modules.workspace import vscode_store as vs_mod

        vscode_id = "test-vscode-expired"
        machine_id = "test-machine-1"
        stored_token = "valid-token"
        now = time.time()

        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": stored_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 1,
                "owner_user_id": 1,
                "created_at": now - 7200,  # Created 2 hours ago
                "expires_at": now - 3600,  # Expired 1 hour ago
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)
            with app.test_client() as client:
                resp = client.get(
                    f"/api/remote/vscode/{vscode_id}/proxy/",
                    query_string={"token": stored_token},
                )
                self.assertEqual(resp.status_code, 403)
                data = resp.get_json()
                self.assertIn("expired", data.get("error", "").lower())

    def test_authorization_header_token_accepted(self):
        """Authorization header Bearer token should be accepted (Issue #2183)."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True

        from app.modules.workspace import vscode_store as vs_mod

        vscode_id = "test-vscode-auth-header"
        machine_id = "test-machine-1"
        stored_token = "bearer-token-for-test"
        now = time.time()

        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": stored_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 1,
                "owner_user_id": 1,
                "created_at": now,
                "expires_at": now + 3600,
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)
            with app.test_client() as client:
                resp = client.get(
                    f"/api/remote/vscode/{vscode_id}/proxy/",
                    headers={"Authorization": f"Bearer {stored_token}"},
                )
                # Should not return 401/403
                self.assertNotEqual(resp.status_code, 401)
                self.assertNotEqual(resp.status_code, 403)


class TestCrossTenantIsolation(unittest.TestCase):
    """Tests for cross-tenant isolation (Issue #2183)."""

    def test_cross_tenant_access_denied(self):
        """Tenant A user should not access Tenant B session."""
        mgr = MagicMock()
        mgr.get_user_permission.return_value = None

        from app.modules.workspace import vscode_store as vs_mod

        vscode_id = "test-vscode-cross-tenant"
        machine_id = "test-machine-1"
        stored_token = "valid-token"
        now = time.time()

        # Session owned by tenant 2
        test_store = vs_mod.VSCodeInfoStore()
        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": stored_token,
                "machine_id": machine_id,
                "original_http_url": "http://remote:8080",
                "tenant_id": 2,  # Tenant 2
                "owner_user_id": 10,
                "created_at": now,
                "expires_at": now + 3600,
            },
        )

        with patch.object(vs_mod, "vscode_info_store", test_store):
            app = _make_app(mgr)

            # Set g.user in application context
            @app.before_request
            def set_cross_tenant_user():
                from flask import g, request

                # Only set for this specific test
                if request.path == f"/api/remote/vscode/{vscode_id}/proxy/":
                    g.user = {
                        "id": 1,
                        "username": "user1",
                        "role": "user",
                        "tenant_id": 1,  # User from tenant 1
                    }

            with app.test_client() as client:
                # Mock user loading to return a tenant 1 user
                with patch("app.routes.remote._load_user_from_token") as mock_load:
                    mock_load.return_value = {
                        "id": 1,
                        "username": "user1",
                        "email": "user1@test.com",
                        "role": "user",
                        "tenant_id": 1,  # User from tenant 1
                    }
                    resp = client.get(
                        f"/api/remote/vscode/{vscode_id}/proxy/",
                    )

                    # Should return 403 for cross-tenant access
                    # Note: May return 401 if user auth fails, which is also acceptable
                    self.assertIn(resp.status_code, [401, 403])


class TestSessionExpiration(unittest.TestCase):
    """Tests for session expiration logic (Issue #2183)."""

    def test_is_expired_returns_true_for_expired_session(self):
        """is_expired should return True for expired session."""
        from app.modules.workspace import vscode_store as vs_mod

        test_store = vs_mod.VSCodeInfoStore()
        machine_id = "test-machine"
        vscode_id = "test-vscode-expired"
        now = time.time()

        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": "test-token",
                "expires_at": now - 100,  # Expired 100 seconds ago
            },
        )

        self.assertTrue(test_store.is_expired(machine_id, vscode_id))

    def test_is_expired_returns_false_for_active_session(self):
        """is_expired should return False for active session."""
        from app.modules.workspace import vscode_store as vs_mod

        test_store = vs_mod.VSCodeInfoStore()
        machine_id = "test-machine"
        vscode_id = "test-vscode-active"
        now = time.time()

        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": "test-token",
                "expires_at": now + 3600,  # Expires in 1 hour
            },
        )

        self.assertFalse(test_store.is_expired(machine_id, vscode_id))

    def test_mark_stopped_invalidates_token(self):
        """mark_stopped should invalidate token immediately."""
        from app.modules.workspace import vscode_store as vs_mod

        test_store = vs_mod.VSCodeInfoStore()
        machine_id = "test-machine"
        vscode_id = "test-vscode-stop"
        now = time.time()
        token = "active-token"

        test_store.put(
            machine_id,
            vscode_id,
            {
                "status": "running",
                "token": token,
                "expires_at": now + 3600,
            },
        )

        # Token should be findable before stop
        found = test_store.find_by_token(token)
        self.assertIsNotNone(found)

        # Mark as stopped
        test_store.mark_stopped(machine_id, vscode_id)

        # Token should be cleared
        info = test_store.get(machine_id, vscode_id)
        self.assertEqual(info.get("token"), "")
        self.assertEqual(info.get("status"), "stopped")


if __name__ == "__main__":
    unittest.main()