"""
Open ACE - Usage Evidence Module

Provider-neutral usage evidence data structure for LLM usage tracking.
Issue #2184: Multi-provider usage recording with cache token support.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UsageEvidence:
    """Provider-neutral usage evidence for LLM API calls.

    Captures token usage from various LLM providers (OpenAI, Anthropic, Gateway)
    in a unified format, supporting cache tokens and providing different
    calculation methods for quota, session, and cost reporting.
    """

    # ── Token counts ─────────────────────────────────────────────────────
    input_tokens: int = 0
    output_tokens: int = 0

    # ── Cache tokens (optional, provider-dependent) ──────────────────────
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None  # ZCode-specific

    # ── Metadata ────────────────────────────────────────────────────────
    provider: str = ""  # "openai" | "anthropic" | "gateway"
    model: str | None = None
    protocol: str = (
        ""  # "openai_chat" | "openai_responses" | "anthropic_messages" | "gateway_openai"
    )
    api_version: str | None = None  # Anthropic API version

    # ── State flags ──────────────────────────────────────────────────────
    is_final: bool = True  # True=final value, False=partial/incremental
    is_indeterminate: bool = False  # True=protocol unknown/cannot determine final
    is_merged: bool = False  # True=merged from multiple events

    # ── Raw data for audit ───────────────────────────────────────────────
    raw_usage: dict[str, Any] = field(default_factory=dict)
    parse_status: str = "success"  # "success" | "partial" | "malformed" | "unsupported"
    parse_diagnostics: dict[str, Any] | None = None

    # ── Request correlation ───────────────────────────────────────────────
    request_id: str | None = None
    session_id: str = ""
    user_id: int = 0
    tenant_id: int = 0

    def effective_quota_tokens(self) -> int:
        """Quota accounting: don't double-charge cache reads.

        Returns:
            Token count for quota deduction.
        """
        cache_read = self.cache_read_tokens or 0
        return self.input_tokens + self.output_tokens - cache_read

    def effective_cost_tokens(self) -> int:
        """Cost report: include cache write cost.

        Returns:
            Token count for cost calculation.
        """
        cache_write = self.cache_write_tokens or 0
        return self.input_tokens + self.output_tokens + cache_write

    def total_session_tokens(self) -> int:
        """Session statistics: record all tokens.

        Returns:
            Total token count for session tracking.
        """
        return self.input_tokens + self.output_tokens

    def merge_with(self, other: UsageEvidence) -> UsageEvidence:
        """Merge another UsageEvidence into this one.

        Used for combining multiple usage events from the same request,
        especially for Anthropic SSE where usage arrives in multiple events.

        Args:
            other: Another UsageEvidence to merge.

        Returns:
            New merged UsageEvidence.
        """
        # Determine final token counts
        # input_tokens: typically from message_start, don't accumulate
        # output_tokens: accumulate from message_delta events
        merged_input = other.input_tokens if other.input_tokens > 0 else self.input_tokens
        merged_output = self.output_tokens + other.output_tokens

        # Accumulate cache tokens
        merged_cache_read_int = (self.cache_read_tokens or 0) + (other.cache_read_tokens or 0)
        merged_cache_read: int | None = merged_cache_read_int if merged_cache_read_int > 0 else None

        merged_cache_write_int = (self.cache_write_tokens or 0) + (other.cache_write_tokens or 0)
        merged_cache_write: int | None = (
            merged_cache_write_int if merged_cache_write_int > 0 else None
        )

        merged_reasoning_int = (self.reasoning_tokens or 0) + (other.reasoning_tokens or 0)
        merged_reasoning: int | None = merged_reasoning_int if merged_reasoning_int > 0 else None

        # Merge raw_usage
        merged_raw = dict(self.raw_usage)
        merged_raw.update(other.raw_usage)

        return UsageEvidence(
            input_tokens=merged_input,
            output_tokens=merged_output,
            cache_read_tokens=merged_cache_read,
            cache_write_tokens=merged_cache_write,
            reasoning_tokens=merged_reasoning,
            provider=other.provider or self.provider,
            model=other.model or self.model,
            protocol=other.protocol or self.protocol,
            api_version=other.api_version or self.api_version,
            is_final=other.is_final,  # Take the final status from the latest event
            is_indeterminate=self.is_indeterminate or other.is_indeterminate,
            is_merged=True,
            raw_usage=merged_raw,
            parse_status=(
                "success"
                if self.parse_status == "success" and other.parse_status == "success"
                else "partial"
            ),
            parse_diagnostics=self.parse_diagnostics or other.parse_diagnostics,
            request_id=other.request_id or self.request_id,
            session_id=other.session_id or self.session_id,
            user_id=other.user_id or self.user_id,
            tenant_id=other.tenant_id or self.tenant_id,
        )

    @classmethod
    def from_openai_chat_response(
        cls,
        response_data: dict[str, Any],
        *,
        provider: str = "openai",
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ) -> UsageEvidence:
        """Create UsageEvidence from OpenAI Chat Completions response.

        Args:
            response_data: Parsed JSON response from OpenAI Chat Completions API.
            provider: Provider identifier.
            model: Model name (from request if not in response).
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier for deduplication.

        Returns:
            UsageEvidence instance.
        """
        usage = response_data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # OpenAI Chat Completions doesn't provide cache tokens
        parse_status = "success" if input_tokens > 0 or output_tokens > 0 else "partial"

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=provider,
            model=model or response_data.get("model"),
            protocol="openai_chat",
            is_final=True,
            raw_usage=usage,
            parse_status=parse_status,
            request_id=request_id or response_data.get("id"),
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @classmethod
    def from_openai_responses_api(
        cls,
        response_data: dict[str, Any],
        *,
        provider: str = "openai",
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ) -> UsageEvidence:
        """Create UsageEvidence from OpenAI Responses API response.

        Args:
            response_data: Parsed JSON response from OpenAI Responses API.
            provider: Provider identifier.
            model: Model name.
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier for deduplication.

        Returns:
            UsageEvidence instance.
        """
        # Responses API wraps usage in response object
        response = response_data.get("response", response_data)
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        parse_status = "success" if input_tokens > 0 or output_tokens > 0 else "partial"

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=provider,
            model=model or response.get("model"),
            protocol="openai_responses",
            is_final=True,
            raw_usage=usage,
            parse_status=parse_status,
            request_id=request_id or response.get("id"),
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @classmethod
    def from_anthropic_response(
        cls,
        response_data: dict[str, Any],
        *,
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
        api_version: str | None = None,
    ) -> UsageEvidence:
        """Create UsageEvidence from Anthropic Messages API response.

        Args:
            response_data: Parsed JSON response from Anthropic Messages API.
            model: Model name.
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier for deduplication.
            api_version: Anthropic API version.

        Returns:
            UsageEvidence instance.
        """
        usage = response_data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens")
        cache_write_tokens = usage.get("cache_creation_input_tokens")

        parse_status = "success" if input_tokens > 0 or output_tokens > 0 else "partial"

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider="anthropic",
            model=model or response_data.get("model"),
            protocol="anthropic_messages",
            api_version=api_version,
            is_final=True,
            raw_usage=usage,
            parse_status=parse_status,
            request_id=request_id or response_data.get("id"),
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @classmethod
    def from_gateway_response(
        cls,
        response_data: dict[str, Any],
        *,
        provider: str,
        model: str | None = None,
        session_id: str = "",
        user_id: int = 0,
        tenant_id: int = 0,
        request_id: str | None = None,
    ) -> UsageEvidence:
        """Create UsageEvidence from Gateway-converted OpenAI-compatible response.

        Args:
            response_data: Parsed JSON response from gateway.
            provider: Original provider (from token payload).
            model: Model name.
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier for deduplication.

        Returns:
            UsageEvidence instance.
        """
        # Gateway returns OpenAI-compatible format
        usage = response_data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        parse_status = "success" if input_tokens > 0 or output_tokens > 0 else "partial"

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=provider,
            model=model or response_data.get("model"),
            protocol="gateway_openai",
            is_final=True,
            raw_usage=usage,
            parse_status=parse_status,
            request_id=request_id or response_data.get("id"),
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    @classmethod
    def create_empty(
        cls,
        *,
        provider: str,
        protocol: str,
        session_id: str,
        user_id: int,
        tenant_id: int,
        request_id: str | None = None,
        parse_status: str = "malformed",
        parse_diagnostics: dict[str, Any] | None = None,
    ) -> UsageEvidence:
        """Create an empty/malformed UsageEvidence for diagnostic purposes.

        Used when usage data is missing or malformed, to preserve
        request correlation for audit and compensation.

        Args:
            provider: Provider identifier.
            protocol: Protocol identifier.
            session_id: Session identifier.
            user_id: User identifier.
            tenant_id: Tenant identifier.
            request_id: Request identifier.
            parse_status: Parse status ("malformed", "unsupported", "partial").
            parse_diagnostics: Diagnostic information.

        Returns:
            UsageEvidence instance with zero tokens and diagnostic info.
        """
        return cls(
            input_tokens=0,
            output_tokens=0,
            provider=provider,
            protocol=protocol,
            is_final=False,
            is_indeterminate=True,
            raw_usage={},
            parse_status=parse_status,
            parse_diagnostics=parse_diagnostics,
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation.
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "provider": self.provider,
            "model": self.model,
            "protocol": self.protocol,
            "api_version": self.api_version,
            "is_final": self.is_final,
            "is_indeterminate": self.is_indeterminate,
            "is_merged": self.is_merged,
            "parse_status": self.parse_status,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "effective_quota_tokens": self.effective_quota_tokens(),
            "effective_cost_tokens": self.effective_cost_tokens(),
            "total_session_tokens": self.total_session_tokens(),
        }
