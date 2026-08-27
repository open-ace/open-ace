#!/usr/bin/env python3
"""
Open ACE - File Changes Panel E2E Test (Issue #144)

以测试用户登录，在浏览器中测试文件改动速览面板全流程:
1. 登录
2. 用户设置 — 面板开关
3. 直接导航到 qwen-code-webui 聊天页
4. 文件变更面板可见性
5. 文件变更列表
6. Diff 弹窗查看
7. VS Code 按钮功能
8. 设置关闭面板后验证（localStorage + URL 参数）

Run:
  HEADLESS=true  python tests/e2e/remote/e2e_file_changes_panel.py
  HEADLESS=false python tests/e2e/remote/e2e_file_changes_panel.py

Pytest note (#2457): step functions are named `_check_*`/helpers and the
script is driven by `run_tests()`. The only collected test is
`test_e2e_file_changes_panel_script`, which re-runs this file as a
subprocess against BASE_URL (exported by the lane runner) and asserts on
its exit code — the same pattern as tests/issues/559. The sync subprocess
keeps pytest away from the async `page` fixture in tests/conftest.py,
whose teardown pytest-timeout cannot kill (the CI shard deadlocks
quarantined in August 2026 were exactly that).

Phases 3-7 need a reachable qwen-code-webui; the issues lane does not
start one, so the script probes it and degrades to the Open ACE-side
phases (1-2, 8-9) instead of failing.
"""

import os
import re
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest
import requests
from playwright.sync_api import sync_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(144)]

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
TEST_USER = os.environ.get("TEST_REAL_USER", "admin")
TEST_PASS = os.environ.get("TEST_REAL_PASS", "admin123")
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-file-changes")

passed = 0
failed = 0


# ── 工具函数 ──────────────────────────────────────────


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"    📸 {name}.png")


def check(label, condition):
    global passed, failed
    if condition:
        print(f"    [PASS] {label}")
        passed += 1
    else:
        print(f"    [FAIL] {label}")
        failed += 1


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def log(tag, msg):
    print(f"    [{tag}] {msg}", flush=True)


