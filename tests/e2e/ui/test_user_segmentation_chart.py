#!/usr/bin/env python3
"""
User Segmentation Pie Chart UI Test

Test the enhanced user segmentation pie chart features:
1. Tooltip enhancement: display user count, percentage, and description
2. Internationalization: segmentation names and descriptions in different languages
3. Help tooltip: info icon tooltip showing segmentation standard
4. Responsive layout: small screen optimization
"""

import os
import sys

# Add the project root to the path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, project_root)

import time

import pytest
from playwright.sync_api import sync_playwright

# Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
MOBILE_VIEWPORT_SIZE = {"width": 375, "height": 667}

# Screenshot directory
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots"
)


def take_screenshot(page, name):
    """Take a screenshot and save it."""
    path = os.path.join(SCREENSHOT_DIR, f"user_segmentation_{name}.png")
    page.screenshot(path=path)
    print(f"  Screenshot saved: {path}")
    return path


def login(page):
    """Login to the application."""
    print("\n[Login] Logging in...")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    time.sleep(2)


def navigate_to_analysis(page):
    """Navigate to the Trend Analysis page hosting the user segmentation card."""
    print("\n[Navigate] Going to Analysis page...")
    # The manage sidebar groups nav items under collapsible sections; clicking
    # the section-hidden link is intercepted by the section header. Navigate
    # straight to the current route instead.
    page.goto(f"{BASE_URL.rstrip('/')}/manage/analysis/trend")
    page.wait_for_load_state("networkidle")
    time.sleep(3)


def change_language(page, language_code):
    """Change the language setting."""
    print(f"\n[Language] Changing to {language_code}...")
    # Language dropdown lives in the header: a dropdown-toggle button with a
    # globe icon. Clicking the icon itself does not open the menu; the toggle
    # button does. Items are labeled in the CURRENT language, so try both.
    language_names = {
        "en": ["English", "英语"],
        "zh": ["Chinese", "中文"],
        "ja": ["Japanese", "日语"],
        # Items are labeled in the CURRENT language: reaching ko goes
        # en -> zh -> ja -> ko, so the ja-state label 韓国語 is the one
        # that has to match here.
        "ko": ["Korean", "韩语", "韓国語"],
    }

    lang_toggle = page.locator("button.header-icon-btn.dropdown-toggle:has(i.bi-globe)").first
    if not lang_toggle.is_visible():
        print("  ⚠ Language toggle not found")
        return False
    lang_toggle.click()
    page.wait_for_timeout(300)

    for name in language_names.get(language_code, [language_code]):
        lang_option = page.locator("button.dropdown-item").filter(has_text=name).first
        if lang_option.count() > 0 and lang_option.is_visible():
            lang_option.click()
            time.sleep(1)
            print(f"  ✓ Language changed to {language_code}")
            return True

    print("  ⚠ Language change not successful")
    return False


def test_user_segmentation_tooltip(
    ui_screenshot_dir,
):  # allow-no-assert: smoke test - visual verification only
    """Test tooltip enhancement for user segmentation pie chart."""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate and login
            print("\n[Step 1] Navigate to login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "01_login"))

            login(page)
            screenshots.append(take_screenshot(page, "02_after_login"))

            # Step 2: Navigate to Analysis
            navigate_to_analysis(page)
            screenshots.append(take_screenshot(page, "03_analysis"))

            # Step 3: Find User Segmentation card
            print("\n[Step 3] Find User Segmentation card")
            user_seg_card = page.locator(
                '.card:has-text("User Segmentation"), .card:has-text("用户分层")'
            )
            if user_seg_card.count() > 0:
                print("  ✓ User Segmentation card found")

                # Check for info icon (help tooltip)
                info_icon = user_seg_card.locator(".bi-info-circle")
                if info_icon.count() > 0:
                    print("  ✓ Info icon found for help tooltip")

                    # Hover over info icon to trigger tooltip
                    info_icon.first.hover()
                    time.sleep(0.5)
                    screenshots.append(take_screenshot(page, "04_info_icon_hover"))

                    # Check if tooltip appears
                    tooltip = page.locator('.tooltip, [role="tooltip"]')
                    if tooltip.count() > 0:
                        tooltip_text = tooltip.first.text_content()
                        print(f"  ✓ Tooltip appeared with text: {tooltip_text[:50]}...")
                    else:
                        print("  ⚠ Tooltip not found (may need Bootstrap initialization)")
                else:
                    print("  ⚠ Info icon not found")

                # Step 4: Check doughnut chart canvas
                print("\n[Step 4] Check doughnut chart")
                chart_canvas = user_seg_card.locator("canvas")
                if chart_canvas.count() > 0:
                    print("  ✓ Doughnut chart canvas found")

                    # Try hovering over chart to trigger tooltip
                    # Note: Chart.js tooltip is triggered by mouse movement over segments
                    chart_canvas.first.hover()
                    time.sleep(0.5)
                    screenshots.append(take_screenshot(page, "05_chart_hover"))
                else:
                    print("  ✗ Chart canvas not found")

                # Step 5: Check for "No data available" message
                print("\n[Step 5] Check data availability")
                no_data_msg = user_seg_card.locator('text="No data available", text="暂无数据"')
                if no_data_msg.count() > 0:
                    print("  ⚠ No data available - cannot test tooltip content")
                else:
                    print("  ✓ Data is present in the chart")
            else:
                print("  ✗ User Segmentation card not found")

            # Step 6: Final screenshot
            print("\n[Step 6] Final screenshot")
            screenshots.append(take_screenshot(page, "06_final"))

            # Summary
            print("\n" + "=" * 50)
            print("User Segmentation Tooltip Test Summary")
            print("=" * 50)
            print(f"Screenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error_tooltip"))
            raise
        finally:
            browser.close()

    return screenshots


