#!/usr/bin/env python3
"""
Open ACE — Scope-exceeded failure banner E2E (Playwright) (#2309)

Verifies the timeline top banner that lets a user raise the per-workflow
changed-files cap and retry after a round fails ONLY on the scope cap.

Flow:
  1. seed: insert a workflow row in failed + scope-exceeded state (owned by the
     admin user), via the app's repo against the configured DB.
  2. login as admin, open the workflow's timeline.
  3. assert the scope-exceeded banner renders with the parsed file count/limit.
  4. choose a preset cap and click "Retry with new cap"; assert the retry
     request fires and the banner clears (workflow leaves the failed state).

Prereqs (not started by this script — mirrors the other tests/e2e/*.py suite):
  - App + frontend served at BASE_URL (default http://localhost:19888), running
    code that includes this feature.
  - admin/admin123 user exists.
  - The seeding helper uses the app DB (configured the same way the running app
    is).

Run:
  HEADLESS=true  python tests/e2e/e2e_scope_exceeded_banner_playwright.py
  HEADLESS=false python tests/e2e/e2e_scope_exceeded_banner_playwright.py
"""

import os
import sys
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-scope-exceeded")
SCOPE_ERROR = "Autonomous change scope exceeded: 143 files changed (limit 60). Sample: a.py, b.py"


def pause(seconds: float) -> None:
    time.sleep(seconds if not HEADLESS else 0.3)


def seed_failed_scope_exceeded_workflow() -> str:
    """Insert a failed+scope-exceeded workflow owned by the admin user; return
    its workflow_id. Idempotent-ish: a unique id per run so re-runs don't
    collide."""
    os.environ.setdefault("SCHEDULER_MODE", "web")
    from app import create_app
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository
    from app.repositories.user_repo import UserRepository

    app = create_app()
    repo = AutonomousWorkflowRepository()
    user = UserRepository().get_user_by_username("admin") or {"id": 1}
    user_id = user.get("id", 1)
    workflow_id = f"e2e-scope-{uuid.uuid4().hex[:12]}"
    with app.app_context():
        repo.create_workflow(
            {
                "workflow_id": workflow_id,
                "user_id": user_id,
                "title": "E2E scope-exceeded #2309",
                "status": "failed",
                "current_phase": "development",
                "error_message": SCOPE_ERROR,
                "cli_tool": "claude-code",
                "project_path": "/tmp/e2e-scope-repo",
                "branch_strategy": "worktree",
                "branch_name": f"auto-dev/{workflow_id}",
                "system_account": "admin",
            }
        )
    return workflow_id


def login(page):
    page.goto(f"{BASE_URL}/login")
    pause(1)
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin123")
    page.click("button[type='submit']")
    pause(2)
    page.wait_for_url("**/work**", timeout=10000)


def main():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    workflow_id = seed_failed_scope_exceeded_workflow()
    print(f"[seed] workflow_id={workflow_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            login(page)

            # Open the workflow's timeline directly (the app routes /work to the
            # list; selecting a row opens the timeline). Navigate via the list
            # and click the seeded workflow.
            page.goto(f"{BASE_URL}/work")
            pause(2)
            page.get_by_text("E2E scope-exceeded #2309").click()
            pause(2)

            banner = page.get_by_test_id("scope-exceeded-banner")
            expect(banner).to_be_visible()
            expect(banner).to_contain_text("143")
            expect(banner).to_contain_text("60")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "01-banner.png"), full_page=True)
            print("[PASS] scope-exceeded banner renders with file count + limit")

            # The smallest preset (150) is selected by default; retry with it.
            banner.get_by_role("button", name="Retry with new cap").click()
            pause(2)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "02-after-retry.png"), full_page=True)
            expect(banner).to_be_hidden()
            print("[PASS] retry with new cap clears the banner (workflow left failed state)")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
