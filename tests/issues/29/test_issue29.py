#!/usr/bin/env python3
"""
测试 Issue #29: 去掉 conversation history 表格的 conversation id 一列
"""

import pytest
import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime

# 配置
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001/')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
SCREENSHOT_DIR = 'screenshots'


@pytest.mark.asyncio
async def test_issue29():
    """测试 Conversation History 表格不包含 Conversation ID 列"""
    
    # 确保截图目录存在
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    
    # 生成时间戳
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()
        
        results = []
        
        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f'{BASE_URL}login')
            await page.wait_for_load_state('networkidle')
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            print("   ✓ 登录成功")
            results.append(("登录", True, ""))
            
            # 2. 导航到 Analysis 页面
            print("2. 导航到 Analysis 页面...")
            await page.click('text=Analysis')
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            print("   ✓ 已进入 Analysis 页面")
            results.append(("导航到 Analysis", True, ""))
            
            # 设置日期范围以确保数据加载
            print("2.5 设置日期范围...")
            await page.evaluate('''() => {
                document.getElementById('analysis-start-date').value = '2026-03-01';
                document.getElementById('analysis-end-date').value = '2026-03-13';
                onAnalysisDateChange();
            }''')
            await asyncio.sleep(2)
            
            # 3. 点击 Conversation History Tab
            print("3. 点击 Conversation History Tab...")
            await page.evaluate('''() => {
                const tab = document.getElementById('conversation-history-tab');
                if (tab) tab.click();
            }''')
            await asyncio.sleep(3)  # Wait for data to load
            await page.wait_for_selector('#conversation-history-table .tabulator-row', timeout=10000)
            print("   ✓ Conversation History 表格已加载")
            results.append(("切换到 Conversation History", True, ""))
            
            # 4. 截图
            screenshot_path = f'{SCREENSHOT_DIR}/issue29_conversation_history_{timestamp}.png'
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"   ✓ 截图已保存: {screenshot_path}")
            
            # 5. 检查表格列头
            print("4. 检查表格列头...")
            headers = await page.query_selector_all('#conversation-history-table-container th')
            header_texts = []
            for header in headers:
                text = await header.text_content()
                header_texts.append(text.strip() if text else '')
            
            print(f"   表格列头: {header_texts}")
            
            # 6. 验证 Conversation ID 列不存在
            has_conversation_id = any('conversation id' in h.lower() for h in header_texts)
            
            if has_conversation_id:
                print("   ✗ 失败: 表格中仍然包含 Conversation ID 列")
                results.append(("验证 Conversation ID 列已移除", False, "表格中仍然包含 Conversation ID 列"))
            else:
                print("   ✓ 成功: Conversation ID 列已移除")
                results.append(("验证 Conversation ID 列已移除", True, ""))
            
            # 7. 检查列选择器菜单
            print("5. 检查列选择器菜单...")
            await page.click('#columnSelectorBtn')
            await page.wait_for_selector('#columnSelectorMenu.show', timeout=3000)
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue29_column_selector_{timestamp}.png', full_page=True)
            
            # 获取列选择器中的所有选项
            column_options = await page.query_selector_all('#columnSelectorMenu .form-check-label')
            column_option_texts = []
            for opt in column_options:
                text = await opt.text_content()
                column_option_texts.append(text.strip() if text else '')
            
            print(f"   列选择器选项: {column_option_texts}")
            
            # 验证列选择器中不包含 Conversation ID 选项
            has_conversation_id_option = any('conversation id' in opt.lower() for opt in column_option_texts)
            
            if has_conversation_id_option:
                print("   ✗ 失败: 列选择器中仍然包含 Conversation ID 选项")
                results.append(("验证列选择器中无 Conversation ID", False, "列选择器中仍然包含 Conversation ID 选项"))
            else:
                print("   ✓ 成功: 列选择器中不包含 Conversation ID 选项")
                results.append(("验证列选择器中无 Conversation ID", True, ""))
            
        except Exception as e:
            print(f"   ✗ 测试出错: {str(e)}")
            results.append(("测试执行", False, str(e)))
            await page.screenshot(path=f'{SCREENSHOT_DIR}/issue29_error_{timestamp}.png', full_page=True)
        
        finally:
            await browser.close()
        
        # 打印测试报告
        print("\n" + "=" * 60)
        print("Issue #29 测试报告")
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


if __name__ == '__main__':
    success = asyncio.run(test_issue29())
    exit(0 if success else 1)