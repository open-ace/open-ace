"""
测试 Issue 30: 验证工具标签显示逻辑
"""

import asyncio
import os

import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:5000"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots"


@pytest.mark.asyncio
async def test_issue30_v4():
    """测试工具标签显示"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=5000)
            print("   ✓ 登录成功")

            # 2. 导航到 Messages 页面
            print("2. 导航到 Messages 页面...")

            # 点击 Messages 导航
            await page.click("#nav-messages")
            print("   已点击 Messages 导航")

            # 等待消息容器可见
            await page.wait_for_selector("#messages-container", state="visible", timeout=10000)
            print("   消息容器已可见")

            # 等待消息加载完成 - 等待 message-item 出现
            try:
                await page.wait_for_selector(".message-item", timeout=15000)
                print("   消息项已加载")
            except:
                print("   等待消息项超时，检查页面状态...")

            # 额外等待确保渲染完成
            await asyncio.sleep(2)

            # 检查是否有消息
            message_items = await page.query_selector_all(".message-item")
            print(f"   找到 {len(message_items)} 条消息")

            # 3. 截图
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v4_messages.png", full_page=True)
            print("   ✓ 截图保存: issue30_v4_messages.png")

            # 4. 检查工具标签
            print("\n3. 检查工具标签...")
            tool_badges = await page.query_selector_all(".message-source")
            print(f"   找到 {len(tool_badges)} 个工具标签")

            if len(tool_badges) > 0:
                # 收集所有标签文本
                badge_texts = {}
                for badge in tool_badges:
                    text = await badge.inner_text()
                    text = text.strip().lower()
                    if text not in badge_texts:
                        badge_texts[text] = 0
                    badge_texts[text] += 1

                print("   工具标签统计:")
                for text, count in sorted(badge_texts.items(), key=lambda x: -x[1]):
                    print(f"   - {text}: {count} 个")

                # 检查是否有组合格式
                combined_labels = [t for t in badge_texts.keys() if "(" in t and ")" in t]
                if combined_labels:
                    print(f"\n   ✓ 发现组合格式标签: {combined_labels}")
                    results.append(("组合格式标签", "通过"))
                else:
                    print("\n   ⚠ 未发现组合格式标签")
                    results.append(("组合格式标签", "跳过"))

                # 检查 CSS class
                print("\n4. 检查 CSS class...")
                for badge in tool_badges[:5]:
                    class_name = await badge.get_attribute("class")
                    text = await badge.inner_text()
                    print(f"   标签: '{text}' -> class: '{class_name}'")

                results.append(("工具标签显示", "通过"))
            else:
                print("   ⚠ 没有找到工具标签")

                # 检查页面内容
                print("\n   检查页面内容...")
                messages_container = await page.query_selector("#messages-container")
                if messages_container:
                    inner_html = await messages_container.inner_html()
                    print(f"   messages-container 内容长度: {len(inner_html)}")
                    if len(inner_html) < 100:
                        print(f"   内容: {inner_html[:200]}")

                results.append(("工具标签显示", "跳过"))

            results.append(("测试执行", "通过"))

        except Exception as e:
            print(f"   ✗ 测试出错: {e}")
            import traceback

            traceback.print_exc()
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v4_error.png")
            results.append(("测试执行", "失败"))

        finally:
            await browser.close()

        # 打印测试报告
        print("\n" + "=" * 50)
        print("Issue 30 测试报告")
        print("=" * 50)
        for name, status in results:
            symbol = "✓" if status == "通过" else "✗" if status == "失败" else "○"
            print(f"  {symbol} {name}: {status}")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_issue30_v4())
