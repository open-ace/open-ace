#!/usr/bin/env python3
"""
全面 UI 功能测试 - 逐页面、逐功能点检查
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "screenshots", "audit"
)

issues = []


def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, timeout=10000)
    except:
        pass
    return path


def add_issue(component, description, severity="medium"):
    issues.append({"component": component, "description": description, "severity": severity})
    print(f"  [ISSUE-{severity.upper()}] {component}: {description}")


def safe_click(page, selector, timeout=5000):
    try:
        page.click(selector, timeout=timeout)
        return True
    except:
        return False


def wait_and_check(page, selector, timeout=8000):
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout)
        return True
    except:
        return False


def safe_goto(page, url, timeout=60000):
    """Navigate to URL with error handling."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return True
    except Exception as e:
        add_issue(url.split("/")[-1], f"页面加载超时: {str(e)[:80]}", severity="high")
        return False


def run_all_tests():
    print("=" * 80)
    print("全面 UI 功能审计测试")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Console errors
        console_errors = []
        page.on(
            "console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None
        )

        # Network errors
        network_errors = []
        page.on(
            "response",
            lambda resp: (
                network_errors.append(f"{resp.status} {resp.url}") if resp.status >= 400 else None
            ),
        )

        # Login
        print("\n[1/17] 登录")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)
        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            print("  OK - 登录成功")
        except:
            add_issue("登录", "登录失败", "high")
            browser.close()
            return issues

        # ============================================================
        print("\n[2/17] Dashboard - 仪表盘总览")
        if safe_goto(page, f"{BASE_URL}/manage/dashboard"):
            page.wait_for_timeout(5000)
            screenshot(page, "01_dashboard")

            # Check summary data
            api_data = page.evaluate("""async () => {
                try { const resp = await fetch('/api/summary'); return resp.json(); } catch(e) { return null; }
            }""")
            if api_data:
                for tool, data in api_data.items():
                    first = data.get("first_date", "")
                    last = data.get("last_date", "")
                    print(
                        f"    {tool}: {first} ~ {last}, total_tokens={data.get('total_tokens',0)}"
                    )
                    if tool == "claude" and last < "2026-04-01":
                        add_issue(f"Dashboard-汇总数据-{tool}", f"数据过期: 最后日期是 {last}")

            # Check today data
            today_data = page.evaluate("""async () => {
                try { const resp = await fetch('/api/today'); return resp.json(); } catch(e) { return null; }
            }""")
            if today_data and isinstance(today_data, list):
                for item in today_data:
                    tool = item.get("tool_name", "?")
                    tokens = item.get("tokens_used", 0)
                    reqs = item.get("request_count", 0)
                    print(f"    今日 {tool}: {tokens:,} tokens, {reqs} requests")
                    if tokens == 0:
                        add_issue(f"Dashboard-今日数据-{tool}", "今日 tokens=0")

            if not wait_and_check(page, "canvas", timeout=5000):
                add_issue("Dashboard-图表", "趋势图表未渲染")

        # ============================================================
        print("\n[3/17] Trend Analysis - 趋势分析")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/trend"):
            page.wait_for_timeout(4000)
            screenshot(page, "02_trend")
            if not wait_and_check(page, "canvas", timeout=8000):
                add_issue("趋势分析-图表", "图表未渲染")

        # ============================================================
        print("\n[4/17] Request Dashboard - 请求看板")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/request-dashboard"):
            page.wait_for_timeout(4000)
            screenshot(page, "03_request_dashboard")

        # ============================================================
        print("\n[5/17] Anomaly Detection - 异常检测")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/anomaly"):
            page.wait_for_timeout(4000)
            screenshot(page, "04_anomaly")

        # ============================================================
        print("\n[6/17] ROI Analysis - ROI分析")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/roi"):
            page.wait_for_timeout(8000)
            screenshot(page, "05_roi")

        # ============================================================
        print("\n[7/17] Conversation History - 对话历史")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/conversation-history"):
            page.wait_for_timeout(5000)
            screenshot(page, "06_conversation_history")

            start = time.time()
            has_data = wait_and_check(
                page, "table tbody tr, .card, .conversation-item, .list-group-item", timeout=10000
            )
            elapsed = time.time() - start
            if elapsed > 8:
                add_issue("对话历史-加载速度", f"加载耗时 {elapsed:.1f}s，过慢")
            if not has_data:
                add_issue("对话历史-数据", "页面无数据显示")

        # ============================================================
        print("\n[8/17] Messages - 消息页面")
        if safe_goto(page, f"{BASE_URL}/manage/analysis/messages"):
            page.wait_for_timeout(4000)
            screenshot(page, "07_messages_before_filter")

            # Test sender filter exists
            sender_input = page.locator(
                "input[placeholder*='Search'], input[placeholder*='搜索'], input[placeholder*='Sender']"
            )
            filter_controls = page.locator(
                "[class*='filter'], [class*='search'], input[type='text'], select"
            )
            if filter_controls.count() > 0:
                print(f"  找到 {filter_controls.count()} 个筛选控件")
            else:
                add_issue("消息-筛选功能", "未找到筛选控件")

        # ============================================================
        print("\n[9/17] Audit Center - 审计中心")
        if safe_goto(page, f"{BASE_URL}/manage/audit"):
            page.wait_for_timeout(4000)
            screenshot(page, "08_audit_logs")

            # Check audit log table
            rows = page.locator("table tbody tr")
            print(f"  审计日志行数: {rows.count()}")
            if rows.count() == 0:
                add_issue("审计中心-日志", "审计日志表格无数据")

            # Test detail button
            detail_btns = page.locator(".audit-detail-btn")
            if detail_btns.count() > 0:
                print("  测试详情按钮...")
                try:
                    detail_btns.first.click(timeout=5000)
                    page.wait_for_timeout(1000)
                    modal = page.locator(".modal.show")
                    if modal.count() > 0:
                        print("    OK - 详情弹窗显示")
                        # Force close modal
                        close_btn = page.locator(".modal .btn-close")
                        if close_btn.count() > 0:
                            close_btn.first.click(force=True)
                        else:
                            page.evaluate(
                                "document.querySelector('.modal.show')?.remove(); document.querySelector('.modal-backdrop')?.remove()"
                            )
                        page.wait_for_timeout(1000)
                    else:
                        add_issue("审计中心-详情按钮", "点击详情按钮后未弹出详情弹窗")
                except Exception as e:
                    add_issue("审计中心-详情按钮", f"详情按钮操作失败: {str(e)[:80]}")
            else:
                print("  未找到详情按钮")

            # Switch to Analysis tab
            analysis_tab = page.locator(
                ".audit-center .nav-link:has-text('分析'), .audit-center .nav-link:has-text('Analysis')"
            )
            if analysis_tab.count() > 0:
                print("  切换到分析Tab...")
                analysis_tab.first.click()
                page.wait_for_timeout(5000)
                screenshot(page, "08_audit_analysis")

                # Check security score
                score_elem = page.locator(".security-score-circle, .display-1.fw-bold")
                if score_elem.count() > 0:
                    score_text = score_elem.first.text_content()
                    print(f"  安全评分: {score_text[:50] if score_text else 'N/A'}")
                else:
                    add_issue("审计中心-分析", "安全评分未显示")

        # ============================================================
        print("\n[10/17] Quota & Alerts - 配额与告警")
        if safe_goto(page, f"{BASE_URL}/manage/quota"):
            page.wait_for_timeout(4000)
            screenshot(page, "09_quota_alerts")

            # Switch to alerts tab
            alerts_tab = page.locator("button:has-text('告警'), button:has-text('Alerts')")
            if alerts_tab.count() > 0:
                alerts_tab.first.click()
                page.wait_for_timeout(3000)
                screenshot(page, "09_alerts_tab")

        # ============================================================
        print("\n[11/17] Compliance - 合规管理")
        if safe_goto(page, f"{BASE_URL}/manage/compliance"):
            page.wait_for_timeout(4000)
            screenshot(page, "10_compliance")

            # Switch to retention tab
            retention_tab = page.locator("button:has-text('保留'), button:has-text('Retention')")
            if retention_tab.count() > 0:
                retention_tab.first.click()
                page.wait_for_timeout(3000)
                screenshot(page, "10_compliance_retention")

        # ============================================================
        print("\n[12/17] Security Center - 安全中心")
        if safe_goto(page, f"{BASE_URL}/manage/security"):
            page.wait_for_timeout(4000)
            screenshot(page, "11_security")

            filter_rows = page.locator("table tbody tr")
            print(f"  过滤规则行数: {filter_rows.count()}")
            if filter_rows.count() == 0:
                add_issue("安全中心-过滤规则", "过滤规则表格无数据")

            # Test create filter rule button
            create_btn = page.locator(
                "button:has-text('添加'), button:has-text('创建'), button:has-text('Create'), button:has-text('Add')"
            )
            if create_btn.count() > 0:
                print("  测试创建过滤规则...")
                try:
                    create_btn.first.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    screenshot(page, "11_security_create_modal")
                    modal = page.locator(".modal.show, .modal-dialog")
                    if modal.count() > 0 and modal.is_visible():
                        print("    OK - 创建规则弹窗显示")
                        safe_click(
                            page,
                            ".modal .btn-close, .modal button:has-text('取消'), .modal button:has-text('Cancel')",
                        )
                        page.wait_for_timeout(1000)
                    else:
                        add_issue("安全中心-创建规则", "点击添加按钮后未弹出创建弹窗")
                except:
                    pass

        # ============================================================
        print("\n[13/17] User Management - 用户管理")
        if safe_goto(page, f"{BASE_URL}/manage/users"):
            page.wait_for_timeout(4000)
            screenshot(page, "12_users")

            user_rows = page.locator("table tbody tr")
            print(f"  用户行数: {user_rows.count()}")
            if user_rows.count() == 0:
                add_issue("用户管理", "用户列表无数据")

        # ============================================================
        print("\n[14/17] Tenant Management - 租户管理")
        if safe_goto(page, f"{BASE_URL}/manage/tenants"):
            page.wait_for_timeout(4000)
            screenshot(page, "13_tenants")

        # ============================================================
        print("\n[15/17] Project Management - 项目管理")
        if safe_goto(page, f"{BASE_URL}/manage/projects"):
            page.wait_for_timeout(4000)
            screenshot(page, "14_projects")

            project_rows = page.locator("table tbody tr, .card, .project-card")
            print(f"  项目元素: {project_rows.count()}")

        # ============================================================
        print("\n[16/17] Remote Machines - 远程机器管理")
        if safe_goto(page, f"{BASE_URL}/manage/remote/machines"):
            page.wait_for_timeout(4000)
            screenshot(page, "15_remote_machines")

        # ============================================================
        print("\n[17/17] SSO Settings - SSO设置")
        if safe_goto(page, f"{BASE_URL}/manage/settings/sso"):
            page.wait_for_timeout(4000)
            screenshot(page, "16_sso")

        # ============================================================
        # Print all issues
        print("\n" + "=" * 80)
        print(f"发现 {len(issues)} 个问题")
        print("=" * 80)

        # Print network errors
        if network_errors:
            print(f"\n--- 网络错误 ({len(network_errors)}) ---")
            for err in network_errors[:10]:
                print(f"  {err}")

        # Print console errors
        if console_errors:
            print(f"\n--- 控制台错误 ({len(console_errors)}) ---")
            for err in console_errors[:5]:
                print(f"  {err[:100]}")

        # Group by severity
        for sev in ["high", "medium", "low"]:
            sev_issues = [i for i in issues if i["severity"] == sev]
            if sev_issues:
                print(f"\n--- {sev.upper()} ---")
                for idx, issue in enumerate(sev_issues):
                    print(f"  {idx+1}. [{issue['component']}] {issue['description']}")

        browser.close()

    return issues


if __name__ == "__main__":
    results = run_all_tests()
    print(f"\nTotal issues: {len(results)}")
