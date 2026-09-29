"""Endpoint-level sandboxed-mode tests for /fs routes (Issue #3427).

The helper-level tests in test_fs_sandboxed_path_validation.py pin the
validation functions; these tests drive the actual Flask endpoints with a
sandboxed WebUIInstance, because that is the layer where #3427's user flow
lives (check-path → project creation) and where the round-1 review found the
gaps:

- check-path must NOT run host-side existence probes in sandboxed mode (the
  host cannot see /workspace); it reports valid+canCreate on shape alone.
- browse must accept explicit /workspace paths at the validation layer and
  degrade through the existing not-exists fallback.
- file-IO endpoints (upload/download/delete/search/create-directory) must
  fail fast with a clear sandbox error instead of a host IO failure.
"""

from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.issue(3427)]

USER = {
    "id": 11,
    "user_id": 11,
    "username": "qlfan",
    "system_account": "qlfan",
    "role": "user",
    "tenant_id": 1,
}


class _FakeInstance:
    def __init__(self, isolation_level):
        self.isolation_level = isolation_level


class _FakeManager:
    def __init__(self, isolation_level):
        self._instance = _FakeInstance(isolation_level)

    def get_user_instance(self, user_id):
        return self._instance


def _make_app(isolation_level):
    from app.routes.fs import fs_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(fs_bp, url_prefix="/api")
    # fs_bp 是模块级单例：清空其 token 鉴权回调后注入测试身份
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
def sandbox_client():
    app = _make_app("sandboxed")
    with (
        patch(
            "app.services.webui_manager.get_webui_manager",
            return_value=_FakeManager("sandboxed"),
        ),
        patch("app.routes.fs.get_workspace_base_dirs", return_value=["/home"]),
    ):
        yield app.test_client()


class TestCheckPathSandboxed:
    def test_own_workspace_path_valid_without_host_probe(self, sandbox_client):
        """#3427 core flow: /workspace/<account>/... is valid even though the
        host has no /workspace — no host existence probing may run."""
        resp = sandbox_client.post("/api/fs/check-path", json={"path": "/workspace/qlfan/proj"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["valid"] is True
        assert body["exists"] is False
        assert body["canCreate"] is True
        assert body.get("sandboxed") is True

    def test_home_root_itself_valid(self, sandbox_client):
        resp = sandbox_client.post("/api/fs/check-path", json={"path": "/workspace/qlfan"})
        assert resp.status_code == 200
        assert resp.get_json()["valid"] is True

    def test_other_users_home_rejected(self, sandbox_client):
        resp = sandbox_client.post("/api/fs/check-path", json={"path": "/workspace/bob/proj"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["valid"] is False

    def test_host_base_dir_path_rejected(self, sandbox_client):
        resp = sandbox_client.post("/api/fs/check-path", json={"path": "/home/qlfan/proj"})
        assert resp.status_code == 400
        assert resp.get_json()["valid"] is False

    def test_non_string_path_returns_400_not_500(self, sandbox_client):
        """#3459: list/dict JSON paths previously 500'd on startswith."""
        resp = sandbox_client.post("/api/fs/check-path", json={"path": ["/workspace/qlfan"]})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Path must be a string"


class TestBrowseSandboxed:
    def test_explicit_workspace_path_not_rejected_by_host_bases(self, sandbox_client):
        """The explicit-path branch must validate against /workspace in
        sandboxed mode (round-1 M1: it 400'd with the host-base error)."""
        resp = sandbox_client.get("/api/fs/browse?path=/workspace/qlfan")
        assert resp.status_code == 200
        body = resp.get_json()
        # #3459: host cannot see inside the container — empty listing + hint,
        # no doomed host probes, no misleading "will be created" note.
        assert body["currentPath"] == "/workspace/qlfan"
        assert body["directories"] == []
        assert body["files"] == []
        assert body["canCreate"] is False
        assert body["sandboxed"] is True
        assert "sandbox container" in body["fallback_note"]

    def test_home_default_returns_empty_listing(self, sandbox_client):
        resp = sandbox_client.get("/api/fs/browse?path=home")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["currentPath"] == "/workspace/qlfan"
        assert body["directories"] == []
        assert body["sandboxed"] is True

    def test_zero_host_probes(self, sandbox_client, monkeypatch):
        """#3459: sandboxed browse must not touch the host filesystem —
        including the legacy get_home_directory sudo probe that
        _primary_home_root used to trigger (round-1 review M1)."""

        def _explode(*args, **kwargs):
            raise AssertionError("host probe reached")

        monkeypatch.setattr("app.routes.fs.get_directory_info", _explode)
        monkeypatch.setattr("app.routes.fs.list_subdirectories", _explode)
        monkeypatch.setattr("app.routes.fs.run_as_user", _explode)
        resp = sandbox_client.get("/api/fs/browse?path=/workspace/qlfan")
        assert resp.status_code == 200
        resp = sandbox_client.get("/api/fs/browse?path=home")
        assert resp.status_code == 200

    def test_other_users_home_rejected(self, sandbox_client):
        resp = sandbox_client.get("/api/fs/browse?path=/workspace/bob")
        assert resp.status_code == 400
        assert "home" in resp.get_json()["error"]

    def test_host_base_dir_path_rejected(self, sandbox_client):
        resp = sandbox_client.get("/api/fs/browse?path=/home/qlfan")
        assert resp.status_code == 400
        assert "Path must be under" in resp.get_json()["error"]


class TestFileIoEndpointsGated:
    """upload/download/delete/search/create-directory fail fast with a clear
    sandbox error instead of host IO that can never succeed (round-1 M2/M3)."""

    def test_upload_gated(self, sandbox_client):
        resp = sandbox_client.post(
            "/api/fs/upload", data={"path": "/workspace/qlfan"}, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert "sandbox container" in resp.get_json()["error"]

    def test_download_gated(self, sandbox_client):
        resp = sandbox_client.get("/api/fs/download?path=/workspace/qlfan/file.txt")
        assert resp.status_code == 400
        assert "sandbox container" in resp.get_json()["error"]

    def test_delete_gated(self, sandbox_client):
        resp = sandbox_client.post(
            "/api/fs/delete-file", json={"path": "/workspace/qlfan/file.txt"}
        )
        assert resp.status_code == 400
        assert "sandbox container" in resp.get_json()["error"]

    def test_search_gated(self, sandbox_client):
        resp = sandbox_client.get("/api/fs/search?q=readme")
        assert resp.status_code == 400
        assert "sandbox container" in resp.get_json()["error"]

    def test_create_directory_gated(self, sandbox_client):
        resp = sandbox_client.post(
            "/api/fs/create-directory", json={"path": "/workspace/qlfan/newdir"}
        )
        assert resp.status_code == 400
        assert "sandbox container" in resp.get_json()["error"]


class TestNonSandboxedUnaffected:
    def test_check_path_os_user_still_host_validated(self):
        app = _make_app("os_user")
        with (
            patch(
                "app.services.webui_manager.get_webui_manager",
                return_value=_FakeManager("os_user"),
            ),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=["/home"]),
        ):
            client = app.test_client()
            resp = client.post("/api/fs/check-path", json={"path": "/workspace/qlfan"})
        # Host semantics unchanged: /workspace is not a host base dir.
        assert resp.status_code == 400
        assert "Path must be under" in resp.get_json()["error"]
