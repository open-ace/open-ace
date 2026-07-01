#!/usr/bin/env python3
"""
Issue #824 - Conversation History Page Pagination Navigation E2E Test

Verifies the shared Pagination component is wired into the Conversation History
page and shows the complete navigation info:
  1. Total page count (pageInfo text contains total pages)
  2. Current page highlight (active button w/ aria-current="page")
  3. Jump-to-page input with validation (valid jump + 4 invalid cases)
  4. Sort state retained after paging
  5. Filter reset returns to page 1
  6. Pagination also renders in fullscreen mode

Assertions are intentionally language-agnostic (ARIA attributes + structural
selectors), so they hold across en/zh/ja/ko.

Note on HTTP: the Flask/gevent backend returns 502 to Python's urllib/requests
stack but responds correctly to curl and to the browser. API calls in this test
therefore use curl (see project E2E gotchas); the browser drives the UI.

Environment:
  WEB_BASE  Playwright UI origin  (default http://localhost:19888)
  API_BASE  curl API origin       (default http://localhost:19888)
  HEADLESS  true|false            (default true)

Run (normal):
  HEADLESS=true  python tests/issues/824/e2e_conversation_history_pagination.py
  HEADLESS=false python tests/issues/824/e2e_conversation_history_pagination.py

Run against a local Vite dev server (exercises current source rather than the
built dist):
  WEB_BASE=http://localhost:3000 API_BASE=http://localhost:19888 HEADLESS=true \\
    python tests/issues/824/e2e_conversation_history_pagination.py
"""

import json
import math
import os
import subprocess
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

WEB_BASE = os.environ.get("WEB_BASE", os.environ.get("BASE_URL", "http://localhost:19888"))
API_BASE = os.environ.get("API_BASE", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
ITEMS_PER_PAGE = 20
PAGE_URL = f"{WEB_BASE}/manage/analysis/conversation-history"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-conversation-history-pagination")

passed = 0
failed = 0


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {desc} {detail}")
    else:
        failed += 1
        print(f"  FAIL: {desc} {detail}")


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot: {name}.png")


def curl(url, read_jar=None, write_jar=None, method="GET", data=None):
    """HTTP via curl. Returns (status_code, body_text)."""
    cmd = ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}"]
    if write_jar:
        cmd += ["-c", write_jar]
    if read_jar:
        cmd += ["-b", read_jar]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json"]
        if data is not None:
            cmd += ["-d", data]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=45).stdout
    if "\n" in out:
        body, code = out.rsplit("\n", 1)
    else:
        body, code = out, "0"
    try:
        return int(code), body
    except ValueError:
        return 0, body


def login():
    """Login via curl (urllib 502s against the gevent backend). Returns token."""
    jar = tempfile.NamedTemporaryFile(delete=False, suffix=".cookies").name
    payload = json.dumps({"username": USERNAME, "password": PASSWORD})
    code, body = curl(f"{API_BASE}/api/auth/login", write_jar=jar, method="POST", data=payload)
    token = ""
    # Prefer the cookie jar (Netscape format). Note: HttpOnly cookies are stored
    # with a "#HttpOnly_<domain>" domain prefix — such lines start with '#' but
    # are NOT comments, so we match by field count + name rather than skipping
    # on a leading '#'.
    if os.path.exists(jar):
        with open(jar) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and parts[5] == "session_token":
                    token = parts[6]
                    break
    # Fall back to JSON body.
    if not token and body:
        try:
            token = json.loads(body).get("session_token") or json.loads(body).get("token") or ""
        except Exception:
            pass
    return token, jar, code


def active_page_number(page):
    """Current page from the active pagination button (aria-current='page')."""
    btn = page.locator(".pagination-container button[aria-current='page']")
    if btn.count() == 0:
        return None
    try:
        return int(btn.first.inner_text().strip())
    except (ValueError, TypeError):
        return None


def jump_input(page):
    """The visible jump input (desktop layout in prod, mobile layout under some
    dev-server CSS setups). Both share the component's inputValue state, so
    interacting with either drives the same jump logic."""
    loc = page.locator(".pagination-container input[type='number']")
    for i in range(loc.count()):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return loc.first


def wait_table(page, timeout=10000):
    try:
        page.wait_for_selector("table tbody tr", timeout=timeout)
        return True
    except Exception:
        return False


