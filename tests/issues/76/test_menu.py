#!/usr/bin/env python3
"""Test script for issue 76: Admin menu visibility."""

import asyncio
from playwright.async_api import async_playwright


async def test_menu(username: str, password: str, user_type: str):
    """Test menu visibility for a specific user type."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await context.new_page()
        
        # 访问登录页面
        await page.goto('http://localhost:5001/login')
        await page.wait_for_load_state('networkidle')
        
        # 登录
        await page.fill('input[name="username"]', username)
        await page.fill('input[name="password"]', password)
        
        # 点击登录并等待导航
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')
        
        # 等待页面加载完成
        await page.wait_for_load_state('networkidle')
        
        # 打印当前 URL
        print(f'当前 URL: {page.url}')
        
        # 等待一下让 JS 执行完成
        await asyncio.sleep(2)
        
        # 获取所有菜单项的显示状态
        menu_items = {
            'nav-dashboard': await page.locator('#nav-dashboard').get_attribute('style') or 'no style',
            'nav-messages': await page.locator('#nav-messages').get_attribute('style') or 'no style',
            'nav-analysis': await page.locator('#nav-analysis').get_attribute('style') or 'no style',
            'nav-management': await page.locator('#nav-management').get_attribute('style') or 'no style',
            'nav-workspace': await page.locator('#nav-workspace').get_attribute('style') or 'no style',
            'nav-report': await page.locator('#nav-report').get_attribute('style') or 'no style',
        }
        
        print(f'{user_type} 登录后菜单状态:')
        for item, style in menu_items.items():
            print(f'  {item}: {style}')
        
        # 检查 Data Status Panel
        print()
        print('检查 Data Status Panel...')
        data_status_exists = await page.locator('#data-status-container').count()
        print(f'Data Status Panel 元素数量: {data_status_exists}')
        if data_status_exists > 0:
            data_status = await page.locator('#data-status-container').inner_html()
            print(f'Data Status Panel 内容: {data_status[:200]}...')
        
        # 检查页面源代码中是否有 data-status-container
        page_content = await page.content()
        has_data_status_in_html = 'id="data-status-container"' in page_content
        print(f'页面源代码中有 data-status-container: {has_data_status_in_html}')
        
        # 检查服务端渲染的菜单初始状态
        # Dashboard 菜单在服务端渲染时的初始 display 状态
        dashboard_initial = 'style="display: block;"' in page_content or 'style="display:block;"' in page_content
        print(f'服务端渲染的 Dashboard 初始状态: {dashboard_initial}')
        
        # 检查 cookies
        cookies = await context.cookies()
        print(f'Cookies: {[c for c in cookies if c["name"] == "session_token"]}')
        
        # 获取用户信息
        print()
        print('检查用户信息...')
        user_info = await page.evaluate('''() => {
            const user = localStorage.getItem("current_user");
            const token = localStorage.getItem("ai_token_session");
            return { user: user, token: token ? "exists" : "none" };
        }''')
        print(f'localStorage user: {user_info["user"]}')
        print(f'localStorage token: {user_info["token"]}')
        
        # 截图
        screenshot_path = f'/Users/rhuang/workspace/ai-token-analyzer/screenshots/issues/76/{user_type.lower()}_menu_test.png'
        await page.screenshot(path=screenshot_path)
        print()
        print(f'截图已保存到 {screenshot_path}')
        
        await browser.close()
        
        return menu_items


async def main():
    print('=' * 60)
    print('测试 Admin 用户菜单')
    print('=' * 60)
    await test_menu('admin', 'admin123', 'Admin')
    
    print()
    print('=' * 60)
    print('测试普通用户菜单 (testuser)')
    print('=' * 60)
    try:
        await test_menu('testuser', 'testuser', 'NormalUser')
    except Exception as e:
        print(f'普通用户测试失败: {e}')


if __name__ == '__main__':
    asyncio.run(main())