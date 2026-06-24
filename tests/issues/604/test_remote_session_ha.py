#!/usr/bin/env python3
"""
Tests for RemoteSessionManager.create_remote_session HA pool token validation.

Covers the critical bug fix: non-qwen tools must not embed empty HA fields
in the proxy token (which would cause all LLM proxy requests to return 500).
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mgr():
    """Create a RemoteSessionManager with mocked dependencies."""
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    m = RemoteSessionManager.__new__(RemoteSessionManager)
    m._agent_manager = MagicMock()
    m._api_key_proxy = MagicMock()
    m._session_manager = MagicMock()
    m._message_repo = MagicMock()
    m._user_repo = MagicMock()
    m._user_name_cache = {}
    m._session_permission_modes = {}
    return m


def _connected_machine(machine_id="mac-1", tenant_id=1):
    """Return a connected machine mock."""
    mgr_mock = MagicMock()
    mgr_mock.is_connected.return_value = True
    mgr_mock.get_machine.return_value = {
        "machine_id": machine_id,
        "tenant_id": tenant_id,
        "machine_name": "test-machine",
        "hostname": "test-host",
    }
    mgr_mock.check_user_access.return_value = True
    return mgr_mock


def _valid_ha_token_payload(user_id=1, tenant_id=1, machine_id="mac-1"):
    """Return a valid ha_pool_token payload."""
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_type": "ha_pool",
        "scope": "remote",
        "machine_id": machine_id,
        "provider": "openai",
        "ha_candidate_keys": [{"key_id": 10, "priority": 100, "weight": 100}],
        "ha_model_key_ids": {"qwen3-coder": [10]},
        "ha_models": [{"id": "qwen3-coder", "name": "Qwen3 Coder"}],
        "ha_settings": {"modelProviders": {"openai": []}},
        "ha_empty_reason": None,
    }


class TestRemoteSessionHA:
    """Test HA pool token handling in create_remote_session."""

    def test_non_qwen_tool_no_ha_fields_in_token(self, mgr):
        """Critical bug regression: non-qwen tools must NOT embed empty HA fields."""
        mgr._agent_manager = _connected_machine()
        mgr._api_key_proxy.validate_proxy_token.return_value = None  # not used
        mgr._api_key_proxy.resolve_api_key_for_scope.return_value = ("sk", None, 1, None)
        mgr._api_key_proxy.get_cli_settings_for_tool.return_value = None
        mgr._api_key_proxy.generate_proxy_token.return_value = "proxy-tok"
        mgr._session_manager.create_session.return_value = MagicMock(context={})

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="claude-code",
            tenant_id=1,
        )

        assert result is not None
        # Verify generate_proxy_token was called
        call_kwargs = mgr._api_key_proxy.generate_proxy_token.call_args
        extra = call_kwargs.kwargs.get("extra_payload") or call_kwargs[1].get("extra_payload")
        # Critical: no ha_candidate_keys or ha_model_key_ids in the payload
        assert "ha_candidate_keys" not in extra
        assert "ha_model_key_ids" not in extra

    def test_qwen_valid_ha_pool_token(self, mgr):
        """Qwen tool with valid HA pool token creates session successfully."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload()
        mgr._api_key_proxy.validate_proxy_token.return_value = payload
        mgr._api_key_proxy.generate_proxy_token.return_value = "proxy-tok"
        mgr._session_manager.create_session.return_value = MagicMock(context={})

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="valid-ha-token",
            model="qwen3-coder",
            tenant_id=1,
        )

        assert result is not None
        assert result["session_id"] is not None
        # Verify proxy token includes HA metadata
        call_kwargs = mgr._api_key_proxy.generate_proxy_token.call_args
        extra = call_kwargs.kwargs.get("extra_payload") or call_kwargs[1].get("extra_payload")
        assert extra["ha_candidate_keys"] == payload["ha_candidate_keys"]
        assert extra["ha_model_key_ids"] == payload["ha_model_key_ids"]

    def test_qwen_missing_ha_pool_token_returns_none(self, mgr):
        """Qwen tool without ha_pool_token returns None."""
        mgr._agent_manager = _connected_machine()

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token=None,
            tenant_id=1,
        )

        assert result is None

    def test_wrong_session_type_rejected(self, mgr):
        """ha_pool_token with wrong session_type is rejected."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload()
        payload["session_type"] = "agent"  # wrong
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            tenant_id=1,
        )

        assert result is None

    def test_user_id_mismatch_rejected(self, mgr):
        """ha_pool_token user_id mismatch is rejected."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload(user_id=999)
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,  # different user
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            tenant_id=1,
        )

        assert result is None

    def test_tenant_id_mismatch_rejected(self, mgr):
        """ha_pool_token tenant_id mismatch is rejected."""
        mgr._agent_manager = _connected_machine(tenant_id=1)
        payload = _valid_ha_token_payload(tenant_id=5)  # mismatch
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            tenant_id=1,
        )

        assert result is None

    def test_machine_id_mismatch_rejected(self, mgr):
        """ha_pool_token machine_id mismatch is rejected."""
        mgr._agent_manager = _connected_machine(machine_id="mac-1")
        payload = _valid_ha_token_payload(machine_id="mac-OTHER")
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            tenant_id=1,
        )

        assert result is None

    def test_scope_not_remote_rejected(self, mgr):
        """ha_pool_token with scope != 'remote' is rejected."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload()
        payload["scope"] = "local"
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            tenant_id=1,
        )

        assert result is None

    def test_unsupported_model_rejected(self, mgr):
        """Requested model not in ha_model_key_ids is rejected."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload()
        mgr._api_key_proxy.validate_proxy_token.return_value = payload

        result = mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            model="nonexistent-model",
            tenant_id=1,
        )

        assert result is None

    def test_ha_pool_uses_token_settings(self, mgr):
        """When HA pool is present, get_cli_settings_for_tool should NOT be called."""
        mgr._agent_manager = _connected_machine()
        payload = _valid_ha_token_payload()
        mgr._api_key_proxy.validate_proxy_token.return_value = payload
        mgr._api_key_proxy.generate_proxy_token.return_value = "proxy-tok"
        mgr._session_manager.create_session.return_value = MagicMock(context={})

        mgr.create_remote_session(
            user_id=1,
            machine_id="mac-1",
            project_path="/home/user/project",
            cli_tool="qwen-code-cli",
            ha_pool_token="token",
            model="qwen3-coder",
            tenant_id=1,
        )

        mgr._api_key_proxy.get_cli_settings_for_tool.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
