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


class TestExtractModelsForTool:
    """Test _extract_models_for_tool across tool-specific config shapes.

    Covers two regressions that left dropdowns showing only "default":
    - codex stores TOML (snake_case ``model_providers`` + top-level ``model``),
      not qwen's camelCase ``modelProviders.<provider> = [{id,...}]`` list.
    - claude/zcode/qwen extraction must remain unchanged.
    """

    def test_codex_extracts_top_level_model_from_toml(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # Parsed TOML shape (as produced by _parse_codex_settings).
        settings = {
            "model_provider": "openace",
            "model": "glm-5",
            "model_providers": {"openace": {"name": "Open ACE Proxy", "wire_api": "responses"}},
        }
        models = APIKeyProxyService._extract_models_for_tool("codex", settings)
        assert [m["id"] for m in models] == ["glm-5"]

    def test_codex_dedups_model_across_top_and_provider(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        settings = {
            "model": "glm-5",
            "model_providers": {
                "openace": {"id": "glm-5", "name": "dup"},
                "other": {"model": "glm-6"},
            },
        }
        models = APIKeyProxyService._extract_models_for_tool("codex", settings)
        # glm-5 from top-level + provider id dedups to one; glm-6 from provider.model
        assert [m["id"] for m in models] == ["glm-5", "glm-6"]

    def test_qwen_extracts_camel_case_model_providers(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        settings = {
            "modelProviders": {
                "openai": [
                    {"id": "glm-5", "name": "glm-5"},
                    {"id": "glm-5.1", "name": "glm-5.1"},
                ]
            }
        }
        models = APIKeyProxyService._extract_models_for_tool("qwen", settings)
        assert [m["id"] for m in models] == ["glm-5", "glm-5.1"]


class TestCollectToolKeySettingsAlias:
    """Test that cli_settings subkeys are found under any alias form.

    Regression: when the request tool_name was "qwen-code-cli" (canonical
    "qwen") but the stored cli_settings subkey was "qwen-code", the lookup
    only tried the request name and the canonical name and missed the stored
    key, dropping all models from the dropdown.
    """

    def _svc_with_single_key(self, cli_settings: dict, cli_tools: list[str]):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        svc._get_connection = lambda: None  # type: ignore[attr-defined]

        rows = [
            {
                "id": 1,
                "provider": "zai",
                "encrypted_key": "",
                "base_url": "",
                "cli_tools": __import__("json").dumps(cli_tools),
                "cli_settings": __import__("json").dumps(cli_settings),
                "priority": 0,
                "weight": 100,
            }
        ]

        class _FakeCursor:
            def execute(self, *a, **kw):
                pass

            def fetchall(self):
                return rows

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def close(self):
                pass

        svc._get_connection = lambda: _FakeConn()  # type: ignore[attr-defined]
        return svc

    def test_qwen_code_cli_finds_settings_stored_under_qwen_code(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = self._svc_with_single_key(
            {"qwen-code": {"modelProviders": {"openai": [{"id": "glm-5", "name": "glm-5"}]}}},
            ["qwen-code"],
        )
        ranked = svc._collect_tool_key_settings(
            tenant_id=1, tool_name="qwen-code-cli", scope="local"
        )
        assert len(ranked) == 1
        models = APIKeyProxyService._extract_models_for_tool("qwen", ranked[0][1])
        assert [m["id"] for m in models] == ["glm-5"]

    def test_codex_cli_finds_settings_stored_under_codex_cli(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = self._svc_with_single_key(
            {"codex-cli": 'model = "glm-5"\nmodel_provider = "openace"'},
            ["codex-cli"],
        )
        ranked = svc._collect_tool_key_settings(tenant_id=1, tool_name="codex", scope="local")
        assert len(ranked) == 1
        models = APIKeyProxyService._extract_models_for_tool("codex", ranked[0][1])
        assert [m["id"] for m in models] == ["glm-5"]


class TestToolModelPool:
    """Test HA model-pool construction for integrated qwen sessions."""

    def test_unions_models_across_priorities_with_stable_canonical_choice(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        svc = APIKeyProxyService.__new__(APIKeyProxyService)
        svc._list_tool_key_rows = lambda tenant_id, provider, tool_name, scope: [  # type: ignore[attr-defined]
            {"id": 10, "priority": 200, "weight": 50, "scope": "remote"},
            {"id": 30, "priority": 100, "weight": 100, "scope": "shared"},
        ]
        settings_by_id = {
            10: {
                "modelProviders": {
                    "openai": [
                        {"id": "shared-model", "name": "High Priority Shared"},
                        {"id": "high-only", "name": "High Only"},
                    ]
                },
                "theme": "kept-from-top-rank",
            },
            30: {
                "modelProviders": {
                    "openai": [
                        {"id": "shared-model", "name": "Lower Priority Shared"},
                        {"id": "low-only", "name": "Low Only"},
                    ]
                }
            },
        }
        svc._get_tool_settings_from_row = lambda row, tool_name: settings_by_id[row["id"]]  # type: ignore[attr-defined]

        pool = svc.get_tool_model_pool(tenant_id=1, tool_name="qwen-code", scope="remote")

        assert [model["id"] for model in pool["models"]] == [
            "high-only",
            "shared-model",
            "low-only",
        ]
        assert pool["models"][1]["name"] == "High Priority Shared"
        assert pool["model_key_ids"]["shared-model"] == [10, 30]
        assert pool["settings"]["theme"] == "kept-from-top-rank"
        assert [model["id"] for model in pool["settings"]["modelProviders"]["openai"]] == [
            "high-only",
            "shared-model",
            "low-only",
        ]


class TestMergeMultiKeySettings:
    """Test _merge_multi_key_settings for multi-API-key model merging."""

    def _make_svc(self):
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        return APIKeyProxyService.__new__(APIKeyProxyService)

    def test_single_key_returns_unchanged(self):
        """Single key fast path — returned directly, not merged."""
        svc = self._make_svc()
        settings = {"modelProviders": {"openai": [{"id": "qwen-max"}]}, "theme": "dark"}

        result = svc._merge_multi_key_settings([((0, -100, 1), settings)])

        assert result["theme"] == "dark"
        assert result["modelProviders"]["openai"] == [{"id": "qwen-max"}]

    def test_disjoint_models_are_unioned(self):
        """Two keys with non-overlapping models — all models appear."""
        svc = self._make_svc()
        ranked = [
            ((-10, -100, 1), {"modelProviders": {"openai": [{"id": "qwen-max"}]}}),
            ((-5, -100, 2), {"modelProviders": {"openai": [{"id": "qwen-turbo"}]}}),
        ]

        result = svc._merge_multi_key_settings(ranked)

        model_ids = [m["id"] for m in result["modelProviders"]["openai"]]
        assert sorted(model_ids) == ["qwen-max", "qwen-turbo"]

    def test_overlapping_model_id_uses_highest_priority(self):
        """Same model ID in two keys — highest-priority config wins."""
        svc = self._make_svc()
        ranked = [
            (
                (-10, -100, 1),
                {
                    "modelProviders": {
                        "openai": [
                            {
                                "id": "qwen-max",
                                "name": "Key A qwen-max",
                                "generationConfig": {"temperature": 0.5},
                            },
                        ]
                    },
                },
            ),
            (
                (-5, -100, 2),
                {
                    "modelProviders": {
                        "openai": [
                            {"id": "qwen-max", "name": "Key B qwen-max"},
                        ]
                    },
                },
            ),
        ]

        result = svc._merge_multi_key_settings(ranked)

        models = result["modelProviders"]["openai"]
        assert len(models) == 1
        assert models[0]["name"] == "Key A qwen-max"
        assert models[0]["generationConfig"]["temperature"] == 0.5

    def test_base_settings_from_highest_priority(self):
        """Non-model settings come from the highest-priority key."""
        svc = self._make_svc()
        ranked = [
            (
                (-10, -100, 1),
                {
                    "modelProviders": {"openai": [{"id": "m1"}]},
                    "theme": "dark",
                    "customField": "from-high-priority",
                },
            ),
            (
                (-5, -100, 2),
                {
                    "modelProviders": {"openai": [{"id": "m2"}]},
                    "theme": "light",
                    "customField": "from-low-priority",
                },
            ),
        ]

        result = svc._merge_multi_key_settings(ranked)

        assert result["theme"] == "dark"
        assert result["customField"] == "from-high-priority"

    def test_no_model_providers_returns_base_settings(self):
        """Keys without modelProviders — base settings returned as-is."""
        svc = self._make_svc()
        ranked = [
            ((-10, -100, 1), {"theme": "dark", "model": "haiku"}),
            ((-5, -100, 2), {"theme": "light"}),
        ]

        result = svc._merge_multi_key_settings(ranked)

        assert result["theme"] == "dark"
        assert result["model"] == "haiku"
        # No modelProviders key injected
        assert "modelProviders" not in result

    def test_same_priority_lower_key_id_wins(self):
        """Same priority — lower key_id (earlier insertion) wins as canonical."""
        svc = self._make_svc()
        ranked = [
            (
                (-10, -100, 5),
                {
                    "modelProviders": {"openai": [{"id": "m1", "name": "Key 5"}]},
                },
            ),
            (
                (-10, -100, 2),
                {
                    "modelProviders": {"openai": [{"id": "m1", "name": "Key 2"}]},
                },
            ),
        ]

        result = svc._merge_multi_key_settings(ranked)

        models = result["modelProviders"]["openai"]
        assert len(models) == 1
        # Key 2 has lower key_id, so after sorting (-10, -100, 2) comes before (-10, -100, 5)
        assert models[0]["name"] == "Key 2"

    def test_does_not_affect_claude_code_settings(self):
        """Claude Code has no modelProviders — merge returns base settings."""
        svc = self._make_svc()
        ranked = [
            ((-10, -100, 1), {"env": {"ANTHROPIC_MODEL": "claude-3"}, "model": "haiku"}),
            ((-5, -100, 2), {"env": {"ANTHROPIC_MODEL": "claude-4"}, "model": "sonnet"}),
        ]

        result = svc._merge_multi_key_settings(ranked)

        assert result["env"]["ANTHROPIC_MODEL"] == "claude-3"
        assert result["model"] == "haiku"

    def test_does_not_affect_codex_settings(self):
        """Codex uses model_providers (snake_case), not modelProviders — merge is a no-op."""
        svc = self._make_svc()
        ranked = [
            (
                (-10, -100, 1),
                {
                    "model_provider": "openace",
                    "model_providers": {"openace": {"name": "Proxy A"}},
                },
            ),
            (
                (-5, -100, 2),
                {
                    "model_provider": "openace",
                    "model_providers": {"openace": {"name": "Proxy B"}},
                },
            ),
        ]

        result = svc._merge_multi_key_settings(ranked)

        assert result["model_provider"] == "openace"
        assert result["model_providers"]["openace"]["name"] == "Proxy A"

    def test_dedup_within_single_key(self):
        """Duplicate model IDs within the same key are ignored."""
        svc = self._make_svc()
        ranked = [
            (
                (-10, -100, 1),
                {
                    "modelProviders": {
                        "openai": [
                            {"id": "qwen-max", "name": "First"},
                            {"id": "qwen-max", "name": "Duplicate"},
                            {"id": "qwen-plus", "name": "Plus"},
                        ]
                    },
                },
            ),
        ]

        result = svc._merge_multi_key_settings(ranked)

        models = result["modelProviders"]["openai"]
        model_ids = [m["id"] for m in models]
        assert model_ids == ["qwen-max", "qwen-plus"]
        # First occurrence wins within the same key
        assert next(m for m in models if m["id"] == "qwen-max")["name"] == "First"
