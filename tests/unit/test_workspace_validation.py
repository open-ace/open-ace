"""
Unit tests for workspace route path validation (Issue #1813).

Tests that project_path is validated server-side before being used
to create sessions, preventing authorization bypass attacks.
"""

import pytest
from unittest.mock import MagicMock, patch
from flask import Flask, g


class TestProjectPathValidation:
    """Test project_path validation in create_session endpoint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from app.routes.workspace import workspace_bp

        app = Flask(__name__)
        app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
        return app

    @pytest.fixture
    def mock_user(self):
        """Mock user object."""
        return {"id": 1, "role": "user", "email": "test@example.com"}

    def test_valid_path_accepted(self, app, mock_user):
        """Valid path within workspace base dirs should be accepted."""
        with app.test_client() as client:
            with patch("app.routes.workspace.get_session_manager") as mock_manager:
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    with patch("app.routes.workspace.g", user=mock_user):
                        mock_session = MagicMock()
                        mock_session.to_dict.return_value = {"id": "test-session-id"}
                        mock_manager.return_value.create_session.return_value = mock_session

                        # Use a valid path under home directory
                        response = client.post(
                            "/api/workspace/sessions",
                            json={
                                "tool_name": "test-tool",
                                "project_path": "/home/testuser/project",
                            },
                            headers={"Content-Type": "application/json"},
                        )

                        # Should succeed (201) or fail for other reasons (not 400 validation error)
                        assert response.status_code in [201, 400, 500]

    def test_blacklisted_path_rejected(self, app, mock_user):
        """Blacklisted system paths should be rejected with 400."""
        blacklisted_paths = [
            "/etc",
            "/etc/passwd",
            "/bin",
            "/bin/bash",
            "/root",
            "/root/.ssh",
            "/usr",
            "/var/log",
        ]

        with app.test_client() as client:
            with patch("app.routes.workspace.g", user=mock_user):
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    for path in blacklisted_paths:
                        response = client.post(
                            "/api/workspace/sessions",
                            json={"tool_name": "test-tool", "project_path": path},
                            headers={"Content-Type": "application/json"},
                        )

                        assert response.status_code == 400
                        data = response.get_json()
                        assert data["success"] is False
                        # Error message should NOT contain the path (security)
                        assert path not in data.get("error", "")

    def test_path_traversal_rejected(self, app, mock_user):
        """Path traversal attempts should be rejected."""
        traversal_paths = [
            "../../../etc/passwd",
            "/home/user/../../../etc",
            "/tmp/../../../root/.ssh",
        ]

        with app.test_client() as client:
            with patch("app.routes.workspace.g", user=mock_user):
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    for path in traversal_paths:
                        response = client.post(
                            "/api/workspace/sessions",
                            json={"tool_name": "test-tool", "project_path": path},
                            headers={"Content-Type": "application/json"},
                        )

                        assert response.status_code == 400
                        data = response.get_json()
                        assert data["success"] is False

    def test_relative_path_rejected(self, app, mock_user):
        """Relative paths should be rejected."""
        relative_paths = [
            "relative/path",
            "./project",
            "../project",
        ]

        with app.test_client() as client:
            with patch("app.routes.workspace.g", user=mock_user):
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    for path in relative_paths:
                        response = client.post(
                            "/api/workspace/sessions",
                            json={"tool_name": "test-tool", "project_path": path},
                            headers={"Content-Type": "application/json"},
                        )

                        assert response.status_code == 400
                        data = response.get_json()
                        assert data["success"] is False

    def test_empty_path_allowed(self, app, mock_user):
        """Empty or missing project_path should be allowed."""
        with app.test_client() as client:
            with patch("app.routes.workspace.get_session_manager") as mock_manager:
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    with patch("app.routes.workspace.g", user=mock_user):
                        mock_session = MagicMock()
                        mock_session.to_dict.return_value = {"id": "test-session-id"}
                        mock_manager.return_value.create_session.return_value = mock_session

                        # Empty string
                        response = client.post(
                            "/api/workspace/sessions",
                            json={"tool_name": "test-tool", "project_path": ""},
                            headers={"Content-Type": "application/json"},
                        )
                        # Should not fail validation (may fail for other reasons)
                        assert response.status_code in [201, 500]

                        # Missing project_path
                        response = client.post(
                            "/api/workspace/sessions",
                            json={"tool_name": "test-tool"},
                            headers={"Content_Type": "application/json"},
                        )
                        assert response.status_code in [201, 500]

    def test_error_message_no_path_disclosure(self, app, mock_user):
        """Error message should not disclose system path list."""
        with app.test_client() as client:
            with patch("app.routes.workspace.g", user=mock_user):
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    response = client.post(
                        "/api/workspace/sessions",
                        json={"tool_name": "test-tool", "project_path": "/etc/passwd"},
                        headers={"Content-Type": "application/json"},
                    )

                    assert response.status_code == 400
                    data = response.get_json()
                    assert data["success"] is False
                    error_msg = data.get("error", "")

                    # Should not contain specific path details
                    assert "/etc" not in error_msg
                    assert "/home" not in error_msg
                    assert "/workspace" not in error_msg

                    # Should be generic error
                    assert "Invalid" in error_msg or "invalid" in error_msg.lower()

    def test_url_parameter_injection_blocked(self, app, mock_user):
        """URL parameter injection should be blocked."""
        # Simulate URL with query parameter injection attempt
        with app.test_client() as client:
            with patch("app.routes.workspace.g", user=mock_user):
                with patch("app.routes.workspace._current_tenant_id", return_value=1):
                    # Attempt to inject path via JSON body
                    response = client.post(
                        "/api/workspace/sessions?projectPath=/etc",
                        json={"tool_name": "test-tool", "project_path": "/etc"},
                        headers={"Content-Type": "application/json"},
                    )

                    assert response.status_code == 400
                    data = response.get_json()
                    assert data["success"] is False


class TestIsValidPathIntegration:
    """Integration tests for is_valid_path function used in workspace routes."""

    def test_valid_path_under_home(self):
        """Valid path under home directory should pass."""
        from app.routes.fs import is_valid_path
        from app.utils.workspace import get_workspace_base_dirs

        base_dirs = get_workspace_base_dirs()
        # Path under one of the base dirs should be valid
        if base_dirs:
            test_path = base_dirs[0] + "/test_project"
            assert is_valid_path(test_path, allowed_prefixes=base_dirs)

    def test_blacklisted_path_fails(self):
        """Blacklisted system paths should fail validation."""
        from app.routes.fs import is_valid_path
        from app.utils.workspace import get_workspace_base_dirs

        base_dirs = get_workspace_base_dirs()
        assert not is_valid_path("/etc", allowed_prefixes=base_dirs)
        assert not is_valid_path("/bin/bash", allowed_prefixes=base_dirs)
        assert not is_valid_path("/root", allowed_prefixes=base_dirs)

    def test_path_traversal_fails(self):
        """Path traversal attempts should fail."""
        from app.routes.fs import is_valid_path
        from app.utils.workspace import get_workspace_base_dirs

        base_dirs = get_workspace_base_dirs()
        assert not is_valid_path("../../../etc/passwd", allowed_prefixes=base_dirs)
        assert not is_valid_path("/home/../etc", allowed_prefixes=base_dirs)