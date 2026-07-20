"""Shared LLM proxy request handling for local and remote workspace scopes.

Security: All outbound requests via custom base_url are validated against SSRF
attacks using app.utils.llm_proxy_request.safe_llm_proxy_request (Issue #1894).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Response, g, jsonify, request, stream_with_context

# ── Model-gateway seam (removable) ────────────────────────────────────────
# When the LiteLLM-compatible model gateway is enabled, ``get_gateway_planner()``
# returns a real planner and the seam below routes through it. Removing the
# gateway feature = drop this import + the seam + the two _gateway_* helpers.
from app.modules.workspace.model_gateway import get_gateway_planner
from app.utils.llm_proxy_request import safe_llm_proxy_request

logger = logging.getLogger(__name__)

# Shared ContentFilter instance for performance (uses cached rules)
_content_filter_instance = None


def _get_content_filter():
    """Get or create shared ContentFilter instance."""
    global _content_filter_instance
    if _content_filter_instance is None:
        from app.modules.governance.content_filter import ContentFilter
        from app.repositories.governance_repo import GovernanceRepository

        governance_repo = GovernanceRepository()
        _content_filter_instance = ContentFilter(governance_repo=governance_repo)
    return _content_filter_instance


def _extract_requested_model() -> str | None:
    """Best-effort extraction of the requested model from the proxied request body."""
    try:
        body_json = json.loads(request.get_data())
    except Exception:
        return None
    model = body_json.get("model")
    return model if isinstance(model, str) and model else None


def _resolve_allowed_key_ids(
    token_payload: dict[str, Any],
    requested_model: str | None,
) -> list[int] | None:
    """Resolve the key-id subset allowed for this request from token HA metadata."""
    candidate_keys = token_payload.get("ha_candidate_keys")
    model_key_ids = token_payload.get("ha_model_key_ids")
    if not isinstance(candidate_keys, list) or not isinstance(model_key_ids, dict):
        return None

    all_key_ids = [
        int(candidate["key_id"])
        for candidate in candidate_keys
        if isinstance(candidate, dict) and candidate.get("key_id") is not None
    ]
    if not requested_model:
        return all_key_ids

    raw_allowed = model_key_ids.get(requested_model)
    if not isinstance(raw_allowed, list):
        return []
    return [int(key_id) for key_id in raw_allowed]


def _determine_target_url(
    provider: str,
    base_url: str | None,
    path: str,
) -> str | tuple[Response, int]:
    """Build the upstream target URL.  Returns a (Response, status) tuple on validation error."""
    if base_url:
        target_base = base_url.rstrip("/")
        # When base_url already contains a version segment (e.g. /v4), strip the
        # v1/ prefix from path to avoid double-versioning (/v4/v1/chat/completions).
        # Only strip when base_url ends with a /v{N} pattern; versionless base_urls
        # (e.g. https://custom.api.com) rely on the v1/ in path.
        last_segment = target_base.rsplit("/", 1)[-1]
        if last_segment.startswith("v") and last_segment[1:].isdigit() and path.startswith("v1/"):
            path = path[3:]
    else:
        provider_urls = {
            "openai": "https://api.openai.com",
            "anthropic": "https://api.anthropic.com",
            "google": "https://generativelanguage.googleapis.com",
        }
        target_base = provider_urls.get(provider, "https://api.openai.com")

    if path:
        suffix = f"/{path}"
    else:
        suffix = request.path.split("/llm-proxy")[-1]

    # Reject path-traversal attempts (e.g. "/../../internal")
    if ".." in suffix.split("/"):
        return (
            jsonify({"error": {"message": "Invalid path", "type": "proxy_error"}}),
            400,
        )

    return f"{target_base}{suffix}"


def _record_messages(
    sm: Any,
    session_id: str,
    request_body: bytes | None,
    response_body: bytes,
    output_tokens: int,
    model: str | None = None,
) -> int:
    """Parse request/response and record messages to session_messages."""
    message_delta = 0
    try:
        # Parse user messages from request body
        if request_body:
            try:
                req_data = json.loads(request_body)
                messages = req_data.get("messages", [])
                if isinstance(messages, list) and messages:
                    # Record the last user message (avoid duplicates)
                    user_content = None
                    for msg in reversed(messages):
                        if not isinstance(msg, dict):
                            continue
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                # Handle multi-part content
                                text_parts = []
                                for part in content:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        text_parts.append(part.get("text", ""))
                                user_content = " ".join(text_parts)
                            elif isinstance(content, str):
                                user_content = content
                            if user_content:
                                break

                    if user_content:
                        stored = sm.append_transcript_message(
                            session_id=session_id,
                            role="user",
                            content=user_content[:10000],  # Truncate to prevent overflow
                            source="llm_proxy",
                        )
                        if getattr(stored, "_was_inserted", False):
                            message_delta += 1
            except (json.JSONDecodeError, ValueError):
                pass

        # Parse assistant message from response body
        if response_body:
            try:
                resp_data = json.loads(response_body)
                choices = resp_data.get("choices", [])
                if isinstance(choices, list) and choices:
                    choice = choices[0]
                    if isinstance(choice, dict):
                        msg = choice.get("message", {})
                        if isinstance(msg, dict) and msg.get("role") == "assistant":
                            content = msg.get("content", "")
                            if isinstance(content, str) and content:
                                stored = sm.append_transcript_message(
                                    session_id=session_id,
                                    role="assistant",
                                    content=content[:10000],
                                    tokens_used=output_tokens,
                                    model=model or resp_data.get("model"),
                                    source="llm_proxy",
                                )
                                if getattr(stored, "_was_inserted", False):
                                    message_delta += 1
            except (json.JSONDecodeError, ValueError):
                # Handle SSE streaming response - accumulate delta content
                assistant_content_parts = []
                for line in response_body.split(b"\n"):
                    line = line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    payload = line[len(b"data:") :].strip()
                    if payload == b"[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content_part = delta.get("content")
                            if content_part:
                                assistant_content_parts.append(content_part)
                    except (json.JSONDecodeError, ValueError):
                        continue

                # Record accumulated assistant message for streaming response
                if assistant_content_parts:
                    full_content = "".join(assistant_content_parts)
                    if full_content:
                        stored = sm.append_transcript_message(
                            session_id=session_id,
                            role="assistant",
                            content=full_content[:10000],
                            tokens_used=output_tokens,
                            model=model,
                            source="llm_proxy",
                        )
                        if getattr(stored, "_was_inserted", False):
                            message_delta += 1
    except Exception:
        logger.debug("Failed to record messages", exc_info=True)
    return message_delta


def _record_llm_usage(
    content: bytes,
    session_id: str,
    user_id: int,
    provider: str,
    content_type: str,
    request_body: bytes | None = None,
) -> None:
    """Extract and record token usage and messages from LLM responses."""
    try:
        if b"usage" not in content:
            return

        usage = None
        response_model = None
        try:
            data = json.loads(content)
            usage = data.get("usage", {})
            response_model = data.get("model")
        except json.JSONDecodeError:
            for line in content.split(b"\n"):
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                payload = line[len(b"data:") :].strip()
                if payload == b"[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    continue
                if "usage" in chunk:
                    usage = chunk["usage"]
                    response_model = chunk.get("model")
                    break

        if not usage or not isinstance(usage, dict):
            return

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        if not input_tokens and not output_tokens:
            return

        from app.modules.governance.quota_manager import QuotaManager
        from app.modules.workspace.session_manager import get_session_manager

        quota_mgr = QuotaManager()
        quota_mgr.record_usage(
            user_id=user_id,
            tokens=input_tokens + output_tokens,
            requests=1,
        )

        sm = get_session_manager()
        session = sm.get_session(session_id)

        # Auto-create session if not exists (for WebUI sessions like "webui:1")
        if not session:
            try:
                session = sm.create_session(
                    session_id=session_id,
                    session_type="webui",
                    tool_name="qwen-code",
                    user_id=user_id,
                    title="WebUI Session",
                )
                logger.info("Auto-created session %s for user %d", session_id, user_id)
            except Exception as exc:
                logger.warning("Failed to auto-create session %s: %s", session_id, exc)
                # Retry get_session - another concurrent request may have created it
                session = sm.get_session(session_id)
                if not session:
                    logger.error("Session %s still not found after retry", session_id)
                    return

        # Record transcript first; summary is updated explicitly afterwards so
        # transcript persistence never owns agent_sessions side effects.
        message_delta = _record_messages(
            sm=sm,
            session_id=session_id,
            request_body=request_body,
            response_body=content,
            output_tokens=output_tokens,
            model=response_model,
        )
        sm.increment_session_usage(
            session_id,
            message_delta=message_delta,
            request_delta=1,
            total_tokens_delta=input_tokens + output_tokens,
            total_input_delta=input_tokens,
            total_output_delta=output_tokens,
        )

        try:
            from app.repositories.daily_stats_repo import DailyStatsRepository

            DailyStatsRepository().refresh_stats()
        except Exception:
            pass
    except Exception:
        logger.debug("Failed to record LLM usage", exc_info=True)


def _emit_responses_sse(resp: Any, body: bytes | None) -> Response | None:
    """Convert an upstream chat-completions response into a Responses-API SSE stream.

    Used when a ``/responses`` request was rewritten to ``/chat/completions`` for a
    non-OpenAI upstream. Returns the streaming ``Response`` on success, or ``None``
    when conversion fails so the caller falls through to the normal finalize path
    (mirrors the pre-extraction fall-through behavior exactly).
    """
    try:
        import uuid as _uuid

        cc_resp = resp.json()
        response_id = f"resp_{cc_resp.get('id', 'default')}"
        model = cc_resp.get("model", "")
        output_text = ""
        if cc_resp.get("choices"):
            output_text = cc_resp["choices"][0].get("message", {}).get("content", "")
        usage = cc_resp.get("usage", {})
        item_id = f"msg_{_uuid.uuid4().hex[:24]}"
        events = [
            {
                "type": "response.created",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                    "usage": None,
                },
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": item_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            },
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": ""},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": output_text,
            },
            {
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": output_text,
            },
            {
                "type": "response.content_part.done",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": output_text},
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": item_id,
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": output_text}],
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "completed",
                    "model": model,
                    "output": [
                        {
                            "type": "message",
                            "id": item_id,
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": output_text}],
                        }
                    ],
                    "usage": (
                        {
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }
                        if usage
                        else None
                    ),
                },
            },
        ]

        def sse_stream(_events=events):
            for event in _events:
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

        return Response(
            sse_stream(),
            status=200,
            content_type="text/event-stream",
        )
    except Exception as exc:
        logger.error("Failed to convert CC response to Responses format: %s", exc)
        return None


def _finalize_upstream_response(
    resp: Any,
    body: bytes | None,
    session_id: str,
    user_id: int,
    provider: str,
    content_type: str | None = None,
) -> Response:
    """Stream or return an upstream response, recording LLM usage on completion.

    Shared by the direct-provider path and the model-gateway path so response
    handling + usage recording lives in exactly one place (this is the shared tail
    that lets the gateway seam stay small and removable). For ``text/event-stream``
    the usage is recorded after the stream drains; otherwise immediately.
    """
    if content_type is None:
        content_type = resp.headers.get("Content-Type", "")

    def generate(_resp=resp, _content_type=content_type, _body=body):
        total_content = b""
        for chunk in _resp.iter_content(chunk_size=4096):
            total_content += chunk
            yield chunk
        try:
            _record_llm_usage(
                total_content,
                session_id,
                user_id,
                provider,
                _content_type,
                request_body=_body,
            )
        except Exception as exc:
            logger.error("Failed to record LLM usage: %s", exc)

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() in ("content-type", "x-request-id", "openai-organization"):
            response_headers[key] = value

    if "text/event-stream" in content_type:
        return Response(
            stream_with_context(generate()),
            status=resp.status_code,
            headers=response_headers,
            content_type=content_type,
        )

    content = resp.content
    try:
        _record_llm_usage(content, session_id, user_id, provider, content_type, request_body=body)
    except Exception as exc:
        logger.error("Failed to record LLM usage: %s", exc)
    return Response(
        content,
        status=resp.status_code,
        headers=response_headers,
        content_type=content_type,
    )


def _gateway_error_response(resp: Any, gateway_key: str) -> tuple[Response, int]:
    """Sanitize an upstream gateway error so the gateway key never leaks.

    Redacts any literal key occurrence, truncates to the existing 500-char peek
    limit, and re-wraps into the standard ``{error:{message,type}}`` shape.
    """
    try:
        content_type = resp.headers.get("Content-Type", "")
        peek = resp.content[:500] if not content_type.startswith("text/event-stream") else b""
        error_text = peek.decode("utf-8", errors="replace")
        if gateway_key and gateway_key in error_text:
            error_text = error_text.replace(gateway_key, "[REDACTED]")
        logger.error("LLM proxy gateway error %d: %s", resp.status_code, error_text)
        message = error_text or f"Gateway returned status {resp.status_code}"
        return (
            jsonify({"error": {"message": message, "type": "upstream_error"}}),
            resp.status_code,
        )
    except Exception as exc:
        logger.error("LLM proxy gateway error (sanitize failed): %s", exc)
        return (
            jsonify({"error": {"message": "Upstream gateway error", "type": "proxy_error"}}),
            502,
        )


def _check_content_filter(
    user_id: int,
    username: str | None,
    request_body: bytes | None,
) -> tuple[Response, int] | str | None:
    """Check user input content for sensitive information.

    Args:
        user_id: User ID for audit logging.
        username: Username for audit logging.
        request_body: Raw request body bytes.

    Returns:
        - tuple(Response, int): Error response if blocked (403)
        - str: Redacted content if action=redact (caller should modify request)
        - None: If passed or action=warn (caller should continue normally)
    """
    if not request_body:
        return None

    try:
        req_data = json.loads(request_body)
        messages = req_data.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return None

        # Extract user messages for content filter check
        user_contents = []
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Handle multi-part content
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    user_contents.append(" ".join(text_parts))
                elif isinstance(content, str):
                    user_contents.append(content)

        if not user_contents:
            return None

        # Join all user messages for filtering check
        combined_content = " ".join(user_contents)

        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        content_filter = _get_content_filter()
        audit_logger = AuditLogger()

        result = content_filter.check_content(combined_content)

        if result.action == "block":
            # Log the block action
            audit_logger.log_action(
                action=AuditAction.CONTENT_BLOCKED,
                user_id=user_id,
                username=username,
                resource_type="content",
                severity="high",
                details={
                    "risk_level": result.risk_level,
                    "matched_rules": result.matched_rules,
                    "message": result.message,
                },
            )
            return (
                jsonify(
                    {
                        "error": {
                            "message": result.message or "Content blocked by content filter",
                            "type": "content_blocked",
                            "matched_rules": result.matched_rules,
                            "suggestion": result.suggestion,
                        }
                    }
                ),
                403,
            )

        if result.action == "warn":
            # Log the warn action
            audit_logger.log_action(
                action=AuditAction.CONTENT_WARNED,
                user_id=user_id,
                username=username,
                resource_type="content",
                severity="medium",
                details={
                    "risk_level": result.risk_level,
                    "matched_rules": result.matched_rules,
                    "message": result.message,
                },
            )
            # Warn: continue normally, the warning is logged but not returned to user
            # (Frontend will show toast based on response headers or separate API)
            return None

        if result.action == "redact":
            # Log the redact action
            audit_logger.log_action(
                action=AuditAction.CONTENT_REDACTED,
                user_id=user_id,
                username=username,
                resource_type="content",
                severity="medium",
                details={
                    "risk_level": result.risk_level,
                    "matched_rules": result.matched_rules,
                    "original_content": result.original_content,
                    "redacted_content": result.redacted_content,
                },
            )
            # Redact: return redacted content for caller to modify request
            return result.redacted_content or combined_content

        return None

    except json.JSONDecodeError:
        return None
    except Exception as exc:
        logger.warning("Content filter check failed, allowing request: %s", exc)
        return None


def _forward_via_gateway(
    plan: Any,
    *,
    session_id: str,
    user_id: int,
    provider: str,
    tenant_id: int,
) -> Response | tuple[Response, int]:
    """Execute a single gateway attempt and return the finalized response.

    Single attempt (no per-key HA failover — the gateway owns upstream keys),
    gateway credentials + attribution headers. Quota was already checked upstream
    of this call. Usage recording + streaming passthrough are handled by the
    shared ``_finalize_upstream_response`` tail. Errors are sanitized via
    ``_gateway_error_response`` so the gateway key never leaks (R7).

    Security: Uses safe_llm_proxy_request for SSRF protection (Issue #1894).
    """
    try:
        fwd_headers = {}
        for key, value in request.headers:
            if key.lower() in ("content-type", "accept", "user-agent"):
                fwd_headers[key] = value
        fwd_headers.update(plan.headers)
        fwd_headers["Authorization"] = f"Bearer {plan.gateway_key}"

        body = plan.body_transformer(request.get_data())

        # Use safe_llm_proxy_request for SSRF protection
        resp = safe_llm_proxy_request(
            request.method,
            plan.target_url,
            tenant_id=tenant_id,
            provider=provider,
            user_id=user_id,
            headers=fwd_headers,
            data=body,
            stream=True,
            timeout=120,
            source="request",
        )

        # Handle SSRF blocking response
        if isinstance(resp, tuple):
            return resp

        if resp.status_code >= 400:
            return _gateway_error_response(resp, plan.gateway_key)

        # Mirror the direct path: a converted /responses request gets its
        # chat-completions response re-wrapped into a Responses-API SSE stream.
        if getattr(plan, "is_responses", False) and resp.status_code == 200:
            sse_response = _emit_responses_sse(resp, body)
            if sse_response is not None:
                return sse_response

        return _finalize_upstream_response(resp, body, session_id, user_id, provider)
    except Exception as exc:
        logger.error("LLM proxy gateway error: %s", exc)
        return (
            jsonify({"error": {"message": "Internal proxy error", "type": "proxy_error"}}),
            502,
        )


def handle_llm_proxy_request(
    *,
    scope: str,
    api_proxy: Any,
    path: str = "",
):
    """Handle a proxied LLM request for a workspace scope."""
    auth_header = request.headers.get("Authorization", "")
    proxy_token = auth_header.replace("Bearer ", "").strip()
    if not proxy_token:
        proxy_token = request.headers.get("x-api-key", "").strip()

    if not proxy_token:
        if request.method == "HEAD":
            return "", 401
        return (
            jsonify({"error": {"message": "Missing authorization token", "type": "auth_error"}}),
            401,
        )

    token_payload = api_proxy.validate_proxy_token(proxy_token)
    if not token_payload:
        return (
            jsonify({"error": {"message": "Invalid or expired proxy token", "type": "auth_error"}}),
            401,
        )

    token_scope = token_payload.get("scope")
    if token_scope and token_scope != scope:
        return (
            jsonify(
                {
                    "error": {
                        "message": f"Proxy token is scoped for '{token_scope}', not '{scope}'",
                        "type": "auth_error",
                    }
                }
            ),
            403,
        )

    user_id = int(token_payload["user_id"])
    tenant_id = int(token_payload["tenant_id"])
    provider = str(token_payload["provider"])
    # Allow X-Session-Id header to override token session_id for WebUI conversations
    request_session_id = request.headers.get("X-Session-Id")
    if request_session_id:
        # Validate header format: alphanumeric, hyphens, underscores, colons only
        # Max length 100 chars to prevent abuse
        if len(request_session_id) > 100 or not all(
            c.isalnum() or c in "-_:" for c in request_session_id
        ):
            logger.warning(
                "Invalid X-Session-Id header format, falling back to token: %s",
                request_session_id[:50],
            )
            session_id = str(token_payload["session_id"])
        else:
            session_id = request_session_id
            logger.debug("Using X-Session-Id header: %s", session_id)
    else:
        session_id = str(token_payload["session_id"])

    try:
        from app.modules.governance.quota_manager import QuotaManager

        quota_mgr = QuotaManager()
        quota_result = quota_mgr.check_quota(user_id)
        if not quota_result["allowed"]:
            return (
                jsonify(
                    {
                        "error": {
                            "message": f"Quota exceeded: {quota_result['reason']}",
                            "type": "quota_exceeded",
                        }
                    }
                ),
                429,
            )
    except Exception as exc:
        logger.error("Quota check failed, denying request for safety: %s", exc)
        return (
            jsonify(
                {
                    "error": {
                        "message": "Quota check unavailable - request denied for safety",
                        "type": "quota_check_error",
                    }
                }
            ),
            429,
        )

    # ── Content filter check for user input ──────────────────────────────
    # Check user messages for sensitive content before forwarding to LLM.
    # Block: return 403, Warn: log and continue, Redact: modify content.
    username = g.user.get("username") if hasattr(g, "user") else None
    content_filter_result = _check_content_filter(
        user_id=user_id,
        username=username,
        request_body=request.get_data(),
    )
    if isinstance(content_filter_result, tuple):
        # Block: return error response
        return content_filter_result
    # Note: redact handling would require modifying request body, which is
    # complex for streaming. For now, we just log and continue for warn/redact.
    # ── end content filter check ─────────────────────────────────────────

    requested_model = _extract_requested_model()

    # ── Model-gateway seam (single, removable) ───────────────────────────
    # When the LiteLLM-compatible gateway is enabled, route this request through
    # it: single attempt, attribution headers, quota already checked above.
    # ``is_noop`` means disabled -> direct-provider mode runs unchanged (R6).
    # Enabled-but-misconfigured -> 503, never a silent fallback to direct mode.
    _gateway = get_gateway_planner()
    if not _gateway.is_noop:
        _gateway_plan = _gateway.plan(
            provider=provider,
            requested_model=requested_model,
            path=path,
            token_payload=token_payload,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if _gateway_plan is None:
            return (
                jsonify(
                    {
                        "error": {
                            "message": (
                                "Model gateway is enabled but not configured. "
                                "Set a gateway base URL and API key, or disable gateway mode."
                            ),
                            "type": "gateway_misconfigured",
                        }
                    }
                ),
                503,
            )
        return _forward_via_gateway(
            _gateway_plan, session_id=session_id, user_id=user_id, provider=provider, tenant_id=tenant_id
        )
    # ── end model-gateway seam ───────────────────────────────────────────

    allowed_key_ids = _resolve_allowed_key_ids(token_payload, requested_model)
    if (
        allowed_key_ids is None
        and scope == "local"
        and provider == "openai"
        and token_payload.get("tool_name") == "qwen-code"
    ):
        dynamic_pool = api_proxy.get_tool_model_pool(
            tenant_id=tenant_id,
            tool_name="qwen-code",
            scope="local",
            provider="openai",
        )
        if requested_model:
            allowed_key_ids = dynamic_pool.get("model_key_ids", {}).get(requested_model, [])
        else:
            allowed_key_ids = [
                int(candidate["key_id"])
                for candidate in dynamic_pool.get("candidate_keys", [])
                if isinstance(candidate, dict) and candidate.get("key_id") is not None
            ]
    if allowed_key_ids == []:
        message = (
            f"No configured API key supports model '{requested_model}'"
            if requested_model
            else "No API keys available in this session pool"
        )
        return (
            jsonify(
                {
                    "error": {
                        "message": message,
                        "type": "config_error",
                    }
                }
            ),
            500,
        )

    exclude_key_ids: set[int] = set()
    attempt = 0

    while True:
        attempt += 1
        if allowed_key_ids is not None:
            key_result = api_proxy.resolve_api_key_from_key_ids(
                tenant_id,
                provider,
                allowed_key_ids,
                exclude_key_ids=exclude_key_ids,
            )
        else:
            key_result = api_proxy.resolve_api_key_for_scope(
                tenant_id,
                provider,
                scope=scope,
                exclude_key_ids=exclude_key_ids,
            )

        if not key_result:
            if exclude_key_ids:
                return (
                    jsonify(
                        {
                            "error": {
                                "message": (
                                    f"All {len(exclude_key_ids)} API key(s) failed "
                                    f"for provider '{provider}'"
                                ),
                                "type": "upstream_error",
                            }
                        }
                    ),
                    502,
                )
            return (
                jsonify(
                    {
                        "error": {
                            "message": f"No API key configured for provider '{provider}'",
                            "type": "config_error",
                        }
                    }
                ),
                500,
            )

        api_key, base_url, key_id, _ = key_result
        target_result = _determine_target_url(provider, base_url, path)
        if isinstance(target_result, tuple):
            return target_result
        target_url = target_result

        logger.info(
            "LLM proxy: %s -> %s model=%s provider=%s key_id=%s attempt=%d scope=%s",
            request.method,
            target_url,
            requested_model or "?",
            provider,
            key_id,
            attempt,
            scope,
        )

        original_target_url = target_url
        converted_from_responses = False
        is_real_openai = "api.openai.com" in target_url
        if path and path.endswith("/responses") and not is_real_openai:
            try:
                resp_body = json.loads(request.get_data())
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
                                str(part.get("text") or "")
                                for part in content
                                if isinstance(part, dict)
                            )
                        if role == "developer":
                            role = "system"
                        messages.append({"role": role, "content": content or ""})

                instructions = resp_body.get("instructions")
                if instructions:
                    messages.insert(0, {"role": "system", "content": instructions})
                if not messages:
                    logger.warning(
                        "Responses API conversion: no messages extracted from input=%s",
                        type(input_data).__name__,
                    )
                    messages.append({"role": "user", "content": ""})

                cc_body = {
                    "model": resp_body.get("model", ""),
                    "messages": messages,
                    "stream": False,
                }
                if resp_body.get("max_output_tokens"):
                    cc_body["max_tokens"] = resp_body["max_output_tokens"]
                if resp_body.get("temperature") is not None:
                    cc_body["temperature"] = resp_body["temperature"]

                target_url = target_url.replace("/responses", "/chat/completions")
                body = json.dumps(cc_body).encode("utf-8")
                converted_from_responses = True
            except Exception as exc:
                logger.warning("Failed to convert Responses API request: %s", exc)

        try:
            # ── SSRF Protection (Issue #1894) ───────────────────────────────
            # Use safe_llm_proxy_request to validate base_url and prevent
            # SSRF attacks via administrator-configured endpoints.
            fwd_headers = {}
            for key, value in request.headers:
                if key.lower() in ("content-type", "accept", "user-agent"):
                    fwd_headers[key] = value

            if provider == "anthropic":
                fwd_headers["x-api-key"] = api_key
                fwd_headers["anthropic-version"] = "2023-06-01"
            else:
                fwd_headers["Authorization"] = f"Bearer {api_key}"

            if not converted_from_responses:
                body = request.get_data()

            resp = safe_llm_proxy_request(
                request.method,
                target_url,
                tenant_id=tenant_id,
                provider=provider,
                user_id=user_id,
                api_key_id=key_id,
                headers=fwd_headers,
                data=body,
                stream=True,
                timeout=120,
                source="request",
            )

            # Handle SSRF blocking response
            if isinstance(resp, tuple):
                return resp

            if resp.status_code >= 400:
                peek = (
                    resp.content[:500]
                    if not resp.headers.get("Content-Type", "").startswith("text/event-stream")
                    else b""
                )
                error_text = peek.decode("utf-8", errors="replace")
                logger.error(
                    "LLM proxy error %d from %s key_id=%s: %s",
                    resp.status_code,
                    original_target_url,
                    key_id,
                    error_text,
                )

                # 检测上游 quota exceeded 错误并触发告警 (Issue #1060)
                if resp.status_code == 429 and "quota exceeded" in error_text.lower():
                    try:
                        from app.modules.governance.alert_notifier import (
                            create_quota_alert,
                            get_alert_notifier,
                        )
                        from app.repositories.user_repo import UserRepository

                        # 去重检查：1小时内已有同类告警则跳过
                        notifier = get_alert_notifier()
                        if notifier.has_recent_quota_alert(user_id, "platform", hours=1):
                            logger.debug(
                                "Skipping duplicate quota exceeded alert for user %d", user_id
                            )
                        else:
                            user_repo = UserRepository()
                            user = user_repo.get_user_by_id(user_id)
                            username = user.get("username", "unknown") if user else "unknown"

                            create_quota_alert(
                                user_id=user_id,
                                username=username,
                                usage_percent=100,
                                quota_type="platform",
                            )
                            logger.warning(
                                "Upstream quota exceeded alert created for user %d", user_id
                            )
                    except Exception as alert_exc:
                        logger.error("Failed to create quota exceeded alert: %s", alert_exc)

                    # 返回明确的 quota exceeded 错误
                    return (
                        jsonify(
                            {
                                "error": {
                                    "message": "Platform quota exceeded. Please wait or contact administrator.",
                                    "type": "quota_exceeded",
                                }
                            }
                        ),
                        429,
                    )

                if resp.status_code in (401, 403, 429):
                    exclude_key_ids.add(key_id)
                    continue

            if converted_from_responses and resp.status_code == 200:
                sse_response = _emit_responses_sse(resp, body)
                if sse_response is not None:
                    return sse_response

            return _finalize_upstream_response(resp, body, session_id, user_id, provider)

        except Exception as exc:
            logger.error("LLM proxy error: %s", exc)
            return (
                jsonify(
                    {
                        "error": {
                            "message": "Internal proxy error",
                            "type": "proxy_error",
                        }
                    }
                ),
                502,
            )
