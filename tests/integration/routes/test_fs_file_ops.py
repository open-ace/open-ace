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

import contextlib
import io
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ---------------------------------------------------------------------------
# Pre-load app.routes.fs directly from its file, bypassing the package
# __init__.py. The package init imports the full route registry (admin, auth,
# …), which triggers app.repositories.database → scripts/shared, a module with
# a pre-existing surrogate-char bug on some dev machines unrelated to this PR.
# The REAL import is attempted first: stubbing sys.modules unconditionally
# would leak `__path__ = []` packages into the process and break later test
# modules that import `from app.services import …` normally (standalone runs
# of this file were the crash; mixed-order runs were the poisoning). Only when
# the real import fails does the file-load fallback below install its stubs.
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402

try:
    import app.routes.fs  # noqa: F401
except Exception:  # pragma: no cover - dev-machine-specific import failure
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
        _ws.OPENACE_RM_WRAPPER = _rw.OPENACE_RM_WRAPPER
        _ws.OPENACE_WRITE_AS_WRAPPER = _rw.OPENACE_WRITE_AS_WRAPPER
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
    # The uuid suffix is load-bearing: CI runs `pytest -n auto`, which spreads
    # one module's tests across workers — a fixed name made two workers rmtree
    # each other's tree mid-test (the same hazard the `tree` fixture below
    # documents).
    ws = Path.home() / f".ace_fs_test_ws_routes_{uuid.uuid4().hex[:8]}"
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
        # #3410 review: the temp+rename write pins uploads to 0600 (the
        # compatibility table's declared change); a refactor that quietly
        # drops the mode argument stays red here.
        import stat as _stat

        assert _stat.S_IMODE((user_home / "report.txt").stat().st_mode) == 0o600

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


def _pw_entry(uid: int = 1001, gid: int = 1002):
    """Stand-in for ``pwd.getpwnam`` (#3410: the uid lookup no longer forks ``id``)."""
    import pwd

    return pwd.struct_passwd(("alice", "x", uid, gid, "", "/home/alice", "/bin/bash"))


class TestChownHelper:
    """Unit tests for _chown_to_user covering all three branches.

    These mock the pwd lookup and subprocess/os primitives so we can exercise
    the root / wrapper / sudo-fallback paths without a real multi-user OS.
    """

    def test_no_system_account_is_noop_success(self):
        from app.routes.fs import _chown_to_user

        # None → single-user, nothing to do, reports success.
        assert _chown_to_user("/any/path", None) is True

    def test_uid_lookup_failure_returns_false(self):
        from app.routes.fs import _chown_to_user

        with patch("app.routes.fs.pwd.getpwnam", side_effect=KeyError("ghost")):
            assert _chown_to_user("/p", "ghost") is False

    def test_root_uses_os_chown(self):
        from app.routes.fs import _chown_to_user

        with (
            patch("app.routes.fs.os.geteuid", return_value=0),
            patch("app.routes.fs.os.chown") as chown_mock,
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
        ):
            assert _chown_to_user("/p", "alice") is True
        chown_mock.assert_called_once_with("/p", 1001, 1002)

    def test_root_chown_raises_returns_false(self):
        from app.routes.fs import _chown_to_user

        with (
            patch("app.routes.fs.os.geteuid", return_value=0),
            patch("app.routes.fs.os.chown", side_effect=PermissionError("denied")),
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
        ):
            assert _chown_to_user("/p", "alice") is False

    def test_non_root_uses_wrapper_when_available(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
            patch("app.routes.fs.subprocess.run", side_effect=fake_run) as run_mock,
        ):
            assert _chown_to_user("/p", "alice") is True
        # The ONLY subprocess is the wrapper itself, fed the looked-up ids.
        run_mock.assert_called_once()
        assert run_mock.call_args.args[0][1:] == ["1001:1002", "/p"]

    def test_wrapper_nonzero_return_returns_false(self):
        from app.routes.fs import _chown_to_user

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.returncode = 1  # wrapper exit code
            r.stdout = ""
            r.stderr = "wrapper denied"
            return r

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
            patch("app.routes.fs.subprocess.run", side_effect=fake_run),
        ):
            assert _chown_to_user("/p", "alice") is False


