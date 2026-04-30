#!/usr/bin/env python3
"""
全面管理功能测试脚本

测试所有管理页面的：
1. 页面加载和渲染
2. 数据展示正确性
3. API 端点可达性和数据一致性
4. 交互功能完整性
5. 前端-后端路由匹配
"""

import json
import os
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

# 配置
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "screenshots", "comprehensive"
)


def ensure_screenshot_dir():
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def save_screenshot(page, name):
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    return path


class APITester:
    """API 端点测试器"""

    def __init__(self, base_url, cookies):
        self.base_url = base_url
        self.cookies = cookies
        self.results = []

    def test_endpoint(self, name, path, method="GET", expected_status=200, data=None):
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, cookies=self.cookies, timeout=10)
            elif method == "POST":
                resp = requests.post(url, cookies=self.cookies, json=data, timeout=10)
            elif method == "PUT":
                resp = requests.put(url, cookies=self.cookies, json=data, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, cookies=self.cookies, timeout=10)
            else:
                resp = None

            status = resp.status_code if resp else 0

            # Check content type
            content_type = resp.headers.get("Content-Type", "") if resp else ""
            is_json = "application/json" in content_type

            if status == expected_status and is_json:
                try:
                    body = resp.json()
                    has_error = "error" in body if isinstance(body, dict) else False
                    if has_error and status == 200:
                        # API returned 200 but has error in body
                        self.results.append(
                            (name, "WARN", f"API returned error: {body.get('error', 'unknown')}", path)
                        )
                        return body
                    self.results.append((name, "PASS", None, path))
                    return body
                except Exception:
                    self.results.append((name, "PASS", None, path))
                    return resp.text
            elif not is_json and status == 200:
                self.results.append(
                    (name, "FAIL", f"Expected JSON, got HTML (SPA catch-all). Route not registered.", path)
                )
                return None
            else:
                self.results.append(
                    (name, "FAIL", f"Expected {expected_status}, got {status}. Content-Type: {content_type}", path)
                )
                return resp.text if resp else None
        except Exception as e:
            self.results.append((name, "ERROR", str(e), path))
            return None


