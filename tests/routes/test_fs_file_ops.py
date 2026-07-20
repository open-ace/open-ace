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

# ---------------------------------------------------------------------------
# Pre-load app.routes.fs directly from its file, bypassing the package
# __init__.py. The package init imports the full route registry (admin, auth,
# …), which triggers app.repositories.database → scripts/shared, a module with
# a pre-existing surrogate-char bug on this dev machine unrelated to this PR.
# By loading fs.py as a standalone module first, subsequent
# `from app.routes.fs import …` calls hit sys.modules and skip __init__.py.
# In CI (where scripts/shared imports cleanly) this pre-load is a harmless
# no-op because the module is already cached.
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402

if "app.routes.fs" not in sys.modules:
    # Provide just enough of the app.* tree for fs.py's own imports to resolve.
    for _pkg in [
        "app",
        "app.routes",
        "app.repositories",
        "app.repositories.user_repo",
        "app.utils",
        "app.utils.workspace",
        "app.auth",
        "app.auth.decorators",
        "app.services",
        "app.services.webui_manager",
    ]:
        if _pkg not in sys.modules:
            sys.modules[_pkg] = type(sys)(_pkg)
            # Mark as a package so `from app.routes.x import y` works without
            # triggering __init__.
            if "." not in _pkg[len("app") :] or _pkg.count(".") <= 1:
                sys.modules[_pkg].__path__ = []  # type: ignore[attr-defined]

    # user_repo stub
    class _UR:
        def get_user_by_id(self, _):
            return None

    sys.modules["app.repositories.user_repo"].UserRepository = _UR

    # auth.decorators — symbols fs._authenticate_user imports lazily.
    _ad = sys.modules["app.auth.decorators"]
    _ad._extract_token = lambda: None  # type: ignore[attr-defined]
    _ad._load_user_from_token = lambda t: None  # type: ignore[attr-defined]
    _ad.enforce_password_change_requirement = lambda u: None  # type: ignore[attr-defined]

    sys.modules["app.services.webui_manager"].get_webui_manager = lambda: None  # type: ignore[attr-defined]

    # tests/conftest.py autouse _clear_cache imports these unconditionally;
    # stub them so the fixture doesn't crash (and so it's a true no-op).
    _cache_mod = type(sys)("app.utils.cache")

    class _Cache:
        def clear(self):
            pass

    _cache_mod.get_cache = lambda: _Cache()  # type: ignore[attr-defined]
    sys.modules["app.utils.cache"] = _cache_mod
    _auth_svc = type(sys)("app.services.auth_service")
    _auth_svc._security_settings_cache = set()  # type: ignore[attr-defined]
    sys.modules["app.services.auth_service"] = _auth_svc

    # Use the REAL workspace base-dir helpers + wrapper constants.
    _ws = sys.modules["app.utils.workspace"]
    _rspec = importlib.util.spec_from_file_location(
        "_real_workspace_for_test", str(Path(project_root) / "app/utils/workspace.py")
    )
    _rw = importlib.util.module_from_spec(_rspec)
    _rspec.loader.exec_module(_rw)
    _ws.get_workspace_base_dir = _rw.get_workspace_base_dir
    _ws.get_workspace_base_dirs = _rw.get_workspace_base_dirs
    _ws.OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
    _ws._is_wrapper_available = lambda p: False  # type: ignore[attr-defined]
    _ws.run_as_root_if_needed = lambda cmd: None  # type: ignore[attr-defined]

    # Now load fs.py as app.routes.fs without touching __init__.py.
    _fs_spec = importlib.util.spec_from_file_location(
        "app.routes.fs", str(Path(project_root) / "app/routes/fs.py")
    )
    assert _fs_spec is not None and _fs_spec.loader is not None
    _fs_mod = importlib.util.module_from_spec(_fs_spec)
    sys.modules["app.routes.fs"] = _fs_mod
    _fs_spec.loader.exec_module(_fs_mod)


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

    # Disable fs_bp's auth hook at the APP level (not the blueprint level).
    # fs_bp is a module-level singleton shared across the whole test session;
    # mutating fs_bp.before_request_funcs would leak into other test files
    # (it broke test_must_change_enforcement_blueprints.py, whose /api/fs/browse
    # case relies on the real _authenticate_user running). Clearing the
    # app-scoped before_request_funcs keeps this app isolated.
    app.before_request_funcs["fs"] = []

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

    def test_rejects_oversized(self, client, workspace):
        from app.routes.fs import MAX_UPLOAD_SIZE_MB as _orig

        _, user_home = workspace
        # Set cap to 0MB so even a 10-byte upload exceeds it. Use module
        # attribute assignment (monkeypatch's string-path form fails because
        # the app.routes package is stub-loaded in this dev environment).
        import app.routes.fs as _fsm

        _fsm.MAX_UPLOAD_SIZE_MB = 0
        try:
            data = {
                "file": (io.BytesIO(b"x" * 10), "big.bin"),
                "path": str(user_home),
            }
            resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
            assert resp.status_code == 413
            assert "too large" in resp.get_json()["error"].lower()
        finally:
            _fsm.MAX_UPLOAD_SIZE_MB = _orig

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


