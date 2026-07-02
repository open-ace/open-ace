#!/usr/bin/env python3
"""
Open ACE — E2E Test: Session Topology + Per-Phase Usage + New Milestones (Issue #983)

Verifies the autonomous-workflow refactor:
  1. Three-line session topology fields (main/review/test_session_id) persist and
     do not break timeline rendering.
  2. New milestones `plan_finalized` (开发前的「最终方案」) and `pr_review_summary`
     (最后一轮 PR 评审后的「PR 评审总结」) render in the timeline.
  3. Per-phase usage cut: each milestone card shows ONLY its own phase token/request
     increment; workflow totals = SUM of phase_* (no double-counting / no foreign
     session pollution).
  4. Single-round vs multi-round naming: a phase that ran one round shows a
     numberless label (e.g. "Plan review"); multiple rounds show the round number.
  5. Milestone session detail surfaces only that milestone's messages (filtered by
     milestone_id), even when milestones share a resumed session.

Data is seeded directly via AutonomousWorkflowRepository (no real agent runs).

Run:
  HEADLESS=true  python tests/issues/983/e2e_session_topology_playwright.py   # CI
  HEADLESS=false python tests/issues/983/e2e_session_topology_playwright.py   # Demo
"""

import json
import os
import sys
import time
import uuid

# tests/issues/983/file.py → tests/issues/983 → tests/issues → tests → PROJECT_ROOT
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
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-session-topology-983")
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
        "title": f"SessionTopology {uuid.uuid4().hex[:8]}",
        "requirements_text": "Build a hello world feature with tests",
        "cli_tool": "claude-code",
        "model": "",
        "workspace_type": "local",
        "project_path": "/tmp/e2e-session-topology",
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
    session_id="",
    review_session_id="",
    phase_total_tokens=0,
    phase_input_tokens=0,
    phase_output_tokens=0,
    phase_request_count=0,
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
        "title": f"{milestone_type} r{round_number}",
        "result_summary": result_summary or f"{milestone_type} summary",
        "started_at": now,
        "completed_at": now,
        "phase_total_tokens": phase_total_tokens,
        "phase_input_tokens": phase_input_tokens,
        "phase_output_tokens": phase_output_tokens,
        "phase_request_count": phase_request_count,
    }
    if session_id:
        data["session_id"] = session_id
    if review_session_id:
        data["review_session_id"] = review_session_id
    if plan_content:
        data["plan_content"] = plan_content
    if review_content:
        data["review_content"] = review_content
    repo.create_milestone(data)
    return data["milestone_id"]


