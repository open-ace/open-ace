#!/usr/bin/env python3
"""
Seed management data for testing.

Populates empty tables with sample data so all management pages show content:
- Audit logs
- Content filter rules
- Alerts
- Hourly stats (aggregated from daily_usage)
"""

import os
import random
import sys
from datetime import datetime, timedelta

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app import create_app
from app.repositories.database import (
    adapt_sql,
    get_connection,
    get_param_placeholder,
    is_postgresql,
)


def seed_audit_logs(conn):
    """Insert sample audit logs."""
    cur = conn.cursor()

    # Check if already has data
    ph = get_param_placeholder()
    cur.execute("SELECT COUNT(*) as cnt FROM audit_logs")
    count = cur.fetchone()["cnt"] if is_postgresql() else cur.fetchone()[0]
    if count > 0:
        print(f"  audit_logs already has {count} rows, skipping")
        return

    users = [
        (1, "admin"),
        (89, "rhuang"),
        (90, "韩成凤"),
        (88, "regular_user"),
    ]

    actions = [
        ("login", "info", "auth", "User logged in"),
        ("login_failed", "warning", "auth", "Failed login attempt"),
        ("logout", "info", "auth", "User logged out"),
        ("user_create", "info", "user", "Created new user"),
        ("user_update", "info", "user", "Updated user profile"),
        ("user_delete", "warning", "user", "Deleted user"),
        ("role_change", "warning", "user", "Changed user role"),
        ("password_change", "info", "auth", "Password changed"),
        ("config_update", "info", "system", "Updated system configuration"),
        ("project_create", "info", "project", "Created new project"),
        ("project_delete", "warning", "project", "Deleted project"),
        ("session_start", "info", "session", "Started AI session"),
        ("session_end", "info", "session", "Ended AI session"),
        ("api_key_create", "info", "api_key", "Created API key"),
        ("api_key_revoke", "warning", "api_key", "Revoked API key"),
        ("quota_update", "info", "quota", "Updated user quota"),
        ("report_generate", "info", "report", "Generated compliance report"),
        ("data_export", "info", "data", "Exported data"),
        ("filter_rule_create", "info", "content_filter", "Created filter rule"),
        ("security_settings_update", "warning", "system", "Updated security settings"),
    ]

    ips = ["192.168.1.100", "192.168.1.101", "10.0.0.50", "192.168.64.4"]

    now = datetime.utcnow()
    logs = []
    for i in range(50):
        days_ago = random.randint(0, 30)
        hours_ago = random.randint(0, 23)
        ts = now - timedelta(days=days_ago, hours=hours_ago)
        user_id, username = random.choice(users)
        action, severity, resource_type, _ = random.choice(actions)
        ip = random.choice(ips)
        success = 1 if action != "login_failed" else random.choice([0, 1])
        detail = f"Action performed by {username}"

        logs.append(
            (
                ts,
                user_id,
                username,
                action,
                severity,
                resource_type,
                resource_type + "_1",
                detail,
                ip,
                "Mozilla/5.0",
                "sess_" + str(i),
                success,
                None,
            )
        )

    cur.execute(
        adapt_sql(f"""
        INSERT INTO audit_logs
        (timestamp, user_id, username, action, severity, resource_type,
         resource_id, details, ip_address, user_agent, session_id, success, error_message)
        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
    """),
        logs[0],
    )

    for log in logs[1:]:
        cur.execute(
            adapt_sql(f"""
            INSERT INTO audit_logs
            (timestamp, user_id, username, action, severity, resource_type,
             resource_id, details, ip_address, user_agent, session_id, success, error_message)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """),
            log,
        )

    conn.commit()
    print(f"  Inserted {len(logs)} audit logs")


