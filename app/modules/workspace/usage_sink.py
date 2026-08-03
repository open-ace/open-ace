"""
Open ACE - Usage Sink Module

Unified consumption interface for usage recording.
Issue #2184: Multi-provider usage recording with unified sink.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.modules.workspace.usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)


class UsageSink(Protocol):
    """Protocol for usage evidence consumers."""

    def consume(self, evidence: UsageEvidence) -> bool:
        """Consume usage evidence.

        Args:
            evidence: Usage evidence to consume.

        Returns:
            True if successful, False if failed.
        """
        ...


class CompositeSink:
    """Combines multiple sinks, handling partial failures.

    Calls each sink in order, logging failures but not aborting on error.
    """

    def __init__(self, sinks: list[UsageSink]):
        """Initialize composite sink.

        Args:
            sinks: List of sinks to call in order.
        """
        self.sinks = sinks

    def consume(self, evidence: UsageEvidence) -> bool:
        """Consume evidence through all sinks.

        Args:
            evidence: Usage evidence to consume.

        Returns:
            True if all sinks succeeded, False if any failed.
        """
        results = []
        for sink in self.sinks:
            try:
                result = sink.consume(evidence)
                results.append(result)
            except Exception as e:
                sink_name = type(sink).__name__
                logger.error(
                    "Sink %s failed: %s",
                    sink_name,
                    e,
                    extra={
                        "session_id": evidence.session_id,
                        "request_id": evidence.request_id,
                        "provider": evidence.provider,
                    },
                )
                results.append(False)

        if not all(results):
            logger.warning(
                "Partial sink failure",
                extra={
                    "request_id": evidence.request_id,
                    "session_id": evidence.session_id,
                    "results": results,
                },
            )
            return False

        return True


class QuotaSink:
    """Sink for QuotaManager consumption."""

    def consume(self, evidence: UsageEvidence) -> bool:
        """Record usage in quota.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful.
        """
        if evidence.user_id <= 0:
            logger.debug(
                "Skipping quota record: invalid user_id=%s",
                evidence.user_id,
            )
            return True  # Not a failure, just skip

        try:
            from app.modules.governance.quota_manager import QuotaManager

            tokens = evidence.effective_quota_tokens()
            quota_mgr = QuotaManager()
            return quota_mgr.record_usage(
                user_id=evidence.user_id,
                tokens=tokens,
                requests=1,
            )
        except Exception as e:
            logger.error(
                "QuotaSink failed: %s",
                e,
                extra={
                    "user_id": evidence.user_id,
                    "tokens": evidence.effective_quota_tokens(),
                },
            )
            return False


class SessionSink:
    """Sink for SessionManager consumption."""

    def consume(self, evidence: UsageEvidence) -> bool:
        """Record usage in session.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful.
        """
        if not evidence.session_id:
            logger.debug("Skipping session record: no session_id")
            return True  # Not a failure, just skip

        try:
            from app.modules.workspace.session_manager import get_session_manager

            sm = get_session_manager()
            return sm.increment_session_usage(
                session_id=evidence.session_id,
                total_tokens_delta=evidence.total_session_tokens(),
                total_input_delta=evidence.input_tokens,
                total_output_delta=evidence.output_tokens,
                total_cache_read_delta=evidence.cache_read_tokens or 0,
                total_cache_write_delta=evidence.cache_write_tokens or 0,
                request_delta=1,
                tenant_id=evidence.tenant_id,
            )
        except Exception as e:
            logger.error(
                "SessionSink failed: %s",
                e,
                extra={
                    "session_id": evidence.session_id,
                    "tokens": evidence.total_session_tokens(),
                },
            )
            return False


class StatsSink:
    """Sink for DailyStatsRepository refresh."""

    def consume(self, evidence: UsageEvidence) -> bool:
        """Refresh daily stats.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful.
        """
        try:
            from app.repositories.daily_stats_repo import DailyStatsRepository

            DailyStatsRepository().refresh_stats()
            return True
        except Exception as e:
            logger.debug("StatsSink refresh failed (non-critical): %s", e)
            return True  # Non-critical, don't fail the whole pipeline


class DiagnosticsSink:
    """Sink for diagnostic logging.

    This sink always succeeds - it only logs diagnostic information.
    """

    def consume(self, evidence: UsageEvidence) -> bool:
        """Log diagnostics for non-success parse status.

        Args:
            evidence: Usage evidence.

        Returns:
            Always True.
        """
        if evidence.parse_status != "success" and evidence.parse_status != "partial":
            logger.info(
                "usage_parse_diagnostics",
                extra={
                    "session_id": evidence.session_id,
                    "provider": evidence.provider,
                    "protocol": evidence.protocol,
                    "parse_status": evidence.parse_status,
                    "diagnostics": evidence.parse_diagnostics,
                    "request_id": evidence.request_id,
                },
            )

        if evidence.is_indeterminate:
            logger.info(
                "usage_indeterminate_recorded",
                extra={
                    "session_id": evidence.session_id,
                    "provider": evidence.provider,
                    "protocol": evidence.protocol,
                    "request_id": evidence.request_id,
                    "input_tokens": evidence.input_tokens,
                    "output_tokens": evidence.output_tokens,
                },
            )

        return True


class MessageRecordingSink:
    """Sink for recording transcript messages.

    Records user and assistant messages from the request/response.
    """

    def __init__(
        self,
        request_body: bytes | None = None,
        response_body: bytes | None = None,
        output_tokens: int = 0,
        model: str | None = None,
    ):
        """Initialize message recording sink.

        Args:
            request_body: Raw request body bytes.
            response_body: Raw response body bytes.
            output_tokens: Output tokens count.
            model: Model name.
        """
        self.request_body = request_body
        self.response_body = response_body
        self.output_tokens = output_tokens
        self.model = model

    def consume(self, evidence: UsageEvidence) -> bool:
        """Record messages to session.

        This is kept separate from SessionSink because message recording
        is a distinct operation with different error handling requirements.

        Also updates message_count in session after recording messages.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful.
        """
        if not evidence.session_id or not self.response_body:
            return True

        try:
            from app.modules.workspace.session_manager import get_session_manager

            sm = get_session_manager()

            # Record messages from request/response
            # This mirrors the existing _record_messages logic
            message_delta = _record_messages_internal(
                sm=sm,
                session_id=evidence.session_id,
                request_body=self.request_body,
                response_body=self.response_body,
                output_tokens=self.output_tokens or evidence.output_tokens,
                model=self.model or evidence.model,
            )

            # Update message_count in session if any messages were inserted
            if message_delta > 0:
                sm.increment_session_usage(
                    session_id=evidence.session_id,
                    message_delta=message_delta,
                    tenant_id=evidence.tenant_id,
                )

            return True
        except Exception as e:
            logger.debug("MessageRecordingSink failed (non-critical): %s", e)
            return True  # Non-critical


def _record_messages_internal(
    sm,
    session_id: str,
    request_body: bytes | None,
    response_body: bytes,
    output_tokens: int,
    model: str | None,
) -> int:
    """Internal helper to record messages.

    Mirrors the existing _record_messages logic from llm_proxy_handler.py
    but using UsageEvidence context.
    """
    import json

    message_delta = 0
    try:
        # Parse user messages from request body
        if request_body:
            try:
                req_data = json.loads(request_body)
                messages = req_data.get("messages", [])
                if isinstance(messages, list) and messages:
                    # Record the last user message
                    user_content = None
                    for msg in reversed(messages):
                        if not isinstance(msg, dict):
                            continue
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, list):
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
                            content=user_content[:10000],
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


def create_default_sink(
    request_body: bytes | None = None,
    response_body: bytes | None = None,
    output_tokens: int = 0,
    model: str | None = None,
) -> CompositeSink:
    """Create the default composite sink for usage recording.

    Args:
        request_body: Raw request body bytes (for message recording).
        response_body: Raw response body bytes (for message recording).
        output_tokens: Output tokens count.
        model: Model name.

    Returns:
        CompositeSink with all default sinks.
    """
    sinks: list[UsageSink] = [
        QuotaSink(),
        SessionSink(),
        StatsSink(),
        DiagnosticsSink(),
    ]

    # Add message recording sink if we have response body
    if response_body:
        sinks.append(
            MessageRecordingSink(
                request_body=request_body,
                response_body=response_body,
                output_tokens=output_tokens,
                model=model,
            )
        )

    return CompositeSink(sinks)
