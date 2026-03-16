"""
UI 测试: Issue 68 - Data Status 远程机器状态检查功能

测试目标:
1. 验证 Data Status 面板显示正确
2. 验证刷新按钮存在并可点击
3. 验证远程机器状态检查功能

测试步骤:
1. 登录系统
2. 导航到 Dashboard 页面
3. 检查 Data Status 面板
4. 点击刷新按钮
5. 验证远程机器状态更新
"""

import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.sync_api import sync_playwright, expect

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001')
USERNAME = os.environ.get('USERNAME', 'admin')
PASSWORD = os.environ.get('PASSWORD', 'admin123')
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots', 'issues', '68')


def ensure_screenshot_dir():
    """Ensure screenshot directory exists."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def take_screenshot(page, name):
    """Take screenshot and save to issue directory."""
    path = os.path.join(SCREENSHOT_DIR, name)
    page.screenshot(path=path)
    print(f"  截图保存: {path}")
    return path


def test_data_status():
    """Test Data Status remote host check functionality."""
    print("\n" + "=" * 60)
    print("UI 测试: Issue 68 - Data Status 远程机器状态检查")
    print("=" * 60)
    
    ensure_screenshot_dir()
    screenshots = []
    test_passed = True
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()
        
        try:
            # Step 1: Login
            print("\n步骤 1: 登录系统")
            page.goto(f'{BASE_URL}/login')
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            
            # Wait for redirect (could be / or /dashboard)
            page.wait_for_url('**/', timeout=10000)
            print("  ✓ 登录成功")
            screenshots.append(take_screenshot(page, '01_login.png'))
            
            # Step 2: Check Data Status panel
            print("\n步骤 2: 检查 Data Status 面板")
            
            # Wait for page to fully load
            time.sleep(3)
            
            # Wait for sidebar to load
            page.wait_for_selector('#data-status-container', timeout=10000)
            
            # Wait for data status to be populated
            time.sleep(2)
            
            print("  ✓ Data Status 面板已加载")
            
            # Check if refresh button exists
            refresh_btn = page.locator('#data-status-container .refresh-btn')
            if refresh_btn.count() > 0:
                print("  ✓ 刷新按钮存在")
            else:
                print("  ⚠ 刷新按钮不存在，等待加载...")
                time.sleep(3)
                refresh_btn = page.locator('#data-status-container .refresh-btn')
                if refresh_btn.count() > 0:
                    print("  ✓ 刷新按钮存在")
                else:
                    print("  ✗ 刷新按钮不存在")
                    test_passed = False
            
            screenshots.append(take_screenshot(page, '02_data_status_panel.png'))
            
            # Step 3: Check host items
            print("\n步骤 3: 检查主机状态项")
            host_items = page.locator('.data-status-item')
            host_count = host_items.count()
            print(f"  发现 {host_count} 个主机")
            
            for i in range(host_count):
                item = host_items.nth(i)
                host_name = item.locator('.host-name').text_content()
                last_updated = item.locator('.last-updated').text_content()
                
                # Check status indicator class
                item_class = item.get_attribute('class')
                status = 'unknown'
                if 'status-online' in item_class:
                    status = 'online'
                elif 'status-offline' in item_class:
                    status = 'offline'
                elif 'status-fresh' in item_class:
                    status = 'fresh'
                elif 'status-recent' in item_class:
                    status = 'recent'
                elif 'status-stale' in item_class:
                    status = 'stale'
                
                print(f"  - {host_name}: {last_updated} ({status})")
            
            # Step 4: Click refresh button
            print("\n步骤 4: 点击刷新按钮检查远程机器状态")
            if refresh_btn.count() > 0:
                refresh_btn.click()
                print("  ✓ 已点击刷新按钮")
                
                # Wait for loading state
                time.sleep(1)
                screenshots.append(take_screenshot(page, '03_refresh_loading.png'))
                
                # Wait for response (up to 15 seconds for SSH check)
                print("  等待远程机器状态检查...")
                time.sleep(10)
                
                # Check if refresh button is no longer loading
                refresh_btn_after = page.locator('#data-status-container .refresh-btn')
                if refresh_btn_after.count() > 0:
                    btn_class = refresh_btn_after.get_attribute('class')
                    if 'loading' not in btn_class:
                        print("  ✓ 刷新完成")
                    else:
                        print("  ⚠ 刷新仍在进行中")
                
                screenshots.append(take_screenshot(page, '04_after_refresh.png'))
                
                # Step 5: Verify remote host status
                print("\n步骤 5: 验证远程机器状态")
                host_items_after = page.locator('.data-status-item')
                for i in range(host_items_after.count()):
                    item = host_items_after.nth(i)
                    host_name = item.locator('.host-name').text_content()
                    item_class = item.get_attribute('class')
                    
                    if 'is_remote' in str(item) or 'ai-lab' in host_name or 'remote' in host_name.lower():
                        if 'status-online' in item_class:
                            print(f"  ✓ {host_name}: 在线")
                        elif 'status-offline' in item_class:
                            print(f"  ✓ {host_name}: 离线（状态检查正常工作）")
                        else:
                            print(f"  - {host_name}: 状态未知")
            else:
                print("  ✗ 无法找到刷新按钮")
                test_passed = False
            
            # Final screenshot
            screenshots.append(take_screenshot(page, '05_final.png'))
            
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            test_passed = False
            screenshots.append(take_screenshot(page, 'error.png'))
        
        finally:
            browser.close()
    
    # Print test report
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)
    print(f"测试状态: {'通过 ✓' if test_passed else '失败 ✗'}")
    print(f"\n截图文件:")
    for s in screenshots:
        print(f"  - {s}")
    print("=" * 60)
    
    return test_passed


if __name__ == '__main__':
    success = test_data_status()
    sys.exit(0 if success else 1)