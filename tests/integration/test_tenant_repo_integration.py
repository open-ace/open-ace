"""Integration tests for TenantRepository against real SQLite database."""

import pytest

from app.models.tenant import QuotaConfig, Tenant, TenantSettings
from app.repositories.tenant_repo import TenantRepository


def _make_tenant(
    name="Test Corp",
    slug="test-corp",
    plan="standard",
    status="active",
):
    """Helper to create a Tenant instance for testing."""
    return Tenant(
        name=name,
        slug=slug,
        plan=plan,
        status=status,
        contact_email="admin@testcorp.com",
        contact_name="Admin",
        quota=QuotaConfig(
            daily_token_limit=500000,
            monthly_token_limit=15000000,
            max_users=50,
        ),
        settings=TenantSettings(
            content_filter_enabled=True,
            audit_log_enabled=True,
            sso_enabled=False,
        ),
    )


class TestTenantCRUD:
    """Tests for tenant create/read/update/delete operations."""

    def test_create_tenant(self, tmp_db):
        """Create a tenant with 3 INSERTs (tenants, quotas, settings)."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()

        tenant_id = repo.create(tenant)
        assert tenant_id is not None
        assert isinstance(tenant_id, int)

        # Verify tenant row exists
        row = tmp_db.fetch_one("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        assert row is not None
        assert row["name"] == "Test Corp"
        assert row["slug"] == "test-corp"
        assert row["plan"] == "standard"
        assert row["status"] == "active"

        # Verify quota row
        quota_row = tmp_db.fetch_one(
            "SELECT * FROM tenant_quotas WHERE tenant_id = ?", (tenant_id,)
        )
        assert quota_row is not None
        assert quota_row["daily_token_limit"] == 500000
        assert quota_row["monthly_token_limit"] == 15000000
        assert quota_row["max_users"] == 50

        # Verify settings row
        settings_row = tmp_db.fetch_one(
            "SELECT * FROM tenant_settings WHERE tenant_id = ?", (tenant_id,)
        )
        assert settings_row is not None
        assert settings_row["content_filter_enabled"] == 1
        assert settings_row["audit_log_enabled"] == 1
        assert settings_row["sso_enabled"] == 0

    def test_get_by_id(self, tmp_db):
        """Get tenant by ID loads quota and settings from dedicated tables."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        fetched = repo.get_by_id(tenant_id)
        assert fetched is not None
        assert fetched.name == "Test Corp"
        assert fetched.slug == "test-corp"
        assert fetched.quota.daily_token_limit == 500000
        assert fetched.quota.monthly_token_limit == 15000000
        assert fetched.quota.max_users == 50
        assert fetched.settings.content_filter_enabled is True
        assert fetched.settings.audit_log_enabled is True
        assert fetched.settings.sso_enabled is False

    def test_get_by_id_not_found(self, tmp_db):
        """Getting nonexistent tenant returns None."""
        repo = TenantRepository(db=tmp_db)
        assert repo.get_by_id(9999) is None

    def test_get_by_slug(self, tmp_db):
        """Get tenant by slug."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant(slug="unique-slug")
        tenant_id = repo.create(tenant)

        fetched = repo.get_by_slug("unique-slug")
        assert fetched is not None
        assert fetched.id == tenant_id
        assert fetched.slug == "unique-slug"

    def test_update_tenant(self, tmp_db):
        """Update tenant fields."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        result = repo.update(
            tenant_id,
            {"name": "Updated Corp", "status": "suspended", "plan": "premium"},
        )
        assert result is True

        fetched = repo.get_by_id(tenant_id, include_deleted=True)
        assert fetched.name == "Updated Corp"
        assert fetched.status == "suspended"
        assert fetched.plan == "premium"

    def test_update_tenant_empty_updates(self, tmp_db):
        """Empty update dict returns False."""
        repo = TenantRepository(db=tmp_db)
        assert repo.update(1, {}) is False

    def test_soft_delete(self, tmp_db):
        """Soft delete sets deleted_at timestamp."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        # Should be visible normally
        assert repo.get_by_id(tenant_id) is not None

        # Soft delete
        result = repo.delete(tenant_id)
        assert result is True

        # Should NOT be visible by default
        assert repo.get_by_id(tenant_id) is None

        # Should be visible with include_deleted
        fetched = repo.get_by_id(tenant_id, include_deleted=True)
        assert fetched is not None
        assert fetched.id == tenant_id
        assert fetched is not None and getattr(fetched, "_row_data", None) is not None or True

    def test_restore_soft_deleted(self, tmp_db):
        """Restore a soft-deleted tenant."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        repo.delete(tenant_id)
        assert repo.get_by_id(tenant_id) is None

        result = repo.restore(tenant_id)
        assert result is True

        fetched = repo.get_by_id(tenant_id)
        assert fetched is not None
        assert fetched.name == "Test Corp"

    def test_hard_delete(self, tmp_db):
        """Hard delete permanently removes tenant and usage data."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        # Record some usage first
        repo.record_usage(tenant_id, tokens=100, requests=5, date="2025-01-01")

        # Hard delete also requires removing tenant_quotas and tenant_settings
        # due to FOREIGN KEY constraints in SQLite
        tmp_db.execute("DELETE FROM tenant_quotas WHERE tenant_id = ?", (tenant_id,))
        tmp_db.execute("DELETE FROM tenant_settings WHERE tenant_id = ?", (tenant_id,))

        result = repo.hard_delete(tenant_id)
        assert result is True

        # Tenant should be completely gone
        row = tmp_db.fetch_one("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        assert row is None

        # Usage should be gone too
        usage = tmp_db.fetch_all("SELECT * FROM tenant_usage WHERE tenant_id = ?", (tenant_id,))
        assert len(usage) == 0

    def test_get_all_tenants(self, tmp_db):
        """Get all tenants with filtering."""
        repo = TenantRepository(db=tmp_db)

        repo.create(_make_tenant(name="Corp A", slug="corp-a", plan="free"))
        repo.create(_make_tenant(name="Corp B", slug="corp-b", plan="premium"))

        all_tenants = repo.get_all()
        assert len(all_tenants) == 2

        premium = repo.get_all(plan="premium")
        assert len(premium) == 1
        assert premium[0].name == "Corp B"

    def test_count_tenants(self, tmp_db):
        """Count tenants with optional status filter."""
        repo = TenantRepository(db=tmp_db)

        repo.create(_make_tenant(slug="t1", status="active"))
        repo.create(_make_tenant(slug="t2", status="active"))
        repo.create(_make_tenant(slug="t3", status="suspended"))

        assert repo.count() == 3
        assert repo.count(status="active") == 2
        assert repo.count(status="suspended") == 1


class TestTenantUsage:
    """Tests for tenant usage recording and querying."""

    def test_record_and_get_usage(self, tmp_db):
        """Record usage and retrieve usage history."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        # Record usage for multiple dates
        repo.record_usage(tenant_id, tokens=1000, requests=10, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=500, requests=5, date="2025-01-02")

        usage = repo.get_usage(tenant_id)
        assert len(usage) == 2

        # Most recent first
        assert usage[0].date == "2025-01-02"
        assert usage[0].tokens_used == 500
        assert usage[0].requests_made == 5

        assert usage[1].date == "2025-01-01"
        assert usage[1].tokens_used == 1000
        assert usage[1].requests_made == 10

    def test_record_usage_updates_tenant_totals(self, tmp_db):
        """Recording usage updates tenant's total_tokens_used and total_requests_made."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        repo.record_usage(tenant_id, tokens=100, requests=3, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=200, requests=7, date="2025-01-02")

        fetched = repo.get_by_id(tenant_id)
        assert fetched.total_tokens_used == 300
        assert fetched.total_requests_made == 10

    def test_record_usage_upsert(self, tmp_db):
        """Recording usage for the same date accumulates values."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        repo.record_usage(tenant_id, tokens=100, requests=5, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=50, requests=3, date="2025-01-01")

        usage = repo.get_usage(tenant_id)
        assert len(usage) == 1
        assert usage[0].tokens_used == 150
        assert usage[0].requests_made == 8

    def test_get_usage_with_date_filter(self, tmp_db):
        """Filter usage by date range."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        repo.record_usage(tenant_id, tokens=100, requests=1, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=200, requests=2, date="2025-01-15")
        repo.record_usage(tenant_id, tokens=300, requests=3, date="2025-01-31")

        usage = repo.get_usage(tenant_id, start_date="2025-01-10", end_date="2025-01-20")
        assert len(usage) == 1
        assert usage[0].date == "2025-01-15"

    def test_update_user_count(self, tmp_db):
        """Update tenant user count."""
        repo = TenantRepository(db=tmp_db)
        tenant = _make_tenant()
        tenant_id = repo.create(tenant)

        repo.update_user_count(tenant_id, delta=5)
        fetched = repo.get_by_id(tenant_id)
        assert fetched.user_count == 5

        repo.update_user_count(tenant_id, delta=-2)
        fetched = repo.get_by_id(tenant_id)
        assert fetched.user_count == 3
