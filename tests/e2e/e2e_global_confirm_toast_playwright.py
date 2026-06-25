#!/usr/bin/env python3
"""
Open ACE - Global Confirm Modal + Global Toast E2E Playwright Test

Regression coverage for the periodic-review refactor that:
1. Replaced scattered window.confirm() with a single global ConfirmHost backed
   by a Zustand store (useConfirm()) — the confirm is now a styled Bootstrap
   <Modal>, NOT a native browser dialog.
2. Replaced per-component Toast state with a global ToastHost mounted once at
   the app root — every toast.* call is visible globally regardless of caller.

This test asserts both behaviors end to end on the SMTP settings page:
- Saving an empty form fires a global toast that renders in the app-root
  toast portal (the old per-component container never mounted on this page, so
  a visible toast here proves the global ToastHost path).
- Clicking Delete opens a ConfirmModal (a real .modal-dialog in the DOM) and
  does NOT trigger a native window.confirm dialog. Cancelling closes it.

Run:
  HEADLESS=true  python tests/e2e/e2e_global_confirm_toast_playwright.py   # auto
  HEADLESS=false python tests/e2e/e2e_global_confirm_toast_playwright.py   # demo

  # Point at a Vite dev server (this PR's frontend tree) proxying the API to :5001:
  BASE_URL=http://localhost:5173 HEADLESS=true python tests/e2e/e2e_global_confirm_toast_playwright.py
"""

import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import json  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-global-confirm-toast")

passed = 0
failed = 0
errors = []
native_dialog_fired = {"value": False}


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

    Cookie injection (per the project's E2E gotchas) is far more reliable than
    driving the React login form. Uses curl because urllib framing triggers
    spurious 502s from the gevent dev server.
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
    page.goto(f"{BASE_URL}/manage/settings/smtp")
    pause(2)
    check("/login" not in page.url, "Authenticated; not redirected to login")
    shot(page, "01-login")


def test_global_toast_renders_at_root(page):
    """Saving an empty form fires a toast in the global toast portal.

    The SMTP page never rendered its own ToastContainer, so a visible toast
    here proves the app-root <ToastHost/> (the refactor) is wiring calls
    globally. We clear the host field first so the validation branch fires
    deterministically without writing to the DB.
    """
    print("\n[TEST] Global toast renders via app-root ToastHost...")
    page.goto(f"{BASE_URL}/manage/settings/smtp")
    pause(2)

    # Ensure the host field is empty so handleSave hits its validation branch.
    host_input = page.locator("input[placeholder='smtp.example.com']").first
    if host_input.is_visible():
        host_input.fill("")
    else:
        # Fallback: first text input in the config form.
        page.locator(".smtp-config input[type='text']").first.fill("")

    # Find the Save button (zh / en fallback) and click it.
    save_btn = page.locator("button").filter(has_text="保存")
    if not save_btn.first.is_visible():
        save_btn = page.locator("button").filter(has_text="Save")
    check(save_btn.first.is_visible(), "Save button visible")
    save_btn.first.click()

    # A toast must appear in the global toast container (portal mounted at root).
    toast = page.locator(".toast-container .toast")
    try:
        toast.first.wait_for(timeout=10000, state="visible")
        toast_visible = True
    except Exception:
        toast_visible = False
    check(toast_visible, "Global toast appears in app-root toast container after save")
    shot(page, "02-global-toast")


def test_delete_uses_confirm_modal_not_native_dialog(page):
    """Delete opens a styled ConfirmModal, never a native window.confirm.

    If no SMTP config exists the Delete button is hidden; we skip the
    ConfirmModal assertion gracefully (like the retention suite's RUN_EXECUTE
    gate). When present, we register a native-dialog handler that FAILS the
    test if any window.confirm/alert fires — proving the migration removed the
    native dialog — then assert a Bootstrap .modal-dialog appears and Cancel
    dismisses it without deleting.
    """
    print("\n[TEST] Delete opens ConfirmModal (not native dialog)...")
    page.goto(f"{BASE_URL}/manage/settings/smtp")
    pause(2)

    del_btn = page.locator("button").filter(has_text="删除")
    if not del_btn.first.is_visible():
        del_btn = page.locator("button").filter(has_text="Delete")

    if not del_btn.first.is_visible():
        check(True, "Delete button absent (no SMTP config); ConfirmModal check skipped")
        print("    [SKIP] no Delete button present — set an SMTP config to exercise this")
        return

    # A global dialog sentinel is registered in run_tests(): if the migration
    # regressed and Delete triggered a native window.confirm, that handler sets
    # native_dialog_fired and dismisses it. Here we just open the confirm.
    del_btn.first.click()

    modal = page.locator(".modal-dialog, .modal-content")
    try:
        modal.first.wait_for(timeout=8000, state="visible")
        modal_visible = True
    except Exception:
        modal_visible = False
    check(modal_visible, "ConfirmModal (.modal-dialog) opened by Delete")
    check(not native_dialog_fired["value"], "No native window.confirm dialog fired")

    if modal_visible:
        # ConfirmModal footer has Cancel (btn-secondary) + Confirm (btn-danger).
        cancel_btn = modal.first.locator("button.btn-secondary")
        check(cancel_btn.first.is_visible(), "ConfirmModal shows a Cancel button")
        cancel_btn.first.click()
        pause(1)
        closed = not modal.first.is_visible()
        check(closed, "Cancel closes the ConfirmModal (no deletion performed)")
    shot(page, "03-confirm-modal")


def run_tests():
    global passed, failed
    print("\n" + "=" * 60)
    print("Global Confirm Modal + Global Toast E2E Test")
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
                # Native dialog sentinel: if ANY window.confirm/alert fires during
                # the whole run, the refactor regressed.
                page.on(
                    "dialog",
                    lambda dialog: native_dialog_fired.__setitem__("value", True)
                    or dialog.dismiss(),
                )

                login(context, page)
                test_global_toast_renders_at_root(page)
                test_delete_uses_confirm_modal_not_native_dialog(page)

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
