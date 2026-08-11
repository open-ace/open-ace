"""Qwen system context detection utilities.

Used by session_history_sync to filter out system-context user messages
that contain Qwen internal prompts.
"""

from __future__ import annotations


def is_qwen_system_context(content: str) -> bool:
    """Check if a user message content is a Qwen system-context message.

    System-context messages are injected by the Qwen CLI and should not
    appear in the webui history view.

    Args:
        content: The user message content to check.

    Returns:
        True if the message is a Qwen system-context message.
    """
    if not content:
        return False
    # System context messages contain specific markers
    markers = [
        "system-context:",
        "<system-context>",
        "QWEN_SYSTEM_CONTEXT",
    ]
    return any(marker in content for marker in markers)
