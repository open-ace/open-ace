#!/usr/bin/env python3
"""
回归测试辅助模块

提供共享的测试配置和辅助函数，优化等待策略避免超时问题。
"""

import logging
import os

from playwright.sync_api import Page

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
PAGE_LOAD_TIMEOUT_MS = int(os.environ.get("E2E_PAGE_LOAD_TIMEOUT_MS", "60000"))
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
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
    try:
        page.screenshot(path=path, timeout=10000)
    except Exception as e:
        # Fallback: skip screenshot on timeout (font loading may hang in CI)
        logging.getLogger(__name__).warning(f"Screenshot failed: {e}")
    return path


def create_browser_context(p):
    """创建浏览器上下文"""
    browser = p.chromium.launch(headless=HEADLESS)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    return browser, context


def dismiss_force_change_password_modal(page: Page, new_password: str = "Admin1234!"):
    """
    处理强制修改密码弹窗（ForceChangePasswordModal）。

    默认 admin 用户首次登录时 must_change_password=true，会弹出不可关闭的
    强制改密弹窗。本函数检测该弹窗并自动完成改密流程，避免阻塞后续测试。

    改密成功后更新模块级 PASSWORD 变量，使后续 login() 调用使用新密码。

    无论弹窗是否出现，函数返回前都会等待页面 dashboard 元素就绪。

    Args:
        page: Playwright 页面对象
        new_password: 新密码（需满足密码策略，默认 Admin1234!）
    """
    global PASSWORD
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        # 等待弹窗出现（最多 5 秒）
        modal = page.locator('div[role="dialog"]')
        modal.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        # 没有弹窗，正常情况
        pass
    else:
        # 弹窗内三个密码输入框通过 placeholder 定位
        current_pw_input = page.locator(
            'div[role="dialog"] input[placeholder="Enter current password"],'
            'div[role="dialog"] input[placeholder="输入当前密码"]'
        )
        new_pw_input = page.locator(
            'div[role="dialog"] input[placeholder="Enter new password"],'
            'div[role="dialog"] input[placeholder="输入新密码"]'
        )
        confirm_pw_input = page.locator(
            'div[role="dialog"] input[placeholder="Confirm Password"],'
            'div[role="dialog"] input[placeholder="确认密码"]'
        )

        # 填写改密表单
        current_pw_input.fill(PASSWORD)
        new_pw_input.fill(new_password)
        confirm_pw_input.fill(new_password)

        # 点击改密按钮
        change_btn = page.locator(
            'div[role="dialog"] button:has-text("Change Password"),'
            'div[role="dialog"] button:has-text("修改密码")'
        )
        change_btn.click()

        # 等待弹窗消失
        try:
            modal.wait_for(state="hidden", timeout=15000)
        except PlaywrightTimeoutError:
            save_screenshot(page, "login", "force_change_password_failed")
            raise AssertionError(
                "Force change password modal did not close after submission. "
                "Check if the new password meets the password policy."
            )

        # 更新全局密码，后续 login() 使用新密码
        PASSWORD = new_password

    # 无论弹窗是否出现，等待 dashboard 元素就绪
    # 这确保页面完全渲染后再返回，避免后续交互超时
    try:
        page.wait_for_selector(
            ".header-icon-btn.dropdown-toggle, .dropdown-toggle:has(.bi-person-circle)",
            state="visible",
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        # Fallback: 等待任意导航栏元素
        try:
            page.wait_for_selector("nav, .navbar, header, .sidebar", state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            # 最后兜底：等待页面稳定
            page.wait_for_timeout(3000)


def login(page: Page):
    """
    登录函数 - 使用优化的等待策略

    避免使用 networkidle，改用 domcontentloaded 和显式等待。
    Includes retry logic for page.goto to handle transient server slowness.
    Handles the ForceChangePasswordModal that appears for default admin user.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    # 导航到登录页面，等待 DOM 加载完成（带重试）
    max_goto_retries = 3
    for attempt in range(max_goto_retries):
        try:
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
            break
        except PlaywrightTimeoutError:
            if attempt < max_goto_retries - 1:
                # Wait before retry to let server recover
                page.wait_for_timeout(3000)
            else:
                # Save screenshot on final timeout for debugging
                save_screenshot(page, "login", "page_load_timeout")
                raise AssertionError(
                    f"Login page failed to load within {PAGE_LOAD_TIMEOUT_MS}ms "
                    f"after {max_goto_retries} attempts. "
                    f"Check if server is running at {BASE_URL}"
                )

    # 等待登录表单元素可见
    try:
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.wait_for_selector("#password", state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        save_screenshot(page, "login", "form_not_visible")
        raise AssertionError(
            "Login form elements not visible within 10s. " "Check if page loaded correctly"
        )

    # 填写凭据
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)

    # 点击登录按钮
    page.click('button[type="submit"]')

    # 等待登录成功 - bcrypt rounds=12 可能需要较长时间
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=120000)
    except Exception as e:
        current_url = page.url
        if "/login" in current_url:
            save_screenshot(page, "login", "redirect_failed")
            raise AssertionError(
                f"Login did not redirect after 120s. Still on {current_url}. "
                f"Check if credentials are correct or server is responsive. Error: {e}"
            ) from e

    # 处理强制修改密码弹窗（默认 admin 用户首次登录后会出现）
    dismiss_force_change_password_modal(page)


def navigate_to(page: Page, path: str):
    """
    导航到指定路径 - 使用优化的等待策略

    避免使用 networkidle，改用 domcontentloaded 和元素等待
    """
    page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)

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
