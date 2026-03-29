#!/usr/bin/env python3
"""Check custom sender dropdown on Messages page"""

import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5001"
USERNAME = "admin"
PASSWORD = "admin123"

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
context = browser.new_context()
page = context.new_page()

try:
    # Login
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE_URL}/", timeout=15000)
    print("Login successful")

    # Navigate to Messages
    page.click("#nav-messages")
    page.wait_for_selector("#messages-container", state="visible", timeout=5000)
    time.sleep(2)
    print("Messages page loaded")

    # Check native select options (hidden)
    sender_filter = page.locator("#sender-filter")
    options = sender_filter.locator("option")
    print(f"\nNative select options count: {options.count()}")
    for i in range(min(6, options.count())):
        text = options.nth(i).inner_text()
        print(f"  Option {i}: {text}")

    # Set date to yesterday
    date_input = page.locator("#date-filter")
    date_input.fill("2026-03-13")
    page.evaluate('document.getElementById("date-filter").dispatchEvent(new Event("change"))')
    time.sleep(3)
    print("\nDate set to 2026-03-13")

    # Check native select options after date change
    options = sender_filter.locator("option")
    print(f"\nNative select options count: {options.count()}")
    for i in range(min(6, options.count())):
        text = options.nth(i).inner_text()
        print(f"  Option {i}: {text}")

    # Check custom dropdown menu
    print("\nChecking custom dropdown menu...")
    wrapper = page.locator(".btn-group").filter(has=page.locator(".filter-text"))
    dropdown_btn = wrapper.locator("button")
    dropdown_btn.click()
    time.sleep(0.5)

    menu_items = page.locator(".sender-dropdown-menu .dropdown-item")
    print(f"Custom dropdown items count: {menu_items.count()}")
    for i in range(min(6, menu_items.count())):
        text = menu_items.nth(i).inner_text()
        print(f"  Item {i}: {text}")

    # Take screenshot
    page.screenshot(path="screenshots/issue36_custom_dropdown.png", full_page=True)
    print("\nScreenshot saved")

finally:
    browser.close()
