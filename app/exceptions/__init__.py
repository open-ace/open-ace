"""
Open ACE Exceptions Module

Provides stable exception types for the application.
"""

from app.exceptions.sync_errors import (
    DingTalkSyncError,
    FeishuSyncError,
    OrgSyncError,
)

__all__ = [
    "OrgSyncError",
    "FeishuSyncError",
    "DingTalkSyncError",
]