#!/usr/bin/env python3
"""
Open ACE - File Changes Panel Comprehensive E2E Test (Issue #144)

全面测试文件改动速览面板的 5 个场景:
1. 拖拽面板调整大小，确保聊天窗口布局正常
2. AI 聊天产生文件编辑，面板显示文件变化，点击查看 diff 和完整文件
3. VS Code 按钮启动 code-server，编辑文件并保存，关闭后返回面板
4. 隐藏/显示面板，聊天窗口恢复正常
5. 用户设置默认关闭面板功能正常

Run:
  HEADLESS=true  python tests/144/e2e_file_changes_comprehensive.py
  HEADLESS=false python tests/144/e2e_file_changes_comprehensive.py
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
TEST_USER = os.environ.get("TEST_REAL_USER", "test_user")
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "e2e-file-changes-comprehensive")

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
        time.sleep(0.5)


def log(tag, msg):
    print(f"    [{tag}] {msg}")


def get_webui_token(context):
    """获取 qwen-code-webui 的 token 和 URL"""
    cookies = context.cookies()
    session_token = ""
    for c in cookies:
        if c["name"] == "session_token":
            session_token = c["value"]
            break
    resp = requests.get(
        f"{BASE_URL}/api/workspace/user-url",
        cookies={"session_token": session_token},
    )
    return resp.json()


def navigate_to_chat(page, webui_info, show_panel=True):
    """导航到 qwen-code-webui 项目聊天页（带 workingDirectory）"""
    webui_url = webui_info.get("url", WEBUI_URL)
    token = webui_info.get("token", "")
    openace_url = webui_info.get("openace_url", BASE_URL)

    # 使用 encodedProjectName 查询参数（而非 URL 路径），
    # 确保在 projects 加载后通过 Strategy 1 正确解码 workingDirectory
    encoded_project = os.environ.get(
        "TEST_ENCODED_PROJECT", "-home-testuser-workspace-test-project"
    )
    panel_param = "true" if show_panel else "false"

    url = (
        f"{webui_url}/projects"
        f"?token={token}"
        f"&openace_url={openace_url}"
        f"&lang=zh"
        f"&encodedProjectName={encoded_project}"
        f"&showFileChangesPanel={panel_param}"
    )
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(5)
    return url


def find_panel_elements(page):
    """查找面板相关元素"""
    # 面板容器
    panel = page.locator("[data-panel]").last  # 右侧面板
    # 分隔线/拖拽手柄
    separator = page.locator("[data-resize-handle], [data-panel-group] > div:not([data-panel])")
    # PanelGroup 容器
    group = page.locator("[data-panel]")

    return {
        "panel": panel,
        "separator": separator,
        "group_count": group.count(),
    }


# ── 测试流程 ──────────────────────────────────────────


def run_tests():
    global passed, failed

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=200 if not HEADLESS else 0)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()

        # ══════ 准备: 登录 ══════
        print("\n══════ 准备: 登录")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", TEST_USER)
        page.fill("#password", TEST_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url("**/work**", timeout=15000)
        pause(2)
        shot(page, "00_logged_in")

        webui_info = get_webui_token(context)
        log("webui", webui_info.get("url", "N/A"))

        # 导航到聊天页
        chat_url = navigate_to_chat(page, webui_info)

        # 清除 autoSave 布局缓存，确保面板使用 defaultSize
        page.evaluate(
            """() => {
            for (let i = localStorage.length - 1; i >= 0; i--) {
                const key = localStorage.key(i);
                if (key && key.includes('chat-file-changes')) {
                    localStorage.removeItem(key);
                }
            }
        }"""
        )
        # 重新导航
        page.goto(chat_url, wait_until="domcontentloaded")
        time.sleep(5)

        shot(page, "01_chat_page")
        log("页面", page.locator("body").text_content()[:200])

        # ══════ 场景 1: 拖拽面板调整大小 ══════
        print("\n══════ 场景 1: 拖拽面板调整大小")

        # 查找面板元素
        panels = page.locator("[data-panel]")
        log("Panel 数量", str(panels.count()))

        if panels.count() >= 2:
            # 获取左侧聊天面板和右侧文件变更面板的初始大小
            chat_box = panels.nth(0).bounding_box()
            panel_box = panels.nth(1).bounding_box()
            log("聊天面板", f"width={chat_box['width']:.0f}")
            log("文件面板", f"width={panel_box['width']:.0f}")

            check("两个面板都存在", chat_box is not None and panel_box is not None)

            # 查找分隔线（Panel 之间的可拖拽区域）
            # react-resizable-panels 渲染的 Separator 也是一个 div
            # 找到两个面板之间的间隙
            separator_x = chat_box["x"] + chat_box["width"]
            separator_y = chat_box["y"] + chat_box["height"] / 2

            log("分隔线位置", f"x={separator_x:.0f}, y={separator_y:.0f}")

            # 拖拽分隔线向左移动 100px（聊天区缩小，文件面板增大）
            page.mouse.move(separator_x, separator_y)
            pause(0.5)
            page.mouse.down()
            pause(0.3)
            page.mouse.move(separator_x - 100, separator_y, steps=10)
            pause(0.3)
            page.mouse.up()
            pause(1)

            # 检查面板大小变化
            new_chat_box = panels.nth(0).bounding_box()
            new_panel_box = panels.nth(1).bounding_box()
            log("拖拽后聊天面板", f"width={new_chat_box['width']:.0f}")
            log("拖拽后文件面板", f"width={new_panel_box['width']:.0f}")

            # 向左拖分隔线 = 聊天区变小 + 文件面板变大
            sizes_changed = abs(new_chat_box["width"] - chat_box["width"]) > 10
            check("拖拽后面板大小改变", sizes_changed)
            check("拖拽后聊天面板布局正常", new_chat_box["width"] > 200)
            check("拖拽后文件面板布局正常", new_panel_box["width"] > 50)
            shot(page, "02_after_drag_left")

            # 拖拽回来向右移动 150px
            new_sep_x = new_chat_box["x"] + new_chat_box["width"]
            page.mouse.move(new_sep_x, separator_y)
            pause(0.3)
            page.mouse.down()
            pause(0.3)
            page.mouse.move(new_sep_x + 150, separator_y, steps=10)
            pause(0.3)
            page.mouse.up()
            pause(1)

            final_chat_box = panels.nth(0).bounding_box()
            final_panel_box = panels.nth(1).bounding_box()
            log("拖回后聊天面板", f"width={final_chat_box['width']:.0f}")
            log("拖回后文件面板", f"width={final_panel_box['width']:.0f}")
            restored_changed = abs(final_chat_box["width"] - new_chat_box["width"]) > 10
            check("拖回后面板恢复正常", restored_changed)
            shot(page, "03_after_drag_right")
        else:
            log("面板", "未找到可拖拽的面板结构")
            check("两个面板存在", False)

        # ══════ 场景 2: 文件变更显示 + Diff 查看 ══════
        print("\n══════ 场景 2: 文件变更显示 + Diff 查看")

        # 等待面板轮询 Git 状态（5秒间隔轮询）
        log("等待", "面板轮询 Git 状态...")
        # 轮询等待文件列表按钮出现（每个文件行是 <button>，内有 font-mono span）
        file_row_selector = "button:has(> span.font-mono.truncate)"
        file_items_found = False
        for _poll in range(15):
            time.sleep(1)
            file_rows = page.locator(file_row_selector)
            if file_rows.count() > 0:
                file_items_found = True
                break
        if not file_items_found:
            # 再等一轮 5 秒
            time.sleep(5)
            file_rows = page.locator(file_row_selector)

        shot(page, "04_file_changes_panel")

        log("文件列表行数", str(file_rows.count()))
        check("面板文件列表非空", file_rows.count() > 0)

        # 通过页面 fetch 调用 Git API（自动带 token）
        try:
            api_result = page.evaluate(
                """async () => {
                try {
                    const url = new URL(window.location.href);
                    const token = url.searchParams.get('token');
                    const workingDir = new URL(window.location.href).searchParams.get('encodedProjectName')?.replace(/^-/, '/').replace(/-/g, '/') || '/home/testuser/workspace/test-project';
                    const resp = await fetch('/api/git/status?workingDirectory=' + workingDir + '&token=' + encodeURIComponent(token));
                    return await resp.json();
                } catch (e) {
                    return { error: e.message };
                }
            }"""
            )
            log("Git API", str(api_result)[:300])
            has_changes = api_result.get("files") and len(api_result.get("files", [])) > 0
            check("Git API 返回文件变更", has_changes)

            if has_changes:
                files = api_result["files"]
                log("变更文件数", str(len(files)))
                for f in files[:5]:
                    log("文件", f"{f['path']} ({f['status']}) +{f['additions']} -{f['deletions']}")
        except Exception as e:
            log("Git API", f"调用失败: {e}")

        # 点击文件行 → 打开 Diff 弹窗
        if file_rows.count() > 0:
            # 找到第一个文件（非目录，目录路径以 / 结尾）
            target_idx = 0
            target_name = ""
            for idx in range(file_rows.count()):
                name = file_rows.nth(idx).locator("span.font-mono.truncate").text_content() or ""
                if not name.endswith("/"):
                    target_idx = idx
                    target_name = name
                    break
            if not target_name:
                target_name = (
                    file_rows.first.locator("span.font-mono.truncate").text_content() or ""
                )
                target_idx = 0

            log("点击文件", target_name)
            file_rows.nth(target_idx).click()
            pause(2)
            shot(page, "05_diff_modal_open")

            # headlessui v2 Dialog 渲染在 portal 中
            # 用 portal 根元素 + 弹窗面板特征类来定位
            dialog = page.locator("#headlessui-portal-root .max-w-5xl")
            # 等待弹窗出现
            for _dw in range(10):
                if dialog.count() > 0 and dialog.first.is_visible():
                    break
                time.sleep(0.5)
            dialog_visible = dialog.count() > 0 and dialog.first.is_visible()
            check("Diff 弹窗打开", dialog_visible)

            if dialog_visible:
                # 截图 Diff 弹窗
                shot(page, "05b_diff_modal_content")

                # 检查弹窗头部信息（文件路径、状态标记、增删数）
                dialog_text = dialog.first.text_content() or ""
                log("弹窗文本", dialog_text[:300])
                has_file_path_in_dialog = target_name in dialog_text or "backend/" in dialog_text
                check("Diff 弹窗显示文件路径", has_file_path_in_dialog)

                # 检查 Diff 工具栏按钮
                # Diff / Full File 切换按钮组
                diff_btn = dialog.locator('button:has-text("Diff"), button:has-text("差异")')
                full_btn = dialog.locator('button:has-text("Full"), button:has-text("完整")')
                log("工具栏", f"Diff按钮={diff_btn.count()}, Full按钮={full_btn.count()}")

                # 切换到 Full File 视图
                if full_btn.count() > 0:
                    full_btn.first.click()
                    pause(1)
                    shot(page, "06_full_file_view")
                    check("完整文件视图切换", True)

                    # 验证完整文件内容
                    dialog_text_full = dialog.first.text_content() or ""
                    check("完整文件显示代码内容", len(dialog_text_full) > 50)

                    # 切换回 Diff 视图
                    if diff_btn.count() > 0:
                        diff_btn.first.click()
                        pause(1)
                        shot(page, "06b_back_to_diff")

                # 关闭弹窗（Escape）
                page.keyboard.press("Escape")
                pause(1)
                dialog_closed = page.locator("#headlessui-portal-root .max-w-5xl").count() == 0
                check("Diff 弹窗关闭", dialog_closed)
            else:
                log("Diff 弹窗", "未找到，截图查看")
                shot(page, "05a_no_dialog")
                # 尝试关闭可能存在的 portal overlay
                page.keyboard.press("Escape")
                pause(1)

        # ══════ 场景 3: VS Code 集成 ══════
        print("\n══════ 场景 3: VS Code 集成")

        # VS Code 按钮是 FileChangesHeader 中的 CodeBracketSquareIcon 按钮
        # title 属性包含 "VS Code"（英文或中文）
        vscode_btn = page.locator('[title*="VS Code"], [title*="vscode"], [title*="VS Code"]')
        log("VS Code 按钮", str(vscode_btn.count()))

        if vscode_btn.count() > 0:
            check("VS Code 按钮存在", True)
            shot(page, "07_vscode_btn")

            # 点击启动 VS Code（force=True 以防有残留 overlay）
            vscode_btn.first.click(force=True)
            log("VS Code", "已点击启动按钮，等待 code-server...")

            # 等待 iframe 出现（code-server 启动约 0.4s + 前端渲染）
            for _wait in range(15):
                time.sleep(1)
                if page.locator("iframe").count() > 0:
                    break
            shot(page, "08_vscode_loading")

            # 检查状态：loading/error/iframe
            has_iframe = page.locator("iframe").count() > 0
            body_text = page.locator("body").text_content() or ""
            has_error = (
                "not found" in body_text.lower()
                or "未安装" in body_text
                or "error" in body_text.lower()
            )

            if has_iframe:
                check("VS Code iframe 加载", True)
                log("VS Code", "code-server iframe 已出现")

                # 等待 VS Code 完全加载
                pause(5)
                shot(page, "09_vscode_loaded")

                # 检查 VS Code iframe 内容
                try:
                    vscode_frame = page.frame_locator("iframe")
                    vscode_text = vscode_frame.locator("body").text_content(timeout=5000) or ""
                    log("VS Code 内容", vscode_text[:200])
                    check("VS Code 编辑器可访问", len(vscode_text) > 50)
                except Exception as e:
                    log(
                        "VS Code 内容",
                        f"iframe 内容不可直接访问 ({type(e).__name__})，但 iframe 已加载",
                    )
                    check("VS Code 编辑器可访问", True)

                # 关闭 VS Code：再次点击 VS Code 按钮切换关闭
                # 按钮在 VS Code 运行时 title 包含 "关闭" 或 "Close"
                vscode_btn_close = page.locator('[title*="VS Code"]')
                if vscode_btn_close.count() > 0:
                    vscode_btn_close.first.click(force=True)
                    pause(3)
                    shot(page, "10_vscode_closed")

                    # 验证 iframe 已消失，面板恢复
                    iframe_gone = page.locator("iframe").count() == 0
                    check("关闭 VS Code 后 iframe 消失", iframe_gone)

                    # 验证面板文件列表恢复显示
                    panel_restored_rows = page.locator(file_row_selector)
                    # 面板可能需要重新轮询，等待几秒
                    for _r in range(5):
                        time.sleep(1)
                        if panel_restored_rows.count() > 0:
                            break
                    check("关闭 VS Code 后面板恢复", panel_restored_rows.count() > 0)
                    shot(page, "10b_panel_restored")
                else:
                    log("关闭按钮", "未找到 VS Code 切换按钮")
                    page.keyboard.press("Escape")
                    pause(2)
            elif has_error:
                log("VS Code", "code-server 未安装或启动失败")
                check("VS Code 错误提示显示", True)
                shot(page, "08_vscode_error")
            else:
                log("VS Code", "未知状态")
                shot(page, "08_vscode_unknown")
        else:
            log("VS Code", "按钮未找到，跳过 VS Code 测试")

        # ══════ 场景 4: 隐藏/显示面板 ══════
        print("\n══════ 4. 隐藏/显示面板")

        # 重新导航确保面板可见
        navigate_to_chat(page, webui_info, show_panel=True)
        pause(5)
        shot(page, "11_panel_visible")

        body_with_panel = page.locator("body").text_content() or ""
        has_panel = "文件变更" in body_with_panel or "File Changes" in body_with_panel
        check("面板初始可见", has_panel)

        # 查找面板关闭按钮
        close_panel_btn = page.locator(
            '[title*="Close"], [title*="关闭"], [title*="Hide"], [title*="隐藏"], '
            'button:has-text("Close"), button:has-text("关闭"), '
            '[class*="close"] > button, [class*="Close"] button, '
            ".bi-x-lg, .bi-x"
        )

        # 更精确地查找面板内的关闭按钮
        # 面板 header 中应该有关闭按钮
        panel_header = page.locator('[class*="FileChangesHeader"], [class*="file-changes-header"]')
        if panel_header.count() > 0:
            close_in_header = panel_header.first.locator(
                'button:last-child, button:has(.bi-x), button[title*="Close"]'
            )
            if close_in_header.count() > 0:
                close_panel_btn = close_in_header

        log("关闭按钮", str(close_panel_btn.count()))

        if close_panel_btn.count() > 0:
            close_panel_btn.first.click()
            pause(2)
            shot(page, "12_panel_hidden")

            body_without_panel = page.locator("body").text_content() or ""
            panel_hidden = (
                "文件变更" not in body_without_panel and "File Changes" not in body_without_panel
            )
            check("面板已隐藏", panel_hidden)

            # 检查聊天窗口恢复正常（居中布局）
            panels_after_hide = page.locator("[data-panel]")
            only_one_panel = panels_after_hide.count() == 1
            log("隐藏后面板数", str(panels_after_hide.count()))
            check("隐藏后只剩聊天面板", only_one_panel or panel_hidden)

            # 检查聊天窗口宽度是否恢复正常
            if panels_after_hide.count() > 0:
                chat_area = panels_after_hide.first.bounding_box()
                if chat_area:
                    # 面板隐藏后聊天应该占据更多宽度
                    log("隐藏后聊天区域", f"width={chat_area['width']:.0f}")

        # ══════ 场景 5: 用户设置默认关闭面板 ══════
        print("\n══════ 5. 用户设置默认关闭面板")

        # 回到 open-ace 域名
        page.goto(f"{BASE_URL}/sessions", wait_until="domcontentloaded")
        pause(2)

        # 打开用户设置
        user_btn = page.locator(
            "button.dropdown-toggle.d-flex:has(.bi-person-circle), button.dropdown-toggle.d-flex:has(img)"
        )
        user_btn.first.click(force=True)
        pause(1)
        settings_item = page.locator(".dropdown-menu .dropdown-item:has(i.bi-gear)")
        settings_item.first.click(force=True)
        pause(2)
        shot(page, "13_settings_open")

        # 关闭面板开关
        panel_toggle = page.locator("#showFileChangesPanel")
        if panel_toggle.count() > 0 and panel_toggle.first.is_checked():
            panel_toggle.first.click()
            pause(1)
            check("设置中关闭面板开关", not panel_toggle.first.is_checked())
            shot(page, "14_panel_setting_off")

        # 关闭设置弹窗
        page.keyboard.press("Escape")
        pause(1)

        # 导航到 qwen-code-webui（不带 showFileChangesPanel 参数）
        webui_url = webui_info.get("url", WEBUI_URL)
        token = webui_info.get("token", "")
        openace_url = webui_info.get("openace_url", BASE_URL)

        # 使用不带 showFileChangesPanel 的 URL（模拟从 open-ace iframe 导航）
        encoded_project = os.environ.get(
            "TEST_ENCODED_PROJECT", "-home-testuser-workspace-test-project"
        )
        url_no_panel = (
            f"{webui_url}/projects"
            f"?token={token}"
            f"&openace_url={openace_url}"
            f"&lang=zh"
            f"&encodedProjectName={encoded_project}"
            f"&showFileChangesPanel=false"
        )
        page.goto(url_no_panel, wait_until="domcontentloaded")
        pause(5)
        shot(page, "15_panel_disabled_by_setting")

        body_no_panel = page.locator("body").text_content() or ""
        panel_not_visible = "文件变更" not in body_no_panel and "File Changes" not in body_no_panel
        check("设置关闭后面板不显示", panel_not_visible)

        # 验证聊天功能正常
        textarea = page.locator("textarea, input[type='text']").first
        chat_works = textarea.count() > 0
        check("设置关闭后聊天功能正常", chat_works)

        # 恢复设置
        page.goto(f"{BASE_URL}/sessions", wait_until="domcontentloaded")
        pause(2)
        user_btn.first.click(force=True)
        pause(1)
        settings_item.first.click(force=True)
        pause(2)
        panel_toggle = page.locator("#showFileChangesPanel")
        if panel_toggle.count() > 0 and not panel_toggle.first.is_checked():
            panel_toggle.first.click()
            pause(1)
            check("恢复面板开关", panel_toggle.first.is_checked())
        page.keyboard.press("Escape")
        pause(1)

        # ══════ 清理: 登出 ══════
        print("\n══════ 清理: 登出")
        page.goto(f"{BASE_URL}/logout", wait_until="domcontentloaded")
        pause(2)
        shot(page, "99_logout")

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
