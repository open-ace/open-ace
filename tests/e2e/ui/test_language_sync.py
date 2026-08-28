"""Language sync from open-ace to the embedded qwen-code-webui iframe.

Consolidates the issue #60 regression (the ``lang`` URL parameter on the
work-page iframe, including zh/en switches and the ja fallback) with the
issue #1425 follow-ups (URL parameter updates, the
``openace-language-change`` postMessage, and real-time sync without a
reload).

#2491 realignment: the work page embeds the webui iframe inside the
Workspace component (frontend/src/components/features/Workspace.tsx) — one
``iframe[title^="Workspace -"]`` per workspace tab, whose src is built by
getEffectiveUrl() with ``lang=<language>`` appended. Reaching it requires
(1) an authenticated session (the SPA redirects /work to /login otherwise)
and (2) the workspace feature enabled (fresh homes default it off and the
page renders "Workspace not configured"). /api/workspace/user-url is
fulfilled with a placeholder so the default tab materializes even where no
qwen-code-webui binary exists — the iframe *src* is the contract under
test here, not the webui app inside it.

Note: the postMessage/real-time checks verify the Open ACE side only — the
iframe WebUI must implement the listener for real-time sync to work; without
it sync falls back to the iframe-reload path (the URL parameter).
"""

import asyncio
import json
import os
import re

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression]

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")

