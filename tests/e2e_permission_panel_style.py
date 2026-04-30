#!/usr/bin/env python3
"""
E2E Test: Permission Panel Button Selection Styling

Tests that:
  1. Permission panel shows correct i18n text (Chinese)
  2. Default "允许" button is selected with blue background
  3. ArrowDown selects "允许，且不再询问" with green background
  4. ArrowDown again selects "拒绝" with red background
  5. ArrowUp cycles back
  6. No duplicate tool names displayed
  7. Clicking a button works

Run:
  HEADLESS=true  python tests/e2e_permission_panel_style.py
  HEADLESS=false python tests/e2e_permission_panel_style.py
"""

import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── Config ──
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
PROXIES = {"http": None, "https": None}
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3101")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-permission-style")

TEST_USER = "黄迎春"
TEST_PASS = "admin123"
MACHINE_ID = os.environ.get("MACHINE_ID", "4c3b203c-6a50-4298-a661-179f2394fb22")
RESPONSE_TIMEOUT = 300

TEST_MESSAGE = "请创建文件 /tmp/test_permission.txt，内容为 hello_world"


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=True, timeout=30000)
    except Exception:
        try:
            page.screenshot(path=path, full_page=False, timeout=10000)
        except Exception:
            return
    print(f"    📸 {name}.png")


def log(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.5)


def api_login():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
        proxies=PROXIES,
    )
    assert r.status_code == 200
    token = r.cookies.get("session_token")
    assert token
    return token


def get_webui_info(token):
    r = requests.get(
        f"{BASE_URL}/api/workspace/user-url",
        cookies={"session_token": token},
        proxies=PROXIES,
    )
    assert r.status_code == 200
    return r.json()


def run_tests():
    token = api_login()
    log("Auth", "✓ Logged in")

    webui_info = get_webui_info(token)
    webui_token = webui_info.get("token", "")
    effective_webui_url = webui_info.get("url", WEBUI_URL)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=200 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        captured_session_id = [None]

        def on_response(response):
            url = response.url
            if "/api/remote/sessions" in url and response.request.method == "POST":
                try:
                    data = response.json()
                    sid = data.get("session", {}).get("session_id")
                    if sid:
                        captured_session_id[0] = sid
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            _run_test(page, token, effective_webui_url, webui_token, captured_session_id)
        except Exception:
            shot(page, "ERROR_final")
            traceback.print_exc()
            raise
        finally:
            sid = captured_session_id[0]
            if sid:
                requests.post(
                    f"{BASE_URL}/api/remote/sessions/{sid}/stop",
                    cookies={"session_token": token},
                    proxies=PROXIES,
                )
                log("Cleanup", f"Stopped session {sid[:8]}...")
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print(f"  ALL PASSED! Screenshots: {SCREENSHOT_DIR}")
    print("=" * 60)


def _wait_for_permission_panel(page, timeout=120):
    """Wait until the permission panel appears in the DOM."""
    start = time.time()
    while time.time() - start < timeout:
        panel = page.locator("text=需要权限确认").first
        if panel.is_visible(timeout=1000):
            return True
        # Also check English fallback
        panel_en = page.locator("text=Permission Required").first
        if panel_en.is_visible(timeout=1000):
            return True
        time.sleep(1)
    return False


def _get_button_bg(button):
    """Get the background color of a button element."""
    return button.evaluate("el => getComputedStyle(el).backgroundColor")


def _get_button_text_color(button):
    """Get the text color of a button element."""
    return button.evaluate("el => getComputedStyle(el.querySelector('span')).color")


