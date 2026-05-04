"""
Open ACE - Workspace Module

This module provides workspace-related functionality for the "用" (Use) aspect
of the Open ACE platform. It includes:

- Prompt Library: Template management for AI prompts
- Session Manager: Session persistence and recovery
- Tool Connector: Unified interface for AI tools
- State Sync: Real-time state synchronization
- Collaboration: Team collaboration features
"""

from app.modules.workspace.collaboration import CollaborationManager, SharedSession
from app.modules.workspace.prompt_library import PromptLibrary, PromptTemplate
from app.modules.workspace.session_manager import AgentSession, SessionManager
from app.modules.workspace.state_sync import StateSyncManager, SyncState
from app.modules.workspace.tool_connector import ToolConnector, ToolInfo

__all__ = [
    "PromptLibrary",
    "PromptTemplate",
    "SessionManager",
    "AgentSession",
    "ToolConnector",
    "ToolInfo",
    "StateSyncManager",
    "SyncState",
    "CollaborationManager",
    "SharedSession",
]
