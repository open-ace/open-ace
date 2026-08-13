#!/usr/bin/env python3
"""
Open ACE - Cleanup Test Filter Rules Script

清理生产环境中的测试规则。

用法:
    python scripts/cleanup_test_filter_rules.py [--force] [--disable] [--delete]

选项:
    --force: 跳过确认提示
    --disable: 禁用测试规则（默认）
    --delete: 删除测试规则

环境变量:
    FORCE_CLEANUP=1: 生产环境必须设置此变量才能执行
"""

import argparse
import os
import sys
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app import create_app
from app.repositories.database import (
    adapt_boolean_condition,
    adapt_boolean_value,
    adapt_sql,
    get_connection,
)


def check_environment():
    """检查环境是否允许清理"""
    # 检查是否在生产环境
    env = os.getenv("OPENACE_ENV", "development")

    if env == "production":
        # 生产环境需要 FORCE_CLEANUP 环境变量
        if not os.getenv("FORCE_CLEANUP"):
            print("❌ 错误: 生产环境需要设置 FORCE_CLEANUP=1 环境变量")
            print("   提示: export FORCE_CLEANUP=1")
            return False

    return True


def get_test_rules(conn):
    """获取所有测试规则"""
    cursor = conn.cursor()

    # Use adapt_boolean_condition for cross-database compatibility
    is_test_condition = adapt_boolean_condition("is_test", True)
    query = adapt_sql(f"""
        SELECT id, pattern, description, type, severity, action, created_at
        FROM content_filter_rules
        WHERE {is_test_condition}
        ORDER BY created_at DESC
    """)

    cursor.execute(query)
    rows = cursor.fetchall()

    return rows


def get_trigger_count(conn, rule_id):
    """获取规则触发次数"""
    cursor = conn.cursor()

    query = adapt_sql("""
        SELECT COUNT(*) as count
        FROM filter_rule_trigger_log
        WHERE rule_id = ?
    """)

    cursor.execute(query, (rule_id,))
    row = cursor.fetchone()

    return row[0] if row else 0


def display_rules(rules, conn):
    """显示测试规则列表"""
    print("\n" + "=" * 80)
    print("测试规则列表:")
    print("=" * 80)

    for rule in rules:
        rule_id = rule[0]
        pattern = rule[1]
        description = rule[2]
        rule_type = rule[3]
        severity = rule[4]
        action = rule[5]
        created_at = rule[6]

        trigger_count = get_trigger_count(conn, rule_id)

        print(f"\n规则 ID: {rule_id}")
        print(f"  模式: {pattern}")
        print(f"  描述: {description or '(无)'}")
        print(f"  类型: {rule_type}")
        print(f"  严重性: {severity}")
        print(f"  动作: {action}")
        print(f"  创建时间: {created_at}")
        print(f"  触发次数: {trigger_count}")

    print("\n" + "=" * 80)


def disable_test_rules(conn, rule_ids):
    """禁用测试规则"""
    cursor = conn.cursor()

    for rule_id in rule_ids:
        # Use adapt_boolean_value for cross-database compatibility
        is_enabled_value = adapt_boolean_value(False)
        query = adapt_sql("""
            UPDATE content_filter_rules
            SET is_enabled = ?
            WHERE id = ?
        """)
        cursor.execute(query, (is_enabled_value, rule_id))

    conn.commit()
    print(f"\n✅ 已禁用 {len(rule_ids)} 条测试规则")


def delete_test_rules(conn, rule_ids):
    """删除测试规则"""
    cursor = conn.cursor()

    # 先删除触发日志
    for rule_id in rule_ids:
        query = adapt_sql("DELETE FROM filter_rule_trigger_log WHERE rule_id = ?")
        cursor.execute(query, (rule_id,))

    # 再删除审批日志
    for rule_id in rule_ids:
        query = adapt_sql("DELETE FROM filter_rule_approval_log WHERE rule_id = ?")
        cursor.execute(query, (rule_id,))

    # 最后删除规则
    for rule_id in rule_ids:
        query = adapt_sql("DELETE FROM content_filter_rules WHERE id = ?")
        cursor.execute(query, (rule_id,))

    conn.commit()
    print(f"\n✅ 已删除 {len(rule_ids)} 条测试规则")


def log_cleanup_action(conn, rule_ids, action, user="system"):
    """记录清理操作到审计日志"""
    cursor = conn.cursor()

    for rule_id in rule_ids:
        query = adapt_sql("""
            INSERT INTO audit_logs
            (timestamp, user_id, username, action, severity, resource_type,
             resource_id, details, ip_address, user_agent, session_id, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)

        cursor.execute(
            query,
            (
                datetime.now().isoformat(),
                0,
                user,
                "test_rule_cleanup",
                "info",
                "filter_rule",
                str(rule_id),
                f'{{"action": "{action}", "rule_id": {rule_id}}}',
                "localhost",
                "cleanup_script",
                None,
                1,
            ),
        )

    conn.commit()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="清理测试内容过滤规则",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--force", action="store_true", help="跳过确认提示")
    parser.add_argument("--disable", action="store_true", help="禁用测试规则（默认）")
    parser.add_argument("--delete", action="store_true", help="删除测试规则")

    args = parser.parse_args()

    # 检查环境
    if not check_environment():
        sys.exit(1)

    # 确定操作
    action = "disable"
    if args.delete:
        action = "delete"

    print(f"清理测试规则脚本 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"环境: {os.getenv('OPENACE_ENV', 'development')}")
    print(f"操作: {action}")

    # 创建应用上下文
    app = create_app()

    with app.app_context():
        conn = get_connection()

        # 获取测试规则
        rules = get_test_rules(conn)

        if not rules:
            print("\n✅ 未发现测试规则")
            return

        # 显示规则
        display_rules(rules, conn)

        rule_ids = [rule[0] for rule in rules]
        print(f"\n发现 {len(rules)} 条测试规则: {rule_ids}")

        # 确认操作
        if not args.force:
            print(f"\n即将执行操作: {action}")
            confirm = input("确认继续? (输入 yes 继续): ")

            if confirm.lower() != "yes":
                print("❌ 操作已取消")
                return

        # 执行清理
        if action == "disable":
            disable_test_rules(conn, rule_ids)
        elif action == "delete":
            delete_test_rules(conn, rule_ids)

        # 记录审计日志
        log_cleanup_action(conn, rule_ids, action)

        print("\n" + "=" * 80)
        print("清理完成!")
        print("=" * 80)


if __name__ == "__main__":
    main()
