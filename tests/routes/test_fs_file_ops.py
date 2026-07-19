#!/usr/bin/env python3
"""Route tests for personal-files file operations (upload / download / delete).

These tests register only ``fs_bp`` against an isolated Flask app (mirrors
``test_upload_auth_config.py``) and stub the auth hook so we can drive the
endpoints directly.

Why the workspace lives under ``Path.home()``: ``is_valid_path`` rejects
anything under a blacklisted system directory, and on macOS ``tempfile.mkdtemp``
yields a path under ``/private/var`` (realpath of ``/var``), which is
blacklisted. ``Path.home()`` is non-blacklisted on both macOS and Linux, so we
build a throwaway workspace tree there and clean it up in teardown.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture
def workspace(tmp_path_factory):
    """A throwaway workspace dir under the real home (non-blacklisted)."""
    # tmp_path is under /var on macOS (blacklisted). Use a home-relative dir.
    ws = Path.home() / ".ace_fs_test_ws_routes"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    # Per-user home inside the workspace.
    user_home = ws / "testuser"
    user_home.mkdir(parents=True, exist_ok=True)
    yield ws, user_home
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def app(workspace):
    from flask import Flask, g

    from app.routes.fs import fs_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")

    # Disable fs_bp's own auth (session/webui token) and inject a fake user.
    fs_bp.before_request_funcs[None] = []

    ws_root, user_home = workspace

    @app.before_request
    def _set_user():
        # No system_account → single-user direct-write path (process owns home).
        g.user = {"id": 1, "username": "testuser"}

    with (
        patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
        patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
        patch(
            "app.routes.fs.get_home_directory",
            return_value=str(user_home),
        ),
    ):
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestSanitizeFilename:
    """Unit tests for the _sanitize_filename helper."""

    def test_normal_name(self):
        from app.routes.fs import _sanitize_filename

        assert _sanitize_filename("report.txt") == "report.txt"

    def test_strips_directory_components(self):
        from app.routes.fs import _sanitize_filename

        # basename() drops the dir part; the result is safe.
        assert _sanitize_filename("../../etc/passwd") == "passwd"
        assert _sanitize_filename("/etc/shadow") == "shadow"

    def test_replaces_control_chars_and_separators(self):
        from app.routes.fs import _sanitize_filename

        # NUL, newline, backslash, forward slash all replaced with '_'.
        assert _sanitize_filename("a\x00b") == "a_b"
        assert _sanitize_filename("a\nb") == "a_b"
        assert _sanitize_filename("a\\b") == "a_b"

    def test_rejects_empty(self):
        from app.routes.fs import _sanitize_filename

        assert _sanitize_filename("") is None
        assert _sanitize_filename("   ") is None

    def test_rejects_dot_and_dotdot_after_basename(self):
        from app.routes.fs import _sanitize_filename

        assert _sanitize_filename(".") is None
        assert _sanitize_filename("..") is None


class TestUpload:
    def test_upload_success(self, client, workspace):
        _, user_home = workspace
        data = {
            "file": (io.BytesIO(b"hello world"), "report.txt"),
            "path": str(user_home),
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["size"] == len(b"hello world")
        assert (user_home / "report.txt").read_bytes() == b"hello world"

    def test_rejects_path_outside_home(self, client):
        # /etc is blacklisted AND outside the user's home subtree.
        data = {
            "file": (io.BytesIO(b"x"), "a.txt"),
            "path": "/etc",
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code in (400, 403)

    def test_rejects_path_inside_workspace_but_outside_home(self, client, workspace):
        # A sibling user's dir under the same workspace root: inside base_dirs
        # but OUTSIDE the current user's home subtree → must be rejected by
        # the home-lock guard.
        ws_root, _ = workspace
        other_home = ws_root / "otheruser"
        other_home.mkdir(exist_ok=True)
        data = {
            "file": (io.BytesIO(b"x"), "a.txt"),
            "path": str(other_home),
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "home directory" in resp.get_json()["error"]

    def test_rejects_oversized(self, client, workspace, monkeypatch):
        _, user_home = workspace
        monkeypatch.setattr("app.routes.fs.MAX_UPLOAD_SIZE_MB", 0)  # 0MB cap
        data = {
            "file": (io.BytesIO(b"x" * 10), "big.bin"),
            "path": str(user_home),
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 413
        assert "too large" in resp.get_json()["error"].lower()

    def test_rejects_missing_file(self, client, workspace):
        _, user_home = workspace
        resp = client.post(
            "/api/fs/upload",
            data={"path": str(user_home)},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_rejects_empty_filename(self, client, workspace):
        _, user_home = workspace
        data = {
            "file": (io.BytesIO(b"x"), ""),
            "path": str(user_home),
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_traversal_filename_neutralized(self, client, workspace):
        # "../../etc/passwd" → basename "passwd" → written inside user home,
        # NOT to /etc. The traversal is neutralized, not followed.
        _, user_home = workspace
        data = {
            "file": (io.BytesIO(b"x"), "../../etc/passwd"),
            "path": str(user_home),
        }
        resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        # File landed inside the user home, not /etc.
        assert (user_home / "passwd").exists()
        assert not Path("/etc/passwd_uploaded").exists()


class TestDownload:
    def test_download_success(self, client, workspace):
        _, user_home = workspace
        (user_home / "doc.txt").write_bytes(b"file contents")

        resp = client.get(f"/api/fs/download?path={user_home / 'doc.txt'}")
        assert resp.status_code == 200
        assert resp.data == b"file contents"
        cd = resp.headers.get("Content-Disposition", "")
        assert "doc.txt" in cd
        assert "attachment" in cd

    def test_rejects_outside_home(self, client):
        resp = client.get("/api/fs/download?path=/etc/passwd")
        assert resp.status_code == 400

    def test_rejects_missing_file(self, client, workspace):
        _, user_home = workspace
        resp = client.get(f"/api/fs/download?path={user_home / 'nope.txt'}")
        assert resp.status_code == 400

    def test_streams_large_file(self, client, workspace):
        # 1MB file should stream without loading fully (smoke check on chunks).
        _, user_home = workspace
        payload = b"AB" * (512 * 1024)  # 1MB
        (user_home / "big.bin").write_bytes(payload)

        resp = client.get(f"/api/fs/download?path={user_home / 'big.bin'}")
        assert resp.status_code == 200
        assert resp.data == payload
        assert resp.headers.get("Content-Length") == str(len(payload))


class TestDelete:
    def test_delete_success(self, client, workspace):
        _, user_home = workspace
        target = user_home / "trash.txt"
        target.write_bytes(b"x")

        resp = client.post("/api/fs/delete-file", json={"path": str(target)})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert not target.exists()

    def test_rejects_outside_home(self, client):
        resp = client.post("/api/fs/delete-file", json={"path": "/etc/passwd"})
        assert resp.status_code == 400

    def test_rejects_missing_file(self, client, workspace):
        _, user_home = workspace
        resp = client.post("/api/fs/delete-file", json={"path": str(user_home / "nope.txt")})
        assert resp.status_code == 400


class TestBrowseIncludeFiles:
    def test_default_returns_no_files(self, client, workspace):
        """Backward compat: without ?include_files=1, files[] is empty."""
        _, user_home = workspace
        (user_home / "f1.txt").write_bytes(b"x")
        (user_home / "subdir").mkdir(exist_ok=True)

        resp = client.get(f"/api/fs/browse?path={user_home}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["files"] == []
        # Directories still listed as before.
        assert any(d["name"] == "subdir" for d in body["directories"])

    def test_include_files_returns_files(self, client, workspace):
        _, user_home = workspace
        (user_home / "f1.txt").write_bytes(b"hello")
        (user_home / "subdir").mkdir(exist_ok=True)

        resp = client.get(f"/api/fs/browse?path={user_home}&include_files=1")
        assert resp.status_code == 200
        body = resp.get_json()
        names = {f["name"] for f in body["files"]}
        assert "f1.txt" in names
        # Directories must NOT appear in files[].
        assert "subdir" not in names
        # File entry shape.
        f1 = next(f for f in body["files"] if f["name"] == "f1.txt")
        assert f1["size"] == 5
        assert f1["is_readable"] is True
