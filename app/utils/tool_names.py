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

from __future__ import annotations




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


def normalize_tool_name(name: str | None) -> str:
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
