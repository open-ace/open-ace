"""
Open ACE - Usage Sink Module

Unified consumption interface for usage recording.
Issue #2184: Multi-provider usage recording with unified sink.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.modules.workspace.usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)

# Module-level error dedup cache for MessageRecordingSink exceptions
# Same error type+message within 5 minutes is logged only once
_error_log_cache: dict[str, float] = defaultdict(float)
ERROR_LOG_INTERVAL = 300  # 5 minutes


def _should_log_error(error_key: str) -> bool:
    """Check if error should be logged (dedup within 5 minutes)."""
    now = time.time()
    last_time = _error_log_cache.get(error_key, 0)
    if now - last_time >= ERROR_LOG_INTERVAL:
        _error_log_cache[error_key] = now
        return True
    return False


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
            # Log error with dedup to avoid log storm (same error logged once per 5 min)
            error_key = f"{type(e).__name__}:{str(e)[:100]}"
            if _should_log_error(error_key):
                logger.error(
                    "MessageRecordingSink failed: %s",
                    e,
                    extra={
                        "session_id": evidence.session_id,
                        "tenant_id": evidence.tenant_id,
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
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
                        # Issue #3337: Strip Qwen system-reminder envelopes,
                        # preserving any real user text that follows.
                        from scripts.shared.qwen_context import strip_qwen_system_envelopes

                        user_content = strip_qwen_system_envelopes(user_content)
                        if user_content:
                            # Issue #3336: Provide stable identity for dedup.
                            # Prefer message id from the request; fallback to content hash.
                            import hashlib

                            msg_id = msg.get("id") or msg.get("message_id")
                            if not msg_id:
                                content_hash = hashlib.sha256(
                                    user_content.encode("utf-8")
                                ).hexdigest()[:16]
                                msg_id = f"llm_proxy:{content_hash}"
                            stored = sm.append_transcript_message(
                                session_id=session_id,
                                role="user",
                                content=user_content[:10000],
                                source="llm_proxy",
                                external_message_id=msg_id,
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
    except Exception as e:
        # Log error with dedup (session_id context from caller)
        error_key = f"record_messages:{type(e).__name__}:{str(e)[:100]}"
        if _should_log_error(error_key):
            logger.error(
                "Failed to record messages: %s",
                e,
                extra={
                    "session_id": session_id,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

    return message_delta


class DailyUsageSink:
    """Sink for incrementing daily_usage table.

    Issue #2732: Write usage data to daily_usage for Dashboard "today's usage" display.
    Uses atomic increment semantics to avoid overwriting accumulated data.
    """

    def consume(self, evidence: UsageEvidence) -> bool:
        """Consume usage evidence and increment daily_usage.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful or skipped (not a failure), False if failed.
        """
        # Skip if no session_id (not a failure, just skip)
        if not evidence.session_id:
            logger.debug("Skipping daily_usage record: no session_id")
            return True

        # Skip if no tokens (nothing to record)
        if evidence.input_tokens == 0 and evidence.output_tokens == 0:
            logger.debug("Skipping daily_usage record: zero tokens")
            return True

        try:
            from app.repositories.usage_repo import UsageRepository

            repo = UsageRepository()

            # Calculate cache tokens
            cache_tokens = (evidence.cache_read_tokens or 0) + (evidence.cache_write_tokens or 0)

            # Prepare models list (skip if no model)
            models_used = [evidence.model] if evidence.model else None

            # Call increment_usage with dimensions from evidence
            success = repo.increment_usage(
                tool_name=evidence.tool_name or "qwen-code",
                host_name=evidence.host_name or "localhost",
                tenant_id=evidence.tenant_id,
                tokens_used=evidence.total_session_tokens(),
                input_tokens=evidence.input_tokens,
                output_tokens=evidence.output_tokens,
                cache_tokens=cache_tokens,
                request_count=1,
                models_used=models_used,
            )

            if not success:
                logger.warning(
                    "DailyUsageSink failed for session %s",
                    evidence.session_id[:8],
                )

            return success

        except Exception as e:
            logger.error(
                "DailyUsageSink failed: %s",
                e,
                extra={
                    "session_id": evidence.session_id,
                    "tenant_id": evidence.tenant_id,
                    "tool_name": evidence.tool_name,
                    "host_name": evidence.host_name,
                },
            )
            return False


class DailyMessagesSink:
    """Sink for writing messages to daily_messages table.

    Issue #3027: Write message data to daily_messages for trend analysis.
    Implements dual-write with session_messages for Workspace AI conversations.
    """

    def __init__(
        self,
        request_body: bytes | None = None,
        response_body: bytes | None = None,
        output_tokens: int = 0,
        model: str | None = None,
    ):
        """Initialize daily messages sink.

        Args:
            request_body: Raw request body bytes (for user message extraction).
            response_body: Raw response body bytes (for assistant message extraction).
            output_tokens: Output tokens count.
            model: Model name.
        """
        self.request_body = request_body
        self.response_body = response_body
        self.output_tokens = output_tokens
        self.model = model

    def consume(self, evidence: UsageEvidence) -> bool:
        """Write messages to daily_messages table.

        Args:
            evidence: Usage evidence.

        Returns:
            True if successful or skipped (not a failure), False if failed.
        """
        # Skip if no session_id (not a failure, just skip)
        if not evidence.session_id:
            logger.debug("Skipping daily_messages record: no session_id")
            return True

        # Skip if no response body (no messages to record)
        if not self.response_body:
            logger.debug("Skipping daily_messages record: no response_body")
            return True

        try:
            _write_messages_to_daily_messages(
                evidence=evidence,
                request_body=self.request_body,
                response_body=self.response_body,
                output_tokens=self.output_tokens or evidence.output_tokens,
                model=self.model or evidence.model,
            )
            return True
        except Exception as e:
            # Log error with dedup to avoid log storm
            # Note: If session_messages was written successfully but daily_messages fails,
            # trend analysis data will be missing. This is acceptable as daily_messages
            # is an analytics table and can be backfilled from session_messages.
            error_key = f"DailyMessagesSink:{type(e).__name__}:{str(e)[:100]}"
            if _should_log_error(error_key):
                logger.warning(
                    "DailyMessagesSink failed (trend analysis data may be incomplete): %s",
                    e,
                    extra={
                        "session_id": evidence.session_id,
                        "tenant_id": evidence.tenant_id,
                        "error_type": type(e).__name__,
                        "data_consistency_note": "session_messages may have been written; "
                        "daily_messages missing for this request",
                    },
                    exc_info=True,
                )
            return True  # Non-critical, don't fail the whole pipeline


def _write_messages_to_daily_messages(
    evidence: UsageEvidence,
    request_body: bytes | None,
    response_body: bytes,
    output_tokens: int,
    model: str | None,
) -> None:
    """Write user and assistant messages to daily_messages table.

    Args:
        evidence: Usage evidence with session/user context.
        request_body: Raw request body bytes.
        response_body: Raw response body bytes.
        output_tokens: Output tokens count.
        model: Model name.
    """
    import json
    from datetime import datetime

    from app.repositories.database import get_db_connection, is_postgresql

    # Parse messages from request/response
    messages_to_write = _parse_messages_for_daily_messages(
        request_body=request_body,
        response_body=response_body,
        output_tokens=output_tokens,
        model=model,
    )

    if not messages_to_write:
        return

    # Generate timestamp and date
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    date_str = timestamp[:10]
    timestamp_ms = int(time.time() * 1000)

    # Get user_id from evidence or session
    user_id: int | None = evidence.user_id
    if not user_id or user_id <= 0:
        # Try to get from session
        try:
            from app.modules.workspace.session_manager import get_session_manager

            sm = get_session_manager()
            session = sm.get_session(evidence.session_id)
            if session:
                user_id = getattr(session, "user_id", None)
        except Exception:
            pass

    # Write each message
    for seq, msg_data in enumerate(messages_to_write):
        role = msg_data["role"]
        content = msg_data["content"]

        # Generate message_id: {session_id}-{timestamp_ms}-{sequence}
        message_id = f"{evidence.session_id}-{timestamp_ms}-{seq}"

        # Build full_entry JSON
        full_entry_json = json.dumps(
            {
                "session_id": evidence.session_id,
                "role": role,
                "content": content,
            },
            ensure_ascii=False,
        )

        # Get token values
        msg_input_tokens = msg_data.get("input_tokens", 0)
        msg_output_tokens = msg_data.get("output_tokens", 0)
        tokens_used = msg_input_tokens + msg_output_tokens

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                if is_postgresql():
                    cursor.execute(
                        """INSERT INTO daily_messages
                        (date, tool_name, host_name, message_id, role, content,
                         full_entry, tokens_used, input_tokens, output_tokens,
                         model, timestamp, message_source,
                         conversation_id, agent_session_id, user_id, project_path, tenant_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (date, tool_name, message_id, host_name) DO NOTHING""",
                        (
                            date_str,
                            evidence.tool_name or "qwen-code",
                            evidence.host_name or "localhost",
                            message_id,
                            role,
                            content[:10000],
                            full_entry_json,
                            tokens_used,
                            msg_input_tokens,
                            msg_output_tokens,
                            model,
                            timestamp,
                            "llm_proxy",
                            evidence.session_id,
                            evidence.session_id,
                            user_id,
                            "",
                            evidence.tenant_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """INSERT OR IGNORE INTO daily_messages
                        (date, tool_name, host_name, message_id, role, content,
                         full_entry, tokens_used, input_tokens, output_tokens,
                         model, timestamp, message_source,
                         conversation_id, agent_session_id, user_id, project_path, tenant_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            date_str,
                            evidence.tool_name or "qwen-code",
                            evidence.host_name or "localhost",
                            message_id,
                            role,
                            content[:10000],
                            full_entry_json,
                            tokens_used,
                            msg_input_tokens,
                            msg_output_tokens,
                            model,
                            timestamp,
                            "llm_proxy",
                            evidence.session_id,
                            evidence.session_id,
                            user_id,
                            "",
                            evidence.tenant_id,
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.debug(
                "Failed to write message to daily_messages: %s (session_id=%s, message_id=%s)",
                e,
                evidence.session_id[:8] if evidence.session_id else "unknown",
                message_id,
            )


def _parse_messages_for_daily_messages(
    request_body: bytes | None,
    response_body: bytes,
    output_tokens: int,
    model: str | None,
) -> list[dict]:
    """Parse user and assistant messages from request/response bodies.

    Args:
        request_body: Raw request body bytes.
        response_body: Raw response body bytes.
        output_tokens: Output tokens count.
        model: Model name.

    Returns:
        List of message dicts with role, content, input_tokens, output_tokens.
    """
    import json

    messages = []

    # Parse user message from request body
    if request_body:
        try:
            req_data = json.loads(request_body)
            req_messages = req_data.get("messages", [])
            if isinstance(req_messages, list) and req_messages:
                # Get the last user message
                user_content = None
                for msg in reversed(req_messages):
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
                    # Issue #3337: Strip Qwen system-reminder envelopes,
                    # preserving any real user text that follows.
                    try:
                        from scripts.shared.qwen_context import strip_qwen_system_envelopes

                        user_content = strip_qwen_system_envelopes(user_content)
                    except ImportError:
                        pass  # If qwen_context not available, use original content
                    if user_content:
                        messages.append(
                            {
                                "role": "user",
                                "content": user_content[:10000],
                                "input_tokens": 0,
                                "output_tokens": 0,
                            }
                        )
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
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": content[:10000],
                                    "input_tokens": 0,
                                    "output_tokens": output_tokens,
                                }
                            )
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
                    messages.append(
                        {
                            "role": "assistant",
                            "content": full_content[:10000],
                            "input_tokens": 0,
                            "output_tokens": output_tokens,
                        }
                    )

    return messages


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

    # Issue #2732: Add DailyUsageSink for Dashboard "today's usage"
    sinks.append(DailyUsageSink())

    # Issue #3027: Add DailyMessagesSink for trend analysis
    if response_body:
        sinks.append(
            DailyMessagesSink(
                request_body=request_body,
                response_body=response_body,
                output_tokens=output_tokens,
                model=model,
            )
        )

    return CompositeSink(sinks)
