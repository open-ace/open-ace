"""
测试 Issue 30: 验证工具标签显示逻辑

#2491 R3b realignment: the retired selectors (input[name="username"],
#nav-messages, #messages-container) matched the pre-React chrome. The current
contract: /manage/messages (frontend/src/components/features/Messages.tsx)
renders ``.messages`` with ``.message-item`` cards, each carrying a
``.message-source`` span whose class includes the source value
(Messages.tsx lines 447-450). The page only shows messages when daily_messages
has rows, so the test seeds its own rows (message_source set; same seeding
pattern as tests/e2e/manage/e2e_conversation_history.py) and cleans them up.
"""

import asyncio
import os
import uuid

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(30)]


HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "screenshots")

SEED_PREFIX = "e2e30"


def _seed_messages(count=3):
    """Seed daily_messages rows carrying message_source (label under test)."""
    import sqlite3
    from datetime import datetime, timezone

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return []
    ids = []
    try:
        conn = sqlite3.connect(db_path)
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM daily_messages WHERE message_id LIKE ?",
                (f"{SEED_PREFIX}%",),
            ).fetchone()[0]
            # Date the rows with UTC today (the /api/messages window is
            # backend-computed; the local calendar date can run ahead).
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for i in range(count - existing):
                mid = f"{SEED_PREFIX}-{uuid.uuid4().hex}"
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO daily_messages "
                    "(date, tool_name, host_name, role, content, tokens_used, "
                    "input_tokens, output_tokens, timestamp, sender_name, "
                    "message_id, agent_session_id, conversation_id, user_id, "
                    "message_source) "
                    "VALUES (?, 'claude-code', 'e2e-host-30', ?, "
                    "'e2e tool label probe', 10, 5, 5, ?, 'admin', "
                    "?, ?, ?, 1, 'cli')",
                    (
                        today,
                        "user" if i % 2 == 0 else "assistant",
                        now,
                        mid,
                        mid,
                        mid,
                    ),
                )
                ids.append(mid)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return ids


def _cleanup_messages():
    import sqlite3

    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM daily_messages WHERE message_id LIKE ?", (f"{SEED_PREFIX}%",))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


@pytest.fixture(autouse=True)
def _seeded_messages():
    _seed_messages()
    yield
    _cleanup_messages()


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_issue30_v4():
    """测试工具标签显示"""
    _skip_if_no_server()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f"{BASE_URL}/login")
            # The React login form re-mounts once its effects settle; re-fill
            # until the values persist (same pattern as test_user_scenario).
            for _attempt in range(3):
                await page.wait_for_selector("#username", timeout=10000)
                await page.fill("#username", USERNAME)
                await page.fill("#password", PASSWORD)
                if (
                    await page.input_value("#username") == USERNAME
                    and await page.input_value("#password") == PASSWORD
                ):
                    break
                await asyncio.sleep(0.5)
            await page.click('button[type="submit"]')
            await page.wait_for_url("**/manage/**", timeout=15000)
            print("   ✓ 登录成功")

            # 2. 导航到 Messages 页面（当前路由 /manage/messages）
            print("2. 导航到 Messages 页面...")
            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle")
            messages_container = page.locator(".messages")
            await messages_container.first.wait_for(state="visible", timeout=10000)
            print("   消息容器已可见")

            # 等待消息加载完成 - 等待 message-item 出现（默认角色过滤已启用）
            try:
                await page.wait_for_selector(".message-item", timeout=15000)
                print("   消息项已加载")
            except:
                print("   等待消息项超时，检查页面状态...")

            # 额外等待确保渲染完成
            await asyncio.sleep(2)

            # 检查是否有消息
            message_items = await page.query_selector_all(".message-item")
            print(f"   找到 {len(message_items)} 条消息")

            # 3. 截图
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v4_messages.png", full_page=True)
            print("   ✓ 截图保存: issue30_v4_messages.png")

            # 4. 检查工具标签（.message-source 携带消息来源）
            print("\n3. 检查工具标签...")
            tool_badges = await page.query_selector_all(".message-source")
            print(f"   找到 {len(tool_badges)} 个工具标签")

            if len(tool_badges) > 0:
                # 收集所有标签文本
                badge_texts = {}
                for badge in tool_badges:
                    text = await badge.inner_text()
                    text = text.strip().lower()
                    if text not in badge_texts:
                        badge_texts[text] = 0
                    badge_texts[text] += 1

                print("   工具标签统计:")
                for text, count in sorted(badge_texts.items(), key=lambda x: -x[1]):
                    print(f"   - {text}: {count} 个")

                # 标签必须渲染出非空文本
                if all(badge_texts.values()) and all(badge_texts.keys()):
                    print("\n   ✓ 所有工具标签均显示非空文本")
                    results.append(("工具标签显示", "通过"))
                else:
                    print("\n   ✗ 存在空工具标签")
                    results.append(("工具标签显示", "失败"))

                # 检查 CSS class（class 应包含 message-source 与来源值）
                print("\n4. 检查 CSS class...")
                class_ok = True
                for badge in tool_badges[:5]:
                    class_name = await badge.get_attribute("class")
                    text = (await badge.inner_text()).strip().lower()
                    print(f"   标签: '{text}' -> class: '{class_name}'")
                    if not class_name or "message-source" not in class_name:
                        class_ok = False
                if class_ok:
                    results.append(("工具标签 CSS class", "通过"))
                else:
                    results.append(("工具标签 CSS class", "失败"))
            else:
                print("   ✗ 没有找到工具标签（消息数据应已注入）")
                results.append(("工具标签显示", "失败"))

            results.append(("测试执行", "通过"))

        except PlaywrightError as e:
            print(f"   ✗ 测试出错: {e}")
            import traceback

            traceback.print_exc()
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue30_v4_error.png")
            results.append(("测试执行", "失败"))

        finally:
            await browser.close()

        # 打印测试报告
        print("\n" + "=" * 50)
        print("Issue 30 测试报告")
        print("=" * 50)
        for name, status in results:
            symbol = "✓" if status == "通过" else "✗" if status == "失败" else "○"
            print(f"  {symbol} {name}: {status}")
        print("=" * 50)

        assert all(
            status != "失败" for _, status in results
        ), f"issue-30 check(s) failed: {[r for r in results if r[1] == '失败']}"
