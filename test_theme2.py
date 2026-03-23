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
    
    # 点击主题切换按钮
    theme_btn = page.locator("button.header-icon-btn").nth(0)
    theme_btn.click()
    time.sleep(3)
    
    # 再次检查
    html = page.locator("html")
    theme = html.evaluate('el => el.getAttribute("data-theme")')
    print(f"html data-theme after click: {theme}")
    
    # 检查 body 类
    body = page.locator("body")
    has_dark_theme = body.evaluate('el => el.classList.contains("dark-theme")')
    print(f"body has dark-theme class: {has_dark_theme}")
    
    browser.close()
