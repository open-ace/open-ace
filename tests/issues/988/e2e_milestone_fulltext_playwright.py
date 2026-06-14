#!/usr/bin/env python3
"""
Open ACE — E2E Test: Milestone Full-Text View + Final Code Changes (Issue #988)

Verifies the milestone full-text feature layered on #984:
  1. Feature A — milestone cards with plan/review output show a "View Plan" /
     "View Review" button that opens a modal rendering the FULL markdown content
     (plan_created/refined/finalized → plan_content; plan_reviewed/pr_reviewed/
     pr_review_summary → review_content).
  2. Feature B — timeline header has three buttons: Final Plan, PR Review Summary,
     Final Code Changes.
       - Final Plan / PR Review Summary open the LATEST (highest dev_round, non-empty
         content) milestone's full text, with a "Round N" badge reflecting that
         milestone's dev_round (fallback to an earlier round if the newest round
         hasn't produced one).
       - Final Code Changes opens the cumulative PR diff modal (GET /pr-diff).
       - Buttons are disabled (greyed) when there is no content / no PR.
  3. Backend — GET /workflows/<id>/pr-diff returns pr_number from the workflow and
     degrades gracefully (empty diff) when gh has no PR.

Data is seeded directly via AutonomousWorkflowRepository (no real agent / gh runs).

Run:
  HEADLESS=true  python tests/issues/988/e2e_milestone_fulltext_playwright.py   # CI
  HEADLESS=false python tests/issues/988/e2e_milestone_fulltext_playwright.py   # Demo
"""

import os
import sys
import time
import uuid

# tests/issues/988/file.py → tests/issues/988 → tests/issues → tests → PROJECT_ROOT
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

import requests

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
_session = requests.Session()
_session.trust_env = False

# ── Config ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-milestone-fulltext-988")
TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = "admin123"

auth_token = None
created_workflow_ids = []


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


def api(method, path, token=None, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = {}
    t = token or auth_token
    if t:
        headers["Cookie"] = f"session_token={t}"
    if method == "get":
        return _session.get(url, headers=headers, **kwargs)
    if method == "post":
        return _session.post(url, headers=headers, **kwargs)
    if method == "delete":
        return _session.delete(url, headers=headers, **kwargs)


def get_repo():
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository

    return AutonomousWorkflowRepository()


def create_workflow_via_api(overrides=None):
    base = {
        "title": f"MilestoneFullText {uuid.uuid4().hex[:8]}",
        "requirements_text": "Build a hello world feature with tests",
        "cli_tool": "claude-code",
        "model": "",
        "workspace_type": "local",
        "project_path": "/tmp/e2e-milestone-fulltext",
        "branch_strategy": "new-branch",
        "max_plan_rounds": 1,
        "max_pr_review_rounds": 1,
    }
    if overrides:
        base.update(overrides)
    return api("post", "/api/autonomous/workflows", json=base)


def cleanup_workflows():
    for wf_id in created_workflow_ids:
        try:
            api("post", f"/api/autonomous/workflows/{wf_id}/stop")
        except Exception:
            pass
        try:
            api("delete", f"/api/autonomous/workflows/{wf_id}")
        except Exception:
            pass


def step_login():
    global auth_token
    log("LOGIN", f"Logging in as {TEST_USER}")
    r = _session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    auth_token = r.cookies.get("session_token")
    assert auth_token, "No session_token cookie"
    log("LOGIN", "✅ Login successful")


# ── Seeders ────────────────────────────────────────────
def _seed_milestone(
    repo,
    wf_id,
    milestone_type,
    phase,
    round_number=1,
    dev_round=1,
    status="completed",
    plan_content="",
    review_content="",
    result_summary="",
):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "workflow_id": wf_id,
        "milestone_id": str(uuid.uuid4()),
        "phase": phase,
        "dev_round": dev_round,
        "round_number": round_number,
        "milestone_type": milestone_type,
        "status": status,
        "title": f"{milestone_type} dev{dev_round} r{round_number}",
        "result_summary": result_summary or f"{milestone_type} summary",
        "started_at": now,
        "completed_at": now,
    }
    if plan_content:
        data["plan_content"] = plan_content
    if review_content:
        data["review_content"] = review_content
    repo.create_milestone(data)
    return data["milestone_id"]


