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
    
    # 检查下拉菜单的背景颜色
    page.click("i.bi-globe")
    time.sleep(1)
    
    dropdown = page.locator("ul.dropdown-menu").first
    bg_style = dropdown.evaluate('el => getComputedStyle(el).backgroundColor')
    color_style = dropdown.evaluate('el => getComputedStyle(el).color')
    
    print(f"下拉菜单背景颜色: {bg_style}")
    print(f"下拉菜单文字颜色: {color_style}")
    
    # 检查所有语言项的样式
    items = page.locator("ul.dropdown-menu button.dropdown-item")
    count = items.count()
    
    for i in range(count):
        item = items.nth(i)
        style = item.evaluate('el => getComputedStyle(el).color')
        bg_style = item.evaluate('el => getComputedStyle(el).backgroundColor')
        text = item.text_content()
        
        print(f"语言项 {i+1}: {text}")
        print(f"  文字颜色: {style}")
        print(f"  背景颜色: {bg_style}")
    
    browser.close()
