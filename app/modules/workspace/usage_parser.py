"""
Open ACE - Usage Parser Module

Provider-specific usage parsers with factory pattern.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from .usage_accumulator import UsageAccumulator
from .usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)


class UsageParser(Protocol):
    """Provider-specific usage parser protocol."""

    def parse_regular(self, content: bytes) -> UsageEvidence:
        """Parse regular (non-streaming) response.

        Args:
            content: Response body (JSON bytes).

        Returns:
            UsageEvidence: Parsing result.
        """
        ...

    def parse_stream_chunk(
        self,
        chunk: bytes,
        accumulator: UsageAccumulator
    ) -> None:
        """Parse streaming chunk, accumulate to accumulator.

        Args:
            chunk: Single SSE chunk.
            accumulator: Accumulator instance.
        """
        ...

    def finalize_stream(
        self,
        accumulator: UsageAccumulator
    ) -> UsageEvidence | None:
        """Finalize streaming parsing, return final usage.

        Args:
            accumulator: Accumulator instance.

        Returns:
            UsageEvidence | None: Final usage, None if failed.
        """
        ...

    def extract_request_id(self, headers: dict) -> str | None:
        """Extract request_id from response headers.

        Args:
            headers: HTTP response header dict.

        Returns:
            str | None: request_id, None if not found.
        """
        ...


class UsageParserFactory:
    """Factory for provider-specific usage parsers."""

    @staticmethod
    def detect_protocol(
        content: bytes,
        content_type: str,
        provider: str,
        endpoint: str | None = None
    ) -> str:
        """Detect protocol type.

        Priority:
        1. Content-Type: text/event-stream → streaming protocol
        2. Response body prefix: b"event:" or b"data:" → SSE
        3. JSON parsing success → regular response
        4. Provider + endpoint → protocol inference (fallback)

        Args:
            content: Response content.
            content_type: Content-Type header value.
            provider: Provider identifier.
            endpoint: API endpoint path.

        Returns:
            Protocol type string.
        """
        # Priority 1: Content-Type
        if "text/event-stream" in content_type.lower():
            if provider == "anthropic":
                return "messages_api_stream"
            else:
                return "chat_completions_stream"

        # Priority 2: Response body prefix (SSE detection)
        content_stripped = content.strip()
        if content_stripped.startswith(b"event:") or content_stripped.startswith(b"data:"):
            if provider == "anthropic":
                return "messages_api_stream"
            else:
                return "chat_completions_stream"

        # Priority 3: Try JSON parsing
        try:
            data = json.loads(content)
            # Check for Responses API format
            if "output" in data and "usage" in data:
                return "responses_api"
            # Check for Anthropic Messages API format
            if "content" in data and "usage" in data:
                return "messages_api"
            # Default to Chat Completions
            return "chat_completions"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Priority 4: Provider + endpoint inference
        if provider == "openai":
            if endpoint and "/responses" in endpoint:
                return "responses_api"
            return "chat_completions"
        elif provider == "anthropic":
            return "messages_api"
        else:
            return "chat_completions"

    @staticmethod
    def get_parser(protocol_type: str, provider: str) -> UsageParser:
        """Get appropriate parser for protocol type.

        Args:
            protocol_type: Protocol type string.
            provider: Provider identifier.

        Returns:
            UsageParser instance.
        """
        if protocol_type in ("messages_api", "messages_api_stream"):
            return AnthropicMessagesParser()
        elif protocol_type == "responses_api":
            return OpenAIResponsesParser()
        else:
            # Default to OpenAI Chat Completions (including gateway converted)
            return OpenAIChatParser()


class OpenAIChatParser:
    """Parser for OpenAI Chat Completions API."""

    def parse_regular(self, content: bytes) -> UsageEvidence:
        """Parse regular Chat Completions response."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return UsageEvidence.create_empty(
                provider="openai",
                parse_status="malformed",
                parse_warnings=[f"JSON parse error: {e}"],
            )

        return self._parse_usage_from_json(data, "openai", "chat_completions")

    def parse_stream_chunk(
        self,
        chunk: bytes,
        accumulator: UsageAccumulator
    ) -> None:
        """Parse SSE chunk for Chat Completions streaming.

        OpenAI Chat Completions streaming usage appears in a chunk like:
        data: {"usage": {"prompt_tokens": N, "completion_tokens": M}}
        """
        try:
            chunk_str = chunk.strip().decode('utf-8')
        except UnicodeDecodeError:
            return

        if not chunk_str:
            return

        # Handle SSE format
        for line in chunk_str.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue

                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Check for usage field
                if "usage" in data:
                    evidence = self._parse_usage_from_json(data, "openai", "chat_completions")
                    accumulator.add_chunk(evidence)

    def finalize_stream(
        self,
        accumulator: UsageAccumulator
    ) -> UsageEvidence | None:
        """Finalize streaming, return final usage."""
        return accumulator.finalize()

    def extract_request_id(self, headers: dict) -> str | None:
        """Extract request_id from headers.

        OpenAI uses: x-request-id
        """
        # Try standard header
        request_id = headers.get("x-request-id")
        if request_id:
            return request_id

        # Try lowercase variant
        for key, value in headers.items():
            if key.lower() == "x-request-id":
                return value

        return None

    def _parse_usage_from_json(
        self,
        data: dict[str, Any],
        provider: str,
        protocol_type: str
    ) -> UsageEvidence:
        """Parse usage from JSON data."""
        usage = data.get("usage")

        if not usage or not isinstance(usage, dict):
            return UsageEvidence.create_empty(
                provider=provider,
                parse_status="missing",
                parse_warnings=["No usage field in response"],
            )

        # Extract tokens
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Try to get request_id from response body
        request_id = data.get("id")

        # Extract model
        model = data.get("model")

        # Extract cache tokens (OpenAI Chat Completions format)
        cache_read = 0
        prompt_tokens_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_tokens_details, dict):
            cache_read = prompt_tokens_details.get("cached_tokens", 0)

        # Also check for Anthropic-style cache fields (gateway converted)
        if cache_read == 0:
            cache_read = usage.get("cache_read_input_tokens", 0)

        cache_write = usage.get("cache_creation_input_tokens", 0)

        # Create evidence
        evidence = UsageEvidence(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            provider=provider,
            model=model,
            is_final=True,
            has_cache_data=(cache_read > 0 or cache_write > 0),
            protocol_type=protocol_type,
            parse_status="success",
            request_id=request_id,
        )

        # Validate and clamp
        warnings = evidence.clamp_tokens()
        if warnings:
            evidence.parse_warnings.extend(warnings)
            logger.warning(
                "Usage validation warnings",
                extra={"warnings": warnings, "evidence": evidence.to_dict()}
            )

        return evidence


