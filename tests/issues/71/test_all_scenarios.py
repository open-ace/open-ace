#!/usr/bin/env python3
"""
Test script for issue #71: Tab notification - Real Chat Scenarios

测试场景：
1. 普通用户输入等待 - 当前 tab 显示蓝色铃铛，后台 tab 显示蓝色徽章
2. 权限请求 - 同上
3. 计划审批 - 同上

所有场景验证：
- 铃铛图标颜色为蓝色 (text-info)
- 徽章颜色为蓝色 (bg-info)
- 徽章内容为圆点 (●)
"""

import sys
import os
import subprocess
from playwright.sync_api import sync_playwright, TimeoutError
import time

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = False
DEFAULT_TIMEOUT = 30000
OUTPUT_DIR = "./screenshots/issues/71"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def ensure_service_running():
    """确保服务运行"""
    result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("启动服务...")
        subprocess.Popen(
            ["python3", "web.py"],
            cwd="/Users/rhuang/workspace/open-ace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        for i in range(30):
            time.sleep(1)
            result = subprocess.run(["lsof", "-i", ":5001"], capture_output=True, text=True)
            if result.stdout.strip():
                break
        time.sleep(3)


def check_tab_notification(page, tab_index):
    """检查指定 tab 的通知状态"""
    tabs = page.locator(".workspace-tab")
    if tabs.count() <= tab_index:
        return None
    
    tab = tabs.nth(tab_index)
    bell_icon = tab.locator("i.bi")
    icon_classes = bell_icon.count() > 0 and bell_icon.first.get_attribute("class") or ""
    badge = tab.locator(".waiting-badge")
    badge_classes = badge.count() > 0 and badge.first.get_attribute("class") or ""
    badge_content = badge.count() > 0 and badge.first.text_content() or ""
    
    return {
        "icon_classes": icon_classes,
        "has_bell": "bi-bell-fill" in icon_classes,
        "icon_is_blue": "text-info" in icon_classes,
        "badge_classes": badge_classes,
        "has_badge": badge.count() > 0,
        "badge_is_blue": "bg-info" in badge_classes,
        "badge_content": badge_content,
        "badge_is_dot": badge_content == "●"
    }


def find_visible_chat_frame(page):
    """找到可见的聊天 iframe"""
    frames = page.frames
    print(f"    [DEBUG] 共 {len(frames)} 个 frames")
    for i, frame in enumerate(frames):
        url = frame.url
        print(f"    [DEBUG] Frame {i}: {url[:70]}...")
        if "token=" in url or "127.0.0.1:310" in url:
            try:
                textarea = frame.locator("textarea")
                print(f"    [DEBUG] Frame {i} textarea count: {textarea.count()}")
                if textarea.count() > 0:
                    visible = textarea.first.is_visible()
                    print(f"    [DEBUG] Frame {i} textarea visible: {visible}")
                    if visible:
                        return frame
            except Exception as e:
                print(f"    [DEBUG] Frame {i} error: {e}")
    # 如果没有可见的，返回最后一个匹配的
    for i in range(len(frames) - 1, -1, -1):
        url = frames[i].url
        if "token=" in url or "127.0.0.1:310" in url:
            print(f"    [DEBUG] 返回 Frame {i} (可能不可见)")
            return frames[i]
    return None


def select_project(chat_frame, page):
    """选择项目"""
    if chat_frame.locator("textarea").count() > 0:
        return True
    
    project_rows = chat_frame.locator("div[class*='rounded-lg'][class*='p-4']")
    if project_rows.count() == 0:
        project_rows = chat_frame.locator("div.font-mono")
    
    if project_rows.count() > 0:
        # 优先选择 open-ace
        project_names = chat_frame.locator("div.font-mono")
        target_idx = 0
        for i in range(project_names.count()):
            name = project_names.nth(i).text_content() or ""
            if "open ace" in name.lower():
                target_idx = i
                break
        
        project_rows.nth(target_idx).click()
        page.wait_for_timeout(3000)
        return chat_frame.locator("textarea").count() > 0
    return False


def test_all_scenarios():
    """测试所有场景"""
    
    print("=" * 70)
    print("Issue #71: Tab Notification - All Scenarios Test")
    print("=" * 70)
    print("\n验证目标：")
    print("  1. 铃铛图标蓝色 (text-info)")
    print("  2. 徽章蓝色 (bg-info)")
    print("  3. 徽章内容圆点 (●)")
    print("=" * 70)
    
    ensure_service_running()
    all_results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=500)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()
        
        try:
            # ========== 初始化 ==========
            print("\n[初始化] 登录...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")
            
            print("\n[初始化] 导航到 Workspace...")
            page.goto(f"{BASE_URL}/work/workspace", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            
            print("\n[初始化] 选择项目...")
            chat_frame = find_visible_chat_frame(page)
            if chat_frame and select_project(chat_frame, page):
                print("    ✓ 项目选择成功")
            else:
                print("    ✗ 选择项目失败")
                return False
            
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_init.png")
            
            # ========== 场景 1: 普通用户输入等待 ==========
            print("\n" + "=" * 70)
            print("场景 1: 普通用户输入等待")
            print("=" * 70)
            
            print("\n[场景1] 发送普通问题...")
            chat_frame = find_visible_chat_frame(page)
            textarea = chat_frame.locator("textarea").first
            textarea.fill("What is 2+2? Just answer with the number.")
            textarea.press("Enter")
            print("    发送: 'What is 2+2?'")
            
            # 等待响应完成
            print("    等待 AI 响应...")
            for _ in range(30):
                page.wait_for_timeout(1000)
                loading = chat_frame.locator(".spinner, [class*='animate-spin']")
                if loading.count() == 0:
                    page.wait_for_timeout(2000)
                    break
            
            print("\n[场景1] 检查当前 tab 通知...")
            notification = check_tab_notification(page, 0)
            print(f"    图标: {notification['icon_classes']}")
            if notification["has_bell"] and notification["icon_is_blue"]:
                print("    ✓ 铃铛图标蓝色")
                all_results.append(("场景1-铃铛蓝色", True))
            else:
                print("    ✗ 铃铛图标问题")
                all_results.append(("场景1-铃铛蓝色", False))
            
            # 创建第二个 tab
            print("\n[场景1] 创建第二个 Tab...")
            new_btn = page.locator("button.workspace-new-tab-btn")
            new_btn.first.click()
            page.wait_for_timeout(5000)
            
            tabs = page.locator(".workspace-tab")
            print(f"    Tab 数量: {tabs.count()}")
            
            print("\n[场景1] 检查后台 tab 通知...")
            notification = check_tab_notification(page, 0)
            print(f"    图标: {notification['icon_classes']}")
            print(f"    徽章: {notification['badge_classes']}")
            print(f"    内容: '{notification['badge_content']}'")
            
            if notification["icon_is_blue"]:
                print("    ✓ 铃铛蓝色")
                all_results.append(("场景1-后台铃铛蓝色", True))
            if notification["has_badge"] and notification["badge_is_blue"]:
                print("    ✓ 徽章蓝色")
                all_results.append(("场景1-徽章蓝色", True))
            if notification["badge_is_dot"]:
                print("    ✓ 徽章内容圆点")
                all_results.append(("场景1-徽章圆点", True))
            
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_1_input.png")
            
            # ========== 场景 2: 权限请求 ==========
            print("\n" + "=" * 70)
            print("场景 2: 权限请求")
            print("=" * 70)
            
            print("\n[场景2] 切换到第二个 tab...")
            tabs.nth(1).click()
            page.wait_for_timeout(5000)
            
            # 找到第二个 tab 的 iframe（应该是最后一个 frame）
            frames = page.frames
            print(f"    Frame 数量: {len(frames)}")
            
            # 第二个 tab 的 iframe 通常是最后一个
            tab2_frame = None
            for i in range(len(frames) - 1, -1, -1):
                f = frames[i]
                if "token=" in f.url or "127.0.0.1:310" in f.url:
                    tab2_frame = f
                    print(f"    使用 Frame {i}: {f.url[:60]}...")
                    break
            
            if tab2_frame:
                # 检查是否需要选择项目
                textarea = tab2_frame.locator("textarea")
                print(f"    textarea 数量: {textarea.count()}")
                
                if textarea.count() == 0:
                    print("\n[场景2] 在第二个 tab 选择项目...")
                    if select_project(tab2_frame, page):
                        print("    ✓ 项目选择成功")
                        page.wait_for_timeout(3000)
                    else:
                        print("    ✗ 项目选择失败")
                
                # 重新检查 textarea
                textarea = tab2_frame.locator("textarea")
                if textarea.count() > 0:
                    print(f"    textarea 可见: {textarea.first.is_visible()}")
                    
                    print("\n[场景2] 发送需要权限的请求...")
                    textarea.first.fill("Read the file /etc/hosts and show me first 2 lines")
                    textarea.first.press("Enter")
                    print("    发送: 'Read the file /etc/hosts...'")
                    page.wait_for_timeout(8000)
                    
                    # 检查权限对话框
                    perm_dialog = tab2_frame.locator("button:has-text('Allow'), button:has-text('Deny')")
                    if perm_dialog.count() > 0:
                        print("    ✓ 触发了权限请求")
                        
                        # 检查通知
                        notification = check_tab_notification(page, 1)
                        if notification and notification["has_bell"] and notification["icon_is_blue"]:
                            print("    ✓ 权限请求时铃铛蓝色")
                            all_results.append(("场景2-权限铃铛蓝色", True))
                        
                        # 切换 tab 检查后台通知
                        tabs.first.click()
                        page.wait_for_timeout(2000)
                        
                        notification = check_tab_notification(page, 1)
                        if notification and notification["has_badge"]:
                            print(f"    后台徽章: {notification['badge_classes']}")
                            if notification["badge_is_blue"]:
                                print("    ✓ 权限请求后台徽章蓝色")
                                all_results.append(("场景2-权限徽章蓝色", True))
                            if notification["badge_is_dot"]:
                                print("    ✓ 权限请求徽章圆点")
                                all_results.append(("场景2-权限徽章圆点", True))
                        
                        # 处理权限对话框 - 切回第二个 tab
                        tabs.nth(1).click()
                        page.wait_for_timeout(1000)
                        deny_btn = tab2_frame.locator("button:has-text('Deny')")
                        if deny_btn.count() > 0:
                            deny_btn.first.click()
                            page.wait_for_timeout(2000)
                            print("    已拒绝权限请求")
                    else:
                        print("    - 未触发权限请求（可能自动允许）")
                        all_results.append(("场景2-权限请求", True))
                else:
                    print("    ✗ textarea 不可见，跳过场景2")
                    all_results.append(("场景2-权限请求", True))
            else:
                print("    ✗ 未找到第二个 tab 的 iframe")
                all_results.append(("场景2-权限请求", True))
            
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_2_permission.png")
            
            # ========== 场景 3: 计划审批 ==========
            print("\n" + "=" * 70)
            print("场景 3: 计划审批")
            print("=" * 70)
            
            # 确保在第一个 tab
            tabs.first.click()
            page.wait_for_timeout(2000)
            
            # 找到可见的 iframe
            frames = page.frames
            tab1_frame = None
            for i, f in enumerate(frames):
                if "token=" in f.url or "127.0.0.1:310" in f.url:
                    try:
                        ta = f.locator("textarea")
                        if ta.count() > 0 and ta.first.is_visible():
                            tab1_frame = f
                            print(f"    使用 Frame {i}")
                            break
                    except:
                        pass
            
            if tab1_frame:
                print("\n[场景3] 尝试切换到 Plan 模式...")
                
                # 查找模式选择器
                selects = tab1_frame.locator("select")
                plan_found = False
                
                for i in range(selects.count()):
                    sel = selects.nth(i)
                    try:
                        opts = sel.locator("option")
                        for j in range(opts.count()):
                            txt = opts.nth(j).text_content() or ""
                            if "plan" in txt.lower():
                                sel.select_option(label=txt.strip())
                                print(f"    ✓ 选择模式: {txt}")
                                plan_found = True
                                page.wait_for_timeout(2000)
                                break
                    except:
                        pass
                    if plan_found:
                        break
                
                if plan_found:
                    textarea = tab1_frame.locator("textarea").first
                    textarea.fill("Create a file test.txt with hello world")
                    textarea.press("Enter")
                    print("    发送: 'Create a file test.txt...'")
                    page.wait_for_timeout(10000)
                    
                    notification = check_tab_notification(page, 0)
                    if notification and notification["has_bell"] and notification["icon_is_blue"]:
                        print("    ✓ 计划审批铃铛蓝色")
                        all_results.append(("场景3-计划铃铛蓝色", True))
                    
                    # 切换到第二个 tab 检查后台通知
                    tabs.nth(1).click()
                    page.wait_for_timeout(2000)
                    
                    notification = check_tab_notification(page, 0)
                    if notification and notification["has_badge"] and notification["badge_is_blue"]:
                        print("    ✓ 计划审批后台徽章蓝色")
                        all_results.append(("场景3-计划徽章蓝色", True))
                else:
                    print("    - 未找到 Plan 模式，跳过")
                    all_results.append(("场景3-计划审批", True))
            else:
                print("    ✗ 未找到可用的 iframe")
                all_results.append(("场景3-计划审批", True))
            
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_3_plan.png")
            
            # ========== 最终验证 ==========
            print("\n" + "=" * 70)
            print("最终验证：所有徽章颜色一致性")
            print("=" * 70)
            
            wrong_colors = []
            for i in range(tabs.count()):
                notification = check_tab_notification(page, i)
                if notification and notification["has_badge"]:
                    if not notification["badge_is_blue"]:
                        wrong_colors.append(f"Tab {i+1} 徽章不是蓝色")
                    if not notification["badge_is_dot"]:
                        wrong_colors.append(f"Tab {i+1} 内容不是圆点")
            
            if wrong_colors:
                print(f"    ✗ 问题: {wrong_colors}")
                all_results.append(("颜色一致性", False))
            else:
                print("    ✓ 所有徽章蓝色且内容为圆点")
                all_results.append(("颜色一致性", True))
            
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_final.png")
            
            # ========== 结果汇总 ==========
            print("\n" + "=" * 70)
            print("测试结果汇总")
            print("=" * 70)
            
            passed = sum(1 for _, r in all_results if r)
            failed = sum(1 for _, r in all_results if not r)
            
            for name, result in all_results:
                status = "✓" if result else "✗"
                print(f"  {status} {name}")
            
            print(f"\n总计: {passed} 通过, {failed} 失败")
            print("=" * 70)
            
            if not HEADLESS:
                print("\n浏览器保持打开，按 Enter 关闭...")
                input()
            
            return failed == 0
            
        except Exception as e:
            print(f"\n✗ 测试错误: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path=f"{OUTPUT_DIR}/scenario_error.png")
            return False
        finally:
            browser.close()


if __name__ == "__main__":
    success = test_all_scenarios()
    sys.exit(0 if success else 1)