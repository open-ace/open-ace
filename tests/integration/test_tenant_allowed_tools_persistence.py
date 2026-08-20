"""Regression tests for tenant ``allowed_tools`` persistence (Issue #2819)."""

from app.models.tenant import Tenant, TenantSettings
from app.repositories.database import Database
from app.repositories.tenant_repo import TenantRepository
from app.services.tenant_service import TenantService


def _create_tenant(db: Database, slug: str) -> int:
    tenant_id = TenantRepository(db=db).create(
        Tenant(
            name="Allowed Tools Regression",
            slug=slug,
            contact_email="admin@example.com",
            settings=TenantSettings(),
        )
    )
    assert tenant_id is not None
    return tenant_id


def _reopened_service(db_url: str) -> TenantService:
    return TenantService(tenant_repo=TenantRepository(db=Database(db_url=db_url)))


def test_allowed_tools_removal_survives_repository_reopen(tmp_db, monkeypatch):
    """Removing one tool remains visible after reopening the database."""
    import app.repositories.tenant_repo as tenant_repo_module

    monkeypatch.setattr(tenant_repo_module, "is_postgresql", lambda: False)
    tenant_id = _create_tenant(tmp_db, "issue-2819-remove-one")
    service = TenantService(tenant_repo=TenantRepository(db=tmp_db))
    requested_tools = ["claude", "qwen", "openclaw", "codex"]

    result = service.update_settings(
        tenant_id,
        {"allowed_tools": requested_tools},
    )

    assert result.success is True
    reopened = _reopened_service(tmp_db.db_url).get_tenant(tenant_id)
    assert reopened is not None
    assert reopened.settings.allowed_tools == requested_tools


def test_empty_allowed_tools_with_nondefault_setting_survives_reopen(tmp_db, monkeypatch):
    """An empty list is a persisted value, even beside non-default settings."""
    import app.repositories.tenant_repo as tenant_repo_module

    monkeypatch.setattr(tenant_repo_module, "is_postgresql", lambda: False)
    tenant_id = _create_tenant(tmp_db, "issue-2819-empty-list")
    service = TenantService(tenant_repo=TenantRepository(db=tmp_db))

    result = service.update_settings(
        tenant_id,
        {"allowed_tools": [], "custom_branding": True},
    )

    assert result.success is True
    reopened = _reopened_service(tmp_db.db_url).get_tenant(tenant_id)
    assert reopened is not None
    assert reopened.settings.allowed_tools == []
    assert reopened.settings.custom_branding is True
