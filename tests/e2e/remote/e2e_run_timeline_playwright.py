#!/usr/bin/env python3
"""
Open ACE - Run Timeline Playwright E2E Test

验证持久化「运行时间线」端到端可见：当 ``run_timeline.enabled=true`` 时，一次
远程会话的生命周期（创建 → 用户消息 → AI 输出/工具调用 → 用量上报）会落库为
agent_runs / agent_run_events / agent_approvals，并在会话详情页的 RunTimeline
组件中渲染出来。

所有 HTTP 均走浏览器 ``fetch``（而非 ``requests``/urllib）——后者对本仓库的
gevent pywsgi 服务会 502（见项目记忆 urllib→gevent 502）。fetch 复用浏览器
cookie，登录后自动携带。

前置条件（与其它 remote E2E 一致）：
  - 运行中的服务 BASE_URL（默认 http://localhost:5001），且 config 中
    ``run_timeline.enabled=true``（特性默认关闭，需显式开启）
  - 存在管理员账号（默认 admin / admin123）
  - 已安装 playwright chromium

Run:
  HEADLESS=true  BASE_URL=http://localhost:5002 python tests/e2e/remote/e2e_run_timeline_playwright.py
  HEADLESS=false BASE_URL=http://localhost:5002 python tests/e2e/remote/e2e_run_timeline_playwright.py
"""

import json
import os
import uuid

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
ADMIN_USER = os.environ.get("TEST_REAL_USER", "admin")
ADMIN_PASS = os.environ.get("TEST_REAL_PASS", "admin123")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "e2e-run-timeline",
)


def shot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=True)
    print(f"    📸 {name}.png")


def pause(seconds):
    import time

    time.sleep(seconds if not HEADLESS else 0.3)


def api(page, method, path, body=None, bearer=None):
    """Same-origin fetch via the browser (shares cookies). Returns {status, ok, data}.

    ``bearer`` adds an Authorization: Bearer header — needed for /agent/message
    calls after register (non-register agent messages require agent bearer auth).
    """
    return page.evaluate(
        """async ([method, path, body, bearer]) => {
            const headers = {'Content-Type': 'application/json'};
            if (bearer) headers['Authorization'] = 'Bearer ' + bearer;
            const opts = {method, headers, credentials: 'include'};
            if (body !== null && body !== undefined) opts.body = JSON.stringify(body);
            const r = await fetch(path, opts);
            let data = null;
            try { data = await r.json(); } catch (e) {}
            return {status: r.status, ok: r.ok, data};
        }""",
        [method, path, body, bearer],
    )


