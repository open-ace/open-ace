"""Unit tests for Tenant, TenantStatus, QuotaConfig, TenantSettings, and TenantUsage models."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.tenant import QuotaConfig, Tenant, TenantSettings, TenantStatus, TenantUsage


class TestTenantStatus:
    """Test TenantStatus enum."""

    def test_active_value(self):
        assert TenantStatus.ACTIVE.value == "active"

    def test_suspended_value(self):
        assert TenantStatus.SUSPENDED.value == "suspended"

    def test_trial_value(self):
        assert TenantStatus.TRIAL.value == "trial"

    def test_inactive_value(self):
        assert TenantStatus.INACTIVE.value == "inactive"

    def test_all_members(self):
        members = list(TenantStatus)
        assert len(members) == 4
        assert TenantStatus.ACTIVE in members
        assert TenantStatus.SUSPENDED in members
        assert TenantStatus.TRIAL in members
        assert TenantStatus.INACTIVE in members


class TestQuotaConfig:
    """Test QuotaConfig dataclass."""

    def test_create_with_defaults(self):
        qc = QuotaConfig()
        assert qc.daily_token_limit == 1_000_000
        assert qc.monthly_token_limit == 30_000_000
        assert qc.daily_request_limit == 10_000
        assert qc.monthly_request_limit == 300_000
        assert qc.max_users == 100
        assert qc.max_sessions_per_user == 5

    def test_create_with_custom_values(self):
        qc = QuotaConfig(
            daily_token_limit=500_000,
            monthly_token_limit=15_000_000,
            daily_request_limit=5_000,
            monthly_request_limit=150_000,
            max_users=50,
            max_sessions_per_user=3,
        )
        assert qc.daily_token_limit == 500_000
        assert qc.monthly_token_limit == 15_000_000
        assert qc.daily_request_limit == 5_000
        assert qc.monthly_request_limit == 150_000
        assert qc.max_users == 50
        assert qc.max_sessions_per_user == 3

    def test_to_dict(self):
        qc = QuotaConfig(max_users=200)
        d = qc.to_dict()
        assert d["daily_token_limit"] == 1_000_000
        assert d["monthly_token_limit"] == 30_000_000
        assert d["daily_request_limit"] == 10_000
        assert d["monthly_request_limit"] == 300_000
        assert d["max_users"] == 200
        assert d["max_sessions_per_user"] == 5

    def test_from_dict(self):
        data = {
            "daily_token_limit": 200_000,
            "monthly_token_limit": 6_000_000,
            "daily_request_limit": 2_000,
            "monthly_request_limit": 60_000,
            "max_users": 25,
            "max_sessions_per_user": 2,
        }
        qc = QuotaConfig.from_dict(data)
        assert qc.daily_token_limit == 200_000
        assert qc.monthly_token_limit == 6_000_000
        assert qc.daily_request_limit == 2_000
        assert qc.monthly_request_limit == 60_000
        assert qc.max_users == 25
        assert qc.max_sessions_per_user == 2

    def test_from_dict_partial_uses_defaults(self):
        data = {"max_users": 10}
        qc = QuotaConfig.from_dict(data)
        assert qc.max_users == 10
        assert qc.daily_token_limit == 1_000_000
        assert qc.monthly_token_limit == 30_000_000

    def test_from_dict_empty_uses_defaults(self):
        qc = QuotaConfig.from_dict({})
        assert qc.daily_token_limit == 1_000_000
        assert qc.max_users == 100


class TestTenantSettings:
    """Test TenantSettings dataclass."""

    def test_create_with_defaults(self):
        ts = TenantSettings()
        assert ts.allowed_tools == ["claude", "qwen", "openclaw", "codex", "zcode"]
        assert ts.content_filter_enabled is True
        assert ts.audit_log_enabled is True
        assert ts.audit_log_retention_days == 90
        assert ts.data_retention_days == 365
        assert ts.sso_enabled is False
        assert ts.sso_provider is None
        assert ts.custom_branding is False
        assert ts.branding_name is None
        assert ts.branding_logo_url is None

    def test_create_with_custom_values(self):
        ts = TenantSettings(
            allowed_tools=["claude"],
            content_filter_enabled=False,
            sso_enabled=True,
            sso_provider="okta",
            custom_branding=True,
            branding_name="MyCorp",
            branding_logo_url="https://example.com/logo.png",
        )
        assert ts.allowed_tools == ["claude"]
        assert ts.content_filter_enabled is False
        assert ts.sso_enabled is True
        assert ts.sso_provider == "okta"
        assert ts.custom_branding is True
        assert ts.branding_name == "MyCorp"
        assert ts.branding_logo_url == "https://example.com/logo.png"

    def test_to_dict(self):
        ts = TenantSettings(sso_enabled=True, sso_provider="azure")
        d = ts.to_dict()
        assert d["allowed_tools"] == ["claude", "qwen", "openclaw", "codex", "zcode"]
        assert d["content_filter_enabled"] is True
        assert d["audit_log_enabled"] is True
        assert d["audit_log_retention_days"] == 90
        assert d["data_retention_days"] == 365
        assert d["sso_enabled"] is True
        assert d["sso_provider"] == "azure"
        assert d["custom_branding"] is False
        assert d["branding_name"] is None
        assert d["branding_logo_url"] is None

    def test_from_dict(self):
        data = {
            "allowed_tools": ["qwen", "openclaw"],
            "content_filter_enabled": False,
            "audit_log_retention_days": 60,
            "data_retention_days": 180,
            "sso_enabled": True,
            "sso_provider": "google",
        }
        ts = TenantSettings.from_dict(data)
        assert ts.allowed_tools == ["qwen", "openclaw"]
        assert ts.content_filter_enabled is False
        assert ts.audit_log_retention_days == 60
        assert ts.data_retention_days == 180
        assert ts.sso_enabled is True
        assert ts.sso_provider == "google"

    def test_from_dict_empty_uses_defaults(self):
        ts = TenantSettings.from_dict({})
        assert ts.allowed_tools == ["claude", "qwen", "openclaw", "codex", "zcode"]
        assert ts.content_filter_enabled is True
        assert ts.audit_log_enabled is True
        assert ts.sso_enabled is False


class TestTenant:
    """Test Tenant dataclass."""

    def test_create_with_defaults(self):
        t = Tenant()
        assert t.id is None
        assert t.name == ""
        assert t.slug == ""
        assert t.status == "active"
        assert t.plan == "standard"
        assert t.contact_email == ""
        assert t.contact_phone is None
        assert t.contact_name is None
        assert isinstance(t.quota, QuotaConfig)
        assert isinstance(t.settings, TenantSettings)
        assert t.created_at is None
        assert t.updated_at is None
        assert t.trial_ends_at is None
        assert t.subscription_ends_at is None
        assert t.user_count == 0
        assert t.total_tokens_used == 0
        assert t.total_requests_made == 0

    def test_create_with_values(self):
        now = datetime(2025, 6, 1, 10, 0, 0)
        t = Tenant(
            id=1,
            name="Test Corp",
            slug="test-corp",
            status="active",
            plan="enterprise",
            contact_email="admin@test.com",
            contact_phone="555-0100",
            contact_name="Admin",
            created_at=now,
            user_count=25,
            total_tokens_used=1000000,
            total_requests_made=50000,
        )
        assert t.id == 1
        assert t.name == "Test Corp"
        assert t.slug == "test-corp"
        assert t.plan == "enterprise"
        assert t.contact_email == "admin@test.com"
        assert t.user_count == 25

    def test_is_active_true(self):
        t = Tenant(status="active")
        assert t.is_active() is True

    def test_is_active_false(self):
        t = Tenant(status="suspended")
        assert t.is_active() is False

    def test_is_active_trial_not_active(self):
        t = Tenant(status="trial")
        assert t.is_active() is False

    def test_is_trial_true(self):
        t = Tenant(
            status="trial",
            trial_ends_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        assert t.is_trial() is True

    def test_is_trial_false_not_trial_status(self):
        t = Tenant(status="active")
        assert t.is_trial() is False

    def test_is_trial_expired(self):
        t = Tenant(
            status="trial",
            trial_ends_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        )
        assert t.is_trial() is False

    def test_is_trial_no_end_date(self):
        t = Tenant(status="trial", trial_ends_at=None)
        assert t.is_trial() is True

    def test_is_subscription_valid_no_end_date(self):
        t = Tenant(subscription_ends_at=None)
        assert t.is_subscription_valid() is True

    def test_is_subscription_valid_future(self):
        t = Tenant(
            subscription_ends_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(days=30)
        )
        assert t.is_subscription_valid() is True

    def test_is_subscription_valid_expired(self):
        t = Tenant(
            subscription_ends_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        )
        assert t.is_subscription_valid() is False

    def test_can_add_users_within_limit(self):
        t = Tenant(user_count=95, quota=QuotaConfig(max_users=100))
        assert t.can_add_users(1) is True
        assert t.can_add_users(5) is True

    def test_can_add_users_at_limit(self):
        t = Tenant(user_count=95, quota=QuotaConfig(max_users=100))
        assert t.can_add_users(5) is True
        assert t.can_add_users(6) is False

    def test_can_add_users_over_limit(self):
        t = Tenant(user_count=99, quota=QuotaConfig(max_users=100))
        assert t.can_add_users(2) is False

    def test_can_add_users_default_additional(self):
        t = Tenant(user_count=99, quota=QuotaConfig(max_users=100))
        assert t.can_add_users() is True
        assert t.can_add_users(2) is False

    def test_to_dict(self):
        now = datetime(2025, 5, 1, 12, 0, 0)
        t = Tenant(
            id=10,
            name="MyCo",
            slug="myco",
            status="active",
            plan="premium",
            contact_email="info@myco.com",
            created_at=now,
            user_count=30,
        )
        d = t.to_dict()
        assert d["id"] == 10
        assert d["name"] == "MyCo"
        assert d["slug"] == "myco"
        assert d["status"] == "active"
        assert d["plan"] == "premium"
        assert d["contact_email"] == "info@myco.com"
        assert d["created_at"] == "2025-05-01T12:00:00"
        assert d["updated_at"] is None
        assert d["trial_ends_at"] is None
        assert d["subscription_ends_at"] is None
        assert d["user_count"] == 30
        assert isinstance(d["quota"], dict)
        assert isinstance(d["settings"], dict)

    def test_from_dict(self):
        data = {
            "id": 5,
            "name": "FromDict Corp",
            "slug": "fromdict",
            "status": "trial",
            "plan": "free",
            "contact_email": "test@fd.com",
            "created_at": "2025-04-01T08:00:00",
            "updated_at": "2025-04-10T10:00:00",
            "user_count": 10,
            "quota": {"max_users": 50},
            "settings": {"sso_enabled": True, "sso_provider": "okta"},
        }
        t = Tenant.from_dict(data)
        assert t.id == 5
        assert t.name == "FromDict Corp"
        assert t.status == "trial"
        assert t.plan == "free"
        assert t.created_at == datetime(2025, 4, 1, 8, 0, 0)
        assert t.updated_at == datetime(2025, 4, 10, 10, 0, 0)
        assert t.user_count == 10
        assert t.quota.max_users == 50
        assert t.settings.sso_enabled is True
        assert t.settings.sso_provider == "okta"

    def test_from_dict_defaults(self):
        t = Tenant.from_dict({})
        assert t.id is None
        assert t.name == ""
        assert t.slug == ""
        assert t.status == "active"
        assert t.plan == "standard"
        assert t.user_count == 0
        assert isinstance(t.quota, QuotaConfig)
        assert isinstance(t.settings, TenantSettings)

    def test_from_dict_empty_quota_and_settings(self):
        data = {"quota": {}, "settings": {}}
        t = Tenant.from_dict(data)
        assert t.quota.daily_token_limit == 1_000_000
        assert t.settings.content_filter_enabled is True

    def test_from_dict_no_quota_and_settings_keys(self):
        data = {"name": "NoQuota"}
        t = Tenant.from_dict(data)
        assert isinstance(t.quota, QuotaConfig)
        assert isinstance(t.settings, TenantSettings)

    def test_roundtrip_to_dict_from_dict(self):
        now = datetime(2025, 10, 1, 8, 0, 0)
        original = Tenant(
            id=20,
            name="RT Corp",
            slug="rt",
            status="active",
            plan="enterprise",
            contact_email="rt@test.com",
            created_at=now,
            user_count=15,
            total_tokens_used=50000,
        )
        d = original.to_dict()
        restored = Tenant.from_dict(d)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.slug == original.slug
        assert restored.status == original.status
        assert restored.plan == original.plan
        assert restored.contact_email == original.contact_email
        assert restored.user_count == original.user_count
        assert restored.total_tokens_used == original.total_tokens_used


class TestTenantUsage:
    """Test TenantUsage dataclass."""

    def test_create_with_required_fields(self):
        tu = TenantUsage(tenant_id=1, date="2025-01-01")
        assert tu.tenant_id == 1
        assert tu.date == "2025-01-01"
        assert tu.tokens_used == 0
        assert tu.requests_made == 0
        assert tu.active_users == 0
        assert tu.new_users == 0

    def test_create_with_values(self):
        tu = TenantUsage(
            tenant_id=5,
            date="2025-06-15",
            tokens_used=10000,
            requests_made=500,
            active_users=20,
            new_users=3,
        )
        assert tu.tenant_id == 5
        assert tu.date == "2025-06-15"
        assert tu.tokens_used == 10000
        assert tu.requests_made == 500
        assert tu.active_users == 20
        assert tu.new_users == 3

    def test_to_dict(self):
        tu = TenantUsage(
            tenant_id=10,
            date="2025-07-01",
            tokens_used=5000,
            requests_made=200,
            active_users=15,
            new_users=2,
        )
        d = tu.to_dict()
        assert d["tenant_id"] == 10
        assert d["date"] == "2025-07-01"
        assert d["tokens_used"] == 5000
        assert d["requests_made"] == 200
        assert d["active_users"] == 15
        assert d["new_users"] == 2
