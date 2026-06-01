#!/usr/bin/env python3
"""
Unit tests for remote VSCode (code-server) proxy endpoints (Issue #610).

Tests cover:
- POST /api/remote/vscode/start   -- start code-server on remote machine
- POST /api/remote/vscode/stop    -- stop code-server
- GET  /api/remote/vscode/<id>/status  -- get status
- POST /api/remote/vscode/<id>/attach  -- re-attach
- GET  /api/remote/vscode/<id>/proxy/  -- HTTP reverse proxy
- GET  /api/remote/vscode/<id>/ws      -- WebSocket fallback (returns 400)
- vscode_status message handler in agent_message()
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _mock_load_user(token):
    """Parse test tokens like 'test-token-<uid>-<role>' into user dicts."""
    if not token:
        return None
    if token.startswith("test-token-"):
        parts = token.split("-")
        if len(parts) >= 4:
            return {
                "id": int(parts[2]),
                "username": f"user{parts[2]}",
                "email": f"user{parts[2]}@test.com",
                "role": parts[3],
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


def _auth_post(client, url, token, **kwargs):
    client.set_cookie("session_token", token)
    return client.post(url, **kwargs)


def _auth_get(client, url, token):
    client.set_cookie("session_token", token)
    return client.get(url)


# ---------------------------------------------------------------------------
# VSCode Start  POST /api/remote/vscode/start
# ---------------------------------------------------------------------------


class TestVSCodeStart(unittest.TestCase):
    """Tests for POST /api/remote/vscode/start."""

    def test_auth_required(self):
        """No session token returns 401."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/vscode/start",
                json={"machine_id": "m1", "project_path": "/tmp"},
            )
            self.assertEqual(resp.status_code, 401)

    def test_missing_machine_id(self):
        """Missing machine_id returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-1-admin",
                json={"project_path": "/tmp"},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertFalse(data["success"])
            self.assertIn("machine_id", data["error"])

    def test_missing_project_path(self):
        """Missing project_path returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-1-admin",
                json={"machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertFalse(data["success"])
            self.assertIn("project_path", data["error"])

    def test_missing_both_params(self):
        """Missing both machine_id and project_path returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-1-admin",
                json={},
            )
            self.assertEqual(resp.status_code, 400)

    def test_access_denied_non_admin(self):
        """Non-admin user without machine access gets 403."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = False
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-2-user",
                json={"machine_id": "m1", "project_path": "/tmp"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_agent_not_connected(self):
        """Agent not connected returns 503."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        mgr.is_agent_connected.return_value = False
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-1-admin",
                json={"machine_id": "m1", "project_path": "/tmp"},
            )
            self.assertEqual(resp.status_code, 503)
            data = resp.get_json()
            self.assertFalse(data["success"])
            self.assertIn("not connected", data["error"].lower())

    def test_successful_start(self):
        """Successful start returns vscode_id and pending status."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        mgr.is_agent_connected.return_value = True
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-1-admin",
                json={"machine_id": "m1", "project_path": "/home/user/project"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "pending")
            self.assertIn("vscode_id", data)
            self.assertTrue(len(data["vscode_id"]) > 0)

            # Verify command was sent to the agent
            mgr.send_command.assert_called_once()
            call_args = mgr.send_command.call_args
            cmd = call_args[0][1]
            self.assertEqual(cmd["command"], "start_vscode")
            self.assertEqual(cmd["project_path"], "/home/user/project")

    def test_successful_start_non_admin_with_access(self):
        """Non-admin user with machine access can start vscode."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        mgr.is_agent_connected.return_value = True
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/start",
                "test-token-5-user",
                json={"machine_id": "m1", "project_path": "/tmp"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "pending")


# ---------------------------------------------------------------------------
# VSCode Stop  POST /api/remote/vscode/stop
# ---------------------------------------------------------------------------


class TestVSCodeStop(unittest.TestCase):
    """Tests for POST /api/remote/vscode/stop."""

    def test_auth_required(self):
        """No session token returns 401."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/vscode/stop",
                json={"vscode_id": "v1", "machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 401)

    def test_missing_vscode_id(self):
        """Missing vscode_id returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/stop",
                "test-token-1-admin",
                json={"machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertFalse(data["success"])

    def test_missing_machine_id(self):
        """Missing machine_id returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/stop",
                "test-token-1-admin",
                json={"vscode_id": "v1"},
            )
            self.assertEqual(resp.status_code, 400)

    def test_missing_both_params(self):
        """Missing both params returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/stop",
                "test-token-1-admin",
                json={},
            )
            self.assertEqual(resp.status_code, 400)

    def test_access_denied_non_admin(self):
        """Non-admin without access gets 403."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = False
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/stop",
                "test-token-2-user",
                json={"vscode_id": "v1", "machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_successful_stop(self):
        """Successful stop sends command and cleans up store."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        app = _make_app(mgr)

        import app.modules.workspace.vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        try:
            with app.test_client() as client:
                resp = _auth_post(
                    client,
                    "/api/remote/vscode/stop",
                    "test-token-1-admin",
                    json={"vscode_id": "vscode-123", "machine_id": "m1"},
                )
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

        # Verify stop command was sent
        mgr.send_command.assert_called_once()
        call_args = mgr.send_command.call_args
        cmd = call_args[0][1]
        self.assertEqual(cmd["command"], "stop_vscode")
        self.assertEqual(cmd["vscode_id"], "vscode-123")

        # Verify store cleanup was called
        mock_store.pop.assert_called_once_with("m1", "vscode-123")


# ---------------------------------------------------------------------------
# VSCode Status  GET /api/remote/vscode/<vscode_id>/status
# ---------------------------------------------------------------------------


class TestVSCodeStatus(unittest.TestCase):
    """Tests for GET /api/remote/vscode/<vscode_id>/status."""

    def test_auth_required(self):
        """No session token returns 401."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = client.get("/api/remote/vscode/vs1/status")
            self.assertEqual(resp.status_code, 401)

    def test_unknown_vscode_id_returns_unknown(self):
        """Unknown vscode_id returns status=unknown."""
        mgr = MagicMock()
        app = _make_app(mgr)

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        vs_mod.vscode_info_store = MagicMock()
        vs_mod.vscode_info_store.find_by_vscode_id.return_value = None

        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client, "/api/remote/vscode/vs-unknown/status", "test-token-1-admin"
                )
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "unknown")

    def test_running_status_returns_proxy_url(self):
        """Running status returns proxy URL with token."""
        mgr = MagicMock()
        app = _make_app(mgr)

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        mock_store.find_by_vscode_id.return_value = (
            "m1",
            {
                "status": "running",
                "token": "browser-secret-token",
                "original_http_url": "http://remote:8080",
            },
        )
        vs_mod.vscode_info_store = mock_store

        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs-running/status",
                    "test-token-1-admin",
                )
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "running")
        self.assertTrue(data["url"].startswith("http://localhost/api/remote/vscode/"))
        self.assertIn("/proxy/", data["url"])
        self.assertIn("browser-secret-token", data["url"])

    def test_error_status_returns_error_message(self):
        """Error status returns the error message."""
        mgr = MagicMock()
        app = _make_app(mgr)

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        mock_store.find_by_vscode_id.return_value = (
            "m1",
            {
                "status": "error",
                "error": "code-server crashed",
            },
        )
        vs_mod.vscode_info_store = mock_store

        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs-errored/status",
                    "test-token-1-admin",
                )
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "code-server crashed")

    def test_access_denied_for_non_admin(self):
        """Non-admin without machine access gets 403."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = False
        app = _make_app(mgr)

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        mock_store.find_by_vscode_id.return_value = (
            "m1",
            {"status": "running", "token": "tok"},
        )
        vs_mod.vscode_info_store = mock_store

        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs-1/status",
                    "test-token-5-user",
                )
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# VSCode Attach  POST /api/remote/vscode/<vscode_id>/attach
# ---------------------------------------------------------------------------


class TestVSCodeAttach(unittest.TestCase):
    """Tests for POST /api/remote/vscode/<vscode_id>/attach."""

    def test_auth_required(self):
        """No session token returns 401."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/vscode/vs1/attach",
                json={"machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 401)

    def test_missing_machine_id(self):
        """Missing machine_id returns 400."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/vs1/attach",
                "test-token-1-admin",
                json={},
            )
            self.assertEqual(resp.status_code, 400)

    def test_access_denied(self):
        """Non-admin without access gets 403."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = False
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/vs1/attach",
                "test-token-5-user",
                json={"machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_successful_attach(self):
        """Successful attach sends attach_vscode command."""
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_post(
                client,
                "/api/remote/vscode/vs-123/attach",
                "test-token-1-admin",
                json={"machine_id": "m1"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])

            mgr.send_command.assert_called_once()
            call_args = mgr.send_command.call_args
            cmd = call_args[0][1]
            self.assertEqual(cmd["command"], "attach_vscode")
            self.assertEqual(cmd["vscode_id"], "vs-123")


# ---------------------------------------------------------------------------
# VSCode Proxy  GET /api/remote/vscode/<vscode_id>/proxy/
# ---------------------------------------------------------------------------


class TestVSCodeProxy(unittest.TestCase):
    """Tests for GET /api/remote/vscode/<vscode_id>/proxy/."""

    def _make_app_with_store(self, mgr, store_find_result=None):
        """Create app and swap vscode_info_store."""
        app = _make_app(mgr)

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        mock_store.find_by_vscode_id.return_value = store_find_result
        vs_mod.vscode_info_store = mock_store

        return app, vs_mod, original_store, mock_store

    def _restore_store(self, vs_mod, original_store):
        vs_mod.vscode_info_store = original_store

    def test_proxy_does_not_require_session_cookie(self):
        """Proxy auth is handled by vscode token, not Open ACE session cookie."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = client.get("/api/remote/vscode/vs1/proxy/")
            self.assertEqual(resp.status_code, 404)

    def test_unknown_vscode_id_returns_404(self):
        """Unknown vscode_id returns 404."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(mgr, None)
        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs-unknown/proxy/?token=abc",
                    "test-token-1-admin",
                )
        finally:
            self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn("not found", data["error"].lower())

    def test_missing_token_uses_running_session_fallback(self):
        """Running sessions may proxy resource requests after initial load."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "running",
                    "token": "valid-token",
                    "original_http_url": "http://remote:8080",
                },
            ),
        )

        def _gen():
            yield b"<h1>VSCode</h1>"

        mock_proxy_result = (200, {"Content-Type": "text/html"}, _gen())

        with (
            patch(
                "app.modules.workspace.vscode_proxy.build_target_url",
                return_value="http://remote:8080/",
            ),
            patch(
                "app.modules.workspace.vscode_proxy.proxy_request_streaming",
                return_value=mock_proxy_result,
            ),
        ):
            try:
                with app.test_client() as client:
                    resp = client.get("/api/remote/vscode/vs1/proxy/")
            finally:
                self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 200)

    def test_invalid_token_returns_403(self):
        """Wrong token returns 403."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "running",
                    "token": "correct-token",
                    "original_http_url": "http://remote:8080",
                },
            ),
        )
        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs1/proxy/?token=wrong-token",
                    "test-token-1-admin",
                )
        finally:
            self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("invalid token", data["error"].lower())

    def test_not_running_returns_503(self):
        """VSCode session that is not running returns 503."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "pending",
                    "token": "valid-token",
                },
            ),
        )
        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs1/proxy/?token=valid-token",
                    "test-token-1-admin",
                )
        finally:
            self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertIn("not running", data["error"].lower())

    def test_no_stored_token_returns_500(self):
        """Info record with empty stored token returns server error."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "running",
                    "token": "",  # empty stored token
                },
            ),
        )
        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs1/proxy/?token=something",
                    "test-token-1-admin",
                )
        finally:
            self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 500)

    def test_successful_proxy(self):
        """Valid token with running session proxies the request."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "running",
                    "token": "valid-token",
                    "original_http_url": "http://remote:8080",
                },
            ),
        )

        def _gen():
            yield b"<h1>VSCode</h1>"

        mock_proxy_result = (200, {"Content-Type": "text/html"}, _gen())

        with (
            patch(
                "app.modules.workspace.vscode_proxy.build_target_url",
                return_value="http://remote:8080/",
            ),
            patch(
                "app.modules.workspace.vscode_proxy.proxy_request_streaming",
                return_value=mock_proxy_result,
            ),
        ):
            try:
                with app.test_client() as client:
                    resp = _auth_get(
                        client,
                        "/api/remote/vscode/vs1/proxy/?token=valid-token",
                        "test-token-1-admin",
                    )
            finally:
                self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 200)

    def test_no_original_http_url_returns_500(self):
        """Running session without original_http_url returns 500."""
        mgr = MagicMock()
        app, vs_mod, orig, mock_store = self._make_app_with_store(
            mgr,
            (
                "m1",
                {
                    "status": "running",
                    "token": "valid-token",
                    "original_http_url": "",  # empty
                },
            ),
        )
        try:
            with app.test_client() as client:
                resp = _auth_get(
                    client,
                    "/api/remote/vscode/vs1/proxy/?token=valid-token",
                    "test-token-1-admin",
                )
        finally:
            self._restore_store(vs_mod, orig)

        self.assertEqual(resp.status_code, 500)


# ---------------------------------------------------------------------------
# VSCode WebSocket fallback  GET /api/remote/vscode/<vscode_id>/ws
# ---------------------------------------------------------------------------


class TestVSCodeWs(unittest.TestCase):
    """Tests for GET /api/remote/vscode/<vscode_id>/ws."""

    def test_non_ws_request_returns_400(self):
        """Non-WebSocket request returns 400 with error message."""
        mgr = MagicMock()
        app = _make_app(mgr)
        with app.test_client() as client:
            resp = _auth_get(
                client,
                "/api/remote/vscode/vs1/ws",
                "test-token-1-admin",
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("websocket", data["error"].lower())


# ---------------------------------------------------------------------------
# vscode_status message handler in agent_message()
# ---------------------------------------------------------------------------


class TestVSCodeStatusMessageHandler(unittest.TestCase):
    """Tests for the vscode_status message type in agent_message()."""

    def _call_agent_message(self, mgr, payload):
        """Call agent_message with a mocked vscode_info_store."""
        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    resp, status = result
                    return resp.get_json(), status
                return result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

    def test_running_status_stores_info(self):
        """Running status stores info with browser token."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            "vscode_id": "vs-123",
            "status": "running",
            "http_url": "http://remote:8080",
            "token": "original-vscode-token",
            "project_path": "/home/user/project",
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

        # Verify store.put was called with correct args
        mock_store.put.assert_called_once()
        call_args = mock_store.put.call_args
        self.assertEqual(call_args[0][0], "m1")  # machine_id
        self.assertEqual(call_args[0][1], "vs-123")  # vscode_id
        stored_info = call_args[0][2]
        self.assertEqual(stored_info["status"], "running")
        self.assertEqual(stored_info["original_http_url"], "http://remote:8080")
        self.assertEqual(stored_info["original_token"], "original-vscode-token")
        self.assertEqual(stored_info["machine_id"], "m1")
        self.assertEqual(stored_info["project_path"], "/home/user/project")
        # A browser token should be generated (64 hex chars from secrets.token_hex(32))
        self.assertEqual(len(stored_info["token"]), 64)

    def test_stopped_status_cleans_up(self):
        """Stopped status removes the store entry."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            "vscode_id": "vs-123",
            "status": "stopped",
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

        # Verify store.pop was called to clean up
        mock_store.pop.assert_called_once_with("m1", "vs-123")

    def test_error_status_stores_error(self):
        """Error status stores the error message."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            "vscode_id": "vs-123",
            "status": "error",
            "error": "port already in use",
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

        mock_store.put.assert_called_once()
        call_args = mock_store.put.call_args
        self.assertEqual(call_args[0][0], "m1")
        self.assertEqual(call_args[0][1], "vs-123")
        stored_info = call_args[0][2]
        self.assertEqual(stored_info["status"], "error")
        self.assertEqual(stored_info["error"], "port already in use")
        self.assertEqual(stored_info["machine_id"], "m1")

    def test_not_found_status_cleans_up(self):
        """not_found status removes the store entry."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            "vscode_id": "vs-123",
            "status": "not_found",
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

        mock_store.pop.assert_called_once_with("m1", "vs-123")

    def test_missing_vscode_id_returns_success(self):
        """Missing vscode_id returns success without storing anything."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            # No vscode_id
            "status": "running",
            "http_url": "http://remote:8080",
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        # Store should not be called
        mock_store.put.assert_not_called()
        mock_store.pop.assert_not_called()

    def test_running_without_http_url_skips_store(self):
        """Running status without http_url does not store (http_url is required)."""
        mgr = MagicMock()

        payload = {
            "type": "vscode_status",
            "machine_id": "m1",
            "vscode_id": "vs-123",
            "status": "running",
            # No http_url
        }

        from app.modules.workspace import vscode_store as vs_mod

        original_store = vs_mod.vscode_info_store
        mock_store = MagicMock()
        vs_mod.vscode_info_store = mock_store

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = mgr

        from flask import Flask

        from app.routes import remote as remote_mod

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        try:
            with app.test_request_context(
                "/api/remote/agent/message",
                method="POST",
                json=payload,
            ):
                result = remote_mod.agent_message()
                if isinstance(result, tuple):
                    data, status = result[0].get_json(), result[1]
                else:
                    data, status = result.get_json(), result.status_code
        finally:
            vs_mod.vscode_info_store = original_store

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        # The code checks `if status == "running" and http_url:` so empty url = skip
        mock_store.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
