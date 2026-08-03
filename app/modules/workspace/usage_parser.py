"""
Open ACE - Usage Parser Module

Provider-neutral usage parser for LLM API responses.
Issue #2184: Multi-provider usage recording with cache token support.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.modules.workspace.usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)


class UsageParser(ABC):
    """Abstract base class for usage parsers."""

    @abstractmethod
    def parse(self, data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from response data.

        Args:
            data: Parsed response data.

        Returns:
            UsageEvidence if usage found, None otherwise.
        """
        ...

    @abstractmethod
    def parse_sse_chunk(self, chunk_data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from SSE chunk data.

        Args:
            chunk_data: Parsed SSE chunk data.

        Returns:
            UsageEvidence if usage found in chunk, None otherwise.
        """
        ...


class OpenAIChatParser(UsageParser):
    """Parser for OpenAI Chat Completions API responses."""

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.request_id = request_id

    def parse(self, data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from Chat Completions response."""
        if "usage" not in data:
            return None

        return UsageEvidence.from_openai_chat_response(
            data,
            provider=self.provider,
            model=self.model,
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            request_id=self.request_id,
        )

    def parse_sse_chunk(self, chunk_data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from SSE chunk.

        OpenAI Chat SSE sends usage in a final chunk when stream_options.include_usage=true.
        """
        if "usage" not in chunk_data:
            return None

        return UsageEvidence.from_openai_chat_response(
            chunk_data,
            provider=self.provider,
            model=self.model or chunk_data.get("model"),
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            request_id=self.request_id,
        )


class OpenAIResponsesParser(UsageParser):
    """Parser for OpenAI Responses API responses."""

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.request_id = request_id

    def parse(self, data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from Responses API response."""
        # Check for response.completed event structure
        if data.get("type") == "response.completed":
            return UsageEvidence.from_openai_responses_api(
                data,
                provider=self.provider,
                model=self.model,
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                request_id=self.request_id,
            )

        # Or direct response object
        if "usage" in data or "response" in data:
            return UsageEvidence.from_openai_responses_api(
                data,
                provider=self.provider,
                model=self.model,
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                request_id=self.request_id,
            )

        return None

    def parse_sse_chunk(self, chunk_data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from SSE event.

        Responses API SSE sends usage in response.completed event.
        """
        # Check for response.completed event
        if chunk_data.get("type") == "response.completed":
            return self.parse(chunk_data)

        return None


class AnthropicMessagesParser(UsageParser):
    """Parser for Anthropic Messages API responses.

    Supports SSE state machine for message_start → message_delta → message_stop sequence.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
        api_version: str | None = None,
    ):
        self.model = model
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.api_version = api_version

    def parse(self, data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from Messages API response."""
        # For non-streaming response
        if "usage" in data:
            return UsageEvidence.from_anthropic_response(
                data,
                model=self.model,
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                request_id=self.request_id,
                api_version=self.api_version,
            )

        return None

    def parse_sse_chunk(self, chunk_data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from SSE event.

        Anthropic SSE events:
        - message_start: contains initial usage (input_tokens, cache tokens)
        - message_delta: contains incremental usage (output_tokens, additional cache)
        - message_stop: final event, marks is_final=True

        Returns UsageEvidence with is_final=False for message_start and message_delta,
        is_final=True for message_stop.
        """
        event_type = chunk_data.get("type", "")

        if event_type == "message_start":
            message = chunk_data.get("message", {})
            usage = message.get("usage", {})
            if not usage:
                return None

            return UsageEvidence(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_write_tokens=usage.get("cache_creation_input_tokens"),
                provider="anthropic",
                model=self.model or message.get("model"),
                protocol="anthropic_messages",
                api_version=self.api_version,
                is_final=False,  # Not final, more events coming
                raw_usage=usage,
                parse_status="partial",
                request_id=self.request_id or message.get("id"),
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
            )

        if event_type == "message_delta":
            delta = chunk_data.get("delta", {})
            usage = chunk_data.get("usage", {})
            if not usage:
                return None

            return UsageEvidence(
                input_tokens=0,  # input_tokens only in message_start
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_write_tokens=usage.get("cache_write_input_tokens"),
                provider="anthropic",
                model=self.model,
                protocol="anthropic_messages",
                api_version=self.api_version,
                is_final=False,  # Not final until message_stop
                raw_usage=usage,
                parse_status="partial",
                request_id=self.request_id,
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
            )

        if event_type == "message_stop":
            # message_stop signals final usage - but no usage data in this event
            # Return a marker evidence to signal finalization
            return UsageEvidence(
                input_tokens=0,
                output_tokens=0,
                provider="anthropic",
                model=self.model,
                protocol="anthropic_messages",
                api_version=self.api_version,
                is_final=True,  # Signal finalization
                raw_usage={},
                parse_status="success",
                request_id=self.request_id,
                session_id=self.session_id,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
            )

        return None


class GatewayParser(UsageParser):
    """Parser for Gateway-converted OpenAI-compatible responses."""

    def __init__(
        self,
        *,
        provider: str,
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.request_id = request_id

    def parse(self, data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from Gateway response."""
        if "usage" not in data:
            return None

        return UsageEvidence.from_gateway_response(
            data,
            provider=self.provider,
            model=self.model,
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            request_id=self.request_id,
        )

    def parse_sse_chunk(self, chunk_data: dict[str, Any]) -> UsageEvidence | None:
        """Parse usage from SSE chunk (OpenAI-compatible format)."""
        if "usage" not in chunk_data:
            return None

        return UsageEvidence.from_gateway_response(
            chunk_data,
            provider=self.provider,
            model=self.model or chunk_data.get("model"),
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            request_id=self.request_id,
        )


class UsageParserFactory:
    """Factory for creating appropriate usage parsers."""

    @staticmethod
    def determine_protocol(
        request_path: str,
        provider: str,
        content_type: str,
        response_body: dict[str, Any] | None = None,
    ) -> str:
        """Determine protocol type from request context.

        Priority:
        1. Request path matching
        2. Provider + Content-Type combination
        3. Response body field inference (fallback)
        4. Default

        Args:
            request_path: Request path (e.g., "/v1/chat/completions").
            provider: Provider identifier.
            content_type: Response content type.
            response_body: Parsed response body (for fallback inference).

        Returns:
            Protocol identifier string.
        """
        # 1. Request path matching
        if request_path:
            path_lower = request_path.lower()
            if "/responses" in path_lower:
                return "openai_responses"
            if "/chat/completions" in path_lower:
                return "openai_chat"
            if "/messages" in path_lower and provider == "anthropic":
                return "anthropic_messages"

        # 2. Provider + Content-Type combination
        if provider == "anthropic":
            return "anthropic_messages"

        # 3. Response body field inference (fallback)
        if response_body:
            if response_body.get("type") == "message" or "anthropic-version" in response_body:
                return "anthropic_messages"
            if "response" in response_body and "choices" not in response_body:
                return "openai_responses"
            if "choices" in response_body:
                return "openai_chat"

        # 4. Default
        return "openai_chat"

    @staticmethod
    def create_parser(
        protocol: str,
        *,
        provider: str,
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
        api_version: str | None = None,
    ) -> UsageParser:
        """Create appropriate parser for protocol.

        Args:
            protocol: Protocol identifier.
            provider: Provider identifier.
            model: Model name.
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier.
            api_version: API version (for Anthropic).

        Returns:
            Appropriate UsageParser instance.

        Raises:
            ValueError: If protocol is unknown.
        """
        if protocol == "openai_chat":
            return OpenAIChatParser(
                provider=provider,
                model=model,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                request_id=request_id,
            )

        if protocol == "openai_responses":
            return OpenAIResponsesParser(
                provider=provider,
                model=model,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                request_id=request_id,
            )

        if protocol == "anthropic_messages":
            return AnthropicMessagesParser(
                model=model,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                request_id=request_id,
                api_version=api_version,
            )

        if protocol == "gateway_openai":
            return GatewayParser(
                provider=provider,
                model=model,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                request_id=request_id,
            )

        raise ValueError(f"Unknown protocol: {protocol}")

    @staticmethod
    def try_parse(
        data: dict[str, Any],
        parser: UsageParser,
        is_sse: bool = False,
    ) -> UsageEvidence | None:
        """Try to parse usage from data using parser.

        Args:
            data: Parsed data.
            parser: Parser instance.
            is_sse: Whether this is SSE data.

        Returns:
            UsageEvidence if found, None otherwise.
        """
        try:
            if is_sse:
                return parser.parse_sse_chunk(data)
            return parser.parse(data)
        except Exception as e:
            logger.debug(f"Failed to parse usage: {e}")
            return None


def parse_sse_line(line: bytes) -> dict[str, Any] | None:
    """Parse a single SSE line into a dictionary.

    Handles both "data: {...}" and "event: ..." formats.

    Args:
        line: Raw SSE line.

    Returns:
        Parsed dictionary or None if not a data line.
    """
    line = line.strip()
    if not line:
        return None

    # Handle event type line
    if line.startswith(b"event:"):
        return {"_event_type": line[6:].strip().decode("utf-8", errors="replace")}

    # Handle data line
    if not line.startswith(b"data:"):
        return None

    payload = line[5:].strip()
    if payload == b"[DONE]":
        return {"_done": True}

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def parse_anthropic_sse_event(line: bytes) -> dict[str, Any] | None:
    """Parse Anthropic SSE event format.

    Anthropic uses:
        event: message_start
        data: {"type": "message_start", ...}

    Args:
        line: Raw SSE line.

    Returns:
        Parsed event data or None.
    """
    line = line.strip()
    if not line:
        return None

    # For Anthropic, we care about data lines
    if not line.startswith(b"data:"):
        return None

    payload = line[5:].strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None