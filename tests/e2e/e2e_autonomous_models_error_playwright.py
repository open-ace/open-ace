#!/usr/bin/env python3
"""
Open ACE - Autonomous Models Load-Error E2E Test (Issue #2667)

A server-side failure on GET /api/autonomous/models (e.g. missing
OPENACE_ENCRYPTION_KEY making APIKeyProxyService construction raise) used to
be masked by the backend as {"success": true, "models": []}, which the
"new autonomous workflow" modal rendered as the misleading
"该工具尚未配置模型，请先在设置中添加 API Key" hint.

Backend + frontend are fixed so that a 500 now surfaces as an inline load
error. This E2E locks the frontend half by intercepting the models request:

1. Login via the real login form.
2. Intercept /api/autonomous/models and force a 500 with the server's
   generic error payload.
3. Open the new-task modal, select Claude Code.
4. Assert the model column shows the load error (data-testid=
   models-load-error) and NOT the "no models configured" hint.
5. Flip the interception to a legitimate 200 + empty_reason response and
   assert the Settings hint comes back (unchanged behavior).

Run (server must be running and reachable at BASE_URL):
  HEADLESS=true  python tests/e2e/e2e_autonomous_models_error_playwright.py   # CI
  HEADLESS=false python tests/e2e/e2e_autonomous_models_error_playwright.py   # Demo
"""

import json
import os
import sys
import time

# Add project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

# Disable proxy for localhost
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

# ── Config ──────────────────────────────────────────────

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-models-error")
TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = os.environ.get("TEST_REAL_PASS", "admin123")

# Rendered i18n text of the misleading hint, in every supported locale — the
# E2E must never see ANY of these when the server errored.
NO_MODELS_HINT_TEXTS = [
    "该工具尚未配置模型",
    "No models configured for this tool",
    "このツールにモデルが設定されていません",
    "이 도구에 구성된 모델이 없습니다",
]

# The user-facing error string the fixed backend returns on 500.
SERVER_ERROR_TEXT = "Failed to load models for this tool"

MODELS_ROUTE_GLOB = "**/api/autonomous/models*"


# ── Helpers ─────────────────────────────────────────────


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {name}.png", flush=True)


def log(tag, msg):
    print(f"  [{tag}] {msg}", flush=True)


def pause(seconds):
    time.sleep(seconds if not HEADLESS else 0.3)


# ── Steps ───────────────────────────────────────────────


def login(page):
    log("LOGIN", f"Logging in as {TEST_USER}")
    page.goto(f"{BASE_URL}/login")
    page.wait_for_timeout(500)
    page.fill("input#username", TEST_USER)
    page.fill("input#password", TEST_PASS)
    page.click("form.login-form button[type='submit']")
    page.wait_for_timeout(1500)
    log("LOGIN", "✅ Logged in")


def open_new_task_modal(page):
    log("MODAL", "Opening the new autonomous workflow modal")
    page.goto(f"{BASE_URL}/work/autonomous")
    page.wait_for_timeout(2500 if HEADLESS else 5000)
    shot(page, "00-autonomous-page")

    # The page has several "+" buttons (sessions panel first); the autonomous
    # panel's button is labeled "New Task" (English default; fall back to the
    # LAST plus-icon button which belongs to the autonomous panel header).
    # Headed runs render slower than headless — allow generous timeouts.
    new_task = page.get_by_role("button", name="New Task")
    try:
        new_task.wait_for(state="visible", timeout=20000)
        new_task.click()
    except Exception:
        page.locator("button:has(i.bi-plus-lg)").last.click(timeout=20000)
    page.wait_for_timeout(800)

    # The modal must actually be the new-task form: it contains the agent
    # tool <select>. Verify instead of trusting the click.
    page.locator(".modal select.form-select").first.wait_for(state="visible", timeout=5000)
    shot(page, "01-modal-open")
    log("MODAL", "✅ Modal opened")