class TestChownHelper:
    """Unit tests for _chown_to_user covering all three branches.

    These mock subprocess/os primitives so we can exercise the root / wrapper
    / sudo-fallback paths without needing a real multi-user OS.
    """

    def test_no_system_account_is_noop_success(self):
        from app.routes.fs import _chown_to_user

        # None → single-user, nothing to do, reports success.
        assert _chown_to_user("/any/path", None) is True

    def test_uid_lookup_failure_returns_false(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 1  # user does not exist
            r.stdout = ""
            return r

        with patch("app.routes.fs.subprocess.run", side_effect=fake_run):
            assert _chown_to_user("/p", "ghost") is False

    def test_root_uses_os_chown(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            if "id" in cmd and "-u" in cmd:
                r.stdout = "1001\n"
            elif "id" in cmd and "-g" in cmd:
                r.stdout = "1002\n"
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=0),
            patch("app.routes.fs.os.chown") as chown_mock,
            patch("app.routes.fs.subprocess.run", side_effect=fake_run),
        ):
            assert _chown_to_user("/p", "alice") is True
        chown_mock.assert_called_once_with("/p", 1001, 1002)

    def test_root_chown_raises_returns_false(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=0),
            patch("app.routes.fs.os.chown", side_effect=PermissionError("denied")),
            patch("app.routes.fs.subprocess.run", side_effect=fake_run),
        ):
            assert _chown_to_user("/p", "alice") is False

    def test_non_root_uses_wrapper_when_available(self):
        from app.routes.fs import _chown_to_user

        def fake_id_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            return r

        # First two calls are the id -u/-g lookups; the third is the wrapper.
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            calls["n"] += 1
            r = MagicMock()
            r.returncode = 0
            r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            r.stderr = ""
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs.subprocess.run", side_effect=fake_run),
        ):
            assert _chown_to_user("/p", "alice") is True

    def test_wrapper_nonzero_return_returns_false(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            # id lookups succeed, but wrapper fails.
            if "id" in cmd:
                r.returncode = 0
                r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            else:
                r.returncode = 1  # wrapper exit code
                r.stdout = ""
                r.stderr = "wrapper denied"
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs.subprocess.run", side_effect=fake_run),
        ):
            assert _chown_to_user("/p", "alice") is False


