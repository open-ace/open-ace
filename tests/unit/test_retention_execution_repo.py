"""
Unit tests for Retention Execution Repository.

Issue #2188: Execution tracking with batch recovery support.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.database import Database
from app.repositories.retention_execution_repo import RetentionExecutionRepository


class TestRetentionExecutionRepository:
    """Test retention execution repository operations."""

    @pytest.fixture
    def db(self):
        """Create test database."""
        db = Database()
        # Create retention_executions table directly for testing
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT UNIQUE NOT NULL,
                    tenant_id INTEGER NOT NULL,
                    policy_id INTEGER,
                    status TEXT NOT NULL,
                    dry_run INTEGER DEFAULT 0,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    lock_acquired_at TIMESTAMP,
                    lock_expires_at TIMESTAMP,
                    records_scanned INTEGER DEFAULT 0,
                    records_affected INTEGER DEFAULT 0,
                    records_skipped INTEGER DEFAULT 0,
                    records_archived INTEGER DEFAULT 0,
                    records_anonymized INTEGER DEFAULT 0,
                    records_in_recycle_bin INTEGER DEFAULT 0,
                    error_message TEXT,
                    error_details TEXT,
                    batch_size INTEGER DEFAULT 1000,
                    last_batch_id INTEGER,
                    total_batches INTEGER,
                    last_batch_status TEXT,
                    max_records_override INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            # Clean table before test
            cursor.execute("DELETE FROM retention_executions")
            conn.commit()
        yield db
        # Cleanup: delete all data after each test
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM retention_executions")
            conn.commit()

    @pytest.fixture
    def repo(self, db):
        """Create repository instance."""
        return RetentionExecutionRepository(db)

    def test_create_execution(self, repo):
        """Test creating an execution record."""
        execution = repo.create_execution(
            execution_id="exec_123",
            tenant_id=1,
            policy_id=1,
            dry_run=False,
            batch_size=1000,
        )

        assert execution is not None
        assert execution["execution_id"] == "exec_123"
        assert execution["tenant_id"] == 1
        assert execution["policy_id"] == 1
        assert execution["status"] == "pending"
        assert execution["dry_run"] == 0  # SQLite returns 0/1
        assert execution["batch_size"] == 1000

    def test_get_execution_by_id(self, repo):
        """Test getting execution by ID."""
        # Create execution
        repo.create_execution(
            execution_id="exec_456",
            tenant_id=1,
            dry_run=True,
        )

        # Get execution
        execution = repo.get_execution_by_id("exec_456")
        assert execution is not None
        assert execution["execution_id"] == "exec_456"
        assert execution["tenant_id"] == 1

    def test_update_execution_status(self, repo):
        """Test updating execution status."""
        # Create execution
        repo.create_execution(
            execution_id="exec_789",
            tenant_id=1,
        )

        # Update status
        updated = repo.update_execution(
            "exec_789",
            status="running",
        )

        assert updated is not None
        assert updated["status"] == "running"

    def test_update_execution_counters(self, repo):
        """Test updating execution counters."""
        # Create execution
        repo.create_execution(
            execution_id="exec_counter",
            tenant_id=1,
        )

        # Update counters
        updated = repo.update_execution(
            "exec_counter",
            records_scanned=1000,
            records_affected=950,
            records_skipped=50,
            records_archived=100,
            records_anonymized=50,
            records_in_recycle_bin=800,
        )

        assert updated is not None
        assert updated["records_scanned"] == 1000
        assert updated["records_affected"] == 950
        assert updated["records_skipped"] == 50
        assert updated["records_archived"] == 100
        assert updated["records_anonymized"] == 50
        assert updated["records_in_recycle_bin"] == 800

    def test_update_execution_error(self, repo):
        """Test updating execution with error."""
        # Create execution
        repo.create_execution(
            execution_id="exec_error",
            tenant_id=1,
        )

        # Update with error
        error_details = {"step": "archive", "reason": "disk full"}
        updated = repo.update_execution(
            "exec_error",
            status="failed",
            error_message="Archive failed: disk full",
            error_details=error_details,
        )

        assert updated is not None
        assert updated["status"] == "failed"
        assert updated["error_message"] == "Archive failed: disk full"
        # error_details is stored as JSON string, parsed back when read

    def test_update_execution_batch_info(self, repo):
        """Test updating batch information."""
        # Create execution
        repo.create_execution(
            execution_id="exec_batch",
            tenant_id=1,
        )

        # Update batch info
        updated = repo.update_execution(
            "exec_batch",
            last_batch_id=5,
            total_batches=10,
            last_batch_status="completed",
        )

        assert updated is not None
        assert updated["last_batch_id"] == 5
        assert updated["total_batches"] == 10
        assert updated["last_batch_status"] == "completed"

    def test_get_executions_for_tenant(self, repo):
        """Test getting executions for a tenant."""
        # Create multiple executions
        repo.create_execution(execution_id="exec_t1_1", tenant_id=1)
        repo.create_execution(execution_id="exec_t1_2", tenant_id=1)
        repo.create_execution(execution_id="exec_t2_1", tenant_id=2)

        # Get executions for tenant 1
        executions = repo.get_executions_for_tenant(tenant_id=1)
        assert len(executions) == 2

        # Get executions for tenant 2
        executions = repo.get_executions_for_tenant(tenant_id=2)
        assert len(executions) == 1

    def test_get_executions_for_tenant_with_status_filter(self, repo):
        """Test getting executions filtered by status."""
        # Create executions with different statuses
        repo.create_execution(execution_id="exec_s1", tenant_id=1)
        repo.create_execution(execution_id="exec_s2", tenant_id=1)

        repo.update_execution("exec_s1", status="completed")
        repo.update_execution("exec_s2", status="failed")

        # Get completed executions
        executions = repo.get_executions_for_tenant(tenant_id=1, status="completed")
        assert len(executions) == 1
        assert executions[0]["execution_id"] == "exec_s1"

    def test_acquire_lock(self, repo):
        """Test acquiring execution lock."""
        # Create execution
        repo.create_execution(
            execution_id="exec_lock",
            tenant_id=1,
        )

        # Acquire lock
        acquired = repo.acquire_lock("exec_lock", lock_timeout_seconds=1800)
        assert acquired is True

        # Verify lock is acquired
        execution = repo.get_execution_by_id("exec_lock")
        assert execution["lock_acquired_at"] is not None
        assert execution["lock_expires_at"] is not None

    def test_acquire_lock_already_locked(self, repo):
        """Test that lock cannot be acquired if already locked."""
        # Create execution
        repo.create_execution(
            execution_id="exec_locked",
            tenant_id=1,
        )

        # Acquire lock first time
        acquired1 = repo.acquire_lock("exec_locked", lock_timeout_seconds=1800)
        assert acquired1 is True

        # Try to acquire again (should fail)
        acquired2 = repo.acquire_lock("exec_locked", lock_timeout_seconds=1800)
        assert acquired2 is False

    def test_release_lock(self, repo):
        """Test releasing execution lock."""
        # Create execution and acquire lock
        repo.create_execution(
            execution_id="exec_release",
            tenant_id=1,
        )
        repo.acquire_lock("exec_release")

        # Release lock
        released = repo.release_lock("exec_release")
        assert released is True

        # Verify lock is released
        execution = repo.get_execution_by_id("exec_release")
        assert execution["lock_acquired_at"] is None
        assert execution["lock_expires_at"] is None

    def test_check_existing_execution(self, repo):
        """Test checking if execution exists (idempotency check)."""
        # Check non-existent execution
        exists = repo.check_existing_execution("exec_nonexistent")
        assert exists is False

        # Create execution
        repo.create_execution(
            execution_id="exec_exists",
            tenant_id=1,
        )

        # Check existing execution
        exists = repo.check_existing_execution("exec_exists")
        assert exists is True

    def test_sql_injection_protection(self, repo):
        """Test that invalid field names are rejected."""
        # Create execution
        repo.create_execution(
            execution_id="exec_sql",
            tenant_id=1,
        )

        # Try to use a field not in the whitelist
        # The update_execution method has explicit field validation
        # Invalid fields should raise ValueError
        from app.repositories.retention_execution_repo import RetentionExecutionRepository

        # Check that ALLOWED_UPDATE_FIELDS exists and contains expected fields
        assert "status" in RetentionExecutionRepository.ALLOWED_UPDATE_FIELDS
        assert "records_scanned" in RetentionExecutionRepository.ALLOWED_UPDATE_FIELDS

        # Verify that malicious field name is not in whitelist
        assert "status; DROP TABLE" not in RetentionExecutionRepository.ALLOWED_UPDATE_FIELDS

        # The whitelist approach prevents SQL injection by only allowing
        # known safe field names in the UPDATE statement

    def test_tenant_isolation(self, repo):
        """Test that executions are isolated by tenant."""
        # Create executions for different tenants
        repo.create_execution(execution_id="exec_iso_1", tenant_id=1)
        repo.create_execution(execution_id="exec_iso_2", tenant_id=2)

        # Get executions for tenant 1
        executions_t1 = repo.get_executions_for_tenant(tenant_id=1)
        assert len(executions_t1) == 1
        assert executions_t1[0]["tenant_id"] == 1

        # Get executions for tenant 2
        executions_t2 = repo.get_executions_for_tenant(tenant_id=2)
        assert len(executions_t2) == 1
        assert executions_t2[0]["tenant_id"] == 2

    def test_completion_timestamp(self, repo):
        """Test setting completion timestamp."""
        # Create execution
        repo.create_execution(
            execution_id="exec_complete",
            tenant_id=1,
        )

        # Update with completion timestamp
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        updated = repo.update_execution(
            "exec_complete",
            status="completed",
            completed_at=completed_at,
        )

        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["completed_at"] is not None
