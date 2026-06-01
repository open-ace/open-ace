#!/usr/bin/env python3
"""
Open ACE - HA Model Pool E2E Test

验证 HA model pool 端到端流程:
1. 登录 admin
2. 验证本地 session-models API
3. 注册远程机器
4. 验证远程 session-models API（生成 ha_pool_token）
5. 创建远程 qwen session（带 ha_pool_token）
6. 验证非 qwen session 不需要 ha_pool_token（Critical bug 回归）
7. 验证 LLM proxy 路由正常工作
8. 清理

Run:
  HEADLESS=true  python tests/issues/604/e2e_ha_model_pool.py
  HEADLESS=false python tests/issues/604/e2e_ha_model_pool.py
"""

import os
import sys
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-ha-pool")

# ── 测试状态 ──────────────────────────────────────────
machine_id = None
admin_token = None
user_token = None
results = []


# ── 工具函数 ──────────────────────────────────────────


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def log_step(tag, msg):
    print(f"  [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def record(test_name, passed, detail=""):
    status = "✅" if passed else "❌"
    results.append((test_name, passed))
    print(f"    {status} {test_name}" + (f" — {detail}" if detail else ""))


# ── API 调用 ──────────────────────────────────────────


def api_login_as(username="admin", password="admin123"):
    r = requests.post(
        f"{BASE_URL}/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, f"Login failed for {username}: {r.status_code} {r.text}"
    token = r.cookies.get("session_token")
    assert token, "No session_token cookie"
    return token


def api_register_machine(adm_token, user_id):
    global machine_id
    # 1. 生成注册 token
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/register",
        json={"tenant_id": 1},
        cookies={"session_token": adm_token},
    )
    assert r.status_code == 200, f"Register machine failed: {r.status_code} {r.text}"
    reg_token = r.json()["registration_token"]

    # 2. 注册机器
    machine_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/register",
        json={
            "registration_token": reg_token,
            "machine_id": machine_id,
            "machine_name": "E2E HA Pool Server",
            "hostname": "ha-pool-server.local",
            "os_type": "linux",
            "os_version": "Ubuntu 24.04 LTS",
            "capabilities": {"cpu_cores": 16, "memory_gb": 64, "cli_installed": True},
            "agent_version": "1.0.0-e2e",
        },
    )
    assert r.status_code == 200, f"Agent register failed: {r.status_code} {r.text}"

    # 3. HTTP 长连接注册
    r = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json={
            "type": "register",
            "machine_id": machine_id,
            "capabilities": {"cpu_cores": 16, "memory_gb": 64, "cli_installed": True},
        },
    )
    assert r.status_code == 200

    # 4. 分配权限给测试用户
    r = requests.post(
        f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
        json={"user_id": user_id, "permission": "admin"},
        cookies={"session_token": adm_token},
    )
    assert r.status_code == 200, f"Assign failed: {r.status_code} {r.text}"


def api_cleanup(adm_token):
    global machine_id
    if machine_id:
        requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={"type": "unregister", "machine_id": machine_id},
        )
        requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}",
            cookies={"session_token": adm_token},
        )
        machine_id = None


# ── 测试步骤 ──────────────────────────────────────────


def step1_login(page):
    """Step 1: 登录"""
    log_step("STEP", "1. 登录 admin")
    global admin_token, user_token

    admin_token = api_login_as("admin")
    user_token = admin_token  # use admin for all operations

    page.goto(f"{BASE_URL}/login")
    pause(1)
    shot(page, "01-login")

    record("Admin login", bool(admin_token))


def step2_local_session_models(page):
    """Step 2: 验证本地 session-models API"""
    log_step("STEP", "2. 验证本地 session-models API")

    r = requests.get(
        f"{BASE_URL}/api/workspace/session-models?workspace_type=local",
        cookies={"session_token": user_token},
    )
    data = r.json()

    record("Local session-models returns 200", r.status_code == 200)
    record("Response has success=True", data.get("success") is True)
    # models 可能为空（取决于是否配置了 API key），但不应报错
    has_models = bool(data.get("models"))
    record("Local models list present", has_models, f"models count: {len(data.get('models', []))}")

    page.goto(f"{BASE_URL}/work")
    pause(1)
    shot(page, "02-local-workspace")


def step3_register_machine(page):
    """Step 3: 注册远程机器"""
    log_step("STEP", "3. 注册远程机器")

    # Admin user id is typically 1
    user_id = 1

    api_register_machine(admin_token, user_id)
    record("Remote machine registered", bool(machine_id))


