"""Integration tests for TenantRepository against real PostgreSQL database."""

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
    """Tests for tenant CRUD via PostgreSQL RETURNING path."""

    def test_create_tenant(self, pg_db):
        """Create uses RETURNING id for PostgreSQL."""
        repo = TenantRepository(db=pg_db)
        tenant = _make_tenant()

        tenant_id = repo.create(tenant)
        assert tenant_id is not None
        assert isinstance(tenant_id, int)

        row = pg_db.fetch_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
        assert row is not None
        assert row["name"] == "Test Corp"
        assert row["slug"] == "test-corp"

        quota_row = pg_db.fetch_one(
            "SELECT * FROM tenant_quotas WHERE tenant_id = %s", (tenant_id,)
        )
        assert quota_row is not None
        assert quota_row["daily_token_limit"] == 500000

        settings_row = pg_db.fetch_one(
            "SELECT * FROM tenant_settings WHERE tenant_id = %s", (tenant_id,)
        )
        assert settings_row is not None
        assert settings_row["content_filter_enabled"] is True
        assert settings_row["sso_enabled"] is False

    def test_get_by_id(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        fetched = repo.get_by_id(tenant_id)
        assert fetched is not None
        assert fetched.name == "Test Corp"
        assert fetched.quota.daily_token_limit == 500000
        assert fetched.settings.content_filter_enabled is True
        assert fetched.settings.sso_enabled is False

    def test_get_by_id_not_found(self, pg_db):
        repo = TenantRepository(db=pg_db)
        assert repo.get_by_id(9999) is None

    def test_get_by_slug(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant(slug="unique-slug"))

        fetched = repo.get_by_slug("unique-slug")
        assert fetched is not None
        assert fetched.id == tenant_id

    def test_update_tenant(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        result = repo.update(
            tenant_id,
            {"name": "Updated Corp", "status": "suspended", "plan": "premium"},
        )
        assert result is True

        fetched = repo.get_by_id(tenant_id, include_deleted=True)
        assert fetched.name == "Updated Corp"
        assert fetched.status == "suspended"

    def test_soft_delete(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        assert repo.get_by_id(tenant_id) is not None
        assert repo.delete(tenant_id) is True
        assert repo.get_by_id(tenant_id) is None
        assert repo.get_by_id(tenant_id, include_deleted=True) is not None

    def test_restore_soft_deleted(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        repo.delete(tenant_id)
        assert repo.restore(tenant_id) is True
        assert repo.get_by_id(tenant_id) is not None

    def test_hard_delete(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())
        repo.record_usage(tenant_id, tokens=100, requests=5, date="2025-01-01")

        result = repo.hard_delete(tenant_id)
        assert result is True

        row = pg_db.fetch_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
        assert row is None

        usage = pg_db.fetch_all("SELECT * FROM tenant_usage WHERE tenant_id = %s", (tenant_id,))
        assert len(usage) == 0

    def test_get_all_tenants(self, pg_db):
        repo = TenantRepository(db=pg_db)
        repo.create(_make_tenant(name="Corp A", slug="corp-a", plan="free"))
        repo.create(_make_tenant(name="Corp B", slug="corp-b", plan="premium"))

        assert len(repo.get_all()) == 2
        assert len(repo.get_all(plan="premium")) == 1

    def test_count_tenants(self, pg_db):
        repo = TenantRepository(db=pg_db)
        repo.create(_make_tenant(slug="t1", status="active"))
        repo.create(_make_tenant(slug="t2", status="active"))
        repo.create(_make_tenant(slug="t3", status="suspended"))

        assert repo.count() == 3
        assert repo.count(status="active") == 2


class TestTenantUsage:
    """Tests for tenant usage recording via PostgreSQL."""

    def test_record_and_get_usage(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        repo.record_usage(tenant_id, tokens=1000, requests=10, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=500, requests=5, date="2025-01-02")

        usage = repo.get_usage(tenant_id)
        assert len(usage) == 2
        assert usage[0].date == "2025-01-02"
        assert usage[1].date == "2025-01-01"

    def test_record_usage_upsert(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        repo.record_usage(tenant_id, tokens=100, requests=5, date="2025-01-01")
        repo.record_usage(tenant_id, tokens=50, requests=3, date="2025-01-01")

        usage = repo.get_usage(tenant_id)
        assert len(usage) == 1
        assert usage[0].tokens_used == 150
        assert usage[0].requests_made == 8

    def test_update_user_count(self, pg_db):
        repo = TenantRepository(db=pg_db)
        tenant_id = repo.create(_make_tenant())

        repo.update_user_count(tenant_id, delta=5)
        assert repo.get_by_id(tenant_id).user_count == 5

        repo.update_user_count(tenant_id, delta=-2)
        assert repo.get_by_id(tenant_id).user_count == 3