def seed_multi_round_workflow(repo, title):
    """dev_round 1 + dev_round 2, each with plan_finalized / pr_reviewed /
    pr_review_summary carrying distinct markers. PR number 42."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    repo.update_workflow(
        wf_id,
        {"status": "completed", "current_phase": "done", "github_pr_number": 42},
    )

    # dev_round 1
    _seed_milestone(
        repo, wf_id, "plan_finalized", "planning", 1, 1, plan_content="## Plan R1\nROUND1-PLAN"
    )
    _seed_milestone(
        repo, wf_id, "pr_reviewed", "pr_review", 1, 1, review_content="round1 code review notes"
    )
    _seed_milestone(
        repo,
        wf_id,
        "pr_review_summary",
        "pr_review",
        1,
        1,
        review_content="## Review Summary R1\nROUND1-REVIEW-SUMMARY",
    )

    # dev_round 2 (newest — header buttons must surface THIS round)
    _seed_milestone(
        repo,
        wf_id,
        "plan_finalized",
        "planning",
        1,
        2,
        plan_content="## Plan R2\nROUND2-PLAN-MARKER",
    )
    _seed_milestone(
        repo, wf_id, "pr_reviewed", "pr_review", 1, 2, review_content="round2 code review notes"
    )
    _seed_milestone(
        repo,
        wf_id,
        "pr_review_summary",
        "pr_review",
        1,
        2,
        review_content="## Review Summary R2\nROUND2-REVIEW-SUMMARY-MARKER",
    )

    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


def seed_fallback_workflow(repo, title):
    """plan_finalized only in round 2, pr_review_summary only in round 1.
    Header Final Plan badge = Round 2, PR Review Summary badge = Round 1."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    repo.update_workflow(
        wf_id,
        {"status": "completed", "current_phase": "done", "github_pr_number": 43},
    )

    _seed_milestone(
        repo,
        wf_id,
        "pr_review_summary",
        "pr_review",
        1,
        1,
        review_content="## Summary R1\nROUND1-ONLY-SUMMARY",
    )
    _seed_milestone(
        repo, wf_id, "plan_finalized", "planning", 1, 2, plan_content="## Plan R2\nFALLBACK-PLAN"
    )

    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


def seed_empty_workflow(repo, title):
    """No plan_finalized / pr_review_summary / PR — all header buttons disabled."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    repo.update_workflow(wf_id, {"status": "completed", "current_phase": "done"})
    # Only an irrelevant milestone (no plan/review content, no PR).
    _seed_milestone(repo, wf_id, "repo_setup", "setup", 1, 1)

    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


# ── API assertions ─────────────────────────────────────
def step_test_pr_diff_route(multi_wf, empty_wf):
    log("API", "Verifying /pr-diff route")
    r = api("get", f"/api/autonomous/workflows/{multi_wf}/pr-diff")
    assert r.status_code == 200, f"pr-diff failed: {r.status_code} {r.text}"
    payload = r.json()
    assert payload.get("success") is True, payload
    assert payload.get("pr_number") == 42, f"expected pr_number=42, got {payload.get('pr_number')}"
    # diff may be empty in test env (no real gh PR) — just assert it's a string.
    assert isinstance(payload.get("diff"), str), payload
    log("API", "  ✅ multi-round /pr-diff → 200, pr_number=42")

    r2 = api("get", f"/api/autonomous/workflows/{empty_wf}/pr-diff")
    assert r2.status_code == 200, r2.text
    p2 = r2.json()
    assert p2.get("pr_number") is None, p2
    assert p2.get("diff") == "", p2
    log("API", "  ✅ empty workflow /pr-diff → 200, pr_number=null, diff empty")


# ── Browser assertions ─────────────────────────────────
def open_timeline_english(context, page, workflow_title, shot_name=None):
    """Auth + English + client-side nav to a seeded workflow's timeline."""
    context.add_cookies([{"name": "session_token", "value": auth_token, "url": BASE_URL}])
    page.goto(f"{BASE_URL}/work")
    page.wait_for_timeout(2000)
    page.evaluate(
        "() => { localStorage.setItem('language','en'); localStorage.setItem('i18nextLng','en'); }"
    )
    nav = page.locator(".work-nav-item:has(.bi-robot)")
    if nav.count() == 0:
        raise AssertionError("Autonomous nav item missing — feature flag not enabled")
    nav.first.click()
    page.wait_for_timeout(1500)
    if "/work/autonomous" not in page.url:
        raise AssertionError(f"Client-side nav failed; url={page.url}")
    item = page.locator(f".auto-workflow-item:has-text('{workflow_title}')")
    page.wait_for_timeout(1500)
    if item.count() == 0:
        raise AssertionError(f"Workflow '{workflow_title}' not found in list")
    item.first.click()
    page.wait_for_timeout(2500)
    log("BROWSER", f"  url={page.url}")
    if shot_name:
        shot(page, shot_name)