@pytest.mark.regression
@pytest.mark.issue(3410)
def test_uid_lookup_is_one_getpwnam_and_never_parses_the_name_as_an_option():
    """#3410 review: ``id -u <name>`` read a leading ``-`` as an option.

    ``validate_username`` admits a name like ``-r``; ``id -u -r`` then printed
    the CALLER's real uid. The lookup is now a single ``getpwnam`` that treats
    the name as data and forks nothing.
    """
    from app.routes.fs import _resolve_uid_gid

    with (
        patch("app.routes.fs.pwd.getpwnam", side_effect=KeyError("-r")) as lookup,
        patch("app.routes.fs.subprocess.run") as run_mock,
    ):
        assert _resolve_uid_gid("-r") is None
    lookup.assert_called_once_with("-r")
    run_mock.assert_not_called()


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
            # #3410: the root branch now uses the fd form.
            patch("app.routes.fs._fchown_to_user", return_value=True),
            # STANDING RULE (#3410): every helper the ROOT branch calls must
            # be patched here. `_resolve_uid_gid` shells out to `id -u
            # testuser`, which fails on CI and on any dev box — the route would
            # then 400 on the ownership gate before doing anything else.
            patch("app.routes.fs._resolve_uid_gid", return_value=(os.getuid(), os.getgid())),
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
        # #3410: same 0600 contract as the direct branch (temp+rename).
        import stat as _stat

        assert _stat.S_IMODE((user_home / "f.txt").stat().st_mode) == 0o600

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
            patch("app.routes.fs._fchown_to_user", return_value=False),
            # STANDING RULE (#3410): every helper the ROOT branch calls must
            # be patched here. `_resolve_uid_gid` shells out to `id -u
            # testuser`, which fails on CI and on any dev box — the route would
            # then 400 on the ownership gate before doing anything else.
            patch("app.routes.fs._resolve_uid_gid", return_value=(os.getuid(), os.getgid())),
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
        # Non-root, no wrapper → sudo chown fallback path. Make it return
        # returncode=1 (failure).
        from subprocess import CompletedProcess

        from app.routes.fs import _chown_to_user

        failed = CompletedProcess(
            args=["sudo", "chown", "1001:1002", "/p"], returncode=1, stderr="denied"
        )

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=False),
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
            patch("app.routes.fs.run_as_root_if_needed", return_value=failed),
        ):
            assert _chown_to_user("/p", "alice") is False

    def test_sudo_chown_success_returns_true(self):
        from subprocess import CompletedProcess

        from app.routes.fs import _chown_to_user

        ok = CompletedProcess(args=["chown", "..."], returncode=0)

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs._is_wrapper_available", return_value=False),
            patch("app.routes.fs.pwd.getpwnam", return_value=_pw_entry()),
            patch("app.routes.fs.run_as_root_if_needed", return_value=ok) as chown_mock,
        ):
            assert _chown_to_user("/p", "alice") is True
        chown_mock.assert_called_once_with(["chown", "1001:1002", "/p"])


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
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            # #3410: deletes need an account-scoped openace-rm (fail-closed gate).
            patch("app.routes.fs._rm_wrapper_is_account_scoped", return_value=True),
        ):
            yield app.test_client()

    @pytest.mark.security
    @pytest.mark.regression
    @pytest.mark.issue(3410)
    def test_delete_refuses_when_the_installed_rm_wrapper_is_outdated(self, sudo_client, workspace):
        """#3410 review: a pre-#3410 openace-rm ran its final rm as root."""
        _, user_home = workspace
        target = user_home / "keep.txt"
        target.write_bytes(b"x")
        with (
            patch("app.routes.fs._rm_wrapper_is_account_scoped", return_value=False),
            patch("app.routes.fs.run_as_user") as run_as_user_mock,
            patch("app.routes.fs.subprocess.run") as run_mock,
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})
        assert resp.status_code == 500
        assert "reinstall" in resp.get_json()["error"].lower()
        run_as_user_mock.assert_not_called()
        run_mock.assert_not_called()
        assert target.exists()

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
                return CompletedProcess(args=cmd, returncode=0, stdout=str(size) + "\n")
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
        """Delete in non-root multi-user mode runs ``sudo openace-rm``."""
        _, user_home = workspace
        target = user_home / "trash.txt"
        target.write_bytes(b"x")

        from subprocess import CompletedProcess

        def fake_run_as_user(account, cmd):
            # Mock test -f call
            if cmd[:2] == ["test", "-f"]:
                return CompletedProcess(args=cmd, returncode=0)
            return CompletedProcess(args=cmd, returncode=1)

        def fake_subprocess(cmd, *args, **kwargs):
            # Mock openace-rm wrapper call: sudo -n /usr/local/bin/openace-rm <user> <path>
            if any("openace-rm" in part for part in cmd):
                return CompletedProcess(args=cmd, returncode=0, stderr="")
            return CompletedProcess(args=cmd, returncode=1)

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=fake_run_as_user,
            ),
            patch(
                "app.routes.fs.subprocess.run",
                side_effect=fake_subprocess,
            ) as run_mock,
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # Verify openace-rm was called with target user and path
        rm_calls = [c for c in run_mock.call_args_list if "openace-rm" in str(c)]
        assert len(rm_calls) == 1
        # cmd[0] is the list argument: ["sudo", "-n", "/usr/local/bin/openace-rm", user, path]
        call_args = rm_calls[0][0][0]
        assert call_args[:2] == ["sudo", "-n"]
        assert "openace-rm" in call_args[2]

    def test_delete_not_a_file_uses_test_f(self, sudo_client, workspace):
        """Delete also benefits from the test -f check: a path that the
        process user can't stat is reported as 'Not a file' via sudo test,
        not via the misleading direct os.path.isfile (Issue #1902)."""
        _, user_home = workspace
        target = user_home / "ghost.txt"

        from subprocess import CompletedProcess

        def fake_run_as_user(account, cmd):
            # Mock test -f call - file does not exist
            if cmd[:2] == ["test", "-f"]:
                return CompletedProcess(args=cmd, returncode=1)
            return CompletedProcess(args=cmd, returncode=1)

        def fake_subprocess(cmd, *args, **kwargs):
            # Should not be called since test -f fails first
            return CompletedProcess(args=cmd, returncode=1)

        with (
            patch(
                "app.routes.fs.run_as_user",
                side_effect=fake_run_as_user,
            ) as run_mock,
            patch("app.routes.fs.subprocess.run", side_effect=fake_subprocess),
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Not a file"
        # Must not have attempted openace-rm (test -f failed first)
        test_calls = [c for c in run_mock.call_args_list if c[0][1][:2] == ["test", "-f"]]
        assert len(test_calls) >= 1  # At least one test -f call

    def test_delete_rm_failure_returns_403(self, sudo_client, workspace):
        """If ``sudo openace-rm`` fails with a permission error,
        surface as 403."""
        _, user_home = workspace
        target = user_home / "locked.txt"

        from subprocess import CompletedProcess

        def fake_run_as_user(account, cmd):
            # Mock test -f call - file exists
            if cmd[:2] == ["test", "-f"]:
                return CompletedProcess(args=cmd, returncode=0)
            return CompletedProcess(args=cmd, returncode=1)

        def fake_subprocess(cmd, *args, **kwargs):
            # Mock openace-rm wrapper call - fails with permission denied
            if any("openace-rm" in part for part in cmd):
                # Return code 2 = invalid path or access denied
                return CompletedProcess(
                    args=cmd, returncode=2, stderr="Path validation failed: outside allowed roots"
                )
            return CompletedProcess(args=cmd, returncode=1)

        with (
            patch("app.routes.fs.run_as_user", side_effect=fake_run_as_user),
            patch("app.routes.fs.subprocess.run", side_effect=fake_subprocess),
        ):
            resp = sudo_client.post("/api/fs/delete-file", json={"path": str(target)})

        assert resp.status_code == 403
        assert "Invalid path" in resp.get_json()["error"]


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
            patch("app.routes.fs.get_effective_system_account", return_value=None),
        ):
            # No system_account or process == target → direct access.
            assert _is_direct_access(None) is True

    def test_is_direct_access_multi_user(self):
        from app.routes.fs import _is_direct_access

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
        ):
            assert _is_direct_access("alice") is False

    def test_check_file_as_user_uses_sudo_test(self):
        from subprocess import CompletedProcess

        from app.routes.fs import _check_file_as_user

        with (
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="alice"),
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