def select_claude_code(page):
    """Claude Code is the first option; pick it explicitly so the models
    query fires for a concrete tool."""
    page.locator(".modal select.form-select").first.select_option(label="Claude Code")
    page.wait_for_timeout(500)


def assert_no_misleading_hint(page):
    for text in NO_MODELS_HINT_TEXTS:
        count = page.get_by_text(text, exact=False).count()
        assert count == 0, (
            f"Misleading 'no models configured' hint ({text!r}) rendered "
            "for a server-side 500 — Issue #2667 regression"
        )


def step_server_error_shows_load_error(page):
    """500 from /api/autonomous/models → inline load error, not the hint."""
    log("ERROR", "Intercepting /api/autonomous/models with a 500 response")

    def handle_500(route):
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"success": False, "error": SERVER_ERROR_TEXT}),
        )

    page.route(MODELS_ROUTE_GLOB, handle_500)

    open_new_task_modal(page)
    select_claude_code(page)
    # React Query retries once globally (retry: 1) before surfacing the
    # error, so wait for the element instead of a fixed delay.
    page.locator("[data-testid='models-load-error']").first.wait_for(state="visible", timeout=10000)
    shot(page, "02-models-load-error")

    error_box = page.locator("[data-testid='models-load-error']")
    assert error_box.first.inner_text().strip() != "", "Load-error element rendered empty"
    assert_no_misleading_hint(page)

    page.unroute(MODELS_ROUTE_GLOB)
    log("ERROR", "✅ Server failure renders a load error, not the hint")


def step_legitimate_empty_keeps_hint(page):
    """200 + empty_reason (no keys configured) → the Settings hint stays."""
    log("EMPTY", "Intercepting with a legitimate 200 + empty_reason")

    def handle_200(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "success": True,
                    "models": [],
                    "empty_reason": (
                        "No active claude-code API keys configured " "for scope 'local'"
                    ),
                }
            ),
        )

    page.route(MODELS_ROUTE_GLOB, handle_200)

    # Re-open the modal fresh so the query re-fetches under the new route.
    close_btn = page.locator(".modal .btn-close, .modal [aria-label='Close']").first
    if close_btn.count() and close_btn.is_visible():
        close_btn.click()
        page.wait_for_timeout(300)
    open_new_task_modal(page)
    select_claude_code(page)
    page.wait_for_timeout(1000)
    shot(page, "03-legitimate-empty-hint")

    assert (
        page.locator("[data-testid='models-load-error']").count() == 0
    ), "Load error must not render for a legitimate empty list"
    hint_count = sum(page.get_by_text(text, exact=False).count() for text in NO_MODELS_HINT_TEXTS)
    assert hint_count > 0, "Settings hint missing for a legitimate empty list"

    page.unroute(MODELS_ROUTE_GLOB)
    log("EMPTY", "✅ Legitimate empty list keeps the Settings hint")


# ── Main ────────────────────────────────────────────────


def run_tests():
    print("\n" + "=" * 60, flush=True)
    print("  Autonomous Models Load-Error E2E (Issue #2667)", flush=True)
    print(f"  BASE_URL: {BASE_URL}", flush=True)
    print(f"  HEADLESS: {HEADLESS}", flush=True)
    print("=" * 60 + "\n", flush=True)

    passed = 0
    failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(10000)

        steps = [
            ("Login", lambda: login(page)),
            ("Server Error → Load Error", lambda: step_server_error_shows_load_error(page)),
            ("Legitimate Empty → Hint", lambda: step_legitimate_empty_keeps_hint(page)),
        ]

        for name, step_fn in steps:
            try:
                step_fn()
                passed += 1
            except Exception as e:
                print(f"  ❌ {name.upper()} FAILED: {e}", flush=True)
                failed += 1
                try:
                    shot(page, f"error-{name.lower().replace(' ', '-').replace('→', '-')}")
                except Exception:
                    pass

        browser.close()

    print("\n" + "=" * 60, flush=True)
    print(f"  Results: {passed} passed, {failed} failed", flush=True)
    print("=" * 60 + "\n", flush=True)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
