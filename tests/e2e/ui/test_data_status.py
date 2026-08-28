"""
UI 测试: Issue 68 - 远程机器状态检查功能

#2491 realignment: the sidebar "Data Status" panel this test originally
asserted (``#data-status-container`` / ``.refresh-btn`` / ``.data-status-item``)
was removed from the product (commit 360fc592 "feat: remove Data Status panel
from sidebar"; the ``dataStatus`` i18n key is orphaned). The remote-host
status surface now lives on the Remote Machines page —
``frontend/src/components/features/management/RemoteMachineManagement.tsx``
served at ``/manage/remote/machines`` (App.tsx route "remote/machines"):
total/online/offline machine stat cards (RemoteMachineManagement.tsx lines
424-452), a machine table whose rows carry online/offline status badges
(lines 477-505), or the registered-machines empty state on a fresh lane
(lines 454-457), plus per-machine Active-Sessions refresh buttons. The test
asserts that current contract and that the page renders without page errors.

测试目标:
1. 验证 Remote Machines 页面（远程机器状态面板）显示正确
2. 验证机器统计卡（总数/在线/离线）存在
3. 验证机器状态徽章或空状态（未注册机器）正确渲染
4. 验证页面无脚本错误
"""

import os
import sys
import time

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

pytestmark = [pytest.mark.regression, pytest.mark.issue(68)]


# Test configuration
BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "screenshots",
    "issues",
    "68",
)


HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"


def ensure_screenshot_dir():
    """Ensure screenshot directory exists."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def take_screenshot(page, name):
    """Take screenshot and save to issue directory."""
    path = os.path.join(SCREENSHOT_DIR, name)
    await page.screenshot(path=path)
    print(f"  截图保存: {path}")
    return path


def _skip_if_no_server():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5).raise_for_status()
    except (requests.exceptions.RequestException, ConnectionError, OSError):
        pytest.skip(f"test server not reachable at {BASE_URL}")


@pytest.mark.asyncio
async def test_data_status():
    """Test the remote machine status surface (Remote Machines page)."""
    _skip_if_no_server()
    print("\n" + "=" * 60)
    print("UI 测试: Issue 68 - 远程机器状态检查（Remote Machines 页面）")
    print("=" * 60)

    ensure_screenshot_dir()
    screenshots = []
    test_passed = True
    console_errors = []

    async with async_playwright() as p:
        # Launch browser

        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )

        try:
            # Step 1: Login
            print("\n步骤 1: 登录系统")
            await page.goto(f"{BASE_URL}/login")
            # The React login form re-mounts once its SSO/config effects
            # settle, which can detach filled inputs; re-fill until the
            # values persist (same pattern as test_user_scenario).
            for _attempt in range(3):
                await page.wait_for_selector("#username", timeout=10000)
                await page.fill("#username", USERNAME)
                await page.fill("#password", PASSWORD)
                if (
                    await page.input_value("#username") == USERNAME
                    and await page.input_value("#password") == PASSWORD
                ):
                    break
                time.sleep(0.5)
            await page.click('button[type="submit"]')

            # Admins land on /manage after login.
            await page.wait_for_url("**/manage/**", timeout=10000)
            print("  ✓ 登录成功")
            screenshots.append(await take_screenshot(page, "01_login.png"))

            # Step 2: Open the Remote Machines page (current home of the
            # remote-host status surface).
            print("\n步骤 2: 打开 Remote Machines 页面")
            await page.goto(f"{BASE_URL}/manage/remote/machines")
            await page.wait_for_load_state("networkidle")
            time.sleep(3)

            header = page.locator("h2")
            header_count = await header.count()
            assert header_count > 0, "Remote Machines page did not render a header"
            header_text = (await header.first.inner_text()).strip()
            print(f"  ✓ 页面标题: {header_text}")
            assert (
                header_text
                in (
                    "Remote Machines",
                    "远程机器",
                    "リモートマシン",
                    "リモート・マシン",
                    "원격 머신",
                )
                or "Remote" in header_text
                or "机器" in header_text
            ), f"unexpected Remote Machines page header: {header_text!r}"
            screenshots.append(await take_screenshot(page, "02_machines_page.png"))

            # Step 3: Machine stat cards (total / online / offline) must be
            # rendered — the machine-status summary contract.
            print("\n步骤 3: 检查机器统计卡")
            stat_cards = page.locator(".row.g-3 .card .card-body .h3")
            stat_count = await stat_cards.count()
            assert (
                stat_count >= 3
            ), f"machine stats cards (total/online/offline) missing: found {stat_count}"
            for i in range(stat_count):
                value = (await stat_cards.nth(i).inner_text()).strip()
                print(f"  - 统计值 {i + 1}: {value}")
                assert value.isdigit(), f"machine stat card {i + 1} is not numeric: {value!r}"
            print("  ✓ 机器统计卡（总数/在线/离线）已渲染")

            # Step 4: Machine rows with status badges, or the registered-
            # machines empty state on a fresh lane.
            print("\n步骤 4: 检查机器状态列表")
            time.sleep(2)
            machine_rows = page.locator("table tbody tr")
            row_count = await machine_rows.count()
            if row_count > 0:
                print(f"  发现 {row_count} 台机器")
                badges = page.locator("table tbody tr .badge")
                badge_count = await badges.count()
                assert badge_count > 0, "machine rows must carry status badges"
                for i in range(min(badge_count, 6)):
                    badge_text = (await badges.nth(i).inner_text()).strip()
                    print(f"  - 状态徽章: {badge_text}")
                screenshots.append(await take_screenshot(page, "03_machine_rows.png"))
            else:
                body_text = await page.locator("body").inner_text()
                empty_state = (
                    "No machines registered" in body_text
                    or "暂无已注册的机器" in body_text
                    or "no machines" in body_text.lower()
                )
                assert (
                    empty_state
                ), "no machine rows rendered and no registered-machines empty state shown"
                print("  ✓ 空状态正确显示（未注册机器）")
                screenshots.append(await take_screenshot(page, "03_empty_state.png"))

            # Step 5: Page health — the machine status surface must render
            # without page errors. "Failed to load resource" notices are
            # excluded: on a fresh lane the platform-admin machines query
            # legitimately answers 400 until a tenant is selected
            # (app/routes/remote.py list_machines requires tenant_id for
            # platform admins) and the UI surfaces that via its own state.
            print("\n步骤 5: 检查页面错误")
            js_errors = [e for e in console_errors if "Failed to load resource" not in e]
            for err in console_errors:
                print(f"  - console error: {err}")
            assert not js_errors, f"Remote Machines page emitted JS errors: {js_errors[:5]}"
            print("  ✓ 页面无脚本错误")

            screenshots.append(await take_screenshot(page, "05_final.png"))

        except PlaywrightError as e:
            print(f"\n✗ 测试失败: {e}")
            test_passed = False
            screenshots.append(await take_screenshot(page, "error.png"))

        finally:
            await browser.close()

    # Print test report
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)
    print(f"测试状态: {'通过 ✓' if test_passed else '失败 ✗'}")
    print("\n截图文件:")
    for s in screenshots:
        print(f"  - {s}")
    print("=" * 60)

    assert test_passed, "Data Status panel checks failed (see report above)"
    return test_passed
