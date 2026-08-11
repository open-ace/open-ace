"""
File change block extraction for Qwen CLI sessions.

Issue #8: Qwen CLI emits tool_use blocks for file operations (mkdir/mv/cp/rm,
write_file/edit) without corresponding file_change blocks. This module extracts
file change information from tool_use blocks for the frontend file-change panel.

This is a placeholder implementation. The full implementation will be added
in a follow-up change.
"""

__all__ = ["append_file_change_blocks", "extract_file_changes"]


def append_file_change_blocks(blocks: list[dict]) -> None:
    """Append synthetic file_change blocks derived from tool_use blocks.

    Args:
        blocks: List of content blocks to augment (modified in place).

    Note:
        This is a placeholder. The full implementation will parse tool_use
        blocks for shell commands (mkdir/mv/cp/rm) and native file tools
        (write_file/edit) to generate synthetic file_change blocks.
    """
    # Placeholder: no-op for now
    # TODO(Issue #8): implement file_change block extraction
    pass


def extract_file_changes(tool_use_block: dict) -> list[dict] | None:
    """Extract file change records from a tool_use block.

    Args:
        tool_use_block: A tool_use content block.

    Returns:
        List of file_change records, or None if not applicable.

    Note:
        This is a placeholder. The full implementation will parse tool_use
        blocks to identify file operations and generate change records.
    """
    # Placeholder: return None for now
    # TODO(Issue #8): implement file_change extraction
    return None