class UITester:
    """UI 页面测试器"""

    def __init__(self, page, results):
        self.page = page
        self.results = results

    def check_element(self, name, selectors, timeout=5000):
        for selector in selectors:
            try:
                self.page.wait_for_selector(selector, state="attached", timeout=timeout)
                count = self.page.locator(selector).count()
                if count > 0:
                    self.results.append((name, "PASS", None))
                    return True
            except Exception:
                continue
        self.results.append((name, "FAIL", f"None of {selectors} found"))
        return False

    def check_no_error(self, name):
        """Check page doesn't show error state"""
        error_selectors = [".alert-danger", ".error-message", ".error-state"]
        for sel in error_selectors:
            try:
                el = self.page.locator(sel)
                if el.count() > 0 and el.is_visible():
                    text = el.text_content()[:100]
                    self.results.append((name, "FAIL", f"Error visible: {text}"))
                    return False
            except Exception:
                pass
        self.results.append((name, "PASS", None))
        return True


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("Open ACE 管理功能全面测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: {BASE_URL}")
    print("=" * 80)

    all_results = {}

    # ==================== Phase 1: API 端点测试 ====================
    print("\n[Phase 1] API 端点测试")
    print("-" * 60)

    # Login first
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    if login_resp.status_code != 200:
        print(f"LOGIN FAILED: {login_resp.status_code} {login_resp.text}")
        return

    cookies = dict(session.cookies)
    api = APITester(BASE_URL, cookies)

    # --- Dashboard APIs ---
    print("  Testing Dashboard APIs...")
    api.test_endpoint("Dashboard - 今日用量", "/api/today")
    api.test_endpoint("Dashboard - 汇总数据", "/api/summary")
    api.test_endpoint("Dashboard - 趋势数据", "/api/trend?days=7")
    api.test_endpoint("Dashboard - 工具列表", "/api/tools")
    api.test_endpoint("Dashboard - 主机列表", "/api/hosts")

    # --- Request Statistics APIs ---
    print("  Testing Request Statistics APIs...")
    api.test_endpoint("Request - 今日请求", "/api/request/today")
    api.test_endpoint("Request - 请求趋势", "/api/request/trend?days=7")
    api.test_endpoint("Request - 按工具", "/api/request/by-tool?days=7")
    api.test_endpoint("Request - 按用户", "/api/request/by-user?days=7")

    # --- Analysis APIs ---
    print("  Testing Analysis APIs...")
    api.test_endpoint("Analysis - 关键指标", "/api/analysis/key-metrics")
    api.test_endpoint("Analysis - 小时用量", "/api/analysis/hourly-usage")
    api.test_endpoint("Analysis - 异常检测", "/api/analysis/anomaly-detection")
    api.test_endpoint("Analysis - 用户排名", "/api/analysis/user-ranking")
    api.test_endpoint("Analysis - 工具对比", "/api/analysis/tool-comparison")
    api.test_endpoint("Analysis - 对话统计", "/api/analysis/conversation-stats")
    api.test_endpoint("Analysis - 批量数据", "/api/analysis/batch")

    # --- ROI APIs ---
    print("  Testing ROI APIs...")
    api.test_endpoint("ROI - 总览", "/api/roi")
    api.test_endpoint("ROI - 汇总", "/api/roi/summary")
    api.test_endpoint("ROI - 趋势", "/api/roi/trend")
    api.test_endpoint("ROI - 按工具", "/api/roi/by-tool")
    api.test_endpoint("ROI - 按用户", "/api/roi/by-user")
    api.test_endpoint("ROI - 成本分解", "/api/roi/cost-breakdown")
    api.test_endpoint("ROI - 每日成本", "/api/roi/daily-costs")
    api.test_endpoint("ROI - 优化建议", "/api/optimization/suggestions")
    api.test_endpoint("ROI - 效率报告", "/api/optimization/efficiency")

    # --- Governance APIs ---
    print("  Testing Governance APIs...")
    api.test_endpoint("Governance - 审计日志", "/api/audit/logs")
    api.test_endpoint("Governance - 审计导出", "/api/audit/logs/export")
    api.test_endpoint("Governance - 过滤规则", "/api/filter-rules")
    api.test_endpoint("Governance - 安全设置", "/api/security-settings")
    api.test_endpoint("Governance - 配额状态", "/api/quota/status/all")

    # --- Governance APIs - Frontend expected paths ---
    print("  Testing Governance APIs (frontend paths)...")
    api.test_endpoint("Governance - 审计日志 (前端路径)", "/api/governance/audit-logs")

    # --- Compliance APIs (frontend expected paths) ---
    print("  Testing Compliance APIs (frontend expected paths)...")
    api.test_endpoint("Compliance - 报告类型 (前端路径)", "/api/compliance/reports")
    api.test_endpoint("Compliance - 已保存报告 (前端路径)", "/api/compliance/reports/saved")
    api.test_endpoint("Compliance - 审计模式 (前端路径)", "/api/compliance/audit/patterns")
    api.test_endpoint("Compliance - 审计异常 (前端路径)", "/api/compliance/audit/anomalies")
    api.test_endpoint("Compliance - 安全评分 (前端路径)", "/api/compliance/audit/security-score")
    api.test_endpoint("Compliance - 保留规则 (前端路径)", "/api/compliance/retention/rules")
    api.test_endpoint("Compliance - 存储估算 (前端路径)", "/api/compliance/retention/storage")
    api.test_endpoint("Compliance - 保留历史 (前端路径)", "/api/compliance/retention/history")

    # --- Compliance APIs (actual backend paths) ---
    print("  Testing Compliance APIs (actual backend paths)...")
    api.test_endpoint("Compliance - 报告类型 (后端路径)", "/api/reports")
    api.test_endpoint("Compliance - 已保存报告 (后端路径)", "/api/reports/saved")
    api.test_endpoint("Compliance - 审计模式 (后端路径)", "/api/audit/patterns")
    api.test_endpoint("Compliance - 审计异常 (后端路径)", "/api/audit/anomalies")
    api.test_endpoint("Compliance - 安全评分 (后端路径)", "/api/audit/security-score")
    api.test_endpoint("Compliance - 保留规则 (后端路径)", "/api/retention/rules")
    api.test_endpoint("Compliance - 存储估算 (后端路径)", "/api/retention/storage")

    # --- Alerts APIs ---
    print("  Testing Alerts APIs...")
    api.test_endpoint("Alerts - 告警列表", "/api/alerts")
    api.test_endpoint("Alerts - 未读数量", "/api/alerts/unread-count")
    api.test_endpoint("Alerts - 通知偏好", "/api/alerts/preferences")

    # --- User Management APIs ---
    print("  Testing User Management APIs...")
    api.test_endpoint("Users - 用户列表", "/api/admin/users")
    api.test_endpoint("Users - 配额使用", "/api/admin/quota/usage")

    # --- Tenant APIs ---
    print("  Testing Tenant APIs...")
    api.test_endpoint("Tenant - 租户列表 (前端路径)", "/api/tenants")
    api.test_endpoint("Tenant - 租户计划", "/api/tenants/plans")
    api.test_endpoint("Tenant - 租户列表 (后端路径)", "/api/")  # actual registration path

    # --- SSO APIs ---
    print("  Testing SSO APIs...")
    api.test_endpoint("SSO - 提供商列表 (前端路径)", "/api/sso/providers")
    api.test_endpoint("SSO - 提供商列表 (后端路径)", "/api/providers")

    # --- Project APIs ---
    print("  Testing Project APIs...")
    api.test_endpoint("Projects - 项目列表", "/api/projects")
    api.test_endpoint("Projects - 项目统计", "/api/projects/stats")

    # --- Remote APIs ---
    print("  Testing Remote APIs...")
    api.test_endpoint("Remote - 机器列表", "/api/remote/machines")

    # --- Workspace APIs ---
    print("  Testing Workspace APIs...")
    api.test_endpoint("Workspace - 会话列表", "/api/workspace/sessions?page=1&limit=5")
    api.test_endpoint("Workspace - 工具列表", "/api/workspace/tools")
    api.test_endpoint("Workspace - 工作区状态", "/api/workspace/status")

    # --- Quota APIs ---
    print("  Testing Quota APIs...")
    api.test_endpoint("Quota - 检查", "/api/quota/check")
    api.test_endpoint("Quota - 状态", "/api/quota/status")
    api.test_endpoint("Quota - 使用详情", "/api/quota/usage/me")

    # --- Analytics APIs ---
    print("  Testing Analytics APIs...")
    api.test_endpoint("Analytics - 报告", "/api/analytics/report?days=30")
    api.test_endpoint("Analytics - 预测", "/api/analytics/forecast?days=7")
    api.test_endpoint("Analytics - 效率", "/api/analytics/efficiency?days=30")

    # --- Messages APIs ---
    print("  Testing Messages APIs...")
    api.test_endpoint("Messages - 消息列表", "/api/messages")
    api.test_endpoint("Messages - 消息统计", "/api/messages/count")
    api.test_endpoint("Messages - 发送者列表", "/api/senders")

    all_results["api"] = api.results

    # ==================== Phase 2: UI 页面测试 ====================
    print("\n[Phase 2] UI 页面测试")
    print("-" * 60)

    ui_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Collect console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Collect network errors
        network_errors = []
        page.on("response", lambda resp: network_errors.append(f"{resp.status} {resp.url}") if resp.status >= 400 else None)

        # Login
        print("  Logging in...")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10000)
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)
        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception:
            pass

        ui = UITester(page, ui_results)

        pages_to_test = [
            # (名称, URL路径, 关键元素选择器)
            ("Dashboard", "/manage/dashboard", [".card", "canvas", ".stat-card"]),
            ("Trend Analysis", "/manage/analysis/trend", ["canvas", ".card", "h1, h2, h3"]),
            ("Request Dashboard", "/manage/analysis/request-dashboard", [".card", "canvas", "table"]),
            ("Anomaly Detection", "/manage/analysis/anomaly", ["canvas", ".card", ".anomaly"]),
            ("ROI Analysis", "/manage/analysis/roi", [".card", "canvas", "table"]),
            ("Conversation History", "/manage/analysis/conversation-history", [".card", "canvas", "table"]),
            ("Messages", "/manage/analysis/messages", [".card", "table", ".message"]),
            ("Audit Center", "/manage/audit", [".card", "table", ".nav-tabs"]),
            ("Quota & Alerts", "/manage/quota", [".card", ".nav-tabs", "table"]),
            ("Compliance", "/manage/compliance", [".card", ".nav-tabs", "table"]),
            ("Security Center", "/manage/security", [".card", ".nav-tabs", "table"]),
            ("User Management", "/manage/users", [".card", "table"]),
            ("Tenant Management", "/manage/tenants", [".card", "table"]),
            ("Project Management", "/manage/projects", [".card", "table"]),
            ("Remote Machines", "/manage/remote/machines", [".card", "table"]),
            ("API Keys", "/manage/remote/api-keys", [".card", "table"]),
            ("SSO Settings", "/manage/settings/sso", [".card", "form", ".sso"]),
        ]

        for page_name, page_path, selectors in pages_to_test:
            print(f"  Testing {page_name}...")
            console_errors.clear()
            network_errors.clear()

            try:
                page.goto(f"{BASE_URL}{page_path}", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Wait for main content
                try:
                    page.wait_for_selector("main, .manage-content, h1, h2, h3, .card", timeout=10000)
                except Exception:
                    pass

                # Screenshot
                safe_name = page_name.lower().replace(" ", "_").replace("&", "and")
                save_screenshot(page, f"manage_{safe_name}")

                # Check page loaded
                ui.check_element(f"{page_name} - 页面加载", selectors)

                # Check no error visible
                ui.check_no_error(f"{page_name} - 无错误显示")

                # Log console errors
                if console_errors:
                    for err in console_errors[:3]:
                        ui_results.append((f"{page_name} - 控制台错误", "WARN", err[:200]))

                # Log network errors
                api_errors = [e for e in network_errors if "/api/" in e]
                if api_errors:
                    for err in api_errors[:5]:
                        ui_results.append((f"{page_name} - API错误", "WARN", err[:200]))

            except Exception as e:
                ui_results.append((f"{page_name} - 页面加载", "ERROR", str(e)[:200]))

        browser.close()

    all_results["ui"] = ui_results

    # ==================== Phase 3: Data Validation ====================
    print("\n[Phase 3] 数据正确性验证")
    print("-" * 60)

    data_results = []

    # Validate dashboard data
    today_data = api.test_endpoint("Data - 今日用量数据", "/api/today")
    if today_data:
        if isinstance(today_data, list) and len(today_data) > 0:
            for item in today_data:
                tool = item.get("tool_name")
                tokens = item.get("tokens_used", 0)
                input_t = item.get("input_tokens", 0)
                output_t = item.get("output_tokens", 0)
                # Validate: tokens_used should equal input + output
                if tokens != input_t + output_t:
                    data_results.append((
                        f"Data - {tool} token计算", "FAIL",
                        f"tokens_used({tokens}) != input({input_t}) + output({output_t})"
                    ))
                else:
                    data_results.append((f"Data - {tool} token计算", "PASS", None))
        else:
            data_results.append(("Data - 今日用量数据", "WARN", "今日无数据"))

    # Validate summary data
    summary = api.test_endpoint("Data - 汇总数据", "/api/summary")
    if summary and isinstance(summary, dict):
        total_tokens = 0
        for tool, data in summary.items():
            if isinstance(data, dict) and "total_tokens" in data:
                total_tokens += data["total_tokens"]
                # Check avg calculation
                if data.get("days_count", 0) > 0:
                    calc_avg = data["total_tokens"] / data["days_count"]
                    reported_avg = data.get("avg_tokens", 0)
                    if abs(calc_avg - reported_avg) > 1:
                        data_results.append((
                            f"Data - {tool} 平均值计算", "FAIL",
                            f"calculated({calc_avg:.0f}) != reported({reported_avg:.0f})"
                        ))
                    else:
                        data_results.append((f"Data - {tool} 平均值计算", "PASS", None))
        data_results.append(("Data - 汇总数据总量", "PASS", f"Total tokens across tools: {total_tokens}"))

    # Validate ROI data
    roi_data = api.test_endpoint("Data - ROI数据", "/api/roi")
    if roi_data and isinstance(roi_data, dict) and "data" in roi_data:
        d = roi_data["data"]
        # Check cost calculation
        total_cost = d.get("total_cost", 0)
        input_cost = d.get("input_cost", 0)
        output_cost = d.get("output_cost", 0)
        if abs(total_cost - input_cost - output_cost) > 0.01:
            data_results.append((
                "Data - ROI成本计算", "FAIL",
                f"total_cost({total_cost}) != input({input_cost}) + output({output_cost})"
            ))
        else:
            data_results.append(("Data - ROI成本计算", "PASS", None))

        # Check tokens = input + output
        total_tokens = d.get("tokens_used", 0)
        input_tokens = d.get("input_tokens", 0)
        output_tokens = d.get("output_tokens", 0)
        if total_tokens != input_tokens + output_tokens:
            data_results.append((
                "Data - ROI token计算", "FAIL",
                f"tokens({total_tokens}) != input({input_tokens}) + output({output_tokens})"
            ))
        else:
            data_results.append(("Data - ROI token计算", "PASS", None))

    # Validate project stats
    project_data = api.test_endpoint("Data - 项目统计", "/api/projects/stats")
    if project_data and isinstance(project_data, dict) and "stats" in project_data:
        for proj in project_data["stats"]:
            name = proj.get("project_name", "unknown")
            total_req = proj.get("total_requests", 0)
            user_req_sum = sum(u.get("total_requests", 0) for u in proj.get("user_stats", []))
            if total_req != user_req_sum and user_req_sum > 0:
                data_results.append((
                    f"Data - {name} 请求数汇总", "WARN",
                    f"project_total({total_req}) != user_sum({user_req_sum})"
                ))
            else:
                data_results.append((f"Data - {name} 请求数汇总", "PASS", None))

    # Validate analysis data consistency
    key_metrics = api.test_endpoint("Data - 分析关键指标", "/api/analysis/key-metrics")
    if key_metrics and isinstance(key_metrics, dict):
        total_input = key_metrics.get("total_input_tokens", 0)
        total_output = key_metrics.get("total_output_tokens", 0)
        tool_total = sum(t.get("count", 0) for t in key_metrics.get("top_tools", []))
        data_results.append((
            "Data - 分析指标 token总计", "INFO",
            f"input={total_input}, output={total_output}, tool_total={tool_total}"
        ))

    all_results["data"] = data_results

    # ==================== Print Results ====================
    print("\n" + "=" * 80)
    print("测试结果汇总")
    print("=" * 80)

    for phase, results in all_results.items():
        passed = sum(1 for r in results if r[1] == "PASS")
        failed = sum(1 for r in results if r[1] == "FAIL")
        warned = sum(1 for r in results if r[1] == "WARN")
        errored = sum(1 for r in results if r[1] == "ERROR")
        total = len(results)

        phase_names = {
            "api": "API 端点测试",
            "ui": "UI 页面测试",
            "data": "数据正确性验证",
        }
        print(f"\n--- {phase_names.get(phase, phase)} ---")
        print(f"总计: {total} | 通过: {passed} | 失败: {failed} | 警告: {warned} | 错误: {errored}")

        if failed > 0 or errored > 0:
            print(f"\n  失败/错误项:")
            for name, status, detail, *rest in results:
                if status in ("FAIL", "ERROR"):
                    path = rest[0] if rest else ""
                    print(f"    [{status}] {name}: {detail}")
                    if path:
                        print(f"           路径: {path}")

    # Overall summary
    all_flat = []
    for results in all_results.values():
        all_flat.extend(results)

    total_all = len(all_flat)
    passed_all = sum(1 for r in all_flat if r[1] == "PASS")
    failed_all = sum(1 for r in all_flat if r[1] == "FAIL")
    warned_all = sum(1 for r in all_flat if r[1] == "WARN")
    errored_all = sum(1 for r in all_flat if r[1] == "ERROR")

    print(f"\n{'=' * 80}")
    print(f"总通过率: {passed_all}/{total_all} = {(passed_all / total_all * 100) if total_all > 0 else 0:.1f}%")
    print(f"  通过: {passed_all} | 失败: {failed_all} | 警告: {warned_all} | 错误: {errored_all}")
    print("=" * 80)

    return all_results


if __name__ == "__main__":
    results = run_all_tests()
