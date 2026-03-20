"""
Test issue 91: My Usage Report page should have Token Usage and Request Chart instead of Usage By Tool.

This test verifies that:
1. The report page displays two separate charts: Token Usage and Request Chart
2. Both charts are visible and rendered correctly
3. Language switching updates chart titles correctly
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.sync_api import sync_playwright, expect
import time

# Test configuration
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5001/')
USERNAME = os.environ.get('USERNAME', 'testuser91')
PASSWORD = os.environ.get('PASSWORD', 'test123')
HEADLESS = os.environ.get('HEADLESS', 'true').lower() == 'true'
VIEWPORT_SIZE = {'width': 1400, 'height': 900}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'screenshots', 'issues', '91')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_report_charts():
    """Test that My Usage Report page has Token Usage and Request Chart."""
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()
        
        test_results = []
        
        try:
            # Step 1: Navigate to login page
            print("Step 1: Navigate to login page...")
            page.goto(BASE_URL + 'login')
            page.wait_for_load_state('networkidle')
            time.sleep(1)
            
            # Step 2: Login
            print("Step 2: Login...")
            page.fill('#username', USERNAME)
            page.fill('#password', PASSWORD)
            page.click('#login-btn')
            
            # Wait for sidebar to appear (indicates successful login)
            page.wait_for_selector('#sidebar', timeout=15000)
            time.sleep(2)
            
            # Verify login success
            expect(page.locator('#sidebar')).to_be_visible()
            test_results.append(("Login", "PASS", "Successfully logged in"))
            
            # Step 3: Navigate to My Usage Report
            print("Step 3: Navigate to My Usage Report...")
            page.click('#nav-report')
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            
            # Verify report section is visible
            expect(page.locator('#report-section')).to_be_visible()
            test_results.append(("Navigate to Report", "PASS", "Report section is visible"))
            
            # Take screenshot of report page
            screenshot_path = os.path.join(SCREENSHOT_DIR, 'report_page_initial.png')
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")
            
            # Step 4: Verify Token Usage chart exists
            print("Step 4: Verify Token Usage chart...")
            token_usage_title = page.locator('#token-usage-title')
            expect(token_usage_title).to_be_visible()
            token_title_text = token_usage_title.text_content()
            print(f"Token Usage title: {token_title_text}")
            
            token_chart = page.locator('#reportTokenUsageChart')
            expect(token_chart).to_be_visible()
            test_results.append(("Token Usage Chart", "PASS", f"Title: {token_title_text}"))
            
            # Step 5: Verify Request Chart exists
            print("Step 5: Verify Request Chart...")
            request_chart_title = page.locator('#request-chart-title')
            expect(request_chart_title).to_be_visible()
            request_title_text = request_chart_title.text_content()
            print(f"Request Chart title: {request_title_text}")
            
            request_chart = page.locator('#reportRequestChart')
            expect(request_chart).to_be_visible()
            test_results.append(("Request Chart", "PASS", f"Title: {request_title_text}"))
            
            # Step 6: Verify old "Usage by Tool" chart no longer exists
            print("Step 6: Verify old chart no longer exists...")
            old_chart = page.locator('#reportByToolChart')
            if old_chart.count() == 0:
                test_results.append(("Old Chart Removed", "PASS", "Old 'Usage by Tool' chart removed"))
            else:
                test_results.append(("Old Chart Removed", "FAIL", "Old 'Usage by Tool' chart still exists"))
            
            # Step 7: Test language switching (English to Chinese)
            print("Step 7: Test language switching...")
            # Select Chinese
            page.select_option('#lang-select', 'zh')
            time.sleep(1)
            page.wait_for_load_state('networkidle')
            
            # Verify Chinese titles
            token_title_zh = page.locator('#token-usage-title').text_content()
            request_title_zh = page.locator('#request-chart-title').text_content()
            print(f"Token Usage title (Chinese): {token_title_zh}")
            print(f"Request Chart title (Chinese): {request_title_zh}")
            
            # Take screenshot with Chinese language
            screenshot_path_zh = os.path.join(SCREENSHOT_DIR, 'report_page_chinese.png')
            page.screenshot(path=screenshot_path_zh)
            print(f"Screenshot saved: {screenshot_path_zh}")
            
            # Verify Chinese translations
            if 'Token 用量' in token_title_zh:
                test_results.append(("Chinese Token Title", "PASS", f"Title: {token_title_zh}"))
            else:
                test_results.append(("Chinese Token Title", "FAIL", f"Expected 'Token 用量', got '{token_title_zh}'"))
            
            if '请求图表' in request_title_zh:
                test_results.append(("Chinese Request Title", "PASS", f"Title: {request_title_zh}"))
            else:
                test_results.append(("Chinese Request Title", "FAIL", f"Expected '请求图表', got '{request_title_zh}'"))
            
            # Switch back to English
            page.select_option('#lang-select', 'en')
            time.sleep(1)
            page.wait_for_load_state('networkidle')
            
            token_title_en = page.locator('#token-usage-title').text_content()
            request_title_en = page.locator('#request-chart-title').text_content()
            
            if 'Token Usage' in token_title_en:
                test_results.append(("English Token Title", "PASS", f"Title: {token_title_en}"))
            else:
                test_results.append(("English Token Title", "FAIL", f"Expected 'Token Usage', got '{token_title_en}'"))
            
            if 'Request Chart' in request_title_en:
                test_results.append(("English Request Title", "PASS", f"Title: {request_title_en}"))
            else:
                test_results.append(("English Request Title", "FAIL", f"Expected 'Request Chart', got '{request_title_en}'"))
            
            # Take final screenshot
            screenshot_path_final = os.path.join(SCREENSHOT_DIR, 'report_page_final.png')
            page.screenshot(path=screenshot_path_final)
            print(f"Screenshot saved: {screenshot_path_final}")
            
        except Exception as e:
            test_results.append(("Error", "FAIL", str(e)))
            # Take error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, 'error_screenshot.png')
            page.screenshot(path=error_screenshot)
            print(f"Error screenshot saved: {error_screenshot}")
        
        finally:
            browser.close()
        
        # Print test report
        print("\n" + "=" * 60)
        print("UI Test Report - Issue 91")
        print("=" * 60)
        print(f"Test Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Tests: {len(test_results)}")
        
        passed = sum(1 for r in test_results if r[1] == "PASS")
        failed = sum(1 for r in test_results if r[1] == "FAIL")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print("-" * 60)
        
        for name, status, message in test_results:
            status_icon = "✓" if status == "PASS" else "✗"
            print(f"  [{status_icon}] {name}: {message}")
        
        print("-" * 60)
        print(f"Screenshots saved in: {SCREENSHOT_DIR}")
        print("=" * 60)
        
        return failed == 0


if __name__ == '__main__':
    success = test_report_charts()
    sys.exit(0 if success else 1)