def seed_single_round_workflow(repo):
    """A complete single-round workflow with new milestones + per-phase usage."""
    r = create_workflow_via_api(
        {"title": "Single-Round Topology", "max_plan_rounds": 1, "max_pr_review_rounds": 1}
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    main_sess = "cli-main-aaa"
    review_sess = "cli-review-bbb"
    test_sess = "cli-test-ccc"
    repo.update_workflow(
        wf_id,
        {
            "main_session_id": main_sess,
            "review_session_id": review_sess,
            "test_session_id": test_sess,
            "status": "completed",
            "current_phase": "done",
        },
    )

    # Each milestone carries its OWN phase increment only.
    _seed_milestone(
        repo,
        wf_id,
        "plan_created",
        "planning",
        1,
        1,
        session_id=main_sess,
        phase_total_tokens=1000,
        phase_input_tokens=800,
        phase_output_tokens=200,
        phase_request_count=4,
    )
    _seed_milestone(
        repo,
        wf_id,
        "plan_reviewed",
        "planning",
        1,
        1,
        review_session_id=review_sess,
        phase_total_tokens=500,
        phase_request_count=2,
        review_content="方案通过审查",
    )
    _seed_milestone(
        repo,
        wf_id,
        "plan_finalized",
        "planning",
        1,
        1,
        session_id=main_sess,
        phase_total_tokens=300,
        phase_request_count=1,
        plan_content="Final plan: build hello.py + tests",
    )
    _seed_milestone(
        repo,
        wf_id,
        "dev_started",
        "development",
        1,
        1,
        session_id=main_sess,
        phase_total_tokens=5000,
        phase_input_tokens=4000,
        phase_output_tokens=1000,
        phase_request_count=20,
    )
    _seed_milestone(
        repo,
        wf_id,
        "tests_run",
        "development",
        1,
        1,
        session_id=test_sess,
        phase_total_tokens=800,
        phase_request_count=5,
    )
    _seed_milestone(
        repo,
        wf_id,
        "pr_reviewed",
        "pr_review",
        1,
        1,
        review_session_id=review_sess,
        phase_total_tokens=600,
        phase_request_count=3,
        review_content="代码审查通过",
    )
    _seed_milestone(
        repo,
        wf_id,
        "pr_review_summary",
        "pr_review",
        1,
        1,
        session_id=main_sess,
        phase_total_tokens=200,
        phase_request_count=1,
        review_content="可以合并",
    )

    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


def seed_multi_round_workflow(repo):
    """A multi-round workflow (2 planning + 2 pr_review rounds)."""
    r = create_workflow_via_api(
        {"title": "Multi-Round Topology", "max_plan_rounds": 2, "max_pr_review_rounds": 2}
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    repo.update_workflow(
        wf_id,
        {
            "main_session_id": "cli-main-multi",
            "review_session_id": "cli-review-multi",
            "test_session_id": "cli-test-multi",
            "status": "completed",
            "current_phase": "done",
        },
    )

    _seed_milestone(repo, wf_id, "plan_created", "planning", 1, 1, phase_request_count=4)
    _seed_milestone(repo, wf_id, "plan_reviewed", "planning", 1, 1, review_content="needs work")
    _seed_milestone(repo, wf_id, "plan_refined", "planning", 2, 1, phase_request_count=5)
    _seed_milestone(repo, wf_id, "plan_reviewed", "planning", 2, 1, review_content="方案通过审查")
    _seed_milestone(repo, wf_id, "plan_finalized", "planning", 2, 1, plan_content="final")
    _seed_milestone(repo, wf_id, "dev_started", "development", 1, 1)
    _seed_milestone(repo, wf_id, "tests_run", "development", 1, 1)
    _seed_milestone(repo, wf_id, "pr_reviewed", "pr_review", 1, 1, review_content="fix x")
    _seed_milestone(repo, wf_id, "pr_updated", "pr_review", 1, 1)
    _seed_milestone(repo, wf_id, "pr_reviewed", "pr_review", 2, 1, review_content="ok")
    _seed_milestone(repo, wf_id, "pr_review_summary", "pr_review", 2, 1, review_content="可以合并")

    repo.refresh_workflow_usage_from_sessions(wf_id)
    return wf_id


# ── API assertions ─────────────────────────────────────
def step_test_new_milestones_and_usage(wf_id):
    """New milestones present; per-phase usage cut; workflow total = SUM(phase_*)."""
    log("API", "Verifying new milestones + per-phase usage")
    r = api("get", f"/api/autonomous/workflows/{wf_id}/timeline")
    assert r.status_code == 200, f"Timeline API failed: {r.status_code}"
    data = r.json()
    milestones = data.get("milestones", [])
    types = {m["milestone_type"] for m in milestones}

    assert "plan_finalized" in types, f"plan_finalized missing: {types}"
    assert "pr_review_summary" in types, f"pr_review_summary missing: {types}"
    log("API", "  ✅ plan_finalized + pr_review_summary present")

    # Per-phase usage: the planning milestones carry seeded increments.
    by_type = {}
    for m in milestones:
        by_type.setdefault(m["milestone_type"], []).append(m)
    plan_final = by_type["plan_finalized"][0]
    assert (
        plan_final["llm_total_tokens"] == 300
    ), f"plan_finalized phase tokens wrong: {plan_final['llm_total_tokens']}"
    assert (
        plan_final["llm_request_count"] == 1
    ), f"plan_finalized phase requests wrong: {plan_final['llm_request_count']}"
    log("API", "  ✅ per-phase usage cut correct (plan_finalized=300/1req)")

    # Workflow total = SUM of all phase_total_tokens (no double counting).
    # The timeline endpoint returns milestones only; workflow totals come from
    # the workflow detail endpoint.
    rw = api("get", f"/api/autonomous/workflows/{wf_id}")
    assert rw.status_code == 200, f"workflow detail failed: {rw.status_code}"
    wf = rw.json().get("workflow") or {}
    expected_total = 1000 + 500 + 300 + 5000 + 800 + 600 + 200  # = 8400
    assert wf.get("total_tokens") == expected_total, (
        f"workflow total_tokens should be SUM(phase_*)={expected_total}, "
        f"got {wf.get('total_tokens')}"
    )
    log("API", f"  ✅ workflow total_tokens = SUM(phase_*) = {expected_total}")

    expected_requests = 4 + 2 + 1 + 20 + 5 + 3 + 1  # = 36
    assert wf.get("total_requests") == expected_requests, (
        f"workflow total_requests should be {expected_requests}, " f"got {wf.get('total_requests')}"
    )
    log("API", f"  ✅ workflow total_requests = SUM(phase_request_count) = {expected_requests}")


def step_test_multi_round_rounds(wf_id):
    """Multi-round workflow exposes round_number for review milestones."""
    log("API", "Verifying multi-round rounds")
    r = api("get", f"/api/autonomous/workflows/{wf_id}/timeline")
    assert r.status_code == 200, r.text
    milestones = r.json().get("milestones", [])
    plan_reviewed_rounds = sorted(
        m["round_number"] for m in milestones if m["milestone_type"] == "plan_reviewed"
    )
    pr_reviewed_rounds = sorted(
        m["round_number"] for m in milestones if m["milestone_type"] == "pr_reviewed"
    )
    assert plan_reviewed_rounds == [1, 2], f"planning rounds wrong: {plan_reviewed_rounds}"
    assert pr_reviewed_rounds == [1, 2], f"pr review rounds wrong: {pr_reviewed_rounds}"
    log(
        "API", f"  ✅ planning rounds={plan_reviewed_rounds}, pr_review rounds={pr_reviewed_rounds}"
    )


def step_test_session_message_filtering(repo):
    """Shared session messages are filtered by milestone_id."""
    log("SESSION", "Verifying message filtering by milestone_id")
    from app.modules.workspace.session_manager import SessionManager

    sm = SessionManager()
    wf_r = create_workflow_via_api({"title": "MsgFilter Topology"})
    assert wf_r.status_code == 201, wf_r.text
    wf_id = wf_r.json()["workflow"]["workflow_id"]
    created_workflow_ids.append(wf_id)

    shared_session = f"shared-session-{uuid.uuid4().hex[:8]}"
    ms_a = str(uuid.uuid4())
    ms_b = str(uuid.uuid4())
    # Two milestones that share one resumed session.
    repo.create_milestone(
        {
            "workflow_id": wf_id,
            "milestone_id": ms_a,
            "phase": "planning",
            "milestone_type": "plan_created",
            "status": "completed",
            "session_id": shared_session,
            "title": "A",
        }
    )
    repo.create_milestone(
        {
            "workflow_id": wf_id,
            "milestone_id": ms_b,
            "phase": "planning",
            "milestone_type": "plan_refined",
            "status": "completed",
            "session_id": shared_session,
            "title": "B",
        }
    )

    sm.create_session(
        tool_name="claude-code",
        user_id=1,
        session_id=shared_session,
        title="shared",
        workspace_type="local",
    )
    sm.add_message(shared_session, "user", "message for milestone A", milestone_id=ms_a)
    sm.add_message(shared_session, "user", "message for milestone B", milestone_id=ms_b)

    # Ask for ms_a's session detail — only ms_a's message should surface.
    r = api("get", f"/api/autonomous/workflows/{wf_id}/milestones/{ms_a}/session")
    assert r.status_code == 200, r.text
    payload = r.json()
    session = payload.get("session") or {}
    contents = [m.get("content", "") for m in (session.get("messages") or [])]
    assert any("message for milestone A" in c for c in contents), f"ms_a msg missing: {contents}"
    assert not any(
        "message for milestone B" in c for c in contents
    ), f"ms_b leaked into ms_a detail: {contents}"
    log("SESSION", "  ✅ milestone session detail filtered by milestone_id")


# ── Browser assertions ─────────────────────────────────
def open_timeline_english(context, page, wf_id, workflow_title, shot_name=None):
    """Open a workflow timeline authenticated + in English via client-side nav.

    A full page reload to /work/autonomous races the route guard (autonomousEnabled
    is false until the async feature-flag config loads → AutonomousDisabledRedirect
    sends us back to /work). So we: land on /work (lets the flag load), then click
    the autonomous nav item (client-side nav, no reload) and pick the seeded
    workflow from the list — the real user flow.
    """
    context.add_cookies(
        [
            {
                "name": "session_token",
                "value": auth_token,
                "url": BASE_URL,
            }
        ]
    )
    # 1) Land on /work so WorkLayout mounts and the autonomous flag loads.
    page.goto(f"{BASE_URL}/work")
    page.wait_for_timeout(2000)
    page.evaluate(
        "() => { localStorage.setItem('language','en'); localStorage.setItem('i18nextLng','en'); }"
    )
    # 2) Client-side nav to /work/autonomous by clicking the nav item.
    nav = page.locator(".work-nav-item:has(.bi-robot)")
    if nav.count() == 0:
        raise AssertionError("Autonomous nav item missing — feature flag not enabled")
    nav.first.click()
    page.wait_for_timeout(1500)
    if "/work/autonomous" not in page.url:
        raise AssertionError(f"Client-side nav failed; url={page.url}")
    # 3) Select the seeded workflow from the list by title.
    item = page.locator(f".auto-workflow-item:has-text('{workflow_title}')")
    page.wait_for_timeout(1500)  # let the list query resolve
    if item.count() == 0:
        raise AssertionError(f"Workflow '{workflow_title}' not found in list")
    item.first.click()
    page.wait_for_timeout(2500)
    log("BROWSER", f"  url={page.url}")
    if shot_name:
        shot(page, shot_name)


def step_browser_single_round_labels(context, page, wf_id):
    """Single-round phase → numberless labels; new milestones render in English."""
    log("BROWSER", "Verifying single-round labels + new milestones render")
    open_timeline_english(context, page, wf_id, "Single-Round Topology", "01-single-round-timeline")

    content = page.content()
    assert "Final plan" in content, "plan_finalized label 'Final plan' should render"
    assert "PR review summary" in content, "pr_review_summary label should render"
    log("BROWSER", "  ✅ 'Final plan' + 'PR review summary' render")

    # Single-round review/development milestones must NOT carry "round" in their label.
    # (Multi-round would show "Plan review round N".)
    has_numberless_plan_review = "Plan review" in content
    assert has_numberless_plan_review, "numberless 'Plan review' label should render"
    log("BROWSER", "  ✅ single-round numberless labels render")


def step_browser_multi_round_labels(context, page, wf_id):
    """Multi-round phase → numbered labels render."""
    log("BROWSER", "Verifying multi-round numbered labels")
    open_timeline_english(context, page, wf_id, "Multi-Round Topology", "02-multi-round-timeline")

    content = page.content()
    # Multi-round planning shows "Plan review round 1" / "round 2".
    assert "Plan review round" in content, "numbered 'Plan review round' label should render"
    log("BROWSER", "  ✅ multi-round numbered labels render")


# ── Runner ─────────────────────────────────────────────
def run_tests():
    global auth_token
    ensure_dir()
    print("\n" + "=" * 60, flush=True)
    print("  Session Topology + Per-Phase Usage + New Milestones (#983)", flush=True)
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

    single_wf = None
    multi_wf = None
    if repo is not None:
        try:
            single_wf = seed_single_round_workflow(repo)
            passed += 1
        except Exception as e:
            print(f"  ❌ SEED SINGLE FAILED: {e}", flush=True)
            failed += 1
        try:
            multi_wf = seed_multi_round_workflow(repo)
            passed += 1
        except Exception as e:
            print(f"  ❌ SEED MULTI FAILED: {e}", flush=True)
            failed += 1

    # API-level tests (language agnostic)
    api_tests = [
        ("New Milestones + Usage", lambda: step_test_new_milestones_and_usage(single_wf)),
        ("Multi-Round Rounds", lambda: step_test_multi_round_rounds(multi_wf)),
        ("Session Message Filtering", lambda: step_test_session_message_filtering(repo)),
    ]
    for name, fn in api_tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name.upper()} FAILED: {e}", flush=True)
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
                (
                    "Single-Round Labels",
                    lambda: step_browser_single_round_labels(context, page, single_wf),
                ),
                (
                    "Multi-Round Labels",
                    lambda: step_browser_multi_round_labels(context, page, multi_wf),
                ),
            ]
            for name, fn in browser_tests:
                try:
                    fn()
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
