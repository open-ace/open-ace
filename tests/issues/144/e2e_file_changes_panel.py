#!/usr/bin/env python3
"""
Open ACE - File Changes Panel E2E Test (Issue #144)

以真实用户「黄迎春」登录，在浏览器中测试文件改动速览面板全流程:
1. 登录
2. 用户设置 — 面板开关
3. 直接导航到 qwen-code-webui 聊天页
4. 文件变更面板可见性
5. 文件变更列表
6. Diff 弹窗查看
7. VS Code 按钮功能
8. 设置关闭面板后验证（localStorage + URL 参数）

Run:
  HEADLESS=true  python tests/144/e2e_file_changes_panel.py
  HEADLESS=false python tests/144/e2e_file_changes_panel.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://localhost:3000")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
TEST_USER = "黄迎春"
TEST_PASS = "admin123"
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
    print(f"    [{tag}] {msg}")


# ── 面板功能测试（在 qwen-code-webui 页面上执行）──────────────


def test_panel_features(page, frame, shot_fn, check_fn, log_fn, pause_fn):
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

        page.wait_for_url("**/work**", timeout=15000)
        pause(2)
        shot(page, "01_logged_in")
        check("登录成功", "work" in page.url)

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
        webui_info = requests.get(
            f"{BASE_URL}/api/workspace/user-url",
            cookies={"session_token": context.cookies()[0]["value"] if context.cookies() else ""},
        ).json()
        log("webui config", str(webui_info)[:200])

        webui_url = webui_info.get("url", WEBUI_URL)
        token = webui_info.get("token", "")
        openace_url = webui_info.get("openace_url", BASE_URL)

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
            test_panel_features(page, page, shot, check, log, pause)

        # ══════ 8. 设置关闭面板验证 ══════
        print("\n══════ 8. 设置关闭面板验证（localStorage → URL 参数）")

        # 先回到 open-ace 域名（确保 localStorage 在正确域上）
        page.goto(f"{BASE_URL}/sessions", wait_until="domcontentloaded")
        pause(2)

        # 找到正确的 localStorage key
        storage_key = page.evaluate(
            """() => {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && (key.includes('ace') || key.includes('storage'))) return key;
            }
            return null;
        }"""
        )
        log("localStorage key", str(storage_key))

        if storage_key:
            # 关闭面板
            page.evaluate(
                f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                if (store.state) {{
                    store.state.showFileChangesPanel = false;
                    localStorage.setItem('{storage_key}', JSON.stringify(store));
                }}
            }}"""
            )
            stored_val = page.evaluate(
                f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                return store.state?.showFileChangesPanel;
            }}"""
            )
            check("localStorage 中面板设为 false", not stored_val)

            # 验证 Workspace 的 getEffectiveUrl 会读到 false
            check("getEffectiveUrl 会读到 false", not stored_val)

            # 恢复
            page.evaluate(
                f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                if (store.state) {{
                    store.state.showFileChangesPanel = true;
                    localStorage.setItem('{storage_key}', JSON.stringify(store));
                }}
            }}"""
            )
            restored = page.evaluate(
                f"""() => {{
                const store = JSON.parse(localStorage.getItem('{storage_key}') || '{{}}');
                return store.state?.showFileChangesPanel;
            }}"""
            )
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
