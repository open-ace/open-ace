"""
测试 Issue #29: 去掉 conversation history 表格的 conversation id 一列

#2491 R3a realignment: the baselined failure evaluated
``document.getElementById('analysis-start-date').value`` on a page that no
longer has those inputs. Two premise changes drive the repair:

1. Navigation: Conversation History is a standalone manage route
   (``/manage/analysis/conversation-history``, ``frontend/src/App.tsx`` line
   434) rendered by ``frontend/src/components/features/ConversationHistory.tsx``
   with date pickers implemented as ``DatePicker`` buttons
   (``frontend/src/components/common/DatePicker.tsx`` — a styled ``<button>``
   inside ``.open-ace-datepicker``, not ``input#analysis-start-date``).

2. The issue-#29 premise was later deliberately reversed: the table again
   shows the conversation id as a first-class "Session ID" column
   (ConversationHistory.tsx line 145, ``conversation_id`` labeled ``sessionId``
   → "Session ID"), rendered truncated (first 8 chars + "...") with a copy
   button (lines 641-659) and toggleable via the Columns dropdown
   (``bi-columns-gap``, lines 497-506). tests/e2e/manage/test_session_id_column.py
   pins that contract too. This test now asserts the CURRENT shape: the
   Session ID column exists, is truncated with a copy affordance, and its
   visibility is user-controllable through the column selector.

The lane DB starts empty, so daily_messages rows are seeded (same pattern as
test_conversation_history_fullscreen.py).
"""

import asyncio
import os
import sqlite3
from datetime import datetime

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(29)]


# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"


SEED_MARKER = "e2e-issue29"


