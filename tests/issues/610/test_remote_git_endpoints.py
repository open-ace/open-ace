#!/usr/bin/env python3
"""
Unit tests for remote git proxy endpoints (Issue #610).

Covers:
- GET /api/remote/machines/<machine_id>/git/status
- GET /api/remote/machines/<machine_id>/git/diff
- GET /api/remote/machines/<machine_id>/git/file
- git_result message handler in agent_message()
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def remote_module():
    """Load remote.py with mocked dependencies."""
    mock_modules = {
        "app.modules": MagicMock(__path__=[]),
        "app.modules.workspace": MagicMock(__path__=[]),
        "app.modules.workspace.api_key_proxy": MagicMock(),
        "app.modules.workspace.llm_proxy_handler": MagicMock(),
        "app.modules.workspace.remote_agent_manager": MagicMock(),
        "app.modules.workspace.remote_session_manager": MagicMock(),
        "app.modules.workspace.terminal_store": MagicMock(),
        "app.modules.workspace.session_manager": MagicMock(),
        "app.modules.workspace.vscode_store": MagicMock(),
        "app.modules.workspace.vscode_proxy": MagicMock(),
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
        "app.modules.governance": MagicMock(__path__=[]),
        "gevent": MagicMock(),
        "gevent.lock": MagicMock(
            RLock=lambda *a, **kw: MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: None),
            Semaphore=lambda *a, **kw: MagicMock(
                __enter__=lambda s: s, __exit__=lambda s, *a: None
            ),
        ),
    }

    with patch.dict(sys.modules, mock_modules):
        remote_path = Path(project_root) / "app" / "routes" / "remote.py"
        spec = importlib.util.spec_from_file_location("remote_git_test", remote_path)
        remote_module = importlib.util.module_from_spec(spec)
        sys.modules["remote_git_test"] = remote_module
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
    """Mock remote agent manager with sensible defaults.

    Defaults: machine exists, is connected, agent responds successfully.
    Individual tests override specific return values as needed.
    """
    mgr = MagicMock()
    mgr.check_user_access.return_value = True
    mgr.get_machine.return_value = {
        "machine_id": "machine-001",
        "machine_name": "Test Machine",
        "status": "online",
    }
    mgr.is_agent_connected.return_value = True
    mgr.send_command.return_value = True
    mgr.get_browse_result.return_value = None  # default: timeout
    return mgr


@pytest.fixture
def admin_user():
    return {"id": 1, "username": "admin", "role": "admin"}


@pytest.fixture
def regular_user():
    return {"id": 42, "username": "regular", "role": "user"}


@pytest.fixture
def flask_app():
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


# ===========================================================================
# Tests for remote_git_status
# ===========================================================================


class TestRemoteGitStatus:
    """Tests for GET /machines/<machine_id>/git/status?path=<project_path>"""

    def test_no_g_user_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            from flask import g

            if hasattr(g, "user"):
                delattr(g, "user")

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 401
                    assert data["error"] == "Unauthorized"

    def test_g_user_none_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            from flask import g

            g.user = None

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 401
                    assert data["error"] == "Unauthorized"

    def test_non_admin_no_access_returns_403(
        self, flask_app, remote_module, mock_agent_mgr, regular_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = regular_user
            mock_agent_mgr.check_user_access.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 403
                    assert data["error"] == "Access denied"

    def test_machine_not_found_returns_404(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_machine.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 404
                    assert data["error"] == "Machine not found"

    def test_agent_not_connected_returns_503(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.is_agent_connected.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 503
                    assert data["success"] is False
                    assert "not connected" in data["error"].lower()

    def test_agent_timeout_returns_504(self, flask_app, remote_module, mock_agent_mgr, admin_user):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            # get_browse_result returns None by default (timeout)
            mock_agent_mgr.get_browse_result.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 504
                    assert data["success"] is False
                    assert "timeout" in data["error"].lower()

    def test_missing_path_param_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            # No path parameter
            with flask_app.test_request_context("/"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 400
                    assert data["success"] is False
                    assert "path" in data["error"].lower()

    def test_successful_git_status_response(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            git_files = [
                {"path": "src/main.py", "status": "modified", "staged": False},
                {"path": "src/utils.py", "status": "added", "staged": True},
                {"path": "README.md", "status": "deleted", "staged": False},
            ]
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"branch": "main", "files": git_files},
            }

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 200
                    assert data["success"] is True
                    assert data["result"]["branch"] == "main"
                    assert len(data["result"]["files"]) == 3
                    assert data["result"]["files"][0]["path"] == "src/main.py"

    def test_git_status_sends_correct_command(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"branch": "main", "files": []},
            }

            with flask_app.test_request_context("/?path=/home/user/myproject"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    remote_module.remote_git_status("machine-001")

                    mock_agent_mgr.send_command.assert_called_once()
                    call_args = mock_agent_mgr.send_command.call_args
                    machine_id = call_args[0][0]
                    command = call_args[0][1]

                    assert machine_id == "machine-001"
                    assert command["type"] == "command"
                    assert command["command"] == "git_status"
                    assert command["project_path"] == "/home/user/myproject"
                    assert "request_id" in command

    def test_error_response_git_not_installed(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": False,
                "error": "git is not installed on this machine",
            }

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 200
                    assert data["success"] is False
                    assert "not installed" in data["error"]

    def test_regular_user_with_access_succeeds(
        self, flask_app, remote_module, mock_agent_mgr, regular_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = regular_user
            mock_agent_mgr.check_user_access.return_value = True
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"branch": "develop", "files": []},
            }

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_status("machine-001"))
                    assert status == 200
                    assert data["success"] is True


# ===========================================================================
# Tests for remote_git_diff
# ===========================================================================


class TestRemoteGitDiff:
    """Tests for GET /machines/<machine_id>/git/diff?path=<path>&file=<file>"""

    def test_no_g_user_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            from flask import g

            if hasattr(g, "user"):
                delattr(g, "user")

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 401

    def test_non_admin_no_access_returns_403(
        self, flask_app, remote_module, mock_agent_mgr, regular_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = regular_user
            mock_agent_mgr.check_user_access.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 403
                    assert data["error"] == "Access denied"

    def test_machine_not_found_returns_404(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_machine.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 404
                    assert data["error"] == "Machine not found"

    def test_agent_not_connected_returns_503(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.is_agent_connected.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 503

    def test_agent_timeout_returns_504(self, flask_app, remote_module, mock_agent_mgr, admin_user):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 504

    def test_missing_path_param_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            # Only file param, no path
            with flask_app.test_request_context("/?file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 400
                    assert data["success"] is False
                    assert "path and file" in data["error"].lower()

    def test_missing_file_param_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            # Only path param, no file
            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 400
                    assert data["success"] is False
                    assert "path and file" in data["error"].lower()

    def test_missing_both_params_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            with flask_app.test_request_context("/"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 400

    def test_successful_git_diff_response(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            diff_content = (
                "diff --git a/src/main.py b/src/main.py\n"
                "--- a/src/main.py\n"
                "+++ b/src/main.py\n"
                "@@ -1,5 +1,6 @@\n"
                " import os\n"
                "+import sys\n"
                " \n"
            )
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"diff": diff_content},
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 200
                    assert data["success"] is True
                    assert "diff --git" in data["result"]["diff"]

    def test_git_diff_sends_correct_command(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"diff": ""},
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    remote_module.remote_git_diff("machine-001")

                    mock_agent_mgr.send_command.assert_called_once()
                    call_args = mock_agent_mgr.send_command.call_args
                    machine_id = call_args[0][0]
                    command = call_args[0][1]

                    assert machine_id == "machine-001"
                    assert command["command"] == "git_diff"
                    assert command["project_path"] == "/home/user/project"
                    assert command["file"] == "src/main.py"
                    assert "request_id" in command

    def test_error_response_git_not_installed(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": False,
                "error": "git is not installed",
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_diff("machine-001"))
                    assert status == 200
                    assert data["success"] is False
                    assert "not installed" in data["error"]


# ===========================================================================
# Tests for remote_git_file
# ===========================================================================


class TestRemoteGitFile:
    """Tests for GET /machines/<machine_id>/git/file?path=<path>&file=<file>"""

    def test_no_g_user_returns_401(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            from flask import g

            if hasattr(g, "user"):
                delattr(g, "user")

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 401

    def test_non_admin_no_access_returns_403(
        self, flask_app, remote_module, mock_agent_mgr, regular_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = regular_user
            mock_agent_mgr.check_user_access.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 403

    def test_machine_not_found_returns_404(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_machine.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 404

    def test_agent_not_connected_returns_503(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.is_agent_connected.return_value = False

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 503

    def test_agent_timeout_returns_504(self, flask_app, remote_module, mock_agent_mgr, admin_user):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = None

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 504

    def test_missing_path_param_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            with flask_app.test_request_context("/?file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 400

    def test_missing_file_param_returns_400(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 400

    def test_successful_git_file_response(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user

            file_content = "import os\nimport sys\n\ndef main():\n    print('hello')\n"
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"content": file_content, "encoding": "utf-8"},
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=src/main.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 200
                    assert data["success"] is True
                    assert "import os" in data["result"]["content"]
                    assert data["result"]["encoding"] == "utf-8"

    def test_git_file_sends_correct_command(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"content": ""},
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=README.md"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    remote_module.remote_git_file("machine-001")

                    mock_agent_mgr.send_command.assert_called_once()
                    call_args = mock_agent_mgr.send_command.call_args
                    machine_id = call_args[0][0]
                    command = call_args[0][1]

                    assert machine_id == "machine-001"
                    assert command["command"] == "git_file"
                    assert command["project_path"] == "/home/user/project"
                    assert command["file"] == "README.md"
                    assert "request_id" in command

    def test_error_response_file_not_found(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": False,
                "error": "File not found: src/missing.py",
            }

            with flask_app.test_request_context("/?path=/home/user/project&file=src/missing.py"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.remote_git_file("machine-001"))
                    assert status == 200
                    assert data["success"] is False
                    assert "not found" in data["error"].lower()


# ===========================================================================
# Tests for git_result message handler in agent_message()
# ===========================================================================


class TestGitResultMessageHandler:
    """Tests for the git_result message type in agent_message()."""

    def test_git_result_stored_successfully(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "type": "git_result",
                    "machine_id": "machine-001",
                    "request_id": "req-abc-123",
                    "success": True,
                    "result": {"branch": "main", "files": []},
                },
            ):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.agent_message())
                    assert status == 200
                    assert data["success"] is True

                    # Verify store_browse_result was called with git result
                    mock_agent_mgr.store_browse_result.assert_called_once()
                    call_args = mock_agent_mgr.store_browse_result.call_args
                    request_id = call_args[0][0]
                    stored_data = call_args[0][1]

                    assert request_id == "req-abc-123"
                    assert stored_data["success"] is True
                    assert stored_data["result"] == {"branch": "main", "files": []}

    def test_git_result_with_error(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "type": "git_result",
                    "machine_id": "machine-001",
                    "request_id": "req-err-456",
                    "success": False,
                    "error": "git is not installed on this machine",
                },
            ):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.agent_message())
                    assert status == 200
                    assert data["success"] is True

                    mock_agent_mgr.store_browse_result.assert_called_once()
                    stored_data = mock_agent_mgr.store_browse_result.call_args[0][1]
                    assert stored_data["success"] is False
                    assert "not installed" in stored_data["error"]

    def test_git_result_without_request_id_still_succeeds(
        self, flask_app, remote_module, mock_agent_mgr
    ):
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "type": "git_result",
                    "machine_id": "machine-001",
                    "success": True,
                    "result": {"diff": ""},
                },
            ):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.agent_message())
                    assert status == 200
                    assert data["success"] is True

                    # store_browse_result should NOT be called without request_id
                    mock_agent_mgr.store_browse_result.assert_not_called()

    def test_git_result_stores_null_result(self, flask_app, remote_module, mock_agent_mgr):
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "type": "git_result",
                    "machine_id": "machine-001",
                    "request_id": "req-null-789",
                    "success": True,
                },
            ):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.agent_message())
                    assert status == 200

                    stored_data = mock_agent_mgr.store_browse_result.call_args[0][1]
                    assert stored_data["success"] is True
                    assert stored_data["result"] is None
                    assert stored_data["error"] is None

    def test_git_result_missing_machine_id_returns_400(
        self, flask_app, remote_module, mock_agent_mgr
    ):
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "type": "git_result",
                    "request_id": "req-no-machine",
                    "success": True,
                },
            ):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    data, status = parse_response(remote_module.agent_message())
                    assert status == 400
                    assert "machine_id" in data["error"]


# ===========================================================================
# Tests for access control edge cases across all three endpoints
# ===========================================================================


class TestAccessControlEdgeCases:
    """Cross-cutting access control tests for all git endpoints."""

    def test_admin_bypasses_check_user_access(
        self, flask_app, remote_module, mock_agent_mgr, admin_user
    ):
        """Admin users should not require check_user_access."""
        with flask_app.app_context():
            from flask import g

            g.user = admin_user
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"branch": "main", "files": []},
            }

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    remote_module.remote_git_status("machine-001")
                    # check_user_access should NOT be called for admin
                    mock_agent_mgr.check_user_access.assert_not_called()

    def test_regular_user_with_access_calls_check(
        self, flask_app, remote_module, mock_agent_mgr, regular_user
    ):
        """Regular users must pass check_user_access."""
        with flask_app.app_context():
            from flask import g

            g.user = regular_user
            mock_agent_mgr.check_user_access.return_value = True
            mock_agent_mgr.get_browse_result.return_value = {
                "success": True,
                "result": {"branch": "main", "files": []},
            }

            with flask_app.test_request_context("/?path=/home/user/project"):
                with patch.object(
                    remote_module, "get_remote_agent_manager", return_value=mock_agent_mgr
                ):
                    remote_module.remote_git_status("machine-001")
                    mock_agent_mgr.check_user_access.assert_called_once_with("machine-001", 42)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
