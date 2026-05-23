#!/usr/bin/env python3
"""
Open ACE - Web Terminal / Claude Code Demo

演示在浏览器中创建远程会话，在 webui 里发送消息给 Claude Code AI，
并看到 AI 的回复。

运行:
  python tests/394/demo_web_terminal.py
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

TEST_USER = "admin"
TEST_PASS = "admin123"

MACHINE_ID = "0092acb3-9b6d-46db-b6c0-73f4e6d363f3"


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


def main():
    print("=" * 60)
    print("Web Terminal / Claude Code 演示")
    print("  可见浏览器模式 - 你可以看到完整流程")
    print("=" * 60)

    # Step 1: 登录获取 token
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    token = resp.cookies.get("session_token")
    log("认证", f"已登录: {TEST_USER}")

    # Step 2: 创建 Claude Code 远程会话
    log("会话", "创建 Claude Code 远程会话...")
    sess_resp = requests.post(
        f"{BASE_URL}/api/remote/sessions",
        cookies={"session_token": token},
        json={
            "machine_id": MACHINE_ID,
            "project_path": "/root",
            "cli_tool": "claude-code",
            "title": "Web Terminal 演示",
        },
    )
    assert sess_resp.status_code == 200, f"创建失败: {sess_resp.text}"
    session_id = sess_resp.json()["session"]["session_id"]
    log("会话", f"已创建: {session_id}")
    time.sleep(3)

    # Step 3: 打开可见浏览器
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # 可见模式
            slow_mo=100,  # 放慢操作便于观察
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
            # 导航到 workspace
            workspace_url = (
                f"{BASE_URL}/work/workspace"
                f"?sessionId={session_id}"
                f"&workspaceType=remote"
                f"&machineId={MACHINE_ID}"
                f"&machineName=openace"
            )
            log("界面", f"打开 Workspace: {workspace_url}")
            page.goto(workspace_url, wait_until="load", timeout=30000)
            time.sleep(10)  # 等待 React 加载

            # 检查 iframe 元素
            iframe_elements = page.locator("iframe")
            iframe_count = iframe_elements.count()
            log("界面", f"找到 {iframe_count} 个 iframe 元素")

            # 查找 webui iframe (通过 iframe 元素的 src 属性)
            webui_frame = None
            for i in range(iframe_count):
                src = iframe_elements.nth(i).get_attribute("src")
                log("界面", f"  iframe[{i}] src: {src[:80] if src else 'None'}...")
                if src and "token=" in src:
                    # 通过 page.frames 找到匹配的 frame
                    for frame in page.frames:
                        if frame.url and src.split("?")[0] in frame.url:
                            webui_frame = frame
                            log("界面", "选中 webui frame")
                            break
                    if webui_frame:
                        break

            if not webui_frame:
                log("错误", f"未找到 webui iframe. Frames: {[f.url[:50] for f in page.frames]}")
                time.sleep(10)
                browser.close()
                return

            log("界面", "找到 webui iframe")

            # 等待聊天输入框
            textarea = webui_frame.locator("textarea").first
            textarea.wait_for(state="visible", timeout=20000)
            log("聊天", "找到聊天输入框")

            # 在输入框输入消息
            test_message = "你好，请用一句话介绍你自己"
            log("聊天", f"输入消息: '{test_message}'")
            textarea.click()
            time.sleep(0.5)
            textarea.fill(test_message)
            time.sleep(1)

            # 发送消息
            log("聊天", "发送消息 (按 Enter)...")
            textarea.press("Enter")

            log("聊天", "等待 AI 回复...")
            # 等待 60 秒观察 AI 回复
            time.sleep(60)
            log("演示", "演示结束")

        except KeyboardInterrupt:
            log("信息", "用户中断")
        except Exception as e:
            log("错误", str(e))
            time.sleep(10)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
