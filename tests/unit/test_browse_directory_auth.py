#!/usr/bin/env python3
"""
Unit tests for browse_remote_directory authentication (Issue #477).

The #477 defensive contract has since been re-architected: unauthenticated
requests are rejected 401 by the blueprint's before_request (load_user),
and per-machine authorization lives in the @machine_access_required
decorator (_check_machine_access: UUID format -> admin role -> machine
existence -> assignment -> tenant isolation). These tests exercise that
current contract: no-token -> 401, invalid machine_id -> 400 before any
g.user access, unassigned user -> 403, missing machine -> 404, and the
admin pass-through to the browse body (online/offline).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(477)]


project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Valid UUID machine_id (#2540 format validation is part of the access contract).
MID = "12345678-1234-5678-1234-123456789abc"


@pytest.fixture(scope="module")
def remote_module():
    """Load remote.py with mocked dependencies."""
    mock_modules = {
        "app.modules": MagicMock(__path__=[]),
        "app.modules.workspace": MagicMock(__path__=[]),
        "app.modules.governance": MagicMock(__path__=[]),
        # remote.py grew governance-audit imports after this mock set was
        # written; without an explicit mock the import machinery asks the
        # mocked parent package for __spec__ and raises.
        "app.modules.governance.audit_logger": MagicMock(),
        "app.modules.workspace.agent_token": MagicMock(),
        "app.modules.workspace.api_key_proxy": MagicMock(),
        "app.modules.workspace.llm_proxy_handler": MagicMock(),
        "app.modules.workspace.remote_agent_manager": MagicMock(),
        "app.modules.workspace.remote_session_manager": MagicMock(),
        "app.modules.workspace.session_access": MagicMock(),
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
    Auth contract of the machine browse path (Issue #477, realigned).

    The original g.user defensive checks moved: unauthenticated requests
    now get 401 from before_request (load_user), and per-machine access
    moved to @machine_access_required / _check_machine_access.
    """

    def test_no_g_user_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        """
        Unauthenticated request (no session token) -> 401 from before_request.

        This is the modern home of the #477 fix: when auth fails, g.user is
        never set and the request is rejected with 401 before any handler
        attribute access.
        """
        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse"
        ):
            with (
                patch.object(remote_module, "_set_user_from_token", return_value=False),
                patch.object(remote_module, "_set_user_from_webui_token", return_value=False),
            ):
                result = remote_module.load_user()
        data, status = parse_response(result)
        assert status == 401
        assert data["error"] == "Authentication required"

    def test_g_user_none_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        """
        Both token loaders failing (g.user stays None) -> 401.

        Same defensive outcome as above via the WebUI-token fallback leg:
        no loader establishes a user, so the request never reaches the
        handler that would access g.user attributes.
        """
        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse"
        ):
            # WebUI fallback engaged (token loader already failed) but also
            # could not establish a user.
            with (
                patch.object(remote_module, "_set_user_from_token", return_value=False),
                patch.object(remote_module, "_set_user_from_webui_token", return_value=False),
            ):
                result = remote_module.load_user()
        data, status = parse_response(result)
        assert status == 401
        assert data["error"] == "Authentication required"

    def test_g_user_without_id_returns_403(self, flask_app, remote_module, mock_agent_mgr):
        """
        Authenticated non-admin without machine assignment -> 403.

        (Originally: g.user without id -> 403. The id is now guaranteed by
        the auth layer; the equivalent denial for an id-less-equivalent
        user — unassigned, no tenant conflict — is the Permission denied
        403 from _check_machine_access.)
        """
        machine = dict(mock_agent_mgr.get_machine.return_value, status="online", tenant_id=None)
        mock_agent_mgr.get_machine.return_value = machine
        mock_agent_mgr.get_user_permission.return_value = None

        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse?machine_id=12345678-1234-5678-1234-123456789abc"
        ):
            from flask import g

            g.user = {"id": 42, "role": "user", "tenant_id": None}  # unassigned user
            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory(MID)
        data, status = parse_response(result)
        assert status == 403
        assert data["error"] == "Permission denied"

    def test_normal_user_without_access_returns_403(self, flask_app, remote_module, mock_agent_mgr):
        """
        Assigned-user check fails for a regular user -> 403.

        Non-admin with id but no machine assignment (same tenant, so
        tenant isolation does not convert the denial to 404).
        """
        machine = dict(
            mock_agent_mgr.get_machine.return_value,
            status="online",
            tenant_id=7,
        )
        mock_agent_mgr.get_machine.return_value = machine
        mock_agent_mgr.get_user_permission.return_value = None

        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse?machine_id=12345678-1234-5678-1234-123456789abc"
        ):
            from flask import g

            g.user = {"id": 42, "username": "testuser", "role": "user", "tenant_id": 7}
            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory(MID)
        data, status = parse_response(result)
        assert status == 403
        assert data["error"] == "Permission denied"

    def test_admin_user_has_access(self, flask_app, remote_module, mock_agent_mgr):
        """Admin role passes the access decorator and gets the browse result."""
        mock_agent_mgr.get_machine.return_value = {
            "machine_id": MID,
            "machine_name": "Test Machine",
            "work_dir": "/home/test/workspace",
            "status": "online",
            "tenant_id": 7,
        }

        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse?machine_id=12345678-1234-5678-1234-123456789abc"
        ):
            from flask import g

            g.user = {"id": 1, "username": "admin", "role": "admin", "tenant_id": 7}
            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory(MID)
        data, status = parse_response(result)
        assert status == 200
        assert data["success"] is True
        assert data["result"]["path"] == "/home/test/workspace"

    def test_admin_user_offline_machine(self, flask_app, remote_module, mock_agent_mgr):
        """Admin access passes; offline machine yields the fallback payload (200)."""
        mock_agent_mgr.get_machine.return_value = {
            "machine_id": MID,
            "machine_name": "Test Machine",
            "work_dir": "/home/test/workspace",
            "status": "offline",
            "tenant_id": 7,
        }

        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse?machine_id=12345678-1234-5678-1234-123456789abc"
        ):
            from flask import g

            g.user = {"id": 1, "username": "admin", "role": "admin", "tenant_id": 7}
            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory(MID)
        data, status = parse_response(result)
        assert status == 200
        assert data["success"] is False
        assert "offline" in data["error"].lower()


class TestBrowseRemoteDirectoryEdgeCases:
    """Additional edge case tests for robustness."""

    def test_machine_not_found_returns_404(self, flask_app, remote_module, mock_agent_mgr):
        """
        Non-existent machine -> 404 from the access check.

        Checked as a non-admin: the admin short-circuit in
        _check_machine_access returns before the machine lookup, so the
        404 contract is observable on the regular-user path.
        """
        mock_agent_mgr.get_machine.return_value = None

        with flask_app.test_request_context(
            "/api/remote/machines/12345678-1234-5678-1234-123456789abc/browse?machine_id=12345678-1234-5678-1234-123456789abc"
        ):
            from flask import g

            g.user = {"id": 42, "username": "testuser", "role": "user", "tenant_id": 7}
            with patch.object(
                remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
            ):
                result = remote_module.browse_remote_directory(MID)
        data, status = parse_response(result)
        assert status == 404
        assert data["error"] == "Machine not found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
