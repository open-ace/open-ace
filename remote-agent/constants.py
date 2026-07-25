"""Shared constants for the Open ACE remote agent."""

from __future__ import annotations

from typing import Any

# Environment variable keys that carry raw credentials. An autonomous agent
# must NEVER inherit these from the service process — it authenticates through
# a short-lived LLM proxy token (Issue #2019). Both _build_agent_env (local)
# and executor._build_env (remote) scrub static ∪ dynamic (collect_dynamic_env_keys)
# before injecting the proxy token.
SENSITIVE_ENV_KEYS = frozenset(
    {
        # LLM provider keys + base URLs + tokens
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_TOKEN",
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "BAILIAN_CODING_PLAN_API_KEY",
        "OPENCLAW_API_KEY",
        "OPENCLAW_BASE_URL",
        "ZAI_API_KEY",
        # GitHub / SSH — the orchestrator owns all remote mutations
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "SSH_AUTH_SOCK",
        "GIT_ASKPASS",
        # Cloud provider credentials / metadata (curated, not exhaustive)
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
    }
)


def collect_dynamic_env_keys(settings: dict[str, Any]) -> set[str]:
    """Collect dynamic envKey names from modelProviders entries.

    Qwen Code's modelProviders can specify custom envKey names like
    "ZAI_API_KEY" or "BAILIAN_CODING_PLAN_API_KEY". These must also
    be stripped from the env block to prevent API key leakage.

    Args:
        settings: CLI settings dict that may contain modelProviders.

    Returns:
        Set of envKey name strings found in modelProviders.
    """
    dynamic: set[str] = set()
    for provider_models in settings.get("modelProviders", {}).values():
        if isinstance(provider_models, list):
            for model in provider_models:
                if isinstance(model, dict) and isinstance(model.get("envKey"), str):
                    dynamic.add(model["envKey"])
    return dynamic