def test_pagination():
    global passed, failed
    print("=" * 60)
    print("Conversation History Pagination E2E Test (#824)")
    print(f"  WEB_BASE={WEB_BASE}  API_BASE={API_BASE}  HEADLESS={HEADLESS}")
    print("=" * 60)

    token, jar, login_code = login()
    check("Login & session token", bool(token), f"(http={login_code}, len={len(token)})")

    # Resolve total / expected page count via API (drives several assertions)
    code, body = curl(f"{API_BASE}/api/conversation-history?limit=1&offset=0", read_jar=jar)
    ch = {}
    if code == 200:
        try:
            ch = json.loads(body)
        except Exception:
            ch = {}
    total = ch.get("total", 0)
    total_pages = math.ceil(total / ITEMS_PER_PAGE) if total else 0
    check(
        "API returns >20 records (multi-page)",
        total > ITEMS_PER_PAGE,
        f"(http={code}, total={total}, pages={total_pages})",
    )
    if total_pages <= 1:
        print("  Not enough data to exercise pagination; aborting.")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )
        page = context.new_page()

        print("\n[1] Page load + pagination rendered")
        page.goto(PAGE_URL, wait_until="networkidle")
        page.wait_for_timeout(5000)
        if not wait_table(page, 15000):
            check("Table loaded with rows", False, "- timeout")
            shot(page, "01_timeout")
            browser.close()
            return False
        check("Table loaded with rows", True)
        try:
            page.wait_for_selector(".pagination-container", timeout=10000)
            check("Pagination container rendered", True)
        except Exception:
            check("Pagination container rendered", False, "- timeout")
            shot(page, "01_no_pagination")
            browser.close()
            return False
        shot(page, "01_loaded")

        total_pages_str = str(total_pages)

        print("\n[2] Total page count shown in page info")
        container_text = page.locator(".pagination-container").first.inner_text()
        check(
            "Page info contains total page count",
            total_pages_str in container_text,
            f"(expected '{total_pages_str}' in container text)",
        )

        print("\n[3] Current page highlighted (aria-current)")
        cur = active_page_number(page)
        check("Active page is 1 on initial load", cur == 1, f"(active={cur})")
        check(
            "Active button carries aria-current='page'",
            page.locator(".pagination-container button[aria-current='page']").count() >= 1,
        )

        print("\n[4] Jump to a valid page via input + Enter")
        target = min(5, total_pages)
        if target < 2:
            target = 2
        inp = jump_input(page)
        inp.fill(str(target))
        inp.press("Enter")
        page.wait_for_timeout(3000)
        wait_table(page)
        cur = active_page_number(page)
        check(
            f"Active page became {target} after valid jump",
            cur == target,
            f"(active={cur})",
        )
        shot(page, "02_after_jump")

        print("\n[5] Invalid jump inputs keep page unchanged + show error")
        for val in ["0", "-5", "99999"]:
            before = active_page_number(page)
            inp = jump_input(page)
            inp.fill(val)
            inp.press("Enter")
            page.wait_for_timeout(800)
            alert_count = page.locator(".pagination-container [role='alert']").count()
            after = active_page_number(page)
            check(
                f"Invalid '{val}' shows error alert",
                alert_count >= 1,
                f"(alerts={alert_count})",
            )
            check(
                f"Invalid '{val}' keeps page unchanged",
                before == after,
                f"(before={before}, after={after})",
            )
        # Non-numeric into type=number input: browser sanitizes; must not crash or change page.
        before = active_page_number(page)
        try:
            jump_input(page).fill("abc")
            jump_input(page).press("Enter")
        except Exception as exc:
            print(f"  (non-numeric fill rejected by number input: {type(exc).__name__})")
        page.wait_for_timeout(800)
        after = active_page_number(page)
        check(
            "Non-numeric input keeps page unchanged (no crash)",
            before == after,
            f"(before={before}, after={after})",
        )
        shot(page, "03_invalid_inputs")

        print("\n[6] Sort state retained across paging")
        first_header = page.locator("table thead th").first
        header_class = first_header.get_attribute("class") or ""
        if "cursor-pointer" in header_class:
            first_header.click()
            page.wait_for_timeout(1500)
            sort_icon = first_header.locator("i.bi-arrow-up, i.bi-arrow-down")
            check("Sort icon appears after header click", sort_icon.count() >= 1)
            other = 3 if target != 3 else 4
            if other > total_pages:
                other = total_pages - 1
            jump_input(page).fill(str(other))
            jump_input(page).press("Enter")
            page.wait_for_timeout(3000)
            wait_table(page)
            sort_icon2 = first_header.locator("i.bi-arrow-up, i.bi-arrow-down")
            check(
                "Sort icon persists after paging",
                sort_icon2.count() >= 1,
                f"(sorted to page {other})",
            )
            shot(page, "04_sort_after_paging")
        else:
            check("Sortable header available for sort-retention check", False)

        print("\n[7] Filter reset returns to page 1")
        reset_btn = page.locator("button:has-text('Reset'), button:has-text('重置')")
        if reset_btn.count() > 0:
            reset_btn.first.click()
            page.wait_for_timeout(3000)
            wait_table(page)
            cur = active_page_number(page)
            check("Active page returns to 1 after reset", cur == 1, f"(active={cur})")
            shot(page, "05_after_reset")
        else:
            check("Reset button present", False)

        print("\n[8] Pagination present in fullscreen mode")
        fs_btn = page.locator("button:has(i.bi-fullscreen)")
        if fs_btn.count() > 0:
            fs_btn.first.click()
            page.wait_for_timeout(1500)
            fullscreen_pagi = page.locator(".conversation-history-fullscreen .pagination-container")
            check(
                "Pagination renders inside fullscreen overlay",
                fullscreen_pagi.count() >= 1,
                f"(found={fullscreen_pagi.count()})",
            )
            shot(page, "06_fullscreen")
            exit_btn = page.locator("button:has(i.bi-fullscreen-exit)")
            if exit_btn.count() > 0:
                exit_btn.first.click()
                page.wait_for_timeout(800)
        else:
            check("Fullscreen button present", False)

        print("\n" + "=" * 60)
        print(f"RESULTS: {passed} passed, {failed} failed")
        print("=" * 60)
        browser.close()

    return failed == 0


if __name__ == "__main__":
    success = test_pagination()
    sys.exit(0 if success else 1)
