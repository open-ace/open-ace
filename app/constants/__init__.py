"""
Open ACE - Constants

Shared, immutable constant definitions. Home for the single source of truth
on values that cross module boundaries (e.g. message roles written by both
the autonomous runner and the OpenClaw importer).
"""

from app.constants.message_role import MessageRole, is_tool_role, normalize_role

__all__ = [
    "MessageRole",
    "normalize_role",
    "is_tool_role",
]
