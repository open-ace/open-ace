#!/usr/bin/env python3
"""
深入测试 AddProjectModal 的点击问题 - 直接访问 webui
"""

import asyncio
import os

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
WEBUI_PORT = "3101"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def test_click_debug():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        # 收集网络请求
        api_calls = []

        def on_request(request):
            if "/api/" in request.url:
                api_calls.append({"url": request.url, "method": request.method})
                print(f"[API] {request.method} {request.url}")

        def on_response(response):
            if "/api/" in response.url:
                print(f"[API Response] {response.status} {response.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        # 收集控制台日志
        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text[:200]}"))

        # 直接访问 webui URL（带 token）
        webui_url = f"http://117.72.38.96:{WEBUI_PORT}?token=3:3101:eaa97487f8c3a8bc4de76e9369235175:37cb1cec45f9d038&openace_url={BASE_URL}&lang=en"

        print("=== 步骤 1: 打开 webui ===")
        print(f"URL: {webui_url}")
        await page.goto(webui_url, timeout=30000)
        await page.wait_for_timeout(3000)
        print("✓ webui 已加载")

        # 截图
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_01_initial.png"))

        print("\n=== 步骤 2: 查找 Add Project 按钮 ===")

        # 注入分析脚本
        analyze_script = """() => {
        console.log('[DEEP ANALYSIS] Starting button analysis...');

        const allButtons = document.querySelectorAll('button');
        console.log('[DEEP ANALYSIS] Total buttons found:', allButtons.length);

        let addButton = null;
        allButtons.forEach((btn, idx) => {
            const text = (btn.textContent || '').trim();
            if (text.includes('Add Project') || text.includes('添加项目')) {
                const style = window.getComputedStyle(btn);
                console.log('[ADD BUTTON FOUND]', JSON.stringify({
                    index: idx,
                    text: text,
                    pointerEvents: style.pointerEvents,
                    disabled: btn.disabled,
                    visible: btn.offsetWidth > 0 && btn.offsetHeight > 0
                }));
                addButton = btn;
            }
        });

        return addButton ? 'FOUND' : 'NOT_FOUND';
        }"""

        result = await page.evaluate(analyze_script)
        print(f"Add Project 按钮: {result}")

        # 尝试不同的选择器
        selectors = [
            'button:has-text("Add Project")',
            'button:has-text("添加项目")',
            '[data-testid="add-project-button"]',
        ]

        add_button = None
        for selector in selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=3000):
                    add_button = el
                    print(f"✓ 找到按钮: {selector}")
                    break
            except:
                continue

        if not add_button:
            print("⚠️ 找不到 Add Project 按钮")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_02_no_button.png"))

            # 打印控制台日志
            print("\n=== 控制台日志 ===")
            for log in console_logs[-20:]:
                print(log)

            await browser.close()
            return False

        print("\n=== 步骤 3: 点击 Add Project 按钮 ===")
        await add_button.click()
        await page.wait_for_timeout(1000)

        # 检查 Modal 是否打开 - 使用多种方式检测
        # 方式1: 检查 role="dialog"
        # 方式2: 检查目录浏览器是否出现

        modal_open = False

        # 检查目录树是否出现（DirectoryBrowser 在 Modal 内）
        try:
            await page.wait_for_selector('[role="treeitem"]', timeout=5000)
            modal_open = True
            print("✓ Modal 已打开（检测到 treeitem）")
        except:
            pass

        # 或者检查 dialog role
        if not modal_open:
            try:
                await page.wait_for_selector('[role="dialog"]', timeout=3000)
                modal_open = True
                print("✓ Modal 已打开（检测到 dialog role）")
            except:
                pass

        # 或者检查 Modal 特定的类名
        if not modal_open:
            try:
                await page.wait_for_selector(".fixed.inset-0", timeout=3000)
                modal_open = True
                print("✓ Modal 已打开（检测到 fixed overlay）")
            except:
                pass

        if not modal_open:
            print("⚠️ Modal 未打开")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_03_no_modal.png"))
            await browser.close()
            return False

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_04_modal_open.png"))

        print("\n=== 步骤 4: 选择目录 ===")
        # 先等待目录浏览器完全加载
        await page.wait_for_timeout(3000)

        # 检查当前显示的路径
        current_path = await page.evaluate("""() => {
            // 找 breadcrumb 中的路径文本
            const breadcrumb = document.querySelector('.flex.items-center.gap-1.flex-1');
            if (breadcrumb) {
                const segments = breadcrumb.querySelectorAll('button');
                const path = '/' + Array.from(segments).map(b => b.textContent).join('/');
                return path;
            }
            return null;
        }""")
        print(f"当前路径: {current_path}")

        # 检查是否有 "no subdirectories" 消息（说明当前目录为空）
        empty_msg = await page.locator("text=No subdirectories").is_visible()
        print(f"空目录提示可见: {empty_msg}")

        # 如果当前目录为空，点击 "Select This Folder" 直接选择当前路径
        # 或者点击 Up 按钮（ArrowUpIcon）导航到上级

        # 方案1: 点击 "Select This Folder" 按钮
        select_folder_btn = page.locator(
            'button:has-text("Select This Folder"), button:has-text("选择此文件夹")'
        )
        try:
            if await select_folder_btn.is_visible(timeout=2000):
                print("找到 'Select This Folder' 按钮")
                await select_folder_btn.click()
                await page.wait_for_timeout(1000)
                print("✓ 点击了 Select This Folder")
        except:
            print("'Select This Folder' 按钮不可见")

        # 方案2: 如果方案1失败，导航到上级目录
        if not await page.locator('input[type="text"]').first.is_visible():
            print("尝试导航到上级目录...")

            # 点击 Up 按钮（ArrowUpIcon）
            up_btn = page.locator('button[title="Go up"], button[title="向上"]').first
            try:
                if await up_btn.is_visible(timeout=2000):
                    await up_btn.click()
                    await page.wait_for_timeout(1000)
                    print("✓ 点击了 Up 按钮")

                    # 等待目录加载
                    await page.wait_for_timeout(2000)

                    # 检查目录列表
                    dir_buttons = await page.locator('button:has-text("rhuang")').all()
                    if dir_buttons:
                        await dir_buttons[0].click()
                        await page.wait_for_timeout(500)
                        print("✓ 点击了 rhuang 目录")

                        # 点击 Select 按钮
                        select_btn = page.locator(
                            'button:has-text("select"), button:has-text("选择")'
                        ).first
                        if await select_btn.is_visible(timeout=1000):
                            await select_btn.click()
                            print("✓ 点击了 Select 按钮")
            except Exception as e:
                print(f"导航失败: {e}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_05_selected.png"))

        print("\n=== 步骤 5: 检查是否进入 details 步骤 ===")
        # 检查是否有项目名称输入框
        name_input = page.locator('input[type="text"]').first
        try:
            is_visible = await name_input.is_visible(timeout=3000)
            print(f"项目名称输入框可见: {is_visible}")
        except:
            is_visible = False
            print("⚠️ 项目名称输入框不可见")

        if not is_visible:
            print("⚠️ 未进入 details 步骤")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_06_no_details.png"))
            await browser.close()
            return False

        print("\n=== 步骤 6: 填写项目信息 ===")
        await name_input.fill("test-project-debug")
        print("✓ 填写了项目名称")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_07_filled.png"))

        print("\n=== 步骤 7: 查找并分析 Create 按钮 ===")
        # 注入按钮分析脚本
        analyze_create_script = """() => {
        console.log('[CREATE BUTTON ANALYSIS]');

        const buttons = document.querySelectorAll('button');
        let createButtons = [];

        buttons.forEach((btn, idx) => {
            const text = (btn.textContent || '').trim();
            if (text.includes('Add Project') || text.includes('Create') || text.includes('创建') || text.includes('添加')) {
                const style = window.getComputedStyle(btn);
                const rect = btn.getBoundingClientRect();

                console.log('[CREATE BUTTON ' + idx + ']', JSON.stringify({
                    text: text,
                    type: btn.type,
                    disabled: btn.disabled,
                    pointerEvents: style.pointerEvents,
                    visibility: style.visibility,
                    display: style.display,
                    opacity: style.opacity,
                    width: rect.width,
                    height: rect.height,
                    className: btn.className.substring(0, 100)
                }));

                createButtons.push({
                    index: idx,
                    text: text,
                    pointerEvents: style.pointerEvents,
                    disabled: btn.disabled
                });
            }
        });

        return createButtons;
        }"""

        create_buttons_info = await page.evaluate(analyze_create_script)
        print(f"找到 {len(create_buttons_info)} 个创建按钮:")
        for btn_info in create_buttons_info:
            print(f"  - {btn_info}")

        # 查找 Modal 内的 Create 按钮（使用 Dialog.Panel 作为限定范围）
        create_button = page.locator(
            '[role="dialog"] button:has-text("Add Project"), .bg-white button:has-text("Add Project"), .dark:bg-slate-800 button:has-text("Add Project")'
        ).first
        try:
            await create_button.wait_for(timeout=3000)
            print("✓ 找到创建按钮（在 Modal 内）")
        except:
            # 如果找不到，尝试更宽泛的搜索
            all_add_buttons = await page.locator('button:has-text("Add Project")').all()
            print(f"找到 {len(all_add_buttons)} 个 Add Project 按钮")
            if len(all_add_buttons) >= 2:
                # 使用第二个按钮（Modal 内的）
                create_button = all_add_buttons[1]
                print("使用第二个 Add Project 按钮（Modal 内）")
            else:
                print("⚠️ 找不到创建按钮")
                await browser.close()
                return False

        print("\n=== 步骤 8: 尝试多种点击方式 ===")

        # 清空之前的 API 调用记录
        api_calls.clear()

        # 方式1: 普通点击
        print("方式1: 普通点击...")
        try:
            await create_button.click(timeout=5000)
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  点击失败: {e}")

        project_calls_1 = [c for c in api_calls if "projects" in c["url"]]
        print(f"  方式1 后 POST projects 调用数: {len(project_calls_1)}")

        # 方式2: 强制点击
        print("方式2: 强制点击...")
        try:
            await create_button.click(force=True, timeout=5000)
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  点击失败: {e}")

        project_calls_2 = [c for c in api_calls if "projects" in c["url"]]
        print(f"  方式2 后 POST projects 调用数: {len(project_calls_2)}")

        # 方式3: JavaScript 点击
        print("方式3: JavaScript 点击...")
        try:
            await create_button.evaluate("el => el.click()")
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  点击失败: {e}")

        project_calls_3 = [c for c in api_calls if "projects" in c["url"]]
        print(f"  方式3 后 POST projects 调用数: {len(project_calls_3)}")

        # 方式4: dispatch_event
        print("方式4: dispatch_event click...")
        try:
            await create_button.dispatch_event("click")
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  点击失败: {e}")

        # 方式5: 模拟鼠标事件
        print("方式5: 模拟鼠标事件...")
        try:
            await create_button.evaluate("""el => {
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;

                const mousedown = new MouseEvent('mousedown', {
                    bubbles: true, cancelable: true, view: window,
                    clientX: x, clientY: y
                });
                const mouseup = new MouseEvent('mouseup', {
                    bubbles: true, cancelable: true, view: window,
                    clientX: x, clientY: y
                });
                const click = new MouseEvent('click', {
                    bubbles: true, cancelable: true, view: window,
                    clientX: x, clientY: y
                });

                el.dispatchEvent(mousedown);
                el.dispatchEvent(mouseup);
                el.dispatchEvent(click);
            }""")
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  点击失败: {e}")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "debug_08_after_clicks.png"))

        print("\n=== 步骤 9: 检查结果 ===")
        project_api_calls = [c for c in api_calls if "projects" in c["url"]]
        print(f"总 API 调用数: {len(api_calls)}")
        print(f"Projects API 调用数: {len(project_api_calls)}")

        for call in api_calls:
            print(f"  - {call['method']} {call['url']}")

        # 检查是否进入了 creating 步骤
        try:
            creating_visible = await page.locator("text=Creating").is_visible(timeout=1000)
        except:
            creating_visible = False

        try:
            success_visible = await page.locator("text=Created").is_visible(timeout=1000)
        except:
            success_visible = False

        try:
            error_visible = await page.locator("text=failed").is_visible(timeout=1000)
        except:
            error_visible = False

        print(f"Creating 状态可见: {creating_visible}")
        print(f"Success 状态可见: {success_visible}")
        print(f"Error 状态可见: {error_visible}")

        # 打印控制台日志
        print("\n=== 控制台日志 ===")
        for log in console_logs[-30:]:
            print(log)

        await browser.close()

        success = len(project_api_calls) > 0 or success_visible
        print(f"\n=== 测试结果: {'成功' if success else '失败'} ===")
        return success


if __name__ == "__main__":
    asyncio.run(test_click_debug())
