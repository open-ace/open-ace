#!/usr/bin/env python3
"""Check sender dropdown on Messages page"""

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

    # Set date to yesterday
    date_input = page.locator("#date-filter")
    date_input.fill("2026-03-13")
    time.sleep(1)

    # Trigger change event
    page.evaluate('document.getElementById("date-filter").dispatchEvent(new Event("change"))')
    time.sleep(2)
    print("Date set to 2026-03-13")

    # Check sender dropdown options
    sender_filter = page.locator("#sender-filter")
    options = sender_filter.locator("option")
    print(f"\nSender options count: {options.count()}")

    for i in range(options.count()):
        text = options.nth(i).inner_text()
        value = options.nth(i).get_attribute("value")
        print(f"  Option {i}: value='{value}', text='{text}'")

    # Take screenshot
    page.screenshot(path="screenshots/issue36_sender_check.png", full_page=True)
    print("\nScreenshot saved")

finally:
    browser.close()