class TestUploadRootBranch:
    """Cover the Docker multi-user (root) upload path with mocked chown.

    The default fixture runs as single-user (no system_account), so this class
    builds its own client with a system_account user and mocks geteuid/os.chown
    to simulate the root + chown path.
    """

    @pytest.fixture
    def root_client(self, workspace):
        from flask import Flask, g

        from app.routes.fs import fs_bp

        ws_root, user_home = workspace
        # Give the user a system_account so the root branch is taken.
        home_root = ws_root / "testuser"
        home_root.mkdir(exist_ok=True)

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fs_bp, url_prefix="/api")
        # Disable auth at app scope (see app fixture comment on singleton safety).
        app.before_request_funcs["fs"] = []

        @app.before_request
        def _set_user():
            g.user = {"id": 1, "username": "testuser", "system_account": "testuser"}

        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home_root)),
            # Simulate running as root so api_upload_file takes the
            # temp→chown→replace branch.
            patch("app.routes.fs.os.geteuid", return_value=0),
            # Stub chown to succeed (the actual os.chown would need root).
            patch("app.routes.fs._chown_to_user", return_value=True),
            # Bypass the sudo-based writable check (no real "testuser" OS user
            # exists on the dev machine).
            patch(
                "app.routes.fs.get_directory_info",
                return_value={
                    "exists": True,
                    "is_dir": True,
                    "is_writable": True,
                    "is_readable": True,
                },
            ),
        ):
            yield app.test_client()

    def test_root_upload_chown_succeeds(self, root_client, workspace):
        _, user_home = workspace
        data = {
            "file": (io.BytesIO(b"hello"), "f.txt"),
            "path": str(user_home),
        }
        resp = root_client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert (user_home / "f.txt").read_bytes() == b"hello"

    def test_root_upload_chown_failure_rolls_back(self, workspace):
        """When _chown_to_user fails, the upload must fail AND no file is left."""
        from flask import Flask, g

        from app.routes.fs import fs_bp

        ws_root, _ = workspace
        home_root = ws_root / "testuser"
        home_root.mkdir(exist_ok=True)

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fs_bp, url_prefix="/api")
        app.before_request_funcs["fs"] = []  # app-scope only (see app fixture)

        @app.before_request
        def _set_user():
            g.user = {"id": 1, "username": "testuser", "system_account": "testuser"}

        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home_root)),
            patch("app.routes.fs.os.geteuid", return_value=0),
            # chown fails — upload must abort and clean up the temp file.
            patch("app.routes.fs._chown_to_user", return_value=False),
            patch(
                "app.routes.fs.get_directory_info",
                return_value={
                    "exists": True,
                    "is_dir": True,
                    "is_writable": True,
                    "is_readable": True,
                },
            ),
        ):
            client = app.test_client()
            data = {
                "file": (io.BytesIO(b"hello"), "f.txt"),
                "path": str(home_root),
            }
            resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 500
        assert "ownership" in resp.get_json()["error"].lower()
        # No leftover file (neither final nor .openace-upload- temp).
        assert not (home_root / "f.txt").exists()
        temps = list(home_root.glob(".openace-upload-*"))
        assert temps == [], f"temp file leaked: {temps}"


class TestContentLengthPrecheck:
    """The upload endpoint rejects oversized requests via Content-Length header
    before the body is fully buffered (cheap DoS guard).

    We exercise the check directly via the in-process test client: rather than
    fake a header, we temporarily set MAX_UPLOAD_SIZE_MB to a value below the
    real Content-Length of a small body, so the declared-size branch trips
    before file buffering. (Faking the CONTENT_LENGTH environ on Werkzeug's
    test client is unreliable across versions; this approach exercises the
    exact same ``request.content_length > max_bytes`` comparison.)
    """

    def test_rejects_oversized_content_length(self, client, workspace):
        import app.routes.fs as _fsm

        orig = _fsm.MAX_UPLOAD_SIZE_MB
        _, user_home = workspace
        # Cap below the declared multipart body size so the pre-check trips.
        # The body is well over 1 byte, so 0-byte cap triggers it.
        _fsm.MAX_UPLOAD_SIZE_MB = 0
        try:
            data = {
                "file": (io.BytesIO(b"x"), "x.txt"),
                "path": str(user_home),
            }
            resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")
            assert resp.status_code == 413
        finally:
            _fsm.MAX_UPLOAD_SIZE_MB = orig