class AnthropicMessagesParser:
    """Parser for Anthropic Messages API."""

    def parse_regular(self, content: bytes) -> UsageEvidence:
        """Parse regular Messages API response."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return UsageEvidence.create_empty(
                provider="anthropic",
                parse_status="malformed",
                parse_warnings=[f"JSON parse error: {e}"],
            )

        return self._parse_usage_from_json(data)

    def parse_stream_chunk(
        self,
        chunk: bytes,
        accumulator: UsageAccumulator
    ) -> None:
        """Parse SSE chunk for Anthropic Messages streaming.

        Anthropic streaming events:
        - message_start: Contains initial usage (input_tokens, cache tokens)
        - message_delta: Contains output_tokens increment
        - message_stop: End marker
        """
        try:
            chunk_str = chunk.strip().decode('utf-8')
        except UnicodeDecodeError:
            return

        if not chunk_str:
            return

        # Handle Anthropic SSE format
        event_type = None
        event_data = None

        for line in chunk_str.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                event_data = line[5:].strip()

        if not event_data:
            return

        try:
            data = json.loads(event_data)
        except json.JSONDecodeError:
            return

        # Handle different event types
        if event_type == "message_start":
            message = data.get("message", {})
            usage = message.get("usage", {})

            if usage:
                evidence = UsageEvidence(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=0,  # Will be accumulated from message_delta
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    provider="anthropic",
                    model=message.get("model"),
                    is_final=False,  # Not final until all deltas accumulated
                    has_cache_data=(
                        usage.get("cache_read_input_tokens", 0) > 0 or
                        usage.get("cache_creation_input_tokens", 0) > 0
                    ),
                    protocol_type="messages_api",
                    parse_status="success",
                    request_id=message.get("id"),
                )
                warnings = evidence.clamp_tokens()
                if warnings:
                    evidence.parse_warnings.extend(warnings)
                accumulator.add_chunk(evidence)

        elif event_type == "message_delta":
            usage = data.get("usage", {})

            if usage:
                output_tokens = usage.get("output_tokens", 0)
                evidence = UsageEvidence(
                    input_tokens=0,  # Already captured in message_start
                    output_tokens=output_tokens,  # Increment
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    provider="anthropic",
                    is_final=False,
                    protocol_type="messages_api",
                    parse_status="success",
                )
                accumulator.add_chunk(evidence)

    def finalize_stream(
        self,
        accumulator: UsageAccumulator
    ) -> UsageEvidence | None:
        """Finalize streaming, return final usage."""
        return accumulator.finalize()

    def extract_request_id(self, headers: dict) -> str | None:
        """Extract request_id from headers.

        Anthropic uses: request-id (not x-request-id)
        """
        # Try Anthropic header
        request_id = headers.get("request-id")
        if request_id:
            return request_id

        # Try lowercase variant
        for key, value in headers.items():
            if key.lower() == "request-id":
                return value

        # Fallback to x-request-id (some gateways)
        for key, value in headers.items():
            if key.lower() == "x-request-id":
                return value

        return None

    def _parse_usage_from_json(self, data: dict[str, Any]) -> UsageEvidence:
        """Parse usage from JSON data."""
        usage = data.get("usage")

        if not usage or not isinstance(usage, dict):
            return UsageEvidence.create_empty(
                provider="anthropic",
                parse_status="missing",
                parse_warnings=["No usage field in response"],
            )

        # Extract tokens
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)

        # Extract model and request_id
        model = data.get("model")
        request_id = data.get("id")

        # Create evidence
        evidence = UsageEvidence(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            provider="anthropic",
            model=model,
            is_final=True,
            has_cache_data=(cache_read > 0 or cache_write > 0),
            protocol_type="messages_api",
            parse_status="success",
            request_id=request_id,
        )

        # Validate and clamp
        warnings = evidence.clamp_tokens()
        if warnings:
            evidence.parse_warnings.extend(warnings)
            logger.warning(
                "Usage validation warnings",
                extra={"warnings": warnings, "evidence": evidence.to_dict()}
            )

        return evidence


class OpenAIResponsesParser:
    """Parser for OpenAI Responses API."""

    def parse_regular(self, content: bytes) -> UsageEvidence:
        """Parse Responses API response."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return UsageEvidence.create_empty(
                provider="openai",
                parse_status="malformed",
                parse_warnings=[f"JSON parse error: {e}"],
            )

        usage = data.get("usage")

        if not usage or not isinstance(usage, dict):
            return UsageEvidence.create_empty(
                provider="openai",
                parse_status="missing",
                parse_warnings=["No usage field in response"],
            )

        # Responses API uses input_tokens/output_tokens (like Anthropic)
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        # Extract model and request_id
        model = data.get("model")
        request_id = data.get("id")

        # Create evidence (no cache tokens in Responses API)
        evidence = UsageEvidence(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="openai",
            model=model,
            is_final=True,
            has_cache_data=False,
            protocol_type="responses_api",
            parse_status="success",
            request_id=request_id,
        )

        # Validate and clamp
        warnings = evidence.clamp_tokens()
        if warnings:
            evidence.parse_warnings.extend(warnings)
            logger.warning(
                "Usage validation warnings",
                extra={"warnings": warnings, "evidence": evidence.to_dict()}
            )

        return evidence

    def parse_stream_chunk(
        self,
        chunk: bytes,
        accumulator: UsageAccumulator
    ) -> None:
        """Parse Responses API streaming chunk.

        Responses API streaming is complex and may have different formats.
        For now, treat as regular SSE with usage in final chunk.
        """
        chunk_str = chunk.strip()
        if not chunk_str:
            return

        for line in chunk_str.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue

                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Check for usage in response.completed event
                if data.get("type") == "response.completed":
                    response = data.get("response", {})
                    usage = response.get("usage")
                    if usage:
                        evidence = UsageEvidence(
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            provider="openai",
                            model=response.get("model"),
                            is_final=True,
                            has_cache_data=False,
                            protocol_type="responses_api",
                            parse_status="success",
                            request_id=response.get("id"),
                        )
                        warnings = evidence.clamp_tokens()
                        if warnings:
                            evidence.parse_warnings.extend(warnings)
                        accumulator.add_chunk(evidence)

    def finalize_stream(
        self,
        accumulator: UsageAccumulator
    ) -> UsageEvidence | None:
        """Finalize streaming, return final usage."""
        return accumulator.finalize()

    def extract_request_id(self, headers: dict) -> str | None:
        """Extract request_id from headers."""
        # Same as OpenAI Chat
        request_id = headers.get("x-request-id")
        if request_id:
            return request_id

        for key, value in headers.items():
            if key.lower() == "x-request-id":
                return value

        return None