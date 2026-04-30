#!/usr/bin/env python3
"""
检查 /work/usage 页面的 HTML 结构
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

def check_page_structure():
    """检查页面结构"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # 登录
        page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)

        # 导航到 usage 页面
        page.goto(f"{BASE_URL}/work/usage", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # 获取页面内容
        html = page.content()
        
        # 查找所有卡片标题
        card_titles = page.locator(".card-title, .card h5").all_text_contents()
        print("卡片标题:")
        for title in card_titles:
            print(f"  - {title}")

        # 查找包含趋势图的区域
        print("\n查找趋势图相关元素...")
        
        # 检查是否有 canvas 元素（图表通常用 canvas）
        canvas_count = page.locator("canvas").count()
        print(f"Canvas 元素数量: {canvas_count}")

        # 检查是否有 border-top 元素（统计分隔线）
        border_top_count = page.locator(".border-top").count()
        print(f"Border-top 元素数量: {border_top_count}")

        # 检查是否有包含"平均"或"Average"的文本
        page_text = page.locator("body").text_content()
        avg_keywords = ["平均", "Average", "average", "平均值"]
        max_keywords = ["最高", "Maximum", "maximum", "最高值"]
        
        found_avg = [kw for kw in avg_keywords if kw in page_text]
        found_max = [kw for kw in max_keywords if kw in page_text]
        
        print(f"\n找到的平均值关键词: {found_avg}")
        print(f"找到的最高值关键词: {found_max}")

        # 打印页面的部分文本内容
        print("\n页面文本内容片段:")
        # 找到 card 元素的内容
        cards = page.locator(".card").all()
        for i, card in enumerate(cards[:5]):
            card_text = card.text_content()
            print(f"\n--- Card {i+1} ---")
            print(card_text[:500])

        # 截图
        page.screenshot(path="/Users/rhuang/workspace/open-ace/screenshots/issues/51/page_structure.png")
        print("\n截图保存: screenshots/issues/51/page_structure.png")

        browser.close()

if __name__ == "__main__":
    check_page_structure()