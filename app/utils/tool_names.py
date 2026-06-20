"""Canonical tool-name normalization.

Centralizes the mapping of variant tool names (e.g. ``qwen-code``,
``qwen-code-cli``, ``claude-code``) to their canonical form (``qwen``,
``claude``). Every write path and every aggregation query funnels through
``normalize_tool_name`` so a single tool never surfaces as multiple
pie-chart / summary slices (see ROI cost-breakdown duplication bug).

Matching is case- and whitespace-insensitive: ``"Qwen"``, ``" qwen-code "``
and ``"QWEN"`` all collapse correctly. Unknown names are returned stripped
and lower-cased so future case drift cannot re-split an aggregate. ``None``
or blank input becomes ``"unknown"``.

Decision basis (forensics on the live DB): the only observed variants were
already-enumerable aliases (``qwen-code``/``qwen-code-cli``/``claude-code``)
plus, defensively, potential case drift. No arbitrary prefixes were found,
so NO prefix/fuzzy matching is performed — it would risk silently merging a
future legitimately-distinct tool (e.g. ``qwen-<something>``) into ``qwen``.
"""

from typing import Optional

TOOL_NAME_ALIASES = {
    "qwen": ["qwen", "qwen-code", "qwen-code-cli"],
    "claude": ["claude", "claude-code"],
    "openclaw": ["openclaw"],
    "codex": ["codex", "codex-cli"],
    "zcode": ["zcode", "zcode-code", "zcode-cli"],
}

CANONICAL_TOOL_NAMES = {
    "qwen-code": "qwen",
    "qwen-code-cli": "qwen",
    "claude-code": "claude",
    "codex-cli": "codex",
    "zcode-code": "zcode",
    "zcode-cli": "zcode",
}

# Canonical message role for tool execution results, matching the OpenClaw
# importer (scripts/fetch_openclaw.py) and docs/en/CONCEPTS.md. Other write
# paths (remote session_sync, RemoteSessionManager) historically emitted
# inconsistent spellings (``tool_result``) or collapsed tool results into a
# generic ``system`` role, which broke the conversation-detail "ToolResult"
# filter. ``normalize_message_role`` unifies them at the write boundary, the
# same pattern as ``normalize_tool_name``.
TOOL_RESULT_ROLE_ALIASES = ("tool_result", "toolresult", "tool-result")
CANONICAL_TOOL_RESULT_ROLE = "toolResult"


def normalize_tool_name(name: Optional[str]) -> str:
    """Normalize a tool name to its canonical, lower-cased form.

    Args:
        name: Raw tool name. May be ``None`` or contain surrounding
            whitespace / mixed case.

    Returns:
        Canonical tool name (e.g. ``"qwen"``). ``None``/blank input returns
        ``"unknown"``. Unknown names are returned stripped and lower-cased
        so case drift never re-splits an aggregate.

    Examples:
        >>> normalize_tool_name("qwen-code")
        'qwen'
        >>> normalize_tool_name(" QWEN ")
        'qwen'
        >>> normalize_tool_name(None)
        'unknown'
        >>> normalize_tool_name("codex")
        'codex'
    """
    if not name or not name.strip():
        return "unknown"
    cleaned = name.strip().lower()
    return CANONICAL_TOOL_NAMES.get(cleaned, cleaned)


def normalize_message_role(role: Optional[str]) -> str:
    """Normalize a message role, collapsing tool-result spellings to ``toolResult``.

    Different write paths spell the tool-execution-result role differently
    (``toolResult`` from the OpenClaw importer, ``tool_result`` from the
    remote agent session_sync, ``toolResult`` from the frontend). This
    collapses every known spelling/casing variant to the canonical
    ``toolResult`` so the conversation-detail "ToolResult" filter matches
    consistently across all data sources.

    Other roles (``user``, ``assistant``, ``system``, ``error``, ...) are
    returned stripped (case preserved) so unrelated consumers are
    unaffected. ``None``/blank input returns an empty string.

    Note: this does NOT remap a generic ``system`` role to ``toolResult`` —
    that cannot be done reliably from the role alone because genuine system
    messages share the ``system`` role. Tool-result detection from content
    must happen at the originating write path (where the tool_result block
    is known), not here.

    Examples:
        >>> normalize_message_role("tool_result")
        'toolResult'
        >>> normalize_message_role("ToolResult")
        'toolResult'
        >>> normalize_message_role("user")
        'user'
        >>> normalize_message_role(None)
        ''
    """
    if role is None:
        return ""
    cleaned = role.strip()
    if not cleaned:
        return ""
    if cleaned.lower() in TOOL_RESULT_ROLE_ALIASES:
        return CANONICAL_TOOL_RESULT_ROLE
    return cleaned
