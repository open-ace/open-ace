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


def extract_zcode_usage(parsed: dict[str, Any]) -> dict[str, int] | None:
    """Extract token usage from a ZCode result.

    ZCode ``--json`` and the ZCode Protocol both report usage with
    camelCase keys (verified against the running CLI):

    .. code-block:: json

        {"usage": {"inputTokens": N, "outputTokens": M,
                   "cacheReadTokens": R, "reasoningTokens": T,
                   "modelRequestCount": C}}

    The persistent app-server also exposes a ``session/usage`` method with
    the same top-level camelCase keys (no ``usage`` wrapper); we accept both.
    Returns the common ``input``/``output`` keys plus zcode-specific extras
    (``cache_read``, ``reasoning``, ``model_requests``) when present.
    """
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        # session/usage shape: keys live at the top level.
        if any(k in parsed for k in ("inputTokens", "outputTokens")):
            usage = parsed
    if not isinstance(usage, dict):
        return None
    result: dict[str, int] = {
        "input": usage.get("inputTokens", 0),
        "output": usage.get("outputTokens", 0),
    }
    if "cacheReadTokens" in usage:
        result["cache_read"] = usage.get("cacheReadTokens", 0)
    if "reasoningTokens" in usage:
        result["reasoning"] = usage.get("reasoningTokens", 0)
    if "modelRequestCount" in usage:
        result["model_requests"] = usage.get("modelRequestCount", 0)
    return result


# Known tools that use the Qwen usage format
_QWEN_TOOLS = {"qwen-code-cli"}

# Known tools that use the ZCode camelCase usage format
_ZCODE_TOOLS = {"zcode", "zcode-code", "zcode-cli"}


def extract_stream_usage(cli_tool: str, parsed: dict[str, Any]) -> dict[str, int] | None:
    """Dispatch to the correct usage extractor based on *cli_tool*.

    This is the single entry-point that both ``executor.py`` and
    ``agent_runner.py`` should use.
    """
    if cli_tool in _QWEN_TOOLS:
        return extract_qwen_stream_usage(parsed)
    if cli_tool in _ZCODE_TOOLS:
        return extract_zcode_usage(parsed)
    return extract_claude_stream_usage(parsed)
