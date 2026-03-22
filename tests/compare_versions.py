#!/usr/bin/env python3
"""
Compare old and new version screenshots for Open ACE.

This script captures screenshots of the same pages from both versions
and saves them for comparison.
"""

import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime

# Configuration
OLD_VERSION_URL = "http://127.0.0.1:5002"
NEW_VERSION_URL = "http://127.0.0.1:5001"
USERNAME = "admin"
PASSWORD = "admin123"

# Pages to capture
PAGES = [
    {"name": "dashboard", "path": "/", "hash": "", "title": "Dashboard"},
    {"name": "messages", "path": "/", "hash": "#messages", "title": "Messages"},
    {"name": "analysis", "path": "/", "hash": "#analysis", "title": "Analysis"},
    {"name": "conversation_history", "path": "/", "hash": "#conversation-history", "title": "Conversation History"},
]

# Screenshot directory
SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/compare"


async def login(page, base_url: str, is_new_version: bool = False):
    """Login to the application."""
    await page.goto(base_url)
    await page.wait_for_load_state("networkidle")
    
    if is_new_version:
        # New version is a React SPA, wait for JS to load
        await asyncio.sleep(2)
        
        # Check if we need to login (look for login form elements)
        try:
            # Try different selectors for React app
            username_selectors = [
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="username"]',
                '#username',
            ]
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]',
                'input[placeholder*="password"]',
                '#password',
            ]
            
            username_input = None
            password_input = None
            
            # Find username input
            for selector in username_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        username_input = selector
                        break
                except:
                    pass
            
            # Find password input
            for selector in password_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        password_input = selector
                        break
                except:
                    pass
            
            if username_input and password_input:
                await page.fill(username_input, USERNAME)
                await page.fill(password_input, PASSWORD)
                # Find and click login button
                login_button_selectors = [
                    'button[type="submit"]',
                    'button:has-text("登录")',
                    'button:has-text("Login")',
                    'button:has-text("Sign in")',
                    '.btn-primary:has-text("登录")',
                ]
                for btn_selector in login_button_selectors:
                    try:
                        btn = page.locator(btn_selector).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            break
                    except:
                        pass
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Login attempt: {e}")
    else:
        # Old version - traditional form with localStorage token
        current_url = page.url
        if "/login" in current_url or "/auth/login" in current_url or "login" in current_url:
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await asyncio.sleep(3)  # Wait for token to be stored and redirect
            await page.wait_for_load_state("networkidle")
            
            # Check if we need to navigate to home
            if "/login" in page.url:
                # Token should be stored, navigate to home
                await page.goto(base_url + "/")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)


async def capture_screenshots(version: str, base_url: str, is_new_version: bool = False):
    """Capture screenshots for a specific version."""
    version_dir = os.path.join(SCREENSHOT_DIR, version)
    os.makedirs(version_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN"
        )
        page = await context.new_page()

        # Login
        print(f"\n[{version.upper()} VERSION] Logging in to {base_url}...")
        await login(page, base_url, is_new_version)
        print(f"[{version.upper()} VERSION] Login successful!")

        # Capture each page
        for page_info in PAGES:
            page_name = page_info["name"]
            page_path = page_info["path"]
            page_hash = page_info.get("hash", "")
            page_title = page_info["title"]

            print(f"[{version.upper()} VERSION] Capturing {page_title}...")

            if is_new_version:
                # New version uses React Router with actual paths
                full_url = f"{base_url}{page_path}"
                if page_name != "dashboard":
                    # Map hash names to actual paths for new version
                    path_map = {
                        "messages": "/messages",
                        "analysis": "/analysis",
                        "conversation_history": "/conversation-history",
                    }
                    if page_name in path_map:
                        full_url = f"{base_url}{path_map[page_name]}"
            else:
                # Old version uses hash-based SPA routing
                full_url = f"{base_url}{page_path}{page_hash}"

            await page.goto(full_url)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)  # Wait for charts/tables to render

            # Take screenshot
            screenshot_path = os.path.join(version_dir, f"{page_name}.png")
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"[{version.upper()} VERSION] Saved: {screenshot_path}")

        await browser.close()

    return version_dir


async def main():
    """Main function to capture and compare screenshots."""
    print("=" * 60)
    print("Open ACE Version Comparison Tool")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Capture old version screenshots
    print("\n" + "-" * 60)
    print("Capturing OLD VERSION (port 5002) screenshots...")
    print("-" * 60)
    old_dir = await capture_screenshots("old", OLD_VERSION_URL, is_new_version=False)
    
    # Capture new version screenshots
    print("\n" + "-" * 60)
    print("Capturing NEW VERSION (port 5001) screenshots...")
    print("-" * 60)
    new_dir = await capture_screenshots("new", NEW_VERSION_URL, is_new_version=True)
    
    print("\n" + "=" * 60)
    print("Screenshot capture complete!")
    print("=" * 60)
    print(f"\nOld version screenshots: {old_dir}")
    print(f"New version screenshots: {new_dir}")
    print("\nPlease compare the screenshots manually to identify differences.")


if __name__ == "__main__":
    asyncio.run(main())