def _controls_btn(page, text):
    """A header control button matching exact-ish text, scoped to the controls bar."""
    return page.locator(".workflow-timeline-controls button").filter(has_text=text)


def _close_open_modal(page):
    """Close any open modal via its close button."""
    btn = page.locator(".modal.show .btn-close")
    if btn.count() > 0:
        btn.first.click()
        page.wait_for_timeout(400)


def step_browser_multi_round(context, page, wf_id):
    log("BROWSER", "Verifying multi-round header buttons + card buttons + modals")
    open_timeline_english(context, page, "Multi-Round FullText", "01-multi-round")

    # Header buttons present + enabled.
    final_plan = _controls_btn(page, "Final Plan")
    pr_summary = _controls_btn(page, "PR Review Summary")
    final_changes = _controls_btn(page, "Final Code Changes")
    assert final_plan.count() == 1, "Final Plan button missing"
    assert pr_summary.count() == 1, "PR Review Summary button missing"
    assert final_changes.count() == 1, "Final Code Changes button missing"
    assert not final_plan.first.is_disabled(), "Final Plan should be enabled"
    assert not pr_summary.first.is_disabled(), "PR Review Summary should be enabled"
    assert not final_changes.first.is_disabled(), "Final Code Changes should be enabled"
    log("BROWSER", "  ✅ 3 header buttons present + enabled")

    # Round badges reflect the LATEST dev_round (2).
    assert "Round 2" in final_plan.first.inner_text(), "Final Plan badge should show Round 2"
    assert "Round 2" in pr_summary.first.inner_text(), "PR Review Summary badge should show Round 2"
    log("BROWSER", "  ✅ header badges show Round 2 (latest dev_round)")

    # Final Plan modal → newest round's plan content.
    final_plan.first.click()
    page.wait_for_timeout(800)
    modal = page.locator(".modal.show")
    assert "ROUND2-PLAN-MARKER" in modal.inner_text(), "Final Plan modal should show round-2 plan"
    assert "ROUND1-PLAN" not in modal.inner_text(), "Final Plan modal should NOT show round-1 plan"
    log("BROWSER", "  ✅ Final Plan modal shows round-2 plan full text")
    _close_open_modal(page)

    # PR Review Summary modal → newest round's summary.
    pr_summary.first.click()
    page.wait_for_timeout(800)
    modal = page.locator(".modal.show")
    assert (
        "ROUND2-REVIEW-SUMMARY-MARKER" in modal.inner_text()
    ), "PR Review Summary modal should show round-2 summary"
    log("BROWSER", "  ✅ PR Review Summary modal shows round-2 summary full text")
    _close_open_modal(page)

    # Feature A — card buttons. A plan_finalized card has "View Plan".
    view_plan = page.locator(".workflow-timeline-inline-btn").filter(has_text="View Plan")
    assert view_plan.count() >= 1, "View Plan card button missing"
    view_plan.first.click()
    page.wait_for_timeout(800)
    modal = page.locator(".modal.show")
    assert "PLAN" in modal.inner_text(), "View Plan card modal should show plan content"
    log("BROWSER", "  ✅ card 'View Plan' opens plan full-text modal")
    _close_open_modal(page)

    # A pr_reviewed card has "View Review".
    view_review = page.locator(".workflow-timeline-inline-btn").filter(has_text="View Review")
    assert view_review.count() >= 1, "View Review card button missing"
    view_review.first.click()
    page.wait_for_timeout(800)
    modal = page.locator(".modal.show")
    assert (
        "review" in modal.inner_text().lower()
    ), "View Review card modal should show review content"
    log("BROWSER", "  ✅ card 'View Review' opens review full-text modal")
    _close_open_modal(page)


