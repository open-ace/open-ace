"""
测试 Issue #32: Conversation Timeline 显示不直观

#2491 R3a realignment: the baselined failure set
``#analysis-start-date`` (dates are now ``DatePicker`` buttons,
``frontend/src/components/common/DatePicker.tsx``) and clicked
``button[onclick*="showTimelineModal"]`` expecting ``#timelineModal`` /
``#timelineContainer`` / ``.timeline-item``. The current UI opens the
conversation DETAIL modal from each row's eye button
(``frontend/src/components/features/ConversationHistory.tsx`` lines 679-684,
``<Button variant="outline-primary"><i className="bi bi-eye"/></Button>``).
The modal (``ConversationDetailModal``, lines 884-1320) renders the improved
card-style timeline: nav tabs "Timeline" / "Latency Curve", message-stat
badges (messages / user / assistant / tool / tokens), ``.message-item`` cards
with role badges and ``#序号`` message-number badges, a role filter and a
latency tab with statistics. The repaired test seeds one conversation with
user/assistant/tool messages and asserts that current shape.
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(32)]


# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"

SEED_MARKER = "e2e-issue32"


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _seed_conversation():
    """Seed one conversation with user/assistant/tool messages (multi-role)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    conv_id = f"{SEED_MARKER}-conv-001"
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM daily_messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()[0]
            if existing:
                return
            today = datetime.now().strftime("%Y-%m-%d")
            messages = [
                ("user", f"{SEED_MARKER} user question", 10, 6, 4, 0),
                ("assistant", f"{SEED_MARKER} assistant reply", 20, 4, 16, 1),
                ("tool", f"{SEED_MARKER} tool result", 5, 0, 5, 2),
                ("assistant", f"{SEED_MARKER} follow-up reply", 20, 4, 16, 3),
            ]
            for role, content, tokens, input_tok, output_tok, offset in messages:
                ts = (datetime.now() + timedelta(seconds=offset)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO daily_messages "
                    "(date, tool_name, host_name, role, content, tokens_used, "
                    "input_tokens, output_tokens, timestamp, sender_name, "
                    "message_id, agent_session_id, conversation_id, tenant_id) "
                    "VALUES (?, 'claude-code', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        today,
                        f"{SEED_MARKER}-host",
                        role,
                        content,
                        tokens,
                        input_tok,
                        output_tok,
                        ts,
                        f"{SEED_MARKER}-sender",
                        f"{SEED_MARKER}-msg-{offset}",
                        conv_id,
                        conv_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _cleanup_seeded_conversation():
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.execute(
                f"DELETE FROM daily_messages WHERE conversation_id LIKE '{SEED_MARKER}-%' "
                f"OR host_name = '{SEED_MARKER}-host'"
            )
            conn.execute(
                f"DELETE FROM daily_stats WHERE host_name = '{SEED_MARKER}-host' "
                f"OR sender_name = '{SEED_MARKER}-sender'"
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


@pytest.mark.asyncio
async def test_issue32():
    """测试 Conversation 详情弹窗中的卡片式 Timeline 显示"""

    # 确保截图目录存在
    _skip_if_no_server()
    _seed_conversation()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            # 1. 登录
            print("1. 登录系统...")
            await page.goto(f"{BASE_URL}login")
            await page.wait_for_selector("#username", timeout=15000)
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_url("**/manage/**", timeout=15000)
            await asyncio.sleep(1)
            print("   ✓ 登录成功")
            results.append(("登录", True, ""))

            # 2. 导航到 Conversation History 页面
            print("2. 导航到 Conversation History 页面...")
            await page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            await page.wait_for_selector(".conversation-history", timeout=15000)
            # The loading skeleton is also a table with rows; wait for it to
            # detach before interacting with real rows.
            await page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            print("   ✓ 已进入 Conversation History 页面")
            results.append(("导航到 Conversation History", True, ""))

            # 截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_conversation_history_{timestamp}.png",
                full_page=True,
            )

            # 3. 找到并点击行内的详情（眼睛）按钮打开 Timeline
            print("3. 点击详情按钮打开 Timeline...")
            eye_button = page.locator(".conversation-history tbody tr button:has(i.bi-eye)").first
            await eye_button.click()
            await asyncio.sleep(2)
            print("   ✓ 详情按钮已点击")
            results.append(("点击详情按钮", True, ""))

            # 4. 验证详情 Modal 已打开
            print("4. 验证详情 Modal 已打开...")
            modal = page.locator(".modal.show")
            modal_visible = await modal.count() > 0
            if not modal_visible:
                print("   ✗ 失败: 详情 Modal 未打开")
                results.append(("详情 Modal 打开", False, "Modal 未显示"))
            else:
                title = (await modal.locator(".modal-title").text_content() or "").strip()
                print(f"   ✓ 详情 Modal 已打开: {title}")
                results.append(("详情 Modal 打开", True, title))

            # 截图
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_timeline_modal_{timestamp}.png", full_page=True
            )

            if modal_visible:
                # 5. 验证新的卡片式时间线显示（Timeline / Latency Curve 标签）
                print("5. 验证 Timeline / Latency 标签...")
                tabs = modal.locator(".nav-tabs .nav-link")
                tab_texts = [
                    (await tabs.nth(i).text_content() or "").strip()
                    for i in range(await tabs.count())
                ]
                print(f"   标签: {tab_texts}")
                has_timeline = any("Timeline" in t for t in tab_texts)
                has_latency = any("Latency" in t for t in tab_texts)
                if has_timeline and has_latency:
                    results.append(("Timeline/Latency 标签", True, str(tab_texts)))
                else:
                    results.append(("Timeline/Latency 标签", False, str(tab_texts)))

                # 6. 消息统计徽章（消息数 / 用户 / AI / tokens）
                print("6. 验证消息统计徽章...")
                badges = modal.locator(".badge")
                badge_texts = [
                    (await badges.nth(i).text_content() or "").strip()
                    for i in range(min(8, await badges.count()))
                ]
                print(f"   徽章: {badge_texts}")
                stats_ok = any("Messages" in b for b in badge_texts) and any(
                    "User" in b or "Assistant" in b for b in badge_texts
                )
                if stats_ok:
                    results.append(("消息统计徽章", True, str(badge_texts[:4])))
                else:
                    results.append(("消息统计徽章", False, str(badge_texts[:4])))

                # 7. 卡片式消息项（含角色徽章与 #序号）
                print("7. 验证卡片式消息项...")
                message_items = modal.locator(".message-item")
                item_count = await message_items.count()
                print(f"   消息卡片数: {item_count}")
                if item_count > 0:
                    role_badges = modal.locator(".message-item .badge")
                    role_texts = [
                        (await role_badges.nth(i).text_content() or "").strip()
                        for i in range(min(6, await role_badges.count()))
                    ]
                    print(f"   角色徽章: {role_texts}")
                    numbered = any(t.startswith("#") for t in role_texts)
                    roles_ok = any(t in ("user", "assistant", "tool") for t in role_texts)
                    if roles_ok and numbered:
                        results.append(
                            ("卡片式消息项", True, f"{item_count} 张卡片, {role_texts[:4]}")
                        )
                    else:
                        results.append(
                            ("卡片式消息项", False, f"roles={role_texts[:4]}, numbered={numbered}")
                        )
                else:
                    results.append(("卡片式消息项", False, "无消息卡片"))

                # 8. 切换到 Latency 标签（延迟统计与表格）
                print("8. 切换到 Latency Curve 标签...")
                await modal.locator(".nav-tabs .nav-link", has_text="Latency").click()
                await asyncio.sleep(1.5)
                latency_cards = modal.locator(".card.bg-light strong")
                latency_tables = modal.locator("table")
                latency_count = await latency_cards.count()
                latency_table_count = await latency_tables.count()
                print(f"   延迟统计卡片: {latency_count}, 表格: {latency_table_count}")
                if latency_count > 0:
                    results.append(("Latency 标签渲染", True, f"{latency_count} 个统计卡片"))
                else:
                    empty_states = modal.locator(".empty-state")
                    if await empty_states.count() > 0:
                        results.append(("Latency 标签渲染", False, "显示空状态"))
                    else:
                        results.append(("Latency 标签渲染", False, "无统计卡片"))

                # 最终截图
                await page.screenshot(
                    path=f"{SCREENSHOT_DIR}/issue32_latency_tab_{timestamp}.png", full_page=True
                )
                print("   ✓ 详细截图已保存")

        except PlaywrightError as e:
            print(f"   ✗ 测试出错: {e.__class__.__name__}: {e}")
            results.append(("测试执行", False, f"{e.__class__.__name__}: {e}"))
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue32_error_{timestamp}.png", full_page=True
            )

        finally:
            await browser.close()
            _cleanup_seeded_conversation()

        # 打印测试报告
        print("\n" + "=" * 60)
        print("Issue #32 测试报告")
        print("=" * 60)
        passed = sum(1 for r in results if r[1])
        failed = len(results) - passed
        print(f"测试用例: {len(results)} 个")
        print(f"通过: {passed} 个")
        print(f"失败: {failed} 个")
        print("-" * 60)

        for name, success, error in results:
            status = "✓ 通过" if success else f"✗ 失败 ({error})"
            print(f"  {name}: {status}")

        print("=" * 60)

        assert failed == 0, (
            f"{failed} issue-32 check(s) failed: " f"{[r for r in results if not r[1]]}"
        )
        return failed == 0
