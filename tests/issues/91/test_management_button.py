#!/usr/bin/env python3
"""Test script for issue 91: Management button should be disabled for non-admin users."""

import pytest
import asyncio
from playwright.async_api import async_playwright


async def test_management_button_disabled(username: str, password: str, user_type: str):
    """Test that Management button is disabled for non-admin users."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await context.new_page()

        # Visit login page
        await page.goto('http://localhost:5001/login')
        await page.wait_for_load_state('networkidle')

        # Login
        await page.fill('input#username', username)
        await page.fill('input#password', password)

        # Click login and wait for navigation
        async with page.expect_navigation(timeout=10000):
            await page.click('button[type="submit"]')

        # Wait for page to load
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)

        # Navigate to manage mode
        print(f'Navigating to /manage/dashboard...')
        await page.goto('http://localhost:5001/manage/dashboard')
        await page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)

        print(f'\n{"=" * 60}')
        print(f'Testing {user_type} user: {username}')
        print(f'{"=" * 60}')
        print(f'Current URL: {page.url}')

        # Check if manage sidebar exists
        sidebar = page.locator('nav.manage-sidebar')
        sidebar_count = await sidebar.count()
        print(f'Manage sidebar count: {sidebar_count}')

        # Find all nav buttons in sidebar
        nav_buttons = page.locator('nav.manage-sidebar button.nav-item')
        nav_count = await nav_buttons.count()
        print(f'Total nav buttons: {nav_count}')

        # Print all button texts
        for i in range(nav_count):
            btn_text = await nav_buttons.nth(i).inner_text()
            print(f'  Button {i}: {btn_text}')

        # Find the Management button in sidebar
        management_button = page.locator('nav.manage-sidebar button.nav-item:has-text("User Management"), nav.manage-sidebar button.nav-item:has-text("用户管理")')
        button_count = await management_button.count()
        print(f'User Management button count: {button_count}')

        if button_count > 0:
            # Check if button is disabled
            is_disabled = await management_button.first.is_disabled()
            has_disabled_class = await management_button.first.evaluate(
                'el => el.classList.contains("disabled") || el.classList.contains("opacity-50")'
            )

            print(f'Button disabled attribute: {is_disabled}')
            print(f'Button has disabled class: {has_disabled_class}')

            # Get button classes
            button_classes = await management_button.first.get_attribute('class')
            print(f'Button classes: {button_classes}')

            # Try to click the button
            try:
                await management_button.first.click(timeout=2000)
                print('Button was clicked successfully')
            except Exception as e:
                print(f'Button click failed (expected for disabled): {type(e).__name__}')

            # Take screenshot
            screenshot_path = f'/Users/rhuang/workspace/open-ace/screenshots/issues/91/{user_type.lower()}_management_button.png'
            await page.screenshot(path=screenshot_path)
            print(f'Screenshot saved to {screenshot_path}')

            return {
                'user_type': user_type,
                'is_disabled': is_disabled,
                'has_disabled_class': has_disabled_class,
                'button_classes': button_classes,
            }
        else:
            print('Management button not found!')
            return {'user_type': user_type, 'error': 'Button not found'}

        await browser.close()


async def main():
    """Run tests for both admin and non-admin users."""
    import os
    os.makedirs('/Users/rhuang/workspace/open-ace/screenshots/issues/91', exist_ok=True)

    print('\n' + '=' * 60)
    print('Testing Admin user - Management button should be ENABLED')
    print('=' * 60)
    admin_result = await test_management_button_disabled('admin', 'admin123', 'Admin')

    print('\n' + '=' * 60)
    print('Testing Normal user - Management button should be DISABLED')
    print('=' * 60)
    try:
        user_result = await test_management_button_disabled('testuser', 'testuser', 'NormalUser')
    except Exception as e:
        print(f'Normal user test failed: {e}')
        user_result = {'error': str(e)}

    print('\n' + '=' * 60)
    print('Test Results Summary')
    print('=' * 60)
    print(f'Admin user: {admin_result}')
    print(f'Normal user: {user_result}')

    # Verify expectations
    if admin_result.get('is_disabled') == False:
        print('\n✓ Admin user: Management button is enabled (correct)')
    else:
        print('\n✗ Admin user: Management button should be enabled!')

    if user_result.get('is_disabled') == True or user_result.get('has_disabled_class') == True:
        print('✓ Normal user: Management button is disabled (correct)')
    else:
        print('✗ Normal user: Management button should be disabled!')


if __name__ == '__main__':
    asyncio.run(main())