def step4_remote_session_models(page):
    """Step 4: 验证远程 session-models API（生成 ha_pool_token）"""
    log_step("STEP", "4. 验证远程 session-models API")

    r = requests.get(
        f"{BASE_URL}/api/workspace/session-models?workspace_type=remote&machine_id={machine_id}",
        cookies={"session_token": user_token},
    )
    data = r.json()

    record("Remote session-models returns 200", r.status_code == 200)
    record("Response has success=True", data.get("success") is True)

    ha_pool_token = data.get("ha_pool_token")
    record(
        "ha_pool_token generated",
        bool(ha_pool_token),
        f"token length: {len(ha_pool_token) if ha_pool_token else 0}",
    )

    page.goto(f"{BASE_URL}/work")
    pause(1)
    shot(page, "04-remote-session-models")

    return ha_pool_token


def step5_create_qwen_session(ha_pool_token):
    """Step 5: 创建远程 qwen session（带 ha_pool_token）"""
    log_step("STEP", "5. 创建远程 qwen session")

    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/home/test/workspace/ha-test",
            "cli_tool": "qwen-code-cli",
            "ha_pool_token": ha_pool_token,
            "title": "E2E HA Pool Test Session",
        },
        cookies={"session_token": user_token},
    )

    if r.status_code == 200:
        sess = r.json().get("session", {})
        qwen_session_id = sess.get("session_id")
        record(
            "Qwen session created with ha_pool_token", True, f"session_id: {qwen_session_id[:8]}..."
        )
        return qwen_session_id
    else:
        # 可能因为没有配置 API key 而失败
        record("Qwen session creation", False, f"status: {r.status_code}, body: {r.text[:200]}")
        return None


def step6_non_qwen_session():
    """Step 6: 验证非 qwen session 不需要 ha_pool_token（Critical bug 回归）"""
    log_step("STEP", "6. 验证非 qwen session（Critical bug 回归）")

    r = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        json={
            "machine_id": machine_id,
            "project_path": "/home/test/workspace/claude-test",
            "cli_tool": "claude-code",
            "title": "E2E Non-Qwen Session",
        },
        cookies={"session_token": user_token},
    )

    if r.status_code == 200:
        sess = r.json().get("session", {})
        record(
            "Non-qwen session created WITHOUT ha_pool_token",
            True,
            f"session_id: {sess.get('session_id', '')[:8]}...",
        )
    else:
        detail = f"status: {r.status_code}"
        try:
            detail += f", body: {r.json()}"
        except Exception:
            pass
        record("Non-qwen session creation", False, detail)


def step7_llm_proxy(qwen_session_id):
    """Step 7: 验证 LLM proxy 路由正常工作"""
    log_step("STEP", "7. 验证 LLM proxy 路由")

    # 测试无 token → 应返回 401
    r = requests.post(
        f"{BASE_URL}/api/remote/llm-proxy",
        json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
    )
    record("LLM proxy without token returns 401", r.status_code == 401)

    # 测试无效 token → 应返回 401
    r = requests.post(
        f"{BASE_URL}/api/remote/llm-proxy",
        json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer invalid-token"},
    )
    record("LLM proxy with invalid token returns 401", r.status_code == 401)


def step8_invalid_workspace_type():
    """Step 8: 验证无效 workspace_type 返回 400"""
    log_step("STEP", "8. 验证输入校验")

    r = requests.get(
        f"{BASE_URL}/api/workspace/session-models?workspace_type=invalid",
        cookies={"session_token": user_token},
    )
    record("Invalid workspace_type returns 400", r.status_code == 400)

    r = requests.get(
        f"{BASE_URL}/api/workspace/session-models?workspace_type=remote",
        cookies={"session_token": user_token},
    )
    record("Remote without machine_id/session_id returns 400", r.status_code == 400)


# ── 主流程 ──────────────────────────────────────────


def run_tests():
    print("\n" + "=" * 60)
    print("  HA Model Pool E2E Test")
    print(f"  BASE_URL: {BASE_URL}  HEADLESS: {HEADLESS}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        try:
            step1_login(page)
            step2_local_session_models(page)
            step3_register_machine(page)
            ha_pool_token = step4_remote_session_models(page)

            if ha_pool_token:
                qwen_session_id = step5_create_qwen_session(ha_pool_token)
                step7_llm_proxy(qwen_session_id)
            else:
                log_step("SKIP", "5 & 7 — no ha_pool_token (no API keys configured)")

            step6_non_qwen_session()
            step8_invalid_workspace_type()

        except Exception as e:
            log_step("ERROR", str(e))
            try:
                shot(page, "error")
            except Exception:
                pass
        finally:
            api_cleanup(admin_token)
            pause(1)
            browser.close()

    # ── 结果汇总 ──
    print("\n" + "=" * 60)
    print("  Results")
    print("=" * 60)
    passed = sum(1 for _, p in results if p)
    total = len(results)
    for name, p in results:
        print(f"  {'✅' if p else '❌'} {name}")
    print(f"\n  {passed}/{total} passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
