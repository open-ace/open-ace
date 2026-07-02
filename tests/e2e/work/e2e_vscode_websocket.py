#!/usr/bin/env python3
"""
E2E Test for VS Code WebSocket proxy in Workspace

Test flow:
1. Login as admin
2. Navigate to Work page (loads webui iframe)
3. Select a project in the webui
4. Click VS Code button in file changes panel
5. Verify code-server loads without WebSocket 1006 error
"""

import os
import re
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def test_vscode_websocket(headless=True):
    """Test VS Code editor loads without WebSocket 1006 error"""
    print("\n=== Testing VS Code WebSocket Proxy ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # 1. Login
        print("Step 1: Login...")
        page.goto(f"{BASE_URL}/login")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        try:
            page.wait_for_url("**/manage/**", timeout=10000)
        except Exception:
            page.wait_for_timeout(5000)
        print("  Logged in")

        # 2. Navigate to Work page
        print("Step 2: Navigate to Work page...")
        page.goto(f"{BASE_URL}/work")
        page.wait_for_timeout(5000)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/vscode_e2e_01_work.png")

        # 3. Find the webui iframe
        print("Step 3: Find webui iframe...")
        iframes = page.locator("iframe").all()
        print(f"  Found {len(iframes)} iframes")
        for i, iframe_el in enumerate(iframes):
            src = iframe_el.get_attribute("src") or ""
            print(f"  iframe[{i}]: {src[:100]}")

        webui_iframe = None
        for iframe_el in iframes:
            src = iframe_el.get_attribute("src") or ""
            if "token=" in src and ("localhost" in src or "127.0.0.1" in src):
                webui_iframe = iframe_el
                break

        if not webui_iframe:
            # Try the first iframe
            if iframes:
                webui_iframe = iframes[0]
                print("  Using first iframe as webui")
            else:
                print("  ERROR: No iframes found")
                page.screenshot(path="/tmp/vscode_e2e_error.png")
                browser.close()
                return False

        webui_src = webui_iframe.get_attribute("src") or ""
        print(f"  Webui iframe src: {webui_src[:120]}")
        webui_frame = page.frame_locator(f"iframe[src='{webui_src}']").first

        # Wait for webui to load
        page.wait_for_timeout(5000)

        # 4. Select a project in the webui
        print("Step 4: Select project in webui...")
        try:
            # Click the first project in the list (e.g. "Open ACE")
            # Projects are listed under "Your Projects" with paths like /Users/...
            project_item = (
                webui_frame.locator("li, [class*='project']")
                .filter(has_text=re.compile(r"/Users/|/home/|workspace"))
                .first
            )
            if project_item.is_visible(timeout=5000):
                project_item.click()
                page.wait_for_timeout(2000)
                print("  Clicked a project")
            else:
                # Try pressing Enter to select the focused project
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)
                print("  Pressed Enter to select project")
        except Exception as e:
            print(f"  Could not select project: {e}")

        page.screenshot(path="/tmp/vscode_e2e_02_project.png")

        # 5. Click VS Code button (it's an icon button with title "Open in VS Code")
        print("Step 5: Click VS Code button...")
        try:
            # The VS Code button has title="Open in VS Code" or contains CodeBracketSquareIcon
            vscode_btn = webui_frame.locator(
                "button[title*='VS Code'], button[title*='vscode']"
            ).first
            if not vscode_btn.is_visible(timeout=3000):
                # Fallback: find the code bracket icon button in the file changes header
                vscode_btn = webui_frame.locator("svg path[d*='M6.75']").first.locator(
                    "xpath=ancestor::button"
                )
                if not vscode_btn.is_visible(timeout=3000):
                    # Last resort: list all buttons for debugging
                    all_btns = webui_frame.locator("button").all()
                    print(f"  Found {len(all_btns)} buttons total")
                    for b in all_btns:
                        title = b.get_attribute("title") or ""
                        text = b.text_content() or ""
                        if title or text.strip():
                            print(f"    button: title='{title}' text='{text[:30]}'")

            if vscode_btn.is_visible(timeout=3000):
                vscode_btn.click()
                print("  Clicked VS Code button")
            else:
                print("  VS Code button not visible")
                page.screenshot(path="/tmp/vscode_e2e_error.png")
                browser.close()
                return False
        except Exception as e:
            print(f"  Could not click VS Code button: {e}")
            page.screenshot(path="/tmp/vscode_e2e_error.png")
            browser.close()
            return False

        # 6. Wait for code-server to load
        print("Step 6: Wait for code-server to load...")
        page.wait_for_timeout(30000)
        page.screenshot(path="/tmp/vscode_e2e_03_vscode.png")

        # Debug: dump all iframes at all levels
        print("Step 6b: Debug iframe structure...")
        all_page_iframes = page.locator("iframe").all()
        print(f"  Top-level iframes: {len(all_page_iframes)}")
        for i, f in enumerate(all_page_iframes):
            src = f.get_attribute("src") or ""
            print(f"  [{i}] {src[:120]}")

        # 7. Check for WebSocket 1006 error
        print("Step 7: Check for WebSocket error...")
        has_error = False

        # Check inside the webui iframe for nested VS Code iframe
        inner_iframes = webui_iframe.locator("iframe").all()
        print(f"  Found {len(inner_iframes)} inner iframes in webui")

        for i, inner_iframe_el in enumerate(inner_iframes):
            inner_src = inner_iframe_el.get_attribute("src") or ""
            print(f"  inner iframe[{i}]: {inner_src[:100]}")

            if "/vscode" in inner_src:
                print("  Found VS Code iframe!")
                vs_frame = webui_frame.frame_locator("iframe[src*='/vscode']").first

                # Check for error dialog
                try:
                    error_text = vs_frame.locator("text=WebSocket close")
                    if error_text.is_visible(timeout=5000):
                        has_error = True
                        error_content = error_text.first.text_content()
                        print(f"  ERROR FOUND: {error_content}")
                    else:
                        print("  No error dialog visible")
                except Exception:
                    print("  No error elements found (likely loaded successfully)")

                # Check for success indicators
                try:
                    editor = vs_frame.locator(".monaco-editor, .part.editor")
                    if editor.is_visible(timeout=3000):
                        print("  SUCCESS: Monaco editor is visible")
                except Exception:
                    pass

        # Also check the webui frame for error state
        try:
            ws_error = webui_frame.get_by_text(
                re.compile(r"WebSocket|1006|failed to connect", re.I)
            )
            if ws_error.is_visible(timeout=2000):
                has_error = True
                print("  ERROR: WebSocket error found in webui frame")
        except Exception:
            pass

        page.screenshot(path="/tmp/vscode_e2e_04_final.png")
        browser.close()

        if has_error:
            print("\n=== Test FAILED: WebSocket 1006 error detected ===")
            return False
        else:
            print("\n=== Test PASSED: No WebSocket errors ===")
            return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test VS Code WebSocket proxy")
    parser.add_argument("--no-headless", action="store_true", help="Run with visible browser")
    args = parser.parse_args()

    result = test_vscode_websocket(headless=not args.no_headless)
    exit(0 if result else 1)
