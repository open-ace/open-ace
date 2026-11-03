"""Unit tests for chat upload endpoint (Issue #127).

Tests cover:
- Normal file upload (images, documents)
- File size limit enforcement (>20MB)
- Missing file in request
- Invalid filename handling
- Path traversal attack prevention
- Cleanup service functionality
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

    def test_upload_image_file(self, client):
        """Test successful image file upload."""
        # Simulate PNG file content
        png_content = b"\x89PNG\r\n\x1a\n" + b"fake image data"
        data = {"file": (io.BytesIO(png_content), "screenshot.png")}

        response = client.post(
            "/api/chat/upload?session_id=image-session",
            data=data,
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        json_data = response.get_json()
        assert json_data["success"] is True
        assert json_data["filename"] == "screenshot.png"

    def test_upload_document_file(self, client):
        """Test successful document file upload."""
        # Simulate PDF file content
        pdf_content = b"%PDF-1.4\nfake pdf content"
        data = {"file": (io.BytesIO(pdf_content), "document.pdf")}

        response = client.post(
            "/api/chat/upload?session_id=doc-session",
            data=data,
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        json_data = response.get_json()
        assert json_data["success"] is True
        assert json_data["filename"] == "document.pdf"

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

    def test_duplicate_filename_handling(self, client):
        """Test that duplicate filenames are handled with suffix."""
        data = {"file": (io.BytesIO(b"first"), "duplicate.txt")}

        # First upload
        response1 = client.post(
            "/api/chat/upload?session_id=dup-session",
            data=data,
            content_type="multipart/form-data",
        )
        assert response1.status_code == 200

        # Second upload with same filename
        data = {"file": (io.BytesIO(b"second"), "duplicate.txt")}
        response2 = client.post(
            "/api/chat/upload?session_id=dup-session",
            data=data,
            content_type="multipart/form-data",
        )
        assert response2.status_code == 200
        json_data = response2.get_json()
        # Should have a suffix like _1
        assert "duplicate" in json_data["filename"]
        assert json_data["filename"] != "duplicate.txt"


class TestChatUploadSecurity:
    """Security tests for chat upload."""

    def test_filename_sanitize_path_traversal(self):
        """Test that path traversal in filename is prevented."""
        from app.routes.fs import _sanitize_filename

        # These should all be sanitized to safe names
        malicious_names = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32",
            "/absolute/path/file.txt",
            "file\x00.txt",  # Null byte injection
        ]

        for name in malicious_names:
            result = _sanitize_filename(name)
            assert result is not None, f"Failed for: {name}"
            # Result should not contain path separators
            assert "/" not in result, f"Contains / for: {name}"
            assert "\\" not in result, f"Contains \\ for: {name}"
            # Result should not be empty or just dots
            assert result not in (".", "..", ""), f"Invalid result for: {name}"

    def test_session_id_sanitize_path_traversal(self):
        """Test that path traversal in session_id is prevented."""
        from app.routes.fs import _sanitize_filename

        # session_id is sanitized using the same function
        malicious_ids = [
            "../../../etc",
            "..\\..\\windows",
            "/absolute/path",
        ]

        for session_id in malicious_ids:
            result = _sanitize_filename(session_id)
            assert result is not None, f"Failed for: {session_id}"
            assert "/" not in result, f"Contains / for: {session_id}"
            assert "\\" not in result, f"Contains \\ for: {session_id}"


class TestChatUploadCleanup:
    """Tests for chat upload cleanup service."""

    def test_cleanup_old_files(self, tmp_path):
        """Test cleanup of old upload files."""
        from app.services.chat_upload_cleanup import (
            CHAT_UPLOADS_DIR,
            cleanup_chat_uploads_once,
        )

        # Create test directory structure
        user_home = tmp_path / "testuser"
        chat_dir = user_home / CHAT_UPLOADS_DIR
        session_dir = chat_dir / "old-session"
        session_dir.mkdir(parents=True)

        # Create a test file
        test_file = session_dir / "test.txt"
        test_file.write_text("test content")

        # Set modification time to 10 days ago
        import time

        old_time = time.time() - (10 * 24 * 60 * 60)
        os.utime(str(session_dir), (old_time, old_time))

        # Mock get_workspace_base_dirs to return our test directory
        with patch(
            "app.utils.workspace.get_workspace_base_dirs",
            return_value=[str(tmp_path)],
        ):
            deleted, errors = cleanup_chat_uploads_once()

        assert deleted == 1
        assert errors == 0
        assert not session_dir.exists()

    def test_cleanup_keeps_recent_files(self, tmp_path):
        """Test that cleanup keeps recent files."""
        from app.services.chat_upload_cleanup import (
            CHAT_UPLOADS_DIR,
            cleanup_chat_uploads_once,
        )

        # Create test directory structure
        user_home = tmp_path / "testuser"
        chat_dir = user_home / CHAT_UPLOADS_DIR
        session_dir = chat_dir / "recent-session"
        session_dir.mkdir(parents=True)

        # Create a test file (recent)
        test_file = session_dir / "test.txt"
        test_file.write_text("test content")

        # Mock get_workspace_base_dirs
        with patch(
            "app.utils.workspace.get_workspace_base_dirs",
            return_value=[str(tmp_path)],
        ):
            deleted, errors = cleanup_chat_uploads_once()

        assert deleted == 0
        assert errors == 0
        assert session_dir.exists()

    def test_cleanup_handles_missing_directory(self, tmp_path):
        """Test that cleanup handles missing directories gracefully."""
        from app.services.chat_upload_cleanup import cleanup_chat_uploads_once

        # Mock get_workspace_base_dirs to return non-existent directory
        with patch(
            "app.utils.workspace.get_workspace_base_dirs",
            return_value=[str(tmp_path / "nonexistent")],
        ):
            deleted, errors = cleanup_chat_uploads_once()

        assert deleted == 0
        assert errors == 0
