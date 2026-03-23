"""
Test sidebar collapse functionality - verify session history icon is visible when collapsed
"""
import asyncio
from playwright.async_api import async_playwright
import os

async def test_sidebar_collapse():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1400, 'height': 900})
        page = await context.new_page()
        
        try:
            # Navigate to login page
            await page.goto('http://localhost:5001/login')
            await page.wait_for_load_state('networkidle')
            
            # Login
            await page.fill('#username', 'admin')
            await page.fill('#password', 'admin123')
            await page.click('button[type="submit"]')
            
            # Wait for redirect after login
            await page.wait_for_url('**/work**', timeout=10000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            
            screenshots_dir = '/Users/rhuang/workspace/open-ace/screenshots'
            os.makedirs(screenshots_dir, exist_ok=True)
            
            # Take screenshot of expanded state
            await page.screenshot(path=f'{screenshots_dir}/sidebar_expanded.png', full_page=False)
            print("✓ Saved: sidebar_expanded.png")
            
            # Check work-nav items in expanded state
            work_nav_items = page.locator('.work-nav-item')
            count = await work_nav_items.count()
            print(f"\nExpanded state - Found {count} work-nav items:")
            for i in range(count):
                item = work_nav_items.nth(i)
                text = await item.inner_text()
                is_visible = await item.is_visible()
                print(f"  - Item {i+1}: '{text}', visible: {is_visible}")
            
            # Find and click the collapse button for left panel
            collapse_btn = page.locator('.work-left-panel .panel-toggle')
            if await collapse_btn.count() > 0:
                await collapse_btn.click()
                await asyncio.sleep(1)
                
                # Take screenshot of collapsed state
                await page.screenshot(path=f'{screenshots_dir}/sidebar_collapsed.png', full_page=False)
                print("\n✓ Saved: sidebar_collapsed.png")
                
                # Check work-nav items in collapsed state
                work_nav_items = page.locator('.work-nav-item')
                count = await work_nav_items.count()
                print(f"\nCollapsed state - Found {count} work-nav items:")
                for i in range(count):
                    item = work_nav_items.nth(i)
                    text = await item.inner_text()
                    is_visible = await item.is_visible()
                    # Check if icon is visible
                    icon = item.locator('i')
                    icon_visible = await icon.count() > 0 and await icon.nth(0).is_visible()
                    print(f"  - Item {i+1}: '{text}', visible: {is_visible}, icon_visible: {icon_visible}")
                
                # Check panel header
                panel_title = page.locator('.work-left-panel .panel-title')
                if await panel_title.count() > 0:
                    title_visible = await panel_title.is_visible()
                    print(f"\nPanel title visible: {title_visible}")
                
                # Check session list collapsed
                session_list_collapsed = page.locator('.session-list-collapsed')
                if await session_list_collapsed.count() > 0:
                    print("✓ Session list collapsed view exists")
                else:
                    print("✗ Session list collapsed view NOT found")
            else:
                print("✗ Collapse button not found")
            
            # Generate HTML report
            html_report = f'''<!DOCTYPE html>
<html>
<head>
    <title>Sidebar Collapse Test Report</title>
    <style>
        body {{ font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .screenshot {{ margin: 20px 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
        .screenshot h3 {{ margin: 0; padding: 10px; background: #f5f5f5; }}
        .screenshot img {{ max-width: 100%; display: block; }}
    </style>
</head>
<body>
    <h1>Sidebar Collapse Test Report</h1>
    <p>测试：菜单栏收缩后对话历史图标是否可见</p>
    
    <div class="screenshot">
        <h3>展开状态 (Expanded)</h3>
        <img src="sidebar_expanded.png">
    </div>
    
    <div class="screenshot">
        <h3>收缩状态 (Collapsed)</h3>
        <img src="sidebar_collapsed.png">
    </div>
</body>
</html>'''
            
            report_path = f'{screenshots_dir}/sidebar_collapse_report.html'
            with open(report_path, 'w') as f:
                f.write(html_report)
            print(f"\n✓ Report saved: {report_path}")
            
            # Open report
            os.system(f'open {report_path}')
            
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(test_sidebar_collapse())