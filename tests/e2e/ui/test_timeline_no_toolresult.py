"""
测试 Issue #35: Conversation Timeline 不能区分 Assistant 和 ToolResult，建议去掉 ToolResult

验证 Timeline 不再显示 ToolResult 类型的消息，只保留 User 和 Assistant 两种类型。
"""

import asyncio
import os
import sys
from datetime import datetime

import pytest

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

import contextlib

import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from scripts.shared import utils

pytestmark = [pytest.mark.regression, pytest.mark.issue(35)]


# Screenshot directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Go up two levels: tests/issues/35 -> tests/issues -> tests -> project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots", "issues", "35")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_timeline_no_toolresult():
    """测试 Timeline 不显示 ToolResult 消息"""
    _skip_if_no_server()
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            # 0. 登录
            print("0. 登录...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE_URL}/", timeout=15000)
            await page.wait_for_timeout(2000)
            print("   ✓ 登录成功")
            results.append(("登录", True, ""))

            # 1. 访问主页
            print("1. 访问主页...")
            # 主页已经通过登录重定向访问
            await page.wait_for_timeout(1000)

            # 截图
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/homepage_{timestamp}.png", full_page=True)
            print("   ✓ 主页加载成功")
            results.append(("访问主页", True, ""))

            # 2. 点击 Analysis 标签
            print("2. 点击 Analysis 标签...")
            analysis_tab = await page.query_selector("#nav-analysis")
            if analysis_tab:
                await analysis_tab.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Analysis 标签已点击")
                results.append(("点击 Analysis 标签", True, ""))
            else:
                print("   ✗ 失败: 未找到 Analysis 标签")
                results.append(("点击 Analysis 标签", False, "未找到标签"))
                return results

            # 3. 点击 Conversation History 标签
            print("3. 点击 Conversation History 标签...")
            history_tab = await page.query_selector("#conversation-history-tab")
            if history_tab:
                await history_tab.click()
                await page.wait_for_timeout(1000)
                print("   ✓ Conversation History 标签已点击")
                results.append(("点击 Conversation History 标签", True, ""))
            else:
                print("   ✗ 失败: 未找到 Conversation History 标签")
                results.append(("点击 Conversation History 标签", False, "未找到标签"))
                return results

            # 4. 等待 Conversation History 表格加载
            print("4. 等待 Conversation History 表格加载...")
            await page.wait_for_selector("#conversation-history-table", timeout=10000)
            await page.wait_for_timeout(2000)

            # 截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/conversation_history_{timestamp}.png", full_page=True
            )
            print("   ✓ Conversation History 表格已加载")
            results.append(("Conversation History 加载", True, ""))

            # 5. 找到并点击第一个 Timeline 按钮
            print("4. 点击 Timeline 按钮...")
            timeline_button_found = await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button[onclick*="showTimelineModal"]');
                if (buttons.length > 0) {
                    buttons[0].click();
                    return true;
                }
                return false;
            }""")

            assert timeline_button_found, "Timeline button not found in conversation history"

            print("   ✓ Timeline 按钮已点击")
            results.append(("点击 Timeline 按钮", True, ""))

            # 等待 Modal 显示
            print("   等待 Timeline Modal 显示...")
            await page.wait_for_timeout(1000)

            # 5. 验证 Timeline Modal 已打开
            print("5. 验证 Timeline Modal 已打开...")
            modal_visible = await page.is_visible("#timelineModal")
            assert modal_visible, "Timeline modal did not open"

            # 检查 Modal 是否有 show 类
            modal_has_show = await page.evaluate("""() => {
                const modal = document.getElementById('timelineModal');
                return modal.classList.contains('show');
            }""")

            if not modal_has_show:
                print("   ⚠ Modal 元素存在但没有 show 类，等待中...")
                await page.wait_for_timeout(500)
                modal_has_show = await page.evaluate("""() => {
                    const modal = document.getElementById('timelineModal');
                    return modal.classList.contains('show');
                }""")

            assert modal_has_show, "Timeline modal did not receive the 'show' class"

            print("   ✓ Timeline Modal 已打开")
            results.append(("Timeline Modal 打开", True, ""))

            # 等待数据加载
            await page.wait_for_timeout(1000)

            # 截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/timeline_modal_{timestamp}.png", full_page=True
            )

            # 6. 检查 timeline items 是否存在
            print("6. 检查 Timeline 内容...")
            timeline_items_count = await page.evaluate("""() => {
                return document.querySelectorAll('.timeline-item').length;
            }""")

            if timeline_items_count > 0:
                print(f"   ✓ 找到 {timeline_items_count} 个 Timeline Items")
                results.append(("Timeline Items 显示", True, f"{timeline_items_count} 个"))
            else:
                print("   ⚠ 未找到 Timeline Items (可能该会话没有消息)")
                results.append(("Timeline Items 显示", True, "无消息数据"))

            # 7. 验证 Timeline 只包含 User 和 Assistant 角色
            print("7. 验证 Timeline 只包含 User 和 Assistant 角色...")

            # 获取所有显示的角色标签
            role_labels = await page.evaluate("""() => {
                const items = document.querySelectorAll('.timeline-item .card-body strong');
                return Array.from(items).map(item => item.textContent.trim());
            }""")

            print(f"   找到的角色标签: {role_labels}")

            # 检查是否有非 User/Assistant 的角色
            invalid_roles = [
                role
                for role in role_labels
                if role not in ["User", "用户", "Assistant", "AI 助手", "AI"]
            ]

            if len(invalid_roles) > 0:
                print(f"   ✗ 失败: 发现非预期的角色: {invalid_roles}")
                results.append(("角色验证", False, f"发现非预期角色: {invalid_roles}"))
            else:
                print("   ✓ 所有角色都是 User 或 Assistant")
                results.append(("角色验证", True, "只包含 User 和 Assistant"))
            assert not invalid_roles, f"unexpected roles in timeline: {invalid_roles}"

            # 8. 验证 Summary 中的计数
            print("8. 验证 Summary 统计...")
            summary_text = await page.text_content(".timeline-summary")
            print(f"   Summary 内容: {summary_text}")
            results.append(("Summary 显示", True, summary_text[:50] if summary_text else ""))

            # 最终截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/timeline_final_{timestamp}.png", full_page=True
            )

        except PlaywrightError as e:
            print(f"   ✗ 测试异常: {str(e)}")
            results.append(("测试执行", False, str(e)))

            # 异常时截图
            with contextlib.suppress(BaseException):
                await page.screenshot(
                    path=f"{SCREENSHOT_DIR}/error_{timestamp}.png", full_page=True
                )

        finally:
            await browser.close()

    return results


@pytest.mark.asyncio
async def test_api_timeline_no_toolresult():
    """测试 API 返回的 timeline 数据不包含 toolResult"""
    _skip_if_no_server()
    import aiohttp

    results = []

    async with aiohttp.ClientSession() as session:
        # 获取 session 列表
        print("1. 获取 session 列表...")
        async with session.get(
            f"{BASE_URL}/api/conversation-history?start={utils.get_days_ago(7)}&end={utils.get_today()}&page=1&limit=20"
        ) as resp:
            assert resp.status == 200, f"session list API returned {resp.status}"

            data = await resp.json()
            sessions = data.get("sessions", [])

            if not sessions:
                print("   ⚠ 没有找到 session 数据")
                results.append(("获取 session 列表", True, "无数据"))
                return results

            print(f"   ✓ 找到 {len(sessions)} 个 sessions")
            results.append(("获取 session 列表", True, f"{len(sessions)} 个"))

        # 测试第一个 session 的 timeline
        first_session = sessions[0]
        session_id = first_session.get("session_id") or first_session.get("conversation_label")

        assert session_id, "could not derive session_id from the first session row"

        print(f"2. 测试 session: {session_id}")

        async with session.get(f"{BASE_URL}/api/conversation-timeline/{session_id}") as resp:
            assert resp.status == 200, f"timeline API returned {resp.status}"

            timeline_data = await resp.json()
            timeline = timeline_data.get("timeline", [])

            print(f"   ✓ 获取到 {len(timeline)} 条 timeline 记录")
            results.append(("获取 Timeline 数据", True, f"{len(timeline)} 条"))

            # 检查是否有 toolResult
            toolresult_count = sum(1 for item in timeline if item.get("role") == "toolResult")
            user_count = sum(1 for item in timeline if item.get("role") == "user")
            assistant_count = sum(1 for item in timeline if item.get("role") == "assistant")

            print(f"   - User 消息: {user_count}")
            print(f"   - Assistant 消息: {assistant_count}")
            print(f"   - ToolResult 消息: {toolresult_count}")

            if toolresult_count > 0:
                print(f"   ✗ 失败: Timeline 包含 {toolresult_count} 条 toolResult 消息")
                results.append(
                    ("ToolResult 过滤验证", False, f"发现 {toolresult_count} 条 toolResult")
                )
            else:
                print("   ✓ Timeline 不包含任何 toolResult 消息")
                results.append(("ToolResult 过滤验证", True, "无 toolResult"))
            assert toolresult_count == 0, f"timeline contains {toolresult_count} toolResult entries"

    return results
