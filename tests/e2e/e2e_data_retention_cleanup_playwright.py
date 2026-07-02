#!/usr/bin/env python3
"""
Open ACE - Data Retention Cleanup E2E Playwright Test (issue #860)

Verifies the fix for "cleanup history never shows + results unclear":
1. Login as admin
2. Go to Compliance Management -> Data Retention tab
3. Open the cleanup preview and assert it renders STRUCTURED content
   (reused CleanupPreviewContent) instead of a raw JSON dump
4. Execute the cleanup from the preview modal and assert a success TOAST
   appears (previously there was zero feedback)
5. Assert a row appears in the cleanup history table (the core #860 symptom:
   history was never persisted on PostgreSQL)

Run:
  HEADLESS=true  python tests/e2e/e2e_data_retention_cleanup_playwright.py   # auto test
  HEADLESS=false python tests/e2e/e2e_data_retention_cleanup_playwright.py   # demo
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import json
import subprocess

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-retention-cleanup")

passed = 0
failed = 0
errors = []


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def pause(seconds):
    time.sleep(seconds) if not HEADLESS else time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def get_admin_session_token():
    """Authenticate via the API and return the session_token cookie value.

    The React login form is rendered client-side and its selectors are flaky;
    cookie injection (recommended in the project's E2E gotchas) is reliable.
    Uses curl because Python's urllib framing triggers spurious 502s from the
    gevent dev server (curl is reliable against the same endpoint).
    """
    for attempt in range(6):
        try:
            out = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-i",
                    "-X",
                    "POST",
                    f"{BASE_URL}/api/auth/login",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps({"username": "admin", "password": "admin123"}),
                    "--max-time",
                    "8",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            for line in out.splitlines():
                low = line.lower()
                if low.startswith("set-cookie:") and "session_token=" in low:
                    for part in low.split("set-cookie:", 1)[1].split(";"):
                        part = part.strip()
                        if part.startswith("session_token="):
                            return part[len("session_token=") :]
            if attempt == 5:
                print("    [WARN] no session_token in login response")
        except Exception as exc:
            if attempt == 5:
                print(f"    [WARN] login curl failed: {exc}")
        time.sleep(0.5)
    return None


def login(context, page):
    print("\n[TEST] Authenticate as admin (cookie injection)...")
    token = get_admin_session_token()
    check(token is not None, "Obtained admin session_token via API")
    context.add_cookies([{"name": "session_token", "value": token or "", "url": BASE_URL}])
    page.goto(f"{BASE_URL}/manage/compliance")
    pause(2)
    # If authenticated, the admin management page loads instead of /login.
    check("/login" not in page.url, "Authenticated; not redirected to login")
    shot(page, "01-login")


def open_retention_tab(page):
    print("\n[TEST] Open Data Retention tab...")
    page.goto(f"{BASE_URL}/manage/compliance")
    pause(2)

    # Switch to the Data Retention tab (zh / en fallback)
    tab = page.locator("button, .nav-link").filter(has_text="数据保留")
    if not tab.first.is_visible():
        tab = page.locator("button, .nav-link").filter(has_text="Data Retention")
    if tab.first.is_visible():
        tab.first.click()
        pause(2)
        check(True, "Data Retention tab opened")
    else:
        check(False, "Data Retention tab not visible")
    shot(page, "02-retention-tab")


def click_preview_button(page):
    """Click the 'Preview' button to open the cleanup preview modal."""
    btn = page.locator("button").filter(has_text="预览")
    if not btn.first.is_visible():
        btn = page.locator("button").filter(has_text="Preview")
    if btn.first.is_visible():
        btn.first.click()
        pause(2)
        return True
    check(False, "Preview button not visible")
    return False


def test_preview_renders_structured_content(page):
    """The preview modal must render structured content, not a raw JSON dump."""
    print("\n[TEST] Preview renders structured content (not raw JSON)...")
    if not click_preview_button(page):
        return

    # runCleanup(dry_run) counts every rule and can take ~10-15s on a large DB,
    # so wait for the modal to actually appear instead of checking too early.
    try:
        page.wait_for_selector(".modal, [class*='modal-dialog']", timeout=30000, state="visible")
    except Exception:
        check(False, "Preview modal did not open (within 30s)")
        shot(page, "03-preview-modal-timeout")
        return

    modal = page.locator(".modal, [class*='modal-dialog']").first

    # CleanupPreviewContent renders stat cards + an "Execution Details" table.
    # Assert the modal contains any known structured marker (zh / en).
    modal_text = modal.text_content() or ""
    structured_markers = [
        "执行详情",
        "删除记录数",
        "归档记录数",
        "匿名化",
        "影响记录",
        "Execution Details",
        "Records Deleted",
        "Records Archived",
    ]
    has_structured = any(marker in modal_text for marker in structured_markers)

    # The old behavior dumped raw JSON inside a <pre>; assert it is gone.
    has_raw_json = modal.locator("pre").count() > 0

    check(has_structured, "Preview shows structured 'Execution Details' content")
    check(not has_raw_json, "Preview no longer shows a raw JSON <pre> dump")
    shot(page, "03-preview-structured")


def test_execute_shows_toast_and_history(page):
    """Execute cleanup -> success toast + a row in cleanup history.

    Destructive: this runs the REAL cleanup (deletes/anonymizes expired records
    per the default rules and persists the report). Off by default to protect
    the dev database; set RUN_EXECUTE=true to opt in (history persistence is
    already covered by tests/integration/test_retention_pg.py).
    """
    print("\n[TEST] Execute cleanup shows toast and history row...")
    if os.environ.get("RUN_EXECUTE", "false").lower() != "true":
        print("    [SKIP] set RUN_EXECUTE=true to run the (destructive) execute step")
        check(True, "Execute step skipped (non-destructive by default; covered by PG test)")
        return

    modal = page.locator(".modal, [class*='modal-dialog']").first
    if not modal.is_visible():
        check(False, "Preview modal not open; cannot execute")
        return

    # Auto-accept the window.confirm() in handleExecuteCleanup.
    page.on("dialog", lambda dialog: dialog.accept())

    # Click the modal footer's "Execute Cleanup" button (zh / en fallback).
    execute_btn = modal.locator("button").filter(has_text="执行清理")
    if not execute_btn.first.is_visible():
        execute_btn = modal.locator("button").filter(has_text="Execute Cleanup")
    if not execute_btn.first.is_visible():
        execute_btn = modal.locator("button").filter(has_text="정리 실행")

    if execute_btn.first.is_visible():
        execute_btn.first.click()
    else:
        check(False, "Execute Cleanup button not visible in modal")
        return

    # Execute runs the real cleanup (deletes/anonymizes per rules + persists the
    # report); on a large DB this takes ~15-25s. Wait for the success toast.
    toast = page.locator(".toast-container, [class*='toast-container']").filter(
        has_text="清理执行成功"
    )
    try:
        toast.first.wait_for(timeout=45000)
        toast_visible = True
    except Exception:
        toast_en = page.locator(".toast-container, [class*='toast-container']").filter(
            has_text="Cleanup executed successfully"
        )
        try:
            toast_en.first.wait_for(timeout=5000)
            toast_visible = True
        except Exception:
            toast_visible = False
    check(toast_visible, "Success toast shown after cleanup")
    shot(page, "04-toast-after-execute")

    # Cleanup history table should now contain at least one row (the core #860
    # fix: history is persisted on PostgreSQL).
    pause(2)
    history_section = page.locator("text=清理历史")
    if not history_section.first.is_visible():
        history_section = page.locator("text=Cleanup History")
    if history_section.first.is_visible():
        # Find the history table near the section and count body rows.
        history_table = page.locator("table").filter(has_text="删除记录数").first
        if not history_table.is_visible():
            history_table = page.locator("table").filter(has_text="Records Deleted").first
        if history_table.is_visible():
            row_count = history_table.locator("tbody tr").count()
            check(row_count >= 1, f"Cleanup history has {row_count} row(s)")
        else:
            check(False, "Cleanup history table not visible")
    else:
        check(False, "Cleanup history section not visible")
    shot(page, "05-cleanup-history")


def run_tests():
    global passed, failed
    print("\n" + "=" * 60)
    print("Data Retention Cleanup E2E Test (#860)")
    print("=" * 60)
    print(f"BASE_URL: {BASE_URL}")
    print(f"HEADLESS: {HEADLESS}")
    print("=" * 60)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(viewport={"width": 1280, "height": 1024}, locale="zh-CN")
            page = context.new_page()
            try:
                login(context, page)
                open_retention_tab(page)
                test_preview_renders_structured_content(page)
                test_execute_shows_toast_and_history(page)

                print("\n" + "=" * 60)
                print(f"Passed: {passed}")
                print(f"Failed: {failed}")
                if errors:
                    print("\nErrors:")
                    for error in errors:
                        print(f"  - {error}")
                print("=" * 60)
                return 1 if failed > 0 else 0
            finally:
                browser.close()
    except Exception as e:
        print(f"\n[ERROR] Test execution failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
