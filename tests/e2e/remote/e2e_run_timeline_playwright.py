#!/usr/bin/env python3
"""
Open ACE - Run Timeline Playwright E2E Test

验证持久化「运行时间线」端到端可见：当 ``run_timeline.enabled=true`` 时，一次
远程会话的生命周期（创建 → 用户消息 → AI 输出/工具调用 → 用量上报）会落库为
agent_runs / agent_run_events / agent_approvals，并在会话详情页的 RunTimeline
组件中渲染出来。

复用 e2e_remote_workspace_playwright 的搭建（登录、注册机器、建会话、模拟 AI
输出与用量上报），然后：
  - 通过 events API 断言事件已持久化（run + events）
  - 在会话详情页断言 [data-testid="run-timeline"] 渲染了事件行

前置条件（与其它 remote E2E 一致）：
  - 运行中的服务 BASE_URL（默认 http://localhost:5001），且 config 中
    ``run_timeline.enabled=true``（特性默认关闭，需显式开启）
  - 存在测试用户 test_user / admin123，以及 admin / admin123
  - 已安装 playwright chromium

Run:
  HEADLESS=true  python tests/e2e/remote/e2e_run_timeline_playwright.py
  HEADLESS=false python tests/e2e/remote/e2e_run_timeline_playwright.py
"""

import os
import sys
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
TEST_USER = os.environ.get("TEST_REAL_USER", "test_user")
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-run-timeline")

machine_id = None
session_id = None


def shot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def pause(seconds):
    time.sleep(seconds if not HEADLESS else 0.3)


def api_login_as(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    return r.cookies.get("session_token")


def api_register_machine(admin_token):
    global machine_id
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": admin_token},
    )
    assert r.status_code == 200
    reg_token = r.json()["registration_token"]
    machine_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/register",
        json={
            "registration_token": reg_token,
            "machine_id": machine_id,
            "machine_name": "Timeline E2E Server",
            "hostname": "timeline-e2e.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04 LTS",
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
            "agent_version": "1.0.0-e2e",
        },
    )
    assert r.status_code == 200
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 8, "memory_gb": 32, "cli_installed": True},
        },
    )
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": 89, "permission": "admin"},
        cookies={"session_token": admin_token},
    )
    assert r.status_code == 200


def agent_message(payload):
    r = requests.post(f"{BASE_URL}/api/remote/agent/message", json=payload)
    return r.status_code == 200


def api_cleanup(token):
    global session_id, machine_id
    if session_id:
        requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop", cookies={"session_token": token}
        )
        session_id = None
    if machine_id:
        requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}", cookies={"session_token": token}
        )
        machine_id = None


def run_tests():
    global session_id
    token = api_login_as()
    admin_token = api_login_as("admin", "admin123")

    # 前置：特性必须开启，否则 events API 返回 {disabled:true}、UI 自隐藏。
    probe = requests.get(
        f"{BASE_URL}/api/remote/sessions/probe/events",
        cookies={"session_token": token},
    ).json()
    if probe.get("disabled"):
        print("\n⚠ run_timeline.enabled=false —— 时间线特性未开启，跳过 E2E。")
        print("  请在 config 中设置 run_timeline.enabled=true 并重启服务后再运行。")
        return

    api_register_machine(admin_token)
    print(f"  ✓ 机器已注册: {machine_id[:8]}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=0 if HEADLESS else 100)
        page = browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN"
        ).new_page()
        page.set_default_timeout(15000)

        # 浏览器登录（注入 cookie）
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.context.add_cookies(
            [{"name": "session_token", "value": token, "domain": "localhost", "path": "/"}]
        )

        # 浏览器内创建会话
        create = page.evaluate(
            """async (machine_id) => {
                const r = await fetch('/api/remote/sessions', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({machine_id, project_path: '/home/u/proj',
                                          cli_tool: 'qwen-code-cli', model: 'qwen3-coder-plus',
                                          title: 'Timeline E2E'})
                });
                return {ok: r.ok, data: await r.json()};
            }""",
            machine_id,
        )
        assert create["ok"], create
        session_id = create["data"]["session"]["session_id"]
        print(f"  ✓ 会话已创建: {session_id[:8]}...")

        # 用户消息
        page.evaluate(
            """async ([sid, content]) => {
                await fetch('/api/remote/sessions/' + sid + '/chat', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    credentials: 'include', body: JSON.stringify({content})
                });
            }""",
            [session_id, "请审查 main.py"],
        )

        # 模拟 AI 输出（assistant + tool_use）+ 用量上报 → 落库时间线事件
        agent_message(
            {
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": '{"type":"assistant","content":"发现 2 个问题并开始修复。"}',
                "stream": "stdout",
                "is_complete": False,
            }
        )
        agent_message(
            {
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": '{"type":"tool_use","tool":"read_file","input":{"path":"main.py"}}',
                "stream": "stdout",
                "is_complete": True,
            }
        )
        agent_message(
            {
                "type": "usage_report",
                "machine_id": machine_id,
                "session_id": session_id,
                "tokens": {"input": 1500, "output": 800},
                "requests": 2,
            }
        )
        pause(1)

        # 断言 1：events API 已持久化 run + 事件
        events = page.evaluate(
            """async (sid) => {
                const r = await fetch('/api/remote/sessions/' + sid + '/events',
                                      {credentials: 'include'});
                return await r.json();
            }""",
            session_id,
        )
        assert events.get("success") is True, events
        assert events["run"] is not None, "run 记录未落库"
        types = [e["event_type"] for e in events["events"]]
        print(f"  ✓ events API: run.status={events['run']['status']}, events={types}")
        assert "session_created" in types and "user_message" in types
        assert "assistant_output" in types and "tool_use" in types
        assert "usage_reported" in types
        assert events["run"]["total_tokens"] >= 2300

        # 断言 2：会话详情页渲染 RunTimeline
        page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
        page.wait_for_selector("main, .session, table, .list-group-item, tr", timeout=10000)
        # 点击我们的会话进入详情
        clicked = False
        items = page.locator(".session-item, .session-card, .list-group-item, tr")
        for i in range(items.count()):
            txt = items.nth(i).text_content() or ""
            if "Timeline" in txt or "E2E" in txt or session_id[:6] in txt:
                items.nth(i).click()
                clicked = True
                break
        if not clicked and items.count() > 0:
            items.first.click()
            clicked = True
        assert clicked, "未找到可点击的会话项"
        pause(2)
        shot(page, "01_session_detail")

        timeline = page.locator("[data-testid='run-timeline']")
        # UI 渲染需要 React Query 拉取；轮询等待时间线挂载
        page.wait_for_selector("[data-testid='run-timeline']", state="visible", timeout=15000)
        assert timeline.count() >= 1, "RunTimeline 组件未渲染"
        # 至少渲染了若干事件行
        event_rows = page.locator(".run-timeline-event")
        print(f"  ✓ RunTimeline 已渲染，事件行数 >= {event_rows.count()}")
        assert event_rows.count() >= 1, "时间线未渲染任何事件行"
        shot(page, "02_run_timeline_rendered")

        browser.close()

    api_cleanup(token)
    print(f"\n{'='*60}\n  RunTimeline E2E 通过！截图: {SCREENSHOT_DIR}\n{'='*60}")


if __name__ == "__main__":
    run_tests()
