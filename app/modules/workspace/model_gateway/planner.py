"""Gateway planners: Null (direct mode) and LiteLLM (gateway mode), plus factory.

Mirrors the run_timeline recorder shape: a Null/real strategy pair, an
``is_noop`` short-circuit flag, and a process-wide cached factory resolved from
the toggle. ``plan()`` returns a :class:`GatewayPlan` (gateway mode) or ``None``
(disabled -> direct mode, OR enabled-but-misconfigured -> the handler surfaces a
503 rather than silently falling back).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app.modules.workspace.model_gateway.attribution import (
    build_attribution_headers,
    build_body_transformer,
    build_metadata_object,
)
from app.modules.workspace.model_gateway.config import GatewayConfig, get_gateway_config, is_enabled
from app.modules.workspace.model_gateway.model_mapping import resolve_model_prefix

logger = logging.getLogger(__name__)


@dataclass
class GatewayPlan:
    """A resolved gateway forward plan (single attempt)."""

    target_url: str
    gateway_key: str
    headers: dict[str, str] = field(default_factory=dict)
    body_transformer: Callable[[bytes], bytes] = field(default=lambda raw: raw)
    mode: str = "gateway"
    is_responses: bool = False


class GatewayPlanner:
    """Strategy interface for resolving a gateway plan (or None = direct mode)."""

    is_noop: bool = False

    def plan(
        self,
        provider: str,
        requested_model: str | None,
        path: str,
        token_payload: dict[str, Any],
        session_id: str,
        user_id: int,
        tenant_id: int,
    ) -> GatewayPlan | None:
        raise NotImplementedError


class NullGatewayPlanner(GatewayPlanner):
    """Direct-provider mode. ``plan()`` always returns None.

    The handler's model-gateway seam is skipped entirely when ``is_noop`` is True,
    so direct mode is byte-identical to the pre-feature behavior (R6).
    """

    is_noop = True

    def plan(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401, ARG002
        return None


class LitellmGatewayPlanner(GatewayPlanner):
    """Routes through a LiteLLM-compatible gateway (single attempt, attribution).

    Constructed with a resolved :class:`GatewayConfig` (or None when the gateway is
    enabled but unconfigured, in which case ``plan()`` returns None and the handler
    surfaces a 503 — never a silent fallback to direct mode).
    """

    is_noop = False

    def __init__(self, config: GatewayConfig | None):
        self._config = config

    def plan(
        self,
        provider: str,
        requested_model: str | None,
        path: str,
        token_payload: dict[str, Any],
        session_id: str,
        user_id: int,
        tenant_id: int,
    ) -> GatewayPlan | None:
        cfg = self._config
        if cfg is None or not (cfg.base_url or "").strip() or not (cfg.api_key or "").strip():
            # Enabled but misconfigured -> None; handler returns 503 (no fallback).
            return None

        base_url = cfg.base_url.strip().rstrip("/")
        target_url, is_responses = _resolve_target_url(base_url, path)

        headers = build_attribution_headers(
            token_payload, requested_model, session_id, user_id, tenant_id, provider
        )
        metadata = build_metadata_object(
            token_payload, requested_model, session_id, user_id, tenant_id, provider
        )
        prefix = resolve_model_prefix(provider, cfg.model_prefix) if cfg.model_prefix_mode else None
        base_transformer = build_body_transformer(metadata, model_prefix=prefix)
        body_transformer = (
            _wrap_responses_transformer(base_transformer) if is_responses else base_transformer
        )

        return GatewayPlan(
            target_url=target_url,
            gateway_key=cfg.api_key,
            headers=headers,
            body_transformer=body_transformer,
            mode="gateway",
            is_responses=is_responses,
        )


def _resolve_target_url(base_url: str, path: str) -> tuple[str, bool]:
    """Build the gateway target URL. Returns (url, is_responses_api_request).

    The gateway is OpenAI-compatible (chat/completions); a ``/responses`` request
    is mapped to ``/chat/completions`` and the body transformer converts it.
    """
    is_responses = bool(path) and path.endswith("/responses")
    if is_responses:
        target_path = "v1/chat/completions"
    elif path:
        target_path = path if path.startswith("v1/") else f"v1/{path}"
    else:
        target_path = "v1/chat/completions"

    # Avoid double-versioning when base_url already ends with /v{N}.
    last_seg = base_url.rsplit("/", 1)[-1]
    if last_seg.startswith("v") and last_seg[1:].isdigit() and target_path.startswith("v1/"):
        target_path = target_path[3:]

    return f"{base_url}/{target_path}", is_responses


def convert_responses_input_to_chat(resp_body: dict) -> dict:
    """Convert a Responses-API request body to a chat-completions body.

    Pure function (no Flask deps) so it can be unit-tested directly. Mirrors the
    conversion the direct-provider path performs for non-OpenAI upstreams.
    """
    messages = []
    input_data = resp_body.get("input", "")
    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        for item in input_data:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            content = item.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(part.get("text") or "") for part in content if isinstance(part, dict)
                )
            if role == "developer":
                role = "system"
            messages.append({"role": role, "content": content or ""})

    instructions = resp_body.get("instructions")
    if instructions:
        messages.insert(0, {"role": "system", "content": instructions})
    if not messages:
        messages.append({"role": "user", "content": ""})

    cc_body: dict = {
        "model": resp_body.get("model", ""),
        "messages": messages,
        "stream": False,
    }
    if resp_body.get("max_output_tokens"):
        cc_body["max_tokens"] = resp_body["max_output_tokens"]
    if resp_body.get("temperature") is not None:
        cc_body["temperature"] = resp_body["temperature"]
    return cc_body


def _wrap_responses_transformer(
    base_transformer: Callable[[bytes], bytes],
) -> Callable[[bytes], bytes]:
    """Compose Responses->CC conversion with the base (metadata) transformer.

    Conversion runs first (deterministic ordering, §2.2), then metadata is merged
    into the converted chat-completions body so it is never dropped.
    """

    def transformer(raw: bytes) -> bytes:  # noqa: ANN001
        try:
            resp_body = json.loads(raw)
            if isinstance(resp_body, dict):
                cc_body = convert_responses_input_to_chat(resp_body)
                return base_transformer(json.dumps(cc_body).encode("utf-8"))
        except (ValueError, TypeError) as exc:
            logger.warning("model_gateway: failed to convert /responses body: %s", exc)
        return base_transformer(raw)

    return transformer


# ── Process-wide planner singleton (mirrors get_run_recorder) ──────────────
_planner_instance: GatewayPlanner | None = None
_planner_lock = threading.Lock()


def get_gateway_planner() -> GatewayPlanner:
    """Return the process-wide planner (Litellm when enabled, Null otherwise).

    The choice is resolved once and cached; flipping the flag requires a restart
    (same semantics as the run_timeline recorder). When enabled but unconfigured,
    a Litellm planner with a None config is returned so the handler can surface a
    503 rather than silently falling back to direct mode.
    """
    global _planner_instance
    if _planner_instance is not None:
        return _planner_instance
    with _planner_lock:
        if _planner_instance is None:
            if is_enabled():
                _planner_instance = LitellmGatewayPlanner(get_gateway_config())
            else:
                _planner_instance = NullGatewayPlanner()
    return _planner_instance


def reset_gateway_planner_for_tests() -> None:
    """Clear the cached planner singleton (tests only)."""
    global _planner_instance
    with _planner_lock:
        _planner_instance = None
