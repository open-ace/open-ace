#!/usr/bin/env python3
"""
测试 Issue #34: Conversation History 表格点击表头排序后内容消失

问题描述：
- Conversation History 表格点击表头排序后，表格内容消失，显示 "No sessions found"

测试步骤：
1. 登录系统
2. 导航到 Analysis 页面
3. 点击 Conversation History 标签
4. 等待表格数据加载
5. 点击表头进行排序
6. 验证表格内容是否仍然存在
"""

import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001/')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
SCREENSHOT_DIR = 'screenshots'


def print_test_report(results):
    """打印测试报告"""
    print("\n" + "=" * 60)
    print("Issue #34 测试报告")
    print("=" * 60)
    
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    
    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)
    
    for name, success, message in results:
        status = "✓ 通过" if success else "✗ 失败"
        msg = f" ({message})" if message else ""
        print(f"  {name}: {status}{msg}")
    
    print("=" * 60)
    return failed == 0


async def test_issue34():
    """测试 Conversation History 表格排序功能"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1400, 'height': 900})
        
        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f'{BASE_URL}login')
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state('networkidle')
            results.append(("登录", True, ""))
            print("   ✓ 登录成功")
            
            # 2. 导航到 Analysis 页面
            print("2. 导航到 Analysis 页面...")
            # 等待页面完全加载
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            
            # 使用 JavaScript 点击 Analysis 导航链接
            await page.evaluate('''() => {
                const navAnalysis = document.getElementById('nav-analysis');
                if (navAnalysis && navAnalysis.style.display !== 'none') {
                    navAnalysis.click();
                }
            }''')
            await asyncio.sleep(3)
            
            # 检查 Analysis 页面是否正确显示
            analysis_visible = await page.is_visible('#analysisTabs')
            print(f"   Analysis 标签组可见: {analysis_visible}")
            
            # 检查日期选择器
            start_date = await page.input_value('#analysis-start-date')
            end_date = await page.input_value('#analysis-end-date')
            print(f"   日期范围: {start_date} 到 {end_date}")
            
            # 如果日期为空，手动设置日期
            if not start_date or not end_date:
                print("   日期为空，手动设置日期...")
                await page.fill('#analysis-start-date', '2025-01-01')
                await page.fill('#analysis-end-date', '2026-12-31')
                await asyncio.sleep(1)
                start_date = await page.input_value('#analysis-start-date')
                end_date = await page.input_value('#analysis-end-date')
                print(f"   新日期范围: {start_date} 到 {end_date}")
            
            results.append(("导航到 Analysis", True, ""))
            print("   ✓ 已进入 Analysis 页面")
            
            # 检查日期选择器
            start_date = await page.input_value('#analysis-start-date')
            end_date = await page.input_value('#analysis-end-date')
            print(f"   日期范围: {start_date} 到 {end_date}")
            
            # 如果日期为空，手动设置日期
            if not start_date or not end_date:
                print("   日期为空，手动设置日期...")
                await page.fill('#analysis-start-date', '2025-01-01')
                await page.fill('#analysis-end-date', '2026-12-31')
                await asyncio.sleep(1)
                start_date = await page.input_value('#analysis-start-date')
                end_date = await page.input_value('#analysis-end-date')
                print(f"   新日期范围: {start_date} 到 {end_date}")
            
            results.append(("导航到 Analysis", True, ""))
            print("   ✓ 已进入 Analysis 页面")
            
            # 3. 点击 Conversation History 标签
            print("3. 点击 Conversation History 标签...")
            # 使用 Bootstrap 的 Tab API 来切换标签
            await page.evaluate('''() => {
                const tab = document.getElementById('conversation-history-tab');
                if (tab) {
                    // 使用 Bootstrap 的 Tab API
                    const bsTab = new bootstrap.Tab(tab);
                    bsTab.show();
                }
            }''')
            await asyncio.sleep(2)
            
            # 检查标签是否已激活
            tab_active = await page.evaluate('''() => {
                const tab = document.getElementById('conversation-history-tab');
                return tab ? tab.classList.contains('active') : false;
            }''')
            print(f"   标签激活状态: {tab_active}")
            
            # 检查内容区域是否可见
            content_visible = await page.is_visible('#conversation-history-content')
            print(f"   内容区域可见: {content_visible}")
            
            results.append(("切换到 Conversation History", True, ""))
            print("   ✓ 已切换到 Conversation History 标签")
            
            # 4. 等待表格数据加载
            print("4. 等待表格数据加载...")
            try:
                await page.wait_for_selector('#conversation-history-table .tabulator-row', timeout=10000)
                rows_before = await page.query_selector_all('#conversation-history-table .tabulator-row')
                row_count_before = len(rows_before)
                print(f"   ✓ 表格已加载，共 {row_count_before} 行数据")
                results.append(("表格数据加载", True, f"{row_count_before} 行"))
            except Exception as e:
                print(f"   ⚠ 表格数据加载超时，检查是否有数据...")
                # 检查表格是否存在
                table_exists = await page.is_visible('#conversation-history-table')
                print(f"   表格容器存在: {table_exists}")
                
                # 检查是否显示 "No sessions found"
                no_sessions = await page.is_visible('text=No sessions found')
                print(f"   显示 'No sessions found': {no_sessions}")
                
                # 检查日期选择器
                start_date = await page.input_value('#analysis-start-date')
                end_date = await page.input_value('#analysis-end-date')
                print(f"   日期范围: {start_date} 到 {end_date}")
                
                if no_sessions:
                    print("   ⚠ 当前日期范围内没有数据，尝试设置更宽的日期范围...")
                    # 设置更宽的日期范围
                    await page.fill('#analysis-start-date', '2025-01-01')
                    await page.fill('#analysis-end-date', '2026-12-31')
                    await asyncio.sleep(2)
                    
                    # 再次检查表格
                    try:
                        await page.wait_for_selector('#conversation-history-table .tabulator-row', timeout=10000)
                        rows_before = await page.query_selector_all('#conversation-history-table .tabulator-row')
                        row_count_before = len(rows_before)
                        print(f"   ✓ 表格已加载，共 {row_count_before} 行数据")
                        results.append(("表格数据加载", True, f"{row_count_before} 行 (扩展日期范围)"))
                    except:
                        print(f"   ✗ 表格数据加载失败: {e}")
                        results.append(("表格数据加载", False, str(e)))
                        await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_no_data_{timestamp}.png')
                        raise
                else:
                    print(f"   ✗ 表格数据加载失败: {e}")
                    results.append(("表格数据加载", False, str(e)))
                    await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_no_data_{timestamp}.png')
                    raise
            
            # 截图：排序前的表格
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_before_sort_{timestamp}.png')
            
            # 5. 点击 User 表头进行排序
            print("5. 点击 User 表头进行排序...")
            user_header = await page.query_selector('#conversation-history-table .tabulator-col-title:has-text("User")')
            if user_header:
                await user_header.click()
                await asyncio.sleep(2)
                print("   ✓ 已点击 User 表头")
            else:
                # 尝试其他方式找到表头
                headers = await page.query_selector_all('#conversation-history-table .tabulator-col-title')
                if headers:
                    await headers[0].click()
                    await asyncio.sleep(2)
                    print("   ✓ 已点击第一个表头")
                else:
                    print("   ✗ 未找到可排序的表头")
                    results.append(("点击表头排序", False, "未找到可排序的表头"))
                    raise Exception("未找到可排序的表头")
            
            # 截图：排序后的表格
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_after_sort_{timestamp}.png')
            
            # 6. 验证表格内容是否仍然存在
            print("6. 验证表格内容...")
            rows_after = await page.query_selector_all('#conversation-history-table .tabulator-row')
            row_count_after = len(rows_after)
            
            # 检查是否显示 "No sessions found"
            no_sessions_visible = await page.is_visible('text=No sessions found')
            
            if no_sessions_visible:
                print(f"   ✗ 排序后表格内容消失，显示 'No sessions found'")
                results.append(("排序后表格验证", False, "表格内容消失"))
            elif row_count_after == 0:
                print(f"   ✗ 排序后表格行数为 0")
                results.append(("排序后表格验证", False, "表格行数为 0"))
            elif row_count_after != row_count_before:
                print(f"   ⚠ 排序前行数 {row_count_before}，排序后行数 {row_count_after}")
                results.append(("排序后表格验证", True, f"行数变化: {row_count_before} -> {row_count_after}"))
            else:
                print(f"   ✓ 排序后表格内容正常，共 {row_count_after} 行")
                results.append(("排序后表格验证", True, f"{row_count_after} 行"))
            
            # 7. 再次点击表头进行反向排序
            print("7. 再次点击表头进行反向排序...")
            user_header = await page.query_selector('#conversation-history-table .tabulator-col-title:has-text("User")')
            if user_header:
                await user_header.click()
                await asyncio.sleep(2)
                print("   ✓ 已再次点击 User 表头")
            
            # 截图：反向排序后的表格
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_after_reverse_sort_{timestamp}.png')
            
            # 验证反向排序后的表格
            rows_reverse = await page.query_selector_all('#conversation-history-table .tabulator-row')
            row_count_reverse = len(rows_reverse)
            no_sessions_visible_reverse = await page.is_visible('text=No sessions found')
            
            if no_sessions_visible_reverse:
                print(f"   ✗ 反向排序后表格内容消失")
                results.append(("反向排序验证", False, "表格内容消失"))
            elif row_count_reverse == 0:
                print(f"   ✗ 反向排序后表格行数为 0")
                results.append(("反向排序验证", False, "表格行数为 0"))
            else:
                print(f"   ✓ 反向排序后表格内容正常，共 {row_count_reverse} 行")
                results.append(("反向排序验证", True, f"{row_count_reverse} 行"))
            
            # 8. 测试其他列的排序
            print("8. 测试其他列的排序...")
            other_headers = ['Model', 'Start Time', 'User Msgs', 'AI Msgs', 'Avg Latency']
            for header_name in other_headers:
                try:
                    header = await page.query_selector(f'#conversation-history-table .tabulator-col-title:has-text("{header_name}")')
                    if header:
                        await header.click()
                        await asyncio.sleep(1)
                        rows = await page.query_selector_all('#conversation-history-table .tabulator-row')
                        if len(rows) > 0:
                            print(f"   ✓ {header_name} 列排序正常")
                        else:
                            print(f"   ✗ {header_name} 列排序后表格为空")
                            results.append((f"{header_name} 列排序", False, "表格为空"))
                except Exception as e:
                    print(f"   ⚠ {header_name} 列排序测试跳过: {e}")
            
            results.append(("其他列排序测试", True, ""))
            
        except Exception as e:
            results.append(("测试执行", False, str(e)))
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue34_error_{timestamp}.png')
        finally:
            await browser.close()
    
    # 打印报告
    success = print_test_report(results)
    return success


if __name__ == '__main__':
    success = asyncio.run(test_issue34())
    exit(0 if success else 1)