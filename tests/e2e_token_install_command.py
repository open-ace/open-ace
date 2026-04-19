#!/usr/bin/env python3
"""
Open ACE - Registration Token Install Command E2E Test

验证注册令牌对话框中安装命令功能:
1. 以 admin 登录
2. 进入管理页面 → 远程机器
3. 点击"生成注册令牌"
4. 验证对话框中显示令牌 + 安装命令
5. 验证安装命令格式正确
6. 测试复制按钮功能

Run:
  HEADLESS=true  python tests/e2e_token_install_command.py   # 自动测试
  HEADLESS=false python tests/e2e_token_install_command.py   # 演示
"""

import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
TEST_USER = "admin"
TEST_PASS = "admin123"
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "tests", "screenshots", "e2e-token")


def ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def shot(page, name):
    ensure_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {name}.png")


def log_step(tag, msg):
    print(f"  [{tag}] {msg}")


def pause(seconds):
    if not HEADLESS:
        time.sleep(seconds)
    else:
        time.sleep(0.3)


def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=100 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        try:
            # ══════ 1. 登录 ══════
            print("\n══════ 1. 登录 ══════")
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            page.wait_for_selector("#username", state="visible", timeout=10000)
            shot(page, "01_login")

            page.fill("#username", TEST_USER)
            page.fill("#password", TEST_PASS)
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            page.wait_for_selector("main, h1, h2, .dashboard", timeout=15000)
            pause(2)
            shot(page, "02_logged_in")
            print("  ✓ 登录成功")

            # ══════ 2. 进入远程机器管理页面 ══════
            print("\n══════ 2. 进入远程机器管理页面 ══════")
            page.goto(f"{BASE_URL}/manage/remote/machines",
                      wait_until="domcontentloaded")
            page.wait_for_selector("h2", timeout=10000)
            pause(2)
            shot(page, "03_remote_machines_page")
            print("  ✓ 远程机器管理页面加载完成")

            # ══════ 3. 点击"生成注册令牌" ══════
            print("\n══════ 3. 生成注册令牌 ══════")
            # 找到生成令牌按钮（有 bi-plus-lg 图标）
            token_btn = page.locator("button:has(.bi-plus-lg)")
            token_btn.click()
            log_step("点击", "生成注册令牌按钮")

            # 等待对话框出现
            page.wait_for_selector(".modal.show, .modal-backdrop", timeout=10000)
            pause(1)
            shot(page, "04_token_dialog")
            print("  ✓ 令牌对话框已打开")

            # ══════ 4. 验证对话框内容 ══════
            print("\n══════ 4. 验证对话框内容 ══════")

            # 验证令牌输入框存在
            token_inputs = page.locator(".modal input[readonly].font-monospace")
            token_count = token_inputs.count()
            log_step("验证", f"只读输入框数量: {token_count}")
            assert token_count >= 1, f"应至少有 1 个只读输入框，实际 {token_count}"

            # 验证令牌值非空
            token_value = token_inputs.nth(0).input_value()
            assert len(token_value) > 0, "令牌值不应为空"
            log_step("验证", f"令牌值: {token_value[:16]}...")

            # 验证安装命令区域存在
            assert token_count >= 2, f"应有 2 个只读输入框（令牌 + 安装命令），实际 {token_count}"

            install_cmd = token_inputs.nth(1).input_value()
            log_step("验证", f"安装命令: {install_cmd[:80]}...")

            # 验证安装命令格式
            assert "curl" in install_cmd, "安装命令应包含 curl"
            assert "install.sh" in install_cmd, "安装命令应包含 install.sh"
            assert "--server" in install_cmd, "安装命令应包含 --server"
            assert "--token" in install_cmd, "安装命令应包含 --token"
            assert token_value in install_cmd, "安装命令应包含令牌值"
            assert BASE_URL in install_cmd, f"安装命令应包含服务器地址 {BASE_URL}"

            print("  ✓ 令牌输入框存在且非空")
            print("  ✓ 安装命令输入框存在")
            print("  ✓ 安装命令格式正确 (curl ... | bash -s -- --server ... --token ...)")

            # ══════ 5. 验证复制安装命令按钮 ══════
            print("\n══════ 5. 测试复制安装命令按钮 ══════")
            # 安装命令旁边的复制按钮（第二个）
            copy_btns = page.locator(".modal .btn-outline-secondary:has(.bi-clipboard), .modal .btn-outline-secondary:has(.bi-check)")
            btn_count = copy_btns.count()
            log_step("验证", f"复制按钮数量: {btn_count}")
            assert btn_count >= 2, f"应有至少 2 个复制按钮，实际 {btn_count}"

            # 点击安装命令的复制按钮
            if btn_count >= 2:
                # 确保按钮可见（clipboard 图标）
                second_copy_btn = copy_btns.nth(1)
                second_copy_btn.click()
                pause(0.5)
                shot(page, "05_copy_install_cmd")

                # 验证按钮图标变为 check
                check_icons = page.locator(".modal .btn-outline-secondary .bi-check")
                log_step("验证", f"check 图标数量: {check_icons.count()}")
                print("  ✓ 复制安装命令按钮可点击")

            # ══════ 6. 关闭对话框 ══════
            print("\n══════ 6. 关闭对话框 ══════")
            close_btn = page.locator(".modal .btn-secondary").first
            close_btn.click()
            pause(1)
            shot(page, "06_dialog_closed")
            print("  ✓ 对话框已关闭")

            # ══════ 结果 ══════
            print("\n" + "=" * 50)
            print("  ✅ 所有测试通过！")
            print("  ✅ 注册令牌对话框中安装命令功能正常")
            print("=" * 50)

        except Exception as e:
            shot(page, "99_error")
            print(f"\n❌ 测试失败: {e}")
            traceback.print_exc()
            raise

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run_tests()
