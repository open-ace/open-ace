"""
Shared helpers to identify Qwen CLI system-context messages.

Qwen CLI writes its system prompts (Platform Tool Limits, startup context,
memory-management instructions, managed-memory guidance) into the session
JSONL as ``type=user`` entries and sends them to the LLM as ``role=user``
messages, because they must be part of the model context. Downstream sinks
(remote session sync, llm-proxy message recording, fetch_qwen history import)
must never surface these as user chat messages in the conversation history.

Issue #28: restoring a remote session showed these internal prompts as
"User" messages. This module is the single place that recognises them.
"""

import re

__all__ = ["is_qwen_system_context"]

# Content markers that identify Qwen system-context user messages. These are
# literal fragments from the CLI's injected prompts (see TROUBLESHOOTING_LOG
# Issue #28 / err.txt). A real user message essentially never contains them.
_SYSTEM_CONTEXT_MARKERS = (
    "[Platform Tool Limits]",
    "This is the Qwen Code. We are setting up the context for our chat.",
    "Memory directory:",
    "Managed memory has TWO directories",
)

# Phase headers of the qwen memory-management instruction block, matching both
# the em-dash and hyphen spellings seen in the wild.
_PHASE_HEADER_RE = re.compile(r"## Phase \d+\s+[—-]\s+", re.IGNORECASE)

# Some CLIs prefix the startup-context reminder with <system-reminder>; the
# date line may vary, so anchor on the Qwen-specific sentence.
_STARTUP_REMINDER_RE = re.compile(
    r"<system-reminder>\s*The current date is:", re.IGNORECASE
)


def is_qwen_system_context(content) -> bool:
    """Return True if ``content`` is Qwen system context that must not be
    surfaced as a user chat message.

    Accepts None / non-string input defensively (callers pass optional
    ``content`` fields).
    """
    if not content or not isinstance(content, str):
        return False
    text = content.strip()
    if not text:
        return False
    if any(marker in text for marker in _SYSTEM_CONTEXT_MARKERS):
        return True
    if _PHASE_HEADER_RE.search(text):
        return True
    if _STARTUP_REMINDER_RE.search(text) and "This is the Qwen Code" in text:
        return True
    return False