def seed_filter_rules(conn):
    """Insert default content filter rules."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM content_filter_rules")
    count = cur.fetchone()["cnt"] if is_postgresql() else cur.fetchone()[0]
    if count > 0:
        print(f"  content_filter_rules already has {count} rows, skipping")
        return

    ph = get_param_placeholder()
    rules = [
        (
            "PII Email Detection",
            "regex",
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "medium",
            "mask",
            True,
        ),
        (
            "PII Phone Detection",
            "regex",
            r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "medium",
            "mask",
            True,
        ),
        (
            "Credit Card Detection",
            "regex",
            r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
            "high",
            "block",
            True,
        ),
        ("Password Exposure", "keyword", r"\bpassword\b", "high", "block", True),
        ("API Key Exposure", "keyword", r"\bapi[_-]?key\b", "high", "block", True),
        ("Secret Exposure", "keyword", r"\bsecret\b", "medium", "mask", True),
        ("SSN Detection", "regex", r"\b\d{3}-\d{2}-\d{4}\b", "high", "block", True),
    ]

    for desc, rule_type, pattern, severity, action, enabled in rules:
        cur.execute(
            adapt_sql(f"""
            INSERT INTO content_filter_rules
            (pattern, type, severity, action, is_enabled, description, created_at, updated_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """),
            (
                pattern,
                rule_type,
                severity,
                action,
                enabled,
                desc,
                datetime.utcnow(),
                datetime.utcnow(),
            ),
        )

    conn.commit()
    print(f"  Inserted {len(rules)} filter rules")


def seed_alerts(conn):
    """Insert sample alerts."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM alerts")
    count = cur.fetchone()["cnt"] if is_postgresql() else cur.fetchone()[0]
    if count > 0:
        print(f"  alerts already has {count} rows, skipping")
        return

    ph = get_param_placeholder()
    alerts_data = [
        (
            "quota",
            "medium",
            "Quota Warning",
            "User rhuang has used 80% of daily token quota",
            89,
            "rhuang",
            "qwen",
        ),
        (
            "quota",
            "high",
            "Quota Exceeded",
            "User rhuang has exceeded daily request quota",
            89,
            "rhuang",
            "claude",
        ),
        (
            "system",
            "low",
            "Data Fetch Completed",
            "Daily usage data refresh completed successfully",
            1,
            "admin",
            None,
        ),
        (
            "security",
            "medium",
            "Failed Login Attempts",
            "Multiple failed login attempts detected from 10.0.0.50",
            None,
            None,
            None,
        ),
        (
            "system",
            "info",
            "New Machine Registered",
            "Machine 'openace' registered successfully",
            1,
            "admin",
            None,
        ),
    ]

    now = datetime.utcnow()
    for alert_type, severity, title, message, user_id, username, tool_name in alerts_data:
        hours_ago = random.randint(1, 48)
        ts = now - timedelta(hours=hours_ago)
        alert_id = f"alert_{random.randint(10000, 99999)}"
        read_flag = 1 if hours_ago > 24 else 0
        cur.execute(
            adapt_sql(f"""
            INSERT INTO alerts
            (alert_id, alert_type, severity, title, message, user_id, username,
             tool_name, metadata, created_at, read, action_url, action_text)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """),
            (
                alert_id,
                alert_type,
                severity,
                title,
                message,
                user_id,
                username,
                tool_name,
                None,
                ts,
                read_flag,
                None,
                None,
            ),
        )

    conn.commit()
    print(f"  Inserted {len(alerts_data)} alerts")


