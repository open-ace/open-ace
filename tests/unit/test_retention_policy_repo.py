"""
Unit tests for Retention Policy Repository.

Issue #2188: Policy inheritance and persistence.
"""

import pytest

from app.repositories.database import Database
from app.repositories.retention_policy_repo import RetentionPolicyRepository


class TestRetentionPolicyRepository:
    """Test retention policy repository operations."""

    @pytest.fixture
    def db(self):
        """Create test database."""
        db = Database()
        # Create retention_policies table directly for testing
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER,
                    data_type TEXT NOT NULL,
                    retention_days INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    version INTEGER DEFAULT 1,
                    archive_target TEXT,
                    archive_config TEXT,
                    anonymize_fields TEXT,
                    backup_before_anonymize INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INTEGER,
                    updated_by INTEGER,
                    UNIQUE(tenant_id, data_type, version)
                )
            """
            )
            # Clean table before test
            cursor.execute("DELETE FROM retention_policies")
            conn.commit()
        yield db
        # Cleanup: delete all data after each test
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM retention_policies")
            conn.commit()

    @pytest.fixture
    def repo(self, db):
        """Create repository instance."""
        return RetentionPolicyRepository(db)

    def test_create_policy(self, repo):
        """Test creating a retention policy."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )

        assert policy is not None
        assert policy["tenant_id"] == 1
        assert policy["data_type"] == "audit_logs"
        assert policy["retention_days"] == 90
        assert policy["action"] == "delete"
        assert policy["version"] == 1
        assert policy["enabled"] == 1  # SQLite returns 1/0, not True/False

    def test_create_archive_policy(self, repo):
        """Test creating an archive policy with required config."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="usage_data",
            retention_days=365,
            action="archive",
            archive_target="local_file",
            archive_config={"path": "/archive"},
            created_by=1,
        )

        assert policy is not None
        assert policy["action"] == "archive"
        assert policy["archive_target"] == "local_file"

    def test_create_archive_policy_missing_target(self, repo):
        """Test that archive policy requires archive_target."""
        with pytest.raises(ValueError, match="archive_target is required"):
            repo.create_policy(
                tenant_id=1,
                data_type="usage_data",
                retention_days=365,
                action="archive",
                created_by=1,
            )

    def test_create_anonymize_policy(self, repo):
        """Test creating an anonymize policy."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="messages",
            retention_days=90,
            action="anonymize",
            anonymize_fields={"username": "hash", "ip_address": "mask"},
            created_by=1,
        )

        assert policy is not None
        assert policy["action"] == "anonymize"
        assert policy["anonymize_fields"]["username"] == "hash"

    def test_create_anonymize_policy_missing_fields(self, repo):
        """Test that anonymize policy requires anonymize_fields."""
        with pytest.raises(ValueError, match="anonymize_fields is required"):
            repo.create_policy(
                tenant_id=1,
                data_type="messages",
                retention_days=90,
                action="anonymize",
                created_by=1,
            )

    def test_policy_inheritance_tenant_over_global(self, repo):
        """Test that tenant policy takes priority over global."""
        # Create global policy
        repo.create_policy(
            tenant_id=None,
            data_type="audit_logs",
            retention_days=30,
            action="delete",
            created_by=1,
        )

        # Create tenant policy
        repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )

        # Get policy for tenant 1 - should get tenant policy
        policy = repo.get_policy(tenant_id=1, data_type="audit_logs")
        assert policy is not None
        assert policy["retention_days"] == 90
        assert policy["policy_source"] == "tenant"

    def test_policy_inheritance_global_fallback(self, repo):
        """Test that global policy is used when tenant policy doesn't exist."""
        # Create global policy
        repo.create_policy(
            tenant_id=None,
            data_type="audit_logs",
            retention_days=30,
            action="delete",
            created_by=1,
        )

        # Get policy for tenant 1 - should get global policy
        policy = repo.get_policy(tenant_id=1, data_type="audit_logs")
        assert policy is not None
        assert policy["retention_days"] == 30
        assert policy["policy_source"] == "global"

    def test_policy_not_configured(self, repo):
        """Test that None is returned when policy doesn't exist."""
        policy = repo.get_policy(tenant_id=1, data_type="nonexistent")
        assert policy is None

    def test_update_policy(self, repo):
        """Test updating a policy."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )

        # Update policy
        updated = repo.update_policy(policy["id"], retention_days=120, updated_by=1)

        assert updated is not None
        assert updated["retention_days"] == 120

    def test_disable_policy(self, repo):
        """Test disabling a policy."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )

        # Disable policy
        updated = repo.update_policy(policy["id"], enabled=False, updated_by=1)
        assert updated["enabled"] == 0  # SQLite returns 1/0, not True/False

        # Policy should not be returned when getting active policy
        active_policy = repo.get_policy(tenant_id=1, data_type="audit_logs")
        assert active_policy is None

    def test_version_management(self, repo):
        """Test that policy versions are managed correctly."""
        # Create first version
        policy1 = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )
        assert policy1["version"] == 1

        # Create second version (different retention_days)
        policy2 = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=120,
            action="delete",
            created_by=1,
        )
        assert policy2["version"] == 2

        # Latest version should be returned
        latest = repo.get_policy(tenant_id=1, data_type="audit_logs")
        assert latest["version"] == 2
        assert latest["retention_days"] == 120

    def test_delete_policy(self, repo):
        """Test deleting a policy."""
        policy = repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )

        # Delete policy
        result = repo.delete_policy(policy["id"])
        assert result is True

        # Policy should not exist
        deleted = repo.get_policy_by_id(policy["id"])
        assert deleted is None

    def test_get_all_policies(self, repo):
        """Test getting all policies for a tenant."""
        # Create multiple policies
        repo.create_policy(
            tenant_id=1,
            data_type="audit_logs",
            retention_days=90,
            action="delete",
            created_by=1,
        )
        repo.create_policy(
            tenant_id=1,
            data_type="sessions",
            retention_days=30,
            action="delete",
            created_by=1,
        )

        # Get all policies
        policies = repo.get_all_policies(tenant_id=1)
        assert len(policies) == 2

    def test_global_policies(self, repo):
        """Test getting global policies."""
        # Create global policy
        repo.create_policy(
            tenant_id=None,
            data_type="audit_logs",
            retention_days=30,
            action="delete",
            created_by=1,
        )

        # Get global policies
        policies = repo.get_all_policies(tenant_id=None)
        assert len(policies) == 1
        assert policies[0]["tenant_id"] is None
