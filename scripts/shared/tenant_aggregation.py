#!/usr/bin/env python3
"""
Tenant Usage Aggregation Module

This module provides functions to aggregate tenant usage statistics from quota_usage table.
It includes:
- Aggregation lock mechanism
- Tenant usage aggregation from quota_usage
- Billing cycle management
- Period reset functionality
"""

import calendar
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Import database utilities
from scripts.shared.db import _execute, get_connection, is_postgresql

logger = logging.getLogger(__name__)

# Constants
AGGREGATION_LOCK_ID = 12345  # Fixed ID for PostgreSQL advisory lock
AGGREGATION_LOCK_KEY = "tenant_aggregation"  # Key for SQLite lock table
DEFAULT_ALERT_THRESHOLD = 80
DEFAULT_CRITICAL_THRESHOLD = 95
DEFAULT_SILENCE_HOURS = 24


# ============================================================================
# Aggregation Lock Mechanism
# ============================================================================


class AggregationLockError(Exception):
    """Exception raised when aggregation lock cannot be acquired."""

    pass


class AggregationLock:
    """
    Context manager for aggregation lock.

    Ensures lock is always released, even on exceptions.
    """

    def __init__(self, timeout_seconds: int = 300):
        """
        Initialize aggregation lock.

        Args:
            timeout_seconds: Lock timeout in seconds (default 5 minutes).
        """
        self.timeout_seconds = timeout_seconds
        self.conn = None

    def __enter__(self):
        """Acquire aggregation lock."""
        self.conn = acquire_aggregation_lock(self.timeout_seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release aggregation lock."""
        if self.conn:
            release_aggregation_lock(self.conn)
        return False  # Don't suppress exceptions


def acquire_aggregation_lock(timeout_seconds: int = 300):
    """
    Acquire aggregation lock to prevent concurrent execution.

    Args:
        timeout_seconds: Lock timeout in seconds.

    Returns:
        Database connection holding the lock.

    Raises:
        AggregationLockError: If lock cannot be acquired.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        if is_postgresql():
            # PostgreSQL: Use advisory lock with timeout
            # Set lock timeout to prevent indefinite blocking
            _execute(cursor, f"SET lock_timeout = '{timeout_seconds}s'")
            _execute(cursor, f"SELECT pg_advisory_lock({AGGREGATION_LOCK_ID})")
            conn.commit()
            logger.info("Acquired PostgreSQL advisory lock for tenant aggregation")
        else:
            # SQLite: Use dedicated lock table with timeout
            # Clean up expired locks first
            _execute(
                cursor,
                f"""
                DELETE FROM aggregation_locks
                WHERE lock_key = '{AGGREGATION_LOCK_KEY}'
                  AND acquired_at < datetime('now', '-{timeout_seconds} seconds')
            """,
            )

            # Try to acquire lock
            try:
                _execute(
                    cursor,
                    f"""
                    INSERT INTO aggregation_locks (lock_key, acquired_at, timeout_seconds)
                    VALUES ('{AGGREGATION_LOCK_KEY}', datetime('now'), {timeout_seconds})
                """,
                )
                conn.commit()
                logger.info("Acquired SQLite lock for tenant aggregation")
            except Exception as e:
                conn.close()
                if "UNIQUE constraint" in str(e) or "duplicate" in str(e).lower():
                    raise AggregationLockError("Aggregation lock already held by another process")
                raise

        return conn

    except Exception as e:
        conn.close()
        if isinstance(e, AggregationLockError):
            raise
        raise AggregationLockError(f"Failed to acquire aggregation lock: {e}")


def release_aggregation_lock(conn):
    """
    Release aggregation lock.

    Args:
        conn: Database connection holding the lock.
    """
    if conn is None:
        return

    try:
        cursor = conn.cursor()

        if is_postgresql():
            # PostgreSQL: Release advisory lock
            _execute(cursor, f"SELECT pg_advisory_unlock({AGGREGATION_LOCK_ID})")
            conn.commit()
            logger.info("Released PostgreSQL advisory lock")
        else:
            # SQLite: Delete lock record
            _execute(
                cursor,
                f"DELETE FROM aggregation_locks WHERE lock_key = '{AGGREGATION_LOCK_KEY}'",
            )
            conn.commit()
            logger.info("Released SQLite lock")

    except Exception as e:
        logger.error(f"Failed to release aggregation lock: {e}")
        # Even if release fails, connection close will release the lock for PostgreSQL
    finally:
        # Always close connection
        try:
            conn.close()
        except Exception:
            pass


# ============================================================================
# Data Quality Check
# ============================================================================


def check_quota_usage_quality(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """
    Check data quality of quota_usage table before aggregation.

    Args:
        start_date: Optional start date filter (YYYY-MM-DD).
        end_date: Optional end date filter (YYYY-MM-DD).

    Returns:
        Dictionary with quality report including:
        - total_records: Total number of records
        - null_user_id: Records with NULL user_id
        - negative_tokens: Records with negative tokens
        - abnormal_tokens: Records with abnormally high tokens (>10M)
        - quality_score: Percentage of good records
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Build date filter
        date_conditions = []
        params = []

        if start_date:
            date_conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            date_conditions.append("date <= ?")
            params.append(end_date)

        date_clause = " AND ".join(date_conditions) if date_conditions else "1=1"

        # Count total records
        _execute(
            cursor,
            f"SELECT COUNT(*) as count FROM quota_usage WHERE {date_clause}",
            params,
        )
        total_records = cursor.fetchone()["count"]

        # Count NULL user_id
        _execute(
            cursor,
            f"""
            SELECT COUNT(*) as count
            FROM quota_usage
            WHERE {date_clause} AND user_id IS NULL
        """,
            params,
        )
        null_user_id = cursor.fetchone()["count"]

        # Count negative tokens
        _execute(
            cursor,
            f"""
            SELECT COUNT(*) as count
            FROM quota_usage
            WHERE {date_clause} AND tokens_used < 0
        """,
            params,
        )
        negative_tokens = cursor.fetchone()["count"]

        # Count abnormally high tokens (>10M)
        _execute(
            cursor,
            f"""
            SELECT COUNT(*) as count
            FROM quota_usage
            WHERE {date_clause} AND tokens_used > 10000000
        """,
            params,
        )
        abnormal_tokens = cursor.fetchone()["count"]

        # Calculate quality score
        good_records = total_records - null_user_id - negative_tokens - abnormal_tokens
        quality_score = (good_records / total_records * 100) if total_records > 0 else 100

        return {
            "total_records": total_records,
            "null_user_id": null_user_id,
            "negative_tokens": negative_tokens,
            "abnormal_tokens": abnormal_tokens,
            "skipped_records": null_user_id + negative_tokens,
            "good_records": good_records,
            "quality_score": round(quality_score, 2),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    finally:
        conn.close()


# ============================================================================
# Billing Cycle Calculation
# ============================================================================


def calculate_next_billing_cycle_end(
    current_end_date: datetime, billing_day: int, cycle_type: str = "monthly"
) -> datetime:
    """
    Calculate next billing cycle end date.

    Args:
        current_end_date: Current billing cycle end date.
        billing_day: Billing day of month (1-31).
        cycle_type: Billing cycle type (monthly, quarterly, yearly).

    Returns:
        Next billing cycle end date.
    """
    # Move to next month
    if cycle_type == "monthly":
        # Next month
        next_month = current_end_date.replace(day=1) + timedelta(days=32)
        next_month = next_month.replace(day=1)
    elif cycle_type == "quarterly":
        # 3 months later
        next_month = current_end_date.replace(day=1) + timedelta(days=92)
        next_month = next_month.replace(day=1)
    elif cycle_type == "yearly":
        # 1 year later
        next_month = current_end_date.replace(year=current_end_date.year + 1, day=1)
    else:
        # Default to monthly
        next_month = current_end_date.replace(day=1) + timedelta(days=32)
        next_month = next_month.replace(day=1)

    # Try to set billing_day
    try:
        # Check if billing_day is valid for next_month
        last_day = calendar.monthrange(next_month.year, next_month.month)[1]

        if billing_day > last_day:
            # Use last day of month if billing_day doesn't exist
            next_end_date = next_month.replace(day=last_day)
        else:
            # Use billing_day
            next_end_date = next_month.replace(day=billing_day)
    except ValueError:
        # Fallback to last day of month
        last_day = calendar.monthrange(next_month.year, next_month.month)[1]
        next_end_date = next_month.replace(day=last_day)

    return next_end_date


# ============================================================================
# Period Reset Functionality
# ============================================================================


def reset_tenant_period(tenant_id: int) -> bool:
    """
    Reset billing period for a specific tenant.

    Args:
        tenant_id: Tenant ID to reset.

    Returns:
        True if successful, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Get tenant info
        _execute(
            cursor,
            """
            SELECT id, billing_cycle_start, billing_cycle_end, current_cycle_tokens,
                   billing_day, billing_cycle_type
            FROM tenants
            WHERE id = ?
        """,
            (tenant_id,),
        )

        tenant = cursor.fetchone()
        if not tenant:
            logger.error(f"Tenant {tenant_id} not found")
            return False

        # Archive current period usage
        current_period_start = tenant["billing_cycle_start"]
        current_period_end = tenant["billing_cycle_end"]
        current_tokens = tenant["current_cycle_tokens"]

        if current_period_start and current_period_end:
            _execute(
                cursor,
                """
                INSERT INTO tenant_period_history
                (tenant_id, period_start, period_end, tokens_used, requests_made, reset_at)
                SELECT ?, ?, ?, ?,
                       COALESCE(SUM(requests_made), 0),
                       datetime('now')
                FROM tenant_usage
                WHERE tenant_id = ?
                  AND date >= ?
                  AND date <= ?
            """,
                (
                    tenant_id,
                    current_period_start,
                    current_period_end,
                    current_tokens,
                    tenant_id,
                    current_period_start.strftime("%Y-%m-%d"),
                    current_period_end.strftime("%Y-%m-%d"),
                ),
            )

        # Calculate new billing cycle
        billing_day = tenant["billing_day"] or 1
        cycle_type = tenant["billing_cycle_type"] or "monthly"

        if current_period_end:
            # Handle both date and datetime objects from database
            if isinstance(current_period_end, datetime):
                period_end_date = current_period_end.date()
            elif isinstance(current_period_end, date):
                period_end_date = current_period_end
            else:
                period_end_date = datetime.strptime(str(current_period_end), "%Y-%m-%d").date()

            new_cycle_end = calculate_next_billing_cycle_end(
                period_end_date,
                billing_day,
                cycle_type,
            )
            new_cycle_start = period_end_date + timedelta(days=1)
        else:
            # Initialize from today
            today = datetime.now().date()
            new_cycle_start = today
            new_cycle_end = calculate_next_billing_cycle_end(today, billing_day, cycle_type)

        # Update tenant
        _execute(
            cursor,
            """
            UPDATE tenants
            SET billing_cycle_start = ?,
                billing_cycle_end = ?,
                current_cycle_tokens = 0,
                updated_at = datetime('now')
            WHERE id = ?
        """,
            (
                new_cycle_start.strftime("%Y-%m-%d"),
                new_cycle_end.strftime("%Y-%m-%d"),
                tenant_id,
            ),
        )

        conn.commit()
        logger.info(
            f"Reset billing period for tenant {tenant_id}: " f"{new_cycle_start} to {new_cycle_end}"
        )
        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to reset period for tenant {tenant_id}: {e}")
        return False
    finally:
        conn.close()


def reset_expired_tenant_periods() -> int:
    """
    Reset billing periods for all expired tenants.

    Returns:
        Number of tenants reset.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Find tenants with expired billing cycles
        today = datetime.now().date().strftime("%Y-%m-%d")

        _execute(
            cursor,
            """
            SELECT id FROM tenants
            WHERE billing_cycle_end < ?
               OR billing_cycle_end IS NULL
        """,
            (today,),
        )

        expired_tenants = cursor.fetchall()
        reset_count = 0

        for tenant_row in expired_tenants:
            tenant_id = tenant_row["id"]
            if reset_tenant_period(tenant_id):
                reset_count += 1

        return reset_count

    finally:
        conn.close()


# ============================================================================
# Tenant Usage Aggregation
# ============================================================================


def aggregate_tenant_usage_from_quota(
    start_date: str | None = None,
    end_date: str | None = None,
    quality_report: dict | None = None,
) -> tuple[int, dict]:
    """
    Aggregate tenant usage from quota_usage table.

    This function:
    1. Queries quota_usage table and joins with users and tenants
    2. Groups by tenant_id and date
    3. Upserts tenant_usage table with replacement strategy
    4. Atomically recalculates tenant statistics

    Args:
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).
        quality_report: Optional quality report from previous check.

    Returns:
        Tuple of (records_created, aggregation_report).
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Build date filter
        date_conditions = []
        params = []

        if start_date:
            date_conditions.append("q.date >= ?")
            params.append(start_date)

        if end_date:
            date_conditions.append("q.date <= ?")
            params.append(end_date)

        date_clause = " AND ".join(date_conditions) if date_conditions else "1=1"

        # Query quota_usage and aggregate by tenant and date
        logger.info("Querying quota_usage for tenant aggregation...")

        _execute(
            cursor,
            f"""
            SELECT
                u.tenant_id,
                q.date,
                SUM(q.tokens_used) as total_tokens,
                SUM(q.requests_used) as total_requests,
                COUNT(*) as record_count
            FROM quota_usage q
            JOIN users u ON q.user_id = u.id
            JOIN tenants t ON u.tenant_id = t.id
            WHERE u.tenant_id IS NOT NULL
              AND {date_clause}
            GROUP BY u.tenant_id, q.date
            ORDER BY u.tenant_id, q.date
        """,
            params,
        )

        usage_records = cursor.fetchall()

        if not usage_records:
            logger.info("No quota_usage records found for tenant aggregation")
            return 0, {"tenants_updated": 0, "records_processed": 0}

        # Upsert tenant_usage table
        logger.info(f"Upserting {len(usage_records)} records to tenant_usage...")

        records_inserted = 0
        tenant_ids = set()

        for record in usage_records:
            tenant_id = record["tenant_id"]
            date = record["date"]
            tokens = record["total_tokens"]
            requests = record["total_requests"]

            tenant_ids.add(tenant_id)

            if is_postgresql():
                _execute(
                    cursor,
                    """
                    INSERT INTO tenant_usage (tenant_id, date, tokens_used, requests_made)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tenant_id, date) DO UPDATE SET
                        tokens_used = EXCLUDED.tokens_used,
                        requests_made = EXCLUDED.requests_made
                """,
                    (tenant_id, date, tokens, requests),
                )
            else:
                _execute(
                    cursor,
                    """
                    INSERT INTO tenant_usage (tenant_id, date, tokens_used, requests_made)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (tenant_id, date) DO UPDATE SET
                        tokens_used = excluded.tokens_used,
                        requests_made = excluded.requests_made
                """,
                    (tenant_id, date, tokens, requests),
                )

            records_inserted += 1

        # Atomically recalculate tenant statistics
        logger.info(f"Recalculating statistics for {len(tenant_ids)} tenants...")

        tenants_updated = 0
        for tenant_id in tenant_ids:
            # Get current billing cycle
            _execute(
                cursor,
                """
                SELECT billing_cycle_start, billing_cycle_end
                FROM tenants
                WHERE id = ?
            """,
                (tenant_id,),
            )

            tenant_info = cursor.fetchone()
            if not tenant_info:
                continue

            # Update total_tokens_used (atomic recalculation from tenant_usage)
            _execute(
                cursor,
                """
                UPDATE tenants
                SET total_tokens_used = (
                    SELECT COALESCE(SUM(tokens_used), 0)
                    FROM tenant_usage
                    WHERE tenant_id = ?
                ),
                total_requests_made = (
                    SELECT COALESCE(SUM(requests_made), 0)
                    FROM tenant_usage
                    WHERE tenant_id = ?
                ),
                updated_at = datetime('now')
                WHERE id = ?
            """,
                (tenant_id, tenant_id, tenant_id),
            )

            # Update current_cycle_tokens if billing cycle is set
            if tenant_info["billing_cycle_start"] and tenant_info["billing_cycle_end"]:
                cycle_start = tenant_info["billing_cycle_start"]
                cycle_end = tenant_info["billing_cycle_end"]

                _execute(
                    cursor,
                    """
                    UPDATE tenants
                    SET current_cycle_tokens = (
                        SELECT COALESCE(SUM(tokens_used), 0)
                        FROM tenant_usage
                        WHERE tenant_id = ?
                          AND date >= ?
                          AND date <= ?
                    )
                    WHERE id = ?
                """,
                    (tenant_id, str(cycle_start), str(cycle_end), tenant_id),
                )

            tenants_updated += 1

        # Commit transaction
        conn.commit()

        logger.info(
            f"Tenant aggregation completed: {records_inserted} records, {tenants_updated} tenants"
        )

        return records_inserted, {
            "tenants_updated": tenants_updated,
            "records_processed": len(usage_records),
            "records_inserted": records_inserted,
            "tenant_ids": list(tenant_ids),
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Tenant aggregation failed: {e}")
        raise
    finally:
        conn.close()


# ============================================================================
# Idempotency Check
# ============================================================================


def check_aggregation_idempotency(aggregation_type: str, start_date: str, end_date: str) -> bool:
    """
    Check if aggregation has already been completed for the given date range.

    Args:
        aggregation_type: Type of aggregation (e.g., 'tenant_usage').
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        True if aggregation should proceed (not yet completed), False if already done.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        _execute(
            cursor,
            """
            SELECT COUNT(*) as count
            FROM aggregation_history
            WHERE type = ?
              AND start_date = ?
              AND end_date = ?
              AND status = 'completed'
        """,
            (aggregation_type, start_date, end_date),
        )

        count = cursor.fetchone()["count"]
        return count == 0

    finally:
        conn.close()


def record_aggregation_history(
    aggregation_type: str,
    start_date: str,
    end_date: str,
    status: str,
    records_count: int = 0,
    quality_report: dict | None = None,
    error_message: str | None = None,
) -> bool:
    """
    Record aggregation history.

    Args:
        aggregation_type: Type of aggregation.
        start_date: Start date.
        end_date: End date.
        status: Status (pending, running, completed, failed).
        records_count: Number of records processed.
        quality_report: Optional quality report.
        error_message: Optional error message.

    Returns:
        True if successful.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        quality_json = json.dumps(quality_report) if quality_report else None

        _execute(
            cursor,
            """
            INSERT INTO aggregation_history
            (type, start_date, end_date, status, records_count, quality_report, error_message, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
            (
                aggregation_type,
                start_date,
                end_date,
                status,
                records_count,
                quality_json,
                error_message,
            ),
        )

        conn.commit()
        return True

    except Exception as e:
        logger.error(f"Failed to record aggregation history: {e}")
        return False
    finally:
        conn.close()


# ============================================================================
# Main Aggregation Function
# ============================================================================


def run_tenant_aggregation(start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    Run complete tenant aggregation process.

    This includes:
    1. Period reset check
    2. Data quality check
    3. Aggregation lock acquisition
    4. Idempotency check
    5. Tenant usage aggregation
    6. History recording

    Args:
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).

    Returns:
        Aggregation report.
    """
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "periods_reset": 0,
        "quality_report": None,
        "records_aggregated": 0,
        "tenants_updated": 0,
        "status": "pending",
    }

    try:
        # Step 1: Reset expired tenant periods
        logger.info("Checking for expired tenant periods...")
        periods_reset = reset_expired_tenant_periods()
        report["periods_reset"] = periods_reset

        # Step 2: Data quality check
        logger.info("Checking data quality...")
        quality_report = check_quota_usage_quality(start_date, end_date)
        report["quality_report"] = quality_report

        # Check quality score
        if quality_report["quality_score"] < 90:
            logger.warning(
                f"Data quality score is low: {quality_report['quality_score']}%."
                f"Proceeding with caution."
            )

        # Step 3: Acquire aggregation lock
        logger.info("Acquiring aggregation lock...")
        with AggregationLock(timeout_seconds=300):
            # Step 4: Idempotency check
            if start_date and end_date:
                if not check_aggregation_idempotency("tenant_usage", start_date, end_date):
                    logger.info(
                        f"Tenant aggregation already completed for {start_date} to {end_date}."
                        f"Skipping."
                    )
                    report["status"] = "skipped"
                    return report

            # Step 5: Aggregate tenant usage
            logger.info("Aggregating tenant usage...")
            records, agg_report = aggregate_tenant_usage_from_quota(
                start_date, end_date, quality_report
            )

            report["records_aggregated"] = records
            report["tenants_updated"] = agg_report["tenants_updated"]

            # Step 6: Record aggregation history
            logger.info("Recording aggregation history...")
            record_aggregation_history(
                "tenant_usage",
                start_date or "all",
                end_date or "all",
                "completed",
                records,
                quality_report,
            )

            report["status"] = "completed"

    except Exception as e:
        logger.error(f"Tenant aggregation failed: {e}")
        report["status"] = "failed"
        report["error"] = str(e)

        # Record failure
        record_aggregation_history(
            "tenant_usage",
            start_date or "all",
            end_date or "all",
            "failed",
            0,
            report.get("quality_report"),
            str(e),
        )

        raise

    finally:
        report["completed_at"] = datetime.now(timezone.utc).isoformat()

    return report


if __name__ == "__main__":
    # Run tenant aggregation when called directly
    logging.basicConfig(level=logging.INFO)

    import argparse

    parser = argparse.ArgumentParser(description="Aggregate tenant usage")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    result = run_tenant_aggregation(args.start_date, args.end_date)

    print("\nTenant Aggregation Report:")
    print(f"  Status: {result['status']}")
    print(f"  Periods Reset: {result['periods_reset']}")
    print(f"  Records Aggregated: {result['records_aggregated']}")
    print(f"  Tenants Updated: {result['tenants_updated']}")

    if result.get("quality_report"):
        print(f"  Data Quality: {result['quality_report']['quality_score']}%")
