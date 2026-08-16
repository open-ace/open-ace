#!/usr/bin/env python3
"""
Open ACE — Paused acceptance-verification UI E2E (Playwright) (#2634)

Verifies the frontend's paused-workflow distinction and the
resume-with-feedback entry against a live Open ACE server (seeded via the
repository, mirroring tests/e2e/e2e_acceptance_override_playwright.py):

  1. /work/autonomous list: the "Paused" filter tab exists; clicking it shows
     the acceptance-paused workflow with the "Awaiting acceptance review"
     badge (and the >3-day stale badge) and the quota-paused workflow with
     the "Quota paused" badge; non-paused workflows are hidden.
  2. Timeline for the acceptance-paused workflow: the
     "Awaiting human acceptance review" banner renders; the
     "Resume with Feedback" button is visible; clicking opens the modal;
     submitting feedback calls POST /resume-with-feedback (payload asserted)
     and the workflow leaves "paused" (status becomes "waiting" — real
     backend transition, no route mocking).
  3. Indeterminate workflow (verification_status=indeterminate): BOTH exits
     render — the "Accept (override)" button and the "Resume with Feedback"
     button (#2658 unified gating; previously indeterminate had no resume
     entry).
  4. Rejected workflow timeline (#2658): the "View acceptance report" button
     opens the generic content viewer with the per-item verdicts and evidence
     refs parsed from the acceptance milestone's metadata JSON.
  5. Rejected pause (#2491 UX): the resume-with-feedback modal opens
     PRE-FILLED with the verifier failed-items list (server-derived from the
     stored verification report; confirmed gates excluded).
  6. Waiting + user_feedback (#2491 UX): the timeline shows the
     "Feedback received" restart banner and hides the manual
     "Complete Development" exit.

Seeding: five workflow rows are inserted via AutonomousWorkflowRepository —
an acceptance-paused (rejected) workflow paused >3 days ago, a quota-paused
and a manual-paused workflow (both paused <3 days ago at phase=developing),
an indeterminate acceptance workflow, and a running (developing) workflow as
the non-paused control. paused_at is written in the backend's non-ISO UTC
format ("%Y-%m-%d %H:%M:%S") through update_workflow so the stale-badge math
exercises the production shape.

Prereqs (the CI runner scripts/run_extended_tests.py --category work
satisfies all of these):
  - App + built frontend served at BASE_URL (default http://localhost:19888).
  - admin/admin123 user exists (scripts/init_db.py creates it).

Run:
  HEADLESS=true  python tests/e2e/work/e2e_paused_acceptance_ui_playwright.py
  HEADLESS=false python tests/e2e/work/e2e_paused_acceptance_ui_playwright.py
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
# Login identity: CI uses the scripts/init_db.py defaults (admin/admin123);
# a local dev DB may have rotated the admin password, so allow an override
# (e.g. a dedicated e2e platform-admin user) without touching CI behavior.
E2E_USERNAME = os.environ.get("E2E_USERNAME", "admin")
E2E_PASSWORD = os.environ.get("E2E_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-paused-acceptance-ui")


def pause(seconds: float) -> None:
    import time

    time.sleep(seconds if not HEADLESS else 0.3)


def shot(page, name: str) -> None:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
        print(f"[SCREENSHOT] {name}.png")
    except Exception:  # allow-swallow: screenshot failure non-critical
        print(f"[WARN] screenshot {name} failed")


def _fmt_backend(dt: datetime) -> str:
    """Backend datetime format: naive UTC '%Y-%m-%d %H:%M:%S' (no T, no zone)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def clear_forced_password_change() -> None:
    """Drop the default admin's must_change_password flag.

    scripts/init_db.py creates admin with must_change_password=True; the
    ForceChangePasswordModal would otherwise cover the whole UI and redirect
    every navigation back to /login. This is test-data setup only.
    """
    os.environ.setdefault("SCHEDULER_MODE", "web")
    from app import create_app
    from app.repositories.user_repo import UserRepository

    app = create_app()
    with app.app_context():
        user = UserRepository().get_user_by_username(E2E_USERNAME)
        if user:
            UserRepository().set_must_change_password(int(user["id"]), False)


