"""Shared constants for the Open ACE remote agent."""

from __future__ import annotations

from typing import Any

# Environment variable keys that contain API credentials.
# These must NEVER be written to settings.json — they are injected
# via environment variables at process launch time.
SENSITIVE_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
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
