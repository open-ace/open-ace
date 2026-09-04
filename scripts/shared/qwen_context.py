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

Issue #3337: Qwen SDK may combine date system-reminder envelope with real
user text in a single user message. We need to strip the envelope and
preserve the user text, rather than filtering the entire message.
"""

import re

__all__ = ["is_qwen_system_context", "strip_qwen_system_envelopes"]

# Maximum length of a single system-reminder envelope.
# Envelopes longer than this are not stripped, to prevent accidental
# removal of user content that might contain similar-looking text.
_MAX_ENVELOPE_LENGTH = 2000

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

# Regex to match the date system-reminder envelope.
# Issue #3337: The date reminder may appear without "This is the Qwen Code"
# sentence, so we no longer require that marker for date reminders.
_STARTUP_REMINDER_RE = re.compile(r"<system-reminder>\s*The current date is:", re.IGNORECASE)

# Regex to match and extract system-reminder envelopes for stripping.
# Matches the entire <system-reminder>...</system-reminder> block.
_SYSTEM_REMINDER_TAG_RE = re.compile(
    r"<system-reminder>\s*(.*?)\s*</system-reminder>",
    re.IGNORECASE | re.DOTALL,
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
    # Issue #3337: Date reminder no longer requires "This is the Qwen Code"
    return _STARTUP_REMINDER_RE.search(text) is not None


def _is_date_system_reminder(content: str) -> bool:
    """Check if content looks like a date system-reminder envelope.

    Used internally by strip_qwen_system_envelopes() to validate envelope
    content before stripping.
    """
    if not content or len(content) > _MAX_ENVELOPE_LENGTH:
        return False
    # Check for date pattern
    return bool(_STARTUP_REMINDER_RE.search(content))


def strip_qwen_system_envelopes(content: str) -> str:
    """Strip known Qwen system-reminder envelopes from content.

    This function removes <system-reminder>...</system-reminder> envelopes
    that contain date information, preserving any real user text that may
    follow or precede them.

    Args:
        content: The user message content, potentially containing system
            envelopes prepended/appended by Qwen SDK.

    Returns:
        - Content with envelopes stripped if valid envelopes were found.
        - Original content unchanged if no envelopes found or envelopes
          don't match known system content patterns.
        - Empty string if all content was envelope(s) with no user text.

    Safety measures:
        - Only strips envelopes at the START of content (not middle/end),
          since Qwen SDK prepends the date reminder before user text.
        - Limits envelope length to _MAX_ENVELOPE_LENGTH to prevent
          accidental removal of user content that happens to contain
          similar-looking tags.
        - Only strips envelopes that match known system content patterns
          (currently: date reminders).

    Example:
        >>> strip_qwen_system_envelopes(
        ...     "<system-reminder>\\nThe current date is: 2026-09-04\\n</system-reminder>\\n\\n帮我修复这个 bug"
        ... )
        '帮我修复这个 bug'
    """
    if not content or not isinstance(content, str):
        return content if content else ""

    text = content.strip()
    if not text:
        return ""

    # Iterate and strip leading envelopes
    while True:
        # Try to match a system-reminder at the start
        match = _SYSTEM_REMINDER_TAG_RE.match(text)
        if not match:
            break

        # Validate this is a known system envelope (currently only date)
        if not _is_date_system_reminder(match.group(0)):
            # Not a known system envelope, stop stripping
            break

        # Strip this envelope and continue checking
        text = text[match.end() :].strip()

    return text