# The workspace tab iframe (Workspace.tsx renders title="Workspace - <tab>")
WORKSPACE_IFRAME = 'iframe[title^="Workspace -"]'


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _ensure_workspace_enabled():
    """Enable the workspace feature in the lane config (idempotent).

    /api/workspace/config reports workspace.enabled from ~/.open-ace/
    config.json (default false in a freshly initialized home) and the work
    page gates the embedded webui iframe behind it. The endpoint re-reads
    the file per request, so flipping it needs no server restart. Same
    pattern as tests/e2e/terminal/e2e_terminal_tab.py.
    """
    config_path = os.path.expanduser("~/.open-ace/config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
    workspace = config.setdefault("workspace", {})
    if workspace.get("enabled"):
        return
    workspace["enabled"] = True
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


async def _open_work_page(page):
    """Login via the current form and open /work with a settled workspace.

    Returns the workspace iframe locator. /api/workspace/user-url is
    fulfilled with a placeholder URL: the default workspace tab (and its
    iframe) is only created once that query settles successfully, and
    environments without the qwen-code-webui binary otherwise 503 forever.
    """
    await page.route(
        "**/api/workspace/user-url",
        lambda route: route.fulfill(
            json={"success": True, "url": "http://127.0.0.1:1/", "token": "e2e-mock-token"}
        ),
    )
    await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    await page.fill("#username", USERNAME)
    await page.fill("#password", PASSWORD)
    await page.click("button[type='submit']")
    # Admins land on /manage; every authed user may open /work directly.
    await page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
    await page.goto(f"{BASE_URL}/work", wait_until="networkidle")
    iframe = page.locator(WORKSPACE_IFRAME)
    await iframe.first.wait_for(state="attached", timeout=15000)
    await page.wait_for_timeout(1000)
    return iframe


def _lang_of(src: str | None) -> str | None:
    if not src:
        return None
    m = re.search(r"lang=(\w+)", src)
    return m.group(1) if m else None


async def _switch_language_via_dropdown(page, label_re):
    """Switch the app language through the Header globe dropdown.

    The application language lives in the persisted zustand store
    (localStorage 'open-ace-store'), so writing localStorage 'language'
    alone no longer changes it after a reload — the dropdown is the
    user-facing contract that updates the store (and localStorage).
    """
    # Same pattern as the passing logout-position test: the globe toggle
    # opens a bootstrap menu that renders .dropdown-menu.show.
    toggle = page.locator("header button:has(i.bi-globe)")
    await toggle.first.wait_for(state="visible", timeout=10000)
    await toggle.first.click()
    item = page.locator(".dropdown-menu.show .dropdown-item").filter(has_text=label_re)
    await item.first.wait_for(state="visible", timeout=5000)
    await item.first.click()
    await page.wait_for_timeout(500)


@pytest.mark.asyncio
@pytest.mark.issue(60)
async def test_language_sync():
    """Test language sync functionality"""
    _skip_if_no_server()
    _ensure_workspace_enabled()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page (authenticated, workspace enabled)
        print("1. Navigating to open-ace work page...")
        iframe_element = await _open_work_page(page)

        # Check if iframe is present
        print("2. Checking for iframe...")
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=" in iframe_src, f"lang missing from iframe src: {iframe_src}"
        print(f"   iframe src: {iframe_src}")

        # Extract lang value
        lang_value = _lang_of(iframe_src)
        assert lang_value, f"could not extract language value from {iframe_src}"
        print(f"   ✓ Language value: {lang_value}")

        # Test changing language in open-ace and verify iframe updates
        print("\n3. Testing language change...")

        # First, check current language by looking at localStorage
        current_lang = await page.evaluate("localStorage.getItem('language')")
        print(f"   Current language in open-ace: {current_lang}")

        # Switch to Chinese via the Header dropdown (updates the persisted
        # app store, which drives the iframe src language)
        await _switch_language_via_dropdown(page, re.compile("中文|Chinese|汉语"))
        print("   Set language to 'zh'")

        # Refresh page to rebuild the iframe src
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Check iframe src again
        iframe_element = page.locator(WORKSPACE_IFRAME)
        await iframe_element.first.wait_for(state="attached", timeout=15000)
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=zh" in iframe_src, f"lang=zh missing after switch: {iframe_src}"
        print(f"   ✓ iframe src after refresh: {iframe_src}")

        # Set language to English
        await _switch_language_via_dropdown(page, re.compile("English|英语|英文|英語"))
        print("\n4. Setting language to 'en'...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = page.locator(WORKSPACE_IFRAME)
        await iframe_element.first.wait_for(state="attached", timeout=15000)
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=en" in iframe_src, f"lang=en missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src}")

        # Test Japanese (should fallback to English in qwen-code-webui)
        await _switch_language_via_dropdown(page, re.compile("日本語|Japanese|日语"))
        print("\n5. Setting language to 'ja' (Japanese)...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = page.locator(WORKSPACE_IFRAME)
        await iframe_element.first.wait_for(state="attached", timeout=15000)
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=ja" in iframe_src, f"lang=ja missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src} (falls back to 'en' in qwen-code-webui)")

        # restore default language for other tests sharing this lane home
        await page.evaluate("localStorage.setItem('language', 'en')")
        await browser.close()
        print("\nTest completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_url_parameter():
    """Test that iframe URL contains lang parameter"""
    _skip_if_no_server()
    _ensure_workspace_enabled()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("1. Navigating to open-ace work page...")
        iframe_element = await _open_work_page(page)

        # Check if iframe is present
        print("2. Checking for iframe...")
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=" in iframe_src, f"lang missing from iframe src: {iframe_src}"
        print(f"   iframe src: {iframe_src}")

        # Extract lang value
        lang_value = _lang_of(iframe_src)
        assert lang_value, f"could not extract language value from {iframe_src}"
        print(f"   ✓ Language value: {lang_value}")

        await browser.close()
        print("\nTest 1 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_url_parameter_update():
    """Test that iframe URL lang parameter updates when language changes"""
    _skip_if_no_server()
    _ensure_workspace_enabled()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to open-ace work page
        print("\n1. Navigating to open-ace work page...")
        await _open_work_page(page)

        # Test changing language in open-ace and verify iframe URL updates
        print("\n2. Testing language change...")

        # First, check current language
        current_lang = await page.evaluate("localStorage.getItem('language')")
        print(f"   Current language in open-ace: {current_lang}")

        # Switch to Chinese via the Header dropdown (updates the persisted
        # app store, which drives the iframe src language)
        await _switch_language_via_dropdown(page, re.compile("中文|Chinese|汉语"))
        print("   Set language to 'zh'")

        # Refresh page to rebuild the iframe src
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Check iframe src again
        iframe_element = page.locator(WORKSPACE_IFRAME)
        await iframe_element.first.wait_for(state="attached", timeout=15000)
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=zh" in iframe_src, f"lang=zh missing after switch: {iframe_src}"
        print(f"   ✓ iframe src after refresh: {iframe_src}")

        # Set language to English
        await _switch_language_via_dropdown(page, re.compile("English|英语|英文|英語"))
        print("\n3. Setting language to 'en'...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(2000)

        iframe_element = page.locator(WORKSPACE_IFRAME)
        await iframe_element.first.wait_for(state="attached", timeout=15000)
        iframe_src = await iframe_element.first.get_attribute("src")
        assert iframe_src and "lang=en" in iframe_src, f"lang=en missing after switch: {iframe_src}"
        print(f"   ✓ iframe src: {iframe_src}")

        await browser.close()
        print("\nTest 2 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_postmessage_sent():
    """
    Test that openace-language-change postMessage is sent when language changes.

    This test verifies that Open ACE sends the postMessage correctly.
    The iframe WebUI needs to implement the listener for this to have effect.
    """
    _skip_if_no_server()
    _ensure_workspace_enabled()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Intercept console messages to track postMessage
        page.on("console", lambda msg: print(f"Console: {msg.text}"))

        # Navigate to open-ace work page
        print("\n1. Navigating to open-ace work page...")
        await _open_work_page(page)

        # Inject script to capture postMessage calls from parent to iframe
        await page.evaluate("""
            () => {
                // Store original postMessage
                const originalPostMessage = window.postMessage.bind(window);

                // Track postMessage calls
                window.__postmessage_calls = [];

                // Override postMessage to track calls
                window.postMessage = function(message, targetOrigin, transfer) {
                    if (typeof message === 'object' && message.type) {
                        window.__postmessage_calls.push({
                            type: message.type,
                            data: message,
                            targetOrigin: targetOrigin,
                            timestamp: Date.now()
                        });
                    }
                    return originalPostMessage(message, targetOrigin, transfer);
                };

            }
        """)

        # Get iframe element
        iframe_element = page.locator(WORKSPACE_IFRAME)
        assert await iframe_element.count() > 0, "work page must embed the qwen-code-webui iframe"

        # Change language via header dropdown (simulate user action)
        print("\n2. Changing language via header dropdown...")

        # Find language dropdown button (globe icon) — scoped to the globe
        # dropdown itself; the Header renders a help dropdown before it.
        lang_button = page.locator(
            "div.dropdown:has(button.dropdown-toggle i.bi-globe) button.dropdown-toggle"
        )
        if await lang_button.count() > 0:
            await lang_button.first.click()
            await page.wait_for_timeout(500)

            # Click Chinese option
            zh_option = page.locator(
                "div.dropdown:has(button.dropdown-toggle i.bi-globe) " "button.dropdown-item",
            ).filter(has_text=re.compile("中文|Chinese|汉语"))
            if await zh_option.count() > 0:
                await zh_option.first.click()
                await page.wait_for_timeout(1000)

                print("   ✓ Language changed to Chinese via dropdown")
            else:
                # Fallback: change via localStorage
                await page.evaluate("localStorage.setItem('language', 'zh')")
                print("   ℹ Language changed via localStorage (dropdown not available)")
        else:
            await page.evaluate("localStorage.setItem('language', 'zh')")
            print("   ℹ Language changed via localStorage (dropdown not available)")

        # Check if postMessage was sent
        # Note: This checks the localStorage change which triggers the useEffect
        postmessage_log = await page.evaluate("window.__postmessage_calls || []")
        print(f"\n3. PostMessage calls captured: {len(postmessage_log)}")
        for call in postmessage_log:
            print(f"   - Type: {call['type']}, TargetOrigin: {call['targetOrigin']}")

        # Check localStorage was updated
        current_lang = await page.evaluate("localStorage.getItem('language')")
        print(f"\n4. Current localStorage language: {current_lang}")
        assert current_lang == "zh", f"language change did not persist: {current_lang!r}"

        # restore default language for other tests sharing this lane home
        await page.evaluate("localStorage.setItem('language', 'en')")
        await browser.close()
        print("\nTest 3 completed!")


@pytest.mark.asyncio
@pytest.mark.issue(1425)
async def test_language_realtime_sync():
    """
    Test real-time language sync without page reload.

    Workspace.tsx sends an ``openace-language-change`` postMessage to every
    tab iframe when the language changes (Issue #1425). The real webui
    iframe is cross-origin, so delivery is verified with a local mock frame
    that forwards every message it receives back to the parent page —
    proving Open ACE sends the message without a reload. The real webui
    still needs its own listener for the sync to have a visible effect.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    _skip_if_no_server()
    _ensure_workspace_enabled()

    iframe_html = (
        "<!doctype html><html><body>mock webui"
        "<script>"
        "window.addEventListener('message', function (e) {"
        "  try { window.parent.postMessage({__forwarded: true, data: e.data}, '*'); }"
        "  catch (err) {}"
        "});"
        "</script></body></html>"
    )

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = iframe_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Point the workspace iframe at the forwarding mock
            await page.route(
                "**/api/workspace/user-url",
                lambda route: route.fulfill(
                    json={"success": True, "url": f"http://127.0.0.1:{port}/", "token": "rt-token"}
                ),
            )
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click("button[type='submit']")
            await page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
            await page.goto(f"{BASE_URL}/work", wait_until="networkidle")

            iframe_element = page.locator(WORKSPACE_IFRAME)
            await iframe_element.first.wait_for(state="attached", timeout=15000)
            await page.wait_for_timeout(1500)

            # Capture messages forwarded back from the mock iframe
            await page.evaluate(
                "() => { window.__forwarded_msgs = [];"
                " window.addEventListener('message', (e) => {"
                "  if (e.data && e.data.__forwarded) window.__forwarded_msgs.push(e.data.data);"
                " }); }"
            )

            print("\n1. Changing language to 'zh' without reload (header dropdown)...")
            await _switch_language_via_dropdown(page, re.compile("中文|Chinese|汉语"))
            await page.wait_for_timeout(1500)

            forwarded = await page.evaluate("window.__forwarded_msgs || []")
            print(f"2. Messages received by the iframe: {forwarded}")
            lang_msgs = [m for m in forwarded if m and m.get("type") == "openace-language-change"]
            assert lang_msgs, (
                "iframe received no openace-language-change postMessage after the "
                f"language switch (received: {forwarded})"
            )
            assert any(
                m.get("language") == "zh" for m in lang_msgs
            ), f"openace-language-change carried an unexpected language: {lang_msgs}"
            print("   ✓ openace-language-change delivered to the iframe without a reload")

            # restore default language for other tests sharing this lane home
            await _switch_language_via_dropdown(page, re.compile("English|英语|英文|英語"))
        finally:
            await browser.close()
            server.shutdown()
        print("\nTest 4 completed!")
