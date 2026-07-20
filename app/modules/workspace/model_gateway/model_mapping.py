"""Optional model-name mapping for gateway routing (provider-prefix passthrough).

LiteLLM typically expects ``provider/model`` syntax, but Open ACE stays
provider-agnostic by default (passthrough). When prefix mode is enabled this maps
the token's provider to a LiteLLM provider tag so the gateway can route correctly.
An explicit override always wins. This keeps Open ACE free of provider-specific
model knowledge (R1 scope decision).
"""

from __future__ import annotations




# Open ACE provider token value -> LiteLLM provider tag prefix.
_PROVIDER_PREFIX = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
}


def resolve_model_prefix(provider: str, explicit: str | None = None) -> str | None:
    """Return a LiteLLM provider prefix for the model, or None (passthrough)."""
    if explicit:
        return explicit
    if not provider:
        return None
    return _PROVIDER_PREFIX.get(provider)


def apply_prefix(model: str | None, prefix: str | None) -> str | None:
    """Prefix a bare model id (``gpt-4`` -> ``openai/gpt-4``); leave tagged ids."""
    if not prefix:
        return model
    if not isinstance(model, str) or not model or "/" in model:
        return model
    return f"{prefix}/{model}"
