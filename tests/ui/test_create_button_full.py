#!/usr/bin/env python3
"""
完整测试：通过 /work 页面 iframe 测试 AddProjectModal 的 Create 按钮
模拟用户实际操作流程
"""

import asyncio
import os

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def test_create_button_full():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # 非无头模式，用户可以看到
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

        print("=== 步骤 1: 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        print(f"当前 URL: {page.url}")

        # 填写登录表单
        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')

        # 等待跳转到 work 页面
        await page.wait_for_url("**/work", timeout=10000)
        print(f"✓ 登录成功，跳转到: {page.url}")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_01_login.png"))

        print("\n=== 步骤 2: 等待 iframe 加载 ===")
        # 等待 iframe 出现
        await page.wait_for_selector("iframe", timeout=15000)
        print("✓ iframe 已出现")

        # 获取 iframe
        iframe = page.frame_locator("iframe").first

        # 等待 iframe 内容加载
        await page.wait_for_timeout(5000)

        # 在 iframe 内查找 Add Project 按钮
        add_btn = iframe.locator('button:has-text("Add Project")')
        try:
            await add_btn.wait_for(timeout=15000)
            print("✓ iframe 内找到 Add Project 按钮")
        except:
            print("⚠️ iframe 内未找到 Add Project 按钮")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_02_no_button.png"))
            await browser.close()
            return False

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_03_iframe_loaded.png"))

        print("\n=== 步骤 3: 点击 Add Project 按钮 ===")
        await add_btn.click()
        await page.wait_for_timeout(1000)
        print("✓ 点击了 Add Project 按钮")

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_04_modal_open.png"))

        print("\n=== 步骤 4: 选择目录 ===")
        # 在 iframe 内等待 Modal 打开
        await page.wait_for_timeout(3000)

        # 点击 Select This Folder 按钮
        select_btn = iframe.locator(
            'button:has-text("Select This Folder"), button:has-text("选择此文件夹")'
        )
        try:
            await select_btn.wait_for(timeout=5000)
            await select_btn.click()
            await page.wait_for_timeout(1000)
            print("✓ 点击了 Select This Folder")
        except:
            print("⚠️ 未找到 Select This Folder 按钮")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_05_no_select.png"))
            await browser.close()
            return False

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_06_selected.png"))

        print("\n=== 步骤 5: 填写项目信息 ===")
        # 检查是否进入 details 步骤
        name_input = iframe.locator('input[type="text"]').first
        try:
            await name_input.wait_for(timeout=3000)
            await name_input.fill("test-project-full")
            print("✓ 填写了项目名称: test-project-full")
        except:
            print("⚠️ 未进入 details 步骤")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_07_no_details.png"))
            await browser.close()
            return False

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_08_filled.png"))

        print("\n=== 步骤 6: 点击 Create 按钮 ===")
        # 清空 API 调用记录
        api_calls.clear()

        # 找到 Modal 内的 Add Project 按钮（details 步骤的创建按钮）
        # 使用 nth(1) 因为第一个按钮是 workspace 的 Add Project
        create_btn = iframe.locator('button:has-text("Add Project")').nth(1)

        try:
            await create_btn.wait_for(timeout=3000)
            print("✓ 找到创建按钮")

            # 点击
            await create_btn.click()
            await page.wait_for_timeout(2000)
            print("✓ 点击了创建按钮")
        except Exception as e:
            print(f"⚠️ 点击失败: {e}")
            await page.screenshot(
                path=os.path.join(SCREENSHOT_DIR, "full_test_09_click_failed.png")
            )
            await browser.close()
            return False

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "full_test_10_after_click.png"))

        print("\n=== 步骤 7: 检查结果 ===")
        project_api_calls = [
            c for c in api_calls if "projects" in c["url"] and c["method"] == "POST"
        ]
        print(f"POST /api/projects 调用数: {len(project_api_calls)}")

        for call in api_calls:
            print(f"  - {call['method']} {call['url']}")

        # 检查是否成功
        if len(project_api_calls) > 0:
            print("\n✅ 测试成功！Create 按钮正常工作")
        else:
            print("\n❌ 测试失败！Create 按钮点击未触发 API 调用")

        # 打印控制台日志
        if console_logs:
            print("\n=== 控制台日志 ===")
            for log in console_logs[-20:]:
                print(log)

        # 保持浏览器打开 10 秒让用户看到结果
        print("\n等待 10 秒后关闭浏览器...")
        await page.wait_for_timeout(10000)

        await browser.close()
        return len(project_api_calls) > 0


if __name__ == "__main__":
    result = asyncio.run(test_create_button_full())
    print(f"\n=== 最终结果: {'成功 ✅' if result else '失败 ❌'} ===")
