#!/usr/bin/env python3
"""
检查 API 返回的数据结构
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

def check_api_data():
    """检查 API 数据"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # 监听网络请求
        api_responses = []

        def handle_response(response):
            if "/api/" in response.url:
                try:
                    body = response.text()
                    api_responses.append({
                        "url": response.url,
                        "status": response.status,
                        "body": body[:2000]  # 只截取前 2000 字符
                    })
                except:
                    pass

        page.on("response", handle_response)

        # 登录
        page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)

        # 导航到 usage 页面
        page.goto(f"{BASE_URL}/work/usage", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # 打印 API 响应
        print("API 响应:")
        for resp in api_responses:
            if "quota" in resp["url"] or "usage" in resp["url"]:
                print(f"\n--- {resp['url']} ---")
                print(f"Status: {resp['status']}")
                print(f"Body: {resp['body']}")

        browser.close()

if __name__ == "__main__":
    check_api_data()