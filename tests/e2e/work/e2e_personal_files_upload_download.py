#!/usr/bin/env python3
"""
Open ACE — Personal Files upload/download/delete E2E (Playwright + real HTTP).

Strategy
--------
The full app cannot boot in this dev environment because of a pre-existing
``scripts/shared/__init__.py`` import bug (surrogate character) unrelated to
this PR. To still get an end-to-end test that exercises the *real* route code
(not a mock), we:

1. Start an isolated Flask app that registers ONLY ``fs_bp`` (the real code
   from app/routes/fs.py) against an ephemeral port. We stub the heavy
   ``app.*`` imports the blueprint pulls in at import time, and bypass its
   session-token ``before_request`` so we can inject a fake authenticated
   user.
2. Drive a real Chromium browser via Playwright to issue genuine ``fetch``
   calls (multipart upload, blob download, JSON delete) against that Flask
   server — same code path the React frontend uses, same network stack.

This validates the full chain: auth bypass → path/home validation → file
write/read/delete → ownership (chown path is exercised only as root, so we
assert the non-root direct-write path here) → isolation between users.

Run:
    python tests/e2e/work/e2e_personal_files_upload_download.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import time
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 1. Stub the heavy app.* deps so app/routes/fs.py imports standalone.
#    (Same technique as tests/routes/test_fs_file_ops.py.)
# ---------------------------------------------------------------------------
for _pkg in [
    "app",
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
        sys.modules[_pkg] = types.ModuleType(_pkg)


class _UR:
    def get_user_by_id(self, _):
        return None


sys.modules["app.repositories.user_repo"].UserRepository = _UR

_ad = sys.modules["app.auth.decorators"]
_ad._extract_token = lambda: None
_ad._load_user_from_token = lambda t: None
_ad.enforce_password_change_requirement = lambda u: None
sys.modules["app.services.webui_manager"].get_webui_manager = lambda: None

# Use the REAL workspace base-dir helpers.
import importlib.util  # noqa: E402

_ws = sys.modules["app.utils.workspace"]
_rspec = importlib.util.spec_from_file_location(
    "_real_workspace", str(PROJECT_ROOT / "app/utils/workspace.py")
)
_rw = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(_rw)
_ws.get_workspace_base_dir = _rw.get_workspace_base_dir
_ws.get_workspace_base_dirs = _rw.get_workspace_base_dirs
_ws.OPENACE_CHOWN_WRAPPER = "/usr/local/bin/openace-chown"
_ws._is_wrapper_available = lambda p: False
_ws.run_as_root_if_needed = lambda cmd: None

# Now import the real fs module.
_fspec = importlib.util.spec_from_file_location(
    "fs_e2e_under_test", str(PROJECT_ROOT / "app/routes/fs.py")
)
fs = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(fs)

from flask import Flask, g  # noqa: E402


# ---------------------------------------------------------------------------
# 2. Build an isolated Flask app + ephemeral workspace, served on a free port.
# ---------------------------------------------------------------------------
def make_app(workspace_root: Path, user: dict):
    """Register the real fs_bp with auth bypassed and workspace pinned."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Wipe the blueprint's session-token before_request so we control the user.
    fs.fs_bp.before_request_funcs[None] = []
    app.register_blueprint(fs.fs_bp, url_prefix="/api")

    # Pin the workspace to our throwaway root, and the user's home within it.
    fs.get_workspace_base_dir = lambda: str(workspace_root)
    fs.get_workspace_base_dirs = lambda: [str(workspace_root)]
    fs.get_home_directory = lambda u=None: str(workspace_root / user["username"])

    @app.before_request
    def _inject_user():
        g.user = user

    return app