class TestDownloadContentDisposition:
    """Download Content-Disposition handles non-ASCII / quote chars safely."""

    def test_ascii_filename(self, client, workspace):
        _, user_home = workspace
        (user_home / "doc.txt").write_bytes(b"x")
        resp = client.get(f"/api/fs/download?path={user_home / 'doc.txt'}")
        cd = resp.headers.get("Content-Disposition", "")
        assert 'filename="doc.txt"' in cd

    def test_non_ascii_filename_uses_rfc5987(self, client, workspace):
        from urllib.parse import quote

        _, user_home = workspace
        # Create a file with a non-ASCII name via the filesystem directly.
        target = user_home / "报告.txt"
        target.write_bytes(b"x")
        resp = client.get(f"/api/fs/download?path={target}")
        cd = resp.headers.get("Content-Disposition", "")
        # filename* present and percent-encoded UTF-8.
        expected_star = f"filename*=UTF-8''{quote('报告.txt')}"
        assert expected_star in cd, f"missing RFC 5987 form in: {cd}"


def _mkproc(stdout="", returncode=0, stderr=""):
    """Build a fake CompletedProcess for run_as_user mocking."""
    from unittest.mock import MagicMock

    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


class TestListSubdirectoriesSudoBranch:
    """Cover the multi-user (sudo) branch of list_subdirectories.

    This is the code path that uses ``stat -c "%n\\t%U\\t%F\\t%s\\t%A"`` to
    batch-list entries. It is the most complex / bug-prone part of the feature
    (review #2 caught an owner-triplet permission bug here because it had no
    coverage). We mock run_as_user to feed constructed ls/stat/test output and
    assert on parsing + the owner-aware permission logic.
    """

    @pytest.fixture
    def sudo_user(self):
        """User with a system_account so get_effective_system_account != None."""
        return {"id": 1, "username": "alice", "system_account": "alice"}

    def _mock_run_as_user(self, path, stat_lines, test_results=None):
        """Return a side_effect that fakes ls + stat + per-entry test calls.

        stat_lines: list of "name\\towner\\ttype\\tsize\\tperm" strings (the
                    stdout of the batched stat call).
        test_results: dict full_path -> {"readable": bool, "writable": bool}
                      for entries that need the owner-mismatch fallback.
        """
        test_results = test_results or {}

        def fake_run(account, cmd):
            # ls -1 <path> — emits basenames only (matches real ls behavior).
            if cmd[:2] == ["ls", "-1"]:
                names = [line.split("\t")[0].rsplit("/", 1)[-1] for line in stat_lines]
                return _mkproc(stdout="\n".join(names))
            # stat -c <fmt> <paths...>
            if cmd[:2] == ["stat", "-c"]:
                return _mkproc(stdout="\n".join(stat_lines))
            # test -r/-w/-d/-e <path>
            if cmd[0] == "test":
                flag, target = cmd[1], cmd[2]
                tr = test_results.get(target, {})
                if flag == "-r":
                    return _mkproc(returncode=0 if tr.get("readable") else 1)
                if flag == "-w":
                    return _mkproc(returncode=0 if tr.get("writable") else 1)
                return _mkproc(returncode=1)
            return _mkproc()

        return fake_run

    def test_parses_owner_matched_entries_via_mode_bits(self, sudo_user):
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        # Two entries both owned by alice → %A owner bits used directly.
        stat_lines = [
            f"{path}/project\talice\tdirectory\t0\tdrwxr-xr-x",
            f"{path}/notes.txt\talice\tregular file\t100\t-rw-r--r--",
        ]
        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(path, stat_lines),
            ),
        ):
            result = list_subdirectories(path, "alice", include_files=True)

        dirs = {d["name"]: d for d in result["directories"]}
        assert "project" in dirs
        # owner==alice + 'rwx' for owner → readable & writable.
        assert dirs["project"]["isReadable"] is True
        assert dirs["project"]["isWritable"] is True

        files = {f["name"]: f for f in result["files"]}
        assert files["notes.txt"]["size"] == 100
        assert files["notes.txt"]["is_readable"] is True  # owner r bit set

    def test_owner_mismatch_falls_back_to_test_r_w(self, sudo_user):
        """Regression for review #2: %A owner bits must NOT be used when the
        file owner != system_account. A root-owned 0600 file must report
        is_readable=False for alice (via test -r fallback), not True."""
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        # root owns root_only.txt with mode 0600 (-rw-------). %A owner bits
        # would say readable/writable, but alice (not root) has NO access.
        stat_lines = [
            f"{path}/root_only.txt\troot\tregular file\t42\t-rw-------",
        ]
        test_results = {
            f"{path}/root_only.txt": {"readable": False, "writable": False},
        }
        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(path, stat_lines, test_results=test_results),
            ),
        ):
            result = list_subdirectories(path, "alice", include_files=True)

        files = {f["name"]: f for f in result["files"]}
        assert "root_only.txt" in files
        # Critical: must reflect the actual test -r result (False), NOT the
        # owner-triplet %A value (which would wrongly say True).
        assert files["root_only.txt"]["is_readable"] is False

    def test_stat_returncode_nonzero_still_parses_stdout(self):
        """Per review #2: a single un-stat-able entry must not blank the
        whole directory. We parse stdout regardless of returncode."""
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        stat_lines = [f"{path}/ok.txt\talice\tregular file\t5\t-rw-r--r--"]

        def fake_run(account, cmd):
            if cmd[:2] == ["ls", "-1"]:
                return _mkproc(stdout="ok.txt")
            if cmd[:2] == ["stat", "-c"]:
                # Return nonzero (e.g. one sibling entry failed) but still
                # emit stdout for the good entry.
                return _mkproc(stdout="\n".join(stat_lines), returncode=1)
            return _mkproc(returncode=1)

        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch("app.routes.fs.run_as_user", side_effect=fake_run),
        ):
            result = list_subdirectories(path, "alice", include_files=True)

        files = {f["name"]: f for f in result["files"]}
        # The good entry still appears even though stat returned nonzero.
        assert "ok.txt" in files

    def test_filename_with_tab_does_not_pollute_other_entries(self):
        """Per review #2: a filename containing a tab shifts columns. The
        parser must skip the malformed line (path not in name_by_path)
        instead of corrupting a sibling entry."""
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        # Construct a stat stdout where a tab-containing name breaks parsing.
        # ls -1 emits "a\tb.txt" as one filename line.
        good = f"{path}/good.txt"
        malformed_name = "a\tb.txt"  # ls would print this as one line

        def fake_run(account, cmd):
            if cmd[:2] == ["ls", "-1"]:
                return _mkproc(stdout="good.txt\n" + malformed_name)
            if cmd[:2] == ["stat", "-c"]:
                # The malformed entry's stat line splits into 6 tab-fields
                # instead of 5 → len(parts) >= 5 still passes, but the first
                # field is "a" (not a known candidate path) → dropped by the
                # name_by_path check. The good entry is unaffected.
                return _mkproc(
                    stdout="\n".join(
                        [
                            f"{good}\talice\tregular file\t5\t-rw-r--r--",
                            "a\tb.txt\talice\tregular file\t5\t-rw-r--r--",
                        ]
                    )
                )
            return _mkproc()

        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch("app.routes.fs.run_as_user", side_effect=fake_run),
        ):
            result = list_subdirectories(path, "alice", include_files=True)

        files = {f["name"]: f for f in result["files"]}
        # good.txt survives, malformed entry dropped (not added with wrong attrs).
        assert "good.txt" in files
        assert "a" not in files  # the corrupted first-field must not appear

    def test_mixed_dirs_and_files(self, sudo_user):
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        stat_lines = [
            f"{path}/subdir\talice\tdirectory\t0\tdrwxr-xr-x",
            f"{path}/a.txt\talice\tregular file\t10\t-rw-r--r--",
            f"{path}/b.txt\talice\tregular file\t20\t-r--r--r--",
        ]
        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(path, stat_lines),
            ),
        ):
            result = list_subdirectories(path, "alice", include_files=True)

        dir_names = {d["name"]: d for d in result["directories"]}
        file_names = {f["name"]: f for f in result["files"]}
        assert set(dir_names) == {"subdir"}
        assert set(file_names) == {"a.txt", "b.txt"}

        # subdir is owner-rwx (drwxr-xr-x) → readable & writable.
        assert dir_names["subdir"]["isReadable"] is True
        assert dir_names["subdir"]["isWritable"] is True

        # b.txt is owner read-only (perm -r--r--r--, owner bits r--) → readable,
        # but file entries do NOT carry an isWritable field (the delete button
        # is gated by the parent directory's isWritable, not per-file). Assert
        # the readable flag we DO expose.
        b = file_names["b.txt"]
        assert b["is_readable"] is True
        # a.txt is owner rw (perm -rw-r--r--).
        assert file_names["a.txt"]["is_readable"] is True

    def test_include_files_false_skips_files_but_keeps_dirs(self, sudo_user):
        from app.routes.fs import list_subdirectories

        path = "/workspace/alice"
        stat_lines = [
            f"{path}/subdir\talice\tdirectory\t0\tdrwxr-xr-x",
            f"{path}/a.txt\talice\tregular file\t10\t-rw-r--r--",
        ]
        with (
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(path, stat_lines),
            ),
        ):
            result = list_subdirectories(path, "alice", include_files=False)

        assert {d["name"] for d in result["directories"]} == {"subdir"}
        assert result["files"] == []