def step_browser_fallback(context, page, wf_id):
    log("BROWSER", "Verifying round-badge fallback semantics")
    open_timeline_english(context, page, "Fallback FullText", "02-fallback")

    final_plan = _controls_btn(page, "Final Plan")
    pr_summary = _controls_btn(page, "PR Review Summary")
    # plan_finalized is in round 2 → badge Round 2.
    assert "Round 2" in final_plan.first.inner_text(), "Final Plan badge should be Round 2"
    # pr_review_summary only in round 1 → fallback badge Round 1.
    assert (
        "Round 1" in pr_summary.first.inner_text()
    ), "PR Review Summary badge should fall back to Round 1"
    log("BROWSER", "  ✅ fallback: Final Plan=Round 2, PR Review Summary=Round 1")


def step_browser_disabled(context, page, wf_id):
    log("BROWSER", "Verifying all header buttons disabled on empty workflow")
    open_timeline_english(context, page, "Empty FullText", "03-empty")

    final_plan = _controls_btn(page, "Final Plan")
    pr_summary = _controls_btn(page, "PR Review Summary")
    final_changes = _controls_btn(page, "Final Code Changes")
    assert final_plan.first.is_disabled(), "Final Plan should be disabled (no plan_finalized)"
    assert pr_summary.first.is_disabled(), "PR Review Summary should be disabled"
    assert final_changes.first.is_disabled(), "Final Code Changes should be disabled (no PR)"
    log("BROWSER", "  ✅ all 3 header buttons disabled on empty workflow")


# ── Runner ─────────────────────────────────────────────
def run_tests():
    global auth_token
    ensure_dir()
    print("\n" + "=" * 60, flush=True)
    print("  Milestone Full-Text View + Final Code Changes (#988)", flush=True)
    print("=" * 60 + "\n", flush=True)

    passed = failed = skipped = 0

    try:
        step_login()
        passed += 1
    except Exception as e:
        print(f"  ❌ LOGIN FAILED: {e}", flush=True)
        failed += 1
        print("\nCannot continue without login — aborting.\n")
        cleanup_workflows()
        sys.exit(1)

    repo = None
    try:
        repo = get_repo()
    except Exception as e:
        print(f"  ⚠️  Cannot import AutonomousWorkflowRepository: {e}", flush=True)

    multi_wf = fallback_wf = empty_wf = None
    if repo is not None:
        for label, fn, arg in [
            ("multi", seed_multi_round_workflow, "Multi-Round FullText"),
            ("fallback", seed_fallback_workflow, "Fallback FullText"),
            ("empty", seed_empty_workflow, "Empty FullText"),
        ]:
            try:
                wid = fn(repo, arg)
                if label == "multi":
                    multi_wf = wid
                elif label == "fallback":
                    fallback_wf = wid
                else:
                    empty_wf = wid
                passed += 1
            except Exception as e:
                print(f"  ❌ SEED {label.upper()} FAILED: {e}", flush=True)
                failed += 1

    # API test
    if multi_wf and empty_wf:
        try:
            step_test_pr_diff_route(multi_wf, empty_wf)
            passed += 1
        except Exception as e:
            print(f"  ❌ PR-DIFF ROUTE FAILED: {e}", flush=True)
            failed += 1
    else:
        skipped += 1

    # Browser tests
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️  playwright not installed — skipping browser tests", flush=True)
        skipped += 3
        sync_playwright = None

    if sync_playwright:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(15000)

            browser_tests = [
                ("Multi-Round Header+Cards", multi_wf, step_browser_multi_round),
                ("Round-Badge Fallback", fallback_wf, step_browser_fallback),
                ("Disabled on Empty", empty_wf, step_browser_disabled),
            ]
            for name, wf, fn in browser_tests:
                if not wf:
                    skipped += 1
                    continue
                try:
                    fn(context, page, wf)
                    passed += 1
                except Exception as e:
                    print(f"  ❌ {name.upper()} FAILED: {e}", flush=True)
                    failed += 1
                    try:
                        shot(page, f"error-{name.lower().replace(' ', '-')}")
                    except Exception:
                        pass

            browser.close()

    # Cleanup
    log("CLEANUP", "Removing seeded workflows")
    cleanup_workflows()

    print("\n" + "=" * 60, flush=True)
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped", flush=True)
    print("=" * 60 + "\n", flush=True)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
