#!/usr/bin/env python3
"""
全面 API 功能测试 - 逐端点验证所有 API 返回正确数据
"""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
s = requests.Session()

issues = []


def add_issue(endpoint, description, severity="medium"):
    issues.append({"endpoint": endpoint, "description": description, "severity": severity})
    print(f"  [ISSUE-{severity.upper()}] {endpoint}: {description}")


def api_get(path, timeout=30):
    """GET request with timeout."""
    try:
        r = s.get(f"{BASE_URL}{path}", timeout=timeout)
        if r.status_code >= 400:
            add_issue(path, f"HTTP {r.status_code}: {r.text[:100]}", "high")
            return None
        return r.json()
    except requests.exceptions.Timeout:
        add_issue(path, f"请求超时 ({timeout}s)", "high")
        return None
    except Exception as e:
        add_issue(path, f"请求错误: {str(e)[:80]}", "high")
        return None


def run_all_tests():
    print("=" * 80)
    print("全面 API 功能测试")
    print("=" * 80)

    # Login
    print("\n[1] 登录")
    r = s.post(
        f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10
    )
    if r.status_code != 200 or not r.json().get("success"):
        print("  FAIL - 登录失败")
        return issues
    print("  OK - 登录成功")

    # ============================================================
    print("\n[2] Dashboard API")
    data = api_get("/api/summary")
    if data:
        for tool, d in data.items():
            last = d.get("last_date", "")
            print(
                f"  {tool}: {d.get('first_date','')} ~ {last}, tokens={d.get('total_tokens',0):,}"
            )
            if tool == "claude" and last < "2026-04-01":
                add_issue(f"/api/summary ({tool})", f"数据过期: 最后日期 {last}")

    data = api_get("/api/today")
    if data and isinstance(data, list):
        for item in data:
            tool = item.get("tool_name", "?")
            tokens = item.get("tokens_used", 0)
            reqs = item.get("request_count", 0)
            print(f"  今日 {tool}: {tokens:,} tokens, {reqs} requests")
            if tokens == 0:
                add_issue(f"/api/today ({tool})", "今日 tokens=0")

    # ============================================================
    print("\n[3] Analysis API")
    api_get("/api/analysis/batch", timeout=30)
    api_get("/api/analysis/key-metrics", timeout=30)
    api_get("/api/analysis/hourly-usage", timeout=30)
    api_get("/api/analysis/peak-usage", timeout=30)
    api_get("/api/analysis/user-ranking", timeout=30)
    api_get("/api/analysis/conversation-stats", timeout=30)
    api_get("/api/analysis/tool-comparison", timeout=30)
    api_get("/api/analysis/anomaly-detection", timeout=30)

    # ============================================================
    print("\n[4] ROI API")
    data = api_get("/api/roi/summary", timeout=60)
    if data and data.get("success"):
        roi = data["data"]["roi"]
        print(f"  ROI: {roi.get('roi_percentage', 0):.1f}%, tokens={roi.get('tokens_used', 0):,}")

    data = api_get("/api/roi/by-tool", timeout=30)
    data = api_get("/api/roi/by-user", timeout=30)

    # ============================================================
    print("\n[5] Messages API")
    data = api_get(
        "/api/messages?start_date=2026-04-22&end_date=2026-04-22&role=user&limit=3", timeout=30
    )
    if data:
        print(f"  消息数: {data.get('total', 0)}, 返回: {len(data.get('messages', []))}")

    # Sender filter
    data = api_get(
        "/api/messages?start_date=2026-04-22&end_date=2026-04-22&sender=rhuang&role=user&limit=3",
        timeout=30,
    )
    if data:
        print(f"  Sender filter (rhuang): total={data.get('total', 0)}")

    data = api_get("/api/senders", timeout=30)
    if data and isinstance(data, list):
        print(f"  Senders: {len(data)} 个, 示例: {data[:3]}")

    # ============================================================
    print("\n[6] Conversation History API")
    data = api_get("/api/conversation-history?limit=3", timeout=30)
    if data:
        convs = data.get("data", [])
        total = data.get("total", 0)
        print(f"  Total: {total}, 返回: {len(convs)}")
        if total == 0:
            add_issue("/api/conversation-history", "无对话数据")
        if convs:
            c = convs[0]
            print(
                f"  示例: {c.get('conversation_id','?')[:30]}..., messages={c.get('message_count',0)}"
            )

    # ============================================================
    print("\n[7] Audit Logs API")
    data = api_get("/api/audit/logs?page=1&limit=3", timeout=15)
    if data:
        logs = data.get("logs", [])
        total = data.get("total", 0)
        print(f"  Total: {total}, 返回: {len(logs)}")
        if total == 0:
            add_issue("/api/audit/logs", "无审计日志数据")

    # ============================================================
    print("\n[8] Audit Analysis API")
    data = api_get("/api/compliance/audit/patterns?days=30", timeout=30)
    if data:
        events = data.get("total_events", 0)
        actions = list(data.get("action_distribution", {}).keys())
        print(f"  Events: {events}, Actions: {actions[:5]}")
        if events == 0:
            add_issue("/api/audit/patterns", "无审计分析数据")

    data = api_get("/api/compliance/audit/security-score?days=30", timeout=30)
    if data:
        print(
            f"  Score: {data.get('score')}, Grade: {data.get('grade')}, Anomalies: {data.get('anomaly_count')}"
        )

    data = api_get("/api/compliance/audit/anomalies?days=7", timeout=30)
    if data:
        print(f"  Anomalies: {data.get('count', 0)}")

    data = api_get("/api/compliance/audit/user/1/profile?days=30", timeout=30)
    if data:
        print(
            f"  User 1 profile: actions={data.get('total_actions', 0)}, peak_hour={data.get('peak_activity_hour')}"
        )

    # ============================================================
    print("\n[9] Alerts API")
    data = api_get("/api/alerts?limit=5", timeout=15)
    if data:
        alerts = data if isinstance(data, list) else data.get("alerts", data.get("data", []))
        print(f"  Alerts: {len(alerts) if isinstance(alerts, list) else 'N/A'}")

    data = api_get("/api/alerts/unread-count", timeout=15)
    if data:
        print(f"  Unread: {data.get('count', data.get('unread_count', 'N/A'))}")

    # ============================================================
    print("\n[10] Security Center API")
    data = api_get("/api/filter-rules", timeout=15)
    if data:
        rules = data if isinstance(data, list) else data.get("rules", data.get("filters", []))
        print(f"  Filter rules: {len(rules) if isinstance(rules, list) else 'N/A'}")

    data = api_get("/api/security-settings", timeout=15)
    if data:
        print("  Security settings: OK")

    # ============================================================
    print("\n[11] Users API")
    data = api_get("/api/admin/users", timeout=15)
    if data:
        users = data if isinstance(data, list) else data.get("users", [])
        print(f"  Users: {len(users) if isinstance(users, list) else 'N/A'}")

    # ============================================================
    print("\n[12] Quota API")
    data = api_get("/api/quota/status", timeout=30)
    if data:
        print("  Quota status: OK")

    # ============================================================
    print("\n[13] Compliance API")
    data = api_get("/api/compliance/reports", timeout=15)
    if data:
        report_types = data.get("report_types", [])
        print(f"  Report types: {len(report_types)}")

    data = api_get("/api/compliance/reports/saved", timeout=15)
    if data:
        reports = data.get("reports", [])
        print(f"  Saved reports: {len(reports)}")

    data = api_get("/api/compliance/retention/rules", timeout=15)
    if data:
        rules = data.get("rules", {})
        print(f"  Retention rules: {len(rules)}")

    data = api_get("/api/compliance/retention/history", timeout=15)
    if data:
        history = data.get("history", [])
        print(f"  Retention history: {len(history)}")

    # ============================================================
    print("\n[14] Tenants API")
    data = api_get("/api/tenants", timeout=15)
    if data:
        tenants = data if isinstance(data, list) else data.get("tenants", [])
        print(f"  Tenants: {len(tenants) if isinstance(tenants, list) else 'N/A'}")

    # ============================================================
    print("\n[15] Projects API")
    data = api_get("/api/projects", timeout=15)
    if data:
        projects = data if isinstance(data, list) else data.get("projects", [])
        print(f"  Projects: {len(projects) if isinstance(projects, list) else 'N/A'}")

    # ============================================================
    print("\n[16] SSO Settings API")
    data = api_get("/api/sso/providers", timeout=15)
    if data:
        print("  SSO providers: OK")

    # ============================================================
    print("\n[17] Remote Machines API")
    data = api_get("/api/remote/machines/available", timeout=15)
    if data:
        machines = data if isinstance(data, list) else data.get("machines", [])
        print(f"  Machines: {len(machines) if isinstance(machines, list) else 'N/A'}")

    # ============================================================
    # Summary
    print("\n" + "=" * 80)
    print(f"发现 {len(issues)} 个问题")
    print("=" * 80)

    for sev in ["high", "medium", "low"]:
        sev_issues = [i for i in issues if i["severity"] == sev]
        if sev_issues:
            print(f"\n--- {sev.upper()} ---")
            for idx, issue in enumerate(sev_issues):
                print(f"  {idx+1}. [{issue['endpoint']}] {issue['description']}")

    return issues


if __name__ == "__main__":
    results = run_all_tests()
    print(f"\nTotal issues: {len(results)}")
