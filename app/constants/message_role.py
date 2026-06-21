"""
Open ACE - Message role constants

Single source of truth for the ``role`` values written to ``session_messages``
(and surfaced via the daily_messages view).

Why this module exists
----------------------
Two writers historically hardcoded the tool-result role with different
spellings, and neither matched the other:

* the autonomous agent runner wrote ``role='tool'`` (the OpenAI/Anthropic API
  standard), while
* the OpenClaw importer wrote ``role='toolResult'`` (a legacy spelling).

The frontend's role filter then compared messages against a single literal,
so one source was always invisible. Centralizing the constants here lets every
writer reference ``MessageRole.TOOL`` instead of retyping the string, and
gives the importer's normalization a named, documented home.

Canonical values follow the OpenAI/Anthropic message protocol. Importers that
ingest foreign formats normalize to these values via ``normalize_role``.
"""

from __future__ import annotations

from typing import Final


class MessageRole:
    """Canonical message role values (OpenAI/Anthropic API standard).

    A plain class rather than ``enum.Enum`` so the constants are plain ``str``
    values — they flow through JSON serialization and SQL parameters without
    ``.value`` plumbing.
    """

    USER: Final[str] = "user"
    ASSISTANT: Final[str] = "assistant"
    SYSTEM: Final[str] = "system"
    # Tool result. The API-standard spelling; historical ``'toolResult'``
    # records are normalized to this on ingest (see ``normalize_role``).
    TOOL: Final[str] = "tool"


# Legacy spellings that must fold into a canonical role on ingest. Keys are the
# raw value as it appears in foreign source data; values are the canonical
# MessageRole.* to map to. Add new aliases here rather than scattering
# conditional normalization across importers.
_ROLE_ALIASES: dict[str, str] = {
    "toolResult": MessageRole.TOOL,
}


def normalize_role(role: str | None) -> str:
    """Normalize a raw role string to its canonical form.

    ``'toolResult'`` (the legacy OpenClaw spelling) collapses to ``'tool'``.
    Any other value is returned unchanged so unrecognized roles stay legible
    instead of being silently rewritten. ``None``/empty become an empty
    string so callers can treat the result as a plain ``str``.
    """
    if not role:
        return ""
    return _ROLE_ALIASES.get(role, role)


def is_tool_role(role: str | None) -> bool:
    """True when ``role`` (raw or canonical) denotes a tool result."""
    return normalize_role(role) == MessageRole.TOOL
