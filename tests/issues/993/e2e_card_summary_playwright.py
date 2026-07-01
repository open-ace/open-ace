#!/usr/bin/env python3
"""
Open ACE — E2E Test: Timeline Milestone Card One-Line Summary (Issue #993)

Verifies the per-round summary line on milestone cards:
  1. A milestone with a `tldr` shows the TL;DR text on the card.
  2. A milestone with only `result_summary` (no tldr) falls back to it.
  3. A milestone with neither shows no summary line (no placeholder).

Data is seeded directly via AutonomousWorkflowRepository (no real agent / gh runs).

Run:
  HEADLESS=true  python tests/issues/993/e2e_card_summary_playwright.py   # CI
  HEADLESS=false python tests/issues/993/e2e_card_summary_playwright.py   # Demo
"""

import os
import sys
import time
import uuid

# tests/issues/993/file.py → tests/issues/993 → tests/issues → tests → PROJECT_ROOT
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

import requests

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
_session = requests.Session()
_session.trust_env = False

# ── Config ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-card-summary-993")
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
        "title": f"CardSummary {uuid.uuid4().hex[:8]}",
        "requirements_text": "Build a hello world feature with tests",
        "cli_tool": "claude-code",
        "model": "",
        "workspace_type": "local",
        "project_path": "/tmp/e2e-card-summary",
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


# ── Seeders ─────────────────────────────────────────────
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
    tldr="",
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
        "result_summary": result_summary,
        "tldr": tldr,
        "started_at": now,
        "completed_at": now,
    }
    if plan_content:
        data["plan_content"] = plan_content
    if review_content:
        data["review_content"] = review_content
    repo.create_milestone(data)
    return data["milestone_id"]


def seed_tldr_workflow(repo, title):
    """plan_finalized with a tldr — card must show the TL;DR text."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)
    repo.update_workflow(wf_id, {"status": "completed", "current_phase": "done"})
    _seed_milestone(
        repo,
        wf_id,
        "plan_finalized",
        "planning",
        1,
        1,
        plan_content="## Plan\nFull plan body here.",
        result_summary="方案：实现登录模块",  # present but tldr wins
        tldr="TLDR-LOGIN-MARKER 实现了用户登录与登出",
    )
    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


def seed_summary_only_workflow(repo, title):
    """plan_finalized with result_summary but empty tldr — card falls back."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)
    repo.update_workflow(wf_id, {"status": "completed", "current_phase": "done"})
    _seed_milestone(
        repo,
        wf_id,
        "plan_finalized",
        "planning",
        1,
        1,
        plan_content="## Plan\nBody.",
        result_summary="SUMMARY-ONLY-MARKER 方案概述：分三步实现",
        tldr="",  # no tldr → card uses result_summary
    )
    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


def seed_empty_workflow(repo, title):
    """repo_setup milestone with no tldr / result_summary — no summary line."""
    r = create_workflow_via_api({"title": title})
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)
    repo.update_workflow(wf_id, {"status": "completed", "current_phase": "done"})
    _seed_milestone(
        repo,
        wf_id,
        "repo_setup",
        "setup",
        1,
        1,
        result_summary="",
        tldr="",
    )
    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


# ── Browser assertions ──────────────────────────────────
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


def step_browser_tldr(context, page, wf_id):
    log("BROWSER", "Verifying card shows TL;DR text")
    open_timeline_english(context, page, "TLDR Card 993", "01-tldr")
    summary = page.locator(".workflow-timeline-card-summary")
    assert summary.count() >= 1, "summary line missing on tldr milestone card"
    assert (
        "TLDR-LOGIN-MARKER" in summary.first.inner_text()
    ), "card should show the tldr text, not result_summary"
    log("BROWSER", "  ✅ card shows TL;DR text (tldr preferred over result_summary)")


def step_browser_summary_fallback(context, page, wf_id):
    log("BROWSER", "Verifying card falls back to result_summary when tldr empty")
    open_timeline_english(context, page, "SummaryOnly Card 993", "02-summary-only")
    summary = page.locator(".workflow-timeline-card-summary")
    assert summary.count() >= 1, "summary line missing on summary-only card"
    assert (
        "SUMMARY-ONLY-MARKER" in summary.first.inner_text()
    ), "card should fall back to result_summary when tldr is empty"
    log("BROWSER", "  ✅ card falls back to result_summary (tldr empty)")


def step_browser_empty(context, page, wf_id):
    log("BROWSER", "Verifying no summary line when both tldr and result_summary empty")
    open_timeline_english(context, page, "Empty Card 993", "03-empty")
    summary = page.locator(".workflow-timeline-card-summary")
    assert (
        summary.count() == 0
    ), "no summary line should render when tldr and result_summary are both empty"
    log("BROWSER", "  ✅ no summary line on empty milestone (no placeholder)")


# ── Runner ──────────────────────────────────────────────
def run_tests():
    global auth_token
    ensure_dir()
    print("\n" + "=" * 60, flush=True)
    print("  Milestone Card One-Line Summary (#993)", flush=True)
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

    tldr_wf = summary_wf = empty_wf = None
    if repo is not None:
        for label, fn, arg in [
            ("tldr", seed_tldr_workflow, "TLDR Card 993"),
            ("summary", seed_summary_only_workflow, "SummaryOnly Card 993"),
            ("empty", seed_empty_workflow, "Empty Card 993"),
        ]:
            try:
                wid = fn(repo, arg)
                if label == "tldr":
                    tldr_wf = wid
                elif label == "summary":
                    summary_wf = wid
                else:
                    empty_wf = wid
                passed += 1
            except Exception as e:
                print(f"  ❌ SEED {label.upper()} FAILED: {e}", flush=True)
                failed += 1

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
                ("TL;DR on card", tldr_wf, step_browser_tldr),
                ("Summary fallback", summary_wf, step_browser_summary_fallback),
                ("Empty → no line", empty_wf, step_browser_empty),
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