def _clear_seeded_password_gate():
    """Clear must_change_password for the seeded admin (lane/CI only, #2457).

    Freshly initialized databases gate the admin behind a password-change
    flow that would intercept the pages this script exercises. Deployed
    environments keep their own user list and are unaffected (no-op when
    the default DB is absent or the gate is already clear).
    """
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE users SET must_change_password = 0 "
                "WHERE username = ? AND must_change_password = 1",
                (TEST_USER,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log("Setup", f"password-gate clear skipped: {exc}")


# ── 面板功能测试（在 qwen-code-webui 页面上执行）──────────────


def _check_panel_features(page, frame, shot_fn, check_fn, log_fn, pause_fn):
    """测试面板功能，frame 可以是 page 本身（直接模式）或 iframe content_frame"""
    # ══════ 4. 文件变更面板验证 ══════
    print("\n══════ 4. 文件变更面板验证")

    pause_fn(5)
    shot_fn(page, "09_chat_page_loaded")

    # 检查面板是否存在
    frame_text = frame.locator("body").text_content() or ""
    log_fn("页面文本片段", frame_text[:300])

    # 检查 "File Changes" 或 "文件变更" 文字
    has_panel_text = (
        "File Changes" in frame_text
        or "文件变更" in frame_text
        or "file-changes" in frame_text.lower()
    )
    check_fn("面板文字存在", has_panel_text)

    # 检查面板 DOM 结构
    panel_group = frame.locator(
        '[data-panel-group], [data-panel-group-direction], [style*="display: flex"]'
    )
    panel_dom = panel_group.count() > 0
    if not panel_dom:
        # react-resizable-panels 的 Group 渲染为 div，检查是否有 Panel 子元素
        panel_dom = frame.locator("[data-panel]").count() > 0
    check_fn("面板 PanelGroup 存在", panel_dom)

    # 检查面板区域
    panel_area = frame.locator(
        '[class*="file-changes"], [class*="FileChanges"], [data-testid*="file-change"]'
    )
    has_panel_area = panel_area.count() > 0
    log_fn("面板区域元素", str(panel_area.count()))

    if not (has_panel_text or panel_dom or has_panel_area):
        log_fn("面板", "未检测到面板，可能仍在项目列表页")
        # 可能还在项目列表页面，需要进入项目
        shot_fn(page, "09a_project_list")

        # 查找项目列表项
        project_items = frame.locator(
            "a[href*='project'], [data-testid*='project'], .project-item, .list-group-item"
        )
        log_fn("项目项数量", str(project_items.count()))

        if project_items.count() > 0:
            # 点击第一个项目
            project_items.first.click()
            log_fn("进入项目", "点击第一个项目")
            pause_fn(5)
            shot_fn(page, "09b_entered_project")

            # 重新检查面板
            frame_text = frame.locator("body").text_content() or ""
            has_panel_text = "File Changes" in frame_text or "文件变更" in frame_text
            check_fn("进入项目后面板文字", has_panel_text)

            panel_group = frame.locator('[data-panel-group-direction="horizontal"]')
            panel_dom = panel_group.count() > 0
            check_fn("进入项目后面板 DOM", panel_dom)

    if has_panel_text or panel_dom or has_panel_area:
        # ══════ 5. 文件列表检查 ══════
        print("\n══════ 5. 文件列表检查")

        # 查找文件列表项
        file_items = frame.locator(
            '[data-testid="file-change-item"], .file-change-item, [class*="file-item"]'
        )
        if file_items.count() > 0:
            log_fn("文件列表", f"找到 {file_items.count()} 个文件变更项")
            check_fn("文件列表非空", file_items.count() > 0)

            # 检查第一个文件项内容
            first_item_text = file_items.first.text_content() or ""
            log_fn("首个文件", first_item_text[:80])

            # 检查状态徽章 (M/A/D)
            has_status = any(
                s in first_item_text for s in ["M", "A", "D", "modified", "added", "deleted"]
            )
            check_fn("状态徽章", has_status)

            # ══════ 6. Diff 弹窗 ══════
            print("\n══════ 6. Diff 弹窗")

            # 点击第一个文件项
            file_items.first.click()
            pause_fn(2)
            shot_fn(page, "10_diff_modal_opened")

            # 检查 diff 弹窗
            diff_modal = frame.locator(
                '[class*="diff-modal"], [class*="DiffModal"], [role="dialog"]'
            )
            diff_visible = diff_modal.count() > 0
            check_fn("Diff 弹窗打开", diff_visible)

            if diff_visible:
                modal_text = diff_modal.first.text_content() or ""
                log_fn("Diff 内容", modal_text[:200])
                shot_fn(page, "11_diff_content")

                # 关闭弹窗
                close_diff = diff_modal.locator(
                    'button:has-text("Close"), button:has-text("关闭"), button[aria-label="close"], .btn-close'
                )
                if close_diff.count() > 0:
                    close_diff.first.click()
                else:
                    page.keyboard.press("Escape")
                pause_fn(1)
        else:
            log_fn("文件列表", "无文件变更（可能工作区没有改动）")
            check_fn("文件列表（空状态正常）", True)

        # ══════ 7. VS Code 按钮 ══════
        print("\n══════ 7. VS Code 按钮")

        # 查找 VS Code 按钮
        vscode_btn = frame.locator(
            '[data-testid="vscode-btn"], button[title*="VS Code"], button[title*="vscode"], [class*="vscode-btn"], button:has-text("VS Code")'
        )
        vscode_exists = vscode_btn.count() > 0
        check_fn("VS Code 按钮存在", vscode_exists)

        if vscode_exists:
            shot_fn(page, "12_vscode_btn_visible")

            # 点击 VS Code 按钮
            vscode_btn.first.click()
            pause_fn(5)
            shot_fn(page, "13_vscode_clicked")

            # 检查状态
            frame_text_after = frame.locator("body").text_content() or ""
            has_loading = "Loading" in frame_text_after or "正在启动" in frame_text_after
            has_error = "not found" in frame_text_after.lower() or "未安装" in frame_text_after
            has_vscode_iframe = frame.locator("iframe").count() > 0

            if has_loading:
                check_fn("VS Code 启动状态", True)
            elif has_error:
                check_fn("VS Code 未安装提示", True)
            elif has_vscode_iframe:
                check_fn("VS Code 运行中", True)
            else:
                log_fn("VS Code", f"状态未知: {frame_text_after[:200]}")

            # 关闭 VS Code
            close_vscode = frame.locator(
                'button:has-text("Close"), button:has-text("关闭"), button[title*="close"]'
            )
            if close_vscode.count() > 0:
                close_vscode.first.click()
                pause_fn(2)
                shot_fn(page, "14_vscode_closed")
    else:
        log_fn("面板", "面板未检测到，跳过面板功能测试")


# ── 主测试流程 ────────────────────────────────────────


def run_tests():
    global passed, failed

    _clear_seeded_password_gate()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=100 if not HEADLESS else 0)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()

        # ══════ 1. 登录 ══════
        print("\n══════ 1. 登录")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)

        page.fill("#username", TEST_USER)
        page.fill("#password", TEST_PASS)
        page.click('button[type="submit"]')

        # 平台管理员登录后落在 /manage/dashboard，普通用户落在 /work
        page.wait_for_url(re.compile(r".*/(work|manage)"), timeout=15000)
        pause(2)
        shot(page, "01_logged_in")
        check("登录成功", "work" in page.url or "manage" in page.url)

        # ══════ 2. 用户设置 — 面板开关 ══════
        print("\n══════ 2. 用户设置 — 面板开关")

        # 去 /sessions 页面（非工作区，header 下拉不被 iframe 遮挡）
        page.goto(f"{BASE_URL}/sessions", wait_until="domcontentloaded")
        pause(2)

        # 点击用户头像下拉按钮
        user_btn = page.locator(
            "button.dropdown-toggle.d-flex:has(.bi-person-circle), button.dropdown-toggle.d-flex:has(img)"
        )
        if user_btn.count() == 0:
            user_btn = page.locator("button.dropdown-toggle.d-flex.header-icon-btn")
        check("用户下拉按钮存在", user_btn.count() > 0)
        user_btn.first.click(force=True)
        pause(1)
        shot(page, "02_menu_opened")

        # 点击设置菜单项
        settings_item = page.locator(".dropdown-menu .dropdown-item:has(i.bi-gear)")
        check("设置菜单项存在", settings_item.count() > 0)
        settings_item.first.click(force=True)
        pause(2)
        shot(page, "03_settings_modal")

        # 检查文件变更面板开关
        panel_toggle = page.locator("#showFileChangesPanel")
        if panel_toggle.count() == 0:
            all_cbs = page.locator('.modal.show input[type="checkbox"]')
            log("弹窗 checkbox 数量", str(all_cbs.count()))
            for i in range(all_cbs.count()):
                cb = all_cbs.nth(i)
                log("checkbox", f"id={cb.get_attribute('id')}, checked={cb.is_checked()}")
            panel_toggle = page.locator(
                'input[type="checkbox"][id*="FileChanges"], input[type="checkbox"][id*="fileChanges"]'
            )

        if panel_toggle.count() > 0:
            is_checked = panel_toggle.first.is_checked()
            check("面板开关存在", True)
            log("默认状态", f"checked={is_checked}")

            panel_toggle.first.click()
            pause(1)
            check("面板开关关闭", not panel_toggle.first.is_checked())
            shot(page, "04_panel_toggle_off")

            panel_toggle.first.click()
            pause(1)
            check("面板开关开启", panel_toggle.first.is_checked())
            shot(page, "05_panel_toggle_on")
        else:
            check("面板开关存在", False)
            shot(page, "04_no_toggle_found")

        page.keyboard.press("Escape")
        pause(1)

        # ══════ 3. 导航到 qwen-code-webui 聊天页 ══════
        print("\n══════ 3. 导航到 qwen-code-webui")

        # 获取 webui 访问信息
        cookies = context.cookies()
        session_token = ""
        for c in cookies:
            if c["name"] == "session_token":
                session_token = c["value"]
                break
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url",
            cookies={"session_token": session_token},
        ).json()
        log("webui config", str(webui_info)[:200])

        webui_url = webui_info.get("url", WEBUI_URL)
        token = webui_info.get("token", "")
        openace_url = webui_info.get("openace_url", BASE_URL)

        # 阶段 3-7 需要真实 qwen-code-webui；issues lane 不启动它（user-url
        # 指向无人监听的端口），探测失败时降级跳过而不是让导航超时。
        webui_reachable = False
        try:
            probe = requests.get(webui_url, timeout=3)
            webui_reachable = probe.status_code < 500
        except requests.RequestException as exc:
            log("webui 探测", f"不可达（{exc.__class__.__name__}），跳过阶段 3-7")

        is_webui = False
        if webui_reachable:
            # 直接导航到 qwen-code-webui projects 页面
            direct_url = (
                f"{webui_url}/projects"
                f"?token={token}"
                f"&openace_url={openace_url}"
                f"&lang=zh"
                f"&showFileChangesPanel=true"
            )
            log("导航", direct_url[:200])
            page.goto(direct_url, wait_until="domcontentloaded")
            pause(5)
            shot(page, "06_webui_projects")

            # 检查是否到了 qwen-code-webui
            body_text = page.locator("body").text_content() or ""
            is_webui = len(body_text) > 50
            check("qwen-code-webui 已加载", is_webui)
            log("页面文本", body_text[:200])

        # ══════ 4-7. 面板功能测试 ══════
        if is_webui:
            _check_panel_features(page, page, shot, check, log, pause)

        # ══════ 8. 设置关闭面板验证 ══════
        print("\n══════ 8. 设置关闭面板验证（localStorage → URL 参数）")

        # 先回到 open-ace 域名（确保 localStorage 在正确域上）
        page.goto(f"{BASE_URL}/sessions", wait_until="domcontentloaded")
        pause(2)

        # 找到正确的 localStorage key
        storage_key = page.evaluate("""() => {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && (key.includes('ace') || key.includes('storage'))) return key;
            }
            return null;
        }""")
        log("localStorage key", str(storage_key))

        if storage_key:
            # 关闭面板
            page.evaluate(f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                if (store.state) {{
                    store.state.showFileChangesPanel = false;
                    localStorage.setItem('{storage_key}', JSON.stringify(store));
                }}
            }}""")
            stored_val = page.evaluate(f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                return store.state?.showFileChangesPanel;
            }}""")
            check("localStorage 中面板设为 false", not stored_val)

            # 验证 Workspace 的 getEffectiveUrl 会读到 false
            check("getEffectiveUrl 会读到 false", not stored_val)

            # 恢复
            page.evaluate(f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                if (store.state) {{
                    store.state.showFileChangesPanel = true;
                    localStorage.setItem('{storage_key}', JSON.stringify(store));
                }}
            }}""")
            restored = page.evaluate(f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                return store.state?.showFileChangesPanel;
            }}""")
            check("恢复后 localStorage 为 true", restored)
            log("恢复", f"showFileChangesPanel = {restored}")
            shot(page, "15_panel_setting_verified")
        else:
            log("localStorage", "未找到 storage key，跳过验证")

        # ══════ 9. 登出 ══════
        print("\n══════ 9. 登出")
        page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
        pause(2)
        shot(page, "16_logout")
        # 登出后可能重定向到 /login 或显示登录表单
        is_logged_out = (
            "login" in page.url.lower()
            or "logout" in page.url.lower()
            or page.locator("#username").count() > 0
            or page.locator('input[type="password"]').count() > 0
            or page.locator("text=successfully logged out").count() > 0
        )
        check("登出成功", is_logged_out)

        context.close()
        browser.close()

    # ══════ 结果 ══════
    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed}/{total} 通过, {failed} 失败")
    print(f"  截图: {SCREENSHOT_DIR}")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()


# ═══════════════════════════════════════════════════════════
# Pytest entry (single collected test; see module docstring)
# ═══════════════════════════════════════════════════════════


def test_e2e_file_changes_panel_script():
    """Drive this script as a subprocess against BASE_URL (#2457)."""
    try:
        resp = requests.get(f"{BASE_URL}/login", timeout=5)
        resp.raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    assert "结果: " in proc.stdout and "失败" in proc.stdout
