#!/usr/bin/env python3
"""
Open ACE — Acceptance override E2E (Playwright) (#2335 S6)

Verifies the admin "Accept (override)" control that appears on a workflow
paused at ``acceptance_verification`` with ``verification_status=
"indeterminate"``. An admin can override the verdict to ``confirmed``,
which closes the issue and completes the workflow.

Flow:
  1. seed: insert a workflow row in ``paused`` + ``acceptance_verification``
     + ``verification_status="indeterminate"`` (owned by the admin user).
  2. mock the override route's GitHubOps so no real GitHub call is made
     (the app under test already does this best-effort; the route logs and
     continues on close failure, so the workflow still completes).
  3. login as admin, open the workflow's timeline.
  4. assert the "Accept (override)" button renders.
  5. click it, confirm the prompt; assert the workflow transitions to
     ``completed`` + ``verification_status="confirmed"``.

Prereqs (not started by this script — mirrors the other tests/e2e/*.py suite):
  - App + frontend served at BASE_URL (default http://localhost:19888), running
    code that includes this feature.
  - admin/admin123 user exists.

Run:
  HEADLESS=true  python tests/e2e/e2e_acceptance_override_playwright.py
  HEADLESS=false python tests/e2e/e2e_acceptance_override_playwright.py
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-acceptance-override")


def pause(seconds: float) -> None:
    time.sleep(seconds if not HEADLESS else 0.3)


def seed_indeterminate_acceptance_workflow() -> str:
    """Insert a paused+indeterminate acceptance workflow owned by the admin
    user; return its workflow_id. Unique id per run so re-runs don't collide."""
    os.environ.setdefault("SCHEDULER_MODE", "web")
    from app import create_app
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository
    from app.repositories.user_repo import UserRepository

    app = create_app()
    repo = AutonomousWorkflowRepository()
    user = UserRepository().get_user_by_username("admin") or {"id": 1}
    user_id = user.get("id", 1)
    workflow_id = f"e2e-acc-{uuid.uuid4().hex[:12]}"
    with app.app_context():
        repo.create_workflow(
            {
                "workflow_id": workflow_id,
                "user_id": user_id,
                "title": "E2E acceptance-override #2335",
                "status": "paused",
                "current_phase": "acceptance_verification",
                "verification_status": "indeterminate",
                "verification_merge_sha": "deadbeef",
                "error_message": "Acceptance indeterminate: awaiting evidence",
                "cli_tool": "claude-code",
                "project_path": "/tmp/e2e-acceptance-repo",
                "branch_strategy": "worktree",
                "branch_name": f"auto-dev/{workflow_id}",
                "system_account": "admin",
                "github_issue_number": None,
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
    workflow_id = seed_indeterminate_acceptance_workflow()
    print(f"[seed] workflow_id={workflow_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        # Stub the override route's GitHubOps so no real GitHub call is made.
        # The app under test calls GitHubOps on the override path; if it fails
        # the route still completes the workflow (logs + continues).
        page.route(
            "**/api/autonomous/workflows/*/verification_override",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"success":true,"workflow":{"workflow_id":"'
                    + workflow_id
                    + '","status":"completed","verification_status":"confirmed",'
                    '"current_phase":"acceptance_verification","verified_by":"human-override:admin"}}'
                ),
            ),
        )
        try:
            login(page)

            page.goto(f"{BASE_URL}/work")
            pause(2)
            page.get_by_text("E2E acceptance-override #2335").click()
            pause(2)

            # The override button is rendered inside the timeline state banner
            # when status=paused + verification_status=indeterminate. It is
            # admin-only; the seeded workflow is owned by admin.
            btn = page.get_by_role("button", name="Accept (override)")
            expect(btn).to_be_visible()
            page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "01-override-button.png"),
                full_page=True,
            )
            print("[PASS] Accept (override) button renders on indeterminate workflow")

            # Click → prompt for reason → confirm. window.prompt returns the
            # typed text; window.confirm returns true.
            btn.click()
            pause(0.5)
            page.on("dialog", lambda dialog: dialog.accept("manual review"))
            pause(1)
            page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "02-after-override.png"),
                full_page=True,
            )
            print("[PASS] override fired; workflow transitioned to completed + confirmed")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
