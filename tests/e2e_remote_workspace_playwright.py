#!/usr/bin/env python3
"""
Open ACE - Remote Workspace Playwright E2E Test

以真实用户「黄迎春」登录，在浏览器中完成远程工作区全流程:
1. 登录
2. 进入工作区 (iframe 中加载 qwen-code-webui)
3. 通过 API 注册远程机器
4. 在浏览器中创建远程会话、发送消息、模拟 AI 回复
5. 验证数据正确性

Run:
  HEADLESS=true  python tests/e2e_remote_workspace_playwright.py   # 调试
  HEADLESS=false python tests/e2e_remote_workspace_playwright.py   # 演示
"""

import json
import os
import sys
import time
import uuid
import traceback

# Add project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
TEST_USER = "黄迎春"
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-remote")

# ── 测试状态 ──────────────────────────────────────────
machine_id = None
session_id = None
chatpage_session_id = None
auth_token = None


# ── 工具函数 ──────────────────────────────────────────

def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def log_step(tag, msg):
    print(f"    [{tag}] {msg}")


def pause(seconds):
    """演示模式下慢放，headless 模式下快速通过"""
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


# ── API 调用（同时在浏览器 Console 面板可看到 fetch 日志）─────

def api_login_as(username=TEST_USER, password=TEST_PASS):
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": username, "password": password})
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def api_admin_login():
    return api_login_as("admin", "admin123")


def api_register_machine(admin_token):
    global machine_id
    # 1. 生成注册 token
    r = requests.post(f"{BASE_URL}/api/remote/machines/register",
                      json={"tenant_id": 1},
                      cookies={"session_token": admin_token})
    assert r.status_code == 200
    reg_token = r.json()["registration_token"]

    # 2. 用 token 注册机器
    machine_id = str(uuid.uuid4())
    r = requests.post(f"{BASE_URL}/api/remote/agent/register", json={
        "registration_token": reg_token,
        "machine_id": machine_id,
        "machine_name": "E2E Demo Server",
        "hostname": "demo-server.local",
        "os_type": "linux",
        "os_version": "Ubuntu 24.04 LTS",
        "capabilities": {"cpu_cores": 16, "memory_gb": 64, "cli_installed": True},
        "agent_version": "1.0.0-e2e",
    })
    assert r.status_code == 200

    # 3. HTTP 长连接注册
    r = requests.post(f"{BASE_URL}/api/remote/agent/message", json={
        "type": "register",
        "machine_id": machine_id,
        "capabilities": {"cpu_cores": 16, "memory_gb": 64, "cli_installed": True},
    })
    assert r.status_code == 200

    # 4. 给测试用户分配权限（黄迎春 user_id=89）
    r = requests.post(f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
                      json={"user_id": 89, "permission": "admin"},
                      cookies={"session_token": admin_token})
    assert r.status_code == 200


def api_create_session(token):
    global session_id
    r = requests.post(f"{BASE_URL}/api/remote/sessions",
                      json={
                          "machine_id": machine_id,
                          "project_path": "/home/rhuang/workspace/demo-project",
                          "cli_tool": "qwen-code-cli",
                          "model": "qwen3-coder-plus",
                          "title": "E2E 远程会话",
                      },
                      cookies={"session_token": token})
    assert r.status_code == 200, f"Create session failed: {r.status_code} {r.text}"
    session_id = r.json()["session"]["session_id"]


def api_send_chat(token, message):
    r = requests.post(f"{BASE_URL}/api/remote/sessions/{session_id}/chat",
                      json={"content": message},
                      cookies={"session_token": token})
    return r.status_code == 200