class TestUploadNonRootMultiUserBranch:
    """Cover the Package non-root multi-user upload path (Issue #1916).

    In this mode the Flask process is a service account (non-root) with a
    ``system_account`` user whose 0700 home it cannot traverse. The endpoint
    must delegate the write to the ``openace-write-as`` wrapper (sudoers-
    authorized, runs as root, drops to the target user via runuser) instead
    of calling ``file.save()`` directly (which would hit EACCES).
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
            # Non-root process → _is_direct_access returns False.
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            # Wrapper is installed.
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            # #3410: ...and carries the symlink-refusal capability marker.
            # /usr/local/bin/openace-write-as does not exist on CI, so without
            # this the route fails closed with a 500 before spawning anything.
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=True),
            # Bypass the sudo-based writable pre-check (no real "testuser" OS
            # user exists on the dev machine).
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

    class _FakeProc:
        """Minimal Popen stand-in that records stdin writes and exits 0."""

        def __init__(self, returncode=0, stderr=b""):
            self._returncode = returncode
            self._stderr = stderr
            self._written = bytearray()
            self.stdin = self._FakeStream(self)
            self.stdout = None
            self.returncode = None

        class _FakeStream:
            def __init__(self, parent):
                self._parent = parent

            def write(self, data):
                self._parent._written.extend(data)
                return len(data)

            def close(self):
                pass

        def communicate(self, timeout=None):
            self.returncode = self._returncode
            return (b"", self._stderr)

        def wait(self):
            self.returncode = self._returncode
            return self._returncode

        def kill(self):
            self.returncode = -9

    def test_upload_streams_via_write_as_wrapper(self, sudo_client, workspace):
        """Upload in non-root multi-user mode streams content to the
        openace-write-as wrapper and returns 200 on success."""
        _, user_home = workspace
        target = user_home / "up.txt"

        fake = self._FakeProc(returncode=0)
        with patch("app.routes.fs.subprocess.Popen", return_value=fake) as popen_mock:
            data = {
                "file": (io.BytesIO(b"hello-upload"), "up.txt"),
                "path": str(user_home),
            }
            resp = sudo_client.post("/api/fs/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # The wrapper was invoked as: sudo -n /usr/local/bin/openace-write-as <user> <path>
        cmd = popen_mock.call_args[0][0]
        assert cmd[:2] == ["sudo", "-n"]
        assert "/usr/local/bin/openace-write-as" in cmd
        assert "testuser" in cmd
        assert str(target) in cmd
        # The uploaded bytes were streamed to the wrapper's stdin.
        assert bytes(fake._written) == b"hello-upload"

    def test_upload_wrapper_failure_returns_403(self, sudo_client, workspace):
        """When the wrapper exits non-zero with a filesystem error, surface 403."""
        _, user_home = workspace

        fake = self._FakeProc(returncode=4, stderr=b"Permission denied")
        with patch("app.routes.fs.subprocess.Popen", return_value=fake):
            data = {
                "file": (io.BytesIO(b"x"), "up.txt"),
                "path": str(user_home),
            }
            resp = sudo_client.post("/api/fs/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 403
        assert resp.get_json()["error"] == "Permission denied"

    def test_upload_sudoers_policy_missing_returns_500(self, sudo_client, workspace):
        """When the wrapper is denied by sudoers (not authorized), return a
        clear 500 referencing the missing sudoers policy — mirroring delete's
        run_as_user failure handling."""
        _, user_home = workspace

        fake = self._FakeProc(
            returncode=1,
            stderr=b"sudo: a password is required for openace",
        )
        with patch("app.routes.fs.subprocess.Popen", return_value=fake):
            data = {
                "file": (io.BytesIO(b"x"), "up.txt"),
                "path": str(user_home),
            }
            resp = sudo_client.post("/api/fs/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 500
        assert "sudoers policy" in resp.get_json()["error"]

    def test_upload_wrapper_not_installed_returns_500(self, workspace):
        """When the openace-write-as wrapper is absent, upload in multi-user
        mode fails with a clear error rather than falling through to the
        broken direct-write path (which would EACCES)."""
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
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            # Wrapper NOT installed.
            patch("app.routes.fs._is_wrapper_available", return_value=False),
            patch(
                "app.routes.fs.get_directory_info",
                return_value={
                    "exists": True,
                    "is_dir": True,
                    "is_writable": True,
                    "is_readable": True,
                },
            ),
            patch("app.routes.fs.subprocess.Popen") as popen_mock,
        ):
            client = app.test_client()
            data = {
                "file": (io.BytesIO(b"x"), "up.txt"),
                "path": str(home_root),
            }
            resp = client.post("/api/fs/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 500
        assert "openace-write-as" in resp.get_json()["error"]
        # Must not have spawned the wrapper at all.
        popen_mock.assert_not_called()


class _RootBranchHarness:
    """Shared #3410 fixtures: a unique tree under the real home + a root env.

    Not collected (no ``Test`` prefix); the symlink test classes inherit it.
    """

    @pytest.fixture
    def tree(self):
        # Own unique root (not the `workspace` fixture): this file runs under
        # `pytest -n auto` in the python-core lane with --maxfail=1, so a
        # cross-worker collision costs the lane.
        # is_valid_path blacklists /var (macOS tmp resolves under /private/var),
        # so anchor under the real home rather than tmp_path.
        ws_root = Path.home() / f".ace_fs_3410_{uuid.uuid4().hex[:8]}"
        (ws_root / "testuser").mkdir(parents=True)
        (ws_root / "victim").mkdir(parents=True)
        (ws_root / "victim" / "secret.txt").write_text("VICTIM-ORIGINAL")
        yield ws_root, ws_root / "testuser", ws_root / "victim" / "secret.txt"
        shutil.rmtree(ws_root, ignore_errors=True)

    def _app(self, system_account="testuser"):
        from flask import Flask, g

        from app.routes.fs import fs_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fs_bp, url_prefix="/api")
        app.before_request_funcs["fs"] = []  # app-scope only (see `app` fixture)

        @app.before_request
        def _set_user():
            g.user = {"id": 1, "username": "testuser", "system_account": system_account}

        return app

    @contextlib.contextmanager
    def _root_env(self, ws_root, home):
        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=0),
            patch("app.routes.fs._fchown_to_user", return_value=True),
            # STANDING RULE (#3410 review): every helper the ROOT branch calls
            # must be patched here. `_resolve_uid_gid` shells out to
            # `id -u testuser`, which fails on CI and on any dev box — the
            # route would then 400 on the ownership gate BEFORE reaching the
            # symlink check, and the symlink tests would pass vacuously.
            # os.fstat is deliberately NOT patched (patching os.geteuid touches
            # only that attribute), so the expected uid must be the real
            # process uid that owns the test-created directory.
            patch("app.routes.fs._resolve_uid_gid", return_value=(os.getuid(), os.getgid())),
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
            yield

    @contextlib.contextmanager
    def _wrapper_env(self, ws_root, home, popen):
        """Package non-root multi-user: the upload goes to openace-write-as."""
        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=True),
            patch("app.routes.fs.os.path.islink", return_value=False),
            patch("app.routes.fs.subprocess.Popen", side_effect=popen),
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
            yield

    def _upload(self, client, path, filename, payload=b"PWNED"):
        return client.post(
            "/api/fs/upload",
            data={"file": (io.BytesIO(payload), filename), "path": str(path)},
            content_type="multipart/form-data",
        )


@pytest.mark.security
@pytest.mark.regression
@pytest.mark.issue(3410)
class TestUploadSymlinkEscape(_RootBranchHarness):
    """#3410: the upload target must never be resolved through a symlink.

    The attacker owns their home, so they can plant a symlink whose NAME is the
    name they are about to upload and whose TARGET is another user's file.
    Before the fix ``realpath(join(resolved_dir, safe_name))`` resolved to the
    victim path and the final guard only re-checked the workspace base dirs
    (not the home subtree), so the root branch's ``os.replace`` overwrote it.
    """

    def test_root_branch_symlink_to_other_user_is_rejected(self, tree):
        ws_root, home, victim = tree
        os.symlink(str(victim), str(home / "secret.txt"))
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), home, "secret.txt")
        assert resp.status_code == 400
        # The distinct symlink refusal, not a generic invalid-path 400 (the
        # wrapper branch maps exit 5 to this same message).
        assert "symbolic link" in resp.get_json()["error"]
        assert victim.read_text() == "VICTIM-ORIGINAL"
        # The attacker's own dirent is untouched and still a symlink.
        assert os.path.islink(str(home / "secret.txt"))
        assert list(home.glob(".openace-upload-*")) == []

    def test_root_branch_does_not_disclose_the_symlink_target(self, tree):
        ws_root, home, victim = tree
        os.symlink(str(victim), str(home / "secret.txt"))
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), home, "secret.txt")
        assert str(victim) not in resp.get_data(as_text=True)

    def test_root_branch_symlinked_parent_dir_is_rejected(self, tree):
        """A symlinked intermediate directory must not be traversed either."""
        ws_root, home, victim = tree
        os.symlink(str(victim.parent), str(home / "linkdir"))
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), home / "linkdir", "secret.txt")
        assert resp.status_code == 400
        assert victim.read_text() == "VICTIM-ORIGINAL"

    def test_root_branch_symlink_outside_workspace_is_rejected(self, tree):
        ws_root, home, _ = tree
        outside = ws_root.parent / f"{ws_root.name}-outside.txt"
        outside.write_text("OUTSIDE-ORIGINAL")
        try:
            os.symlink(str(outside), str(home / "out.txt"))
            with self._root_env(ws_root, home):
                resp = self._upload(self._app().test_client(), home, "out.txt")
            assert resp.status_code == 400
            assert outside.read_text() == "OUTSIDE-ORIGINAL"
        finally:
            outside.unlink(missing_ok=True)

    def test_root_branch_overwrites_a_plain_file_normally(self, tree):
        ws_root, home, victim = tree
        (home / "notes.txt").write_text("OLD")
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), home, "notes.txt", payload=b"NEW")
        assert resp.status_code == 200
        assert (home / "notes.txt").read_bytes() == b"NEW"
        assert victim.read_text() == "VICTIM-ORIGINAL"

    def test_root_branch_uploads_into_own_subdirectory(self, tree):
        ws_root, home, _ = tree
        sub = home / "docs" / "2026"
        sub.mkdir(parents=True)
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), sub, "ok.txt", payload=b"OK")
        assert resp.status_code == 200
        assert (sub / "ok.txt").read_bytes() == b"OK"

    def test_root_branch_refuses_a_home_root_not_owned_by_the_account(self, tree):
        """#3410: the anchor assertion needs POSITIVE coverage.

        Every other root-branch test patches `_resolve_uid_gid` to the process
        uid so the gate passes; this one makes it disagree, which is the only
        test that proves the gate is wired at all.
        """
        ws_root, home, _ = tree
        with (
            self._root_env(ws_root, home),
            patch(
                "app.routes.fs._resolve_uid_gid",
                return_value=(os.getuid() + 4242, os.getgid()),
            ),
        ):
            resp = self._upload(self._app().test_client(), home, "x.txt", payload=b"NOPE")
        assert resp.status_code == 400
        assert not (home / "x.txt").exists()
        assert list(home.glob(".openace-upload-*")) == []

    def test_direct_branch_rejects_a_directory_target(self, tree):
        ws_root, home, _ = tree
        (home / "notes.txt").mkdir()
        with self._root_env(ws_root, home):
            resp = self._upload(self._app().test_client(), home, "notes.txt")
        assert resp.status_code == 400
        assert "directory" in resp.get_json()["error"].lower()
        assert (home / "notes.txt").is_dir()
        # Nothing moved INTO it.
        assert list((home / "notes.txt").iterdir()) == []

    def test_direct_branch_symlink_is_rejected(self, tree):
        """Single-user / non-root: the process already owns the home."""
        ws_root, home, victim = tree
        os.symlink(str(victim), str(home / "secret.txt"))
        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
        ):
            resp = self._upload(self._app(system_account=None).test_client(), home, "secret.txt")
        assert resp.status_code == 400
        assert "symbolic link" in resp.get_json()["error"]
        assert victim.read_text() == "VICTIM-ORIGINAL"

    def test_wrapper_branch_passes_the_in_home_path_not_the_resolved_one(self, tree):
        """Package non-root multi-user.

        The web process is a service account that CANNOT traverse the target
        user's 0700 home, so the route's own ``islink`` probe cannot see the
        symlink (it returns False on EACCES) — the wrapper is the enforcement
        point. What the route must guarantee is that the path it hands over is
        the IN-HOME joined path, never the realpath-escaped one.
        """
        ws_root, home, victim = tree
        os.symlink(str(victim), str(home / "secret.txt"))
        seen = {}

        class _Proc:
            returncode = 0

            def __init__(self):
                self.stdin = io.BytesIO()

            def communicate(self, timeout=None):
                return b"", b""

        def _popen(cmd, **kwargs):
            seen["cmd"] = cmd
            return _Proc()

        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=True),
            patch("app.routes.fs.os.path.islink", return_value=False),  # simulate EACCES probe
            patch("app.routes.fs.subprocess.Popen", side_effect=_popen),
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
            self._upload(self._app().test_client(), home, "secret.txt")
        assert seen["cmd"][-1] == str(home / "secret.txt")  # NOT str(victim)

    def test_wrapper_branch_islink_probe_true_short_circuits_to_400(self, tree):
        """The route's own best-effort islink probe, when it CAN see the link
        (e.g. a same-fs deployment where the service account can traverse),
        answers the same symlink 400 WITHOUT spawning the wrapper."""
        ws_root, home, _ = tree
        os.symlink("/etc/hosts", str(home / "probe-link.txt"))
        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=True),
            patch("app.routes.fs.subprocess.Popen") as popen_mock,
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
            resp = self._upload(self._app().test_client(), home, "probe-link.txt")
        assert resp.status_code == 400
        assert "symbolic link" in resp.get_json()["error"]
        popen_mock.assert_not_called()

    def test_wrapper_branch_maps_exit_5_to_a_symlink_rejection(self, tree):
        """The wrapper's symlink refusal must surface as 400, not a bare 403."""
        ws_root, home, _ = tree

        class _Proc:
            returncode = 5

            def __init__(self):
                self.stdin = io.BytesIO()

            def communicate(self, timeout=None):
                return b"", b"ERROR: Target is a symbolic link; refusing to write through it"

        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=True),
            patch("app.routes.fs.os.path.islink", return_value=False),
            patch("app.routes.fs.subprocess.Popen", side_effect=lambda cmd, **kw: _Proc()),
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
            resp = self._upload(self._app().test_client(), home, "secret.txt")
        assert resp.status_code == 400
        assert "symbolic link" in resp.get_json()["error"].lower()

    def test_wrapper_branch_refuses_when_the_installed_wrapper_is_outdated(self, tree):
        """#3410: fail closed rather than let the contract over-claim."""
        ws_root, home, _ = tree
        with (
            patch("app.routes.fs.get_workspace_base_dir", return_value=str(ws_root)),
            patch("app.routes.fs.get_workspace_base_dirs", return_value=[str(ws_root)]),
            patch("app.routes.fs.get_home_directory", return_value=str(home)),
            patch("app.routes.fs.os.geteuid", return_value=1000),
            patch("app.routes.fs.get_effective_system_account", return_value="testuser"),
            patch("app.routes.fs._is_wrapper_available", return_value=True),
            patch("app.routes.fs._write_as_wrapper_enforces_symlink_refusal", return_value=False),
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
            resp = self._upload(self._app().test_client(), home, "plain.txt", payload=b"OK")
        assert resp.status_code == 500
        assert "reinstall" in resp.get_json()["error"].lower()

    def test_wrapper_branch_early_refusal_stays_400_when_stdin_is_broken(self, tree):
        """#3410 review: a refused upload must not turn into a 500.

        When the wrapper exits before reading the body, stdin is a broken pipe.
        Closing it by hand re-raised that (BrokenPipeError -> 500);
        communicate() owns flushing and closing stdin and ignores it.
        """
        ws_root, home, _ = tree

        class _BrokenStdin:
            def _broken(self, *args):
                raise BrokenPipeError("Broken pipe")

            write = flush = close = _broken

        class _Proc:
            returncode = 5

            def __init__(self):
                self.stdin = _BrokenStdin()

            def communicate(self, timeout=None):
                return b"", b"ERROR: Target is a symbolic link; refusing to write through it"

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return self.returncode

        with self._wrapper_env(ws_root, home, lambda cmd, **kw: _Proc()):
            resp = self._upload(
                self._app().test_client(), home, "secret.txt", payload=b"x" * (70 * 1024)
            )
        assert resp.status_code == 400
        assert "symbolic link" in resp.get_json()["error"].lower()

    def test_root_branch_checks_the_home_root_owner_not_the_leaf_directory(self, tree):
        """#3410 review: the ownership assertion is made on the ANCHOR fd.

        A subdirectory owned by someone else inside a correctly owned home is
        ordinary and must be accepted; a home ROOT with a foreign owner must
        be refused. Only ``os.fstat``'s uid is faked, keyed by inode.
        """
        ws_root, home, _ = tree
        sub = home / "owned-by-someone-else"
        sub.mkdir()
        real_fstat = os.fstat

        def foreign_owner_for(inode):
            def fake_fstat(fd):
                st = real_fstat(fd)
                if st.st_ino != inode:
                    return st
                fields = list(st)
                fields[4] += 4242  # st_uid
                return os.stat_result(fields)

            return fake_fstat

        with (
            self._root_env(ws_root, home),
            patch("app.routes.fs.os.fstat", side_effect=foreign_owner_for(sub.stat().st_ino)),
        ):
            leaf = self._upload(self._app().test_client(), sub, "leaf.txt", payload=b"LEAF")
        assert leaf.status_code == 200
        assert (sub / "leaf.txt").read_bytes() == b"LEAF"

        with (
            self._root_env(ws_root, home),
            patch("app.routes.fs.os.fstat", side_effect=foreign_owner_for(home.stat().st_ino)),
        ):
            anchor = self._upload(self._app().test_client(), sub, "anchor.txt", payload=b"NO")
        assert anchor.status_code == 400
        assert not (sub / "anchor.txt").exists()


@pytest.mark.security
@pytest.mark.regression
@pytest.mark.issue(3410)
class TestReadPathRootRace(_RootBranchHarness):
    """#3410 review round 1: download/delete-file/search on the ROOT branch.

    They validated a realpath'd path and then acted on it BY PATH, so a user
    who swapped a component for a symlink right after validation made root
    read, delete or list another user's files. Each race test performs that
    swap exactly where the attacker wins: when the route's validation helper
    returns.
    """

    @pytest.fixture
    def docs(self, tree):
        _, home, victim = tree
        (home / "docs").mkdir()
        (home / "docs" / "note.txt").write_text("MINE")
        (victim.parent / "note.txt").write_text("VICTIM-SECRET")
        (victim.parent / "needle-victim.txt").write_text("VICTIM-SECRET")
        return home / "docs"

    @contextlib.contextmanager
    def _after_validation(self, helper, swap):
        import app.routes.fs as fsm

        real = getattr(fsm, helper)

        def validate_then_swap(*args, **kwargs):
            result = real(*args, **kwargs)
            swap()
            return result

        with patch.object(fsm, helper, side_effect=validate_then_swap):
            yield

    @staticmethod
    def _swap_for_symlink(path, target):
        def swap():
            path.rename(path.with_name(path.name + ".orig"))
            os.symlink(str(target), str(path))

        return swap

    @contextlib.contextmanager
    def _foreign_home_owner(self):
        with patch(
            "app.routes.fs._resolve_uid_gid", return_value=(os.getuid() + 4242, os.getgid())
        ):
            yield

    def _download(self, path, app=None):
        client = (app or self._app()).test_client()
        return client.get("/api/fs/download", query_string={"path": str(path)})

    def _delete(self, path, app=None):
        client = (app or self._app()).test_client()
        return client.post("/api/fs/delete-file", json={"path": str(path)})

    def _search(self, root, query, app=None):
        client = (app or self._app()).test_client()
        return client.get("/api/fs/search", query_string={"path": str(root), "q": query})

    # --- download -----------------------------------------------------------

    def test_download_streams_a_plain_file(self, tree, docs):
        ws_root, home, _ = tree
        with self._root_env(ws_root, home):
            resp = self._download(docs / "note.txt")
        assert resp.status_code == 200
        assert resp.data == b"MINE"
        assert resp.headers["Content-Length"] == "4"

    def test_download_parent_swapped_after_validation_is_refused(self, tree, docs):
        ws_root, home, victim = tree
        swap = self._swap_for_symlink(docs, victim.parent)
        with self._root_env(ws_root, home), self._after_validation("_resolve_file_in_home", swap):
            resp = self._download(docs / "note.txt")
        assert resp.status_code == 400
        assert b"VICTIM" not in resp.get_data()

    def test_download_file_swapped_for_a_symlink_after_validation_is_refused(self, tree, docs):
        ws_root, home, victim = tree
        swap = self._swap_for_symlink(docs / "note.txt", victim)
        with self._root_env(ws_root, home), self._after_validation("_resolve_file_in_home", swap):
            resp = self._download(docs / "note.txt")
        assert resp.status_code == 400
        assert b"VICTIM" not in resp.get_data()

    def test_download_refuses_a_fifo_without_blocking(self, tree):
        """O_NONBLOCK: opening a FIFO planted under the name must not hang a worker."""
        ws_root, home, _ = tree
        fifo = home / "pipe.txt"
        os.mkfifo(str(fifo))
        result = {}
        # Patches are applied in THIS thread around the worker's whole
        # lifetime (patching inside a worker thread leaks into other tests).
        with self._root_env(ws_root, home):
            worker = threading.Thread(
                target=lambda: result.setdefault("resp", self._download(fifo)), daemon=True
            )
            worker.start()
            worker.join(10)
            if worker.is_alive():
                # Release the blocked open() so the worker can finish, then fail.
                os.close(os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK))
                worker.join(5)
                pytest.fail("download blocked in open() on a FIFO")
        assert result["resp"].status_code == 400

    def test_download_refuses_a_directory_target(self, tree, docs):
        """A directory named as the download target answers 400, not a stream
        of nothing or a 500 from reading a directory fd."""
        ws_root, home, _ = tree
        with self._root_env(ws_root, home):
            resp = self._download(docs)
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Not a file"

    def test_download_refuses_a_home_root_not_owned_by_the_account(self, tree, docs):
        ws_root, home, _ = tree
        with self._root_env(ws_root, home), self._foreign_home_owner():
            resp = self._download(docs / "note.txt")
        assert resp.status_code == 400
        assert b"MINE" not in resp.get_data()

    # --- delete-file --------------------------------------------------------

    def test_delete_removes_a_plain_file(self, tree, docs):
        ws_root, home, _ = tree
        with self._root_env(ws_root, home):
            resp = self._delete(docs / "note.txt")
        assert resp.status_code == 200
        assert not (docs / "note.txt").exists()

    def test_delete_parent_swapped_after_validation_is_refused(self, tree, docs):
        ws_root, home, victim = tree
        swap = self._swap_for_symlink(docs, victim.parent)
        with self._root_env(ws_root, home), self._after_validation("_resolve_file_in_home", swap):
            resp = self._delete(docs / "note.txt")
        assert resp.status_code == 400
        assert (victim.parent / "note.txt").read_text() == "VICTIM-SECRET"

    def test_delete_target_swapped_for_a_directory_is_refused(self, tree, docs):
        ws_root, home, _ = tree
        target = docs / "note.txt"

        def swap():
            target.unlink()
            target.mkdir()

        with self._root_env(ws_root, home), self._after_validation("_resolve_file_in_home", swap):
            resp = self._delete(target)
        assert resp.status_code == 400
        assert target.is_dir()

    def test_delete_refuses_a_home_root_not_owned_by_the_account(self, tree, docs):
        ws_root, home, _ = tree
        with self._root_env(ws_root, home), self._foreign_home_owner():
            resp = self._delete(docs / "note.txt")
        assert resp.status_code == 400
        assert (docs / "note.txt").read_text() == "MINE"

    # --- search -------------------------------------------------------------

    def test_search_root_swapped_after_validation_is_refused(self, tree, docs):
        ws_root, home, victim = tree
        swap = self._swap_for_symlink(docs, victim.parent)
        with (
            self._root_env(ws_root, home),
            self._after_validation("_resolve_user_owned_path", swap),
        ):
            resp = self._search(docs, "needle")
        assert resp.status_code == 400
        assert "needle-victim" not in resp.get_data(as_text=True)

    def test_search_does_not_enter_a_directory_swapped_during_the_walk(self, tree, docs):
        """Even a by-path symlink probe the attacker wins (islink -> False) must not matter."""
        import app.routes.fs as fsm

        ws_root, home, victim = tree
        real_visible = fsm._entry_visible
        swapped = []

        def visible_then_swap(name):
            if name == "docs" and not swapped:
                swapped.append(name)
                self._swap_for_symlink(docs, victim.parent)()
            return real_visible(name)

        with (
            self._root_env(ws_root, home),
            patch.object(fsm, "_entry_visible", side_effect=visible_then_swap),
            patch("app.routes.fs.os.path.islink", return_value=False),
        ):
            resp = self._search(home, "needle")
        assert swapped == ["docs"]
        assert resp.status_code == 200
        assert resp.get_json()["results"] == []

    def test_search_skips_symlinks_instead_of_following_them(self, tree):
        """Type, size and readability of a link all live on its target."""
        ws_root, home, victim = tree
        os.symlink(str(victim), str(home / "linked-secret.txt"))
        (home / "linked-plain.txt").write_text("ok")
        with self._root_env(ws_root, home):
            resp = self._search(home, "linked")
        assert resp.status_code == 200
        assert [r["name"] for r in resp.get_json()["results"]] == ["linked-plain.txt"]

    def test_search_refuses_a_home_root_not_owned_by_the_account(self, tree, docs):
        ws_root, home, _ = tree
        with self._root_env(ws_root, home), self._foreign_home_owner():
            resp = self._search(home, "note")
        assert resp.status_code == 400

    # --- unmapped user whose username is another user's account -------------

    def _unmapped_app(self, username="testuser"):
        from flask import Flask, g

        from app.routes.fs import fs_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(fs_bp, url_prefix="/api")
        app.before_request_funcs["fs"] = []  # app-scope only (see `app` fixture)

        @app.before_request
        def _set_user():
            g.user = {"id": 7, "username": username}  # no system_account

        return app

    @contextlib.contextmanager
    def _users(self, rows):
        import app.routes.fs as fsm

        # create=True: this module's _UR stub has no get_all_users.
        with patch.object(fsm.user_repo, "get_all_users", return_value=rows, create=True):
            yield

    def test_unmapped_user_named_like_another_users_account_gets_no_home(self, tree, docs):
        """#3410 review: ``<base>/<username>`` is then ANOTHER user's home."""
        ws_root, home, _ = tree
        app = self._unmapped_app()
        rows = [
            {"id": 7, "username": "testuser", "system_account": None},
            {"id": 9, "username": "alice", "system_account": "testuser"},
        ]
        with self._root_env(ws_root, home), self._users(rows):
            download = self._download(docs / "note.txt", app)
            delete = self._delete(docs / "note.txt", app)
            search = self._search(home, "note", app)
            upload = self._upload(app.test_client(), docs, "planted.txt")
            browse = app.test_client().get("/api/fs/browse", query_string={"path": str(home)})
        assert [r.status_code for r in (download, delete, search, upload, browse)] == [400] * 5
        assert b"MINE" not in download.get_data()
        assert sorted(p.name for p in docs.iterdir()) == ["note.txt"]

    def test_unmapped_user_without_a_collision_keeps_access(self, tree, docs):
        ws_root, home, _ = tree
        rows = [
            {"id": 7, "username": "testuser", "system_account": None},
            {"id": 9, "username": "alice", "system_account": "alice"},
        ]
        with self._root_env(ws_root, home), self._users(rows):
            resp = self._download(docs / "note.txt", self._unmapped_app())
        assert resp.status_code == 200
        assert resp.data == b"MINE"

    def test_an_account_named_shared_gets_no_home(self, tree):
        """#3410 review: <base>/shared is the shared-project namespace root.

        Usernames also arrive from SSO and org sync, and nothing reserves this
        one, so an unmapped user named ``shared`` would otherwise act as root
        inside every tenant's shared projects.
        """
        ws_root, _, _ = tree
        namespace = ws_root / "shared"
        (namespace / "team-proj").mkdir(parents=True)
        (namespace / "team-proj" / "plan.txt").write_text("TEAM-PLAN")
        app = self._unmapped_app(username="shared")
        with self._root_env(ws_root, namespace), self._users([]):
            download = self._download(namespace / "team-proj" / "plan.txt", app)
            delete = self._delete(namespace / "team-proj" / "plan.txt", app)
            search = self._search(namespace, "plan", app)
            upload = self._upload(app.test_client(), namespace / "team-proj", "planted.txt")
            browse = app.test_client().get("/api/fs/browse", query_string={"path": str(namespace)})
        assert [r.status_code for r in (download, delete, search, upload, browse)] == [400] * 5
        assert b"TEAM-PLAN" not in download.get_data()
        assert sorted(p.name for p in (namespace / "team-proj").iterdir()) == ["plan.txt"]