def _seed_conversations(count=3):
    """Idempotently seed daily_messages rows for today (lane DB starts empty)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            now = datetime.now().isoformat(timespec="seconds")
            for i in range(1, count + 1):
                conv_id = f"{SEED_MARKER}-conv-{i:03d}"
                existing = conn.execute(
                    "SELECT COUNT(*) FROM daily_messages WHERE conversation_id = ?",
                    (conv_id,),
                ).fetchone()[0]
                if existing:
                    continue
                conn.execute(
                    "INSERT INTO daily_messages "
                    "(date, tool_name, host_name, role, content, tokens_used, "
                    "input_tokens, output_tokens, timestamp, sender_name, "
                    "message_id, agent_session_id, conversation_id, tenant_id) "
                    "VALUES (?, 'claude-code', ?, 'user', 'issue29 probe', 10, 5, 5, "
                    "?, ?, ?, ?, ?, 1)",
                    (
                        today,
                        f"{SEED_MARKER}-host",
                        now,
                        f"{SEED_MARKER}-sender",
                        f"{SEED_MARKER}-msg-{i:03d}",
                        conv_id,
                        conv_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _cleanup_seeded_conversations():
    """Remove seeded rows (daily_messages + daily_stats aggregates)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.execute(
                f"DELETE FROM daily_messages WHERE host_name = '{SEED_MARKER}-host' "
                f"OR conversation_id LIKE '{SEED_MARKER}-%'"
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


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_issue29():
    """Session ID column: 当前契约 —— 存在、截断显示、可在列选择器中开关"""

    # 确保截图目录存在
    _skip_if_no_server()
    _seed_conversations()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        results = []

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

            # 2. 导航到 Conversation History 管理路由
            print("2. 导航到 Conversation History 页面...")
            await page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            await page.wait_for_selector(".conversation-history", timeout=15000)
            # The loading skeleton is also a table with rows; wait for it to
            # detach so only real data rows are inspected.
            await page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            await asyncio.sleep(1)
            print("   ✓ 已进入 Conversation History 页面")
            results.append(("导航到 Conversation History", True, ""))

            # 3. 截图
            screenshot_path = f"{SCREENSHOT_DIR}/issue29_conversation_history_{timestamp}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"   ✓ 截图已保存: {screenshot_path}")

            # 4. 检查表格列头：Session ID 是第一列（issue #29 的前提已被产品反转）
            print("3. 检查表格列头...")
            headers = page.locator(".conversation-history thead th")
            header_texts = [
                (await headers.nth(i).text_content() or "").strip()
                for i in range(await headers.count())
            ]
            print(f"   表格列头: {header_texts}")

            if header_texts and header_texts[0] == "Session ID":
                results.append(("Session ID 列存在且为首列", True, str(header_texts[:3])))
            else:
                results.append(
                    ("Session ID 列存在且为首列", False, f"实际首列: {header_texts[:1]}")
                )

            # 5. Session ID 单元格截断显示（前 8 位 + ...）并带复制按钮
            print("4. 检查 Session ID 截断显示与复制按钮...")
            first_row = page.locator(".conversation-history tbody tr").first
            first_cell = (await first_row.locator("td").first.text_content() or "").strip()
            copy_btn = first_row.locator("button:has(i.bi-clipboard)")

            truncated_ok = first_cell.startswith(SEED_MARKER[:8]) and first_cell.endswith("...")
            copy_ok = await copy_btn.count() > 0

            if truncated_ok and copy_ok:
                results.append(("Session ID 截断 + 复制按钮", True, first_cell))
            else:
                results.append(
                    (
                        "Session ID 截断 + 复制按钮",
                        False,
                        f"cell={first_cell!r}, copy_btn={copy_ok}",
                    )
                )

            # 6. 列选择器可以关闭/恢复 Session ID 列（当前产品契约）
            print("5. 通过列选择器切换 Session ID 列...")
            columns_btn = page.locator(".conversation-history button:has(i.bi-columns-gap)")
            await columns_btn.click()
            await asyncio.sleep(0.5)

            dropdown_labels = page.locator(".dropdown-menu.show .form-check-label")
            label_texts = [
                (await dropdown_labels.nth(i).text_content() or "").strip()
                for i in range(await dropdown_labels.count())
            ]
            print(f"   列选择器选项: {label_texts}")
            has_session_id_option = "Session ID" in label_texts
            results.append(
                (
                    "列选择器包含 Session ID 选项",
                    has_session_id_option,
                    str(label_texts[:3]),
                )
            )

            if has_session_id_option:
                # 关闭第一项（Session ID）
                await page.locator(".dropdown-menu.show .form-check-input").first.click()
                await asyncio.sleep(0.8)
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)

                headers_after = page.locator(".conversation-history thead th")
                header_texts_after = [
                    (await headers_after.nth(i).text_content() or "").strip()
                    for i in range(await headers_after.count())
                ]
                print(f"   关闭后列头: {header_texts_after}")

                if header_texts_after and header_texts_after[0] != "Session ID":
                    results.append(("关闭后 Session ID 列消失", True, str(header_texts_after[:2])))

                    # 恢复 Session ID 列
                    await columns_btn.click()
                    await asyncio.sleep(0.5)
                    await page.locator(".dropdown-menu.show .form-check-input").first.click()
                    await asyncio.sleep(0.8)
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)

                    headers_restored = page.locator(".conversation-history thead th")
                    header_texts_restored = [
                        (await headers_restored.nth(i).text_content() or "").strip()
                        for i in range(await headers_restored.count())
                    ]
                    restored = bool(header_texts_restored) and (
                        header_texts_restored[0] == "Session ID"
                    )
                    results.append(
                        ("重新打开后 Session ID 列恢复", restored, str(header_texts_restored[:2]))
                    )
                else:
                    results.append(("关闭后 Session ID 列消失", False, str(header_texts_after[:2])))

            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue29_final_{timestamp}.png", full_page=True
            )

        except PlaywrightError as e:
            print(f"   ✗ 测试出错: {e.__class__.__name__}: {e}")
            results.append(("测试执行", False, f"{e.__class__.__name__}: {e}"))
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue29_error_{timestamp}.png", full_page=True
            )

        finally:
            await browser.close()
            _cleanup_seeded_conversations()

        # 打印测试报告
        print("\n" + "=" * 60)
        print("Issue #29 测试报告")
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
            f"{failed} issue-29 check(s) failed: " f"{[r for r in results if not r[1]]}"
        )
        return failed == 0