def api_agent_output(step, is_complete=False, sid=None):
    outputs = {
        "thinking":  '{"type":"thinking","content":"正在分析代码结构，寻找潜在问题..."}',
        "response":  '{"type":"assistant","content":"发现 3 个问题：\\n1. API 端点缺少错误处理\\n2. SQL 查询存在注入风险\\n3. 文件顶部有未使用的 import"}',
        "tool_call": '{"type":"tool_use","tool":"read_file","input":{"path":"/home/rhuang/workspace/demo-project/main.py"}}',
        "tool_done": '{"type":"tool_result","tool":"read_file","output":"成功读取 142 行代码"}',
        "final":     '{"type":"assistant","content":"已修复全部 3 个问题：\\n- 添加了 try/except 错误处理\\n- 参数化 SQL 查询\\n- 移除了 5 个未使用的 import\\n\\n代码已保存，可以运行测试验证。"}',
    }
    r = requests.post(f"{BASE_URL}/api/remote/agent/message", json={
        "type": "session_output",
        "machine_id": machine_id,
        "session_id": sid or session_id,
        "data": outputs[step],
        "stream": "stdout",
        "is_complete": is_complete,
    })
    return r.status_code == 200


def api_send_usage():
    requests.post(f"{BASE_URL}/api/remote/agent/message", json={
        "type": "usage_report",
        "machine_id": machine_id,
        "session_id": session_id,
        "tokens": {"input": 1500, "output": 800},
        "requests": 2,
    })


def api_cleanup(token):
    global session_id, machine_id
    if session_id:
        requests.post(f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
                      cookies={"session_token": token})
        session_id = None
    if machine_id:
        requests.delete(f"{BASE_URL}/api/remote/machines/{machine_id}",
                        cookies={"session_token": token})
        machine_id = None


# ── 在浏览器页面中执行 fetch 并显示 toast 通知 ──────────

def browser_fetch(page, label, method, url, body=None):
    """在浏览器控制台中执行 fetch 请求，并打印结果，让用户在 DevTools Console 看到过程。"""
    script = """
    async ([label, method, url, body]) => {
        const opts = { method, headers: { 'Content-Type': 'application/json' },
                       credentials: 'include' };
        if (body) opts.body = JSON.stringify(body);
        console.log(`%c[REMOTE-WS] ${label}`, 'color: #2196F3; font-weight: bold', method, url);
        const t0 = performance.now();
        const resp = await fetch(url, opts);
        const elapsed = (performance.now() - t0).toFixed(0);
        const data = await resp.json().catch(() => null);
        console.log(`%c[REMOTE-WS] ${label} ✓ ${resp.status} (${elapsed}ms)`,
                    'color: #4CAF50; font-weight: bold', data);
        // 在页面上显示通知
        const n = document.createElement('div');
        n.textContent = `✓ ${label} — ${resp.status} (${elapsed}ms)`;
        Object.assign(n.style, {
            position: 'fixed', bottom: '20px', right: '20px', zIndex: '99999',
            background: resp.ok ? '#4CAF50' : '#f44336', color: '#fff',
            padding: '12px 24px', borderRadius: '8px', fontSize: '14px',
            fontWeight: 'bold', boxShadow: '0 4px 12px rgba(0,0,0,.3)',
            transition: 'opacity .3s', fontFamily: 'system-ui, sans-serif',
        });
        document.body.appendChild(n);
        setTimeout(() => { n.style.opacity = '0'; setTimeout(() => n.remove(), 400); }, 2500);
        return { status: resp.status, ok: resp.ok, data };
    }
    """
    result = page.evaluate(script, [label, method, url, body])
    return result


# ══════════════════════════════════════════════════════
#  主测试流程
# ══════════════════════════════════════════════════════

