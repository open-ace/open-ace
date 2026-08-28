"""
测试 Issue #38: Conversation History 点击 Page Size 后内容清空

问题描述:
- Conversation History 页面点击 Page Size（每页显示数量）下拉框后，表格内容消失，显示 "No sessions found"

#2491 R3a realignment: the baselined failure set
``#analysis-start-date`` (no longer an input — dates are ``DatePicker``
buttons, ``frontend/src/components/common/DatePicker.tsx``) and selected
``.tabulator-page-size`` (Tabulator is gone). The current table
(``frontend/src/components/features/ConversationHistory.tsx``) has a FIXED
page size of 20 (``ITEMS_PER_PAGE``, line 41) with a ``Pagination`` component
rendered when there is more than one page (lines 557-565). The product intent
of issue #38 — changing the paging parameters must not clear the table — is
re-targeted to that shape: seed >20 conversations, verify the pager appears
with 20 rows on page 1, navigate to page 2, and verify rows are still shown
(no empty state). The old page-size selector is asserted absent, documenting
its removal.

预期行为:
- 分页后，表格应继续显示数据，不应清空
"""

import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(38)]


# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"

SEED_MARKER = "e2e-issue38"
PAGE_SIZE = 20  # ITEMS_PER_PAGE in ConversationHistory.tsx


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


def _seed_conversations(count=25):
    """Seed more than one page of conversations (lane DB starts empty)."""
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            for i in range(1, count + 1):
                conv_id = f"{SEED_MARKER}-conv-{i:03d}"
                existing = conn.execute(
                    "SELECT COUNT(*) FROM daily_messages WHERE conversation_id = ?",
                    (conv_id,),
                ).fetchone()[0]
                if existing:
                    continue
                ts = (datetime.now() - timedelta(minutes=i)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO daily_messages "
                    "(date, tool_name, host_name, role, content, tokens_used, "
                    "input_tokens, output_tokens, timestamp, sender_name, "
                    "message_id, agent_session_id, conversation_id, tenant_id) "
                    "VALUES (?, 'claude-code', ?, 'user', 'issue38 probe', 10, 5, 5, "
                    "?, ?, ?, ?, ?, 1)",
                    (
                        today,
                        f"{SEED_MARKER}-host-{i:02d}",
                        ts,
                        f"{SEED_MARKER}-sender-{i:02d}",
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
    db_path = os.path.expanduser("~/.open-ace/ace.db")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            conn.execute(
                f"DELETE FROM daily_messages WHERE conversation_id LIKE '{SEED_MARKER}-%' "
                f"OR host_name LIKE '{SEED_MARKER}-%'"
            )
            conn.execute(
                f"DELETE FROM daily_stats WHERE host_name LIKE '{SEED_MARKER}-%' "
                f"OR sender_name LIKE '{SEED_MARKER}-%'"
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


@pytest.mark.asyncio
async def test_issue38():
    """测试 Conversation History 分页功能（固定每页 20 条 + Pagination 组件）"""

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

            # 2. 导航到 Conversation History 页面
            print("2. 导航到 Conversation History 页面...")
            await page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            await page.wait_for_selector(".conversation-history", timeout=15000)
            # The loading skeleton is also a table with rows; wait for it to
            # detach so the row count reflects real data.
            await page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            print("   ✓ 已进入 Conversation History 页面")
            results.append(("导航到 Conversation History", True, ""))

            # 3. 截图初始状态
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_01_initial_{timestamp}.png", full_page=True
            )

            # 4. 验证第 1 页数据：固定每页 20 条
            print("3. 验证第 1 页行数（固定 page size = 20）...")
            initial_rows = await page.locator(".conversation-history tbody tr").count()
            print(f"   第 1 页行数: {initial_rows}")
            if initial_rows == PAGE_SIZE:
                results.append(("第 1 页固定 20 行", True, f"{initial_rows} 行"))
            else:
                results.append(("第 1 页固定 20 行", False, f"实际 {initial_rows} 行"))

            # 5. 分页控件存在（Pagination 组件替代了旧的 Page Size 选择器）
            print("4. 验证分页控件...")
            pagination = page.locator(".conversation-history .pagination")
            page_links = pagination.locator(".page-link")
            link_count = await page_links.count()
            print(f"   分页链接数: {link_count}")
            if link_count >= 4:  # Previous, 1, 2, Next
                results.append(("分页控件存在", True, f"{link_count} 个链接"))
            else:
                results.append(("分页控件存在", False, f"仅 {link_count} 个链接"))

            # 旧的 Page Size 选择器已被移除（固定 20 条/页）
            page_size_selector = await page.locator(".tabulator-page-size").count()
            if page_size_selector == 0:
                results.append(("旧 Page Size 选择器已移除", True, ""))
            else:
                results.append(("旧 Page Size 选择器已移除", False, "仍然存在"))

            # 6. 点击第 2 页，验证数据不清空
            print("5. 点击第 2 页验证数据保留...")
            await pagination.locator(".page-link", has_text="2").first.click()
            await asyncio.sleep(2)

            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_02_page2_{timestamp}.png", full_page=True
            )

            empty_visible = await page.locator(".conversation-history .empty-state").count()
            if empty_visible > 0:
                print("   ✗ 失败: 第 2 页显示空状态")
                results.append(("第 2 页数据保留", False, "数据消失"))
            else:
                page2_rows = await page.locator(".conversation-history tbody tr").count()
                print(f"   ✓ 第 2 页行数: {page2_rows}")
                if page2_rows > 0:
                    # Current Pagination renders both the active page number
                    # and a "N / M" current-page indicator with .active — read
                    # the indicator for the assertion detail.
                    active = await pagination.locator(
                        ".active .page-link", has_text=re.compile(r"^\s*\d+\s*/")
                    ).inner_text()
                    results.append(
                        ("第 2 页数据保留", True, f"{page2_rows} 行, active={active.strip()}")
                    )
                else:
                    results.append(("第 2 页数据保留", False, "行数为 0"))

            # 7. 返回第 1 页，验证数据仍然存在
            print("6. 返回第 1 页验证数据保留...")
            await pagination.locator(".page-link", has_text="1").first.click()
            await asyncio.sleep(2)

            empty_back = await page.locator(".conversation-history .empty-state").count()
            if empty_back > 0:
                results.append(("返回第 1 页数据保留", False, "数据消失"))
            else:
                back_rows = await page.locator(".conversation-history tbody tr").count()
                print(f"   ✓ 第 1 页行数: {back_rows}")
                results.append(("返回第 1 页数据保留", True, f"{back_rows} 行"))

            print("\n   ✓ Issue #38 测试通过！分页功能正常工作")

        except PlaywrightError as e:
            print(f"   ✗ 测试出错: {e.__class__.__name__}: {e}")
            results.append(("测试执行", False, f"{e.__class__.__name__}: {e}"))
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue38_error_{timestamp}.png", full_page=True
            )

        finally:
            await browser.close()
            _cleanup_seeded_conversations()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("Issue #38 测试报告: Conversation History 分页功能")
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

    assert failed == 0, f"{failed} issue-38 check(s) failed: " f"{[r for r in results if not r[1]]}"
    return failed == 0
