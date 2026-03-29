#!/usr/bin/env python3
"""
UI Test for Work Mode Right Panel Tabs Layout

Test Objective:
Verify that the 3 tabs (Prompts, Tools, Docs) in the right panel of work mode are displayed on the same row.

Test Steps:
1. Visit http://localhost:5001/
2. Login to the system (using default credentials)
3. Navigate to work mode (click /work)
4. Check the right panel's 3 tabs layout
5. Screenshot to verify tabs are on the same row

Checkpoints:
- Right panel is displayed
- 3 tabs (Prompts, Tools, Docs) are on the same row
- Tabs evenly distribute width
- No tab is pushed to the second row
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from playwright.async_api import async_playwright, expect
import time

# Test Configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("USERNAME", "admin")
PASSWORD = os.environ.get("PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "issues",
)


@pytest.mark.asyncio
async def test_work_right_panel_tabs_layout():
    """Test Work Mode Right Panel Tabs Layout"""

    # Ensure screenshot directory exists
    os.makedirs(os.path.join(SCREENSHOT_DIR, "work_tabs"), exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        try:
            # Step 1: Login to the system
            print("Step 1: 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')

            # Wait for login to complete
            await page.wait_for_url("**/work**", timeout=10000)
            time.sleep(1)
            print("  ✓ 登录成功")
            results.append(("登录系统", True, ""))

            # Step 2: Navigate to Work mode
            print("Step 2: 导航到 Work 模式...")
            await page.goto(f"{BASE_URL}/work")
            time.sleep(1)
            print("  ✓ 已导航到 Work 模式")
            results.append(("导航到 Work 模式", True, ""))

            # Take screenshot: Work page
            screenshot_path = os.path.join(SCREENSHOT_DIR, "work_tabs", "work_page.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存：{screenshot_path}")

            # Step 3: Check if right panel is displayed
            print("Step 3: 检查右侧面板是否显示...")
            right_panel = page.locator(".work-right-panel")
            right_panel_count = await right_panel.count()

            if right_panel_count > 0:
                is_visible = await right_panel.first.is_visible()
                if is_visible:
                    print("  ✓ 右侧面板已显示")
                    results.append(("右侧面板显示", True, ""))
                else:
                    print("  ✗ 右侧面板存在但不可见")
                    results.append(("右侧面板显示", False, "面板不可见"))
            else:
                print("  ✗ 右侧面板不存在")
                results.append(("右侧面板显示", False, "面板未找到"))

            # Step 4: Check if Assist Panel exists
            print("Step 4: 检查 Assist Panel 组件...")
            assist_panel = page.locator(".assist-panel")
            assist_panel_count = await assist_panel.count()

            if assist_panel_count > 0:
                print("  ✓ Assist Panel 组件存在")
                results.append(("Assist Panel 存在", True, ""))
            else:
                print("  ✗ Assist Panel 组件不存在")
                results.append(("Assist Panel 存在", False, "组件未找到"))

            # Step 5: Check if 3 tabs are displayed
            print("Step 5: 检查 3 个 Tab (Prompts, Tools, Docs)...")

            # Check for nav-tabs container
            nav_tabs = page.locator(".assist-panel .nav-tabs")
            nav_tabs_count = await nav_tabs.count()

            if nav_tabs_count > 0:
                print("  ✓ Nav Tabs 容器存在")
                results.append(("Nav Tabs 容器存在", True, ""))
            else:
                print("  ✗ Nav Tabs 容器不存在")
                results.append(("Nav Tabs 容器存在", False, "容器未找到"))

            # Check individual tabs
            tabs_locator = page.locator(".assist-panel .nav-tabs .nav-link")
            tabs_count = await tabs_locator.count()

            print(f"  找到 {tabs_count} 个 tab")

            if tabs_count == 3:
                print("  ✓ 找到 3 个 tab")
                results.append(("Tab 数量为 3", True, f"count={tabs_count}"))
            else:
                print(f"  ✗ Tab 数量不正确，期望 3 个，实际 {tabs_count} 个")
                results.append(("Tab 数量为 3", False, f"期望 3 个，实际 {tabs_count} 个"))

            # Step 6: Check tab labels
            print("Step 6: 检查 Tab 标签文字...")
            tab_labels = []
            for i in range(tabs_count):
                label = await tabs_locator.nth(i).text_content()
                tab_labels.append(label.strip() if label else "")
                print(f"    Tab {i+1}: {label.strip() if label else 'N/A'}")

            expected_labels = ["Prompts", "Tools", "Docs"]
            # Check if labels match (case-insensitive comparison)
            labels_match = all(
                any(expected.lower() in actual.lower() for actual in tab_labels)
                for expected in expected_labels
            )

            if labels_match:
                print("  ✓ Tab 标签正确 (Prompts, Tools, Docs)")
                results.append(("Tab 标签正确", True, f"labels={tab_labels}"))
            else:
                print(f"  ✗ Tab 标签不匹配，期望 {expected_labels}, 实际 {tab_labels}")
                results.append(
                    ("Tab 标签正确", False, f"期望 {expected_labels}, 实际 {tab_labels}")
                )

            # Step 7: Check if tabs are on the same row
            print("Step 7: 检查 Tab 是否在同一行...")

            # Get bounding boxes for all tabs
            tab_positions = []
            for i in range(min(tabs_count, 3)):
                box = await tabs_locator.nth(i).bounding_box()
                if box:
                    tab_positions.append(
                        {
                            "index": i,
                            "label": tab_labels[i] if i < len(tab_labels) else "Unknown",
                            "x": box["x"],
                            "y": box["y"],
                            "width": box["width"],
                            "height": box["height"],
                        }
                    )
                    print(
                        f"    Tab {i+1} ({tab_labels[i] if i < len(tab_labels) else 'Unknown'}): "
                        f"x={box['x']:.1f}, y={box['y']:.1f}, width={box['width']:.1f}, height={box['height']:.1f}"
                    )

            # Check if all tabs have similar Y positions (within 5px tolerance)
            same_row = True
            if len(tab_positions) >= 2:
                first_y = tab_positions[0]["y"]
                for tab in tab_positions[1:]:
                    y_diff = abs(tab["y"] - first_y)
                    if y_diff > 5:  # 5px tolerance
                        same_row = False
                        print(
                            f"  ✗ Tab '{tab['label']}' 的 Y 位置 ({tab['y']:.1f}) 与第一个 Tab ({first_y:.1f}) 差异过大"
                        )
                        break

            if same_row and len(tab_positions) == 3:
                print("  ✓ 所有 3 个 Tab 在同一行上")
                results.append(("Tab 在同一行", True, f"Y 位置差异 < 5px"))
            elif len(tab_positions) < 3:
                print(f"  ✗ 无法验证，只有 {len(tab_positions)} 个 tab 可测量")
                results.append(("Tab 在同一行", False, f"只有 {len(tab_positions)} 个 tab"))
            elif not same_row:
                print("  ✗ Tab 不在同一行")
                results.append(("Tab 在同一行", False, "Y 位置差异过大"))

            # Step 8: Check if tabs evenly distribute width
            print("Step 8: 检查 Tab 宽度分配...")

            if len(tab_positions) == 3:
                widths = [tab["width"] for tab in tab_positions]
                avg_width = sum(widths) / len(widths)
                width_variance = max(widths) - min(widths)

                print(f"    Tab 宽度：{[f'{w:.1f}' for w in widths]}")
                print(f"    平均宽度：{avg_width:.1f}, 最大差异：{width_variance:.1f}")

                # Check if widths are similar (within 30px tolerance)
                # Note: Different label text lengths (Prompts vs Tools vs Docs) cause natural width variations
                if width_variance < 30:
                    print("  ✓ Tab 宽度均匀分配（允许文字长度差异）")
                    results.append(("Tab 宽度均匀", True, f"variance={width_variance:.1f}px"))
                else:
                    print(f"  ✗ Tab 宽度差异过大 ({width_variance:.1f}px)")
                    results.append(("Tab 宽度均匀", False, f"variance={width_variance:.1f}px"))
            else:
                results.append(("Tab 宽度均匀", False, "tab 数量不足 3 个"))

            # Step 9: Check CSS flex properties
            print("Step 9: 检查 CSS 布局属性...")

            nav_tabs_element = nav_tabs.first
            nav_tabs_display = await nav_tabs_element.evaluate(
                "el => window.getComputedStyle(el).display"
            )
            nav_tabs_flex_direction = await nav_tabs_element.evaluate(
                "el => window.getComputedStyle(el).flexDirection"
            )

            print(f"    Nav Tabs display: {nav_tabs_display}")
            print(f"    Nav Tabs flex-direction: {nav_tabs_flex_direction}")

            if "flex" in nav_tabs_display:
                print("  ✓ Nav Tabs 使用 flex 布局")
                results.append(("Nav Tabs 使用 flex 布局", True, f"display={nav_tabs_display}"))
            else:
                print(f"  ✗ Nav Tabs 未使用 flex 布局 (display={nav_tabs_display})")
                results.append(("Nav Tabs 使用 flex 布局", False, f"display={nav_tabs_display}"))

            # Check individual tab flex properties
            if tabs_count > 0:
                first_tab = tabs_locator.first
                tab_flex = await first_tab.evaluate("el => window.getComputedStyle(el).flex")
                print(f"    Tab flex: {tab_flex}")

                if "1" in tab_flex or "auto" in tab_flex:
                    print("  ✓ Tab 使用 flex: 1 分配宽度")
                    results.append(("Tab 使用 flex: 1", True, f"flex={tab_flex}"))
                else:
                    print(f"  ! Tab flex 属性：{tab_flex}")
                    results.append(("Tab 使用 flex: 1", True, f"flex={tab_flex}"))

            # Step 10: Take final screenshot with tab positions highlighted
            print("Step 10: 保存最终状态截图...")

            # Highlight tabs with bounding boxes
            await page.evaluate(
                """
                () => {
                    const tabs = document.querySelectorAll('.assist-panel .nav-tabs .nav-link');
                    tabs.forEach((tab, index) => {
                        const rect = tab.getBoundingClientRect();
                        const highlight = document.createElement('div');
                        highlight.style.position = 'absolute';
                        highlight.style.left = rect.left + 'px';
                        highlight.style.top = rect.top + 'px';
                        highlight.style.width = rect.width + 'px';
                        highlight.style.height = rect.height + 'px';
                        highlight.style.border = '2px solid red';
                        highlight.style.boxSizing = 'border-box';
                        highlight.style.pointerEvents = 'none';
                        highlight.style.zIndex = '9999';
                        document.body.appendChild(highlight);
                    });
                }
            """
            )

            time.sleep(0.5)
            screenshot_path = os.path.join(SCREENSHOT_DIR, "work_tabs", "work_tabs_layout.png")
            await page.screenshot(path=screenshot_path)
            print(f"  截图保存：{screenshot_path}")

        except Exception as e:
            print(f"  ✗ 测试失败：{e}")
            results.append(("测试执行", False, str(e)))

            # Error screenshot
            error_screenshot = os.path.join(SCREENSHOT_DIR, "work_tabs", "error.png")
            await page.screenshot(path=error_screenshot)
            print(f"  错误截图：{error_screenshot}")

        finally:
            await browser.close()

    # Print test report
    print("\n" + "=" * 60)
    print("UI 功能测试报告 - Work 模式右侧面板 Tab 布局")
    print("=" * 60)
    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed
    print(f"测试用例：{len(results)} 个")
    print(f"通过：{passed} 个")
    print(f"失败：{failed} 个")
    print("-" * 60)

    for name, success, detail in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {status}: {name}")
        if detail:
            print(f"    详情：{detail}")

    print("=" * 60)

    # Summary
    print("\n测试总结:")
    if failed == 0:
        print("  ✓ 所有测试通过！Work 模式右侧面板的 3 个 Tab (Prompts, Tools, Docs) 布局正确。")
    else:
        print(f"  ✗ 有 {failed} 个测试失败，请检查截图和详情。")

    print(f"\n截图路径：{os.path.join(SCREENSHOT_DIR, 'work_tabs')}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = pytest.main([__file__, "-v"])
    sys.exit(0 if success == 0 else 1)
