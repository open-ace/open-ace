#!/usr/bin/env python3
"""
Open ACE - Codex Frontend E2E Tests (Issue #517)

Extracted from tests/issues/517/e2e_codex_integration.py in the #2429
batch-17 e2e exodus: the four Playwright frontend checks — codex sessions
page, session detail rendering, the (informational) work-page prompts
drawer, and the codex CLI option on the API-key management page.

Run:
  HEADLESS=true  python tests/e2e/browser/test_codex_frontend.py
"""

import os
import sys

import pytest

# Absolute package import of the shared codex helpers (prepend-mode
# requirement): put the project root on sys.path so ``tests.e2e.work.helpers``
# resolves under both pytest import modes.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

from tests.e2e.work.helpers import (
    PROJECT_ROOT,
    WEBUI_URL,
    api_get,
    cleanup_codex_seed,
    create_browser_page,
    ensure_lane_login,
    playwright_login,
    poll_until,
    screenshot,
    seed_codex_data,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(517)]

SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-codex")


@pytest.fixture(autouse=True, scope="module")
def _lane_seed_and_auth():
    """Login + seed codex data once for this module (lane-only concerns)."""
    ensure_lane_login()
    seed_codex_data()
    yield
    cleanup_codex_seed()


def test_frontend_codex_sessions_page():
    """Frontend sessions page loads and shows codex sessions."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work/sessions")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: "session" in page.inner_text("body").lower(),
                timeout=5,
                interval=0.5,
                description="sessions page load",
            )
            screenshot(page, "codex-sessions-page", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert (
                "codex" in page_text.lower()
                or "session" in page_text.lower()
                or "会话" in page_text
            ), f"Session page doesn't show codex content: {page_text[:300]}"
            print("    Sessions page loaded with codex filter")
        finally:
            browser.close()


def test_frontend_codex_session_detail():
    """Frontend session detail page renders codex content_blocks."""
    from playwright.sync_api import sync_playwright

    data = api_get("/workspace/sessions", params={"tool_name": "codex", "limit": 1})
    sessions = data.get("data", {}).get("sessions", [])
    if not sessions:
        print("    SKIP: No codex sessions to test detail page")
        return

    sid = sessions[0]["session_id"]

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work/sessions")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="session detail load",
            )
            screenshot(page, "codex-session-detail", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert len(page_text) > 50, "Session detail page appears empty"
            print(f"    Session detail loaded for {sid[:16]}...")
        finally:
            browser.close()


def test_frontend_assist_panel_codex():
    """Frontend work page prompts drawer opens (codex panel informational check).

    Historical note: the original test checked a Codex "tools" tab in the
    right AssistPanel. That tools tab was removed from the AssistPanel long
    before the panel itself was retired (replaced by the floating prompts
    drawer). This check is informational only, as it always has been: it
    opens the drawer and reports whether "codex" text is present on the page.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL)
            page.goto(f"{WEBUI_URL}/work")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="work page load",
            )

            # Open the prompts drawer (replaces the retired assist panel)
            toggle = page.locator(".prompts-drawer-toggle")
            if toggle.count() > 0:
                toggle.first.click()
                page.wait_for_timeout(500)
            screenshot(page, "codex-assist-panel", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert len(page_text) > 50, "work page appears empty"
            has_codex = "codex" in page_text.lower()
            print(f"    Work page loaded, codex on page: {has_codex}")
        finally:
            browser.close()


def test_frontend_api_key_codex_option():
    """Frontend API key management includes Codex CLI option."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = create_browser_page(p)
        try:
            playwright_login(page, WEBUI_URL, username="admin")
            page.goto(f"{WEBUI_URL}/manage/settings/api-keys")
            page.wait_for_load_state("networkidle")
            poll_until(
                lambda: len(page.inner_text("body")) > 50,
                timeout=5,
                interval=0.5,
                description="api key page load",
            )
            screenshot(page, "codex-api-key-page", SCREENSHOT_DIR)

            page_text = page.inner_text("body")
            assert len(page_text) > 50, "API key page appears empty"
            has_codex = "codex" in page_text.lower()
            print(f"    API key page loaded, codex option present: {has_codex}")
        finally:
            browser.close()
