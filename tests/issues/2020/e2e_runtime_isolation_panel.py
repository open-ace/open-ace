#!/usr/bin/env python3
"""
Open ACE — #2020 Phase B Runtime & Isolation panel E2E.

Verifies the workflow detail page surfaces the effective resource/isolation
policy snapshot (provider, declared capabilities, limits, enforced flags) when a
workflow's ``sandbox_effective_policy`` column is populated.

The column is normally written by the orchestrator at sandbox-create time (i.e.
once the agent actually runs). To keep this E2E independent of a full agent run
(which needs provider API keys), it creates a workflow via the API then seeds
the snapshot directly on the row, then asserts the panel renders honestly —
Legacy's storage/inode show "not enforced" because Legacy does not declare
STORAGE_INODE_QUOTA.

Run:
  HEADLESS=true  python tests/issues/2020/e2e_runtime_isolation_panel.py   # CI
  HEADLESS=false python tests/issues/2020/e2e_runtime_isolation_panel.py   # Demo

Requires a backend with the #2020 Phase B migration applied (sandbox_effective_policy
column) and the built FE served (the worktree backend cannot start without
config.json; CI deploys both — see docs memory e2e-infra-gotchas #6/#7).
"""

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import expect, sync_playwright

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
_session = requests.Session()
_session.trust_env = False

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = "admin123"

auth_token = None
created_workflow_id = None

# The snapshot we seed — a Legacy run with memory/pids/cpu/wall_clock enforced
# but storage/inode NOT (Legacy honestly declares no STORAGE_INODE_QUOTA).
SEEDED_SNAPSHOT = {
    "schema_version": 1,
    "provider": "legacy_posix",
    "capabilities": [
        "private_home_tmp_xdg",
        "filesystem_acl",
        "cpu_mem_pids_time_quota",
        "credential_token_binding",
    ],
    "limits": {
        "memory_max_bytes": 2147483648,
        "pids_max": 512,
        "cpu_max": "200000/100000",
        "wall_clock_limit": 3600,
        "ephemeral_storage_limit": 0,
        "inode_limit": 0,
    },
    "cgroup_enabled": "auto",
    "task_root": "/run/openace-agent-tasks",
    "enforced": {
        "memory": True,
        "pids": True,
        "cpu": True,
        "wall_clock": True,
        "ephemeral_storage": False,
        "inode": False,
    },
}


def log(tag, msg):
    print(f"  [{tag}] {msg}", flush=True)


def api(method, path, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = {}
    if auth_token:
        headers["Cookie"] = f"session_token={auth_token}"
    return getattr(_session, method)(url, headers=headers, **kwargs)


def seed_effective_policy(workflow_id: str) -> None:
    """Write the snapshot directly on the workflow row (bypasses needing an agent run)."""
    from app.repositories.database import Database
    from scripts.shared.config import get_database_url

    db = Database(db_url=get_database_url())
    db.execute(
        "UPDATE autonomous_workflows SET sandbox_effective_policy = ? WHERE workflow_id = ?",
        (json.dumps(SEEDED_SNAPSHOT), workflow_id),
    )
    log("SEED", f"seeded sandbox_effective_policy on {workflow_id[:8]}")


def step_login():
    global auth_token
    log("LOGIN", f"login as {TEST_USER}")
    r = _session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    auth_token = r.cookies.get("session_token")
    assert auth_token, "No session_token cookie"


def step_create_workflow():
    global created_workflow_id
    log("CREATE", "create workflow via API")
    r = api(
        "post",
        "/api/autonomous/workflows",
        json={
            "title": "#2020 Phase B panel E2E",
            "requirements_text": "Seed effective policy and verify the panel renders",
            "cli_tool": "claude-code",
            "model": "",
            "workspace_type": "local",
            "project_path": "/tmp/e2e-2020-phase-b-project",
            "branch_strategy": "new-branch",
            "max_plan_rounds": 1,
            "max_pr_review_rounds": 1,
        },
    )
    assert r.status_code == 201, f"create failed: {r.status_code} {r.text}"
    created_workflow_id = r.json()["workflow"]["workflow_id"]
    seed_effective_policy(created_workflow_id)


def step_verify_panel(page):
    log("UI", "open autonomous page + select workflow")
    page.goto(f"{BASE_URL}/autonomous-dev")
    page.context.add_cookies([{"name": "session_token", "value": auth_token, "url": BASE_URL}])
    page.reload()
    page.wait_for_selector(f"text={created_workflow_id[:8]}", timeout=15000)
    page.click(f"text={created_workflow_id[:8]}")
    panel = page.wait_for_selector("[data-testid='runtime-isolation-panel']", timeout=15000)
    expect(panel).to_be_visible()
    # Provider badge + an enforced limit value render.
    expect(
        page.locator("[data-testid='runtime-isolation-panel']").locator("text=legacy_posix")
    ).to_be_visible()
    log("UI", "✅ Runtime & Isolation panel rendered with provider")
    # Honest enforcement: storage shows "not enforced" for Legacy.
    body = page.locator("[data-testid='runtime-isolation-panel']").inner_text()
    assert "not enforced" in body, "Legacy storage/inode should show 'not enforced'"
    log("UI", "✅ honest 'not enforced' badge present for Legacy storage/inode")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        try:
            step_login()
            step_create_workflow()
            step_verify_panel(page)
            log("DONE", "✅ E2E passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
