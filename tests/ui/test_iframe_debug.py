#!/usr/bin/env python3
"""
调试 iframe 内容
"""

import asyncio
import os

from playwright.async_api import async_playwright

BASE_URL = "http://117.72.38.96:5000"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


async def test_iframe_debug():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        print("=== 登录 ===")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        await page.fill('input[type="text"]', "rhuang")
        await page.fill('input[type="password"]', "admin123")
        await page.click('button[type="submit"]')
        await page.wait_for_url("**/work", timeout=10000)
        print(f"✓ 登录成功: {page.url}")

        await page.wait_for_selector("iframe", timeout=15000)
        print("✓ iframe 已出现")

        # 获取 iframe 的 src
        iframe_el = await page.locator("iframe").first.element_handle()
        iframe_src = await iframe_el.get_attribute("src")
        print(f"iframe src: {iframe_src}")

        await page.wait_for_timeout(10000)

        # 获取 iframe 内容
        iframe = page.frame_locator("iframe").first

        # 截图 iframe 区域
        await iframe_el.screenshot(path=os.path.join(SCREENSHOT_DIR, "iframe_content.png"))
        print("截图已保存: iframe_content.png")

        # 尝试获取 iframe 内的 HTML
        try:
            iframe_html = await iframe.locator("body").inner_html()
            print(f"iframe HTML 长度: {len(iframe_html)}")
            print(f"iframe HTML 前 500 字符:\n{iframe_html[:500]}")
        except Exception as e:
            print(f"获取 iframe HTML 失败: {e}")

        # 检查 iframe 内有多少按钮
        try:
            buttons = await iframe.locator("button").all()
            print(f"iframe 内按钮数: {len(buttons)}")
            for i, btn in enumerate(buttons[:10]):
                try:
                    text = await btn.text_content()
                    print(f"  按钮 {i}: {text[:50]}")
                except:
                    pass
        except Exception as e:
            print(f"获取按钮失败: {e}")

        # 检查是否有错误信息
        try:
            error_el = iframe.locator('.error, .text-red, [class*="error"]')
            if await error_el.count() > 0:
                error_text = await error_el.first.text_content()
                print(f"发现错误信息: {error_text}")
        except:
            pass

        # 检查是否有加载状态
        try:
            loading_el = iframe.locator('.loading, .spinner, [class*="loading"]')
            if await loading_el.count() > 0:
                print("发现加载状态")
        except:
            pass

        # 检查 webui 进程
        print("\n=== 检查服务器上的 webui 进程 ===")

        # 保持浏览器打开
        print("\n等待 30 秒后关闭...")
        await page.wait_for_timeout(30000)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_iframe_debug())
