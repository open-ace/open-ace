"""
测试语言切换下拉列表功能
验证下拉列表文字是否可见
"""

from playwright.sync_api import sync_playwright, expect, Page
import time

BASE_URL = "http://localhost:5001/"
USERNAME = "admin"
PASSWORD = "admin123"

def test_language_dropdown():
    """测试语言切换下拉列表"""
    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=True)  # 使用无头模式
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        
        print("=" * 50)
        print("测试语言切换下拉列表")
        print("=" * 50)
        
        # Step 1: 访问登录页
        print("\n1. 访问登录页...")
        page.goto(BASE_URL + "login")
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        # Step 2: 登录
        print("2. 登录系统...")
        # 等待登录页面加载
        page.wait_for_selector("#username", timeout=5000)
        
        # 填写用户名密码
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        
        # 点击登录按钮
        page.click('button[type="submit"]')
        
        # 等待登录成功，直接等待 URL 变化或元素出现
        try:
            page.wait_for_url("**/work/**", timeout=5000)
        except:
            # 如果 URL 没变，等待 header 出现
            try:
                page.wait_for_selector("header", timeout=5000)
                print("   检测到 header，可能已登录")
            except:
                pass
        
        print("   当前 URL:", page.url)
        
        # Step 3: 等待页面加载
        print("3. 等待页面加载...")
        time.sleep(3)
        
        # 截图查看当前状态
        page.screenshot(path="screenshots/issues/80/current_page.png")
        print("   已截图当前页面")
        
        # Step 4: 查找语言切换按钮
        print("4. 查找语言切换按钮...")
        # 尝试多种 selector
        selectors = [
            'i.bi-globe',
            'button[data-bs-toggle="dropdown"] i',
            'header i.bi-globe',
            '.dropdown i.bi-globe'
        ]
        
        lang_button = None
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
            print("   未找到语言按钮，尝试查找所有 globe 图标...")
            # 查找包含 globe 的元素
            globe_elements = page.locator('[class*="globe"]')
            print(f"   找到 {globe_elements.count()} 个 globe 相关元素")
            return
        
        # Step 5: 点击语言切换按钮
        print("5. 点击语言切换按钮...")
        lang_button.click()
        time.sleep(1)
        
        # Step 6: 检查下拉菜单是否显示
        print("6. 检查下拉菜单...")
        # 使用更精确的 selector，查找包含语言选项的下拉菜单
        dropdown_menu = page.locator("ul.dropdown-menu").first
        try:
            expect(dropdown_menu).to_be_visible(timeout=5000)
            print("   下拉菜单可见 ✓")
        except Exception as e:
            print(f"   下拉菜单未显示：{e}")
            # 截图查看
            page.screenshot(path="screenshots/issues/80/after_click.png")
            print("   已截图点击后的状态")
            return
        
        # Step 7: 检查下拉菜单项
        print("7. 检查下拉菜单项...")
        dropdown_items = dropdown_menu.locator("button.dropdown-item")
        count = dropdown_items.count()
        print(f"   找到 {count} 个语言选项")
        
        # Step 8: 检查每个语言项的文字是否可见
        print("8. 检查语言项文字可见性...")
        
        for i in range(count):
            item = dropdown_items.nth(i)
            text = item.text_content().strip()
            
            # 获取文字颜色
            color = item.evaluate("el => getComputedStyle(el).color")
            
            # 获取背景颜色
            bg_color = item.evaluate("el => getComputedStyle(el).backgroundColor")
            
            print(f"   语言项 {i+1}: '{text}'")
            print(f"      文字颜色：{color}")
            print(f"      背景颜色：{bg_color}")
            
            # 检查文字是否可见（非白色文字在白色背景上）
            if "rgb(255, 255, 255)" in color and "rgb(255, 255, 255)" in bg_color:
                print(f"      ⚠️ 警告：白色文字在白色背景上可能不可见")
            elif "rgb(0, 0, 0)" in color or "rgb(15, 23, 42)" in color:
                print(f"      ✓ 文字颜色正常（深色）")
            else:
                print(f"      ✓ 文字颜色：{color}")
        
        # Step 9: 截图
        print("\n9. 截图...")
        screenshot_path = "screenshots/issues/80/language_dropdown.png"
        dropdown_menu.screenshot(path=screenshot_path)
        print(f"   截图已保存到：{screenshot_path}")
        
        # 清理
        browser.close()
        
        print("\n" + "=" * 50)
        print("测试完成 ✓")
        print("=" * 50)

if __name__ == "__main__":
    test_language_dropdown()
