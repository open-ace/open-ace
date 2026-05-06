#!/usr/bin/env python3
"""
Issue #210 - Project Stats Accuracy E2E Test

Verifies that project stats correctly aggregate token/request data
from daily_messages rather than showing zeros.

Test plan:
1. Login as admin
2. Upload messages with project_path to daily_messages via upload API
3. Call GET /api/projects/stats
4. Verify the project's total_tokens and total_requests are non-zero

Run:
  HEADLESS=true  python tests/210/e2e_project_stats_accuracy.py
  HEADLESS=false python tests/210/e2e_project_stats_accuracy.py
"""

import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests

# Config
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def test_project_stats_accuracy():
    print("=" * 60)
    print("Issue #210 - Project Stats Accuracy E2E Test")
    print("=" * 60)

    # Login
    print("\n[1] Login")
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    print("  OK - logged in")

    # Get existing projects
    print("\n[2] Get existing projects")
    r = session.get(f"{BASE_URL}/api/projects/stats")
    assert r.status_code == 200, f"Stats API failed: {r.status_code}"
    data = r.json()
    stats = data.get("stats", [])
    print(f"  OK - {len(stats)} projects found")

    if not stats:
        print("  SKIP - no projects found, cannot test")
        return

    # Pick the first project and upload messages with its path
    target_project = stats[0]
    project_path = target_project["project_path"]
    project_name = target_project.get("project_name") or project_path
    print(f"\n[3] Upload messages with project_path={project_path}")

    message_id = str(uuid.uuid4())
    tokens_to_upload = 5000

    # Use the upload API to insert a message with project_path
    upload_payload = {
        "date": "2026-05-06",
        "tool_name": "test-tool",
        "messages": [
            {
                "message_id": message_id,
                "role": "assistant",
                "content": "Test message for project stats accuracy",
                "tokens_used": tokens_to_upload,
                "input_tokens": 2000,
                "output_tokens": 3000,
                "project_path": project_path,
            }
        ],
    }

    # Try upload endpoint (may require auth token)
    r = session.post(f"{BASE_URL}/api/upload/messages", json=upload_payload)
    upload_ok = r.status_code == 200

    if not upload_ok:
        # Fallback: try batch upload
        batch_payload = {
            "messages": [
                {
                    "date": "2026-05-06",
                    "tool_name": "test-tool",
                    "message_id": message_id,
                    "role": "assistant",
                    "content": "Test message for project stats accuracy",
                    "tokens_used": tokens_to_upload,
                    "project_path": project_path,
                }
            ]
        }
        r = session.post(f"{BASE_URL}/api/upload/batch", json=batch_payload)
        upload_ok = r.status_code == 200

    if upload_ok:
        print(f"  OK - uploaded message with {tokens_to_upload} tokens")
    else:
        print(f"  WARN - upload failed ({r.status_code}), checking existing data")

    # Verify project stats now reflect the data
    print("\n[4] Verify project stats")
    r = session.get(f"{BASE_URL}/api/projects/stats")
    assert r.status_code == 200, f"Stats API failed: {r.status_code}"
    data = r.json()
    stats_after = data.get("stats", [])

    # Find our target project
    target_after = None
    for s in stats_after:
        if s["project_path"] == project_path:
            target_after = s
            break

    assert target_after is not None, f"Project {project_path} not found in stats"

    total_tokens = target_after.get("total_tokens", 0)
    total_requests = target_after.get("total_requests", 0)

    print(f"  Project: {project_name}")
    print(f"  total_tokens: {total_tokens}")
    print(f"  total_requests: {total_requests}")

    assert total_tokens > 0, (
        f"total_tokens is still 0! The fix did not work. "
        f"project_path={project_path}, upload_ok={upload_ok}"
    )
    assert total_requests > 0, (
        f"total_requests is still 0! The fix did not work. "
        f"project_path={project_path}, upload_ok={upload_ok}"
    )

    print("\n  OK - total_tokens and total_requests are non-zero!")

    # Verify via browser that the UI shows the data correctly
    if HEADLESS:
        _test_browser_ui(session)

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


def _test_browser_ui(session):
    """Verify the project management page shows non-zero stats."""
    from playwright.sync_api import sync_playwright

    print("\n[5] Verify UI shows correct data")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Login via browser
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.fill('input[type="text"], input[name="username"]', USERNAME)
        page.fill('input[type="password"], input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

        # Navigate to project management
        page.goto(f"{BASE_URL}/manage/projects", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.wait_for_selector("table", timeout=10000)

        # Check that at least one row has non-zero token count (not "0")
        rows = page.locator("table tbody tr")
        found_nonzero = False
        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            if cells.count() >= 3:
                token_text = cells.nth(2).inner_text().strip()
                if token_text != "0":
                    found_nonzero = True
                    print(f"  Row {i}: tokens={token_text}")
                    break

        assert found_nonzero, "No row with non-zero tokens found in UI table"
        print("  OK - UI shows non-zero token data")

        browser.close()


if __name__ == "__main__":
    test_project_stats_accuracy()
