#!/usr/bin/env python3
"""
Open ACE - Session Messages Pagination E2E Playwright Test (Issue #241 #22)

Regression coverage for the composite-key keyset pagination of session messages.
Seeds a session with many deliberately OUT-OF-ORDER-timestamp messages (the
keyset-inversion scenario the unit tests also cover), opens the session detail
modal, and asserts:

  1. First screen renders only the most-recent page (<= DEFAULT_MESSAGE_PAGE_SIZE
     items), NOT the full history — with the "(loaded/total)" indicator.
  2. "Load older" walks back one adjacent page and the count grows.
  3. Searching shows the "scoped to loaded messages" banner (degradation notice).
  4. After loading everything, "Refresh to latest" re-anchors back to the recent
     page.

Run:
  # Against this checkout's server (must be running with its own DB):
  HEADLESS=true  python tests/e2e/e2e_session_messages_pagination_playwright.py
  HEADLESS=false python tests/e2e/e2e_session_messages_pagination_playwright.py

  BASE_URL=http://localhost:5173 HEADLESS=true python tests/e2e/e2e_session_messages_pagination_playwright.py

Preconditions (why this may be skipped in sandboxes):
  - A backend serving THIS branch must be up at BASE_URL (the new
    GET /sessions/<id>/messages endpoint and the paginated get_session envelope
    must exist; the auto-dev sandbox's :5001 serves a different, older tree).
  - The script seeds data via SessionManager against the SAME database the
    server uses, so it must run in an env that shares the server's DB config.
  - Admin credentials admin/admin123 (default dev seed).
"""

import json
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-session-pagination")

# Seed size: comfortably above the default page (100) so the first page is
# capped and an older page exists. Kept modest so seeding is fast.
SEED_COUNT = 150
DEFAULT_PAGE_SIZE = 100

passed = 0
failed = 0
errors = []
seeded_session_id = None


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    [SCREENSHOT] {name}.png")


def check(condition, description):
    global passed, failed
    if condition:
        passed += 1
        print(f"    [PASS] {description}")
    else:
        failed += 1
        errors.append(description)
        print(f"    [FAIL] {description}")


def server_ready(timeout_s=20):
    """Wait for the backend health endpoint (so the test fails fast + clearly
    rather than timing out inside Playwright when the env isn't up)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            code = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "--max-time",
                    "4",
                    f"{BASE_URL}/health",
                ],
                capture_output=True,
                text=True,
                timeout=6,
            ).stdout.strip()
            if code == "200":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def admin_token():
    """Login as admin via curl (urllib framing trips the gevent dev server) and
    return the session_token cookie value for injection into the browser context
    — far more reliable than driving the React login form."""
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
    return None


def api_delete_session(token, session_id):
    """Best-effort cleanup of the seeded session."""
    try:
        subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "DELETE",
                f"{BASE_URL}/api/workspace/sessions/{session_id}",
                "-H",
                f"Cookie: session_token={token}",
                "--max-time",
                "8",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        pass


def seed_session():
    """Create a session owned by admin (user_id=1) with SEED_COUNT messages
    inserted in SHUFFLED chronological order, so insertion order != display
    order — the keyset-inversion scenario. Returns (session_id, unique_title).

    Writes via SessionManager against the same DB the server reads, so the data
    is visible to the API/UI under test.
    """
    from app.modules.workspace.session_manager import SessionType, get_session_manager

    mgr = get_session_manager()
    unique = f"E2E-PAG-{uuid.uuid4().hex[:8]}"
    session = mgr.create_session(
        tool_name="qwen",
        user_id=1,  # admin owns it -> visible in admin's sessions list
        session_type=SessionType.CHAT.value,
        title=unique,
    )
    sid = session.session_id

    # 150 distinct timestamps; insert in shuffled order.
    base = datetime(2026, 7, 1, 12, 0, 0)
    offsets = list(range(SEED_COUNT))
    random.shuffle(offsets)
    for off in offsets:
        ts = base + timedelta(minutes=off)
        mgr.add_message(
            sid,
            role="user" if off % 2 == 0 else "assistant",
            content=f"msg-{off} " + ("alpha " * 5),
            tokens_used=10,
            model="glm-5",
            count_usage=True,  # so message_count == SEED_COUNT (non-milestone total)
            timestamp=ts,
        )
    return sid, unique


def open_session_detail(page, unique_title):
    """Navigate to /work/sessions and click the seeded session card to open the
    detail modal. Returns once the messages container is visible."""
    page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    # Click the card/row whose text includes the unique title.
    page.get_by_text(unique_title, exact=False).first.click()
    page.wait_for_selector(".modal-dialog .messages-container", timeout=15000)
    page.wait_for_timeout(800)  # let the embedded page settle


def count_header(page):
    """Return the '(loaded/total)' tuple parsed from the modal's messages h6."""
    el = page.query_selector(".modal-dialog h6.mb-0")
    if not el:
        return None
    text = el.inner_text()
    # "Messages (100/150)" -> ("100", "150")
    if "(" in text and "/" in text and ")" in text:
        pair = text.split("(", 1)[1].split(")", 1)[0]
        if "/" in pair:
            loaded, total = pair.split("/")
            return (loaded.strip(), total.strip())
    return None


