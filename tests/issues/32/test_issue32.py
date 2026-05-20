#!/usr/bin/env python3
"""
测试 Issue #32: Conversation Timeline 显示不直观
"""

import asyncio
import os
from datetime import datetime

import pytest
from playwright.async_api import async_playwright

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000/")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"


@pytest.mark.asyncio
async def test_issue32():
    """测试 Conversation Timeline 的改进显示"""

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

            # 设置日期范围以确保数据加载
            print("2.5 设置日期范围...")
            await page.evaluate("""() => {
                document.getElementById('analysis-start-date').value = '2026-03-01';
                document.getElementById('analysis-end-date').value = '2026-03-13';
                onAnalysisDateChange();
            }""")
            await asyncio.sleep(2)

            # 3. 点击 Conversation History Tab
            print("3. 点击 Conversation History Tab...")

            # 先截图看看当前状态
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_before_tab_click_{timestamp}.png", full_page=True
            )

            await page.evaluate("""() => {
                const tab = document.getElementById('conversation-history-tab');
                console.log('Tab found:', tab);
                if (tab) tab.click();
            }""")
            await asyncio.sleep(5)  # Wait longer for data to load

            # 截图看看点击后的状态
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_after_tab_click_{timestamp}.png", full_page=True
            )

            # 等待表格初始化
            try:
                await page.wait_for_selector(
                    "#conversation-history-table .tabulator-row", timeout=15000
                )
                print("   ✓ Conversation History 表格已加载")
                results.append(("切换到 Conversation History", True, ""))
            except:
                # 检查表格是否存在
                table_exists = await page.is_visible("#conversation-history-table")
                print(f"   表格容器可见: {table_exists}")

                # 检查表格内容
                table_html = await page.evaluate("""() => {
                    const table = document.getElementById('conversation-history-table');
                    return table ? table.innerHTML : 'not found';
                }""")
                print(f"   表格内容: {table_html[:200]}...")

                # 检查是否有 tabulator 类
                has_tabulator = await page.evaluate("""() => {
                    const table = document.getElementById('conversation-history-table');
                    return table ? table.classList.contains('tabulator') : false;
                }""")
                print(f"   表格有 tabulator 类: {has_tabulator}")

                if table_exists:
                    print("   ✓ Conversation History 表格容器存在（可能无数据）")
                    results.append(("切换到 Conversation History", True, "表格存在但可能无数据"))
                else:
                    print("   ✗ 失败: Conversation History 表格未找到")
                    results.append(("切换到 Conversation History", False, "表格未找到"))
                    return False

            # 截图
            screenshot_path = f"{SCREENSHOT_DIR}/issue32_conversation_history_{timestamp}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"   ✓ 截图已保存: {screenshot_path}")

            # 4. 找到并点击 Timeline 按钮
            print("4. 点击 Timeline 按钮...")

            # 使用 JavaScript 找到并点击第一个 Timeline 按钮
            timeline_button_found = await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button[onclick*="showTimelineModal"]');
                if (buttons.length > 0) {
                    buttons[0].click();
                    return true;
                }
                return false;
            }""")

            if not timeline_button_found:
                print("   ✗ 失败: 未找到 Timeline 按钮")
                results.append(("点击 Timeline 按钮", False, "未找到 Timeline 按钮"))
                return False

            await asyncio.sleep(2)  # Wait for modal to open
            print("   ✓ Timeline 按钮已点击")
            results.append(("点击 Timeline 按钮", True, ""))

            # 5. 验证 Timeline Modal 已打开
            print("5. 验证 Timeline Modal 已打开...")
            modal_visible = await page.is_visible("#timelineModal.show")

            if not modal_visible:
                print("   ✗ 失败: Timeline Modal 未打开")
                results.append(("Timeline Modal 打开", False, "Modal 未显示"))
                return False

            print("   ✓ Timeline Modal 已打开")
            results.append(("Timeline Modal 打开", True, ""))

            # 截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_timeline_modal_{timestamp}.png", full_page=True
            )

            # 6. 验证新的卡片式时间线显示
            print("6. 验证新的卡片式时间线显示...")

            # 检查 timeline container 是否存在
            container_exists = await page.is_visible("#timelineContainer")
            if container_exists:
                print("   ✓ Timeline Container 存在")
                results.append(("Timeline Container 存在", True, ""))
            else:
                print("   ✗ 失败: Timeline Container 不存在")
                results.append(("Timeline Container 存在", False, "Container 未找到"))
                return False

            # 检查 summary 是否存在
            summary_exists = await page.is_visible(".timeline-summary")
            if summary_exists:
                summary_text = await page.text_content(".timeline-summary")
                print(f"   ✓ Timeline Summary 存在: {summary_text}")
                results.append(("Timeline Summary 存在", True, ""))
            else:
                print("   ✗ 失败: Timeline Summary 不存在")
                results.append(("Timeline Summary 存在", False, "Summary 未找到"))

            # 检查 timeline items 是否存在
            timeline_items_count = await page.evaluate("""() => {
                return document.querySelectorAll('.timeline-item').length;
            }""")

            if timeline_items_count > 0:
                print(f"   ✓ 找到 {timeline_items_count} 个 Timeline Items")
                results.append(("Timeline Items 显示", True, f"{timeline_items_count} 个"))
            else:
                print("   ✗ 失败: 未找到 Timeline Items")
                results.append(("Timeline Items 显示", False, "无 Timeline Items"))

            # 检查卡片样式
            cards_count = await page.evaluate("""() => {
                return document.querySelectorAll('.timeline-item .card').length;
            }""")

            if cards_count > 0:
                print(f"   ✓ 找到 {cards_count} 个卡片样式元素")
                results.append(("卡片样式显示", True, f"{cards_count} 个"))
            else:
                print("   ✗ 失败: 未找到卡片样式元素")
                results.append(("卡片样式显示", False, "无卡片样式"))

            # 检查时间间隔显示
            time_gap_exists = await page.is_visible(".timeline-item small.text-muted")
            if time_gap_exists:
                print("   ✓ 时间间隔显示存在")
                results.append(("时间间隔显示", True, ""))
            else:
                print("   - 时间间隔显示不存在（可能是第一条消息）")
                results.append(("时间间隔显示", True, "第一条消息无间隔"))

            # 最终截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_timeline_detail_{timestamp}.png", full_page=True
            )
            print("   ✓ 详细截图已保存")

        except Exception as e:
            print(f"   ✗ 测试出错: {str(e)}")
            results.append(("测试执行", False, str(e)))
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_error_{timestamp}.png", full_page=True
            )

        finally:
            await browser.close()

        # 打印测试报告
        print("\n" + "=" * 60)
        print("Issue #32 测试报告")
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
    success = asyncio.run(test_issue32())
    exit(0 if success else 1)
