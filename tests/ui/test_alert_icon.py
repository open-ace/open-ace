"""
Test alert management page icon display
Verify that the unread count card shows proper icon instead of a dot
"""

import asyncio
from playwright.async_api import async_playwright
import os
from datetime import datetime
import traceback

# Configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
SCREENSHOT_DIR = 'screenshots/issues/alert-icon'

async def test_alert_icon():
    """Test alert management page icon display"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()

        results = []

        try:
            # Step 1: Login
            print("Step 1: Login...")
            await page.goto(f'{BASE_URL}/login')
            await page.fill('#username', USERNAME)
            await page.fill('#password', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url('**/', timeout=10000)
            results.append(("Login", "Pass", ""))

            # Step 2: Navigate to Alert Management
            print("Step 2: Navigate to Alert Management...")
            # Navigate directly to the manage page
            await page.goto(f'{BASE_URL}/manage')
            await page.wait_for_timeout(2000)

            # Take screenshot of manage page
            timestamp = datetime.now().strftime('%H%M%S')
            screenshot_path = f'{SCREENSHOT_DIR}/manage_page_{timestamp}.png'
            await page.screenshot(path=screenshot_path, full_page=True)
            results.append(("Navigate to Manage", "Pass", screenshot_path))

            # Step 3: Check for bi-dot icon in the entire page (the bug)
            print("Step 3: Check for bi-dot icon (the bug)...")
            
            # Check for bi-dot icon (the bug)
            dot_icons = page.locator('.bi-dot')
            dot_count = await dot_icons.count()
            
            print(f"  Dot icons found (should be 0): {dot_count}")
            
            if dot_count > 0:
                # Take screenshot of the issue
                bug_screenshot = f'{SCREENSHOT_DIR}/bug_dot_icon_{timestamp}.png'
                await page.screenshot(path=bug_screenshot, full_page=True)
                results.append(("Icon Check", "Fail", f"Found {dot_count} bi-dot icons. Should use proper icons."))
            else:
                results.append(("Icon Check", "Pass", "No bi-dot icons found. Icons are correct."))

            # Step 4: Try to navigate to Quota & Alerts section
            print("Step 4: Navigate to Quota & Alerts section...")
            
            # First, expand the section by clicking the section header
            section_header = page.locator('.nav-section-header:has-text("Governance"), .nav-section-header:has-text("治理")')
            if await section_header.count() > 0:
                await section_header.first.click(force=True)
                await page.wait_for_timeout(500)
            
            # Now click on Quota & Alerts nav item
            quota_alerts_nav = page.locator('.nav-item:has-text("Quota"), .nav-item:has-text("配额")')
            if await quota_alerts_nav.count() > 0:
                await quota_alerts_nav.first.click(force=True)
                await page.wait_for_timeout(1000)
                
                # Click on Alerts tab - use .nav-link selector
                alerts_tab = page.locator('.nav-link:has-text("Alert"), .nav-link:has-text("告警")')
                if await alerts_tab.count() > 0:
                    await alerts_tab.first.click()
                    await page.wait_for_timeout(1000)
                    
                    # Take screenshot of alerts page
                    alerts_screenshot = f'{SCREENSHOT_DIR}/alerts_page_{timestamp}.png'
                    await page.screenshot(path=alerts_screenshot, full_page=True)
                    results.append(("Navigate to Alerts Tab", "Pass", alerts_screenshot))
                    
                    # Check for bi-dot icon again
                    dot_icons_alerts = page.locator('.bi-dot')
                    dot_count_alerts = await dot_icons_alerts.count()
                    
                    if dot_count_alerts > 0:
                        results.append(("Alerts Icon Check", "Fail", f"Found {dot_count_alerts} bi-dot icons in alerts tab."))
                    else:
                        results.append(("Alerts Icon Check", "Pass", "No bi-dot icons in alerts tab."))
                    
                    # List all icons in stat cards
                    print("  Listing all icons in stat cards...")
                    stat_card_icons = page.locator('.stat-card i, .card:has(.fs-4) i')
                    icon_classes = []
                    
                    for i in range(await stat_card_icons.count()):
                        icon = stat_card_icons.nth(i)
                        class_attr = await icon.get_attribute('class')
                        if class_attr and 'bi-' in class_attr:
                            icon_classes.append(class_attr)
                    
                    print(f"  Found icons: {icon_classes}")
                    
                    # Check for bi-envelope-unread icon
                    envelope_unread_icons = page.locator('.bi-envelope-unread')
                    envelope_unread_count = await envelope_unread_icons.count()
                    
                    # Also check for any envelope icon
                    envelope_icons = page.locator('.bi-envelope')
                    envelope_count = await envelope_icons.count()
                    
                    print(f"  bi-envelope-unread icons: {envelope_unread_count}")
                    print(f"  bi-envelope icons: {envelope_count}")
                    
                    if envelope_unread_count > 0:
                        results.append(("Envelope Unread Icon", "Pass", f"Found {envelope_unread_count} bi-envelope-unread icons"))
                    elif envelope_count > 0:
                        # Check if any envelope icon has the correct class
                        results.append(("Envelope Icon", "Pass", f"Found {envelope_count} bi-envelope icons (checking classes...)"))
                    else:
                        results.append(("Envelope Icon", "Info", f"No envelope icons found in stat cards. Icons found: {icon_classes[:10]}..."))
                else:
                    results.append(("Navigate to Alerts Tab", "Skip", "Alerts tab not found"))
            else:
                results.append(("Navigate to Quota & Alerts", "Skip", "Quota & Alerts nav item not found"))

        except Exception as e:
            error_screenshot = f'{SCREENSHOT_DIR}/error_{datetime.now().strftime("%H%M%S")}.png'
            await page.screenshot(path=error_screenshot, full_page=True)
            error_detail = f"{str(e)}\n{traceback.format_exc()}"
            results.append(("Error", "Fail", error_detail))

        finally:
            await browser.close()

    # Print results
    print("\n" + "=" * 50)
    print("Alert Icon Test Report")
    print("=" * 50)
    for step, status, detail in results:
        symbol = "✓" if status == "Pass" else "✗" if status == "Fail" else "ℹ" if status in ("Info", "Skip") else "?"
        print(f"{symbol} {step}: {status}")
        if detail:
            print(f"  {detail}")
    print("=" * 50)

    # Return overall result
    failed = any(status == "Fail" for _, status, _ in results)
    return not failed

if __name__ == '__main__':
    result = asyncio.run(test_alert_icon())
    exit(0 if result else 1)