"""Integration tests for tenant settings audit logging.

Issue #2790: 租户设置修改审计日志
"""

from unittest.mock import MagicMock, patch

import pytest

MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def app():
    """Create test Flask app."""
    from flask import Flask

    from app.routes.governance import governance_bp
    from app.routes.tenant import tenant_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tenant_bp)
    app.register_blueprint(governance_bp, url_prefix="/api")
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestTenantSettingsAuditAPI:
    """Integration tests for tenant settings audit API."""

    def test_audit_actions_includes_tenant_settings(self, client):
        """Test audit-actions API includes tenant_settings resource type."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get("/api/audit-actions", headers={"Authorization": "Bearer t"})

        assert resp.status_code == 200
        data = resp.get_json()

        # Verify tenant_settings is in system category resource_types
        system_category = next((c for c in data["categories"] if c["key"] == "system"), None)
        assert system_category is not None
        assert "tenant_settings" in system_category["resource_types"]

        # Verify system_config_change maps to tenant_settings
        assert "tenant_settings" in data["actionToResourceTypes"].get("system_config_change", [])


class TestTenantServiceUpdateSettingsIntegration:
    """Integration tests for TenantService.update_settings with audit."""

    def test_update_settings_returns_result_object(self):
        """Test update_settings returns UpdateSettingsResult."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService, UpdateSettingsResult

        # Create mock repo
        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        mock_repo.get_by_id.return_value = tenant
        mock_repo.update.return_value = True

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        # Verify result type
        assert isinstance(result, UpdateSettingsResult)
        assert result.success is True
        assert result.tenant_id == 1
        assert "content_filter_enabled" in result.changed_fields

    def test_update_settings_permission_denied_returns_error_result(self):
        """Test permission denied returns error result."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService, UpdateSettingsResult

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings())
        mock_repo.get_by_id.return_value = tenant

        service = TenantService(tenant_repo=mock_repo)

        # Actor for different tenant
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=999)

        result = service.update_settings(
            tenant_id=1,  # Different tenant
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert isinstance(result, UpdateSettingsResult)
        assert result.success is False
        assert result.error_type == "permission"

    def test_update_settings_validation_error_returns_error_result(self):
        """Test validation error returns error result."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService, UpdateSettingsResult

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings())
        mock_repo.get_by_id.return_value = tenant

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"sensitive_keyword_match_mode": "invalid_mode"},
            actor=actor,
        )

        assert isinstance(result, UpdateSettingsResult)
        assert result.success is False
        assert result.error_type == "validation"

    def test_update_settings_field_whitelist(self):
        """Test field whitelist filters invalid fields."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        mock_repo.get_by_id.return_value = tenant
        mock_repo.update.return_value = True

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={
                "content_filter_enabled": False,
                "invalid_field": "should_be_ignored",
            },
            actor=actor,
        )

        assert result.success is True
        # Only valid field should be in changed_fields
        assert "invalid_field" not in result.changed_fields
        assert "content_filter_enabled" in result.changed_fields

    def test_update_settings_platform_admin_cross_tenant(self):
        """Test platform admin can modify any tenant."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        mock_repo.get_by_id.return_value = tenant
        mock_repo.update.return_value = True

        service = TenantService(tenant_repo=mock_repo)

        # Platform admin with no tenant_id
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is True
        assert result.tenant_id == 1

    def test_update_settings_audit_log_enabled_field(self):
        """Test modifying audit_log_enabled field."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(audit_log_enabled=True))
        mock_repo.get_by_id.return_value = tenant
        mock_repo.update.return_value = True

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"audit_log_enabled": False},
            actor=actor,
        )

        assert result.success is True
        assert "audit_log_enabled" in result.changed_fields

    def test_update_settings_multiple_fields(self):
        """Test modifying multiple fields."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        tenant = Tenant(
            id=1,
            settings=TenantSettings(
                content_filter_enabled=True,
                block_sensitive_keyword=False,
                sensitive_keyword_match_mode="word_boundary",
            ),
        )
        mock_repo.get_by_id.return_value = tenant
        mock_repo.update.return_value = True

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={
                "content_filter_enabled": False,
                "block_sensitive_keyword": True,
                "sensitive_keyword_match_mode": "substring",
            },
            actor=actor,
        )

        assert result.success is True
        assert len(result.changed_fields) == 3
        assert set(result.changed_fields) == {
            "content_filter_enabled",
            "block_sensitive_keyword",
            "sensitive_keyword_match_mode",
        }

    def test_update_settings_no_changes(self):
        """Test update with same values returns empty diff."""
        from app.core.actor_context import ActorContext
        from app.models.tenant import Tenant, TenantSettings
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        mock_repo.get_by_id.return_value = tenant

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": True},  # Same value
            actor=actor,
        )

        assert result.success is True
        assert result.changed_fields == []

    def test_update_settings_tenant_not_found(self):
        """Test tenant not found returns error result."""
        from app.core.actor_context import ActorContext
        from app.services.tenant_service import TenantService

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None

        service = TenantService(tenant_repo=mock_repo)
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)

        result = service.update_settings(
            tenant_id=999,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is False
        assert result.error_type == "not_found"


class TestAuditHelperFunction:
    """Tests for _log_tenant_settings_audit helper function."""

    def test_audit_helper_calls_log_action(self, app):
        """Test audit helper calls audit_logger.log_action."""
        from app.core.actor_context import ActorContext
        from app.modules.governance.audit_logger import AuditAction
        from app.routes import tenant as tenant_module

        # Create mock audit logger
        mock_audit_logger = MagicMock()
        original_logger = tenant_module.audit_logger
        tenant_module.audit_logger = mock_audit_logger

        try:
            actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

            with app.test_request_context(
                "/api/tenants/1/settings",
                method="PUT",
                json={"content_filter_enabled": False},
            ):
                tenant_module._log_tenant_settings_audit(
                    actor=actor,
                    tenant_id=1,
                    changed_fields=["content_filter_enabled"],
                    old_values={"content_filter_enabled": True},
                    new_values={"content_filter_enabled": False},
                    success=True,
                )

            # Verify log_action was called
            mock_audit_logger.log_action.assert_called_once()
            call_kwargs = mock_audit_logger.log_action.call_args.kwargs

            assert call_kwargs.get("action") == AuditAction.SYSTEM_CONFIG_CHANGE
            assert call_kwargs.get("resource_type") == "tenant_settings"
            assert call_kwargs.get("tenant_id") == 1
            assert call_kwargs.get("success") is True

        finally:
            tenant_module.audit_logger = original_logger

    def test_audit_helper_handles_failure(self, app):
        """Test audit helper handles failure case."""
        from app.core.actor_context import ActorContext
        from app.routes import tenant as tenant_module

        mock_audit_logger = MagicMock()
        original_logger = tenant_module.audit_logger
        tenant_module.audit_logger = mock_audit_logger

        try:
            actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

            with app.test_request_context("/"):
                tenant_module._log_tenant_settings_audit(
                    actor=actor,
                    tenant_id=1,
                    changed_fields=[],
                    old_values={},
                    new_values={},
                    success=False,
                    error="Permission denied",
                    error_type="permission",
                )

            call_kwargs = mock_audit_logger.log_action.call_args.kwargs

            assert call_kwargs.get("success") is False
            assert call_kwargs.get("error_message") == "Permission denied"
            assert call_kwargs.get("details", {}).get("error_type") == "permission"

        finally:
            tenant_module.audit_logger = original_logger

    def test_audit_helper_handles_exception(self, app):
        """Test audit helper handles exception and logs error."""
        from app.core.actor_context import ActorContext
        from app.routes import tenant as tenant_module

        mock_audit_logger = MagicMock()
        mock_audit_logger.log_action.side_effect = Exception("DB connection failed")
        original_logger = tenant_module.audit_logger

        # Track logger calls
        original_logger_error = tenant_module.logger.error
        error_calls = []

        def track_error(*args, **kwargs):
            error_calls.append((args, kwargs))

        tenant_module.audit_logger = mock_audit_logger
        tenant_module.logger.error = track_error

        try:
            actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

            with app.test_request_context("/"):
                # Should not raise exception
                tenant_module._log_tenant_settings_audit(
                    actor=actor,
                    tenant_id=1,
                    changed_fields=["content_filter_enabled"],
                    old_values={},
                    new_values={},
                    success=True,
                )

            # Verify error was logged
            assert len(error_calls) > 0

        finally:
            tenant_module.audit_logger = original_logger
            tenant_module.logger.error = original_logger_error
