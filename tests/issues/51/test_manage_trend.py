#!/usr/bin/env python3
"""
检查 manage 页面的趋势图
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


def check_manage_trend():
    """检查 manage 页面趋势图"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # 登录
        page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)

        # 导航到 manage/dashboard 页面
        page.goto(f"{BASE_URL}/manage/dashboard", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # 检查页面内容
        print("Manage Dashboard 页面检查:")

        # 查找所有卡片标题
        card_titles = page.locator(".card-title, .card h5").all_text_contents()
        print("卡片标题:")
        for title in card_titles:
            print(f"  - {title}")

        # 检查是否有 canvas 元素
        canvas_count = page.locator("canvas").count()
        print(f"\nCanvas 元素数量: {canvas_count}")

        # 检查是否有 border-top 元素
        border_top_count = page.locator(".border-top").count()
        print(f"Border-top 元素数量: {border_top_count}")

        # 检查页面文本中是否有平均值和最高值
        page_text = page.locator("body").text_content()
        avg_keywords = ["平均", "Average", "average", "平均值"]
        max_keywords = ["最高", "Maximum", "maximum", "最高值"]

        found_avg = [kw for kw in avg_keywords if kw in page_text]
        found_max = [kw for kw in max_keywords if kw in page_text]

        print(f"\n找到的平均值关键词: {found_avg}")
        print(f"找到的最高值关键词: {found_max}")

        # 截图
        page.screenshot(
            path="/Users/rhuang/workspace/open-ace/screenshots/issues/51/manage_dashboard.png",
            full_page=True,
        )
        print("\n截图保存: screenshots/issues/51/manage_dashboard.png")

        browser.close()


if __name__ == "__main__":
    check_manage_trend()
