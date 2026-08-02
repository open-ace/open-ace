"""
Unit tests for Legal Hold Repository.

Issue #2188: Legal hold mechanism for preventing data deletion.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.database import Database
from app.repositories.legal_hold_repo import LegalHoldRepository


class TestLegalHoldRepository:
    """Test legal hold repository operations."""

    @pytest.fixture
    def db(self):
        """Create test database."""
        db = Database()
        # Create legal_holds table directly for testing
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS legal_holds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    hold_type TEXT NOT NULL,
                    data_type TEXT,
                    record_id TEXT,
                    reason TEXT NOT NULL,
                    case_reference TEXT,
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    lifted_by INTEGER,
                    lifted_at TIMESTAMP,
                    lift_reason TEXT
                )
            """)
            # Clean table before test
            cursor.execute("DELETE FROM legal_holds")
            conn.commit()
        yield db
        # Cleanup: delete all data after each test
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM legal_holds")
            conn.commit()

    @pytest.fixture
    def repo(self, db):
        """Create repository instance."""
        return LegalHoldRepository(db)

    def test_create_global_hold(self, repo):
        """Test creating a global legal hold."""
        hold = repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Litigation hold for case #123",
            created_by=1,
        )

        assert hold is not None
        assert hold["tenant_id"] == 1
        assert hold["hold_type"] == "global"
        assert hold["reason"] == "Litigation hold for case #123"
        assert hold["lifted_at"] is None

    def test_create_data_type_hold(self, repo):
        """Test creating a data type level hold."""
        hold = repo.create_hold(
            tenant_id=1,
            hold_type="data_type",
            data_type="audit_logs",
            reason="Audit logs under review",
            created_by=1,
        )

        assert hold is not None
        assert hold["hold_type"] == "data_type"
        assert hold["data_type"] == "audit_logs"

    def test_create_record_hold(self, repo):
        """Test creating a record level hold."""
        hold = repo.create_hold(
            tenant_id=1,
            hold_type="record",
            record_id="audit_log_123",
            reason="Specific record under investigation",
            created_by=1,
        )

        assert hold is not None
        assert hold["hold_type"] == "record"
        assert hold["record_id"] == "audit_log_123"

    def test_create_hold_missing_required_params(self, repo):
        """Test that creating hold without required params raises error."""
        with pytest.raises(ValueError, match="data_type is required"):
            repo.create_hold(
                tenant_id=1,
                hold_type="data_type",
                reason="Test",
                created_by=1,
            )

        with pytest.raises(ValueError, match="record_id is required"):
            repo.create_hold(
                tenant_id=1,
                hold_type="record",
                reason="Test",
                created_by=1,
            )

    def test_check_global_hold(self, repo):
        """Test checking global hold."""
        # Create global hold
        repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Global hold",
            created_by=1,
        )

        # Check hold
        is_held, reason = repo.check_hold(tenant_id=1)
        assert is_held is True
        assert "Global legal hold active" in reason

    def test_check_data_type_hold(self, repo):
        """Test checking data type level hold."""
        # Create data type hold
        repo.create_hold(
            tenant_id=1,
            hold_type="data_type",
            data_type="audit_logs",
            reason="Audit logs hold",
            created_by=1,
        )

        # Check hold for audit_logs
        is_held, reason = repo.check_hold(tenant_id=1, data_type="audit_logs")
        assert is_held is True
        assert "audit_logs" in reason

        # Check no hold for other data type
        is_held, reason = repo.check_hold(tenant_id=1, data_type="sessions")
        assert is_held is False
        assert reason is None

    def test_check_record_hold(self, repo):
        """Test checking record level hold."""
        # Create record hold
        repo.create_hold(
            tenant_id=1,
            hold_type="record",
            record_id="record_123",
            reason="Record hold",
            created_by=1,
        )

        # Check hold for blocked record
        is_held, reason = repo.check_hold(
            tenant_id=1, record_ids=["record_123", "record_456"]
        )
        assert is_held is True
        assert "record_123" in reason

        # Check no hold for other records
        is_held, reason = repo.check_hold(tenant_id=1, record_ids=["record_999"])
        assert is_held is False

    def test_lift_hold(self, repo):
        """Test lifting a legal hold."""
        # Create hold
        hold = repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Temporary hold",
            created_by=1,
        )

        # Verify hold is active
        active_holds = repo.get_active_holds(tenant_id=1)
        assert len(active_holds) == 1

        # Lift hold
        lifted = repo.lift_hold(hold["id"], lifted_by=1, lift_reason="Case closed")

        assert lifted is not None
        assert lifted["lifted_at"] is not None
        assert lifted["lifted_by"] == 1
        assert lifted["lift_reason"] == "Case closed"

        # Verify hold is no longer active
        active_holds = repo.get_active_holds(tenant_id=1)
        assert len(active_holds) == 0

    def test_hold_expiry(self, repo):
        """Test that expired holds are not active."""
        # Create hold that expires in 1 second
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            seconds=1
        )
        repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Expiring hold",
            created_by=1,
            expires_at=expires_at,
        )

        # Verify hold is active initially
        active_holds = repo.get_active_holds(tenant_id=1)
        assert len(active_holds) == 1

    def test_tenant_isolation(self, repo):
        """Test that holds are isolated by tenant."""
        # Create hold for tenant 1
        repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Tenant 1 hold",
            created_by=1,
        )

        # Create hold for tenant 2
        repo.create_hold(
            tenant_id=2,
            hold_type="global",
            reason="Tenant 2 hold",
            created_by=2,
        )

        # Verify tenant 1 has hold
        is_held, reason = repo.check_hold(tenant_id=1)
        assert is_held is True
        # Global hold returns generic message, not the specific reason
        assert "Global legal hold active" in reason

        # Verify tenant 2 has hold
        is_held, reason = repo.check_hold(tenant_id=2)
        assert is_held is True
        assert "Global legal hold active" in reason

        # Verify tenant 3 has no hold
        is_held, reason = repo.check_hold(tenant_id=3)
        assert is_held is False
        assert reason is None

    def test_multiple_hold_types_priority(self, repo):
        """Test that global hold takes priority."""
        # Create multiple holds
        repo.create_hold(
            tenant_id=1,
            hold_type="global",
            reason="Global hold",
            created_by=1,
        )
        repo.create_hold(
            tenant_id=1,
            hold_type="data_type",
            data_type="audit_logs",
            reason="Data type hold",
            created_by=1,
        )

        # Check hold - global should take priority
        is_held, reason = repo.check_hold(tenant_id=1)
        assert is_held is True
        assert "Global" in reason