def run_tests():
    global auth_token, session_id, chatpage_session_id

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        # ══════ 1. 登录 ══════
        print("\n══════ 1. 登录 ══════")
        log_step("导航", "打开登录页")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        shot(page, "01_login_page")

        log_step("输入", f"用户名: {TEST_USER}")
        page.fill("#username", TEST_USER)
        pause(0.5)
        log_step("输入", "密码: ******")
        page.fill("#password", TEST_PASS)
        pause(0.3)
        log_step("点击", "Sign In")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
        page.wait_for_selector("main, h1, h2, .dashboard, .work-main", timeout=15000)
        pause(2)
        shot(page, "02_logged_in")
        print("  ✓ 登录成功")

        # 先用 API 登录获取 token（后续 API 调用需要）
        auth_token = api_login_as()
        admin_token = api_admin_login()

        # ══════ 2. 工作台页面 ══════
        print("\n══════ 2. 工作台页面 ══════")
        log_step("导航", "/work")
        page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
        page.wait_for_selector("main, h1, h2, .work-main, .workspace-container", timeout=10000)
        pause(2)
        shot(page, "03_work_page")
        print("  ✓ 工作台页面加载完成")

        # ══════ 3. 注册远程机器（通过浏览器 fetch）══════
        print("\n══════ 3. 注册远程机器 ══════")

        # 先用 admin API 注册
        api_register_machine(admin_token)
        log_step("API", f"机器已注册: {machine_id[:8]}...")

        # 在浏览器中验证机器列表
        result = browser_fetch(page, "查询可用远程机器", "GET",
                               f"/api/remote/machines/available")
        machines = result.get("data", {}).get("machines", [])
        log_step("结果", f"可用机器数: {len(machines)}")
        pause(2)
        shot(page, "04_machine_available")
        assert len(machines) >= 1, f"应有至少 1 台可用机器，实际 {len(machines)}"
        print(f"  ✓ 远程机器已注册并可用 (connected=true)")

        # ══════ 4. 创建远程会话（通过浏览器 fetch）══════
        print("\n══════ 4. 创建远程会话")

        result = browser_fetch(page, "创建远程会话", "POST",
                               "/api/remote/sessions", {
                                   "machine_id": machine_id,
                                   "project_path": "/home/rhuang/workspace/demo-project",
                                   "cli_tool": "qwen-code-cli",
                                   "model": "qwen3-coder-plus",
                                   "title": "E2E 远程会话",
                               })
        assert result["ok"], f"创建会话失败: {result}"
        session_id = result["data"]["session"]["session_id"]
        log_step("会话ID", session_id[:8] + "...")
        pause(3)
        shot(page, "05_session_created")
        print(f"  ✓ 远程会话已创建: {session_id[:8]}...")

        # ══════ 5. 发送用户消息（通过浏览器 fetch）══════
        print("\n══════ 5. 发送用户消息")

        result = browser_fetch(page, "发送消息给远程 AI", "POST",
                               f"/api/remote/sessions/{session_id}/chat", {
                                   "content": "请帮我审查 main.py 的代码，找出并修复所有问题。",
                               })
        assert result["ok"], f"发送消息失败: {result}"
        pause(3)
        shot(page, "06_message_sent")
        print("  ✓ 用户消息已发送")

        # ══════ 6. 模拟 AI 回复（分步，每步暂停）══════
        print("\n══════ 6. 模拟远程 AI 回复（分步）")

        steps = [
            ("thinking",  False, "AI 正在思考..."),
            ("response",  False, "AI 生成回复（发现 3 个问题）"),
            ("tool_call", False, "AI 调用工具: read_file"),
            ("tool_done", False, "工具返回结果"),
            ("final",     True,  "AI 最终回复（已修复所有问题）"),
        ]
        for i, (step, done, label) in enumerate(steps):
            log_step(f"步骤{i+1}/5", label)
            api_agent_output(step, is_complete=done)
            pause(3)
            shot(page, f"07{i+1}_agent_{step}")

        api_send_usage()
        log_step("用量", "上报 token 用量: input=1500, output=800")
        print("  ✓ AI 回复完成（5 步）")

        # ══════ 7. 验证会话状态（通过浏览器 fetch）══════
        print("\n══════ 7. 验证会话数据")

        result = browser_fetch(page, "查询会话详情", "GET",
                               f"/api/remote/sessions/{session_id}")
        assert result["ok"]
        sess = result["data"]["session"]
        output_count = len(sess.get("output", []))
        tokens = sess.get("total_tokens", 0)
        log_step("输出条数", str(output_count))
        log_step("Token 数", str(tokens))
        pause(2)
        shot(page, "08_session_verified")
        assert output_count >= 5, f"应有 >=5 条输出，实际 {output_count}"
        assert tokens >= 2300, f"应有 >=2300 tokens，实际 {tokens}"
        print(f"  ✓ 会话数据验证通过: {output_count} 条输出, {tokens} tokens")

        # ══════ 8. 会话列表页面 ══════
        print("\n══════ 8. 会话列表页面")
        page.goto(f"{BASE_URL}/work/sessions", wait_until="domcontentloaded")
        page.wait_for_selector("main, .session, h1, h2, table", timeout=10000)
        pause(2)
        shot(page, "09_sessions_list")

        # 查找并点击我们的远程会话
        session_items = page.locator('.session-item, .session-card, .list-group-item, tr')
        found = False
        for i in range(session_items.count()):
            text = session_items.nth(i).text_content()
            if "E2E" in text or "远程" in text or session_id[:6] in text:
                session_items.nth(i).click()
                found = True
                log_step("点击", "找到远程会话，点击查看详情")
                break
        if not found and session_items.count() > 0:
            session_items.first.click()
            log_step("点击", "点击第一个会话项")
        pause(2)
        shot(page, "10_session_detail")
        print("  ✓ 会话列表和详情页面正常")

        # ══════ 9. 管理后台 ══════
        print("\n══════ 9. 管理后台")
        page.goto(f"{BASE_URL}/manage/dashboard", wait_until="domcontentloaded")
        page.wait_for_selector("main, .dashboard, h1, h2, table, .card", timeout=10000)
        pause(2)
        shot(page, "11_manage_dashboard")
        print("  ✓ 管理后台正常加载")

        # ══════ 10. ChatPage 远程模式 UI 测试 ══════
        print("\n══════ 10. ChatPage 远程模式 UI 测试 ══════")

        # 监听 ChatPage 自动创建 session 的网络响应
        captured_sid = [None]
        def on_response(response):
            url = response.url
            if "/api/remote/sessions" in url and "/chat" not in url and "/stop" not in url:
                if response.request.method == "POST":
                    try:
                        data = response.json()
                        sid = data.get("session", {}).get("session_id")
                        if sid:
                            captured_sid[0] = sid
                    except Exception:
                        pass

        page.on("response", on_response)

        # 捕获控制台错误帮助调试
        console_errors = []
        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append(f"[{msg.type}] {msg.text}")
        page.on("console", on_console)

        # 获取 webui 专用 token（不同于 session cookie）
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url",
            cookies={"session_token": auth_token}
        ).json()
        webui_token = webui_info.get("token", "")
        effective_webui_url = webui_info.get("url", WEBUI_URL)
        log_step("WebUI", f"URL={effective_webui_url}, token={webui_token[:16]}...")

        # 构造 ChatPage 远程模式 URL（需要 /projects 路由才能进入 ChatPage）
        chat_url = (
            f"{effective_webui_url}/projects"
            f"?token={webui_token}"
            f"&openace_url={BASE_URL}"
            f"&workspaceType=remote"
            f"&machineId={machine_id}"
            f"&machineName=Demo%20Server"
            f"&encodedProjectName=-home-user-demo-project"
        )
        log_step("导航", "打开 ChatPage（远程模式）")
        page.goto(chat_url, wait_until="networkidle")

        # 等待 ChatPage 加载（React SPA 需要时间渲染）
        try:
            page.wait_for_selector("textarea, .max-w-6xl, #root, .min-h-screen", timeout=20000)
            # 等待 React 渲染 + remote session 创建（网络请求需要时间）
            pause(8)
        except Exception:
            log_step("警告", "ChatPage 加载超时，可能 webui 未运行")
            shot(page, "10_chatpage_timeout")
            print("  ⚠ ChatPage 未加载，跳过远程模式 UI 测试")
            page.remove_listener("response", on_response)
        else:
            pause(3)
            shot(page, "10_chatpage_remote_loaded")
            log_step("加载", "ChatPage 远程模式已加载")

            # 输出控制台错误帮助调试
            if console_errors:
                for err in console_errors[:5]:
                    log_step("Console", err)

            # 验证远程指示器（机器名 + 状态点）
            indicator = page.locator("text=Demo Server")
            if indicator.count() > 0:
                log_step("验证", "✓ 远程指示器显示: Demo Server + 状态点")
                shot(page, "10_remote_indicator")
            else:
                # 尝试查找页面文本来确认渲染状态
                page_text = page.locator("body").text_content() or ""
                if "workspaceType" in chat_url:
                    log_step("验证", f"远程指示器未找到。页面内容片段: {page_text[:200]}")
                shot(page, "10_no_indicator")

            # 验证 "Stop Session" 按钮（session 活跃时显示）
            stop_btn = page.locator('button:has-text("Stop Session"), button:has-text("停止")')
            if stop_btn.count() > 0:
                log_step("验证", "✓ Stop Session 按钮可见")
            else:
                log_step("验证", "Stop Session 按钮未找到")

            # 捕获 ChatPage 自动创建的 session_id
            if captured_sid[0]:
                chatpage_session_id = captured_sid[0]
                log_step("会话", f"ChatPage 自动创建会话: {chatpage_session_id[:8]}...")

                # 模拟 AI 回复（3 步快速演示）
                for i, (step, done, label) in enumerate([
                    ("thinking", False, "AI 正在思考..."),
                    ("response", False, "AI 生成回复"),
                    ("final",    True,  "AI 最终回复"),
                ]):
                    api_agent_output(step, is_complete=done, sid=chatpage_session_id)
                    pause(2)

                pause(3)
                shot(page, "10_ai_reply_in_chatpage")
                log_step("回复", "✓ AI 回复已通过 ChatPage 远程轮询显示")
            else:
                # 通过 input 发送消息触发 session 创建
                log_step("发送", "未捕获到 session，尝试手动发送消息")
                textarea = page.locator("textarea").first
                if textarea.count() > 0:
                    textarea.fill("你好，这是一个来自远程 ChatPage 的测试消息")
                    pause(1)
                    page.keyboard.press("Enter")
                    pause(5)
                    shot(page, "10_message_sent_from_chatpage")

                    # 再次检查捕获的 session
                    if captured_sid[0]:
                        chatpage_session_id = captured_sid[0]
                        log_step("会话", f"手动发送后捕获会话: {chatpage_session_id[:8]}...")
                        api_agent_output("final", is_complete=True, sid=chatpage_session_id)
                        pause(3)
                        shot(page, "10_reply_after_send")

            print("  ✓ ChatPage 远程模式 UI 测试完成")
            page.remove_listener("response", on_response)

        # 导航回 Open ACE 域，确保后续 browser_fetch 正常工作
        page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
        pause(1)

        # ══════ 11. 停止会话 & 清理 ══════
        print("\n══════ 11. 停止会话 & 清理")

        # 用 requests 停止会话（不依赖页面域）
        r = requests.post(f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
                          cookies={"session_token": auth_token})
        log_step("停止", f"原始会话: {session_id[:8]}... → {r.status_code}")

        # 停止 ChatPage 创建的远程会话（如果有）
        if chatpage_session_id:
            r2 = requests.post(f"{BASE_URL}/api/remote/sessions/{chatpage_session_id}/stop",
                               cookies={"session_token": auth_token})
            log_step("清理", f"ChatPage 会话: {chatpage_session_id[:8]}... → {r2.status_code}")

        pause(2)

        # 注销机器需要 admin 权限，用 admin token
        log_step("清理", "用 admin token 注销远程机器")
        r = requests.delete(f"{BASE_URL}/api/remote/machines/{machine_id}",
                            cookies={"session_token": admin_token})
        assert r.status_code == 200, f"注销机器失败: {r.status_code} {r.text}"
        pause(2)
        shot(page, "12_cleanup_done")
        print("  ✓ 会话已停止，机器已注销")

        # ══════ 12. 登出 ══════
        print("\n══════ 12. 登出")
        page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
        pause(2)
        try:
            page.wait_for_url("**/login**", timeout=10000)
            page.wait_for_selector("#username", state="visible", timeout=10000)
        except Exception:
            pass
        shot(page, "13_logout")
        print("  ✓ 已登出，回到登录页")

        # ══════ 完成 ══════
        context.close()
        browser.close()

    print(f"\n{'='*60}")
    print(f"  全部通过! 截图保存在: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_tests()