def test_user_segmentation_i18n(
    ui_screenshot_dir,
):  # allow-no-assert: smoke test - visual verification only
    """Test internationalization for user segmentation."""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate and login
            print("\n[Step 1] Navigate and login")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "i18n_01_login"))

            login(page)
            screenshots.append(take_screenshot(page, "i18n_02_after_login"))

            # Step 2: Navigate to Analysis
            navigate_to_analysis(page)
            screenshots.append(take_screenshot(page, "i18n_03_analysis_en"))

            # Step 3: Verify English labels
            print("\n[Step 3] Verify English labels")
            user_seg_card_en = page.locator('.card-title:has-text("User Segmentation")')
            if user_seg_card_en.count() > 0:
                print("  ✓ English card title found: 'User Segmentation'")
            else:
                print("  ✗ English card title not found")

            # Step 4: Change to Chinese
            print("\n[Step 4] Change to Chinese")
            change_language(page, "zh")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "i18n_04_analysis_zh"))

            # Verify Chinese labels
            user_seg_card_zh = page.locator('.card-title:has-text("用户分层")')
            if user_seg_card_zh.count() > 0:
                print("  ✓ Chinese card title found: '用户分层'")
            else:
                print("  ✗ Chinese card title not found")

            # Step 5: Change to Japanese
            print("\n[Step 5] Change to Japanese")
            change_language(page, "ja")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "i18n_05_analysis_ja"))

            # Verify Japanese labels
            user_seg_card_ja = page.locator('.card-title:has-text("ユーザーセグメンテーション")')
            if user_seg_card_ja.count() > 0:
                print("  ✓ Japanese card title found")
            else:
                print("  ✗ Japanese card title not found")

            # Step 6: Change to Korean
            print("\n[Step 6] Change to Korean")
            change_language(page, "ko")
            time.sleep(2)
            screenshots.append(take_screenshot(page, "i18n_06_analysis_ko"))

            # Verify Korean labels
            user_seg_card_ko = page.locator('.card-title:has-text("사용자 세그먼테이션")')
            if user_seg_card_ko.count() > 0:
                print("  ✓ Korean card title found")
            else:
                print("  ✗ Korean card title not found")

            # Summary
            print("\n" + "=" * 50)
            print("User Segmentation I18n Test Summary")
            print("=" * 50)
            print(f"Screenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error_i18n"))
            raise
        finally:
            browser.close()

    return screenshots


def test_user_segmentation_responsive(
    ui_screenshot_dir,
):  # allow-no-assert: smoke test - visual verification only
    """Test responsive layout for user segmentation on small screens."""
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=MOBILE_VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate and login on mobile viewport
            print("\n[Step 1] Navigate and login (mobile viewport)")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "responsive_01_login"))

            login(page)
            screenshots.append(take_screenshot(page, "responsive_02_after_login"))

            # Step 2: Navigate to Analysis
            navigate_to_analysis(page)
            screenshots.append(take_screenshot(page, "responsive_03_analysis"))

            # Step 3: Check User Segmentation card layout
            print("\n[Step 3] Check User Segmentation card layout on mobile")
            user_seg_card = page.locator(
                '.card:has-text("User Segmentation"), .card:has-text("用户分层")'
            )
            if user_seg_card.count() > 0:
                print("  ✓ User Segmentation card found")

                # Check chart height
                chart_container = user_seg_card.locator(".chart-container")
                if chart_container.count() > 0:
                    print("  ✓ Chart container found")
                    screenshots.append(take_screenshot(page, "responsive_04_chart"))
                else:
                    print("  ⚠ Chart container not found")
            else:
                print("  ✗ User Segmentation card not found")

            # Summary
            print("\n" + "=" * 50)
            print("User Segmentation Responsive Test Summary")
            print("=" * 50)
            print(f"Screenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error_responsive"))
            raise
        finally:
            browser.close()

    return screenshots


