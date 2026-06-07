"""
Shared stream-json usage extraction for CLI tool output.

Each CLI tool (Claude Code, Qwen Code, etc.) emits a different JSON format
for token usage in its ``type: "result"`` message.  This module centralises
the extraction logic so that both the remote-agent executor and the
autonomous agent_runner can reuse it.
"""

from __future__ import annotations

from typing import Any


def extract_claude_stream_usage(parsed: dict[str, Any]) -> dict[str, int] | None:
    """Extract token usage from a Claude Code stream-json result message.

    Claude Code ``--print --output-format stream-json --verbose`` emits:

    .. code-block:: json

        {"type": "result", "usage": {"input_tokens": N, "output_tokens": M}}

    Older builds may nest under ``data.usage`` or ``data.message.usage``.
    """
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        data = parsed.get("data", {})
        usage = data.get("usage") or (data.get("message", {}) or {}).get("usage")
    if isinstance(usage, dict):
        return {
            "input": usage.get("input_tokens", usage.get("input", 0)),
            "output": usage.get("output_tokens", usage.get("output", 0)),
        }
    return None


def extract_qwen_stream_usage(parsed: dict[str, Any]) -> dict[str, int] | None:
    """Extract token usage from a Qwen Code stream-json result message.

    Qwen Code may use either the Claude-compatible ``usage`` dict or its own
    ``usageMetadata`` with ``promptTokenCount`` / ``candidatesTokenCount``.
    """
    usage = parsed.get("usage") or parsed.get("usageMetadata")
    if not isinstance(usage, dict):
        data = parsed.get("data", {})
        usage = data.get("usage") or data.get("usageMetadata")
    if isinstance(usage, dict):
        input_t: int = usage.get("input_tokens", usage.get("promptTokenCount", 0))
        output_t: int = usage.get("output_tokens", usage.get("candidatesTokenCount", 0))
        # Deduct cached tokens if present (Qwen-specific)
        cached: int = usage.get("cachedContentTokenCount", 0)
        input_t = max(0, input_t - cached)
        return {"input": input_t, "output": output_t}
    return None
