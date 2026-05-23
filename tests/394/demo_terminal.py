#!/usr/bin/env python3
"""
Open ACE - Web Terminal Demo (真实终端 + Claude Code)

演示流程:
1. 打开浏览器导航到 workspace
2. 点击 New Tab 按钮
3. 选择 Terminal workspace type
4. 选择远程机器并创建终端
5. 在终端中运行 `claude` 命令
6. 在 Claude Code CLI 中输入消息并等待 AI 回复

运行:
  HEADLESS=false python tests/394/demo_terminal.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

TEST_USER = "admin"
TEST_PASS = "admin123"

MACHINE_ID = "0092acb3-9b6d-46db-b6c0-73f4e6d363f3"
MACHINE_NAME = "openace"


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def main():
    print("=" * 60)
    print("Web Terminal + Claude Code 演示")
    print(f"  BASE_URL: {BASE_URL}")
    print(f"  HEADLESS: {HEADLESS}")
    print("=" * 60)

    # Step 1: 登录获取 token
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    token = resp.cookies.get("session_token")
    log("认证", f"已登录: {TEST_USER}")

    # Create screenshots directory
    os.makedirs("screenshots/demo-terminal", exist_ok=True)

    # Step 2: 打开可见浏览器
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            slow_mo=50 if not HEADLESS else 0,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        context.add_cookies(
            [
                {
                    "name": "session_token",
                    "value": token,
                    "domain": "localhost",
                    "path": "/",
                }
            ]
        )
        page = context.new_page()

        try:
            # Step 3: 导航到 workspace
            log("界面", f"打开 workspace: {BASE_URL}/work/workspace")
            page.goto(f"{BASE_URL}/work/workspace", wait_until="load", timeout=30000)
            time.sleep(5)

            # Step 4: 点击 New Tab 按钮
            new_tab_btn = page.locator(".workspace-new-tab-btn")
            new_tab_btn.wait_for(state="visible", timeout=10000)
            log("界面", "点击 New Tab 按钮...")
            new_tab_btn.click()
            time.sleep(2)

            # Step 5: 等待 modal 出现
            modal = page.locator(".modal.show")
            modal.wait_for(state="visible", timeout=5000)
            log("界面", "Modal 已打开")

            # Step 6: 选择 Terminal workspace type
            buttons = modal.locator("button")
            terminal_btn = None
            for i in range(buttons.count()):
                text = buttons.nth(i).inner_text()
                if "Terminal" in text or "终端" in text:
                    terminal_btn = buttons.nth(i)
                    break

            if not terminal_btn:
                log("错误", "Terminal 按钮未找到")
                time.sleep(10)
                browser.close()
                return

            log("界面", "选择 Terminal workspace type")
            terminal_btn.click()
            time.sleep(1)

            # Step 7: 选择远程机器
            machine_list = modal.locator(".list-group-item")
            if machine_list.count() == 0:
                log("错误", "没有可用的远程机器")
                time.sleep(10)
                browser.close()
                return

            log("界面", f"选择远程机器: {MACHINE_NAME}")
            machine_list.first.click()
            time.sleep(1)

            # Step 8: 点击 Create 创建终端
            create_btn = modal.locator(".btn-primary").last
            log("界面", "点击 Create 创建终端...")
            create_btn.click()
            time.sleep(15)  # 等待 agent 启动 terminal server

            # Step 9: 等待 xterm.js 终端渲染 (终端标签会自动创建)
            log("终端", "等待终端标签和 xterm.js 渲染...")
            for attempt in range(20):
                xterm_screen = page.locator(".xterm-screen")
                if xterm_screen.count() > 0:
                    log("终端", f"xterm.js 终端已渲染! (第 {attempt+1} 次检查)")
                    break
                time.sleep(1)

            if xterm_screen.count() == 0:
                log("错误", "xterm.js 终端未渲染，检查页面状态...")
                # 截图保存
                page.screenshot(path="screenshots/demo-terminal/error_no_terminal.png")
                log("截图", "screenshots/demo-terminal/error_no_terminal.png")
                time.sleep(10)
                browser.close()
                return

            # Step 10: 等待连接状态变为 Connected
            connected = False
            for _ in range(20):
                body_text = page.locator("body").inner_text()
                if "Connected" in body_text:
                    connected = True
                    log("终端", "WebSocket 已连接!")
                    break
                time.sleep(1)

            if not connected:
                log("警告", "终端未连接，但继续尝试交互")

            # 截图: 连接成功
            page.screenshot(path="screenshots/demo-terminal/01_connected.png")
            log("截图", "screenshots/demo-terminal/01_connected.png")

            time.sleep(3)

            # Step 11: 在终端中输入 claude 命令启动 CLI
            log("CLI", "在终端中启动 Claude Code CLI...")
            xterm_screen.first.click()
            time.sleep(0.5)
            page.keyboard.type("claude")
            time.sleep(0.5)
            page.keyboard.press("Enter")
            time.sleep(5)  # 等待 CLI 启动

            # 截图: CLI 启动
            page.screenshot(path="screenshots/demo-terminal/02_claude_started.png")
            log("截图", "screenshots/demo-terminal/02_claude_started.png")

            log("CLI", "等待 Claude Code CLI 启动...")
            time.sleep(10)

            # Step 12: 在 CLI 中输入消息
            test_message = "你好，请用一句话介绍你自己"
            log("聊天", f"输入消息: '{test_message}'")
            page.keyboard.type(test_message)
            time.sleep(1)
            page.keyboard.press("Enter")

            log("聊天", "等待 AI 回复...")

            # 定期截图观察 AI 回复
            for i in range(12):  # 每5秒截图一次，共12次（60秒）
                time.sleep(5)
                page.screenshot(path=f"screenshots/demo-terminal/03_response_{i}.png")
                log("截图", f"screenshots/demo-terminal/03_response_{i}.png")

            log("演示", "演示结束")

        except Exception as e:
            log("错误", str(e))
            import traceback

            traceback.print_exc()
            page.screenshot(path="screenshots/demo-terminal/error_exception.png")
            time.sleep(10)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
