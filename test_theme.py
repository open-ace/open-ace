from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    
    page.goto("http://localhost:5001/login")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    
    # 登录
    page.fill("#username", "admin")
    page.fill("#password", "admin123")
    page.click('button[type="submit"]')
    time.sleep(3)
    
    # 检查 html 元素的属性
    html = page.locator("html")
    theme = html.evaluate('el => el.getAttribute("data-theme")')
    print(f"html data-theme: {theme}")
    
    # 直接通过 JavaScript 调用 setTheme
    result = page.evaluate("""
        () => {
            const useAppStore = window.useAppStore || window.__zustand_devtools_store;
            if (useAppStore && useAppStore.getState) {
                useAppStore.getState().setTheme("dark");
                return { success: true, theme: useAppStore.getState().theme };
            }
            return { success: false };
        }
    """)
    time.sleep(2)
    
    print(f"JavaScript result: {result}")
    
    # 再次检查
    html = page.locator("html")
    theme = html.evaluate('el => el.getAttribute("data-theme")')
    print(f"html data-theme after setTheme: {theme}")
    
    browser.close()
