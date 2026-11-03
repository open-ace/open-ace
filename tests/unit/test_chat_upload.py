"""Unit tests for /api/chat/upload endpoint (Issue #127).

Tests cover:
- Normal file upload
- File size limit enforcement
- Missing file in request
- Invalid filename handling
"""

import io
import os
from unittest.mock import patch

import pytest
from flask import Flask, g

USER = {
    "id": 1,
    "user_id": 1,
    "username": "test_user",
    "system_account": "test_user",
    "role": "user",
    "tenant_id": 1,
}


def _make_app():
    """Create Flask app for testing."""
    from app.routes.fs import fs_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.register_blueprint(fs_bp, url_prefix="/api")

    # Clear existing before_request handlers and inject test user
    app.before_request_funcs["fs"] = []

    @app.before_request
    def _set_user():
        g.user = dict(USER)
        g.user_id = USER["id"]
        g.user_role = USER["role"]
        g.tenant_id = USER["tenant_id"]
        return None

    return app


@pytest.fixture
def client(tmp_path):
    """Create test client with mocked home directory."""
    app = _make_app()

    with patch("app.routes.fs.get_home_directory", return_value=str(tmp_path)):
        yield app.test_client()


class TestChatUpload:
    """Test /api/chat/upload endpoint."""

    def test_normal_upload(self, client):
        """Test successful file upload."""
        data = {"file": (io.BytesIO(b"test content"), "test.txt")}

        response = client.post(
            "/api/chat/upload?session_id=test-session",
            data=data,
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        json_data = response.get_json()
        assert json_data["success"] is True
        assert json_data["filename"] == "test.txt"
        assert json_data["size"] == 12
        assert "path" in json_data
        assert os.path.exists(json_data["path"])

        # Verify file content
        with open(json_data["path"], "rb") as f:
            assert f.read() == b"test content"

    def test_file_too_large(self, tmp_path):
        """Test upload rejection when file exceeds size limit."""
        from app.routes.fs import fs_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(fs_bp, url_prefix="/api")
        app.before_request_funcs["fs"] = []

        @app.before_request
        def _set_user():
            g.user = dict(USER)
            g.user_id = USER["id"]
            g.user_role = USER["role"]
            g.tenant_id = USER["tenant_id"]
            return None

        # Create file larger than 20MB default (21MB)
        large_content = b"x" * (21 * 1024 * 1024)
        data = {"file": (io.BytesIO(large_content), "large.txt")}

        with patch("app.routes.fs.get_home_directory", return_value=str(tmp_path)):
            response = app.test_client().post(
                "/api/chat/upload?session_id=test-session",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 413
        json_data = response.get_json()
        assert json_data["success"] is False
        assert "too large" in json_data["error"].lower()

    def test_no_file_provided(self, client):
        """Test upload rejection when no file is provided."""
        response = client.post(
            "/api/chat/upload?session_id=test-session",
            data={},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        json_data = response.get_json()
        assert json_data["success"] is False
        assert "no file" in json_data["error"].lower()

    def test_invalid_filename(self, client):
        """Test upload rejection for invalid filenames."""
        # Test with filename that sanitizes to empty (path traversal)
        data = {"file": (io.BytesIO(b"test"), "../../../etc/passwd")}

        response = client.post(
            "/api/chat/upload?session_id=test-session",
            data=data,
            content_type="multipart/form-data",
        )

        # Should either reject or sanitize the filename
        json_data = response.get_json()
        if response.status_code == 400:
            assert json_data["success"] is False
            assert "invalid" in json_data["error"].lower()
        else:
            # If accepted, filename should be sanitized
            assert json_data["success"] is True
            assert ".." not in json_data["filename"]
            assert "/" not in json_data["filename"]

    def test_missing_session_id(self, client):
        """Test upload rejection when session_id is missing."""
        data = {"file": (io.BytesIO(b"test"), "test.txt")}

        response = client.post(
            "/api/chat/upload",
            data=data,
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        json_data = response.get_json()
        assert json_data["success"] is False
        assert "session_id" in json_data["error"].lower()

    def test_invalid_session_id(self, client):
        """Test upload rejection for invalid session_id."""
        data = {"file": (io.BytesIO(b"test"), "test.txt")}

        # Session ID with path traversal - should be sanitized by _sanitize_filename
        response = client.post(
            "/api/chat/upload?session_id=../../../etc",
            data=data,
            content_type="multipart/form-data",
        )

        # _sanitize_filename("../../../etc") returns "etc" (basename), which is valid
        # So the request should succeed with sanitized session_id
        json_data = response.get_json()
        if response.status_code == 200:
            assert json_data["success"] is True
            # Verify path doesn't escape chat-uploads directory
            assert ".qwen/chat-uploads" in json_data["path"]
        else:
            # If rejected, should be 400 with invalid error
            assert response.status_code == 400
            assert json_data["success"] is False
