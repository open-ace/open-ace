"""
测试 Issue #34: Conversation History 表格点击表头排序后内容消失

问题描述：
- Conversation History 表格点击表头排序后，表格内容消失，显示 "No sessions found"

#2491 R3a realignment: the baselined failure read
``page.input_value("#analysis-start-date")`` — the React page has no such
input (dates are ``DatePicker`` buttons,
``frontend/src/components/common/DatePicker.tsx``) and Conversation History
is now the manage route ``/manage/analysis/conversation-history``
(``frontend/src/App.tsx`` line 434). Sorting in the current table is done by
clicking the ``th`` header of a sortable column
(``frontend/src/components/features/ConversationHistory.tsx`` lines 320-327
and 522-540: ``handleSort`` on header click with a ``bi-arrow-up`` /
``bi-arrow-down`` direction indicator); there is no Tabulator. The repaired
test seeds a few conversations (lane DB starts empty) and asserts the
issue-34 contract against that markup: after sorting (asc, then desc, and
across several columns) the rows remain, the direction indicator appears,
and the order actually reverses.

测试步骤：
1. 登录系统
2. 导航到 Conversation History 页面
3. 等待表格数据加载
4. 点击表头进行排序
5. 验证表格内容仍然存在且顺序正确
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta

import pytest
import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(34)]


BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888").rstrip("/") + "/"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots"

SEED_MARKER = "e2e-issue34"


def print_test_report(results):
    """打印测试报告"""
    print("\n" + "=" * 60)
    print("Issue #34 测试报告")
    print("=" * 60)

    passed = sum(1 for r in results if r[1])
    failed = len(results) - passed

    print(f"测试用例: {len(results)} 个")
    print(f"通过: {passed} 个")
    print(f"失败: {failed} 个")
    print("-" * 60)

    for name, success, message in results:
        status = "✓ 通过" if success else "✗ 失败"
        msg = f" ({message})" if message else ""
        print(f"  {name}: {status}{msg}")

    print("=" * 60)
    return failed == 0


def _seed_conversations(count=6):
    """Seed conversations with distinct senders/timestamps for sort checks."""
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
                    "VALUES (?, 'claude-code', ?, 'user', 'issue34 probe', ?, 5, 5, "
                    "?, ?, ?, ?, ?, 1)",
                    (
                        today,
                        f"{SEED_MARKER}-host-{i:02d}",
                        10 * i,
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


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


async def _sender_cells(page, count):
    cells = []
    for i in range(count):
        row = page.locator(".conversation-history tbody tr").nth(i)
        # Sender is the 5th visible column (Session ID, Date, Tool, Host, Sender)
        cells.append((await row.locator("td").nth(4).text_content() or "").strip())
    return cells


@pytest.mark.asyncio
async def test_issue34():
    """测试 Conversation History 表格排序功能"""
    _skip_if_no_server()
    _seed_conversations()
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
            results.append(("登录", True, ""))
            print("   ✓ 登录成功")

            # 2. 导航到 Conversation History 页面
            print("2. 导航到 Conversation History 页面...")
            await page.goto(f"{BASE_URL}manage/analysis/conversation-history")
            await page.wait_for_selector(".conversation-history", timeout=15000)

            # 3. 等待表格数据加载（骨架屏也是 table 行，需等其消失）
            print("3. 等待表格数据加载...")
            await page.wait_for_selector(".conversation-history table", timeout=15000)
            await page.wait_for_selector(
                ".conversation-history .skeleton", state="detached", timeout=15000
            )
            row_count = await page.locator(".conversation-history tbody tr").count()
            print(f"   ✓ 表格已加载，共 {row_count} 行数据")
            results.append(("表格数据加载", True, f"{row_count} 行"))

            # 截图：排序前的表格
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue34_before_sort_{timestamp}.png")

            # 4. 点击 Sender 表头进行升序排序
            print("4. 点击 Sender 表头进行排序...")
            sender_header = page.locator(".conversation-history thead th", has_text="Sender")
            await sender_header.click()
            await asyncio.sleep(1)

            rows_asc = await page.locator(".conversation-history tbody tr").count()
            icon_asc = await page.locator(".conversation-history thead th .bi-arrow-up").count()
            senders_asc = await _sender_cells(page, min(3, row_count))
            print(f"   升序指示器: {icon_asc}, 前 3 行 Sender: {senders_asc}")

            if rows_asc == row_count and rows_asc > 0 and icon_asc > 0:
                results.append(("升序排序后表格保留", True, f"{rows_asc} 行"))
            else:
                results.append(("升序排序后表格保留", False, f"rows={rows_asc}, icon={icon_asc}"))

            empty_visible = await page.locator(".conversation-history .empty-state").count()
            if empty_visible == 0:
                results.append(("排序后无空状态", True, ""))
            else:
                results.append(("排序后无空状态", False, "出现 No data 空状态"))

            # 5. 验证升序顺序
            asc_sorted = senders_asc == sorted(senders_asc)
            if asc_sorted:
                results.append(("升序顺序正确", True, str(senders_asc)))
            else:
                results.append(("升序顺序正确", False, str(senders_asc)))

            # 截图：排序后的表格
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue34_after_sort_{timestamp}.png")

            # 6. 再次点击 Sender 表头进行反向排序
            print("5. 再次点击 Sender 表头进行反向排序...")
            await sender_header.click()
            await asyncio.sleep(1)

            rows_desc = await page.locator(".conversation-history tbody tr").count()
            icon_desc = await page.locator(".conversation-history thead th .bi-arrow-down").count()
            senders_desc = await _sender_cells(page, min(3, row_count))
            print(f"   降序指示器: {icon_desc}, 前 3 行 Sender: {senders_desc}")

            if rows_desc == row_count and rows_desc > 0 and icon_desc > 0:
                results.append(("反向排序后表格保留", True, f"{rows_desc} 行"))
            else:
                results.append(("反向排序后表格保留", False, f"rows={rows_desc}, icon={icon_desc}"))

            desc_sorted = senders_desc == sorted(senders_desc, reverse=True)
            reversed_ok = senders_desc == list(reversed(senders_asc)) or desc_sorted
            if reversed_ok:
                results.append(("降序顺序正确", True, str(senders_desc)))
            else:
                results.append(("降序顺序正确", False, str(senders_desc)))

            # 截图：反向排序后的表格
            await page.screenshot(
                path=f"{SCREENSHOT_DIR}/issue34_after_reverse_sort_{timestamp}.png"
            )

            # 7. 测试其他列的排序（点击后表格不能清空）
            print("6. 测试其他列的排序...")
            other_headers = ["Session ID", "Date", "Tool", "Host", "Messages", "Tokens"]
            for header_name in other_headers:
                header = page.locator(".conversation-history thead th", has_text=header_name)
                if await header.count() == 0:
                    continue
                await header.first.click()
                await asyncio.sleep(0.8)
                rows = await page.locator(".conversation-history tbody tr").count()
                if rows > 0:
                    print(f"   ✓ {header_name} 列排序正常")
                else:
                    print(f"   ✗ {header_name} 列排序后表格为空")
                    results.append((f"{header_name} 列排序", False, "表格为空"))

            results.append(("其他列排序测试", True, ""))

        except PlaywrightError as e:
            results.append(("测试执行", False, f"{e.__class__.__name__}: {e}"))
            await page.screenshot(path=f"{SCREENSHOT_DIR}/issue34_error_{timestamp}.png")
        finally:
            await browser.close()
            _cleanup_seeded_conversations()

    # 打印报告
    success = print_test_report(results)
    assert success, f"issue-34 sort check(s) failed: " f"{[r for r in results if not r[1]]}"
    return success
