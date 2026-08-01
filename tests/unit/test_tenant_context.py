"""
Unit tests for TenantContext Fail-Closed Mechanism

Issue #2179: 租户管理员权限模型
"""

import pytest
from flask import Flask, g

from app.core.tenant_context import TenantContext, TenantContextError


@pytest.fixture
def app():
    """Create a minimal Flask app for testing"""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


class TestTenantContext:
    """Test TenantContext Fail-Closed mechanism"""

    def test_get_required_tenant_id_success(self, app):
        """Test successful tenant_id retrieval"""
        with app.test_request_context():
            g.tenant_id = 123
            tenant_id = TenantContext.get_required_tenant_id()
            assert tenant_id == 123

    def test_get_required_tenant_id_fail_closed(self, app):
        """Test fail-closed when tenant_id is missing"""
        with app.test_request_context():
            # g.tenant_id is not set
            with pytest.raises(TenantContextError) as exc_info:
                TenantContext.get_required_tenant_id()

            assert "租户上下文缺失" in str(exc_info.value)

    def test_get_required_tenant_id_no_fallback_to_1(self, app):
        """Test that there is no silent fallback to tenant_id=1"""
        with app.test_request_context():
            # g.tenant_id is not set
            # This should raise an exception, NOT return 1
            with pytest.raises(TenantContextError):
                tenant_id = TenantContext.get_required_tenant_id()
                # Should never reach here
                assert tenant_id != 1

    def test_get_optional_tenant_id_success(self, app):
        """Test optional tenant_id retrieval with value"""
        with app.test_request_context():
            g.tenant_id = 456
            tenant_id = TenantContext.get_optional_tenant_id()
            assert tenant_id == 456

    def test_get_optional_tenant_id_none(self, app):
        """Test optional tenant_id retrieval without value"""
        with app.test_request_context():
            # g.tenant_id is not set
            tenant_id = TenantContext.get_optional_tenant_id()
            assert tenant_id is None

    def test_set_tenant_id(self, app):
        """Test setting tenant_id"""
        with app.test_request_context():
            TenantContext.set_tenant_id(789)
            assert g.tenant_id == 789

    def test_set_tenant_id_none(self, app):
        """Test setting tenant_id to None (platform admin)"""
        with app.test_request_context():
            TenantContext.set_tenant_id(None)
            assert g.tenant_id is None

    def test_fail_closed_message_content(self, app):
        """Test that error message contains helpful information"""
        with app.test_request_context():
            with pytest.raises(TenantContextError) as exc_info:
                TenantContext.get_required_tenant_id()

            message = str(exc_info.value)
            assert "权限装饰器" in message
            assert "tenant_id" in message
            assert "平台管理员" in message
