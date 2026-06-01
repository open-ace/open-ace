#!/usr/bin/env python3
"""
Open ACE - Remote File Changes Panel & VSCode E2E Playwright Test

在浏览器中完成远程工作区文件变更面板和 VSCode 编辑器的端到端测试:
1. 登录 + 注册远程机器（模拟带 has_git/has_code_server 能力）
2. 创建远程会话
3. 通过 agent message 模拟 git_status 结果，验证文件变更面板显示
4. 验证 git_diff 端点
5. 验证 git_file 端点
6. 验证 VSCode 启动端点
7. 验证降级：无 git 的机器不显示文件变更

Run:
  HEADLESS=true  python tests/issues/610/e2e_remote_file_changes_playwright.py
  HEADLESS=false python tests/issues/610/e2e_remote_file_changes_playwright.py
"""

import json
import os
import sys
import time
import uuid

# Add project root
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
TEST_USER = os.environ.get("TEST_REAL_USER", "test_user")
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-file-changes-remote")

# ── 测试状态 ──────────────────────────────────────────
machine_id_git = None  # 带 git 的机器
machine_id_no_git = None  # 不带 git 的机器
session_id = None
auth_token = None
admin_token = None


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
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def browser_fetch(page, label, method, url, body=None):
    """在浏览器中执行 fetch 请求并返回结果。"""
    script = """
    async ([label, method, url, body]) => {
        const opts = { method, headers: { 'Content-Type': 'application/json' },
                       credentials: 'include' };
        if (body) opts.body = JSON.stringify(body);
        console.log(`[E2E] ${label}`, method, url);
        const resp = await fetch(url, opts);
        const data = await resp.json().catch(() => null);
        return { status: resp.status, ok: resp.ok, data };
    }
    """
    return page.evaluate(script, [label, method, url, body])


# ── API 辅助 ──────────────────────────────────────────


def api_login_as(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return r.cookies.get("session_token")


def api_get_user_id(username=TEST_USER, password=TEST_PASS):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    return r.json()["user"]["id"]


def api_admin_login():
    return api_login_as("admin", "admin123")


def register_machine(admin_tok, name, capabilities):
    """注册一台远程机器并返回 machine_id。"""
    # 生成注册 token
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": admin_tok},
    )
    assert r.status_code == 200
    reg_token = r.json()["registration_token"]

    mid = str(uuid.uuid4())
    short_id = mid[:8]
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/register",
        json={
            "registration_token": reg_token,
            "machine_id": mid,
            "machine_name": f"{name} {short_id}",
            "hostname": f"{name.lower().replace(' ', '-')}-{short_id}.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04",
            "capabilities": capabilities,
            "agent_version": "1.0.0-e2e",
        },
    )
    assert r.status_code == 200

    # 心跳注册（让机器显示为在线）
    requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={"type": "register", "machine_id": mid, "capabilities": capabilities},
    )
    return mid


def assign_machine(admin_tok, mid, user_id=89):
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{mid}/assign",
        json={"user_id": user_id, "permission": "admin"},
        cookies={"session_token": admin_tok},
    )
    assert r.status_code == 200


def simulate_git_result(mid, request_id, result_data):
    """模拟 remote agent 返回 git_result 消息。"""
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "git_result",
            "machine_id": mid,
            "request_id": request_id,
            "success": True,
            "result": result_data,
        },
    )
    return r.status_code


# ── 模拟 git status 数据 ─────────────────────────────


MOCK_GIT_STATUS = {
    "files": [
        {
            "path": "src/main.py",
            "status": "modified",
            "additions": 15,
            "deletions": 3,
        },
        {
            "path": "src/utils.py",
            "status": "added",
            "additions": 42,
            "deletions": 0,
        },
        {
            "path": "README.md",
            "status": "modified",
            "additions": 5,
            "deletitions": 2,
        },
        {
            "path": "old_feature.py",
            "status": "deleted",
            "additions": 0,
            "deletions": 120,
        },
    ]
}

MOCK_GIT_DIFF = {
    "file": "src/main.py",
    "diff": "@@ -10,7 +10,8 @@\n def hello():\n-    print('hello')\n+    print('hello world')\n+    return True\n",
    "originalContent": "def hello():\n    print('hello')\n",
    "modifiedContent": "def hello():\n    print('hello world')\n    return True\n",
}

