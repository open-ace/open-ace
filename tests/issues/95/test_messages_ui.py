"""
测试 Messages 页面的 UI 修复
1. Role checkbox 文字对齐
2. Search、Date 框和 Host 下拉列表高度一致
"""

from playwright.sync_api import sync_playwright
import time

BASE_URL = "http://localhost:5000/"
USERNAME = "admin"
PASSWORD = "admin123"


def test_messages_ui():
    """测试 Messages 页面的 UI 修复"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()

        print("=" * 60)
        print("测试 Messages 页面的 UI 修复")
        print("=" * 60)

        # Step 1: 访问登录页
        print("\n1. 访问登录页...")
        page.goto(BASE_URL + "login")
        page.wait_for_load_state("networkidle")
        time.sleep(2)

        # Step 2: 登录
        print("2. 登录系统...")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')
        time.sleep(3)

        # Step 3: 导航到 Messages 页面
        print("3. 导航到 Messages 页面...")

        # 先确保在 Work 模式
        try:
            work_btn = page.locator('button:has-text("Work")').first
            if work_btn.count() > 0:
                work_btn.click()
                time.sleep(1)
        except:
            pass

        # 点击 Messages 菜单
        try:
            messages_link = page.locator('a:has-text("Messages")').first
            if messages_link.count() > 0:
                messages_link.click()
                page.wait_for_load_state("networkidle")
                time.sleep(2)
                print("   ✓ 已导航到 Messages 页面")
            else:
                print("   ✗ 未找到 Messages 菜单")
                # 尝试直接导航
                page.goto(BASE_URL + "work/messages")
                page.wait_for_load_state("networkidle")
                time.sleep(2)
        except Exception as e:
            print(f"   导航失败：{e}")
            page.goto(BASE_URL + "work/messages")
            page.wait_for_load_state("networkidle")
            time.sleep(2)

        # 检查页面是否加载
        page_content = page.content()
        if "Role" in page_content:
            print("   ✓ 页面包含 Role 过滤器")
        else:
            print("   ✗ 页面未正确加载")

        time.sleep(2)

        # Step 4: 检查 Role checkbox 的文字对齐
        print("4. 检查 Role checkbox 的文字对齐...")

        try:
            # 使用 text content 来定位
            role_section = page.locator("text=Role").first
            if role_section.count() > 0:
                print("   ✓ 找到 Role 标签")

                # 检查 checkbox 是否存在
                checkbox = page.locator("#roleUser").first
                if checkbox.count() > 0:
                    print("   ✓ 找到 User checkbox")

                    # 检查 label 是否存在
                    label = page.locator('label[for="roleUser"]').first
                    if label.count() > 0:
                        print("   ✓ 找到 User label")

                        # 获取位置
                        label_box = label.bounding_box()
                        checkbox_box = checkbox.bounding_box()

                        if label_box and checkbox_box:
                            label_top = label_box["y"]
                            checkbox_top = checkbox_box["y"]
                            diff = abs(label_top - checkbox_top)
                            print(f"   Label top: {label_top:.2f}px")
                            print(f"   Checkbox top: {checkbox_top:.2f}px")
                            print(f"   差异：{diff:.2f}px")

                            if diff < 3:
                                print("   ✓ Role checkbox 文字对齐正确")
                            else:
                                print("   ✗ Role checkbox 文字对齐有问题")
                    else:
                        print("   ✗ 未找到 User label")
                else:
                    print("   ✗ 未找到 User checkbox")
            else:
                print("   ✗ 未找到 Role 标签，可能在其他位置")
        except Exception as e:
            print(f"   无法获取元素位置：{e}")

        # Step 5: 检查表单控件高度一致性
        print("5. 检查表单控件高度一致性...")

        try:
            # 使用更精确的 selector
            date_input = page.locator('input[type="date"]').first
            host_select = page.locator("select").filter(has_text="All Hosts").first
            search_input = page.locator('input[type="text"][placeholder*="Search"]').first

            if date_input.count() > 0:
                date_box = date_input.bounding_box()
                print(f"   Date 框高度：{date_box['height']:.2f}px")

            if host_select.count() > 0:
                host_box = host_select.bounding_box()
                print(f"   Host 下拉列表高度：{host_box['height']:.2f}px")

            if search_input.count() > 0:
                search_box = search_input.bounding_box()
                print(f"   Search 框高度：{search_box['height']:.2f}px")

            # 检查高度是否一致（允许 2px 误差）
            if date_input.count() > 0 and host_select.count() > 0 and search_input.count() > 0:
                date_height = date_input.bounding_box()["height"]
                host_height = host_select.bounding_box()["height"]
                search_height = search_input.bounding_box()["height"]

                max_diff = max(
                    abs(date_height - host_height),
                    abs(date_height - search_height),
                    abs(host_height - search_height),
                )

                if max_diff < 2:
                    print(f"   ✓ 表单控件高度一致（最大差异：{max_diff:.2f}px）")
                else:
                    print(f"   ✗ 表单控件高度不一致（最大差异：{max_diff:.2f}px）")
        except Exception as e:
            print(f"   无法获取元素高度：{e}")

        # Step 6: 截图
        print("6. 截图...")
        page.screenshot(path="screenshots/messages_ui_fix.png")
        print("   截图已保存到：screenshots/messages_ui_fix.png")

        browser.close()

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)


if __name__ == "__main__":
    test_messages_ui()
