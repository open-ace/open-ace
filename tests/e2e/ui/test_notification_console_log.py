"""
Test that captures browser console logs to verify tab notification message handling.

#2491 R3a realignment: the baselined failure asserted
``button.workspace-new-tab-btn`` count > 0 on /work/workspace. The new-tab
button still exists (``frontend/src/components/features/Workspace.tsx``
lines 2692-2703) but it only renders inside the tab bar, which requires at
least one live workspace tab (``tabs.length > 0``, Workspace.tsx line 2571).
In the CI/extended-test lane the workspace is deliberately NOT configured
(``/api/workspace/config`` returns ``enabled: false, url: ""``), so /work
renders the "Workspace not configured" gate instead of any tab bar/iframe.
The realigned contract asserts that gating: without a configured workspace
there is no new-tab entry point, no iframe, and no console errors from the
workspace page. When a workspace IS configured, the new-tab button must be
present (checked in the configured branch).
"""

import json
import os
import sys

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(71)]


project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
OUTPUT_DIR = "./screenshots/issues/71"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def test_console_logs():
    """Console-log capture for the workspace tab-notification surface."""

    _skip_if_no_server()
    print("=" * 60)
    print("Console Log Test for Tab Notification")
    print("=" * 60)

    console_logs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Capture console logs from main page
        def handle_console(msg):
            log_type = msg.type
            text = msg.text
            if log_type == "log":
                console_logs.append(f"[PAGE LOG] {text}")
            elif log_type == "error":
                console_logs.append(f"[PAGE ERROR] {text}")
            elif log_type == "warning":
                console_logs.append(f"[PAGE WARN] {text}")
            else:
                console_logs.append(f"[PAGE {log_type}] {text}")

        page.on("console", handle_console)

        try:
            # Login
            print("\n[1] 登录...")
            page.goto(f"{BASE_URL}login")
            page.wait_for_selector("#username", timeout=15000)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=15000)
            print("    ✓ 登录成功")

            # Navigate to workspace
            print("\n[2] 导航到 Workspace...")
            page.goto(f"{BASE_URL}work/workspace")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(4000)

            # Read the workspace configuration contract that gates the tab UI
            config = page.evaluate("""async () => {
                    const r = await fetch('/api/workspace/config', { credentials: 'include' });
                    return r.ok ? await r.json() : null;
                }""")
            print(f"    workspace config: {json.dumps(config)[:200]}")
            assert config is not None, "workspace config endpoint should be reachable"
            workspace_configured = bool(config.get("enabled") and config.get("url"))

            new_tab_btn = page.locator("button.workspace-new-tab-btn")
            workspace_tabs = page.locator(".workspace-tab")
            iframes = page.locator("iframe")

            if not workspace_configured:
                # Current lane contract: unconfigured workspace renders the
                # gate instead of the tab bar.
                body_text = page.locator("body").inner_text()
                assert (
                    "Workspace not configured" in body_text or "工作区未配置" in body_text
                ), "unconfigured workspace should show the 'Workspace not configured' gate"
                assert (
                    new_tab_btn.count() == 0
                ), "new-tab button must not render without a configured workspace"
                assert workspace_tabs.count() == 0, "no workspace tabs without configuration"
                assert iframes.count() == 0, "no workspace iframe without configuration"
                print("    ✓ 未配置状态：无 tab bar / new-tab 按钮 / iframe（符合当前契约）")
            else:
                # Configured environment: the new-tab entry point must exist.
                assert (
                    new_tab_btn.count() > 0
                ), "workspace new-tab button should render when workspace is configured"
                print("    ✓ 已配置状态：new-tab 按钮存在")

            page.screenshot(path=f"{OUTPUT_DIR}/console_test_final.png")

            # Console health: the workspace page must not emit page errors
            # while rendering the (gated) tab-notification surface.
            errors = [log for log in console_logs if log.startswith("[PAGE ERROR]")]
            print(f"    console errors: {len(errors)}")
            for log in errors[:5]:
                print(f"      {log}")
            assert not errors, f"workspace page emitted console errors: {errors[:5]}"

            # Print console logs related to notification
            print("\n" + "=" * 60)
            print("Console Logs (Notification Related)")
            print("=" * 60)

            notification_logs = [
                log
                for log in console_logs
                if "notification" in log.lower()
                or "workspace" in log.lower()
                or "waiting" in log.lower()
            ]

            if notification_logs:
                for log in notification_logs:
                    print(log)
            else:
                print("未找到通知相关日志")
                print("\n所有日志:")
                for log in console_logs[-20:]:
                    print(log)

            print("\n" + "=" * 60)

        except PlaywrightError as e:
            print(f"\n    ✗ 测试错误: {e}")
            import traceback

            traceback.print_exc()
            raise
        finally:
            browser.close()