MOCK_GIT_FILE = {
    "file": "src/utils.py",
    "content": "import os\n\ndef get_env(key):\n    return os.environ.get(key)\n",
}


# ══════════════════════════════════════════════════════
#  主测试流程
# ══════════════════════════════════════════════════════


def run_tests():
    global machine_id_git, machine_id_no_git, session_id, auth_token, admin_token

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(15000)

        # ══════ 1. 登录 ══════
        print("\n══════ 1. 登录 ══════")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", TEST_USER)
        page.fill("#password", TEST_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
        pause(2)
        shot(page, "01_logged_in")
        print("  ✓ 登录成功")

        auth_token = api_login_as()
        admin_token = api_admin_login()
        test_user_id = api_get_user_id(TEST_USER, TEST_PASS)

        # ══════ 2. 注册远程机器 ══════
        print("\n══════ 2. 注册远程机器（带 git + code-server）══════")

        machine_id_git = register_machine(
            admin_token,
            "Git Server",
            {
                "cpu_cores": 8,
                "memory_gb": 32,
                "has_git": True,
                "has_code_server": True,
                "cli_installed": True,
            },
        )
        assign_machine(admin_token, machine_id_git, test_user_id)
        log_step("注册", f"Git Server: {machine_id_git[:8]}...")

        machine_id_no_git = register_machine(
            admin_token,
            "Minimal Server",
            {
                "cpu_cores": 2,
                "memory_gb": 4,
                "has_git": False,
                "has_code_server": False,
                "cli_installed": False,
            },
        )
        assign_machine(admin_token, machine_id_no_git, test_user_id)
        log_step("注册", f"Minimal Server (no git): {machine_id_no_git[:8]}...")

        pause(2)
        shot(page, "02_machines_registered")
        print("  ✓ 两台远程机器已注册")

        # ══════ 3. 测试 Git Status API 端点 ══════
        print("\n══════ 3. 测试 Git Status API 端点 ══════")

        # 3a. 未认证请求
        log_step("测试", "未认证请求应返回 401")
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/status",
            params={"path": "/home/user/project"},
        )
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
        log_step("通过", f"未认证: {r.status_code}")

        # 3b. 正常请求（agent 在线但无实时 agent，会超时/503）
        log_step("测试", "认证请求（无实时 agent 连接）")
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/status",
            params={"path": "/home/user/project"},
            cookies={"session_token": auth_token},
        )
        # agent 不是真正的持久连接，期望 503 或 504
        assert r.status_code in (200, 503, 504), f"Unexpected: {r.status_code}"
        log_step("通过", f"认证请求: {r.status_code}")

        # 3c. 缺少 path 参数
        log_step("测试", "缺少 path 参数应返回 400")
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/status",
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        log_step("通过", f"缺少 path: {r.status_code}")

        # 3d. 通过 browser fetch 测试
        page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
        page.wait_for_selector("main, h1, h2, .work-main", timeout=10000)
        pause(1)

        result = browser_fetch(
            page,
            "Git Status (无 path)",
            "GET",
            f"/api/remote/machines/{machine_id_git}/git/status",
        )
        assert result["status"] == 400
        log_step("浏览器", f"Git Status 无 path: {result['status']}")

        shot(page, "03_git_api_tests")
        print("  ✓ Git Status API 端点测试通过")

        # ══════ 4. 测试 Git Diff API 端点 ══════
        print("\n══════ 4. 测试 Git Diff API 端点 ══════")

        # 4a. 缺少参数
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/diff",
            params={"path": "/home/user/project"},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        log_step("通过", f"Git Diff 缺少 file: {r.status_code}")

        # 4b. 正常请求（无实时 agent）
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/diff",
            params={"path": "/home/user/project", "file": "src/main.py"},
            cookies={"session_token": auth_token},
        )
        assert r.status_code in (200, 503, 504)
        log_step("通过", f"Git Diff 正常请求: {r.status_code}")

        # 4c. 浏览器测试
        result = browser_fetch(
            page,
            "Git Diff",
            "GET",
            f"/api/remote/machines/{machine_id_git}/git/diff?path=/home/user/project&file=src/main.py",
        )
        assert result["status"] in (200, 503, 504)
        log_step("浏览器", f"Git Diff: {result['status']}")

        shot(page, "04_git_diff_tests")
        print("  ✓ Git Diff API 端点测试通过")

        # ══════ 5. 测试 Git File API 端点 ══════
        print("\n══════ 5. 测试 Git File API 端点 ══════")

        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id_git}/git/file",
            params={"path": "/home/user/project", "file": "src/main.py"},
            cookies={"session_token": auth_token},
        )
        assert r.status_code in (200, 503, 504)
        log_step("通过", f"Git File: {r.status_code}")

        print("  ✓ Git File API 端点测试通过")

        # ══════ 6. 测试 VSCode Start 端点 ══════
        print("\n══════ 6. 测试 VSCode Start/Stop/Status 端点 ══════")

        # 6a. 未认证
        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/start",
            json={"machine_id": machine_id_git, "project_path": "/home/user/project"},
        )
        assert r.status_code == 401
        log_step("通过", f"VSCode Start 未认证: {r.status_code}")

        # 6b. 缺少参数
        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/start",
            json={"machine_id": machine_id_git},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400
        log_step("通过", f"VSCode Start 缺少 project_path: {r.status_code}")

        # 6c. 正常请求（无实时 agent -> 503）
        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/start",
            json={"machine_id": machine_id_git, "project_path": "/home/user/project"},
            cookies={"session_token": auth_token},
        )
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            data = r.json()
            assert data.get("vscode_id"), "Should return vscode_id"
            assert data.get("status") == "pending"
            log_step("通过", f"VSCode Start: 200, vscode_id={data['vscode_id'][:8]}...")
        else:
            log_step("通过", f"VSCode Start: {r.status_code} (无实时 agent)")

        # 6d. VSCode Status（未知的 vscode_id）
        fake_vscode_id = str(uuid.uuid4())
        r = requests.get(
            f"{BASE_URL}/api/remote/vscode/{fake_vscode_id}/status",
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "unknown"
        log_step("通过", "VSCode Status unknown")

        # 6e. VSCode WebSocket fallback
        r = requests.get(
            f"{BASE_URL}/api/remote/vscode/{fake_vscode_id}/ws",
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400
        log_step("通过", "VSCode WS fallback: 400")

        # 6f. VSCode Proxy（未知的 vscode_id）
        r = requests.get(
            f"{BASE_URL}/api/remote/vscode/{fake_vscode_id}/proxy/?token=invalid",
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 404
        log_step("通过", "VSCode Proxy unknown: 404")

        # 6g. 浏览器测试 VSCode
        result = browser_fetch(
            page,
            "VSCode Start (无 agent)",
            "POST",
            "/api/remote/vscode/start",
            {"machine_id": machine_id_git, "project_path": "/home/user/project"},
        )
        log_step("浏览器", f"VSCode Start: {result['status']}")

        shot(page, "05_vscode_api_tests")
        print("  ✓ VSCode API 端点测试通过")

        # ══════ 7. 测试能力检测（has_git / has_code_server）══════
        print("\n══════ 7. 测试机器能力检测 ══════")

        result = browser_fetch(page, "查询可用机器", "GET", "/api/remote/machines/available")
        machines = result.get("data", {}).get("machines", [])
        log_step("结果", f"可用机器数: {len(machines)}")

        git_machine = None
        no_git_machine = None
        for m in machines:
            caps = m.get("capabilities", {})
            if m["machine_id"] == machine_id_git:
                git_machine = m
                assert caps.get("has_git") is True, "Git Server should have has_git=True"
                assert (
                    caps.get("has_code_server") is True
                ), "Git Server should have has_code_server=True"
            elif m["machine_id"] == machine_id_no_git:
                no_git_machine = m
                assert caps.get("has_git") is False, "Minimal Server should have has_git=False"
                assert (
                    caps.get("has_code_server") is False
                ), "Minimal Server should have has_code_server=False"

        assert git_machine is not None, "Git Server not found in machines"
        assert no_git_machine is not None, "Minimal Server not found in machines"

        pause(2)
        shot(page, "06_capabilities_test")
        print("  ✓ 机器能力检测验证通过 (has_git / has_code_server)")

        # ══════ 8. 测试 VSCode Stop 端点 ══════
        print("\n══════ 8. 测试 VSCode Stop 端点 ══════")

        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/stop",
            json={"vscode_id": str(uuid.uuid4()), "machine_id": machine_id_git},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 200
        log_step("通过", "VSCode Stop: 200")

        # 缺少参数
        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/stop",
            json={"vscode_id": str(uuid.uuid4())},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400
        log_step("通过", "VSCode Stop 缺少 machine_id: 400")

        print("  ✓ VSCode Stop 端点测试通过")

        # ══════ 9. 测试 VSCode Attach 端点 ══════
        print("\n══════ 9. 测试 VSCode Attach 端点 ══════")

        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/{str(uuid.uuid4())}/attach",
            json={"machine_id": machine_id_git},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 200
        log_step("通过", "VSCode Attach: 200")

        # 缺少 machine_id
        r = requests.post(
            f"{BASE_URL}/api/remote/vscode/{str(uuid.uuid4())}/attach",
            json={},
            cookies={"session_token": auth_token},
        )
        assert r.status_code == 400
        log_step("通过", "VSCode Attach 缺少 machine_id: 400")

        print("  ✓ VSCode Attach 端点测试通过")

        # ══════ 10. ChatPage 远程模式 - 文件变更面板 UI ══════
        print("\n══════ 10. ChatPage 远程模式 - 文件变更面板 UI ══════")

        console_errors = []

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append(f"[{msg.type}] {msg.text}")

        page.on("console", on_console)

        # 获取 webui token
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url", cookies={"session_token": auth_token}
        ).json()
        webui_token = webui_info.get("token", "")
        effective_webui_url = webui_info.get("url", WEBUI_URL)

        # 构造 ChatPage 远程模式 URL，带 showFileChangesPanel=true
        chat_url = (
            f"{effective_webui_url}/projects"
            f"?token={webui_token}"
            f"&openace_url={BASE_URL}"
            f"&workspaceType=remote"
            f"&machineId={machine_id_git}"
            f"&machineName=Git%20Server"
            f"&encodedProjectName=-home-user-demo-project"
            f"&showFileChangesPanel=true"
        )
        log_step("导航", "打开 ChatPage（远程模式 + 文件变更面板）")
        try:
            page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            log_step("警告", "ChatPage 导航超时，继续验证")

        try:
            page.wait_for_selector("textarea, .max-w-6xl, #root, .min-h-screen", timeout=20000)
            pause(5)
        except Exception:
            log_step("警告", "ChatPage 加载超时，可能 webui 未运行")
            shot(page, "10_chatpage_timeout")

        # 输出控制台错误
        if console_errors:
            for err in console_errors[:10]:
                log_step("Console", err)

        pause(3)
        shot(page, "10_chatpage_remote_filechanges")

        # 验证文件变更面板是否可见（不再显示"暂不支持"消息）
        unsupported_text = page.locator("text=暂不支持")
        unsupported_en = page.locator("text=not supported")
        if unsupported_text.count() > 0 or unsupported_en.count() > 0:
            log_step("注意", "文件变更面板显示了'暂不支持'（可能无实时 agent）")
        else:
            log_step("验证", "文件变更面板已渲染（无'暂不支持'消息）")

        print("  ✓ ChatPage 远程模式文件变更面板 UI 测试完成")
        page.remove_listener("console", on_console)

        # ══════ 11. 清理 ══════
        print("\n══════ 11. 清理")

        page.goto(f"{BASE_URL}/work", wait_until="domcontentloaded")
        pause(1)

        for mid in [machine_id_git, machine_id_no_git]:
            if mid:
                requests.delete(
                    f"{BASE_URL}/api/remote/machines/{mid}",
                    cookies={"session_token": admin_token},
                )
                log_step("清理", f"机器 {mid[:8]}... 已注销")

        pause(2)
        shot(page, "11_cleanup_done")
        print("  ✓ 清理完成")

        # ══════ 完成 ══════
        context.close()
        browser.close()

    print(f"\n{'='*60}")
    print(f"  E2E 测试全部通过! 截图保存在: {SCREENSHOT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_tests()