def run_tests():
    machine_id = str(uuid.uuid4())
    session_id = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=0 if HEADLESS else 100)
        page = browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN"
        ).new_page()
        page.set_default_timeout(15000)
        page.goto(BASE_URL, wait_until="domcontentloaded")

        # 前置：特性必须开启，否则 events API 返回 {disabled:true}、UI 自隐藏。
        probe = api(page, "GET", "/api/remote/sessions/probe/events")
        if (probe.get("data") or {}).get("disabled"):
            print("\n⚠ run_timeline.enabled=false —— 时间线特性未开启，跳过 E2E。")
            print("  请在 config 中设置 run_timeline.enabled=true 并重启服务后再运行。")
            browser.close()
            return

        # 登录（fetch 拿到 session_token cookie，后续自动携带）
        login = api(
            page, "POST", "/api/auth/login", {"username": ADMIN_USER, "password": ADMIN_PASS}
        )
        assert login["ok"], f"登录失败: {login}"
        print(f"  ✓ 登录: {ADMIN_USER}")

        # 注册远程机器（admin）
        reg = api(page, "POST", "/api/remote/machines/register", {"tenant_id": 1})
        assert reg["ok"], reg
        reg_token = reg["data"]["registration_token"]
        reg2 = api(
            page,
            "POST",
            "/api/remote/agent/register",
            {
                "registration_token": reg_token,
                "machine_id": machine_id,
                "machine_name": f"Timeline E2E {machine_id[:8]}",
                "hostname": f"e2e-{machine_id[:8]}.local",
                "os_type": "linux",
                "os_version": "Ubuntu 24.04",
                "capabilities": {"cli_installed": True},
                "agent_version": "1.0.0-e2e",
            },
        )
        assert reg2["ok"], reg2
        agent_token = reg2["data"]["machine"]["agent_token"]
        api(
            page,
            "POST",
            "/api/remote/agent/message",
            {"type": "register", "machine_id": machine_id, "capabilities": {"cli_installed": True}},
            bearer=agent_token,
        )
        assign = api(
            page,
            "POST",
            f"/api/remote/machines/{machine_id}/assign",
            {"user_id": 1, "permission": "admin"},
        )
        assert assign["ok"], assign
        print(f"  ✓ 机器已注册并分配: {machine_id[:8]}...")

        # 创建会话（用 claude-code 绕过 qwen 的 ha_pool_token/model-key 依赖；
        # run_timeline 记录不依赖模型真正可用，只需会话生命周期存在）
        create = api(
            page,
            "POST",
            "/api/remote/sessions",
            {
                "machine_id": machine_id,
                "project_path": "/home/u/proj",
                "cli_tool": "claude-code",
                "model": "claude-sonnet",
                "title": "Timeline E2E",
            },
        )
        assert create["ok"], create
        session_id = create["data"]["session"]["session_id"]
        print(f"  ✓ 会话已创建: {session_id[:8]}...")

        # 用户消息
        assert api(
            page, "POST", f"/api/remote/sessions/{session_id}/chat", {"content": "请审查 main.py"}
        )["ok"]

        # 模拟 AI 输出（assistant + tool_use）+ 用量上报 → 落库时间线事件。
        # 报文用 Claude 结构化形状 {type:assistant, message:{content:[blocks]}},
        # is_complete=True 触发一次累积→flush，记 assistant_output + tool_use。
        # 非 register 的 agent 消息需 Bearer(agent_token)。
        assistant_data = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "发现 2 个问题并开始修复。"},
                        {"type": "tool_use", "name": "read_file", "input": {"path": "main.py"}},
                    ]
                },
            }
        )
        api(
            page,
            "POST",
            "/api/remote/agent/message",
            {
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": assistant_data,
                "stream": "stdout",
                "is_complete": True,
            },
            bearer=agent_token,
        )
        api(
            page,
            "POST",
            "/api/remote/agent/message",
            {
                "type": "usage_report",
                "machine_id": machine_id,
                "session_id": session_id,
                "tokens": {"input": 1500, "output": 800},
                "requests": 2,
            },
            bearer=agent_token,
        )
        pause(1)

        # 断言 1：events API 已持久化 run + 事件
        ev = api(page, "GET", f"/api/remote/sessions/{session_id}/events")
        assert ev["ok"], ev
        data = ev["data"]
        assert data["success"] is True, data
        assert data["run"] is not None, "run 记录未落库"
        types = [e["event_type"] for e in data["events"]]
        print(f"  ✓ events API: run.status={data['run']['status']}, events={types}")
        for need in (
            "session_created",
            "user_message",
            "assistant_output",
            "tool_use",
            "usage_reported",
        ):
            assert need in types, f"缺少事件 {need}: {types}"
        assert data["run"]["total_tokens"] >= 2300, data["run"]
        assert data["run"]["total_requests"] >= 2, data["run"]

        # 断言 2：会话详情页渲染 RunTimeline
        page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
        page.wait_for_selector(".sessions, main, h1, h2, table", timeout=10000)
        pause(2)  # 等会话列表拉取并渲染
        # 列表行用文本定位更稳：点含会话标题的行进入详情。
        clicked = False
        title_loc = page.locator("text=Timeline E2E")
        try:
            title_loc.first.wait_for(state="visible", timeout=10000)
            title_loc.first.click()
            clicked = True
        except Exception:
            # 兜底：点第一个看起来是会话行的元素
            row = page.locator("[onclick], li.session-item, .list-group-item, tr").first
            if row.count() > 0:
                row.click()
                clicked = True
        assert clicked, "未找到可点击的会话项"
        pause(2)
        shot(page, "01_session_detail")

        page.wait_for_selector("[data-testid='run-timeline']", state="visible", timeout=15000)
        assert page.locator("[data-testid='run-timeline']").count() >= 1, "RunTimeline 未渲染"
        event_rows = page.locator(".run-timeline-event")
        print(f"  ✓ RunTimeline 已渲染，事件行数 >= {event_rows.count()}")
        assert event_rows.count() >= 1, "时间线未渲染任何事件行"
        shot(page, "02_run_timeline_rendered")

        # 清理
        api(page, "POST", f"/api/remote/sessions/{session_id}/stop")
        api(page, "DELETE", f"/api/remote/machines/{machine_id}")
        browser.close()

    print(f"\n{'='*60}\n  RunTimeline E2E 通过！截图: {SCREENSHOT_DIR}\n{'='*60}")


if __name__ == "__main__":
    run_tests()