@pytest.mark.security
@pytest.mark.regression
@pytest.mark.issue(3410)
@pytest.mark.parametrize(
    "user",
    [
        {"id": 3, "username": "shared"},
        {"id": 3, "username": "someone", "system_account": "shared"},
    ],
)
def test_the_shared_namespace_name_never_yields_a_home_root(user):
    from app.routes.fs import _home_roots_for_user

    with patch("app.routes.fs.get_workspace_base_dirs", return_value=["/a", "/b"]):
        assert _home_roots_for_user(user) == []


@pytest.mark.regression
@pytest.mark.issue(3410)
def test_write_lock_accepts_every_base_dirs_home_root():
    """#3410: the /fs per-file paths must not use single-base get_home_directory().

    ``get_workspace_base_dir()`` returns the RAW env value, so on
    ``WORKSPACE_BASE_DIR=/a,/b`` the old helper produced the literal
    ``"/a,/b/<account>"`` — a path no lock can ever match, which 400'd
    upload/download/delete/search on every multi-base deployment. #3376 fixed
    exactly this for browse; these endpoints now share that root set.
    """
    from app.routes.fs import _home_roots_for_write, _primary_home_root

    user = {"id": 1, "username": "alice", "system_account": "alice"}
    with (
        patch("app.routes.fs.get_workspace_base_dirs", return_value=["/a", "/b"]),
        patch("app.routes.fs.get_workspace_base_dir", return_value="/a,/b"),
        # Without this, _home_roots_for_write reaches get_home_directory ->
        # run_as_user("alice", ["test", "-e", ...]) — a real `sudo -u` with
        # timeout=10 and no exception handling, which can PROMPT on a
        # developer machine and raise TimeoutExpired out of the test.
        patch("app.routes.fs.get_home_directory", return_value="/a,/b/alice"),
    ):
        roots = _home_roots_for_write(user)
        # The REPORTING helper must agree with the lock — this is the assertion
        # that actually fails on the pre-#3410 code.
        primary = _primary_home_root(user)
    assert os.path.realpath("/a/alice") in roots
    assert os.path.realpath("/b/alice") in roots
    assert primary == os.path.realpath("/a/alice")


