#!/usr/bin/env python3
"""
Unit tests for /api/workspace/remote-projects API endpoint (Issue #417).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Modules that need to be mocked for workspace.py to load
_MOCK_MODULE_NAMES = [
    "app.modules",
    "app.modules.workspace",
    "app.modules.workspace.collaboration",
    "app.modules.workspace.prompt_library",
    "app.modules.workspace.session_manager",
    "app.modules.workspace.state_sync",
    "app.modules.workspace.tool_connector",
    "app.auth.decorators",
    "app.repositories.database",
    "app.repositories.schema_init",
    "app.utils.tool_names",
    "app.modules.analytics",
    "app.modules.governance",
    "app.utils.cache",
    "gevent",
]


@pytest.fixture(scope="module")
def workspace_module():
    """Load workspace.py with mocked dependencies.

    Uses patch.dict to mock sys.modules entries only within this fixture's
    scope, automatically restoring them when the fixture tears down.
    This prevents MagicMock objects from leaking into other test modules.
    """
    mock_modules = {
        "app.modules": MagicMock(__path__=[]),
        "app.modules.workspace": MagicMock(__path__=[]),
        "app.modules.workspace.collaboration": MagicMock(),
        "app.modules.workspace.prompt_library": MagicMock(),
        "app.modules.workspace.session_manager": MagicMock(),
        "app.modules.workspace.state_sync": MagicMock(),
        "app.modules.workspace.tool_connector": MagicMock(),
        "app.auth.decorators": MagicMock(),
        "app.repositories.database": MagicMock(),
        "app.repositories.schema_init": MagicMock(),
        "app.utils.tool_names": MagicMock(),
        "app.modules.analytics": MagicMock(__path__=[]),
        "app.modules.governance": MagicMock(__path__=[]),
        "app.utils.cache": MagicMock(),
        "gevent": MagicMock(),
    }

    with patch.dict(sys.modules, mock_modules):
        workspace_path = Path(project_root) / "app" / "routes" / "workspace.py"
        spec = importlib.util.spec_from_file_location("workspace_direct", workspace_path)
        workspace_module = importlib.util.module_from_spec(spec)
        sys.modules["workspace_direct"] = workspace_module
        spec.loader.exec_module(workspace_module)
        yield workspace_module


def parse_response(result):
    if isinstance(result, tuple):
        resp, status = result
    else:
        resp = result
        status = resp.status_code
    return resp, status


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_user():
    return {"id": 42, "username": "testuser", "role": "user"}


@pytest.fixture
def flask_context():
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    with app.app_context():
        yield app


class TestGetRemoteProjectsAuthentication:
    def test_no_user_returns_401(self, flask_context, workspace_module):
        from flask import g

        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock()
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        if hasattr(g, "user"):
            delattr(g, "user")
        result = workspace_module.get_remote_projects()
        resp, status = parse_response(result)
        assert status == 401
        assert resp.get_json()["success"] is False

    def test_user_without_id_returns_401(self, flask_context, workspace_module):
        from flask import g

        g.user = {"username": "testuser"}
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock()
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        result = workspace_module.get_remote_projects()
        resp, status = parse_response(result)
        assert status == 401


class TestGetRemoteProjectsNormalResponse:
    def test_returns_projects_list(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_results = [
            {
                "project_path": "/home/user/demo",
                "last_used": datetime(2026, 5, 18),
                "machine_id": "machine-001",
                "session_count": 5,
            },
        ]
        mock_machine_rows = [{"machine_id": "machine-001", "machine_name": "Server 1"}]
        mock_db.fetch_all.side_effect = [mock_results, mock_machine_rows]
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        result = workspace_module.get_remote_projects()
        resp, status = parse_response(result)
        data = resp.get_json()
        assert status == 200
        assert data["projects"][0]["project_path"] == "/home/user/demo"
        assert data["projects"][0]["machine_name"] == "Server 1"

    def test_returns_empty_list(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_db.fetch_all.return_value = []
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        result = workspace_module.get_remote_projects()
        resp, status = parse_response(result)
        assert resp.get_json()["projects"] == []


class TestGetRemoteProjectsMachineLookup:
    def test_deduplicates_machine_ids(self, flask_context, mock_db, mock_user, workspace_module):
        """Verify machine_ids deduplication (Issue #417 optimization)."""
        from flask import g

        g.user = mock_user
        mock_results = [
            {
                "project_path": "/p1",
                "last_used": datetime(2026, 5, 18),
                "machine_id": "machine-A",
                "session_count": 2,
            },
            {
                "project_path": "/p2",
                "last_used": datetime(2026, 5, 17),
                "machine_id": "machine-A",
                "session_count": 1,
            },
            {
                "project_path": "/p3",
                "last_used": datetime(2026, 5, 16),
                "machine_id": "machine-B",
                "session_count": 3,
            },
        ]
        mock_machine_rows = [
            {"machine_id": "machine-A", "machine_name": "Server A"},
            {"machine_id": "machine-B", "machine_name": "Server B"},
        ]
        mock_db.fetch_all.side_effect = [mock_results, mock_machine_rows]
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        workspace_module.get_remote_projects()
        machine_params = mock_db.fetch_all.call_args_list[1][0][1]
        assert len(machine_params) == 2  # deduplicated
        assert set(machine_params) == {"machine-A", "machine-B"}

    def test_no_machine_ids_skips_lookup(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_results = [
            {
                "project_path": "/p",
                "last_used": datetime(2026, 5, 18),
                "machine_id": None,
                "session_count": 1,
            }
        ]
        mock_db.fetch_all.return_value = mock_results
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        workspace_module.get_remote_projects()
        assert mock_db.fetch_all.call_count == 1


class TestGetRemoteProjectsErrorHandling:
    def test_handles_database_exception(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_db.fetch_all.side_effect = Exception("DB failed")
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        result = workspace_module.get_remote_projects()
        resp, status = parse_response(result)
        assert status == 500


class TestGetRemoteProjectsQueryStructure:
    def test_query_filters_deleted(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_db.fetch_all.return_value = []
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        workspace_module.get_remote_projects()
        query = mock_db.fetch_all.call_args[0][0]
        assert "status != 'deleted'" in query

    def test_query_has_limit_50(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_db.fetch_all.return_value = []
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        workspace_module.get_remote_projects()
        query = mock_db.fetch_all.call_args[0][0]
        assert "LIMIT 50" in query

    def test_query_groups_by_path(self, flask_context, mock_db, mock_user, workspace_module):
        from flask import g

        g.user = mock_user
        mock_db.fetch_all.return_value = []
        mock_db_module = sys.modules["app.repositories.database"]
        mock_db_module.Database = MagicMock(return_value=mock_db)
        mock_db_module.get_param_placeholder = MagicMock(return_value="%s")
        workspace_module.get_remote_projects()
        query = mock_db.fetch_all.call_args[0][0]
        assert "GROUP BY project_path" in query
