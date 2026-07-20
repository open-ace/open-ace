"""Canonical message-role normalization.

Centralizes the mapping of variant tool-result role spellings
(``toolResult``, ``tool_result``) to the canonical form ``tool``. The
canonical value matches the Anthropic/OpenAI native ``tool`` role and the
value the live agent runner already writes (``agent_runner`` ->
``session_manager.add_message(role="tool")``).

Why this matters: two parallel write paths persisted different spellings for
tool-result messages. The Claude/OpenClaw intake path wrote ``role="toolResult"``
while the live autonomous agent path wrote ``role="tool"``. Downstream
consumers (the conversation-detail role filter, message statistics, latency
curve) only compared against one spelling, so conversations produced by the
"other" path surfaced as "no messages found" when filtered.

Every write path into ``daily_messages`` funnels through
``normalize_message_role`` at the write boundary (mirroring
``normalize_tool_name``), so a tool-result message can never again split or
hide based on spelling. Matching is case- and whitespace-insensitive.

Decision basis (forensics on the codebase): the only observed variants were
``tool``, ``toolResult`` and ``tool_result`` -- all three representing the same
semantic "tool result" message. No arbitrary matching is performed; unknown
roles are returned stripped and lower-cased so future case drift cannot
re-introduce a split.
"""

from __future__ import annotations


# Canonical message roles. All write paths should reference these instead of
# bare string literals so role values can never drift.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLE_TOOL = "tool"

# Variant spellings observed across write paths, all mapping to ROLE_TOOL.
# Matched case- and whitespace-insensitively.
_TOOL_ROLE_ALIASES = {
    "tool",
    "toolresult",
    "tool_result",
}


def normalize_message_role(role: str | None) -> str:
    """Normalize a message role to its canonical form.

    Tool-result spellings (``toolResult``, ``tool_result``) collapse to the
    canonical ``tool``. Other roles (``user``/``assistant``/``system``) pass
    through. ``None``/blank input becomes ``"unknown"``. Unknown values are
    returned stripped and lower-cased so case drift never re-splits a
    downstream aggregate or filter.

    Args:
        role: Raw message role. May be ``None`` or contain surrounding
            whitespace / mixed case.

    Returns:
        Canonical role string (e.g. ``"tool"``). ``None``/blank input returns
        ``"unknown"``.

    Examples:
        >>> normalize_message_role("toolResult")
        'tool'
        >>> normalize_message_role(" tool_result ")
        'tool'
        >>> normalize_message_role("assistant")
        'assistant'
        >>> normalize_message_role(None)
        'unknown'
    """
    if not role or not role.strip():
        return "unknown"
    cleaned = role.strip().lower()
    if cleaned in _TOOL_ROLE_ALIASES:
        return ROLE_TOOL
    return cleaned
