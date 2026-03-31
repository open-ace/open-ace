"""
测试 Manage 模式下的语言切换下拉列表
验证下拉列表文字是否可见
"""

from playwright.sync_api import sync_playwright, expect
import time
import os

BASE_URL = "http://localhost:5000/"
USERNAME = "admin"
PASSWORD = "admin123"
SCREENSHOT_DIR = "screenshots/issues/80"


def take_screenshot(page, name):
    """截图并保存"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = f"{SCREENSHOT_DIR}/{name}"
    page.screenshot(path=path)
    print(f"   截图已保存到：{path}")
    return path


def test_manage_language_dropdown():
    """测试 Manage 模式下的语言切换下拉列表"""
    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()

        print("=" * 60)
        print("测试 Manage 模式下的语言切换下拉列表")
        print("=" * 60)

        # Step 1: 访问登录页
        print("\n1. 访问登录页...")
        page.goto(BASE_URL + "login")
        page.wait_for_load_state("networkidle")
        time.sleep(2)

        # Step 2: 登录
        print("2. 登录系统...")
        page.wait_for_selector("#username", timeout=5000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')

        try:
            page.wait_for_url("**/work/**", timeout=5000)
        except:
            try:
                page.wait_for_selector("header", timeout=5000)
                print("   检测到 header，可能已登录")
            except:
                pass

        print("   当前 URL:", page.url)
        time.sleep(2)

        # Step 3: 切换到 Manage 模式
        print("3. 切换到 Manage 模式...")
        # 查找并点击 Manage 模式切换按钮
        try:
            manage_btn = page.locator("button.mode-btn").filter(has_text="Manage").first
            if manage_btn.count() > 0:
                manage_btn.click()
                page.wait_for_url("**/manage/**", timeout=5000)
                print("   已切换到 Manage 模式")
            else:
                print("   未找到 Manage 按钮，直接导航到 Manage 页面")
                page.goto(BASE_URL + "manage/dashboard")
                page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"   切换失败: {e}")
            # 直接导航到 Manage 页面
            page.goto(BASE_URL + "manage/dashboard")
            page.wait_for_load_state("networkidle")

        time.sleep(2)
        take_screenshot(page, "01_manage_mode.png")

        # Step 4: 查找语言切换按钮
        print("4. 查找语言切换按钮...")
        lang_button = None
        selectors = [
            "i.bi-globe",
            'button[data-bs-toggle="dropdown"] i',
            "header i.bi-globe",
            ".dropdown i.bi-globe",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    lang_button = locator
                    print(f"   找到语言按钮 (selector: {selector})")
                    break
            except:
                pass

        if not lang_button:
            print("   未找到语言按钮")
            browser.close()
            return

        # Step 5: 点击语言切换按钮
        print("5. 点击语言切换按钮...")
        lang_button.click()
        time.sleep(1)

        # Step 6: 检查下拉菜单
        print("6. 检查下拉菜单...")
        dropdown_menu = page.locator("ul.dropdown-menu").first
        try:
            expect(dropdown_menu).to_be_visible(timeout=5000)
            print("   下拉菜单可见 ✓")
        except Exception as e:
            print(f"   下拉菜单未显示：{e}")
            take_screenshot(page, "02_dropdown_not_visible.png")
            browser.close()
            return

        take_screenshot(page, "03_dropdown_opened.png")

        # Step 7: 检查下拉菜单项
        print("7. 检查下拉菜单项...")
        dropdown_items = dropdown_menu.locator("button.dropdown-item")
        count = dropdown_items.count()
        print(f"   找到 {count} 个语言选项")

        # Step 8: 检查每个语言项的文字颜色和背景颜色
        print("8. 检查语言项文字可见性...")

        all_pass = True
        for i in range(count):
            item = dropdown_items.nth(i)
            text = item.text_content().strip()

            # 获取文字颜色
            color = item.evaluate("el => getComputedStyle(el).color")
            # 获取背景颜色
            bg_color = item.evaluate("el => getComputedStyle(el).backgroundColor")
            # 获取文本颜色值
            text_color = item.evaluate("el => getComputedStyle(el).color")

            print(f"   语言项 {i+1}: '{text}'")
            print(f"      文字颜色：{color}")
            print(f"      背景颜色：{bg_color}")

            # 检查文字是否可见
            # 白色文字在蓝色背景上应该可见
            if "rgb(255, 255, 255)" in color and (
                "rgb(14, 165, 233)" in bg_color or "rgb(2, 132, 199)" in bg_color
            ):
                print(f"      ✓ 白色文字在蓝色背景上可见")
            # 深色文字在透明/浅色背景上应该可见
            elif "rgb(15, 23, 42)" in color or "rgb(0, 0, 0)" in color:
                print(f"      ✓ 深色文字可见")
            # 白色文字在白色背景上不可见
            elif "rgb(255, 255, 255)" in color and (
                "rgb(255, 255, 255)" in bg_color or "rgba(0, 0, 0, 0)" in bg_color
            ):
                print(f"      ✗ 失败：白色文字在白色/透明背景上不可见")
                all_pass = False
            else:
                print(f"      ? 无法确定可见性")

        # Step 9: 截图
        print("\n9. 截图...")
        take_screenshot(page, "04_language_dropdown.png")

        # Step 10: 验证结果
        print("\n10. 验证结果...")
        if all_pass:
            print("   ✓ 所有语言项文字可见")
        else:
            print("   ✗ 部分语言项文字可能不可见")

        browser.close()

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)

        return all_pass


if __name__ == "__main__":
    result = test_manage_language_dropdown()
    exit(0 if result else 1)