@pytest.mark.regression
@pytest.mark.issue(3410)
def test_api_home_reports_a_root_the_lock_accepts(client, workspace):
    """#3410 smoke check: /api/fs/home must not report a path upload rejects.

    Single-base fixture, so this passes before and after the change — the
    multi-base regression is pinned by
    ``test_write_lock_accepts_every_base_dirs_home_root``.
    """
    _, user_home = workspace
    resp = client.get("/api/fs/home")
    assert resp.status_code == 200
    assert resp.get_json()["homePath"] == str(user_home)


@pytest.mark.security
@pytest.mark.regression
@pytest.mark.issue(3410)
class TestReadPathSymlinkLock:
    """#3410 evidence for the `enforced` claim: download/delete/search too.

    These pin behavior that is already correct — ``_resolve_file_in_home``
    realpaths BEFORE the home check, so a symlink out of the home resolves to
    a path the lock rejects. The upload fix would be half a story without
    them: the contract declares a symlink policy for all eight operations.
    """

    def test_download_through_symlink_to_other_user_is_rejected(self, client, workspace):
        ws_root, user_home = workspace
        other = ws_root / "otheruser"
        other.mkdir(exist_ok=True)
        (other / "secret.txt").write_text("VICTIM")
        os.symlink(str(other / "secret.txt"), str(user_home / "link.txt"))
        resp = client.get("/api/fs/download", query_string={"path": str(user_home / "link.txt")})
        assert resp.status_code == 400

    def test_delete_through_symlink_to_other_user_is_rejected(self, client, workspace):
        ws_root, user_home = workspace
        other = ws_root / "otheruser"
        other.mkdir(exist_ok=True)
        (other / "secret.txt").write_text("VICTIM")
        os.symlink(str(other / "secret.txt"), str(user_home / "link2.txt"))
        resp = client.post("/api/fs/delete-file", json={"path": str(user_home / "link2.txt")})
        assert resp.status_code == 400
        assert (other / "secret.txt").read_text() == "VICTIM"

    def test_search_does_not_descend_into_a_symlinked_directory(self, client, workspace):
        ws_root, user_home = workspace
        other = ws_root / "otheruser"
        other.mkdir(exist_ok=True)
        (other / "needle-target.txt").write_text("VICTIM")
        os.symlink(str(other), str(user_home / "linkdir"))
        resp = client.get(
            "/api/fs/search", query_string={"path": str(user_home), "q": "needle-target"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["results"] == []

    def test_search_still_reports_an_in_home_symlink_for_a_non_root_process(
        self, client, workspace
    ):
        """Skipping links is ROOT-only: a non-root process IS the account."""
        _, user_home = workspace
        (user_home / "real-report.txt").write_text("12345")
        os.symlink(str(user_home / "real-report.txt"), str(user_home / "alias-report.txt"))
        # geteuid pinned so the test holds under a root runner too (under
        # `sudo pytest` the real euid is 0 and links would be skipped).
        with patch("app.routes.fs.os.geteuid", return_value=1000):
            resp = client.get("/api/fs/search", query_string={"path": str(user_home), "q": "alias"})
        assert resp.status_code == 200
        (entry,) = resp.get_json()["results"]
        assert (entry["name"], entry["type"], entry["size"]) == ("alias-report.txt", "file", 5)


@pytest.mark.security
@pytest.mark.issue(3410)
def test_write_as_wrapper_capability_sentinel_is_in_sync():
    """The route's capability marker must match the shipped wrapper (#3410).

    A drift here silently disables the fail-closed gate that keeps the
    ``filesystem_api: enforced`` claim true on package non-root deployments.
    """
    from app.routes.fs import _WRITE_AS_CAPABILITY_SENTINEL

    repo_root = Path(project_root)
    wrapper = repo_root / "scripts" / "openace-write-as.sh"
    text = wrapper.read_text()
    assert _WRITE_AS_CAPABILITY_SENTINEL in text
    # The refusal runs as the target user, on the parent-resolved path.
    assert 'if as_target test -L "$RESOLVED_PATH"' in text
    assert 'readlink -f "$TARGET_PATH"' not in text


@pytest.mark.security
@pytest.mark.issue(3410)
def test_rm_wrapper_capability_sentinel_is_in_sync():
    """The delete-side marker must match the shipped openace-rm (#3410 review)."""
    from app.routes.fs import _RM_CAPABILITY_SENTINEL

    repo_root = Path(project_root)
    text = (repo_root / "scripts" / "openace-rm.sh").read_text()
    assert _RM_CAPABILITY_SENTINEL in text
    assert 'if as_target rm "${RM_OPTIONS[@]}" -- "$TARGET_PATH"' in text
    # No root-run rm is left anywhere in the script.
    assert 'if rm "${RM_OPTIONS[@]}"' not in text


@pytest.mark.security
@pytest.mark.issue(3410)
class TestWriteAsCapabilityProbe:
    """#3410: the probe that decides whether the installed wrapper enforces."""

    def _probe(self, path):
        import app.routes.fs as fsm

        fsm._reset_write_as_capability_cache()
        try:
            with patch.object(fsm, "OPENACE_WRITE_AS_WRAPPER", str(path)):
                return fsm._write_as_wrapper_enforces_symlink_refusal()
        finally:
            fsm._reset_write_as_capability_cache()

    def test_true_when_the_sentinel_is_present(self, tmp_path):
        from app.routes.fs import _WRITE_AS_CAPABILITY_SENTINEL

        f = tmp_path / "openace-write-as"
        f.write_text(f"#!/bin/bash\n# {_WRITE_AS_CAPABILITY_SENTINEL}\n")
        assert self._probe(f) is True

    def test_false_for_a_pre_3410_wrapper(self, tmp_path):
        f = tmp_path / "openace-write-as"
        f.write_text('#!/bin/bash\ntee "$RESOLVED_PATH"\n')
        assert self._probe(f) is False

    def test_false_when_the_wrapper_is_absent(self, tmp_path):
        assert self._probe(tmp_path / "nope") is False

    def test_rm_probe_reads_its_own_marker(self, tmp_path):
        """The two wrappers share a memo; keys must not collide (#3410 review)."""
        import app.routes.fs as fsm

        write_as = tmp_path / "openace-write-as"
        write_as.write_text(f"# {fsm._WRITE_AS_CAPABILITY_SENTINEL}\n")
        rm_old = tmp_path / "openace-rm"
        rm_old.write_text('#!/bin/bash\nrm "$2"\n')
        fsm._reset_write_as_capability_cache()
        try:
            with (
                patch.object(fsm, "OPENACE_WRITE_AS_WRAPPER", str(write_as)),
                patch.object(fsm, "OPENACE_RM_WRAPPER", str(rm_old)),
            ):
                assert fsm._write_as_wrapper_enforces_symlink_refusal() is True
                assert fsm._rm_wrapper_is_account_scoped() is False
                rm_old.write_text(f"# {fsm._RM_CAPABILITY_SENTINEL}\n")
                assert fsm._rm_wrapper_is_account_scoped() is True
        finally:
            fsm._reset_write_as_capability_cache()