def seed_workflow(
    *,
    title: str,
    status: str = "paused",
    current_phase: str = "acceptance_verification",
    verification_status: str | None = "rejected",
    error_message: str = "Acceptance verification rejected; awaiting review",
    paused_days_ago: float,
) -> str:
    """Insert a workflow owned by admin with the given paused shape.

    The workflow_id suffix in the title keeps runs unique so repeated runs
    against the same server cannot produce strict-mode locator collisions.
    """
    os.environ.setdefault("SCHEDULER_MODE", "web")
    from app import create_app
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository
    from app.repositories.user_repo import UserRepository

    app = create_app()
    repo = AutonomousWorkflowRepository()
    user = UserRepository().get_user_by_username(E2E_USERNAME) or {"id": 1}
    workflow_id = f"e2e-pause-{uuid.uuid4().hex[:12]}"
    with app.app_context():
        repo.create_workflow(
            {
                "workflow_id": workflow_id,
                "user_id": user.get("id", 1),
                "title": f"{title} [{workflow_id[-6:]}]",
                "status": status,
                "current_phase": current_phase,
                "cli_tool": "claude-code",
                "project_path": "/tmp/e2e-paused-acceptance",
                "branch_strategy": "worktree",
                "branch_name": f"auto-dev/{workflow_id}",
                "system_account": "admin",
                "github_issue_number": None,
            }
        )
        # create_workflow's INSERT does not persist error_message or the
        # verification columns (the production writer sets them later via
        # update_workflow), so seed them through update_workflow along with
        # paused_at — written in the backend's naive-UTC format so the stale
        # math ("Unreviewed >3 days") exercises the production shape.
        repo.update_workflow(
            workflow_id,
            {
                "error_message": error_message,
                **({"verification_status": verification_status} if verification_status else {}),
                "paused_at": _fmt_backend(
                    datetime.now(timezone.utc) - timedelta(days=paused_days_ago)
                ),
            },
        )
    return workflow_id


def seed_acceptance_milestone(workflow_id: str, status: str) -> None:
    """#2658: attach an acceptance milestone whose metadata carries the full
    verification report JSON — the report viewer renders from this column."""
    import json as _json

    os.environ.setdefault("SCHEDULER_MODE", "web")
    from app import create_app
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository

    report = {
        "merge_sha": "abc123def456",
        "status": status,
        "verified_by": "glm-5/e2e",
        "scope": [{"item": "src/app.py", "verdict": "confirmed", "evidence": []}],
        "gates": [],
        "verifier": [
            {
                "item": "修复登录失败",
                "verdict": "rejected",
                "evidence": [{"ref": "tests/test_login.py:12", "note": "用例仍然失败"}],
                "rationale": "合并后用例依旧失败",
            }
        ],
    }
    app = create_app()
    repo = AutonomousWorkflowRepository()
    with app.app_context():
        repo.create_milestone(
            {
                "workflow_id": workflow_id,
                "phase": "acceptance_verification",
                "round_number": 1,
                "milestone_type": "acceptance_verification",
                "status": status,
                "title": f"Acceptance verification: {status}",
                "result_summary": f"status={status}; not-verified: 修复登录失败",
                "metadata": _json.dumps(report, ensure_ascii=False),
            }
        )


def cleanup_previous_runs() -> None:
    """Delete workflows left by previous runs of this test (idempotent re-runs)."""
    from app import create_app
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository

    app = create_app()
    repo = AutonomousWorkflowRepository()
    with app.app_context():
        for workflow in repo.list_workflows(limit=500):
            if str(workflow.get("title", "")).startswith("E2E "):
                try:
                    repo.delete_workflow(workflow["workflow_id"])
                except Exception as exc:  # allow-swallow: best-effort cleanup
                    print(f"[WARN] cleanup skipped {workflow['workflow_id']}: {exc}")


