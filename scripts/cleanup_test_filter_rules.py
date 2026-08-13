#!/usr/bin/env python3
"""
Cleanup test filter rules from content_filter_rules table.

Issue: #2550

This script:
1. Backs up test/invalid rules to a JSON file
2. Disables test rules (sets is_enabled=false)
3. Provides rollback capability to restore rules
4. Logs all operations to audit_logs

Usage:
    # Dry run (show what would be cleaned)
    python scripts/cleanup_test_filter_rules.py --dry-run

    # Clean up test rules
    python scripts/cleanup_test_filter_rules.py

    # Rollback from backup
    python scripts/cleanup_test_filter_rules.py --rollback --backup-file /path/to/backup.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app import create_app
from app.repositories.database import get_connection


def get_backup_dir() -> Path:
    """Get backup directory path."""
    backup_dir = Path("/var/lib/openace/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def identify_test_rules(conn) -> list[dict]:
    """
    Identify test/invalid filter rules.

    Test rules are identified by:
    - category='test'
    - Patterns that are clearly test patterns
    - Empty or invalid descriptions
    """
    cur = conn.cursor()

    # Rules with category='test'
    cur.execute(
        """
        SELECT * FROM content_filter_rules
        WHERE category = 'test' OR description LIKE '%test%' OR description LIKE '%测试%'
        """
    )
    test_rules = cur.fetchall()

    # Also find rules with empty descriptions or patterns that look like test data
    cur.execute(
        """
        SELECT * FROM content_filter_rules
        WHERE description IS NULL OR description = '' OR pattern LIKE '%test_keyword%'
        """
    )
    additional_rules = cur.fetchall()

    # Combine and dedupe by id
    all_rules = {r["id"]: r for r in test_rules}
    for r in additional_rules:
        all_rules[r["id"]] = r

    # Exclude system rules
    system_rules = [
        r for r in all_rules.values() if r.get("source") == "system" or r.get("category") == "pii"
    ]
    for r in system_rules:
        all_rules.pop(r["id"], None)

    return list(all_rules.values())


def backup_rules(rules: list[dict], backup_file: Path) -> None:
    """Backup rules to JSON file."""
    backup_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rule_count": len(rules),
        "rules": rules,
    }

    with open(backup_file, "w") as f:
        json.dump(backup_data, f, indent=2, default=str)

    print(f"Backed up {len(rules)} rules to {backup_file}")


def disable_rules(conn, rule_ids: list[int], dry_run: bool = False) -> None:
    """Disable rules by setting is_enabled=false."""
    if not rule_ids:
        print("No rules to disable")
        return

    if dry_run:
        print(f"DRY RUN: Would disable {len(rule_ids)} rules: {rule_ids}")
        return

    cur = conn.cursor()
    placeholders = ", ".join(["?" for _ in rule_ids])
    cur.execute(
        f"""
        UPDATE content_filter_rules
        SET is_enabled = 0, updated_at = ?
        WHERE id IN ({placeholders})
        """,
        [datetime.now(timezone.utc).replace(tzinfo=None).isoformat()] + rule_ids,
    )

    conn.commit()
    print(f"Disabled {len(rule_ids)} rules")


def log_cleanup_action(conn, rule_ids: list[int], action: str) -> None:
    """Log cleanup action to audit_logs."""
    cur = conn.cursor()

    # Get a system user ID for audit logging
    cur.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    user_row = cur.fetchone()
    user_id = user_row["id"] if user_row else 1

    for rule_id in rule_ids:
        cur.execute(
            """
            INSERT INTO audit_logs
            (timestamp, user_id, username, action, severity, resource_type,
             resource_id, details, ip_address, user_agent, session_id, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                user_id,
                "system",
                action,
                "info",
                "filter_rule",
                str(rule_id),
                json.dumps({"action": "cleanup_test_rule", "rule_id": rule_id}),
                "127.0.0.1",
                "cleanup_script",
                None,
                True,
            ),
        )

    conn.commit()


def rollback_rules(conn, backup_file: Path) -> None:
    """Restore rules from backup file."""
    if not backup_file.exists():
        print(f"Backup file not found: {backup_file}")
        return

    with open(backup_file) as f:
        backup_data = json.load(f)

    rules = backup_data.get("rules", [])
    if not rules:
        print("No rules in backup file")
        return

    cur = conn.cursor()
    restored_count = 0

    for rule in rules:
        cur.execute(
            """
            UPDATE content_filter_rules
            SET is_enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                rule.get("is_enabled", 1),
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                rule["id"],
            ),
        )
        if cur.rowcount > 0:
            restored_count += 1

    conn.commit()
    print(f"Restored {restored_count} rules from backup")


def main():
    parser = argparse.ArgumentParser(description="Cleanup test filter rules")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleaned without making changes")
    parser.add_argument("--rollback", action="store_true", help="Rollback from backup file")
    parser.add_argument("--backup-file", type=str, help="Backup file path for rollback")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        conn = get_connection()

        if args.rollback:
            backup_file = Path(args.backup_file) if args.backup_file else None
            if not backup_file:
                # Find most recent backup
                backup_dir = get_backup_dir()
                backups = sorted(backup_dir.glob("filter_rules_*.json"), reverse=True)
                if not backups:
                    print("No backup files found")
                    return
                backup_file = backups[0]

            rollback_rules(conn, backup_file)
            return

        # Identify test rules
        test_rules = identify_test_rules(conn)

        if not test_rules:
            print("No test rules found")
            return

        print(f"Found {len(test_rules)} test/invalid rules:")
        for rule in test_rules:
            print(f"  ID {rule['id']}: {rule.get('description', '(no desc)')} - {rule['pattern']}")

        if args.dry_run:
            print("\nDRY RUN: No changes made")
            return

        # Create backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = get_backup_dir() / f"filter_rules_{timestamp}.json"
        backup_rules(test_rules, backup_file)

        # Disable rules
        rule_ids = [r["id"] for r in test_rules]
        disable_rules(conn, rule_ids)

        # Log cleanup action
        log_cleanup_action(conn, rule_ids, "system_config_change")

        print("\nCleanup completed successfully")
        print(f"Backup file: {backup_file}")
        print("To rollback, run:")
        print(f"  python scripts/cleanup_test_filter_rules.py --rollback --backup-file {backup_file}")


if __name__ == "__main__":
    main()