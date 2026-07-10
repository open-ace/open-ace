#!/usr/bin/env python3
"""
Manual migration script to add tenant usage aggregation infrastructure.

This script bypasses Alembic version conflicts and directly applies the schema changes.
"""

import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from datetime import datetime

from app.repositories.database import Database


def apply_migration():
    """Apply tenant usage aggregation migration."""
    db = Database()

    with db.connection() as conn:
        cursor = conn.cursor()

        # Check if already applied
        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='aggregation_history'
        """
        )

        if cursor.fetchone():
            print("Migration already applied. Skipping...")
            return

        print("Applying tenant usage aggregation migration...")

        # 1. Create aggregation_history table
        print("Creating aggregation_history table...")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS aggregation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type VARCHAR(50) NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                records_count INTEGER DEFAULT 0,
                quality_report TEXT,
                error_message TEXT,
                started_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_aggregation_history_type_date ON aggregation_history(type, start_date, end_date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_aggregation_history_status ON aggregation_history(status)"
        )

        # 2. Create tenant_period_history table
        print("Creating tenant_period_history table...")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_period_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                tokens_used BIGINT DEFAULT 0,
                requests_made BIGINT DEFAULT 0,
                reset_at DATETIME NOT NULL,
                reset_by VARCHAR(100),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_period_history_tenant ON tenant_period_history(tenant_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_period_history_dates ON tenant_period_history(period_start, period_end)"
        )

        # 3. Create tenant_plans table
        print("Creating tenant_plans table...")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL UNIQUE,
                slug VARCHAR(50) NOT NULL UNIQUE,
                quota_defaults TEXT,
                price_monthly DECIMAL(10, 2) DEFAULT 0,
                price_quarterly DECIMAL(10, 2) DEFAULT 0,
                price_yearly DECIMAL(10, 2) DEFAULT 0,
                features TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenant_plans_slug ON tenant_plans(slug)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tenant_plans_active ON tenant_plans(is_active)"
        )

        # 4. Create alerts_history table
        print("Creating alerts_history table...")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type VARCHAR(50) NOT NULL,
                tenant_id INTEGER,
                severity VARCHAR(20) DEFAULT 'warning',
                message TEXT NOT NULL,
                details TEXT,
                recipients TEXT,
                channels VARCHAR(100),
                status VARCHAR(20) DEFAULT 'sent',
                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_history_type ON alerts_history(alert_type)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_history_tenant ON alerts_history(tenant_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_history_sent_at ON alerts_history(sent_at)"
        )

        # 5. Create consistency_violations table
        print("Creating consistency_violations table...")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS consistency_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER,
                violation_type VARCHAR(50) NOT NULL,
                expected_value BIGINT,
                actual_value BIGINT,
                difference BIGINT,
                details TEXT,
                status VARCHAR(20) DEFAULT 'detected',
                detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                repaired_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_consistency_violations_tenant ON consistency_violations(tenant_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_consistency_violations_status ON consistency_violations(status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_consistency_violations_detected ON consistency_violations(detected_at)"
        )

        # 6. Add new columns to tenants table
        print("Adding new columns to tenants table...")

        # Check and add each column individually
        columns_to_add = [
            ("billing_day", "INTEGER DEFAULT 1"),
            ("billing_cycle_type", 'VARCHAR(20) DEFAULT "monthly"'),
            ("billing_cycle_start", "DATE"),
            ("billing_cycle_end", "DATE"),
            ("current_cycle_tokens", "BIGINT DEFAULT 0"),
            ("over_limit_strategy", 'VARCHAR(20) DEFAULT "soft"'),
            ("over_limit_price_per_token", "DECIMAL(10, 6)"),
            ("usage_alert_threshold", "INTEGER DEFAULT 80"),
            ("usage_critical_threshold", "INTEGER DEFAULT 95"),
            ("alert_silence_hours", "INTEGER DEFAULT 24"),
        ]

        for column_name, column_type in columns_to_add:
            try:
                cursor.execute(f"ALTER TABLE tenants ADD COLUMN {column_name} {column_type}")
                print(f"  Added column: {column_name}")
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    print(f"  Column already exists: {column_name}")
                else:
                    print(f"  Error adding column {column_name}: {e}")

        # Create index on billing_cycle_end
        try:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenants_billing_cycle ON tenants(billing_cycle_end)"
            )
        except Exception as e:
            print(f"  Index creation warning: {e}")

        # Commit all changes
        conn.commit()

        print("\nMigration completed successfully!")
        print(
            "New tables created: aggregation_history, tenant_period_history, tenant_plans, alerts_history, consistency_violations"
        )
        print("New columns added to tenants table")


if __name__ == "__main__":
    try:
        apply_migration()
    except Exception as e:
        print(f"\nMigration failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