def main():
    global seeded_session_id

    print("[1/5] Checking server readiness at", BASE_URL)
    if not server_ready():
        print(
            "    [SKIP] backend not reachable at",
            BASE_URL,
            "(this checkout's server must be running with its own DB).",
        )
        return 77  # skip exit code
    print("    [OK] server ready")

    print("[2/5] Admin login")
    token = admin_token()
    if not token:
        print("    [SKIP] could not obtain admin session_token; aborting.")
        return 77
    print("    [OK] logged in")

    print(f"[3/5] Seeding session with {SEED_COUNT} out-of-order messages")
    try:
        seeded_session_id, unique_title = seed_session()
    except Exception as exc:
        print(f"    [SKIP] seed failed (DB not shared with server?): {exc}")
        return 77
    print(f"    [OK] seeded session {seeded_session_id} ({unique_title})")

    try:
        print("[4/5] Driving browser")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies(
                [
                    {
                        "name": "session_token",
                        "value": token,
                        "domain": "localhost",
                        "path": "/",
                    }
                ]
            )
            page = context.new_page()

            # ---- Assertion 1: first screen is capped to one page -----------
            open_session_detail(page, unique_title)
            shot(page, "01-first-page")
            items = page.query_selector_all(".modal-dialog .message-item")
            header = count_header(page)
            print(f"    rendered items={len(items)} header={header}")
            check(
                len(items) <= DEFAULT_PAGE_SIZE,
                f"first page capped to <= {DEFAULT_PAGE_SIZE} (got {len(items)})",
            )
            check(
                header is not None and header[1] == str(SEED_COUNT),
                f"total indicator reflects {SEED_COUNT} (got {header})",
            )
            check(
                header is not None and int(header[0]) <= DEFAULT_PAGE_SIZE,
                f"loaded indicator is the page size, not the full count (got {header})",
            )

            # ---- Assertion 3 (before loading all): search degradation banner
            search = page.query_selector(".modal-dialog input.form-control")
            if search:
                search.fill("alpha")
                page.wait_for_timeout(600)
                shot(page, "02-search-banner")
                banner = page.query_selector(".modal-dialog .alert-warning")
                check(
                    banner is not None,
                    "search shows 'scoped to loaded messages' banner while more remain",
                )
                search.fill("")
                page.wait_for_timeout(400)

            # ---- Assertion 2: load older grows the count ------------------
            load_btn = page.query_selector(".modal-dialog .bi-arrow-bar-up")
            if load_btn:
                # click the parent button
                page.locator(".modal-dialog button:has(.bi-arrow-bar-up)").first.click()
                page.wait_for_timeout(1500)
                shot(page, "03-after-load-older")
                items_after = page.query_selector_all(".modal-dialog .message-item")
                header_after = count_header(page)
                print(f"    after load-older items={len(items_after)} header={header_after}")
                check(len(items_after) > len(items), "loading older grows the rendered count")
                check(
                    header_after is not None and header_after[0] == header_after[1],
                    f"after loading all, loaded==total (got {header_after})",
                )

            # ---- Assertion 4: refresh-to-latest re-anchors to recent page -
            refresh_btn = page.locator(".modal-dialog button:has(.bi-arrow-repeat)").first
            if refresh_btn.count() > 0:
                refresh_btn.click()
                page.wait_for_timeout(1500)
                shot(page, "04-after-refresh")
                header_refresh = count_header(page)
                items_refresh = page.query_selector_all(".modal-dialog .message-item")
                print(f"    after refresh items={len(items_refresh)} header={header_refresh}")
                check(
                    header_refresh is not None and int(header_refresh[0]) <= DEFAULT_PAGE_SIZE,
                    f"refresh-to-latest re-anchors to the recent page (got {header_refresh})",
                )

            browser.close()
    finally:
        print("[5/5] Cleanup seeded session")
        if seeded_session_id:
            api_delete_session(token, seeded_session_id)
            print(f"    [OK] deleted {seeded_session_id}")

    print("\n==== SUMMARY ====")
    print(f"passed={passed} failed={failed}")
    if errors:
        print("FAILURES:")
        for e in errors:
            print(f"  - {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
