"""
Integration tests for API Key tenant authorization.

Issue #2327: API Key 管理 Tenant 授权修复。
测试四个 API 端点的完整授权流程。
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g


class TestAPIKeyTenantAuthorization:
    """测试 API Key 管理的租户授权"""

    def _create_test_app(self):
        """创建测试 Flask 应用"""
        from app.routes.api_keys import api_keys_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_keys_bp)
        return app

    def _mock_api_key_service(self, mock_proxy):
        """模拟 API Key Service"""
        mock_instance = MagicMock()
        mock_proxy.return_value = mock_instance
        return mock_instance

    # ==================== GET /api/api-keys ====================

    def test_get_api_keys_unauthenticated(self):
        """测试未认证用户访问"""
        app = self._create_test_app()
        client = app.test_client()

        response = client.get("/api-keys?tenant_id=1")
        assert response.status_code == 401

    def test_get_api_keys_tenant_admin_own_tenant(self):
        """测试 tenant_admin 查询自己租户的 API Key"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    # 模拟认证用户
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "tenant_admin",
                        "tenant_id": 1,
                        "username": "test_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.list_api_keys.return_value = []

                    client = app.test_client()
                    response = client.get(
                        "/api-keys",
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        # 验证调用了正确的 tenant_id
        mock_instance.list_api_keys.assert_called_once_with(1)

    def test_get_api_keys_tenant_admin_cross_tenant_denied(self):
        """测试 tenant_admin 跨租户访问被拒绝"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                # 模拟 tenant A 的管理员
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "tenant_admin",
                    "tenant_id": 1,
                    "username": "test_admin",
                }

                client = app.test_client()
                # 尝试访问 tenant B
                response = client.get(
                    "/api-keys?tenant_id=2",
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 403
        data = response.get_json()
        assert "denied" in data["error"].lower()

    def test_get_api_keys_platform_admin_explicit_tenant(self):
        """测试 platform_admin 显式指定 tenant_id"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    # 模拟 platform admin
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "platform_admin",
                        "tenant_id": None,
                        "username": "platform_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.list_api_keys.return_value = []

                    client = app.test_client()
                    response = client.get(
                        "/api-keys?tenant_id=2",
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        mock_instance.list_api_keys.assert_called_once_with(2)

    def test_get_api_keys_platform_admin_missing_tenant(self):
        """测试 platform_admin 缺少 tenant_id 时 fail closed"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                # 模拟 platform admin
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "platform_admin",
                    "tenant_id": None,
                    "username": "platform_admin",
                }

                client = app.test_client()
                response = client.get(
                    "/api-keys",
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 400
        data = response.get_json()
        assert "required" in data["error"].lower()

    # ==================== POST /api/api-keys ====================

    def test_post_api_keys_tenant_admin_own_tenant(self):
        """测试 tenant_admin 创建自己租户的 API Key"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "tenant_admin",
                        "tenant_id": 1,
                        "username": "test_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.store_api_key.return_value = {"success": True}

                    client = app.test_client()
                    response = client.post(
                        "/api-keys",
                        json={
                            "provider": "openai",
                            "key_name": "test_key",
                            "api_key": "sk-test",
                        },
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        # 验证调用了正确的 tenant_id
        call_kwargs = mock_instance.store_api_key.call_args[1]
        assert call_kwargs["tenant_id"] == 1
        assert call_kwargs["created_by"] == 1

    def test_post_api_keys_tenant_admin_cross_tenant_denied(self):
        """测试 tenant_admin 跨租户创建被拒绝"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "tenant_admin",
                    "tenant_id": 1,
                    "username": "test_admin",
                }

                client = app.test_client()
                response = client.post(
                    "/api-keys",
                    json={
                        "provider": "openai",
                        "key_name": "test_key",
                        "api_key": "sk-test",
                        "tenant_id": 2,  # 尝试为 tenant B 创建
                    },
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 403

    # ==================== PUT /api/api-keys/<key_id> ====================

    def test_put_api_keys_tenant_admin_own_key(self):
        """测试 tenant_admin 更新自己租户的 API Key"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "tenant_admin",
                        "tenant_id": 1,
                        "username": "test_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.update_api_key_by_id.return_value = True

                    client = app.test_client()
                    response = client.put(
                        "/api-keys/1",
                        json={"key_name": "updated_key"},
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        # 验证调用了正确的参数
        call_kwargs = mock_instance.update_api_key_by_id.call_args[1]
        assert call_kwargs["key_id"] == 1
        assert call_kwargs["tenant_id"] == 1

    def test_put_api_keys_tenant_admin_other_tenant_key_denied(self):
        """测试 tenant_admin 更新其他租户的 API Key 被拒绝"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "tenant_admin",
                    "tenant_id": 1,
                    "username": "test_admin",
                }

                client = app.test_client()
                # 尝试提供 tenant B 的 tenant_id 和 key_id
                response = client.put(
                    "/api-keys/2",
                    json={"tenant_id": 2, "key_name": "updated_key"},
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 403

    # ==================== DELETE /api/api-keys/<key_id> ====================

    def test_delete_api_keys_tenant_admin_own_key(self):
        """测试 tenant_admin 删除自己租户的 API Key"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "tenant_admin",
                        "tenant_id": 1,
                        "username": "test_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.delete_api_key_by_id.return_value = True

                    client = app.test_client()
                    response = client.delete(
                        "/api-keys/1",
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        # 验证调用了正确的参数
        call_args = mock_instance.delete_api_key_by_id.call_args
        assert call_args[0][0] == 1  # key_id
        assert call_args[0][1] == 1  # tenant_id

    def test_delete_api_keys_tenant_admin_cross_tenant_denied(self):
        """测试 tenant_admin 跨租户删除被拒绝"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "tenant_admin",
                    "tenant_id": 1,
                    "username": "test_admin",
                }

                client = app.test_client()
                response = client.delete(
                    "/api-keys/2",
                    json={"tenant_id": 2},
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 403

    # ==================== Legacy Admin Tests ====================

    def test_legacy_admin_explicit_tenant(self):
        """测试 legacy admin 显式指定 tenant_id"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                with patch("app.routes.api_keys.get_api_key_proxy_service") as mock_proxy:
                    mock_extract.return_value = "test-token"
                    mock_load_user.return_value = {
                        "id": 1,
                        "role": "admin",
                        "tenant_id": None,
                        "username": "legacy_admin",
                    }
                    mock_instance = self._mock_api_key_service(mock_proxy)
                    mock_instance.list_api_keys.return_value = []

                    client = app.test_client()
                    response = client.get(
                        "/api-keys?tenant_id=1",
                        headers={"Authorization": "Bearer test-token"}
                    )

        assert response.status_code == 200
        mock_instance.list_api_keys.assert_called_once_with(1)

    def test_legacy_admin_missing_tenant(self):
        """测试 legacy admin 缺少 tenant_id"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "admin",
                    "tenant_id": None,
                    "username": "legacy_admin",
                }

                client = app.test_client()
                response = client.get(
                    "/api-keys",
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 400

    # ==================== Invalid Input Tests ====================

    def test_invalid_tenant_id_negative(self):
        """测试无效的 tenant_id（负数）"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "platform_admin",
                    "tenant_id": None,
                    "username": "platform_admin",
                }

                client = app.test_client()
                response = client.get(
                    "/api-keys?tenant_id=-1",
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 400

    def test_invalid_tenant_id_zero(self):
        """测试无效的 tenant_id（0）"""
        app = self._create_test_app()

        with patch("app.auth.decorators._load_user_from_token") as mock_load_user:
            with patch("app.auth.decorators._extract_session_token") as mock_extract:
                mock_extract.return_value = "test-token"
                mock_load_user.return_value = {
                    "id": 1,
                    "role": "platform_admin",
                    "tenant_id": None,
                    "username": "platform_admin",
                }

                client = app.test_client()
                response = client.get(
                    "/api-keys?tenant_id=0",
                    headers={"Authorization": "Bearer test-token"}
                )

        assert response.status_code == 400
