"""
Tests for custom envKey normalization and fallback injection.

Covers:
- normalize_model_providers: envKey unification + baseUrl removal
- collect_custom_envkeys: reading settings and returning env overrides
- Edge cases: malformed entries, missing fields, nested structures
"""

import json
import os

# Add remote-agent to sys.path so we can import cli_adapters
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent"))

from cli_adapters.base import collect_custom_envkeys, normalize_model_providers

# ---------------------------------------------------------------------------
# normalize_model_providers tests
# ---------------------------------------------------------------------------


class TestNormalizeModelProviders:
    """Tests for normalize_model_providers()."""

    def test_unifies_custom_envkey_to_openai(self):
        settings = {
            "modelProviders": {
                "openai": [
                    {"envKey": "BAILIAN_CODING_PLAN_API_KEY", "id": "glm-5", "name": "glm-5"}
                ]
            }
        }
        normalize_model_providers(settings)
        assert settings["modelProviders"]["openai"][0]["envKey"] == "OPENAI_API_KEY"

    def test_removes_base_url(self):
        settings = {
            "modelProviders": {
                "openai": [
                    {
                        "envKey": "OPENAI_API_KEY",
                        "id": "gpt-4o",
                        "baseUrl": "https://api.openai.com/v1",
                    }
                ]
            }
        }
        normalize_model_providers(settings)
        assert "baseUrl" not in settings["modelProviders"]["openai"][0]

    def test_preserves_other_fields(self):
        settings = {
            "modelProviders": {
                "openai": [
                    {
                        "envKey": "CUSTOM_KEY",
                        "id": "gpt-4o",
                        "name": "GPT-4o",
                        "generationConfig": {"timeout": 60000},
                    }
                ]
            }
        }
        normalize_model_providers(settings)
        entry = settings["modelProviders"]["openai"][0]
        assert entry["envKey"] == "OPENAI_API_KEY"
        assert entry["id"] == "gpt-4o"
        assert entry["name"] == "GPT-4o"
        assert entry["generationConfig"] == {"timeout": 60000}

    def test_preserves_top_level_settings(self):
        settings = {
            "model": {"name": "glm-5"},
            "mcpServers": {"my-server": {"command": "node"}},
            "statusLine": "custom",
            "modelProviders": {"openai": [{"envKey": "CUSTOM_KEY", "id": "glm-5"}]},
        }
        normalize_model_providers(settings)
        assert settings["model"] == {"name": "glm-5"}
        assert settings["mcpServers"] == {"my-server": {"command": "node"}}
        assert settings["statusLine"] == "custom"

    def test_multiple_providers(self):
        settings = {
            "modelProviders": {
                "openai": [{"envKey": "KEY_A", "id": "model-a"}],
                "anthropic": [
                    {"envKey": "KEY_B", "id": "model-b", "baseUrl": "https://api.anthropic.com"}
                ],
            }
        }
        normalize_model_providers(settings)
        assert settings["modelProviders"]["openai"][0]["envKey"] == "OPENAI_API_KEY"
        assert settings["modelProviders"]["anthropic"][0]["envKey"] == "OPENAI_API_KEY"
        assert "baseUrl" not in settings["modelProviders"]["anthropic"][0]

    def test_multiple_models_per_provider(self):
        settings = {
            "modelProviders": {
                "openai": [
                    {"envKey": "KEY_A", "id": "model-a", "baseUrl": "https://a.com"},
                    {"envKey": "KEY_B", "id": "model-b", "baseUrl": "https://b.com"},
                ]
            }
        }
        normalize_model_providers(settings)
        for model in settings["modelProviders"]["openai"]:
            assert model["envKey"] == "OPENAI_API_KEY"
            assert "baseUrl" not in model

    def test_missing_envKey_field(self):
        """Models without envKey should not crash."""
        settings = {"modelProviders": {"openai": [{"id": "model-no-envkey"}]}}
        normalize_model_providers(settings)
        assert "envKey" not in settings["modelProviders"]["openai"][0]

    def test_empty_model_providers(self):
        settings = {"modelProviders": {}}
        normalize_model_providers(settings)
        assert settings == {"modelProviders": {}}

    def test_no_model_providers_key(self):
        settings = {"model": {"name": "glm-5"}}
        normalize_model_providers(settings)
        assert settings == {"model": {"name": "glm-5"}}

    def test_malformed_model_providers_not_dict(self):
        settings = {"modelProviders": "invalid"}
        normalize_model_providers(settings)
        assert settings["modelProviders"] == "invalid"

    def test_malformed_models_list_not_list(self):
        settings = {"modelProviders": {"openai": "not-a-list"}}
        normalize_model_providers(settings)
        assert settings["modelProviders"]["openai"] == "not-a-list"

    def test_malformed_model_entry_not_dict(self):
        settings = {"modelProviders": {"openai": ["not-a-dict"]}}
        normalize_model_providers(settings)
        assert settings["modelProviders"]["openai"] == ["not-a-dict"]


# ---------------------------------------------------------------------------
# collect_custom_envkeys tests
# ---------------------------------------------------------------------------


class TestCollectCustomEnvkeys:
    """Tests for collect_custom_envkeys()."""

    def test_extracts_custom_envkey(self, tmp_path):
        settings = {
            "modelProviders": {"openai": [{"envKey": "BAILIAN_CODING_PLAN_API_KEY", "id": "glm-5"}]}
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        result = collect_custom_envkeys(str(settings_file), "my-token")
        assert result == {"BAILIAN_CODING_PLAN_API_KEY": "my-token"}

    def test_skips_openai_api_key(self, tmp_path):
        settings = {"modelProviders": {"openai": [{"envKey": "OPENAI_API_KEY", "id": "gpt-4o"}]}}
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        result = collect_custom_envkeys(str(settings_file), "my-token")
        assert result == {}

    def test_multiple_custom_keys(self, tmp_path):
        settings = {
            "modelProviders": {
                "openai": [
                    {"envKey": "KEY_A", "id": "model-a"},
                    {"envKey": "KEY_B", "id": "model-b"},
                ],
                "anthropic": [
                    {"envKey": "KEY_C", "id": "model-c"},
                ],
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        result = collect_custom_envkeys(str(settings_file), "token")
        assert result == {"KEY_A": "token", "KEY_B": "token", "KEY_C": "token"}

    def test_nonexistent_file(self, tmp_path):
        result = collect_custom_envkeys(str(tmp_path / "nonexistent.json"), "token")
        assert result == {}

    def test_malformed_json(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{invalid json")

        result = collect_custom_envkeys(str(settings_file), "token")
        assert result == {}

    def test_missing_model_providers(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"model": {"name": "glm-5"}}))

        result = collect_custom_envkeys(str(settings_file), "token")
        assert result == {}

    def test_model_without_envkey(self, tmp_path):
        settings = {"modelProviders": {"openai": [{"id": "model-no-envkey"}]}}
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        result = collect_custom_envkeys(str(settings_file), "token")
        assert result == {}

    def test_malformed_providers_not_dict(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"modelProviders": "invalid"}))

        result = collect_custom_envkeys(str(settings_file), "token")
        assert result == {}
