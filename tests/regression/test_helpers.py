#!/usr/bin/env python3
"""
回归测试辅助模块

提供共享的测试配置和辅助函数，优化等待策略避免超时问题。
"""

import os
import sys

from playwright.sync_api import BrowserContext, Page, sync_playwright

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "regression",
)


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page: Page, module: str, name: str):
    """保存截图"""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{module}_{name}.png")
    page.screenshot(path=path)
    return path


def create_browser_context(p):
    """创建浏览器上下文"""
    browser = p.chromium.launch(headless=HEADLESS)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    return browser, context


def login(page: Page):
    """
    登录函数 - 使用优化的等待策略

    避免使用 networkidle，改用 domcontentloaded 和显式等待
    """
    # 导航到登录页面，等待 DOM 加载完成
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")

    # 等待登录表单元素可见
    page.wait_for_selector("#username", state="visible", timeout=10000)
    page.wait_for_selector("#password", state="visible", timeout=10000)

    # 填写凭据
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)

    # 点击登录按钮
    page.click('button[type="submit"]')

    # 等待登录成功 - 检查 URL 变化或成功消息
    # 登录后有 500ms 延迟，所以需要等待足够时间
    page.wait_for_timeout(3000)

    # 等待导航完成 - 使用 domcontentloaded 而不是 networkidle
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        # 如果 URL 没变化，检查是否显示成功消息
        success_msg = page.locator(".login-success, .alert-success")
        if success_msg.count() > 0 and success_msg.is_visible():
            # 等待导航完成
            page.wait_for_timeout(2000)
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)


def navigate_to(page: Page, path: str):
    """
    导航到指定路径 - 使用优化的等待策略

    避免使用 networkidle，改用 domcontentloaded 和元素等待
    """
    page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")

    # 等待骨架屏消失（如果存在）
    try:
        # 检查是否有骨架屏，如果有则等待其消失
        skeleton = page.locator(".skeleton")
        if skeleton.count() > 0:
            # 等待骨架屏消失（最多等待 15 秒）
            page.wait_for_selector(".skeleton", state="hidden", timeout=15000)
    except Exception:
        # 如果骨架屏没有消失，继续执行
        pass

    # 额外等待页面渲染完成
    page.wait_for_timeout(1000)

    # 等待主要内容区域加载
    try:
        # 等待主内容区域或页面标题出现
        page.wait_for_selector("main, .manage-content, h1, h2, .page-title", timeout=10000)
    except Exception:
        # 如果没有找到，等待一段时间让页面渲染
        page.wait_for_timeout(2000)


def check_element_exists(page: Page, selectors: list, timeout: int = 5000) -> bool:
    """
    检查元素是否存在（多个选择器，任意一个匹配即可）

    Args:
        page: Playwright 页面对象
        selectors: 选择器列表
        timeout: 超时时间（毫秒）

    Returns:
        bool: 是否找到元素
    """
    for selector in selectors:
        try:
            # 等待元素出现，而不是立即检查
            page.wait_for_selector(selector, state="attached", timeout=timeout)
            element = page.locator(selector)
            if element.count() > 0:
                return True
        except Exception:
            continue
    return False


def wait_for_element(page: Page, selectors: list, timeout: int = 10000):
    """
    等待元素出现（多个选择器，任意一个匹配即可）

    Args:
        page: Playwright 页面对象
        selectors: 选择器列表
        timeout: 超时时间（毫秒）
    """
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout)
            return
        except Exception:
            continue
    raise Exception(f"None of the selectors found: {selectors}")


class TestRunner:
    """测试运行器基类"""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.results = []

    def run_test(self, name: str, test_func):
        """运行单个测试"""
        try:
            test_func()
            self.results.append((name, "PASS", None))
            print(f"  ✓ {name}")
            return True
        except Exception as e:
            self.results.append((name, "FAIL", str(e)))
            print(f"  ✗ {name}: {e}")
            return False

    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "-" * 60)
        passed = sum(1 for r in self.results if r[1] == "PASS")
        total = len(self.results)
        print(f"结果: {passed}/{total} 通过")
        print("-" * 60)
        return self.results

    def print_header(self):
        """打印测试标题"""
        print("\n" + "=" * 60)
        print(f"{self.module_name} 回归测试")
        print("=" * 60)
