#!/usr/bin/env python3
"""
测试提示词页面搜索框光标问题 (Issue #684)

验证：在搜索框输入字符后，光标是否仍停留在搜索框中
"""

from playwright.sync_api import sync_playwright
import time

# 默认登录凭据
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"

def check_focus(page, selector):
    """检查指定选择器的元素是否有焦点"""
    return page.evaluate(f"document.activeElement === document.querySelector('{selector}')")

def test_search_focus():
    """测试搜索框焦点保持"""

    BASE_URL = "http://localhost:5000"
    URL = f"{BASE_URL}/work/prompts"
    # 使用更精确的选择器，只选择 Prompts.tsx 中的搜索框（不是 AssistPanel）
    # Prompts.tsx 的搜索框父容器是 'd-flex flex-nowrap align-items-center gap-2'
    # AssistPanel 的搜索框父容器是 'input-group input-group-sm'
    SEARCH_SELECTOR_PROMPTS = '.prompts .d-flex.flex-nowrap input[placeholder*="Search prompts"]'
    SEARCH_SELECTOR_ASSIST = '.input-group input[placeholder*="Search prompts"]'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # 无图形界面环境使用 headless
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            # 先登录
            print("0. 登录...")
            login_url = f"{BASE_URL}/login"
            page.goto(login_url, timeout=30000)
            page.wait_for_load_state('networkidle', timeout=10000)

            print(f"  当前 URL: {page.url}")
            
            if "login" in page.url:
                try:
                    # 等待登录表单可见
                    page.wait_for_selector('#username', timeout=5000)
                    page.fill("#username", DEFAULT_USERNAME)
                    page.fill("#password", DEFAULT_PASSWORD)
                    page.click('button[type="submit"]')
                    # 等待导航完成，检查是否离开登录页面
                    page.wait_for_function("window.location.href.indexOf('login') === -1", timeout=10000)
                    print(f"  登录后 URL: {page.url}")
                    print("  ✓ 登录成功")
                except Exception as e:
                    print(f"  ⚠ 登录可能失败: {e}")
                    # 截图查看登录状态
                    page.screenshot(path="/home/cfhan/open-ace/screenshots/login_state.png")

            print(f"1. 打开提示词页面: {URL}")
            # 先访问 Vite 开发服务器确保加载最新代码
            if 'localhost:5000' in URL:
                print("  注意: 使用本地 Vite 开发服务器 (端口 3000)")
                page.goto('http://localhost:3000/work/prompts', timeout=30000)
            else:
                page.goto(URL, timeout=30000)

            # 等待页面加载
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(2)  # 等待 React 组件渲染

            print("2. 查找搜索框...")
            # 先截图看看页面状态
            page.screenshot(path="/home/cfhan/open-ace/screenshots/prompts_page.png")
            print("  页面截图: screenshots/prompts_page.png")
            
            # 检查两个搜索框的存在
            prompts_search_count = page.locator(SEARCH_SELECTOR_PROMPTS).count()
            assist_search_count = page.locator(SEARCH_SELECTOR_ASSIST).count()
            print(f"  Prompts 页面搜索框数量: {prompts_search_count}")
            print(f"  AssistPanel 搜索框数量: {assist_search_count}")
            
            # 选择 Prompts 页面的搜索框
            search_input = page.locator(SEARCH_SELECTOR_PROMPTS).first
            
            if not search_input.is_visible():
                print("  ❌ Prompts 页面搜索框未找到或不可见")
                return False
            
            print("  ✓ Prompts 页面搜索框已找到")

            # 获取搜索框初始状态
            initial_value = search_input.input_value()
            print(f"  当前值: '{initial_value}'")

            # 点击搜索框获取焦点
            print("3. 点击搜索框获取焦点...")
            search_input.click()
            time.sleep(0.5)

            # 安装 blur 事件监听器，记录焦点丢失的原因
            page.evaluate("""
                window._focusLog = [];
                const searchInput = document.querySelector('.prompts .d-flex.flex-nowrap input[placeholder*="Search prompts"]');
                if (searchInput) {
                    searchInput.addEventListener('blur', (e) => {
                        window._focusLog.push({
                            time: Date.now(),
                            type: 'blur',
                            relatedTarget: e.relatedTarget?.tagName || 'none',
                            relatedTargetClass: e.relatedTarget?.className || 'none',
                            activeElement: document.activeElement?.tagName || 'none'
                        });
                    });
                }
                document.addEventListener('focusin', (e) => {
                    window._focusLog.push({
                        time: Date.now(),
                        type: 'focusin',
                        target: e.target?.tagName || 'none',
                        targetClass: e.target?.className || 'none'
                    });
                });
            """)
            print("  已安装 blur/focus 事件监听器")
            
            # 验证焦点状态
            is_focused_before = check_focus(page, SEARCH_SELECTOR_PROMPTS)
            print(f"  焦点状态 (输入前): {is_focused_before}")

            if not is_focused_before:
                print("  ❌ 点击后搜索框未获得焦点")
                return False

            print("  ✓ 搜索框已获得焦点")

            # 逐字符输入测试
            print("4. 逐字符输入测试...")
            test_chars = ['a', 'b', 'c']

            for i, char in enumerate(test_chars):
                print(f"\n  输入字符 '{char}'...")

                # 使用 fill 而不是 type，消除输入过程中的时间变量
                search_input.fill(char)
                
                # 记录搜索框的 DOM ID（如果有的话），用于追踪元素是否被重新创建
                input_id_before = search_input.evaluate("el => el.id || 'no-id'")
                input_class_before = search_input.evaluate("el => el.className")
                print(f"    [0ms] 输入元素 ID: '{input_id_before}', class: '{input_class_before}'")
                print(f"    [0ms] 焦点: {check_focus(page, SEARCH_SELECTOR_PROMPTS)}, activeElement: INPUT")
                
                # 在 debounce 过程中监控焦点和 DOM 变化
                last_focus = True
                focus_lost_time = None
                dom_changed_time = None
                
                for ms in range(50, 500, 50):
                    time.sleep(0.05)
                    is_focused = check_focus(page, SEARCH_SELECTOR_PROMPTS)
                    
                    # 检查 DOM 元素是否变化
                    input_id_after = page.locator(SEARCH_SELECTOR_PROMPTS).first.evaluate("el => el.id || 'no-id'")
                    input_class_after = page.locator(SEARCH_SELECTOR_PROMPTS).first.evaluate("el => el.className")
                    
                    dom_changed = (input_id_after != input_id_before) or (input_class_after != input_class_before)
                    active_element = page.evaluate("document.activeElement.tagName")
                    
                    if is_focused != last_focus or dom_changed:
                        print(f"    [{ms}ms] 焦点: {is_focused}, activeElement: {active_element}, DOM变化: {dom_changed}")
                        if not is_focused and focus_lost_time is None:
                            focus_lost_time = ms
                        if dom_changed and dom_changed_time is None:
                            dom_changed_time = ms
                    last_focus = is_focused
                
                if focus_lost_time:
                    print(f"    ⚠ 焦点在 {focus_lost_time}ms 时丢失（debounce 触发时间应为 300ms）")
                    
                    # 检查焦点日志
                    focus_log = page.evaluate("JSON.stringify(window._focusLog || [])")
                    print(f"    焦点事件日志: {focus_log}")
                else:
                    print(f"    ✓ 焦点在 500ms 内保持稳定")
                
                # 检查搜索框是否仍然存在
                search_input_count = page.locator(SEARCH_SELECTOR_PROMPTS).count()
                print(f"    Prompts 搜索框数量: {search_input_count}")
                
                # 检查 AssistPanel 搜索框是否有焦点
                assist_focus = check_focus(page, SEARCH_SELECTOR_ASSIST)
                print(f"    AssistPanel 搜索框焦点: {assist_focus}")
                
                current_value = search_input.input_value()

                print(f"    当前值: '{current_value}'")

                if not is_focused:
                    print(f"    ❌ 输入 '{char}' 后光标跳出！问题存在！")
                    # 截图保存证据
                    page.screenshot(path="/home/cfhan/open-ace/screenshots/search_focus_bug.png")
                    print(f"    截图已保存: screenshots/search_focus_bug.png")
                    return False

                print(f"    ✓ 光标仍停留在搜索框中")

            print("\n5. 快速连续输入测试...")
            # 清空搜索框
            search_input.fill('')
            time.sleep(0.6)  # 等待 debounce 完成

            # 快速输入多个字符
            search_input.type('test input', delay=50)
            time.sleep(0.6)

            is_focused_final = check_focus(page, SEARCH_SELECTOR)
            final_value = search_input.input_value()

            print(f"  最终值: '{final_value}'")
            print(f"  焦点状态: {is_focused_final}")

            if not is_focused_final:
                print("  ❌ 快速输入后光标跳出！")
                page.screenshot(path="/home/cfhan/open-ace/screenshots/search_focus_bug_fast.png")
                return False

            print("  ✓ 快速输入后光标仍停留")

            # 截图保存成功状态
            page.screenshot(path="/home/cfhan/open-ace/screenshots/search_focus_ok.png")
            print("\n截图已保存: screenshots/search_focus_ok.png")

            print("\n✅ 测试通过！搜索框焦点行为正常，Issue #684 已修复")
            return True

        except Exception as e:
            print(f"\n❌ 测试出错: {e}")
            page.screenshot(path="/home/cfhan/open-ace/screenshots/search_focus_error.png")
            return False

        finally:
            browser.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Issue #684: 提示词页面搜索框光标跳出问题验证")
    print("=" * 60)

    result = test_search_focus()

    print("\n" + "=" * 60)
    if result:
        print("结果: ✅ 问题已修复")
    else:
        print("结果: ❌ 问题仍存在")
    print("=" * 60)