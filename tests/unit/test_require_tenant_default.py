"""
Test require_tenant default value fix (Issue #1824, F3)

Tests for:
- update_session_fields defaults to require_tenant=True
- increment_session_usage defaults to require_tenant=True
- Fail-closed behavior when tenant_id is None

The fail-closed/backward-compat tests were assertion-free placeholders in the
original fix; #2429 batch 5 implemented them against a real in-process sqlite
SessionManager (house pattern: tests/unit/test_workspace_modules.py).
"""

import inspect

import pytest

from app.modules.workspace.session_manager import SessionManager

pytestmark = [pytest.mark.regression, pytest.mark.issue(1824)]


@pytest.fixture
def session_manager(tmp_path):
    """SessionManager over an isolated per-test sqlite file."""
    manager = SessionManager(db_path=str(tmp_path / "session-manager.db"))
    manager._ensure_tables()
    return manager


class TestRequireTenantDefault:
    """Test require_tenant default value in session manager."""

    def test_update_session_fields_defaults_require_tenant_true(self):
        """update_session_fields should default to require_tenant=True."""
        sig = inspect.signature(SessionManager.update_session_fields)
        require_tenant_param = sig.parameters.get("require_tenant")

        assert require_tenant_param is not None
        assert require_tenant_param.default is True

    def test_increment_session_usage_defaults_require_tenant_true(self):
        """increment_session_usage should default to require_tenant=True."""
        sig = inspect.signature(SessionManager.increment_session_usage)
        require_tenant_param = sig.parameters.get("require_tenant")

        assert require_tenant_param is not None
        assert require_tenant_param.default is True


class TestRequireTenantFailClosed:
    """Test fail-closed behavior when tenant_id is None."""

    def test_update_with_null_tenant_fails_closed(self, session_manager):
        """update_session_fields with tenant_id=None should match 0 rows when require_tenant=True."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        updated = session_manager.update_session_fields(
            created.session_id, {"title": "rewritten"}, tenant_id=None
        )
        reloaded = session_manager.get_session(created.session_id)

        assert updated is False
        assert reloaded.title != "rewritten"

    def test_update_with_explicit_tenant_succeeds(self, session_manager):
        """update_session_fields with correct tenant_id should succeed."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        updated = session_manager.update_session_fields(
            created.session_id, {"title": "rewritten"}, tenant_id=1
        )
        reloaded = session_manager.get_session(created.session_id)

        assert updated is True
        assert reloaded.title == "rewritten"

    def test_increment_usage_with_null_tenant_fails_closed(self, session_manager):
        """increment_session_usage with tenant_id=None should match 0 rows when require_tenant=True."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        incremented = session_manager.increment_session_usage(
            created.session_id, request_delta=1, tenant_id=None
        )
        reloaded = session_manager.get_session(created.session_id)

        assert incremented is False
        assert reloaded.request_count == 0

    def test_increment_usage_with_explicit_tenant_succeeds(self, session_manager):
        """increment_session_usage with correct tenant_id should succeed."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        incremented = session_manager.increment_session_usage(
            created.session_id, request_delta=1, tenant_id=1
        )
        reloaded = session_manager.get_session(created.session_id)

        assert incremented is True
        assert reloaded.request_count == 1


class TestRequireTenantBackwardCompatibility:
    """Test backward compatibility for callers passing require_tenant=False."""

    def test_update_with_require_tenant_false_allows_null_tenant(self, session_manager):
        """update_session_fields with require_tenant=False should allow null tenant."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        updated = session_manager.update_session_fields(
            created.session_id, {"title": "legacy-path"}, tenant_id=None, require_tenant=False
        )
        reloaded = session_manager.get_session(created.session_id)

        assert updated is True
        assert reloaded.title == "legacy-path"

    def test_increment_with_require_tenant_false_allows_null_tenant(self, session_manager):
        """increment_session_usage with require_tenant=False should allow null tenant."""
        created = session_manager.create_session(tool_name="qwen", tenant_id=1)

        incremented = session_manager.increment_session_usage(
            created.session_id, request_delta=1, tenant_id=None, require_tenant=False
        )
        reloaded = session_manager.get_session(created.session_id)

        assert incremented is True
        assert reloaded.request_count == 1
