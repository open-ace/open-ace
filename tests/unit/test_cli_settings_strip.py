#!/usr/bin/env python3
"""
Unit tests for sensitive field stripping in CLI settings.

Verifies that API keys and base URLs are properly stripped from
settings.json, including dynamic envKey names from modelProviders.
"""

from __future__ import annotations

import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestCollectDynamicEnvKeys:
    """Test _collect_dynamic_env_keys / collect_dynamic_env_keys."""

    def test_empty_settings(self):
        from app.modules.workspace.api_key_proxy import _collect_dynamic_env_keys

        assert _collect_dynamic_env_keys({}) == set()

    def test_no_model_providers(self):
        from app.modules.workspace.api_key_proxy import _collect_dynamic_env_keys

        settings = {"env": {"FOO": "bar"}, "model": {"name": "test"}}
        assert _collect_dynamic_env_keys(settings) == set()

    def test_collects_env_key_from_providers(self):
        from app.modules.workspace.api_key_proxy import _collect_dynamic_env_keys

        settings = {
            "modelProviders": {
                "openai": [
                    {"id": "glm-5", "name": "glm-5", "envKey": "ZAI_API_KEY"},
                    {"id": "gpt-4", "name": "gpt-4", "envKey": "CUSTOM_KEY"},
                ]
            }
        }
        result = _collect_dynamic_env_keys(settings)
        assert result == {"ZAI_API_KEY", "CUSTOM_KEY"}

    def test_ignores_non_string_env_key(self):
        from app.modules.workspace.api_key_proxy import _collect_dynamic_env_keys

        settings = {
            "modelProviders": {
                "openai": [
                    {"id": "m1", "envKey": 123},  # not a string
                    {"id": "m2", "envKey": "VALID_KEY"},
                ]
            }
        }
        result = _collect_dynamic_env_keys(settings)
        assert result == {"VALID_KEY"}

    def test_ignores_missing_env_key(self):
        from app.modules.workspace.api_key_proxy import _collect_dynamic_env_keys

        settings = {
            "modelProviders": {
                "openai": [
                    {"id": "m1", "name": "model-1"},  # no envKey
                ]
            }
        }
        assert _collect_dynamic_env_keys(settings) == set()


class TestBuildCliSettingsStripsSensitive:
    """Test that _build_cli_settings_for_tool strips sensitive fields."""

    def test_strips_hardcoded_keys(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        settings = {
            "env": {
                "ANTHROPIC_API_KEY": "secret-key",
                "ANTHROPIC_BASE_URL": "https://api.example.com",
                "ANTHROPIC_MODEL": "claude-3",  # should be kept
            },
            "model": "haiku",
        }
        result = svc._build_cli_settings_for_tool("claude-code", settings)
        assert "ANTHROPIC_API_KEY" not in result.get("env", {})
        assert "ANTHROPIC_BASE_URL" not in result.get("env", {})
        assert result["env"]["ANTHROPIC_MODEL"] == "claude-3"
        assert result["model"] == "haiku"

    def test_strips_dynamic_env_key(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        settings = {
            "env": {
                "ZAI_API_KEY": "secret-key",  # dynamic envKey
                "SOME_OTHER_VAR": "keep-me",
            },
            "modelProviders": {
                "openai": [
                    {"id": "glm-5", "name": "glm-5", "envKey": "ZAI_API_KEY"},
                ]
            },
        }
        result = svc._build_cli_settings_for_tool("qwen-code", settings)
        assert "ZAI_API_KEY" not in result.get("env", {})
        assert result["env"]["SOME_OTHER_VAR"] == "keep-me"

    def test_strips_base_url_from_model_providers(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        settings = {
            "modelProviders": {
                "openai": [
                    {"id": "m1", "name": "model-1", "baseUrl": "https://api.example.com"},
                ]
            },
        }
        result = svc._build_cli_settings_for_tool("qwen-code", settings)
        assert "baseUrl" not in result["modelProviders"]["openai"][0]

    def test_preserves_non_sensitive_config(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        settings = {
            "env": {"ANTHROPIC_MODEL": "glm-5"},
            "model": "haiku",
            "theme": "dark",
        }
        result = svc._build_cli_settings_for_tool("claude-code", settings)
        assert result["env"]["ANTHROPIC_MODEL"] == "glm-5"
        assert result["model"] == "haiku"
        assert result["theme"] == "dark"

    def test_parses_codex_toml_settings(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        result = svc._build_cli_settings_for_tool(
            "codex-cli",
            """
model_provider = "openace"
model = "qwen3.7-max"

[model_providers.openace]
name = "Open ACE Proxy"
wire_api = "responses"
""",
        )

        assert result["model_provider"] == "openace"
        assert result["model"] == "qwen3.7-max"
        assert result["model_providers"]["openace"]["name"] == "Open ACE Proxy"
        assert result["model_providers"]["openace"]["wire_api"] == "responses"


class TestValidateCliSettingsPayload:
    """Test server-side validation of stored CLI settings payloads."""

    def test_accepts_valid_codex_toml_string(self):
        from app.modules.workspace.api_key_proxy import validate_cli_settings_payload

        error = validate_cli_settings_payload(
            """
            {"codex-cli":"model_provider = \\"openace\\"\\nmodel = \\"qwen3.7-max\\""}
            """
        )
        assert error is None

    def test_rejects_invalid_codex_toml_string(self):
        from app.modules.workspace.api_key_proxy import validate_cli_settings_payload

        error = validate_cli_settings_payload('{"codex-cli":"[broken"}')
        assert error is not None
        assert "Invalid Codex settings TOML" in error
