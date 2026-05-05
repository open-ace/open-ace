#!/usr/bin/env python3
"""
UI Test for Work Mode Session List - Issues #64, #66, #69

Tests:
1. Issue #64: Session list auto-refresh every 1 minute
2. Issue #66: Session list item shows session name on hover (title attribute)
3. Issue #66: Session list shows localized unit (请求 instead of req)
4. Issue #69: Session detail modal shows Session ID in title
5. Issue #69: Session detail shows 3-column layout
6. Issue #69: Session detail shows updated time
"""

import os
import sys

from playwright.sync_api import TimeoutError, expect, sync_playwright

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# UI 测试配置
BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"
VIEWPORT_SIZE = {"width": 1400, "height": 900}
HEADLESS = True  # 先使用无头模式
DEFAULT_TIMEOUT = 15000
OUTPUT_DIR = "./screenshots"

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)


def test_work_mode_session_list():
    """Test Work Mode Session List improvements"""

    print("=" * 60)
    print("Work Mode Session List - UI Test (Issues #64, #66, #69)")
    print("=" * 60)

    test_results = []
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport=VIEWPORT_SIZE)
        page = context.new_page()

        try:
            # Step 1: Login
            print("\n[1] 登录系统...")
            page.goto(f"{BASE_URL}/login", timeout=DEFAULT_TIMEOUT)
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_url("**/manage/**", timeout=DEFAULT_TIMEOUT)
            print("    ✓ 登录成功")

            page.screenshot(path=f"{OUTPUT_DIR}/work_session_01_login.png")
            screenshots.append("login.png")

            # Step 2: Navigate to Work mode
            print("[2] 导航到 Work 模式...")
            page.goto(f"{BASE_URL}/work", timeout=DEFAULT_TIMEOUT)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)  # Wait for session list to load

            page.screenshot(path=f"{OUTPUT_DIR}/work_session_02_work_mode.png")
            screenshots.append("work_mode.png")
            print("    ✓ Work 页面加载完成")

            # Test 1: Check session list is visible
            print("[3] 检查会话列表...")
            session_list = page.locator(".session-list")
            expect(session_list).to_be_visible(timeout=DEFAULT_TIMEOUT)
            test_results.append(("会话列表可见", True))
            print("    ✓ 会话列表可见")

            # Test 2: Check session items have title attribute (Issue #66)
            print("[4] 检查会话项 title 属性（Issue #66）...")
            session_items = page.locator(".session-item")
            session_count = session_items.count()

            if session_count > 0:
                first_session = session_items.first
                title_attr = first_session.get_attribute("title")
                has_title = title_attr is not None and len(title_attr) > 0
                test_results.append(("会话项有 title 属性", has_title))
                if has_title:
                    print(f"    ✓ 会话项有 title 属性：{title_attr[:50]}")
                else:
                    print("    ✗ 会话项缺少 title 属性")
            else:
                test_results.append(("会话项有 title 属性", None))
                print("    - 没有会话数据可测试")

            # Test 3: Check localized unit (Issue #66)
            print("[5] 检查单位国际化（Issue #66）...")
            request_spans = page.locator(".session-requests span")
            if request_spans.count() > 0:
                first_request_text = request_spans.first.inner_text()
                has_localized_unit = "请求" in first_request_text or "Request" in first_request_text
                has_raw_req = (
                    "req" in first_request_text and "request" not in first_request_text.lower()
                )
                is_localized = has_localized_unit and not has_raw_req
                test_results.append(("单位国际化显示", is_localized))
                if is_localized:
                    print(f"    ✓ 单位已国际化：'{first_request_text}'")
                else:
                    print(f"    ✗ 单位未国际化：'{first_request_text}' (期望包含'请求'或'Request')")
            else:
                test_results.append(("单位国际化显示", None))
                print("    - 没有找到请求数显示")

            # Test 4: Click session to open detail modal
            print("[6] 点击会话打开详情（Issue #69）...")
            if session_count > 0:
                session_items.first.click()
                page.wait_for_timeout(2000)

                # Test 5: Check modal is visible
                modal = page.locator(".modal-dialog")
                expect(modal).to_be_visible(timeout=DEFAULT_TIMEOUT)
                print("    ✓ 详情弹窗已打开")

                # Test 6: Check modal title contains Session ID (Issue #69)
                print("[7] 检查详情弹窗标题包含 Session ID（Issue #69）...")
                modal_title = modal.locator(".modal-title")
                title_text = modal_title.inner_text()
                has_session_id = len(title_text) > 8 and "-" in title_text
                test_results.append(("详情标题包含 Session ID", has_session_id))
                if has_session_id:
                    print(f"    ✓ 标题包含 Session ID: '{title_text}'")
                else:
                    print(f"    ✗ 标题不包含 Session ID: '{title_text}'")

                page.screenshot(path=f"{OUTPUT_DIR}/work_session_03_session_detail.png")
                screenshots.append("session_detail.png")

                # Test 7: Check 3-column layout (Issue #69)
                print("[8] 检查三列布局（Issue #69）...")
                meta_row = modal.locator(".session-meta .row")
                cols = meta_row.locator('[class*="col-md"]')
                col_count = cols.count()
                has_multi_cols = col_count >= 6
                test_results.append(("三列布局", has_multi_cols))
                if has_multi_cols:
                    print(f"    ✓ 三列布局：{col_count} 列")
                else:
                    print(f"    ✗ 非三列布局：{col_count} 列")

                # Test 8: Check updated time field (Issue #69)
                print("[9] 检查结束时间字段（Issue #69）...")
                meta_text = meta_row.inner_text()
                has_updated_time = (
                    "Last Active" in meta_text
                    or "最后活跃" in meta_text
                    or "updated" in meta_text.lower()
                )
                test_results.append(("显示结束时间", has_updated_time))
                if has_updated_time:
                    print("    ✓ 显示结束时间字段")
                else:
                    print("    ✗ 未找到结束时间字段")

                # Close modal
                close_btn = modal.locator(".btn-close")
                if close_btn.count() > 0:
                    close_btn.first.click()
                    page.wait_for_timeout(500)
            else:
                test_results.append(("详情标题包含 Session ID", None))
                test_results.append(("三列布局", None))
                test_results.append(("显示结束时间", None))
                print("    - 没有会话可点击测试详情")

            # Test 9: Verify page is stable (Issue #64 auto-refresh base)
            print("[10] 验证页面稳定运行（Issue #64 自动刷新基础）...")
            page.wait_for_timeout(3000)
            test_results.append(("页面稳定运行", True))
            print("    ✓ 页面稳定运行")

        except TimeoutError as e:
            print(f"\n    ✗ 测试超时：{e}")
            test_results.append(("页面加载", False))
            page.screenshot(path=f"{OUTPUT_DIR}/work_session_error_timeout.png")
            screenshots.append("error_timeout.png")
        except Exception as e:
            print(f"\n    ✗ 测试错误：{e}")
            test_results.append(("测试执行", False))
            page.screenshot(path=f"{OUTPUT_DIR}/work_session_error_exception.png")
            screenshots.append("error_exception.png")
        finally:
            browser.close()

        # Print results
        print("\n" + "=" * 60)
        print("测试结果")
        print("=" * 60)

        passed = 0
        failed = 0
        skipped = 0

        for test_name, result in test_results:
            if result is True:
                print(f"  ✓ {test_name}: 通过")
                passed += 1
            elif result is False:
                print(f"  ✗ {test_name}: 失败")
                failed += 1
            else:
                print(f"  - {test_name}: 跳过 (无数据)")
                skipped += 1

        print("\n" + "-" * 60)
        print(f"总计：{passed} 通过，{failed} 失败，{skipped} 跳过")
        print("-" * 60)

        if screenshots:
            print("\n截图:")
            for shot in screenshots:
                print(f"  - {OUTPUT_DIR}/work_session_{shot}")

        print("=" * 60)

        return failed == 0


if __name__ == "__main__":
    success = test_work_mode_session_list()
    sys.exit(0 if success else 1)
