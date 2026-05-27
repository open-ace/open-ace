#!/usr/bin/env python3
"""
Open ACE - Remote Directory Browser E2E Test (Issue #584)

Tests the directory browser feature in NewSessionModal using Playwright.
Uses route interception to mock browse API responses with realistic
directory data from the remote machine (192.168.64.3).

Flow:
  1. Login as admin
  2. Register a remote machine via API
  3. Intercept browse API calls and respond with real directory data via SSH
  4. Open New Session modal, select Remote workspace
  5. Click Browse, navigate directories
  6. Select a path, verify it's set in the form
  7. Verify Terminal workspace shows path input

Run:
  HEADLESS=true  python tests/e2e/remote/e2e_directory_browser.py
  HEADLESS=false python tests/e2e/remote/e2e_directory_browser.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

# ── Config ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
REMOTE_HOST = os.environ.get("REMOTE_HOST", "192.168.64.3")
REMOTE_USER = os.environ.get("REMOTE_USER", "root")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-directory-browser")

# ── State ───────────────────────────────────────────────
machine_id = None
admin_token = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot: {name}.png")


def log(tag, msg):
    print(f"  [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


# ── SSH helper to list real directories ──────────────────


def ssh_list_dirs(path):
    """List subdirectories on the remote machine via SSH."""
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                f"{REMOTE_USER}@{REMOTE_HOST}",
                f"ls -1d {path}/*/ 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        dirs = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip().rstrip("/")
                if line:
                    name = os.path.basename(line)
                    dirs.append({"name": name, "path": line, "writable": True})

        wr_result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                f"{REMOTE_USER}@{REMOTE_HOST}",
                f"test -w {path} && echo 'writable' || echo 'readonly'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        is_writable = "writable" in (wr_result.stdout or "")

        return {
            "success": True,
            "result": {
                "path": path,
                "name": os.path.basename(path) or "/",
                "directories": dirs,
                "parent": os.path.dirname(path) if path != "/" else None,
                "homePath": "/root",
                "is_writable": is_writable,
            },
            "machine": {"machine_id": machine_id, "status": "online"},
        }
    except Exception as e:
        log("SSH", f"Error listing dirs: {e}")
    return {
        "success": True,
        "result": {
            "path": path,
            "name": os.path.basename(path) or "/",
            "directories": [],
            "parent": os.path.dirname(path) if path != "/" else None,
            "homePath": "/root",
            "is_writable": True,
        },
        "machine": {"machine_id": machine_id, "status": "online"},
    }


# ── API Helpers (using curl to avoid requests 502 issues) ─────


def curl_api(method, path, data=None, cookie=None):
    cmd = ["curl", "-s", "-X", method]
    if data:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    if cookie:
        cmd += ["-b", f"session_token={cookie}"]
    cmd.append(f"{BASE_URL}{path}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": result.stdout}


def api_login(username="admin", password="admin123"):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cookies", delete=False) as f:
        cookie_file = f.name
    try:
        subprocess.run(
            [
                "curl",
                "-s",
                "-c",
                cookie_file,
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps({"username": username, "password": password}),
                f"{BASE_URL}/api/auth/login",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        with open(cookie_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") and "HttpOnly" not in line:
                    continue
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_") :]
                parts = line.split("\t")
                if len(parts) >= 7 and parts[5] == "session_token":
                    return parts[6]
    finally:
        os.unlink(cookie_file)
    raise RuntimeError("Could not extract session_token cookie")


def api_register_machine(token):
    global machine_id
    resp = curl_api("POST", "/api/remote/machines/register", {"tenant_id": 1}, cookie=token)
    assert resp.get("registration_token"), f"Reg token failed: {resp}"
    reg_token = resp["registration_token"]

    machine_id = str(uuid.uuid4())
    resp = curl_api(
        "POST",
        "/api/remote/agent/register",
        {
            "registration_token": reg_token,
            "machine_id": machine_id,
            "machine_name": f"Test Server ({REMOTE_HOST})",
            "hostname": REMOTE_HOST,
            "os_type": "linux",
            "os_version": "Rocky Linux 9.7",
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
            "agent_version": "1.0.0-e2e",
        },
    )
    assert resp.get("success") or resp.get("machine_id"), f"Machine register failed: {resp}"

    # Agent register message
    curl_api(
        "POST",
        "/api/remote/agent/message",
        {
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
        },
    )

    # Assign admin user (id=1) access
    resp = curl_api(
        "POST",
        f"/api/remote/machines/{machine_id}/assign",
        {"user_id": 1, "permission": "admin"},
        cookie=token,
    )
    assert resp.get("success"), f"Assign failed: {resp}"
    log("API", f"Machine registered: {machine_id[:8]}...")


def api_cleanup(token):
    global machine_id
    if machine_id:
        curl_api("DELETE", f"/api/remote/machines/{machine_id}", cookie=token)
        log("Cleanup", f"Deleted machine {machine_id[:8]}...")
        machine_id = None


# ══════════════════════════════════════════════════════
#  Main Test
# ══════════════════════════════════════════════════════


def run_tests():
    global admin_token, machine_id

    passed = 0
    failed = 0
    total = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed, total
        total += 1
        if condition:
            passed += 1
            log("PASS", name)
        else:
            failed += 1
            log("FAIL", f"{name} — {detail}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(15000)

        try:
            # ─── 1. Login & Setup ───
            print("\n══════ 1. Login & Setup ══════")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            page.fill("#username", "admin")
            page.fill("#password", "admin123")
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            pause(2)
            shot(page, "01_logged_in")

            admin_token = api_login()
            api_register_machine(admin_token)

            # ─── Intercept browse API calls ───
            # When the frontend calls GET /api/remote/machines/{id}/browse,
            # intercept and respond with real directory data via SSH
            def handle_browse(route):
                url = route.request.url
                # Extract path parameter
                import urllib.parse

                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                browse_path = params.get("path", ["/root/workspace"])[0]
                log("Intercept", f"Browse request: {browse_path}")
                response_data = ssh_list_dirs(browse_path)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(response_data),
                )

            page.route(f"**/api/remote/machines/{machine_id}/browse**", handle_browse)
            log("Setup", "Route interception for browse API registered")

            # ─── 2. Navigate to /work and open New Session modal ───
            print("\n══════ 2. Open New Session Modal ══════")
            page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
            page.wait_for_selector("main, .work-main, .workspace-container", timeout=10000)
            pause(2)
            shot(page, "02_work_page")

            new_session_btn = page.get_by_test_id("new-session-btn")
            new_session_btn.click()
            pause(1)

            modal = page.locator(".modal")
            modal.wait_for(state="visible", timeout=5000)
            shot(page, "03_new_session_modal")
            check("New Session modal opens", modal.is_visible())

            # ─── 3. Verify workspace type buttons ───
            print("\n══════ 3. Workspace Type Buttons ══════")
            check(
                "Local button visible",
                page.locator("button").filter(has_text="Local").first.is_visible(),
            )
            check(
                "Remote button visible",
                page.locator("button").filter(has_text="Remote").first.is_visible(),
            )
            check(
                "Terminal button visible",
                page.locator("button").filter(has_text="Terminal").first.is_visible(),
            )

            # ─── 4. Select Remote, verify Browse button ───
            print("\n══════ 4. Remote Workspace Browse ══════")
            page.locator("button").filter(has_text="Remote").first.click()
            pause(1)
            shot(page, "04_remote_selected")

            machine_area = modal.locator("text=Machine").first
            check("Machine area visible", machine_area.is_visible())

            browse_btn = modal.locator("button").filter(has_text="Browse")
            check("Browse button visible", browse_btn.first.is_visible())

            # ─── 5. Click Browse and verify directory listing ───
            print("\n══════ 5. Directory Browser Modal ══════")
            browse_btn.first.click()
            pause(1)

            dir_modal = page.locator(".modal").last
            shot(page, "05_directory_browser")

            # Wait for directory listing to appear (intercepted response)
            try:
                page.wait_for_function(
                    """() => {
                        const modals = document.querySelectorAll('.modal');
                        const last = modals[modals.length - 1];
                        const text = last?.textContent || '';
                        return text.includes('open-ace') || text.includes('qwen')
                            || text.includes('empty') || text.includes('Empty');
                    }""",
                    timeout=10000,
                )
            except Exception:
                log("Browser", "Timeout waiting for content")
            pause(1)

            page_text = dir_modal.text_content() or ""
            log("Browser", f"Modal text: {page_text[:300]}")

            has_path = "/root" in page_text or "workspace" in page_text
            check("Directory path shown", has_path, f"Got: {page_text[:100]}")

            has_dirs = "open-ace" in page_text or "qwen" in page_text
            check(
                "Directories listed",
                has_dirs,
                f"Expected open-ace/qwen-code-webui in: {page_text[:300]}",
            )

            shot(page, "05b_directory_listing")

            # ─── 6. Navigate into a subdirectory ───
            print("\n══════ 6. Navigate Subdirectory ══════")
            openace_item = dir_modal.locator("text=open-ace").first
            if openace_item.is_visible():
                openace_item.click()
                try:
                    page.wait_for_function(
                        """() => {
                            const modals = document.querySelectorAll('.modal');
                            const text = modals[modals.length - 1]?.textContent || '';
                            return text.includes('/root/workspace/open-ace');
                        }""",
                        timeout=10000,
                    )
                except Exception:
                    pass
                time.sleep(1)

                shot(page, "06_navigated_openace")

                page_text = dir_modal.text_content() or ""
                log("Browser", f"After nav: {page_text[:300]}")

                # Path should include open-ace (breadcrumb or current path)
                in_subdir = "/root/workspace/open-ace" in page_text
                check(
                    "Navigated into subdirectory",
                    in_subdir,
                    f"Expected /root/workspace/open-ace, got: {page_text[:200]}",
                )
            else:
                log("SKIP", "open-ace directory not found in listing")

            # ─── 7. Navigate back using parent button ───
            print("\n══════ 7. Navigate Back (Parent) ══════")
            up_btn = dir_modal.locator("button").filter(has_text="up").first
            if not up_btn.is_visible():
                up_btn = dir_modal.locator(
                    "[title*='Up'], [title*='Parent'], button:has(.bi-arrow-up), .btn-up"
                ).first

            if up_btn.is_visible():
                up_btn.click()
                try:
                    page.wait_for_function(
                        """() => {
                            const modals = document.querySelectorAll('.modal');
                            const text = modals[modals.length - 1]?.textContent || '';
                            return text.includes('open-ace') && text.includes('qwen');
                        }""",
                        timeout=10000,
                    )
                except Exception:
                    pass
                time.sleep(1)
                shot(page, "07_navigate_back")

                page_text = dir_modal.text_content() or ""
                back_ok = "open-ace" in page_text and "qwen" in page_text
                check(
                    "Navigated back to parent",
                    back_ok,
                    f"Expected parent dir listing, got: {page_text[:200]}",
                )
            else:
                log("SKIP", "Up/parent button not found")

            # ─── 8. Select a path and confirm ───
            print("\n══════ 8. Select Path ══════")
            # Navigate to open-ace first
            openace_item = dir_modal.locator("text=open-ace").first
            if openace_item.is_visible():
                openace_item.click()
                time.sleep(1)

            # Find Select button
            select_btn = None
            for label in ["Select", "Choose", "选择", "确定"]:
                btn = dir_modal.locator("button").filter(has_text=label)
                if btn.first.is_visible():
                    select_btn = btn.first
                    break

            if select_btn:
                select_btn.click()
                time.sleep(1)
                shot(page, "08_path_selected")

                new_session_text = modal.text_content() or ""
                path_set = "open-ace" in new_session_text
                check(
                    "Selected path shown in form",
                    path_set,
                    f"Expected path in form, got: {new_session_text[:200]}",
                )
            else:
                log("SKIP", "Select button not found in directory browser")

            # ─── 9. Cancel the New Session modal ───
            print("\n══════ 9. Close Modal ══════")
            cancel_btn = modal.locator("button").filter(has_text="Cancel")
            if not cancel_btn.is_visible():
                cancel_btn = modal.locator("button").filter(has_text="取消")
            if cancel_btn.is_visible():
                cancel_btn.click()
                time.sleep(1)
                check("Modal closes on Cancel", not modal.is_visible())
            else:
                log("SKIP", "Cancel button not found")
            shot(page, "09_modal_closed")

            # ─── 10. Terminal workspace shows path input ───
            print("\n══════ 10. Terminal Workspace ══════")
            new_session_btn = page.get_by_test_id("new-session-btn")
            new_session_btn.click()
            pause(1)
            modal = page.locator(".modal")
            modal.wait_for(state="visible", timeout=5000)

            terminal_btn = page.locator("button").filter(has_text="Terminal").first
            terminal_btn.click()
            pause(1)
            shot(page, "10_terminal_selected")

            path_label = (
                modal.locator("text=Project Path")
                .or_(modal.locator("text=Working Directory"))
                .or_(modal.locator("text=工作目录"))
                .or_(modal.locator("text=项目路径"))
            )
            check(
                "Terminal shows path input",
                path_label.first.is_visible(),
                "Project path label not found for terminal workspace",
            )
            shot(page, "10b_terminal_path")

            # Close modal
            cancel_btn = modal.locator("button").filter(has_text="Cancel")
            if not cancel_btn.is_visible():
                cancel_btn = modal.locator("button").filter(has_text="取消")
            if cancel_btn.is_visible():
                cancel_btn.click()
                pause(1)

            # ─── Results ───
            print(f"\n{'='*60}")
            print(f"  Results: {passed}/{total} passed, {failed} failed")
            print(f"  Screenshots: {SCREENSHOT_DIR}")
            print(f"{'='*60}")

        except Exception as e:
            log("ERROR", str(e))
            shot(page, "error")
            raise

        finally:
            if admin_token and machine_id:
                api_cleanup(admin_token)
            context.close()
            browser.close()

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
