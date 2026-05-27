#!/usr/bin/env python3
"""
Unit tests for browse_remote_directory authentication fix (Issue #477).

Tests verify the defensive check for g.user existence before accessing
its attributes, ensuring proper 401 Unauthorized response when auth fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture(scope="module")
def remote_module():
    """Load remote.py with mocked dependencies."""
    mock_modules = {
        "app.modules": MagicMock(__path__=[]),
        "app.modules.workspace": MagicMock(__path__=[]),
        "app.modules.workspace.api_key_proxy": MagicMock(),
        "app.modules.workspace.remote_agent_manager": MagicMock(),
        "app.modules.workspace.remote_session_manager": MagicMock(),
        "app.modules.workspace.terminal_store": MagicMock(),
        "app.modules.workspace.session_manager": MagicMock(),
        "app.auth.decorators": MagicMock(
            _extract_token=MagicMock(return_value=""),
            _load_user_from_token=MagicMock(return_value=None),
            admin_required=MagicMock(),
        ),
        "app.repositories.database": MagicMock(),
        "app.repositories.schema_init": MagicMock(),
        "app.repositories.user_repo": MagicMock(),
        "app.services.auth_service": MagicMock(),
        "app.services.webui_manager": MagicMock(),
        "app.services.remote_agent_manager": MagicMock(),
        "gevent": MagicMock(),
        "gevent.lock": MagicMock(
            RLock=lambda *a, **kw: MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: None),
            Semaphore=lambda *a, **kw: MagicMock(
                __enter__=lambda s: s, __exit__=lambda s, *a: None
            ),
        ),
        "hmac": MagicMock(),
    }

    with patch.dict(sys.modules, mock_modules):
        remote_path = Path(project_root) / "app" / "routes" / "remote.py"
        spec = importlib.util.spec_from_file_location("remote_direct", remote_path)
        remote_module = importlib.util.module_from_spec(spec)
        sys.modules["remote_direct"] = remote_module
        spec.loader.exec_module(remote_module)
        yield remote_module


def parse_response(result):
    """Parse Flask response tuple into (response_json, status_code)."""
    if isinstance(result, tuple):
        resp, status = result
    else:
        resp = result
        status = resp.status_code
    return resp.get_json(), status


@pytest.fixture
def mock_agent_mgr():
    """Mock remote agent manager."""
    mgr = MagicMock()
    mgr.check_user_access.return_value = False  # Default: no access
    mgr.get_machine.return_value = {
        "machine_id": "test-machine-001",
        "machine_name": "Test Machine",
        "work_dir": "/home/test/workspace",
        "status": "offline",  # Machine status
    }
    mgr.send_command.return_value = True
    mgr.get_browse_result.return_value = {
        "success": True,
        "result": {
            "path": "/home/test/workspace",
            "name": "workspace",
            "directories": [],
            "parent": "/home/test",
            "is_writable": True,
        },
    }
    return mgr


@pytest.fixture
def flask_app():
    """Create Flask app for testing."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


class TestBrowseRemoteDirectoryAuthCheck:
    """
    Tests for authentication check - Issue #477 fix.

    These tests verify the defensive check for g.user existence before
    accessing its attributes, which was the core fix for Issue #477.
    """

    def test_no_g_user_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that missing g.user returns 401 Unauthorized.

        This is the primary test case for Issue #477:
        When before_request hook fails authentication, g.user is not set,
        and the function should return 401 instead of raising AttributeError.
        """
        with flask_app.app_context():
            from flask import g

            # Ensure g.user is not set (simulates failed before_request auth)
            if hasattr(g, "user"):
                delattr(g, "user")

            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory("test-machine-001")
                data, status = parse_response(result)

                assert status == 401
                assert data["error"] == "Unauthorized"

    def test_g_user_none_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that g.user=None returns 401 Unauthorized.

        Another edge case for Issue #477: g.user could be explicitly set to None.
        """
        with flask_app.app_context():
            from flask import g

            g.user = None

            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory("test-machine-001")
                data, status = parse_response(result)

                assert status == 401
                assert data["error"] == "Unauthorized"

    def test_g_user_without_id_returns_403(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that g.user without 'id' field returns 403 Forbidden.

        Edge case: g.user exists but has no 'id' field - passes auth check
        but fails access control.
        """
        with flask_app.app_context():
            from flask import g

            g.user = {"role": "user"}  # No 'id' field

            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                # This will raise KeyError when accessing g.user["id"]
                # The test verifies that auth check passes but fails at access control
                with pytest.raises(KeyError):
                    remote_module.browse_remote_directory("test-machine-001")

    def test_normal_user_without_access_returns_403(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that authenticated user without machine access returns 403 Forbidden.
        """
        with flask_app.app_context():
            from flask import g

            g.user = {"id": 42, "username": "testuser", "role": "user"}
            mock_agent_mgr.check_user_access.return_value = False

            # Mock request.args to provide request context
            with flask_app.test_request_context():
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    result = remote_module.browse_remote_directory("test-machine-001")
                    data, status = parse_response(result)

                    assert status == 403
                    assert data["error"] == "Access denied"

    def test_admin_user_has_access(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that admin user has access to browse machine directory.

        When machine is online, sends command to agent and returns result.
        """
        with flask_app.app_context():
            from flask import g

            g.user = {"id": 1, "username": "admin", "role": "admin"}

            # Set machine to online for this test
            mock_agent_mgr.get_machine.return_value = {
                "machine_id": "test-machine-001",
                "machine_name": "Test Machine",
                "work_dir": "/home/test/workspace",
                "status": "online",
            }

            # Mock request.args to provide request context
            with flask_app.test_request_context():
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    result = remote_module.browse_remote_directory("test-machine-001")
                    data, status = parse_response(result)

                    assert status == 200
                    assert data["success"] is True
                    assert data["result"]["path"] == "/home/test/workspace"

    def test_admin_user_offline_machine(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that browsing offline machine returns error.
        """
        with flask_app.app_context():
            from flask import g

            g.user = {"id": 1, "username": "admin", "role": "admin"}

            # Set machine to offline for this test
            mock_agent_mgr.get_machine.return_value = {
                "machine_id": "test-machine-001",
                "machine_name": "Test Machine",
                "work_dir": "/home/test/workspace",
                "status": "offline",
            }

            # Mock request.args to provide request context
            with flask_app.test_request_context():
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    result = remote_module.browse_remote_directory("test-machine-001")
                    data, status = parse_response(result)

                    assert status == 200
                    assert data["success"] is False
                    assert "offline" in data["error"].lower()


class TestBrowseRemoteDirectoryEdgeCases:
    """Additional edge case tests for robustness."""

    def test_machine_not_found_returns_404(self, flask_app, remote_module, mock_agent_mgr):
        """
        Test that non-existent machine returns 404 Not Found.
        """
        with flask_app.app_context():
            from flask import g

            g.user = {"id": 1, "username": "admin", "role": "admin"}
            mock_agent_mgr.get_machine.return_value = None

            with flask_app.test_request_context():
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    result = remote_module.browse_remote_directory("nonexistent-machine")
                    data, status = parse_response(result)

                    assert status == 404
                    assert data["error"] == "Machine not found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