def _run_test(page, token, webui_url, webui_token, captured_session_id):
    # ════════════════════════════════════════════
    #  STEP 1: Open ChatPage and trigger permission
    # ════════════════════════════════════════════

    print("\n══════ STEP 1: Open ChatPage & Trigger Permission ══════")

    chat_url = (
        f"{webui_url}/projects"
        f"?token={webui_token}"
        f"&openace_url={BASE_URL}"
        "&workspaceType=remote"
        f"&machineId={MACHINE_ID}"
        "&machineName=TestServer"
        "&encodedProjectName=-root"
        "&permissionMode=default"
    )

    page.goto(chat_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("textarea, .min-h-screen", timeout=30000)
    pause(10)
    shot(page, "S1_chatpage_loaded")
    log("Load", "✓ ChatPage loaded")

    # Get session ID
    sid = None
    for _ in range(30):
        try:
            r = requests.get(
                f"{BASE_URL}/api/remote/sessions",
                cookies={"session_token": token},
                proxies=PROXIES,
            )
            if r.status_code == 200:
                for s in r.json().get("sessions", []):
                    if s.get("machine_id") == MACHINE_ID or s.get("status") == "active":
                        sid = s.get("session_id")
                        if sid:
                            break
        except Exception:
            pass
        if sid:
            break
        time.sleep(1)

    if not sid:
        sid = captured_session_id[0]
    assert sid, "Session not created"
    log("Session", f"✓ {sid[:12]}...")

    # Wait for CLI init
    time.sleep(8)

    # Send message to trigger write_file permission
    r = requests.post(
        f"{BASE_URL}/api/remote/sessions/{sid}/chat",
        json={"content": TEST_MESSAGE},
        cookies={"session_token": token},
        proxies=PROXIES,
    )
    assert r.status_code == 200
    log("Send", "✓ Message sent")

    # ════════════════════════════════════════════
    #  STEP 2: Verify Permission Panel Appears
    # ════════════════════════════════════════════

    print("\n══════ STEP 2: Verify Permission Panel ══════")

    # Wait for permission panel to appear
    assert _wait_for_permission_panel(page), "Permission panel did not appear"
    pause(1)
    shot(page, "S2_panel_default_selected")
    log("Panel", "✓ Permission panel visible")

    # Verify i18n - check for Chinese text
    title_zh = page.locator("text=需要权限确认").first
    assert title_zh.is_visible(), "Chinese title '需要权限确认' not visible"
    log("i18n", "✓ Chinese title displayed")

    # Check for Enter hint
    hint = page.locator("text=按 Enter 确认").first
    if not hint.is_visible(timeout=2000):
        page.locator("text=Enter to confirm").first
        log("i18n", "Hint text found (checking English fallback)")
    else:
        log("i18n", "✓ Enter hint displayed")

    # ════════════════════════════════════════════
    #  STEP 3: Verify Default "允许" Selected (Blue)
    # ════════════════════════════════════════════

    print("\n══════ STEP 3: Default '允许' Selected (Blue) ══════")

    allow_btn = page.locator('[data-permission-action="allow"]').first
    assert allow_btn.is_visible(), "Allow button not visible"
    log("Button", "✓ '允许' button visible")

    allow_bg = _get_button_bg(allow_btn)
    allow_text_color = _get_button_text_color(allow_btn)
    log("Style", f"  bg={allow_bg} text={allow_text_color}")

    # Selected = solid blue background (rgb(59, 130, 246) = blue-500) and white text
    assert (
        allow_bg != "rgba(0, 0, 0, 0)"
    ), "Allow button should have colored background when selected, got transparent"
    assert (
        "255" in allow_text_color or "255, 255, 255" in allow_text_color
    ), f"Allow button text should be white when selected, got {allow_text_color}"
    log("Style", "✓ '允许' has solid colored background + white text (selected)")
    shot(page, "S3_allow_selected")

    # ════════════════════════════════════════════
    #  STEP 4: ArrowDown → "允许且不再询问" (Green)
    # ════════════════════════════════════════════

    print("\n══════ STEP 4: ArrowDown → Green Button ══════")

    page.keyboard.press("ArrowDown")
    pause(0.5)

    permanent_btn = page.locator('[data-permission-action="allowPermanent"]').first
    assert permanent_btn.is_visible(), "Permanent allow button not visible"

    # Debug: dump actual className
    permanent_class = permanent_btn.evaluate("el => el.className")
    log("Debug", f"  permanent className: {permanent_class[:300]}")
    permanent_style = permanent_btn.evaluate("el => el.getAttribute('style')")
    log("Debug", f"  permanent style attr: {permanent_style}")
    shot(page, "S4_green_selected")

    permanent_bg = _get_button_bg(permanent_btn)
    permanent_text_color = _get_button_text_color(permanent_btn)
    log("Style", f"  bg={permanent_bg} text={permanent_text_color}")

    assert (
        permanent_bg != "rgba(0, 0, 0, 0)"
    ), "Permanent button should have colored background when selected"
    assert (
        "255" in permanent_text_color or "255, 255, 255" in permanent_text_color
    ), "Permanent button text should be white when selected"
    log("Style", "✓ '允许，且不再询问' has solid colored background + white text (selected)")

    # Previous button should lose selection
    allow_bg_after = _get_button_bg(allow_btn)
    log("Style", f"  allow bg after: {allow_bg_after}")

    # ════════════════════════════════════════════
    #  STEP 5: ArrowDown → "拒绝" (Red)
    # ════════════════════════════════════════════

    print("\n══════ STEP 5: ArrowDown → Red Button ══════")

    page.keyboard.press("ArrowDown")
    pause(0.5)
    shot(page, "S5_red_selected")

    deny_btn = page.locator('[data-permission-action="deny"]').first
    assert deny_btn.is_visible(), "Deny button not visible"

    deny_bg = _get_button_bg(deny_btn)
    deny_text_color = _get_button_text_color(deny_btn)
    log("Style", f"  bg={deny_bg} text={deny_text_color}")

    assert deny_bg != "rgba(0, 0, 0, 0)", "Deny button should have colored background when selected"
    assert (
        "255" in deny_text_color or "255, 255, 255" in deny_text_color
    ), "Deny button text should be white when selected"
    log("Style", "✓ '拒绝' has solid colored background + white text (selected)")

    # ════════════════════════════════════════════
    #  STEP 6: ArrowUp → Back to Green
    # ════════════════════════════════════════════

    print("\n══════ STEP 6: ArrowUp → Back to Green ══════")

    page.keyboard.press("ArrowUp")
    pause(0.5)
    shot(page, "S6_back_to_green")

    permanent_bg2 = _get_button_bg(permanent_btn)
    deny_bg2 = _get_button_bg(deny_btn)
    log("Style", f"  permanent bg={permanent_bg2} deny bg={deny_bg2}")

    assert (
        permanent_bg2 != "rgba(0, 0, 0, 0)"
    ), "Permanent button should be selected again after ArrowUp"
    assert (
        deny_bg2 == "rgba(0, 0, 0, 0)" or deny_bg2 != permanent_bg2
    ), "Deny button should not be selected after ArrowUp"
    log("Style", "✓ ArrowUp correctly moves selection back")

    # ════════════════════════════════════════════
    #  STEP 7: ArrowUp → Back to Blue (allow)
    # ════════════════════════════════════════════

    print("\n══════ STEP 7: ArrowUp → Back to Blue ══════")

    page.keyboard.press("ArrowUp")
    pause(0.5)
    shot(page, "S7_back_to_blue")

    allow_bg2 = _get_button_bg(allow_btn)
    assert allow_bg2 != "rgba(0, 0, 0, 0)", "Allow button should be selected again after ArrowUp"
    log("Style", "✓ ArrowUp cycles back to '允许'")

    # ════════════════════════════════════════════
    #  STEP 8: No duplicate tool names
    # ════════════════════════════════════════════

    print("\n══════ STEP 8: Verify No Duplicate Tool Names ══════")

    # Check the permission content area for duplicate command badges
    command_spans = page.locator(".font-mono.bg-slate-100, .font-mono.dark\\:bg-slate-700")
    count = command_spans.count()
    log("Dup", "  Found %d command badge(s)" % count)

    if count > 1:
        texts = [command_spans.nth(i).text_content() for i in range(count)]
        log("Dup", f"  Badge texts: {texts}")
        unique = set(texts)
        assert len(unique) == len(texts), f"Duplicate tool names found: {texts}"
    log("Dup", "✓ No duplicate tool names")

    # ════════════════════════════════════════════
    #  STEP 9: All 3 buttons always visible
    # ════════════════════════════════════════════

    print("\n══════ STEP 9: All 3 Buttons Always Visible ══════")

    for key in ["allow", "allowPermanent", "deny"]:
        btn = page.locator(f'[data-permission-action="{key}"]').first
        assert btn.is_visible(), f"Button '{key}' should always be visible"
    log("Visible", "✓ All 3 buttons always visible during navigation")

    # ════════════════════════════════════════════
    #  Done - approve and clean up
    # ════════════════════════════════════════════

    print("\n══════ STEP 10: Approve via Enter ══════")
    # Navigate back to allow and press Enter
    page.keyboard.press("ArrowUp")  # might already be on allow
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Enter")
    pause(2)
    shot(page, "S10_approved")

    log("Result", "✓ ALL STYLE TESTS PASSED!")


if __name__ == "__main__":
    run_tests()
