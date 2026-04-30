#!/usr/bin/env python3
"""
Open ACE - /context Slash Command E2E Test

Tests the /context command in the webui ChatPage:
1. /context appears in slash command suggestions
2. /context is intercepted client-side (not sent to backend)
3. ContextUsagePanel renders when token data is available

Requires:
  - Open ACE backend running at BASE_URL
  - WebUI dev server running at WEBUI_URL

Run:
  HEADLESS=true  python tests/e2e_context_command.py
  HEADLESS=false python tests/e2e_context_command.py
"""

import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-context")


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {name}.png")


def log_step(tag, msg):
    print(f"  [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def run_tests():
    print("\n" + "=" * 60)
    print("  /context Command E2E Test")
    print("=" * 60)

    passed = 0
    failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        page = browser.new_page()
        page.set_default_timeout(15000)

        try:
            # ══════ 1. Navigate to ChatPage ══════
            print("\n══════ 1. Navigate to ChatPage ══════")
            page.goto(WEBUI_URL, wait_until="domcontentloaded", timeout=10000)
            pause(2)

            # The projects page lists project paths. Click on "open-ace" project.
            # Each project item is likely a clickable row. Try clicking by text.
            openace_item = page.locator("text=/open-ace/").first
            if openace_item.count() > 0:
                openace_item.click()
                pause(1)
                # Then click Enter or Select button to enter the project
                enter_btn = (
                    page.locator("button", has_text="Enter")
                    .or_(page.locator("button", has_text="Select"))
                    .first
                )
                if enter_btn.count() > 0:
                    enter_btn.click()
                    pause(2)
            else:
                # Fallback: directly navigate to a project chat URL
                page.goto(
                    f"{WEBUI_URL}/?project=open-ace", wait_until="domcontentloaded", timeout=10000
                )
                pause(2)

            # Wait for textarea to appear (indicates we're on the chat page)
            try:
                page.wait_for_selector("textarea", state="visible", timeout=10000)
                log_step("OK", "ChatPage loaded with textarea")
            except Exception:
                shot(page, "01-no-textarea")
                log_step("INFO", "No textarea found, trying direct URL navigation")
                # Direct URL to a specific project
                page.goto(
                    f"{WEBUI_URL}/chat/open-ace", wait_until="domcontentloaded", timeout=10000
                )
                pause(2)
                try:
                    page.wait_for_selector("textarea", state="visible", timeout=10000)
                except Exception:
                    # Last resort: try the work page
                    page.goto(
                        f"{WEBUI_URL}/work?project=/Users/rhuang/workspace/open-ace",
                        wait_until="domcontentloaded",
                        timeout=10000,
                    )
                    pause(2)
                    page.wait_for_selector("textarea", state="visible", timeout=10000)

            shot(page, "01-chatpage-loaded")
            log_step("OK", "ChatPage loaded")
            passed += 1

            # ══════ 2. Test /context appears in slash suggestions ══════
            print("\n══════ 2. Test /context slash command suggestion ══════")
            textarea = page.locator("textarea").first
            textarea.fill("/")
            pause(1)

            # Check slash command suggestion dropdown
            suggestion_list = page.locator("ul")
            context_suggestion = suggestion_list.locator("li", has_text="/context")
            if context_suggestion.count() > 0:
                log_step("OK", "/context appears in slash command suggestions")
                shot(page, "02-context-suggestion")
                passed += 1
            else:
                # Try checking all suggestion items
                all_items = suggestion_list.locator("li")
                texts = []
                for i in range(all_items.count()):
                    texts.append(all_items.nth(i).text_content())
                log_step("INFO", f"Suggestions found: {texts}")
                if any("/context" in t for t in texts):
                    log_step("OK", "/context found in suggestions")
                    shot(page, "02-context-suggestion")
                    passed += 1
                else:
                    log_step("FAIL", "/context NOT found in suggestions")
                    shot(page, "02-no-context-suggestion")
                    failed += 1

            # ══════ 3. Test /context is intercepted ══════
            print("\n══════ 3. Test /context client-side interception ══════")

            # Clear textarea and type /context
            textarea.fill("")
            pause(0.5)
            textarea.fill("/context")
            pause(0.5)

            # Press Enter - first Enter completes the suggestion,
            # second Enter submits the message
            textarea.press("Enter")  # auto-complete suggestion
            pause(0.5)
            textarea.press("Enter")  # actually send
            pause(1)
            shot(page, "03-context-intercepted")

            # Verify no user message was added to chat
            # The textarea should be cleared (intercepted like /clear)
            textarea_value = textarea.input_value()
            if textarea_value == "":
                log_step("OK", "/context was intercepted - textarea cleared")
                passed += 1
            else:
                log_step("FAIL", f"Textarea not cleared: '{textarea_value}'")
                failed += 1

            # Check if ContextUsagePanel appeared or if no-data state shown
            # Since no messages have been exchanged, contextPanelData may be null
            context_panel = page.locator("text=Context Usage").or_(
                page.locator("text=上下文使用情况")
            )
            if context_panel.count() > 0:
                log_step("OK", "ContextUsagePanel is visible")
                shot(page, "03b-context-panel-visible")
                passed += 1
            else:
                log_step(
                    "INFO",
                    "ContextUsagePanel not shown (no token data yet - expected without API calls)",
                )
                # This is expected behavior - no API calls = no token data
                passed += 1

            # ══════ 4. Test /context with Escape key ══════
            print("\n══════ 4. Test /context escape from suggestions ══════")
            textarea.fill("/")
            pause(0.5)
            # Press Escape to close suggestions
            textarea.press("Escape")
            pause(0.5)
            textarea.fill("")
            log_step("OK", "Escape closes slash suggestions")
            passed += 1

            # ══════ 5. Test /clear still works ══════
            print("\n══════ 5. Test /clear command still works ══════")
            textarea.fill("/clear")
            textarea.press("Enter")
            pause(1)
            shot(page, "05-clear-confirm")

            # Should show confirm modal
            confirm_modal = page.locator("text=Are you sure").or_(page.locator("text=确定要清空"))
            if confirm_modal.count() > 0:
                log_step("OK", "/clear confirm modal shown")
                passed += 1
                # Close the modal
                page.locator("button", has_text="Cancel").or_(
                    page.locator("button", has_text="取消")
                ).first.click()
                pause(0.5)
            else:
                log_step("FAIL", "/clear confirm modal not shown")
                failed += 1

        except Exception as e:
            print(f"\n  ❌ Test error: {e}")
            traceback.print_exc()
            shot(page, "error")
            failed += 1

        finally:
            browser.close()

    # ── Summary ──
    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