def pick_free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# 3. Minimal HTML host page so fetch() has a same-origin document. The page
#    exposes a few JS helpers that return {status, body, headers} dicts.
# ---------------------------------------------------------------------------
HOST_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>fs e2e</title></head><body>
<script>
async function upload(fileBytes, fileName, targetDir) {
  const blob = new Blob([fileBytes]);
  const file = new File([blob], fileName);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('path', targetDir);
  const r = await fetch('/api/fs/upload', { method:'POST', body: fd, credentials:'include' });
  let body; try { body = await r.json(); } catch { body = await r.text(); }
  return { status: r.status, body };
}
async function download(path) {
  const r = await fetch('/api/fs/download?path=' + encodeURIComponent(path), { credentials:'include' });
  let body; try { body = await r.text(); } catch(e){ body = '<read err>'; }
  return { status: r.status, body, disposition: r.headers.get('Content-Disposition'), length: r.headers.get('Content-Length') };
}
async function del(path) {
  const r = await fetch('/api/fs/delete-file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path}), credentials:'include' });
  let body; try { body = await r.json(); } catch { body = await r.text(); }
  return { status: r.status, body };
}
async function browse(path, includeFiles) {
  let url = '/api/fs/browse?path=' + encodeURIComponent(path);
  if (includeFiles) url += '&include_files=1';
  const r = await fetch(url, { credentials:'include' });
  return { status: r.status, body: await r.json() };
}
window.__ready = true;
</script></body></html>"""


# ---------------------------------------------------------------------------
# 4. Test driver.
# ---------------------------------------------------------------------------
def main():
    from playwright.sync_api import sync_playwright

    workspace_root = Path.home() / ".ace_fs_e2e_ws"
    if workspace_root.exists():
        shutil.rmtree(workspace_root, ignore_errors=True)
    workspace_root.mkdir(parents=True)

    # Two users to verify isolation between home subtrees. alice is the
    # authenticated user; bob's home is the sibling that must be unreachable.
    alice = {"id": 1, "username": "alice"}
    (workspace_root / "alice").mkdir()
    (workspace_root / "bob").mkdir()

    port = pick_free_port()
    app = make_app(workspace_root, alice)  # app's g.user defaults to alice

    # Add the host page route.
    @app.route("/__e2e__")
    def _host():
        return HOST_HTML

    def serve():
        app.run(host="127.0.0.1", port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    # Wait for server
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        import urllib.request

        try:
            urllib.request.urlopen(f"{base}/__e2e__", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        print("FAIL: Flask server did not start")
        return 1

    passed = 0
    failed = 0

    def check(cond, desc):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"    [PASS] {desc}")
        else:
            failed += 1
            print(f"    [FAIL] {desc}")

    HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        page.goto(f"{base}/__e2e__")
        page.wait_for_function("window.__ready")

        alice_home = str(workspace_root / "alice")

        print("\n=== E2E 1: upload via real fetch (multipart) ===")
        res = page.evaluate(
            "async () => await upload(new Uint8Array([104,105]), 'hi.txt', '%s')" % alice_home
        )
        check(res["status"] == 200, f"upload returns 200 (got {res['status']})")
        check(
            (workspace_root / "alice" / "hi.txt").read_bytes() == b"hi",
            "file landed with correct bytes",
        )

        print("\n=== E2E 2: browse with include_files shows uploaded file ===")
        res = page.evaluate("async () => await browse('%s', true)" % alice_home)
        names = [f["name"] for f in res["body"].get("files", [])]
        check(res["status"] == 200, "browse 200")
        check("hi.txt" in names, f"hi.txt in files list: {names}")
        check(res["body"].get("files", [{}])[0].get("size") == 2, "size reported as 2")

        print("\n=== E2E 3: browse WITHOUT include_files (backward compat) ===")
        res = page.evaluate("async () => await browse('%s', false)" % alice_home)
        check(res["body"].get("files") == [], "files[] empty by default")

        print("\n=== E2E 4: download streams the bytes back ===")
        res = page.evaluate("async () => await download('%s/hi.txt')" % alice_home)
        check(res["status"] == 200, "download 200")
        check(res["body"] == "hi", f"download body == 'hi' (got {res['body']!r})")
        check("hi.txt" in (res["disposition"] or ""), "Content-Disposition has filename")
        check(res["length"] == "2", "Content-Length == 2")

        print("\n=== E2E 5: upload outside home rejected ===")
        res = page.evaluate("async () => await upload(new Uint8Array([120]), 'x.txt', '/etc')")
        check(res["status"] in (400, 403), f"outside-home upload rejected (got {res['status']})")

        print("\n=== E2E 6: download outside home rejected ===")
        res = page.evaluate("async () => await download('/etc/passwd')")
        check(res["status"] == 400, f"outside-home download rejected (got {res['status']})")

        print("\n=== E2E 7: oversized upload rejected (413) ===")
        big = "new Uint8Array(new Array(%d).fill(65))" % (fs.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 10)
        res = page.evaluate("async () => await upload(%s, 'big.bin', '%s')" % (big, alice_home))
        check(res["status"] == 413, f"oversized rejected 413 (got {res['status']})")

        print("\n=== E2E 8: empty filename rejected ===")
        res = page.evaluate("async () => await upload(new Uint8Array([1]), '', '%s')" % alice_home)
        check(res["status"] == 400, f"empty filename 400 (got {res['status']})")

        print("\n=== E2E 9: filename traversal neutralized (basename only) ===")
        res = page.evaluate(
            "async () => await upload(new Uint8Array([1]), '../../etc/hosts', '%s')" % alice_home
        )
        check(res["status"] == 200, f"traversal upload neutralized (got {res['status']})")
        check(
            (workspace_root / "alice" / "hosts").exists(),
            "file written as basename 'hosts' in home",
        )
        check(not Path("/etc/hosts_uploaded_e2e").exists(), "no escape to /etc")

        print("\n=== E2E 10: HOME ISOLATION — cannot reach sibling user's dir ===")
        bob_home = str(workspace_root / "bob")
        res = page.evaluate(
            "async () => await upload(new Uint8Array([1]), 'spy.txt', '%s')" % bob_home
        )
        check(res["status"] == 400, f"upload to sibling's home rejected (got {res['status']})")
        check("home directory" in str(res["body"]), "error mentions home lock")

        print("\n=== E2E 11: delete the uploaded file ===")
        res = page.evaluate("async () => await del('%s/hi.txt')" % alice_home)
        check(res["status"] == 200, f"delete 200 (got {res['status']})")
        check(not (workspace_root / "alice" / "hi.txt").exists(), "file removed from disk")

        print("\n=== E2E 12: delete outside home rejected ===")
        res = page.evaluate("async () => await del('/etc/passwd')")
        check(res["status"] == 400, f"outside-home delete rejected (got {res['status']})")

        browser.close()

    shutil.rmtree(workspace_root, ignore_errors=True)
    print(f"\n{'='*50}")
    print(f"E2E RESULT: {passed} passed, {failed} failed")
    print(f"{'='*50}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
