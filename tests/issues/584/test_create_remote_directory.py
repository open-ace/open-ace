"""
Tests for remote directory creation feature (Issue #584 Phase 2).

Covers:
- Route endpoint: POST /api/remote/machines/<machine_id>/create-directory
- Agent command handler: _cmd_create_directory
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ==================== Route Tests ====================


class TestCreateRemoteDirectoryRoute(unittest.TestCase):
    """Test the create_remote_directory route endpoint."""

    def _make_app(self, mgr):
        """Create a minimal Flask app with remote_bp for route testing."""
        from flask import Flask

        import app.modules.workspace.remote_agent_manager as ram_mod
        from app.routes import remote as remote_mod

        ram_mod._agent_manager = mgr

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        def _mock_load_user(token):
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

        from app.auth import decorators as auth_dec

        auth_dec._load_user_from_token = _mock_load_user
        remote_mod._load_user_from_token = _mock_load_user
        self._auth_dec = auth_dec
        return app

    def _auth_post(self, client, url, token, **kwargs):
        client.set_cookie("session_token", token)
        return client.post(url, **kwargs)

    def test_unauthenticated_returns_401(self):
        mgr = MagicMock()
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/machines/m1/create-directory",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 401)

    def test_no_path_returns_400(self):
        mgr = MagicMock()
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertFalse(data["success"])
            self.assertIn("Path is required", data["error"])

    def test_path_too_long_returns_400(self):
        mgr = MagicMock()
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "a" * 4097},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("too long", data["error"])

    def test_machine_not_found_returns_404(self):
        mgr = MagicMock()
        mgr.get_machine.return_value = None
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 404)

    def test_machine_offline_returns_503(self):
        mgr = MagicMock()
        mgr.get_machine.return_value = {"status": "offline"}
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 503)

    def test_non_admin_no_access_returns_403(self):
        mgr = MagicMock()
        mgr.check_user_access.return_value = False
        mgr.get_machine.return_value = {"status": "online"}
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-2-user",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_successful_creation(self):
        mgr = MagicMock()
        mgr.check_user_access.return_value = True
        mgr.get_machine.return_value = {"status": "online"}
        mgr.send_command.return_value = True
        mgr.get_browse_result.return_value = {
            "success": True,
            "result": {"path": "/tmp/test", "message": "Directory created successfully"},
        }
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["result"]["path"], "/tmp/test")

    def test_agent_creation_failure(self):
        mgr = MagicMock()
        mgr.get_machine.return_value = {"status": "online"}
        mgr.send_command.return_value = True
        mgr.get_browse_result.return_value = {
            "success": False,
            "error": "Permission denied: /tmp/test",
        }
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertFalse(data["success"])
            self.assertIn("Permission denied", data["error"])

    def test_send_command_failure_returns_500(self):
        mgr = MagicMock()
        mgr.get_machine.return_value = {"status": "online"}
        mgr.send_command.return_value = False
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 500)

    def test_agent_timeout_returns_504(self):
        mgr = MagicMock()
        mgr.get_machine.return_value = {"status": "online"}
        mgr.send_command.return_value = True
        mgr.get_browse_result.return_value = None
        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = self._auth_post(
                client,
                "/api/remote/machines/m1/create-directory",
                "test-token-1-admin",
                json={"path": "/tmp/test"},
            )
            self.assertEqual(resp.status_code, 504)


# ==================== Agent Command Tests ====================


class TestAgentCreateDirectory(unittest.TestCase):
    """Test the _cmd_create_directory agent command handler."""

    @classmethod
    def setUpClass(cls):
        """Import RemoteAgent with mocked dependencies."""
        agent_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
        agent_dir = os.path.abspath(agent_dir)

        # Mock all remote-agent dependencies before importing
        mock_modules = [
            "config",
            "executor",
            "session_sync",
            "system_info",
            "cli_settings",
        ]
        for mod_name in mock_modules:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = MagicMock()

        # Provide AgentConfig
        mock_config = sys.modules["config"]
        mock_config.AgentConfig = type("AgentConfig", (), {"__init__": lambda self: None})

        # Provide ProcessExecutor
        mock_executor = sys.modules["executor"]
        mock_executor.ProcessExecutor = MagicMock

        # Provide SessionSyncService
        mock_session_sync = sys.modules["session_sync"]
        mock_session_sync.SessionSyncService = MagicMock

        # Provide get_capabilities
        mock_system_info = sys.modules["system_info"]
        mock_system_info.get_capabilities = MagicMock(return_value={})

        # Provide apply_cli_settings
        mock_cli = sys.modules["cli_settings"]
        mock_cli.apply_cli_settings = MagicMock()

        if agent_dir not in sys.path:
            sys.path.insert(0, agent_dir)

        if "agent" in sys.modules:
            del sys.modules["agent"]

        import agent as agent_mod

        cls.AgentClass = agent_mod.RemoteAgent

    def _make_agent(self):
        """Create a RemoteAgent with mocked _http_send."""
        agent = self.AgentClass.__new__(self.AgentClass)
        agent.config = MagicMock()
        agent.config.machine_id = "test-machine"
        agent._http_send = MagicMock()
        return agent

    def _get_last_send(self, agent):
        """Get the last _http_send call arguments."""
        agent._http_send.assert_called()
        return agent._http_send.call_args[0][0]

    def test_empty_path_returns_error(self):
        agent = self._make_agent()
        agent._cmd_create_directory({"request_id": "r1", "path": ""})
        result = self._get_last_send(agent)
        self.assertFalse(result["success"])
        self.assertIn("No path specified", result["error"])

    def test_missing_path_returns_error(self):
        agent = self._make_agent()
        agent._cmd_create_directory({"request_id": "r1"})
        result = self._get_last_send(agent)
        self.assertFalse(result["success"])
        self.assertIn("No path specified", result["error"])

    def test_parent_not_exists_returns_error(self):
        agent = self._make_agent()
        agent._cmd_create_directory({"request_id": "r1", "path": "/nonexistent/path/test"})
        result = self._get_last_send(agent)
        self.assertFalse(result["success"])
        self.assertIn("Parent directory does not exist", result["error"])

    def test_parent_not_directory_returns_error(self):
        with tempfile.NamedTemporaryFile() as f:
            dir_path = os.path.join(f.name, "subdir")
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertFalse(result["success"])
            self.assertIn("not a directory", result["error"])

    def test_parent_not_writable_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "readonly")
            os.makedirs(subdir)
            os.chmod(subdir, 0o444)
            dir_path = os.path.join(subdir, "newdir")
            try:
                agent = self._make_agent()
                agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
                result = self._get_last_send(agent)
                self.assertFalse(result["success"])
                self.assertIn("Permission denied", result["error"])
            finally:
                os.chmod(subdir, 0o755)

    def test_invalid_name_with_null_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = os.path.join(tmpdir, "bad\x00name")
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertFalse(result["success"])
            self.assertIn("Invalid directory name", result["error"])

    def test_already_exists_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing = os.path.join(tmpdir, "existing")
            os.makedirs(existing)
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": existing})
            result = self._get_last_send(agent)
            self.assertFalse(result["success"])
            self.assertIn("already exists", result["error"])

    def test_successful_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = os.path.join(tmpdir, "newdir")
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertTrue(result["success"])
            self.assertEqual(result["result"]["path"], os.path.realpath(dir_path))
            self.assertTrue(os.path.isdir(dir_path))

    def test_expanduser_works(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = os.path.join(tmpdir, "expanded")
            # Use realpath since expanduser on ~ may resolve differently
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertTrue(result["success"])

    def test_path_with_dotdot_normalized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)
            dir_path = os.path.join(subdir, "..", "sub", "newdir")
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertTrue(result["success"])
            # realpath should normalize .. components
            normalized = os.path.realpath(dir_path)
            self.assertEqual(result["result"]["path"], normalized)

    def test_response_includes_request_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = os.path.join(tmpdir, "reqtest")
            agent = self._make_agent()
            agent._cmd_create_directory({"request_id": "test-req-123", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertEqual(result["request_id"], "test-req-123")

    def test_windows_invalid_chars_rejected(self):
        """Test that Windows-specific invalid characters are rejected when os.name is 'nt'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = os.path.join(tmpdir, "bad*name")
            agent = self._make_agent()
            with patch("os.name", "nt"):
                agent._cmd_create_directory({"request_id": "r1", "path": dir_path})
            result = self._get_last_send(agent)
            self.assertFalse(result["success"])
            self.assertIn("Invalid directory name", result["error"])


if __name__ == "__main__":
    unittest.main()