class TestChownSudoFallbackFailure:
    """Cover the sudo chown fallback branch's failure path.

    Per review #2: the importlib stub in this test file sets
    ``run_as_root_if_needed = lambda cmd: None``, and _chown_to_user does
    ``getattr(r, "returncode", 0) != 0`` — which treats None as success.
    This test exercises the real failure path with a proper CompletedProcess
    so the branch is actually covered.
    """

    def test_sudo_chown_nonzero_returns_false(self):
        from app.routes.fs import _chown_to_user

        def fake_id_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            return r

        # Non-root, no wrapper → sudo chown fallback path. Make it return
        # returncode=1 (failure).
        from subprocess import CompletedProcess

        failed = CompletedProcess(
            args=["sudo", "chown", "1001:1002", "/p"], returncode=1, stderr="denied"
        )

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=False),
            patch("app.routes.fs.subprocess.run", side_effect=fake_id_run),
            patch("app.routes.fs.run_as_root_if_needed", return_value=failed),
        ):
            assert _chown_to_user("/p", "alice") is False

    def test_sudo_chown_success_returns_true(self):
        from app.routes.fs import _chown_to_user

        def fake_id_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            r.stdout = "1001\n" if "-u" in cmd else "1002\n"
            return r

        from subprocess import CompletedProcess

        ok = CompletedProcess(args=["chown", "..."], returncode=0)

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=False),
            patch("app.routes.fs.subprocess.run", side_effect=fake_id_run),
            patch("app.routes.fs.run_as_root_if_needed", return_value=ok),
        ):
            assert _chown_to_user("/p", "alice") is True