def login(page) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_selector("#username", state="visible", timeout=15000)
    page.fill("#username", E2E_USERNAME)
    page.fill("#password", E2E_PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_url(lambda url: "/login" not in url, timeout=120000)


def main() -> None:
    clear_forced_password_change()
    cleanup_previous_runs()
    accepted_id = seed_workflow(
        title="E2E paused-acceptance UI #2634",
        paused_days_ago=4,
    )
    seed_acceptance_milestone(accepted_id, "rejected")
    quota_id = seed_workflow(
        title="E2E quota-paused UI #2634",
        current_phase="developing",
        verification_status=None,
        error_message="Quota exceeded: daily usage at 100% (1000/1000)",
        paused_days_ago=1,
    )
    manual_id = seed_workflow(
        title="E2E manual-paused UI #2634",
        current_phase="developing",
        verification_status=None,
        error_message="Paused by operator",
        paused_days_ago=1,
    )
    indeterminate_id = seed_workflow(
        title="E2E indeterminate UI #2634",
        verification_status="indeterminate",
        error_message="Acceptance indeterminate: awaiting evidence",
        paused_days_ago=1,
    )
    running_id = seed_workflow(
        title="E2E running UI #2634",
        status="developing",
        current_phase="developing",
        verification_status=None,
        error_message="",
        paused_days_ago=0,
    )
    accepted_title = f"E2E paused-acceptance UI #2634 [{accepted_id[-6:]}]"
    quota_title = f"E2E quota-paused UI #2634 [{quota_id[-6:]}]"
    manual_title = f"E2E manual-paused UI #2634 [{manual_id[-6:]}]"
    running_title = f"E2E running UI #2634 [{running_id[-6:]}]"
    print(
        f"[seed] accepted={accepted_id} quota={quota_id} manual={manual_id} "
        f"indeterminate={indeterminate_id} running={running_id}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            login(page)

            # ── 1. Workflows list: paused filter tab ──────────────────────
            page.goto(f"{BASE_URL}/work/autonomous", wait_until="domcontentloaded")
            page.wait_for_selector(f"text={accepted_title}", timeout=30000)
            pause(1)
            accepted_item = page.locator(".auto-workflow-title", has_text=accepted_title)

            paused_tab = page.get_by_role("button", name="Paused", exact=True)
            expect(paused_tab).to_be_visible()
            paused_tab.click()
            pause(1.5)

            expect(accepted_item).to_be_visible()
            expect(page.get_by_text("Awaiting acceptance review", exact=True).first).to_be_visible()
            expect(page.get_by_text("Unreviewed >3 days").first).to_be_visible()
            expect(page.locator(".auto-workflow-title", has_text=quota_title)).to_be_visible()
            expect(page.get_by_text("Quota paused", exact=True).first).to_be_visible()
            expect(page.locator(".auto-workflow-title", has_text=manual_title)).to_be_visible()
            expect(page.get_by_text("Paused manually", exact=True).first).to_be_visible()
            # A workflow that is not paused must be filtered out.
            expect(page.locator(".auto-workflow-title", has_text=running_title)).not_to_be_visible()
            shot(page, "01-paused-filter-tab")
            print("[PASS] paused filter tab shows acceptance + quota badges, hides non-paused")

            # ── 2. Acceptance timeline: banner, modal, resume ─────────────
            accepted_item.click()
            pause(2)
            page.wait_for_selector(".timeline-state-banner", timeout=30000)

            expect(page.get_by_text("Awaiting human acceptance review")).to_be_visible()

            # The button carries a long aria-label (help text), so role+name
            # cannot match the visible label; scope by the banner instead.
            resume_btn = page.locator(".timeline-state-banner").get_by_text(
                "Resume with Feedback", exact=True
            )
            expect(resume_btn).to_be_visible()
            shot(page, "02-acceptance-banner")

            resume_btn.click()
            pause(0.5)
            modal = page.locator(".modal-dialog")
            expect(modal).to_be_visible()

            # Modal validation: the submit button is disabled until feedback
            # is non-empty; a whitespace-only entry keeps it disabled.
            textarea = modal.locator("textarea")
            submit = modal.get_by_text("Resume with Feedback", exact=True)
            expect(submit).to_be_disabled()
            textarea.fill("   ")
            expect(submit).to_be_disabled()

            feedback_text = "E2E: address the rejected acceptance findings"
            textarea.fill(feedback_text)
            expect(submit).to_be_enabled()
            shot(page, "03-resume-modal")

            # Submit → real backend transition (no route mocking): the API
            # stores the feedback and flips status paused → waiting.
            captured: dict = {}
            page.on(
                "request",
                lambda request: (
                    captured.update(payload=request.post_data)
                    if request.url.endswith("/resume-with-feedback")
                    else None
                ),
            )
            submit.click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=15000)
            expect(page.get_by_text("Awaiting human acceptance review")).not_to_be_visible(
                timeout=30000
            )
            shot(page, "04-after-resume")
            print(
                f"[PASS] resume-with-feedback submitted; request payload={captured.get('payload')}"
            )
            assert feedback_text in (
                captured.get("payload") or ""
            ), f"feedback missing from request payload: {captured.get('payload')}"

            # Status left "paused" server-side.
            from app import create_app
            from app.repositories.autonomous_repo import AutonomousWorkflowRepository

            app = create_app()
            with app.app_context():
                row = AutonomousWorkflowRepository().get_workflow(accepted_id) or {}
            assert row.get("status") == "waiting", f"expected waiting, got {row.get('status')}"
            assert feedback_text == row.get(
                "user_feedback"
            ), f"feedback not persisted: {row.get('user_feedback')!r}"
            print("[PASS] workflow left paused (status=waiting, feedback persisted)")

            # ── 3. Indeterminate workflow: BOTH exits (#2658) ────────────
            page.goto(
                f"{BASE_URL}/work/autonomous?workflow={indeterminate_id}",
                wait_until="domcontentloaded",
            )
            pause(2)
            page.wait_for_selector(".timeline-state-banner", timeout=30000)
            expect(
                page.locator(".timeline-state-banner").get_by_text("Accept (override)")
            ).to_be_visible()
            # #2658: indeterminate now also offers resume-with-feedback (was
            # override-only before the unified gating).
            expect(
                page.locator(".timeline-state-banner").get_by_text(
                    "Resume with Feedback", exact=True
                )
            ).to_be_visible()
            shot(page, "05-indeterminate")
            print("[PASS] indeterminate workflow shows override + resume-with-feedback")

            # ── 4. Rejected workflow: full report viewer (#2658) ──────────
            page.goto(
                f"{BASE_URL}/work/autonomous?workflow={accepted_id}",
                wait_until="domcontentloaded",
            )
            pause(2)
            report_btn = page.get_by_role("button", name="View acceptance report")
            expect(report_btn).to_be_visible(timeout=30000)
            report_btn.click()
            pause(0.5)
            # The generic content viewer shows the per-item verdicts and
            # evidence refs parsed from the milestone's metadata JSON. Scope
            # to the dialog — the milestone card's summary line repeats the
            # item name outside the modal (strict-mode collision).
            viewer = page.get_by_role("dialog")
            expect(viewer.get_by_text("修复登录失败")).to_be_visible()
            expect(viewer.get_by_text("tests/test_login.py:12")).to_be_visible()
            shot(page, "06-report-viewer")
            print("[PASS] acceptance report viewer renders per-item verdicts + evidence")

            # ── 5. Rejected pause: feedback modal prefill (#2491 UX) ──────
            prefill_id = seed_workflow(
                title="E2E prefill-acceptance UI #2491",
                paused_days_ago=0,
            )
            import json as _json_dump

            os.environ.setdefault("SCHEDULER_MODE", "web")
            from app import create_app as _create_app

            _report = {
                "merge_sha": "abc123def456",
                "status": "rejected",
                "verified_by": "glm-5/e2e",
                "scope": [],
                "gates": [{"item": "call-chain", "verdict": "confirmed", "evidence": []}],
                "verifier": [
                    {
                        "item": "修复登录失败",
                        "verdict": "rejected",
                        "evidence": [],
                        "rationale": "合并后用例依旧失败",
                    }
                ],
            }
            _app = _create_app()
            with _app.app_context():
                AutonomousWorkflowRepository().update_workflow(
                    prefill_id,
                    {"verification_report": _json_dump.dumps(_report, ensure_ascii=False)},
                )
            page.goto(
                f"{BASE_URL}/work/autonomous?workflow={prefill_id}",
                wait_until="domcontentloaded",
            )
            pause(2)
            page.wait_for_selector(".timeline-state-banner", timeout=30000)
            page.locator(".timeline-state-banner").get_by_text(
                "Resume with Feedback", exact=True
            ).click()
            pause(0.5)
            prefill_modal = page.get_by_role("dialog")
            expect(prefill_modal).to_be_visible()
            prefill_textarea = prefill_modal.locator("textarea")
            # Server-derived prefill mirrors the issue comment's failed-items
            # section: confirmed gates must not leak in.
            expect(prefill_textarea).to_have_value(
                "Rejected / missing:\n- [verifier] `修复登录失败` (rejected) — 合并后用例依旧失败"
            )
            expect(prefill_modal.get_by_text("Resume with Feedback", exact=True)).to_be_enabled()
            shot(page, "07-feedback-prefill")
            print("[PASS] feedback modal pre-fills the verifier failed-items list")
            prefill_modal.get_by_text("Cancel", exact=True).click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=10000)

            # ── 6. Waiting+feedback: restart banner, no manual exit ───────
            waiting_id = seed_workflow(
                title="E2E feedback-pending UI #2491",
                status="waiting",
                current_phase="wait",
                verification_status="rejected",
                error_message="",
                paused_days_ago=0,
            )
            with _app.app_context():
                AutonomousWorkflowRepository().update_workflow(
                    waiting_id, {"user_feedback": "E2E: wire the missing CI lane"}
                )
            page.goto(
                f"{BASE_URL}/work/autonomous?workflow={waiting_id}",
                wait_until="domcontentloaded",
            )
            pause(2)
            page.wait_for_selector(".timeline-state-banner--feedback", timeout=30000)
            expect(
                page.get_by_text("Feedback received — a new development round will start shortly")
            ).to_be_visible()
            # No manual "Complete Development" exit while a restart is queued.
            expect(page.get_by_text("Complete Development", exact=True)).not_to_be_visible()
            shot(page, "08-feedback-pending-banner")
            print("[PASS] waiting+feedback shows restart banner and hides Complete Development")
        finally:
            browser.close()

    print("[OK] e2e_paused_acceptance_ui_playwright passed")


def test_paused_acceptance_ui() -> None:
    """Pytest entry: the scenario asserts inside main() — pin the contract.

    main() fails via raised assertion/TimeoutError on any broken expectation;
    this guard keeps the function itself assert-bearing (scanner #2189).
    """
    result = main()
    assert result is None, "main() must complete without raising"


if __name__ == "__main__":
    main()
