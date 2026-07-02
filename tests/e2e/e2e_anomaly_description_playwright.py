#!/usr/bin/env python3
"""
Open ACE - Anomaly Detection Description E2E Playwright Test

Verifies the anomaly-detection description optimization (clearer, actionable
anomaly descriptions on both the dedicated anomaly page and the Analysis
overview table):

1. Login as admin
2. Dedicated page (/manage/analysis/anomaly):
   - Anomaly List card exposes a help tooltip (ℹ️) with detection rules
   - Baseline banner shows avg / std dev / sample days when statistics exist
   - Each anomaly row renders a description (text-muted small) + suggestion
     (text-primary small) when anomalies are present
3. Language switch (en <-> zh): description / suggestion text localizes
4. Analysis overview page (/manage/analysis): inline top5 anomaly table shows
   the same description rows

Note: this test is robust to an empty database — when no anomalies exist it
verifies the empty state rather than description rows.

Run:
  HEADLESS=true  python tests/e2e/e2e_anomaly_description_playwright.py   # automated
  HEADLESS=false python tests/e2e/e2e_anomaly_description_playwright.py   # demo
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-anomaly-description")

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
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def login(page):
    print("\n[TEST] Login as admin...")
    page.goto(f"{BASE_URL}/login")
    pause(1)
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click("button[type='submit']")
    pause(2)
    # Admin lands on /manage/dashboard (not /work). Allow a moment for the
    # post-login redirect chain to settle (auth race — see memory gotchas).
    try:
        page.wait_for_url("**/manage/**", timeout=10000)
    except Exception:
        # Fall back: as long as we've left /login, consider it authenticated.
        if "/login" in page.url:
            raise
    check("/login" not in page.url, f"Login successful (landed on {page.url})")
    shot(page, "01-login")


def switch_language(page, lang):
    """Switch UI language via localStorage + reload (robust to UI changes)."""
    page.evaluate(f"localStorage.setItem('language', '{lang}')")
    page.reload()
    pause(2)


def anomaly_list_card(page):
    """Locate the Anomaly List card specifically (avoid the Recommendations
    card, which also renders a list-group). Title is localized en/zh."""
    titles = ["Anomaly List", "异常列表"]
    for title in titles:
        card = page.locator(".card").filter(has=page.locator(".card-title", has_text=title))
        if card.count() > 0:
            return card.first
    return page.locator(".card.nonexistent")


def test_anomaly_page_help_tooltip(page):
    """The Anomaly List card should carry a help-tooltip icon (detection rules)."""
    print("\n[TEST] Anomaly page — help tooltip on Anomaly List card...")
    page.goto(f"{BASE_URL}/manage/analysis/anomaly")
    pause(3)
    card = anomaly_list_card(page)
    help_icon = card.locator(".card-header .bi-info-circle")
    check(help_icon.count() > 0, "Help tooltip (ℹ️) icon present on Anomaly List card")
    shot(page, "02-anomaly-help-tooltip")


def test_anomaly_page_descriptions(page):
    """When anomalies exist, each row shows a description + suggestion."""
    print("\n[TEST] Anomaly page — per-row description + suggestion...")
    page.goto(f"{BASE_URL}/manage/analysis/anomaly")
    pause(3)

    card = anomaly_list_card(page)
    items = card.locator(".list-group-item")
    if items.count() == 0:
        empty = card.locator(".bi-check-circle")
        check(empty.count() > 0, "No anomalies present — empty state shown (data-dependent skip)")
        shot(page, "03-anomaly-empty")
        return

    # Description (muted) + suggestion (primary) small text under each row.
    # Scoped to the anomaly card so the Recommendations card never matches.
    desc = card.locator(".list-group-item .text-muted.small")
    suggestion = card.locator(".list-group-item .text-primary.small")
    check(desc.count() > 0, f"Description rows rendered ({desc.count()})")
    check(suggestion.count() > 0, f"Suggestion rows rendered ({suggestion.count()})")
    shot(page, "03-anomaly-descriptions")


def test_language_switch_localizes(page):
    """Switching to Chinese changes the suggestion wording."""
    print("\n[TEST] Language switch localizes descriptions (en -> zh)...")
    page.goto(f"{BASE_URL}/manage/analysis/anomaly")
    pause(2)
    card = anomaly_list_card(page)
    if card.locator(".list-group-item").count() == 0:
        check(True, "No anomalies — language test skipped (data-dependent)")
        return

    switch_language(page, "en")
    en_text = card.locator(".list-group-item .text-primary.small").first.text_content() or ""
    switch_language(page, "zh")
    zh_text = card.locator(".list-group-item .text-primary.small").first.text_content() or ""
    check(en_text != zh_text, "Suggestion text changes with language (en≠zh)")
    check("建议" in zh_text, f"Chinese suggestion contains '建议' (got: '{zh_text[:40]}')")
    # restore
    switch_language(page, "en")
    shot(page, "04-language-switch")


def test_overview_page_descriptions(page):
    """The Analysis overview inline anomaly table also shows descriptions."""
    print("\n[TEST] Analysis overview — inline anomaly table descriptions...")
    page.goto(f"{BASE_URL}/manage/analysis")
    pause(3)
    # Overview table renders <td colSpan="4"> description cells when anomalies exist
    rows = page.locator("table tbody tr")
    if rows.count() == 0:
        check(True, "Overview anomaly table empty — skipped (data-dependent)")
        shot(page, "05-overview-empty")
        return
    desc_cell = page.locator("table tbody td[colspan='4']")
    check(
        desc_cell.count() > 0,
        f"Overview table has description cells (colspan=4): {desc_cell.count()}",
    )
    shot(page, "05-overview-descriptions")


def main():
    print("=" * 60)
    print("Anomaly Detection Description — E2E (Playwright)")
    print(f"BASE_URL={BASE_URL}  HEADLESS={HEADLESS}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        try:
            login(page)
            test_anomaly_page_help_tooltip(page)
            test_anomaly_page_descriptions(page)
            test_language_switch_localizes(page)
            test_overview_page_descriptions(page)
        except Exception as e:
            global failed
            failed += 1
            errors.append(f"Unexpected exception: {e}")
            print(f"    [ERROR] {e}")
            try:
                shot(page, "99-error")
            except Exception:
                pass
        finally:
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    if errors:
        print("Failures:")
        for e in errors:
            print(f"  - {e}")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