def seed_hourly_stats(conn):
    """Aggregate hourly stats from daily_usage."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM hourly_stats")
    count = cur.fetchone()["cnt"] if is_postgresql() else cur.fetchone()[0]
    if count > 0:
        print(f"  hourly_stats already has {count} rows, skipping")
        return

    # Check if daily_messages has timestamps to aggregate from
    # For now, generate synthetic hourly data from daily_usage totals
    ph = get_param_placeholder()

    # Get recent daily_usage data to know which dates/tools/hosts have data
    cur.execute(adapt_sql("""
        SELECT date, tool_name, host_name,
               SUM(input_tokens) as input_tokens,
               SUM(output_tokens) as output_tokens,
               SUM(tokens_used) as total_tokens,
               SUM(request_count) as request_count
        FROM daily_usage
        WHERE date >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY date, tool_name, host_name
        ORDER BY date DESC
        LIMIT 50
    """))

    if not is_postgresql():
        # SQLite fallback
        cur.execute(adapt_sql("""
            SELECT date, tool_name, host_name,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(tokens_used) as total_tokens,
                   SUM(request_count) as request_count
            FROM daily_usage
            WHERE date >= date('now', '-7 days')
            GROUP BY date, tool_name, host_name
            ORDER BY date DESC
            LIMIT 50
        """))

    rows = cur.fetchall()
    if not rows:
        print("  No daily_usage data to aggregate, generating synthetic hourly stats")
        # Generate synthetic data for today
        today = datetime.utcnow().strftime("%Y-%m-%d")
        tools = ["qwen", "claude"]
        hosts = ["RichdeMacBook-Pro.local"]
        for tool in tools:
            for host in hosts:
                for hour in range(24):
                    base_tokens = (
                        random.randint(100000, 5000000)
                        if tool == "qwen"
                        else random.randint(50000, 500000)
                    )
                    out_tokens = int(base_tokens * 0.04)
                    in_tokens = base_tokens - out_tokens
                    reqs = random.randint(5, 100)
                    cur.execute(
                        adapt_sql(f"""
                        INSERT INTO hourly_stats
                        (date, hour, tool_name, host_name, total_tokens, input_tokens, output_tokens, request_count)
                        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """),
                        (today, hour, tool, host, base_tokens, in_tokens, out_tokens, reqs),
                    )
        conn.commit()
        print("  Generated synthetic hourly stats for today")
        return

    # Distribute daily totals across hours with realistic patterns
    business_hours_weight = [
        0.1,
        0.05,
        0.05,
        0.05,
        0.05,
        0.1,
        0.3,
        0.6,
        0.8,
        1.0,
        1.0,
        1.0,
        0.9,
        0.8,
        1.0,
        1.0,
        0.9,
        0.7,
        0.5,
        0.3,
        0.2,
        0.15,
        0.1,
        0.1,
    ]

    inserted = 0
    for row in rows:
        if isinstance(row, dict):
            date = row["date"]
            tool = row["tool_name"]
            host = row["host_name"]
            total_tokens = int(row["total_tokens"] or 0)
            input_tokens = int(row["input_tokens"] or 0)
            output_tokens = int(row["output_tokens"] or 0)
            request_count = int(row["request_count"] or 0)
        else:
            continue

        total_weight = sum(business_hours_weight)
        for hour in range(24):
            w = business_hours_weight[hour]
            h_tokens = int(total_tokens * w / total_weight)
            h_input = int(input_tokens * w / total_weight)
            h_output = int(output_tokens * w / total_weight)
            h_reqs = max(1, int(request_count * w / total_weight))

            cur.execute(
                adapt_sql(f"""
                INSERT INTO hourly_stats
                (date, hour, tool_name, host_name, total_tokens, input_tokens, output_tokens, request_count)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """),
                (date, hour, tool, host, h_tokens, h_input, h_output, h_reqs),
            )
            inserted += 1

    conn.commit()
    print(f"  Inserted {inserted} hourly stats rows")


def seed_retention_history(conn):
    """Insert sample retention history if empty."""
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM retention_history")
    count = cur.fetchone()["cnt"] if is_postgresql() else cur.fetchone()[0]
    if count > 0:
        print(f"  retention_history already has {count} rows, skipping")
        return

    import json

    ph = get_param_placeholder()
    now = datetime.utcnow()
    for i in range(3):
        ts = now - timedelta(days=i * 7)
        report = json.dumps(
            {
                "timestamp": ts.isoformat(),
                "rules_applied": [
                    {
                        "data_type": "sessions",
                        "action": "delete",
                        "records_affected": random.randint(5, 20),
                    },
                    {
                        "data_type": "audit_logs",
                        "action": "archive",
                        "records_affected": random.randint(50, 200),
                    },
                ],
                "records_deleted": random.randint(10, 50),
                "records_archived": random.randint(50, 200),
                "records_anonymized": 0,
                "errors": [],
            }
        )
        cur.execute(
            adapt_sql(f"""
            INSERT INTO retention_history (timestamp, report_data)
            VALUES ({ph}, {ph})
        """),
            (ts, report),
        )

    conn.commit()
    print("  Inserted 3 retention history records")


def main():
    print("Seeding management data...")
    print("=" * 50)

    app = create_app()
    with app.app_context():
        conn = get_connection()

        seed_audit_logs(conn)
        seed_filter_rules(conn)
        seed_alerts(conn)
        seed_hourly_stats(conn)
        seed_retention_history(conn)

        conn.close()

    print("=" * 50)
    print("Done! All management pages should now have data.")


if __name__ == "__main__":
    main()
