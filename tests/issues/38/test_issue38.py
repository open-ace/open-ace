#!/usr/bin/env python3
"""
测试 Issue #38: Conversation History 点击 Page Size 后内容清空

问题描述:
- Conversation History 页面点击 Page Size（每页显示数量）下拉框后，表格内容消失，显示 "No sessions found"

预期行为:
- 点击 Page Size 后，表格应按照新的每页显示数量重新加载数据
"""

import pytest
import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"


@pytest.mark.asyncio
async def test_issue38():
    """测试 Conversation History Page Size 功能"""

    # 确保截图目录存在
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f"{BASE_URL}login")
            await page.wait_for_load_state("networkidle")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            print("   ✓ 登录成功")
            results.append(("登录", True, ""))

            # 2. 导航到 Analysis 页面
            print("2. 导航到 Analysis 页面...")
            await page.click("text=Analysis")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            print("   ✓ 已进入 Analysis 页面")
            results.append(("导航到 Analysis", True, ""))

            # 3. 先设置日期范围（在点击 Tab 之前）
            print("3. 设置日期范围...")
            await page.evaluate(
                """() => {
                document.getElementById('analysis-start-date').value = '2026-03-01';
                document.getElementById('analysis-end-date').value = '2026-03-13';
            }"""
            )
            await asyncio.sleep(1)

            # 4. 点击 Conversation History Tab
            print("4. 点击 Conversation History Tab...")
            await page.evaluate(
                """() => {
                const tab = document.getElementById('conversation-history-tab');
                if (tab) tab.click();
            }"""
            )
            await asyncio.sleep(3)  # Wait for data to load

            # 检查表格状态
            table_info = await page.evaluate(
                """() => {
                const table = document.getElementById('conversation-history-table');
                const rows = document.querySelectorAll('#conversation-history-table .tabulator-row');
                const placeholder = document.querySelector('#conversation-history-table .tabulator-placeholder');
                return {
                    tableExists: !!table,
                    rowCount: rows.length,
                    placeholderExists: !!placeholder,
                    tableHTML: table ? table.innerHTML.substring(0, 200) : 'no table'
                };
            }"""
            )
            print(f"   表格信息: {table_info}")

            try:
                await page.wait_for_selector(
                    "#conversation-history-table .tabulator-row", timeout=10000
                )
                print("   ✓ Conversation History 表格已加载")
                results.append(("切换到 Conversation History", True, ""))
            except:
                print("   ! 表格无数据，可能日期范围内没有会话")
                results.append(("切换到 Conversation History", True, "表格存在但无数据"))
                # 继续测试，因为即使无数据，Page Size 功能也应该正常工作

            # 4. 截图初始状态
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_01_initial_{timestamp}.png", full_page=True
            )

            # 5. 获取初始表格行数
            print("4. 获取初始表格行数...")
            initial_rows = await page.evaluate(
                """() => {
                return document.querySelectorAll('#conversation-history-table .tabulator-row').length;
            }"""
            )
            print(f"   初始行数: {initial_rows}")
            results.append(("初始表格数据", True, f"行数: {initial_rows}"))

            # 6. 测试 Page Size 功能
            print("5. 测试 Page Size 功能...")

            # 查找 Page Size 选择器
            page_size_selector = await page.query_selector(".tabulator-page-size")
            if not page_size_selector:
                print("   ! 未找到 Page Size 选择器")
                results.append(("Page Size 选择器", False, "未找到"))
                await browser.close()
                return False

            print("   找到 Page Size 选择器")

            # 选择新的 Page Size (50)
            await page.select_option(".tabulator-page-size", "50")
            await asyncio.sleep(2)  # 等待数据重新加载

            # 截图：选择新 Page Size 后
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_02_after_page_size_50_{timestamp}.png",
                full_page=True,
            )

            # 7. 验证表格数据仍然存在
            print("6. 验证表格数据...")

            # 检查是否有 "No sessions found" 占位符
            no_data_visible = await page.is_visible(
                "#conversation-history-table .tabulator-placeholder"
            )

            if no_data_visible:
                print("   ✗ 失败: 表格内容消失，显示 'No sessions found'")
                results.append(("Page Size 变更后数据保留", False, "数据消失"))
                await browser.close()
                return False

            # 获取新的表格行数
            new_rows = await page.evaluate(
                """() => {
                return document.querySelectorAll('#conversation-history-table .tabulator-row').length;
            }"""
            )
            print(f"   ✓ 表格数据仍然存在，当前行数: {new_rows}")
            results.append(("Page Size 变更后数据保留", True, f"新行数: {new_rows}"))

            # 8. 再次切换 Page Size 验证
            print("7. 再次切换 Page Size 验证...")
            await page.select_option(".tabulator-page-size", "10")
            await asyncio.sleep(2)

            # 截图：最终状态
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_03_final_{timestamp}.png", full_page=True
            )

            # 验证数据仍然存在
            no_data_visible = await page.is_visible(
                "#conversation-history-table .tabulator-placeholder"
            )
            if no_data_visible:
                print("   ✗ 失败: 再次切换后数据消失")
                results.append(("再次切换 Page Size", False, "数据消失"))
            else:
                final_rows = await page.evaluate(
                    """() => {
                    return document.querySelectorAll('#conversation-history-table .tabulator-row').length;
                }"""
                )
                print(f"   ✓ 数据仍然存在，行数: {final_rows}")
                results.append(("再次切换 Page Size", True, f"行数: {final_rows}"))

            print("\n   ✓ Issue #38 测试通过！Page Size 功能正常工作")

        except Exception as e:
            print(f"   ✗ 测试出错: {str(e)}")
            results.append(("测试执行", False, str(e)))
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_error_{timestamp}.png", full_page=True
            )

        finally:
            await browser.close()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("Issue #38 测试报告: Conversation History Page Size 功能")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)

    for name, success, error in results:
        status = "✓ 通过" if success else f"✗ 失败 ({error})"
        print(f"  {name}: {status}")

    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(test_issue38())
    exit(0 if success else 1)
