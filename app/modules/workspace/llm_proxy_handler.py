"""Shared LLM proxy request handling for local and remote workspace scopes."""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)


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
        # path comes from the client's OPENAI_BASE_URL which always includes /v1;
        # strip it to avoid double-versioning (e.g. /v4/v1/chat/completions)
        if path.startswith("v1/"):
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


def _record_llm_usage(
    content: bytes, session_id: str, user_id: int, provider: str, content_type: str
) -> None:
    """Extract and record token usage from LLM responses."""
    try:
        if b"usage" not in content:
            return

        usage = None
        try:
            data = json.loads(content)
            usage = data.get("usage", {})
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
        if session:
            session.total_input_tokens = (session.total_input_tokens or 0) + input_tokens
            session.total_output_tokens = (session.total_output_tokens or 0) + output_tokens
            session.total_tokens = (session.total_tokens or 0) + input_tokens + output_tokens
            session.request_count = (session.request_count or 0) + 1
            sm.update_session(session)

        try:
            from app.repositories.daily_stats_repo import DailyStatsRepository

            DailyStatsRepository().refresh_stats()
        except Exception:
            pass
    except Exception:
        logger.debug("Failed to record LLM usage", exc_info=True)


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

    requested_model = _extract_requested_model()
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

        api_key, base_url, key_id = key_result
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
            import requests as http_requests

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

            resp = http_requests.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                data=body,
                stream=True,
                timeout=120,
                proxies={"http": None, "https": None},  # type: ignore[dict-item]
            )

            if resp.status_code >= 400:
                peek = (
                    resp.content[:500]
                    if not resp.headers.get("Content-Type", "").startswith("text/event-stream")
                    else b""
                )
                logger.error(
                    "LLM proxy error %d from %s key_id=%s: %s",
                    resp.status_code,
                    original_target_url,
                    key_id,
                    peek.decode("utf-8", errors="replace"),
                )
                if resp.status_code in (401, 403, 429):
                    exclude_key_ids.add(key_id)
                    continue

            if converted_from_responses and resp.status_code == 200:
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

            content_type = resp.headers.get("Content-Type", "")

            def generate(_resp=resp, _content_type=content_type):
                total_content = b""
                for chunk in _resp.iter_content(chunk_size=4096):
                    total_content += chunk
                    yield chunk
                try:
                    _record_llm_usage(total_content, session_id, user_id, provider, _content_type)
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
                _record_llm_usage(content, session_id, user_id, provider, content_type)
            except Exception as exc:
                logger.error("Failed to record LLM usage: %s", exc)
            return Response(
                content,
                status=resp.status_code,
                headers=response_headers,
                content_type=content_type,
            )

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
