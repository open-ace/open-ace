#!/usr/bin/env python3
"""
Migration: anomaly_status gains anomaly_id + tenant_id columns.

Issue: #2749 – anomaly status identity was too coarse (type + users hash only),
causing historical status to pollute new anomaly instances.

Schema changes applied:
  1. Add ``anomaly_id TEXT NOT NULL DEFAULT ''`` column.
  2. Add ``tenant_id INTEGER`` column.
  3. Create partial unique index ``ix_anomaly_status_anomaly_id`` on
     anomaly_id where anomaly_id is not empty.

Existing rows are left with anomaly_id = '' so they do not collide with
new rows.  They will be treated as *legacy* by the application and will
not be matched against newly detected anomalies.
"""

import os
import sys

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from app.repositories.database import Database


def _col_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def apply_migration() -> None:
    db = Database()

    with db.connection() as conn:
        cursor = conn.cursor()

        # 1. Add anomaly_id column
        if not _col_exists(cursor, "anomaly_status", "anomaly_id"):
            print("Adding anomaly_id column to anomaly_status …")
            cursor.execute(
                "ALTER TABLE anomaly_status "
                "ADD COLUMN anomaly_id TEXT NOT NULL DEFAULT ''"
            )
        else:
            print("anomaly_id column already exists – skipping.")

        # 2. Add tenant_id column
        if not _col_exists(cursor, "anomaly_status", "tenant_id"):
            print("Adding tenant_id column to anomaly_status …")
            cursor.execute(
                "ALTER TABLE anomaly_status ADD COLUMN tenant_id INTEGER"
            )
        else:
            print("tenant_id column already exists – skipping.")

        # 3. Create partial unique index on anomaly_id
        try:
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_anomaly_status_anomaly_id "
                "ON anomaly_status (anomaly_id) WHERE anomaly_id != ''"
            )
            print("Created partial unique index ix_anomaly_status_anomaly_id.")
        except Exception as e:
            print(f"Index creation note: {e}")

        conn.commit()

    print("Migration complete.")


if __name__ == "__main__":
    apply_migration()
