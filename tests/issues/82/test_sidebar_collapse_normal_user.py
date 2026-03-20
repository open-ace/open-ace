#!/usr/bin/env python3
"""Test script for issue 82: Sidebar collapse should hide menu text completely.

Issue: When sidebar is collapsed, menu text like "Wo", "My" is still visible
because the span elements were removed by renderSidebarNav function.
"""

import asyncio
from playwright.async_api import async_playwright


async def test_sidebar_collapse_normal_user():
    """Test that sidebar collapse hides all menu text for normal user."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # 禁用缓存
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            ignore_https_errors=True
        )
        # 清除缓存
        await context.route('**', lambda route: route.continue_())
        page = await context.new_page()
        # 禁用缓存
        await page.route('**', lambda route: route.continue_())

        try:
            # 访问登录页面
            await page.goto('http://localhost:5001/login')
            await page.wait_for_load_state('networkidle')

            # 登录普通用户
            await page.fill('input[name="username"]', 'testuser')
            await page.fill('input[name="password"]', 'testuser')

            # 点击登录并等待导航
            async with page.expect_navigation(timeout=10000):
                await page.click('button[type="submit"]')

            # 等待页面加载完成
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)  # 等待 JavaScript 执行完成

            print("=" * 60)
            print("Issue 82: Sidebar Collapse Text Visibility Test")
            print("=" * 60)

            # 检查 sidebar 初始状态
            sidebar = page.locator('#sidebar')
            sidebar_class = await sidebar.get_attribute('class')
            print(f'\n1. Sidebar 初始 class: {sidebar_class}')

            # 获取 sidebar 宽度
            sidebar_width = await sidebar.evaluate('el => el.offsetWidth')
            print(f'   Sidebar 初始宽度: {sidebar_width}px')

            # 检查菜单项文字是否存在
            nav_workspace = page.locator('#nav-workspace')
            nav_report = page.locator('#nav-report')
            nav_workspace_text = page.locator('#nav-workspace-text')
            nav_report_text = page.locator('#nav-report-text')
            
            workspace_count = await nav_workspace.count()
            report_count = await nav_report.count()
            workspace_text_count = await nav_workspace_text.count()
            report_text_count = await nav_report_text.count()
            
            print(f'\n2. 检查菜单项:')
            print(f'   #nav-workspace 存在: {workspace_count > 0}')
            print(f'   #nav-report 存在: {report_count > 0}')
            print(f'   #nav-workspace-text 存在: {workspace_text_count > 0}')
            print(f'   #nav-report-text 存在: {report_text_count > 0}')
            
            # 检查菜单项的 innerHTML
            if workspace_count > 0:
                workspace_html = await nav_workspace.inner_html()
                print(f'   #nav-workspace innerHTML: {workspace_html[:100]}')
            if report_count > 0:
                report_html = await nav_report.inner_html()
                print(f'   #nav-report innerHTML: {report_html[:100]}')

            # 点击收缩按钮
            toggle_btn = page.locator('#sidebar-toggle')
            await toggle_btn.click()
            await asyncio.sleep(0.5)

            # 检查 sidebar 是否收缩
            sidebar_class_after = await sidebar.get_attribute('class')
            sidebar_width_after = await sidebar.evaluate('el => el.offsetWidth')
            print(f'\n3. 收缩后:')
            print(f'   Sidebar class: {sidebar_class_after}')
            print(f'   Sidebar 宽度: {sidebar_width_after}px')

            # 检查文字是否被隐藏
            workspace_text_visible = await nav_workspace_text.is_visible() if workspace_text_count > 0 else False
            report_text_visible = await nav_report_text.is_visible() if report_text_count > 0 else False
            print(f'\n4. 收缩后文字可见性:')
            print(f'   #nav-workspace-text 可见: {workspace_text_visible}')
            print(f'   #nav-report-text 可见: {report_text_visible}')

            # 截图
            await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/82/normal_user_sidebar_collapsed.png')
            print(f'\n5. 截图已保存: screenshots/issues/82/normal_user_sidebar_collapsed.png')

            # 验证结果
            print('\n' + "=" * 60)
            if sidebar_width_after <= 70 and not workspace_text_visible and not report_text_visible:
                print("测试结果: ✓ 通过")
                print("  - Sidebar 宽度正确收缩")
                print("  - 菜单文字完全隐藏")
            else:
                print("测试结果: ✗ 失败")
                if sidebar_width_after > 70:
                    print(f"  - Sidebar 宽度未正确收缩: {sidebar_width_after}px")
                if workspace_text_visible:
                    print("  - Workspace 文字仍然可见")
                if report_text_visible:
                    print("  - Report 文字仍然可见")
            print("=" * 60)

            # 再次点击展开
            await toggle_btn.click()
            await asyncio.sleep(0.5)

            sidebar_width_final = await sidebar.evaluate('el => el.offsetWidth')
            print(f'\n6. 展开后 Sidebar 宽度: {sidebar_width_final}px')

            # 截图
            await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/82/normal_user_sidebar_expanded.png')
            print(f'   截图已保存: screenshots/issues/82/normal_user_sidebar_expanded.png')

        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(test_sidebar_collapse_normal_user())