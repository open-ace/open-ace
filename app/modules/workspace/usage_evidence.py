"""
Open ACE - Usage Evidence Module

Provider-neutral usage parsing result with full context and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# Token limits to prevent database overflow
TOKEN_FIELD_MAX = 1_000_000_000  # 1 billion
TOKEN_TOTAL_MAX = 2_000_000_000  # 2 billion


@dataclass
class UsageEvidence:
    """Provider-neutral usage parsing result with full context and diagnostics.

    This dataclass represents the parsed token usage from various LLM providers,
    normalizing different provider-specific formats into a unified structure.

    Attributes:
        input_tokens: Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.
        cache_read_tokens: Number of tokens read from cache.
        cache_write_tokens: Number of tokens written to cache.
        provider: Provider identifier (e.g., "openai", "anthropic", "google", "unknown").
        model: Model identifier (e.g., "gpt-4", "claude-3-opus").
        is_final: Whether this is the final usage value (not an intermediate chunk).
        is_indeterminate: Whether the final value could not be determined.
        has_cache_data: Whether the provider provided cache token information.
        protocol_type: Protocol type (e.g., "chat_completions", "responses_api", "messages_api").
        parse_status: Parse status ("success", "partial", "failed", "missing").
        parse_warnings: List of warning messages from parsing.
        request_id: Request identifier from provider response headers.
        session_id: Session identifier for correlation.
    """

    # Core token counts (provider-provided actual values)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # Metadata
    provider: str = ""  # "openai" | "anthropic" | "google" | "unknown"
    model: str | None = None

    # Parsing status
    is_final: bool = True  # Whether this is the final value (non-streaming or last chunk)
    is_indeterminate: bool = False  # Whether the final value could not be determined
    has_cache_data: bool = False  # Whether the provider provided cache token info

    # Original protocol information
    protocol_type: str = ""  # "chat_completions" | "responses_api" | "messages_api"
    parse_status: str = "success"  # "success" | "partial" | "failed" | "missing"
    parse_warnings: list[str] = field(default_factory=list)

    # Correlation info (for auditing and compensation)
    request_id: str | None = None
    session_id: str | None = None

    @property
    def total_tokens(self) -> int:
        """Total token count: input + output + cache.

        Note: This definition applies to quota deduction and session accumulation.
        ROI cost calculations may use different weight coefficients.
        """
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def billable_tokens(self) -> int:
        """Billable token count (excluding cache)."""
        return self.input_tokens + self.output_tokens

    def validate(self) -> list[str]:
        """Validate usage data validity, returning list of warnings.

        Checks for:
        - Negative token values
        - Token values exceeding limits
        - Total exceeding limit

        Returns:
            List of warning messages (empty if valid).
        """
        warnings = []

        # Check negative values
        if self.input_tokens < 0:
            warnings.append(f"input_tokens cannot be negative: {self.input_tokens}")
        if self.output_tokens < 0:
            warnings.append(f"output_tokens cannot be negative: {self.output_tokens}")
        if self.cache_read_tokens < 0:
            warnings.append(f"cache_read_tokens cannot be negative: {self.cache_read_tokens}")
        if self.cache_write_tokens < 0:
            warnings.append(f"cache_write_tokens cannot be negative: {self.cache_write_tokens}")

        # Check field limits
        if self.input_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"input_tokens exceeds limit: {self.input_tokens} > {TOKEN_FIELD_MAX}")
        if self.output_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"output_tokens exceeds limit: {self.output_tokens} > {TOKEN_FIELD_MAX}")
        if self.cache_read_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"cache_read_tokens exceeds limit: {self.cache_read_tokens} > {TOKEN_FIELD_MAX}")
        if self.cache_write_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"cache_write_tokens exceeds limit: {self.cache_write_tokens} > {TOKEN_FIELD_MAX}")

        # Check total limit
        if self.total_tokens > TOKEN_TOTAL_MAX:
            warnings.append(f"total_tokens exceeds limit: {self.total_tokens} > {TOKEN_TOTAL_MAX}")

        return warnings

    def clamp_tokens(self) -> list[str]:
        """Clamp token values to valid range (0 to limit).

        Modifies the instance in-place.

        Returns:
            List of warning messages for clamped values.
        """
        warnings = []

        # Clamp negative values to 0
        if self.input_tokens < 0:
            warnings.append(f"Clamped negative input_tokens: {self.input_tokens} -> 0")
            self.input_tokens = 0
        if self.output_tokens < 0:
            warnings.append(f"Clamped negative output_tokens: {self.output_tokens} -> 0")
            self.output_tokens = 0
        if self.cache_read_tokens < 0:
            warnings.append(f"Clamped negative cache_read_tokens: {self.cache_read_tokens} -> 0")
            self.cache_read_tokens = 0
        if self.cache_write_tokens < 0:
            warnings.append(f"Clamped negative cache_write_tokens: {self.cache_write_tokens} -> 0")
            self.cache_write_tokens = 0

        # Clamp to field limit
        if self.input_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"Clamped input_tokens: {self.input_tokens} -> {TOKEN_FIELD_MAX}")
            self.input_tokens = TOKEN_FIELD_MAX
        if self.output_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"Clamped output_tokens: {self.output_tokens} -> {TOKEN_FIELD_MAX}")
            self.output_tokens = TOKEN_FIELD_MAX
        if self.cache_read_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"Clamped cache_read_tokens: {self.cache_read_tokens} -> {TOKEN_FIELD_MAX}")
            self.cache_read_tokens = TOKEN_FIELD_MAX
        if self.cache_write_tokens > TOKEN_FIELD_MAX:
            warnings.append(f"Clamped cache_write_tokens: {self.cache_write_tokens} -> {TOKEN_FIELD_MAX}")
            self.cache_write_tokens = TOKEN_FIELD_MAX

        return warnings

    @classmethod
    def create_empty(
        cls,
        provider: str,
        parse_status: str,
        parse_warnings: list[str],
        session_id: str | None = None,
        request_id: str | None = None,
        **metadata
    ) -> "UsageEvidence":
        """Create empty usage for exception cases (not forging 0 values).

        Use this when usage data is missing or malformed. The resulting object
        has is_indeterminate=True and contains no token counts.

        Args:
            provider: Provider identifier.
            parse_status: Parse status ("missing", "malformed", "failed").
            parse_warnings: List of warning messages.
            session_id: Optional session identifier.
            request_id: Optional request identifier.
            **metadata: Additional metadata (e.g., protocol_type, model).

        Returns:
            UsageEvidence with is_indeterminate=True.
        """
        return cls(
            provider=provider,
            parse_status=parse_status,
            parse_warnings=parse_warnings,
            is_final=True,
            is_indeterminate=True,
            session_id=session_id,
            request_id=request_id,
            **metadata
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for logging and serialization."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "provider": self.provider,
            "model": self.model,
            "is_final": self.is_final,
            "is_indeterminate": self.is_indeterminate,
            "has_cache_data": self.has_cache_data,
            "protocol_type": self.protocol_type,
            "parse_status": self.parse_status,
            "parse_warnings": self.parse_warnings,
            "request_id": self.request_id,
            "session_id": self.session_id,
        }