class TestDownloadDeleteSudoBranch:
    """Cover the non-root multi-user (sudo) branch of download/delete (Issue #1902).

    In this mode the Flask process runs as a service account that cannot
    traverse the target user's 0700 home directory. The endpoints must
    delegate file checks and reads/removes to ``sudo -u <system_account>``
    instead of using ``os.path.isfile`` / ``os.access`` (which silently
    return False on EACCES and produce a misleading "Not a file" error).

    We mock ``os.geteuid`` to a non-root uid and ``get_effective_system_account``
    to return the target user so the sudo code path is taken, then stub
    ``run_as_user`` and ``subprocess.Popen`` to fake ``test`` / ``stat`` /
    ``cat`` / ``rm`` results.
    """

    @pytest.fixture
    def sudo_client(self, workspace):
        from flask import Flask, g

        from app.routes.fs import fs_bp

        ws_root, user_home = workspace
        home_root = ws_root / "testuser"
        home_root.mkdir(exist_ok=True)

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fs_bp, url_prefix="/api")
        app.before_request_funcs["fs"] = []

        @app.before_request
        def _set_user():
            g.user = {"id": 1, "username": "testuser", "system_account": "testuser"}

        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home_root)),
            # Non-root process: forces the sudo code path.
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch(
                "app.routes.fs.get_effective_system_account", return_value="testuser"
            ),
        ):
            yield app.test_client()

    def _mock_run_as_user(self, is_file=True, is_readable=True, size=11, rm_ok=True):
        """Build a side_effect faking test/stat/rm calls from run_as_user."""
        from subprocess import CompletedProcess

        def fake_run(account, cmd):
            # test -f <path>
            if cmd[:2] == ["test", "-f"]:
                return CompletedProcess(args=cmd, returncode=0 if is_file else 1)
            # test -r <path>
            if cmd[:2] == ["test", "-r"]:
                return CompletedProcess(args=cmd, returncode=0 if is_readable else 1)
            # stat -c %s <path>
            if cmd[:2] == ["stat", "-c"]:
                return CompletedProcess(
                    args=cmd, returncode=0, stdout=str(size) + "\n"
                )
            # rm -- <path>
            if cmd[:1] == ["rm"]:
                return CompletedProcess(
                    args=cmd,
                    returncode=0 if rm_ok else 1,
                    stderr="" if rm_ok else "Permission denied",
                )
            return CompletedProcess(args=cmd, returncode=1)

        return fake_run

    def test_download_streams_via_sudo_cat(self, sudo_client, workspace):
        """Download in non-root multi-user mode streams via ``sudo -u cat``."""
        _, user_home = workspace
        target = user_home / "doc.txt"
        target.write_bytes(b"file contents")  # 12 bytes

        payload = b"file contents"

        class _FakeProc:
            def __init__(self, data):
                self._data = data
                self.stdout = self._FakeStream(data)
                self.stderr = None
                self.returncode = 0

            class _FakeStream:
                def __init__(self, data):
                    self._data = data
                    self._pos = 0

                def read(self, n):
                    chunk = self._data[self._pos : self._pos + n]
                    self._pos += len(chunk)
                    return chunk

                def close(self):
                    pass

            def wait(self):
                return 0

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(size=len(payload)),
            ),
            patch(
                "app.routes.fs.subprocess.Popen",
                return_value=_FakeProc(payload),
            ) as popen_mock,
        ):
            resp = sudo_client.get(f"/api/fs/download?path={target}")

        assert resp.status_code == 200
        assert resp.data == payload
        assert resp.headers.get("Content-Length") == str(len(payload))
        # Popen should have been invoked with sudo -u testuser cat <path>.
        popen_args = popen_mock.call_args[0][0]
        assert popen_args[:3] == ["sudo", "-u", "testuser"]
        assert "cat" in popen_args
        assert str(target) in popen_args

    def test_download_not_a_file_uses_test_f(self, sudo_client, workspace):
        """When ``test -f`` fails (returncode 1), return 400 Not a file — NOT
        a misleading 500 or a silent 200 with empty body."""
        _, user_home = workspace
        target = user_home / "missing.txt"

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(is_file=False),
            ),
            patch("app.routes.fs.subprocess.Popen") as popen_mock,
        ):
            resp = sudo_client.get(f"/api/fs/download?path={target}")

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Not a file"
        # Must not have spawned cat at all.
        popen_mock.assert_not_called()

    def test_download_not_readable_returns_403(self, sudo_client, workspace):
        """``test -r`` failing must surface as 403, not 'Not a file'."""
        _, user_home = workspace
        target = user_home / "secret.txt"

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(is_readable=False),
            ),
            patch("app.routes.fs.subprocess.Popen") as popen_mock,
        ):
            resp = sudo_client.get(f"/api/fs/download?path={target}")

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Permission denied"
        popen_mock.assert_not_called()

    def test_delete_via_sudo_rm(self, sudo_client, workspace):
        """Delete in non-root multi-user mode runs ``sudo -u rm``."""
        _, user_home = workspace
        target = user_home / "trash.txt"
        target.write_bytes(b"x")

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=self._mock_run_as_user(rm_ok=True),
            ) as run_mock,
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # Verify the rm command was issued with the target path.
        rm_calls = [c for c in run_mock.call_args_list if c[0][1][:1] == ["rm"]]
        assert len(rm_calls) == 1
        assert rm_calls[0][0][1][:2] == ["rm", "--"]

    def test_delete_not_a_file_uses_test_f(self, sudo_client, workspace):
        """Delete also benefits from the test -f check: a path that the
        process user can't stat is reported as 'Not a file' via sudo test,
        not via the misleading direct os.path.isfile (Issue #1902)."""
        _, user_home = workspace
        target = user_home / "ghost.txt"

        with patch(
            "app.routes.fs.run_as_user",
            side_effect=self._mock_run_as_user(is_file=False),
        ) as run_mock:
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Not a file"
        # Must not have attempted rm.
        rm_calls = [c for c in run_mock.call_args_list if c[0][1][:1] == ["rm"]]
        assert rm_calls == []

    def test_delete_rm_failure_returns_403(self, sudo_client, workspace):
        """If ``sudo -u rm`` fails with a filesystem permission error
        (not a sudoers policy issue), surface as 403."""
        _, user_home = workspace
        target = user_home / "locked.txt"

        with patch(
            "app.routes.fs.run_as_user",
            side_effect=self._mock_run_as_user(rm_ok=False),
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Permission denied"


class TestDirectAccessHelper:
    """Unit tests for _is_direct_access / _check_file_as_user helpers."""

    def test_is_direct_access_root(self):
        from app.routes.fs import _is_direct_access

        with patch("app.routes.fs.os.geteuid", return_value=0):
            assert _is_direct_access("alice") is True

    def test_is_direct_access_single_user(self):
        from app.routes.fs import _is_direct_access

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch(
                "app.routes.fs.get_effective_system_account", return_value=None
            ),
        ):
            # No system_account or process == target → direct access.
            assert _is_direct_access(None) is True

    def test_is_direct_access_multi_user(self):
        from app.routes.fs import _is_direct_access

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch(
                "app.routes.fs.get_effective_system_account", return_value="alice"
            ),
        ):
            assert _is_direct_access("alice") is False

    def test_check_file_as_user_uses_sudo_test(self):
        from app.routes.fs import _check_file_as_user
        from subprocess import CompletedProcess

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch(
                "app.routes.fs.get_effective_system_account", return_value="alice"
            ),
            patch(
                "app.routes.fs.run_as_user",
                return_value=CompletedProcess(args=[], returncode=0),
            ) as run_mock,
        ):
            assert _check_file_as_user("/p/f.txt", "alice", "-f") is True
        # Should have called sudo -u alice test -f /p/f.txt
        call = run_mock.call_args[0]
        assert call[0] == "alice"
        assert call[1] == ["test", "-f", "/p/f.txt"]
