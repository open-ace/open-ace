#!/usr/bin/env python3
"""Test script for issue 82: Sidebar collapse functionality."""

import pytest
import asyncio
from playwright.async_api import async_playwright


@pytest.mark.asyncio
async def test_sidebar_collapse():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await context.new_page()
        
        # 访问登录页面
        await page.goto('http://localhost:5001/login')
        await page.wait_for_load_state('networkidle')
        
        # 登录 admin
        await page.fill('input[name="username"]', 'admin')
        await page.fill('input[name="password"]', 'admin123')
        
        # 点击登录并等待导航
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')
        
        # 等待页面加载完成
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(1)
        
        # 检查 sidebar 初始状态
        sidebar = page.locator('#sidebar')
        sidebar_class = await sidebar.get_attribute('class')
        print(f'Sidebar 初始 class: {sidebar_class}')
        
        # 检查收缩按钮是否存在
        toggle_btn = page.locator('#sidebar-toggle')
        toggle_count = await toggle_btn.count()
        print(f'收缩按钮存在: {toggle_count > 0}')
        
        # 获取 sidebar 宽度
        sidebar_width = await sidebar.evaluate('el => el.offsetWidth')
        print(f'Sidebar 初始宽度: {sidebar_width}px')
        
        # 点击收缩按钮
        await toggle_btn.click()
        await asyncio.sleep(0.5)
        
        # 检查 sidebar 是否收缩
        sidebar_class_after = await sidebar.get_attribute('class')
        print(f'点击后 Sidebar class: {sidebar_class_after}')
        
        sidebar_width_after = await sidebar.evaluate('el => el.offsetWidth')
        print(f'点击后 Sidebar 宽度: {sidebar_width_after}px')
        
        # 检查 localStorage 是否保存了状态
        is_collapsed = await page.evaluate('() => localStorage.getItem("sidebar_collapsed")')
        print(f'localStorage sidebar_collapsed: {is_collapsed}')
        
        # 截图
        await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/82/sidebar_collapsed.png')
        print()
        print('截图已保存到 screenshots/issues/82/sidebar_collapsed.png')
        
        # 再次点击展开
        await toggle_btn.click()
        await asyncio.sleep(0.5)
        
        sidebar_class_final = await sidebar.get_attribute('class')
        print(f'再次点击后 Sidebar class: {sidebar_class_final}')
        
        sidebar_width_final = await sidebar.evaluate('el => el.offsetWidth')
        print(f'再次点击后 Sidebar 宽度: {sidebar_width_final}px')
        
        # 截图
        await page.screenshot(path='/Users/rhuang/workspace/open-ace/screenshots/issues/82/sidebar_expanded.png')
        print('截图已保存到 screenshots/issues/82/sidebar_expanded.png')
        
        await browser.close()


if __name__ == '__main__':
    asyncio.run(test_sidebar_collapse())