#!/usr/bin/env python3
"""Unit tests for the model_gateway planner, attribution, mapping, and config layers."""

import json
from unittest.mock import patch

import pytest

from app.modules.workspace.model_gateway.attribution import (
    build_attribution_headers,
    build_body_transformer,
    build_metadata_object,
)
from app.modules.workspace.model_gateway.config import GatewayConfig
from app.modules.workspace.model_gateway.model_mapping import apply_prefix, resolve_model_prefix
from app.modules.workspace.model_gateway.planner import (
    LitellmGatewayPlanner,
    NullGatewayPlanner,
    convert_responses_input_to_chat,
)
from app.utils.llm_proxy_url_validator import LlmProxyValidationResult


def _mock_validate_llm_proxy_url(url, tenant_id, provider, *, resolver=None):
    """Mock validator that allows all URLs for testing."""
    return LlmProxyValidationResult(True)


def _token_payload(**overrides):
    base = {
        "user_id": 7,
        "tenant_id": 3,
        "provider": "openai",
        "session_id": "sess-run-1",
        "tool_name": "qwen-code",
    }
    base.update(overrides)
    return base


# ── Null planner ────────────────────────────────────────────────────────


class TestNullGatewayPlanner:
    def test_is_noop_and_returns_none(self):
        planner = NullGatewayPlanner()
        assert planner.is_noop is True
        assert (
            planner.plan("openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3)
            is None
        )


# ── Planner target URL + misconfigured handling ────────────────────────


class TestLitellmGatewayPlanner:
    def _planner(self, **cfg_overrides):
        cfg = GatewayConfig(
            base_url="https://gateway.example.com/v1",
            api_key="gw-secret",
            **cfg_overrides,
        )
        return LitellmGatewayPlanner(cfg)

    def test_misconfigured_returns_none(self):
        """Enabled but no config -> None (handler surfaces 503, no fallback)."""
        assert (
            LitellmGatewayPlanner(None).plan(
                "openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3
            )
            is None
        )
        # missing key also treated as misconfigured
        cfg = GatewayConfig(base_url="https://x/v1", api_key="")
        assert (
            LitellmGatewayPlanner(cfg).plan(
                "openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3
            )
            is None
        )

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_chat_completions_target_url(self):
        plan = self._planner().plan(
            "openai", "gpt-4", "v1/chat/completions", _token_payload(), "sess-1", 7, 3
        )
        assert plan is not None
        assert plan.mode == "gateway"
        # base ends with /v1 -> v1/ stripped from path
        assert plan.target_url == "https://gateway.example.com/v1/chat/completions"
        assert plan.gateway_key == "gw-secret"
        assert plan.is_responses is False

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_versionless_base_url_keeps_v1(self):
        plan = LitellmGatewayPlanner(
            GatewayConfig(base_url="https://gateway.example.com", api_key="k")
        ).plan("openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3)
        assert plan.target_url == "https://gateway.example.com/v1/chat/completions"

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_responses_path_maps_to_chat_completions(self):
        plan = self._planner().plan(
            "openai", "model-a", "v1/responses", _token_payload(), "sess-1", 7, 3
        )
        assert plan.is_responses is True
        assert plan.target_url.endswith("/chat/completions")
        # transformer should convert the responses body
        out = plan.body_transformer(json.dumps({"model": "model-a", "input": "Hi"}).encode())
        sent = json.loads(out)
        assert sent["messages"] == [{"role": "user", "content": "Hi"}]
        assert sent["stream"] is False
        # metadata survives the conversion (not dropped)
        assert sent["metadata"]["openace_user_id"] == 7

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_attribution_headers_from_token(self):
        plan = self._planner().plan(
            "openai", "gpt-4", "v1/chat/completions", _token_payload(), "sess-1", 7, 3
        )
        assert plan.headers["X-OpenACE-User-Id"] == "7"
        assert plan.headers["X-OpenACE-Tenant-Id"] == "3"
        assert plan.headers["X-OpenACE-Session-Id"] == "sess-1"
        assert plan.headers["X-OpenACE-Tool"] == "qwen-code"
        assert plan.headers["X-OpenACE-Model"] == "gpt-4"
        assert plan.headers["X-OpenACE-Run-Id"] == "sess-run-1"

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_metadata_injected_into_body(self):
        plan = self._planner().plan(
            "openai", "gpt-4", "v1/chat/completions", _token_payload(), "sess-1", 7, 3
        )
        out = plan.body_transformer(json.dumps({"model": "gpt-4", "messages": []}).encode())
        sent = json.loads(out)
        assert sent["metadata"]["openace_user_id"] == 7
        assert sent["metadata"]["openace_session_id"] == "sess-1"
        assert sent["user"] == "7"

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_model_prefix_when_enabled(self):
        plan = self._planner(model_prefix_mode=True).plan(
            "openai", "glm-5", "v1/chat/completions", _token_payload(), "s", 7, 3
        )
        out = plan.body_transformer(json.dumps({"model": "glm-5"}).encode())
        assert json.loads(out)["model"] == "openai/glm-5"

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_model_prefix_off_passthrough(self):
        plan = self._planner(model_prefix_mode=False).plan(
            "openai", "glm-5", "v1/chat/completions", _token_payload(), "s", 7, 3
        )
        out = plan.body_transformer(json.dumps({"model": "glm-5"}).encode())
        assert json.loads(out)["model"] == "glm-5"

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_stream_options_include_usage_hinted(self):
        plan = self._planner().plan(
            "openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3
        )
        out = plan.body_transformer(json.dumps({"model": "gpt-4", "stream": True}).encode())
        sent = json.loads(out)
        assert sent["stream_options"]["include_usage"] is True

    @patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url)
    def test_transformer_passthrough_on_invalid_json(self):
        plan = self._planner().plan(
            "openai", "gpt-4", "v1/chat/completions", _token_payload(), "s", 7, 3
        )
        raw = b"not-json{"
        assert plan.body_transformer(raw) == raw


# ── Responses conversion (pure) ─────────────────────────────────────────


class TestResponsesConversion:
    def test_string_input(self):
        cc = convert_responses_input_to_chat({"model": "m", "input": "Hi"})
        assert cc["messages"] == [{"role": "user", "content": "Hi"}]
        assert cc["stream"] is False

    def test_developer_role_becomes_system(self):
        cc = convert_responses_input_to_chat(
            {"model": "m", "input": [{"role": "developer", "content": "be nice"}]}
        )
        assert any(m["role"] == "system" for m in cc["messages"])

    def test_instructions_prepended(self):
        cc = convert_responses_input_to_chat({"model": "m", "input": "Hi", "instructions": "sys"})
        assert cc["messages"][0] == {"role": "system", "content": "sys"}

    def test_token_params_mapped(self):
        cc = convert_responses_input_to_chat(
            {"model": "m", "input": "Hi", "max_output_tokens": 50, "temperature": 0.5}
        )
        assert cc["max_tokens"] == 50
        assert cc["temperature"] == 0.5

    def test_empty_input_defaults_user_message(self):
        cc = convert_responses_input_to_chat({"model": "m"})
        assert cc["messages"] == [{"role": "user", "content": ""}]


# ── Attribution helpers ──────────────────────────────────────────────────


class TestAttribution:
    def test_headers_never_carry_secrets(self):
        h = build_attribution_headers(_token_payload(), "gpt-4", "s", 7, 3, "openai")
        assert all("Bearer" not in v and "sk-" not in v for v in h.values())

    def test_metadata_object_keys(self):
        m = build_metadata_object(_token_payload(), "gpt-4", "s", 7, 3, "openai")
        assert m["openace_user_id"] == 7
        assert m["openace_provider_hint"] == "openai"
        assert m["openace_run_id"] == "sess-run-1"

    def test_body_transformer_preserves_caller_metadata(self):
        meta = build_metadata_object(_token_payload(), "gpt-4", "s", 7, 3, "openai")
        t = build_body_transformer(meta)
        out = json.loads(t(json.dumps({"metadata": {"trace": "x"}, "model": "m"}).encode()))
        assert out["metadata"]["trace"] == "x"  # caller key preserved
        assert out["metadata"]["openace_user_id"] == 7  # merged


# ── Model mapping ────────────────────────────────────────────────────────


class TestModelMapping:
    @pytest.mark.parametrize(
        "provider,explicit,expected",
        [
            ("openai", None, "openai"),
            ("anthropic", None, "anthropic"),
            ("google", None, "gemini"),
            ("openai", "custom", "custom"),
            ("unknown", None, None),
        ],
    )
    def test_resolve_prefix(self, provider, explicit, expected):
        assert resolve_model_prefix(provider, explicit) == expected

    def test_apply_prefix(self):
        assert apply_prefix("gpt-4", "openai") == "openai/gpt-4"
        assert apply_prefix("openai/gpt-4", "openai") == "openai/gpt-4"  # already tagged
        assert apply_prefix("gpt-4", None) == "gpt-4"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
