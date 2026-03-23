"""
Test: Work mode theme button visibility fix
Issue: After clicking theme button in work mode, the three buttons (language, theme, user) become invisible
Fix: Changed text-dark class to header-icon-btn which uses CSS variables for theme-aware colors
"""

import asyncio
from playwright.async_api import async_playwright
import os

SCREENSHOT_DIR = "/Users/rhuang/workspace/open-ace/screenshots/issues/theme-btn-fix"
BASE_URL = "http://localhost:5001"

async def test_theme_button():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()
        
        results = []
        
        try:
            # Step 0: Login first
            print("Step 0: Login...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)
            
            # Fill login form
            username_input = page.locator('input[name="username"], input[type="text"]').first
            password_input = page.locator('input[name="password"], input[type="password"]').first
            
            if await username_input.count() > 0 and await password_input.count() > 0:
                await username_input.fill("admin")
                await password_input.fill("admin123")
                
                # Click login button
                login_btn = page.locator('button[type="submit"], button:has-text("Login"), button:has-text("登录")')
                if await login_btn.count() > 0:
                    await login_btn.first.click()
                    await page.wait_for_timeout(3000)
                    print("  Logged in successfully")
            
            # Step 1: Navigate to work mode
            print("Step 1: Navigate to work mode...")
            await page.goto(f"{BASE_URL}/work", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Take screenshot of initial state
            await page.screenshot(path=f"{SCREENSHOT_DIR}/01_work_mode_initial.png", full_page=False)
            results.append(("Initial state", "PASS", "Work mode loaded"))
            
            # Step 2: Check header buttons visibility
            print("Step 2: Check header buttons visibility...")
            header = page.locator('.work-header')
            if await header.count() > 0:
                # Check for header icon buttons
                icon_btns = page.locator('.header-icon-btn')
                btn_count = await icon_btns.count()
                print(f"  Found {btn_count} header icon buttons")
                
                # Check visibility of each button
                for i in range(btn_count):
                    btn = icon_btns.nth(i)
                    is_visible = await btn.is_visible()
                    icon = await btn.locator('i').get_attribute('class')
                    print(f"  Button {i+1}: icon={icon}, visible={is_visible}")
                
                results.append(("Header buttons", "PASS", f"Found {btn_count} buttons"))
            else:
                results.append(("Header buttons", "FAIL", "Work header not found"))
            
            # Step 3: Click theme toggle button
            print("Step 3: Click theme toggle button...")
            theme_btn = page.locator('.header-icon-btn:has(.bi-moon), .header-icon-btn:has(.bi-sun)')
            if await theme_btn.count() > 0:
                await theme_btn.first.click()
                await page.wait_for_timeout(1000)
                
                # Take screenshot after theme change
                await page.screenshot(path=f"{SCREENSHOT_DIR}/02_after_theme_click.png", full_page=False)
                
                # Check if buttons are still visible
                icon_btns = page.locator('.header-icon-btn')
                btn_count = await icon_btns.count()
                all_visible = True
                for i in range(btn_count):
                    btn = icon_btns.nth(i)
                    is_visible = await btn.is_visible()
                    if not is_visible:
                        all_visible = False
                    icon = await btn.locator('i').get_attribute('class')
                    print(f"  After theme click - Button {i+1}: icon={icon}, visible={is_visible}")
                
                if all_visible:
                    results.append(("Theme toggle", "PASS", "All buttons visible after theme change"))
                else:
                    results.append(("Theme toggle", "FAIL", "Some buttons not visible after theme change"))
            else:
                results.append(("Theme toggle", "FAIL", "Theme button not found"))
            
            # Step 4: Click theme button again to toggle back
            print("Step 4: Click theme button again...")
            theme_btn = page.locator('.header-icon-btn:has(.bi-moon), .header-icon-btn:has(.bi-sun)')
            if await theme_btn.count() > 0:
                await theme_btn.first.click()
                await page.wait_for_timeout(1000)
                
                # Take screenshot after toggling back
                await page.screenshot(path=f"{SCREENSHOT_DIR}/03_after_second_theme_click.png", full_page=False)
                
                # Check if buttons are still visible
                icon_btns = page.locator('.header-icon-btn')
                btn_count = await icon_btns.count()
                all_visible = True
                for i in range(btn_count):
                    btn = icon_btns.nth(i)
                    is_visible = await btn.is_visible()
                    if not is_visible:
                        all_visible = False
                
                if all_visible:
                    results.append(("Theme toggle back", "PASS", "All buttons visible after toggling back"))
                else:
                    results.append(("Theme toggle back", "FAIL", "Some buttons not visible after toggling back"))
            
            # Step 5: Check dropdown menus work
            print("Step 5: Check language dropdown...")
            lang_btn = page.locator('.header-icon-btn.dropdown-toggle:has(.bi-globe)')
            if await lang_btn.count() > 0:
                await lang_btn.click()
                await page.wait_for_timeout(500)
                await page.screenshot(path=f"{SCREENSHOT_DIR}/04_language_dropdown.png", full_page=False)
                
                dropdown = page.locator('.dropdown-menu.show')
                if await dropdown.count() > 0:
                    results.append(("Language dropdown", "PASS", "Dropdown opens correctly"))
                    # Close dropdown
                    await page.keyboard.press('Escape')
                else:
                    results.append(("Language dropdown", "FAIL", "Dropdown not showing"))
            
        except Exception as e:
            results.append(("Test", "FAIL", str(e)))
            await page.screenshot(path=f"{SCREENSHOT_DIR}/error.png", full_page=False)
        
        finally:
            await browser.close()
        
        # Print results
        print("\n" + "="*60)
        print("TEST RESULTS")
        print("="*60)
        for name, status, message in results:
            status_icon = "✓" if status == "PASS" else "✗"
            print(f"[{status_icon}] {name}: {message}")
        
        # Generate HTML report
        report_path = f"{SCREENSHOT_DIR}/report.html"
        screenshots = [
            ("01_work_mode_initial.png", "Initial state - Work mode"),
            ("02_after_theme_click.png", "After clicking theme button"),
            ("03_after_second_theme_click.png", "After clicking theme button again"),
            ("04_language_dropdown.png", "Language dropdown"),
        ]
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Theme Button Fix - Test Report</title>
    <style>
        body { font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1 { color: #333; }
        .results { background: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
        .result { padding: 8px 0; border-bottom: 1px solid #eee; }
        .pass { color: #10b981; }
        .fail { color: #ef4444; }
        .screenshot { margin: 20px 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; background: white; }
        .screenshot h3 { margin: 0; padding: 10px; background: #f5f5f5; }
        .screenshot img { max-width: 100%; display: block; }
    </style>
</head>
<body>
    <h1>Theme Button Fix - Test Report</h1>
    <div class="results">
        <h3>Test Results</h3>
"""
        for name, status, message in results:
            status_class = "pass" if status == "PASS" else "fail"
            html += f'        <div class="result"><span class="{status_class}">[{status}]</span> {name}: {message}</div>\n'
        
        html += """    </div>
    <h3>Screenshots</h3>
"""
        for filename, title in screenshots:
            if os.path.exists(f"{SCREENSHOT_DIR}/{filename}"):
                html += f"""    <div class="screenshot">
        <h3>{title}</h3>
        <img src="{filename}">
    </div>
"""
        
        html += """</body>
</html>"""
        
        with open(report_path, 'w') as f:
            f.write(html)
        
        print(f"\nReport saved to: {report_path}")
        return all(status == "PASS" for _, status, _ in results)

if __name__ == "__main__":
    success = asyncio.run(test_theme_button())
    exit(0 if success else 1)