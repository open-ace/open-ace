"""Unit tests for WorkspaceService."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.workspace_service import WorkspaceService


class TestWorkspaceService:
    """Test WorkspaceService business logic."""

    def test_create_prompt_template(self):
        svc = WorkspaceService()
        mock_pl = MagicMock()
        mock_pl.create_template.return_value = 1
        svc._prompt_library = mock_pl

        template = svc.create_prompt_template(
            name="Test Template",
            content="Hello {name}",
            user_id=1,
            username="admin",
            variables=[{"name": "name", "description": "User name"}],
        )
        assert template.id == 1
        assert template.name == "Test Template"
        mock_pl.create_template.assert_called_once()

    def test_render_prompt(self):
        svc = WorkspaceService()
        mock_pl = MagicMock()
        mock_template = MagicMock()
        mock_template.validate_variables.return_value = []
        mock_template.render.return_value = "Hello World"
        mock_pl.get_template.return_value = mock_template
        svc._prompt_library = mock_pl

        result = svc.render_prompt(1, {"name": "World"})
        assert result == "Hello World"
        mock_pl.increment_use_count.assert_called_with(1)

    def test_render_prompt_not_found(self):
        svc = WorkspaceService()
        mock_pl = MagicMock()
        mock_pl.get_template.return_value = None
        svc._prompt_library = mock_pl

        with pytest.raises(ValueError, match="Template not found"):
            svc.render_prompt(999, {})

    def test_render_prompt_missing_variables(self):
        svc = WorkspaceService()
        mock_pl = MagicMock()
        mock_template = MagicMock()
        mock_template.validate_variables.return_value = ["name"]
        mock_pl.get_template.return_value = mock_template
        svc._prompt_library = mock_pl

        with pytest.raises(ValueError, match="Missing required variables"):
            svc.render_prompt(1, {})

    def test_lazy_init_prompt_library(self):
        svc = WorkspaceService()
        assert svc._prompt_library is None
        with patch("app.services.workspace_service.PromptLibrary") as MockPL:
            MockPL.return_value = MagicMock()
            _ = svc.prompts
            assert svc._prompt_library is not None

    def test_lazy_init_session_manager(self):
        svc = WorkspaceService()
        assert svc._session_manager is None
        with patch("app.services.workspace_service.SessionManager") as MockSM:
            MockSM.return_value = MagicMock()
            _ = svc.sessions
            assert svc._session_manager is not None

    def test_get_available_tools(self):
        svc = WorkspaceService()
        mock_tc = MagicMock()
        mock_tc.list_tools.return_value = ["qwen", "claude"]
        svc._tool_connector = mock_tc
        result = svc.get_available_tools()
        assert len(result) == 2

    def test_get_tool_info(self):
        svc = WorkspaceService()
        mock_tc = MagicMock()
        mock_tc.get_tool.return_value = {"name": "qwen", "version": "1.0"}
        svc._tool_connector = mock_tc
        result = svc.get_tool_info("qwen")
        assert result["name"] == "qwen"

    def test_get_workspace_stats(self):
        svc = WorkspaceService()
        mock_sm = MagicMock()
        mock_sm.get_session_stats.return_value = {"total": 10}
        svc._session_manager = mock_sm
        mock_tc = MagicMock()
        mock_tc.get_tool_stats.return_value = {"tools": 3}
        svc._tool_connector = mock_tc
        mock_sync = MagicMock()
        mock_sync.get_stats.return_value = {"events": 50}
        svc._state_sync = mock_sync
        result = svc.get_workspace_stats()
        assert result["sessions"]["total"] == 10
        assert result["tools"]["tools"] == 3
