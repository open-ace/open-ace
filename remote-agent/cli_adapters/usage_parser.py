"""
Shared stream-json usage extraction for CLI tool output.

Each CLI tool (Claude Code, Qwen Code, etc.) emits a different JSON format
for token usage in its ``type: "result"`` message.  This module centralises
the extraction logic so that both the remote-agent executor and the
autonomous agent_runner can reuse it.

Some tools (see :data:`CUMULATIVE_RESULT_TOOLS`) report CROSS-TURN CUMULATIVE
usage in their result message. For those, callers must difference successive
snapshots into per-turn deltas via :func:`diff_cumulative_usage` before
reporting — otherwise the running total is re-added every turn.
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

# Tools whose ``type: "result"`` message reports CROSS-TURN CUMULATIVE usage
# (e.g. qwen-code-webui derives ``usage.input_tokens`` from
# ``computeUsageFromMetrics()`` → ``stats.totalPromptTokens``). For these tools
# the raw value must NOT be reported as a per-turn delta — doing so re-adds the
# running total every turn and inflates ``session.total_*_tokens`` / quota.
# Instead callers difference successive snapshots (``cur - last``); see
# ``diff_cumulative_usage``. Tools not in this set report per-request usage in
# their result message and are reported as-is.
CUMULATIVE_RESULT_TOOLS: set[str] = {"qwen-code-cli"}


def is_cumulative_result_tool(cli_tool: str) -> bool:
    """Return True if *cli_tool*'s result message reports cumulative usage."""
    return cli_tool in CUMULATIVE_RESULT_TOOLS


def diff_cumulative_usage(
    cur: dict[str, int],
    last_input: int | None,
    last_output: int | None,
) -> tuple[dict[str, int], int, int]:
    """Derive a per-turn delta from a cumulative usage snapshot.

    Given the current cumulative snapshot *cur* (``{"input": ..., "output": ...}``)
    and the previous cumulative baseline (*last_input*/*last_output*, ``None`` on
    the first turn), return ``(delta, new_input, new_output)`` where:

    - ``delta`` is the clamped (``>= 0``) per-turn increment to report;
    - ``new_input``/``new_output`` are the updated cumulative baseline to store
      for the next call.

    First turn (either ``last`` is ``None``): ``delta == cur``, baseline seeded
    to *cur* (correct for a fresh session; see the resume caveat below).
    Subsequent turns: ``delta = max(0, cur - last)``; baseline updated to *cur*.

    Resume caveat: if a tool replays history on reconnect, the first post-resume
    result may already carry a high cumulative value, which would be reported in
    full. That is the same class of over-count that exists today (pre-fix) and is
    not introduced by differencing; callers may seed the baseline from the first
    result without reporting if a future empirical fix is warranted.
    """
    cur_input = int(cur.get("input", 0) or 0)
    cur_output = int(cur.get("output", 0) or 0)
    if last_input is None or last_output is None:
        delta_input, delta_output = cur_input, cur_output
    else:
        delta_input = max(0, cur_input - int(last_input))
        delta_output = max(0, cur_output - int(last_output))
    return {"input": delta_input, "output": delta_output}, cur_input, cur_output


def extract_stream_usage(cli_tool: str, parsed: dict[str, Any]) -> dict[str, int] | None:
    """Dispatch to the correct usage extractor based on *cli_tool*.

    This is the single entry-point that both ``executor.py`` and
    ``agent_runner.py`` should use. Returns the RAW usage reported by the tool
    (cumulative for tools in :data:`CUMULATIVE_RESULT_TOOLS`); callers are
    responsible for differencing cumulative values into per-turn deltas via
    :func:`diff_cumulative_usage`.
    """
    if cli_tool in _QWEN_TOOLS:
        return extract_qwen_stream_usage(parsed)
    if cli_tool in _ZCODE_TOOLS:
        return extract_zcode_usage(parsed)
    return extract_claude_stream_usage(parsed)
