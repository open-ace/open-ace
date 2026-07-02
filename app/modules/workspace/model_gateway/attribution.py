"""Build Open ACE attribution headers + LiteLLM metadata and a body transformer.

This is the R3 (attribution forwarding) layer: it produces (a) passthrough HTTP
headers and (b) a non-destructive LiteLLM ``metadata`` object merged into the
request body, both sourced ONLY from the validated proxy token payload. No secrets
ever appear here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app.modules.workspace.model_gateway.model_mapping import apply_prefix

logger = logging.getLogger(__name__)

# Passthrough HTTP headers (depth defense: different gateways read different points).
_OPENACE_USER = "X-OpenACE-User-Id"
_OPENACE_TENANT = "X-OpenACE-Tenant-Id"
_OPENACE_SESSION = "X-OpenACE-Session-Id"
_OPENACE_TOOL = "X-OpenACE-Tool"
_OPENACE_MODEL = "X-OpenACE-Model"
_OPENACE_RUN = "X-OpenACE-Run-Id"
_OPENACE_PROVIDER = "X-OpenACE-Provider"


def build_attribution_headers(
    token_payload: dict[str, Any],
    requested_model: str | None,
    session_id: str,
    user_id: int,
    tenant_id: int,
    provider: str,
) -> dict[str, str]:
    """Forward Open ACE context as passthrough headers for the gateway to log.

    Sourced only from the validated token payload — never from client-supplied
    headers. These headers carry no secrets and are never echoed to the client
    (response headers are whitelisted downstream in the proxy handler).
    """
    run_id = token_payload.get("session_id") or session_id
    return {
        _OPENACE_USER: str(user_id),
        _OPENACE_TENANT: str(tenant_id),
        _OPENACE_SESSION: str(session_id),
        _OPENACE_TOOL: str(token_payload.get("tool_name") or ""),
        _OPENACE_MODEL: str(requested_model or ""),
        _OPENACE_RUN: str(run_id),
        _OPENACE_PROVIDER: str(provider or ""),
    }


def build_metadata_object(
    token_payload: dict[str, Any],
    requested_model: str | None,
    session_id: str,
    user_id: int,
    tenant_id: int,
    provider: str,
) -> dict[str, Any]:
    """Build the LiteLLM-spec ``metadata`` object recorded in its spend/logs DB."""
    return {
        "openace_user_id": user_id,
        "openace_tenant_id": tenant_id,
        "openace_session_id": session_id,
        "openace_tool": token_payload.get("tool_name"),
        "openace_run_id": token_payload.get("session_id") or session_id,
        "openace_provider_hint": provider,
        "openace_model": requested_model,
    }


def build_body_transformer(
    metadata: dict[str, Any],
    model_prefix: str | None = None,
) -> Callable[[bytes], bytes]:
    """Return a callable that merges LiteLLM metadata (+ optional model prefix).

    The transformer parses the request body once, injects a non-destructive
    ``metadata`` object and ``user`` field, optionally rewrites the ``model`` to a
    provider-prefixed alias, hints ``stream_options.include_usage`` when streaming,
    and re-serializes. On parse failure the raw body is returned unchanged so
    metadata injection is best-effort and never breaks a valid request.
    """
    user_id = metadata.get("openace_user_id")

    def transformer(raw: bytes) -> bytes:  # noqa: ANN001
        if not raw:
            return raw
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if not isinstance(data, dict):
            return raw

        # Non-destructive metadata merge (preserve caller-supplied keys).
        existing_meta = data.get("metadata")
        if isinstance(existing_meta, dict):
            merged = dict(existing_meta)
            merged.update(metadata)
            data["metadata"] = merged
        else:
            data["metadata"] = dict(metadata)

        # LiteLLM ``user`` field (string) for spend attribution, only if unset.
        if user_id is not None and "user" not in data:
            data["user"] = str(user_id)

        # Ask the gateway to emit a final usage chunk when streaming so Open ACE
        # can record token usage (LiteLLM omits it otherwise).
        if data.get("stream") is True:
            existing_so = data.get("stream_options")
            if isinstance(existing_so, dict):
                existing_so.setdefault("include_usage", True)
            else:
                data["stream_options"] = {"include_usage": True}

        # Optional provider-prefixed model rewrite.
        if model_prefix:
            data["model"] = apply_prefix(data.get("model"), model_prefix)

        return json.dumps(data).encode("utf-8")

    return transformer