def test_user_segmentation_role_view(
    ui_screenshot_dir,
):  # allow-no-assert: smoke test - visual verification only
    """Test role-based view toggle for user segmentation.

    Issue #3079: Verify that users can toggle between usage-based and role-based views.
    """
    global SCREENSHOT_DIR
    SCREENSHOT_DIR = ui_screenshot_dir
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Navigate and login
            print("\n[Step 1] Navigate to login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            screenshots.append(take_screenshot(page, "role_view_01_login"))

            login(page)
            screenshots.append(take_screenshot(page, "role_view_02_after_login"))

            # Step 2: Navigate to Analysis
            navigate_to_analysis(page)
            screenshots.append(take_screenshot(page, "role_view_03_analysis"))

            # Step 3: Find User Segmentation card
            print("\n[Step 3] Find User Segmentation card with view toggle")
            user_seg_card = page.locator(
                '.card:has-text("User Segmentation"), .card:has-text("用户分层")'
            )
            if user_seg_card.count() > 0:
                print("  ✓ User Segmentation card found")

                # Step 4: Check for view toggle buttons
                print("\n[Step 4] Check for view toggle buttons")
                # Look for "By Role" / "按角色" button
                role_btn = user_seg_card.locator(
                    'button:has-text("By Role"), button:has-text("按角色")'
                )
                if role_btn.count() > 0:
                    print("  ✓ Role view button found")

                    # Click the role view button
                    role_btn.first.click()
                    time.sleep(1)
                    screenshots.append(take_screenshot(page, "role_view_04_role_view"))

                    # Verify role chart is displayed (check for Admin/管理员 label)
                    admin_label = page.locator('text="Admin", text="管理员"')
                    if admin_label.count() > 0:
                        print("  ✓ Role distribution chart displayed")
                    else:
                        print("  ⚠ Role labels not found (may be no data)")

                    # Step 5: Switch back to usage view
                    print("\n[Step 5] Switch back to usage view")
                    usage_btn = user_seg_card.locator(
                        'button:has-text("By Usage"), button:has-text("按使用量")'
                    )
                    if usage_btn.count() > 0:
                        usage_btn.first.click()
                        time.sleep(1)
                        screenshots.append(take_screenshot(page, "role_view_05_usage_view"))
                        print("  ✓ Switched back to usage view")
                else:
                    print("  ⚠ View toggle buttons not found")
            else:
                print("  ✗ User Segmentation card not found")

            # Summary
            print("\n" + "=" * 50)
            print("User Segmentation Role View Test Summary")
            print("=" * 50)
            print(f"Screenshots saved: {len(screenshots)}")
            for s in screenshots:
                print(f"  - {s}")

        except Exception as e:  # allow-swallow: UI element may not exist
            print(f"\n✗ Error: {e}")
            screenshots.append(take_screenshot(page, "error_role_view"))
            raise
        finally:
            browser.close()

    return screenshots


def run_all_tests():
    """Run all user segmentation tests."""
    print("\n" + "=" * 60)
    print("Running User Segmentation Pie Chart Tests")
    print("=" * 60)

    all_screenshots = []

    # Test 1: Tooltip enhancement
    print("\n>>> Test 1: Tooltip Enhancement")
    try:
        screenshots = test_user_segmentation_tooltip()
        all_screenshots.extend(screenshots)
        print("✓ Tooltip test completed")
    except Exception as e:  # allow-swallow: UI element may not exist
        print(f"✗ Tooltip test failed: {e}")

    # Test 2: Internationalization
    print("\n>>> Test 2: Internationalization")
    try:
        screenshots = test_user_segmentation_i18n()
        all_screenshots.extend(screenshots)
        print("✓ I18n test completed")
    except Exception as e:  # allow-swallow: UI element may not exist
        print(f"✗ I18n test failed: {e}")

    # Test 3: Responsive layout
    print("\n>>> Test 3: Responsive Layout")
    try:
        screenshots = test_user_segmentation_responsive()
        all_screenshots.extend(screenshots)
        print("✓ Responsive test completed")
    except Exception as e:  # allow-swallow: UI element may not exist
        print(f"✗ Responsive test failed: {e}")

    # Final summary
    print("\n" + "=" * 60)
    print("All Tests Completed")
    print("=" * 60)
    print(f"Total screenshots saved: {len(all_screenshots)}")
    print(f"Screenshots directory: {SCREENSHOT_DIR}")

    return all_screenshots


if __name__ == "__main__":
    run_all_tests()
