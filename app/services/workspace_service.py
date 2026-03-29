#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Workspace Service

Business logic layer for workspace operations.
"""

import logging
from typing import Any, Dict, List, Optional

from app.modules.workspace.collaboration import (
    Annotation,
    CollaborationManager,
    KnowledgeEntry,
    SharedSession,
    Team,
)
from app.modules.workspace.prompt_library import PromptLibrary, PromptTemplate
from app.modules.workspace.session_manager import (
    AgentSession,
    SessionManager,
    SessionType,
)
from app.modules.workspace.state_sync import (
    StateSyncManager,
    SyncEvent,
    SyncEventType,
    get_state_sync_manager,
)
from app.modules.workspace.tool_connector import ToolConnector, ToolInfo, get_tool_connector

logger = logging.getLogger(__name__)


class WorkspaceService:
    """
    Service layer for workspace operations.

    Provides a unified interface for:
    - Prompt template management
    - Session management
    - Tool connections
    - State synchronization
    - Collaboration features
    """

    def __init__(self):
        """Initialize the workspace service."""
        self._prompt_library: Optional[PromptLibrary] = None
        self._session_manager: Optional[SessionManager] = None
        self._tool_connector: Optional[ToolConnector] = None
        self._state_sync: Optional[StateSyncManager] = None
        self._collaboration: Optional[CollaborationManager] = None

    @property
    def prompts(self) -> PromptLibrary:
        """Get the prompt library instance."""
        if self._prompt_library is None:
            self._prompt_library = PromptLibrary()
        return self._prompt_library

    @property
    def sessions(self) -> SessionManager:
        """Get the session manager instance."""
        if self._session_manager is None:
            self._session_manager = SessionManager()
        return self._session_manager

    @property
    def tools(self) -> ToolConnector:
        """Get the tool connector instance."""
        if self._tool_connector is None:
            self._tool_connector = get_tool_connector()
        return self._tool_connector

    @property
    def sync(self) -> StateSyncManager:
        """Get the state sync manager instance."""
        if self._state_sync is None:
            self._state_sync = get_state_sync_manager()
        return self._state_sync

    @property
    def collaboration(self) -> CollaborationManager:
        """Get the collaboration manager instance."""
        if self._collaboration is None:
            self._collaboration = CollaborationManager()
        return self._collaboration

    # ==================== Prompt Operations ====================

    def create_prompt_template(
        self,
        name: str,
        content: str,
        user_id: Optional[int] = None,
        username: str = "",
        description: str = "",
        category: str = "general",
        variables: Optional[List[Dict[str, str]]] = None,
        tags: Optional[List[str]] = None,
        is_public: bool = False,
    ) -> PromptTemplate:
        """
        Create a new prompt template.

        Args:
            name: Template name.
            content: Template content with {variable} placeholders.
            user_id: Creator user ID.
            username: Creator username.
            description: Template description.
            category: Template category.
            variables: List of variable definitions.
            tags: List of tags.
            is_public: Whether the template is public.

        Returns:
            PromptTemplate: The created template.
        """
        template = PromptTemplate(
            name=name,
            description=description,
            category=category,
            content=content,
            variables=variables or [],
            tags=tags or [],
            author_id=user_id,
            author_name=username,
            is_public=is_public,
        )

        template_id = self.prompts.create_template(template)
        template.id = template_id

        logger.info(f"Created prompt template: {name} (ID: {template_id})")
        return template

    def render_prompt(self, template_id: int, variables: Dict[str, str]) -> str:
        """
        Render a prompt template with variables.

        Args:
            template_id: Template ID.
            variables: Variable values.

        Returns:
            str: Rendered prompt.

        Raises:
            ValueError: If template not found or missing variables.
        """
        template = self.prompts.get_template(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")

        missing = template.validate_variables(**variables)
        if missing:
            raise ValueError(f"Missing required variables: {', '.join(missing)}")

        rendered = template.render(**variables)
        self.prompts.increment_use_count(template_id)

        return rendered

    # ==================== Session Operations ====================

    def start_session(
        self,
        tool_name: str,
        user_id: Optional[int] = None,
        session_type: str = SessionType.CHAT.value,
        title: str = "",
        model: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AgentSession:
        """
        Start a new agent session.

        Args:
            tool_name: AI tool to use.
            user_id: User ID.
            session_type: Type of session.
            title: Session title.
            model: Model to use.
            context: Initial context.

        Returns:
            AgentSession: The created session.
        """
        session = self.sessions.create_session(
            tool_name=tool_name,
            user_id=user_id,
            session_type=session_type,
            title=title,
            model=model,
            context=context,
        )

        # Emit session start event
        self.sync.emit_event(
            SyncEvent(
                event_id=str(__import__("uuid").uuid4()),
                event_type=SyncEventType.SESSION_START.value,
                timestamp=__import__("datetime").datetime.utcnow(),
                source="workspace",
                session_id=session.session_id,
                user_id=user_id,
                tool_name=tool_name,
                data={"title": title, "model": model},
            )
        )

        logger.info(f"Started session: {session.session_id} for tool: {tool_name}")
        return session

    def add_message_to_session(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        model: Optional[str] = None,
    ) -> int:
        """
        Add a message to a session.

        Args:
            session_id: Session ID.
            role: Message role (user, assistant, system, tool).
            content: Message content.
            tokens_used: Tokens used.
            model: Model used.

        Returns:
            int: Message ID.
        """
        message_id = self.sessions.add_message(
            session_id=session_id, role=role, content=content, tokens_used=tokens_used, model=model
        )

        # Emit message event
        event_type = (
            SyncEventType.MESSAGE_SENT if role == "user" else SyncEventType.MESSAGE_RECEIVED
        )
        self.sync.emit_event(
            SyncEvent(
                event_id=str(__import__("uuid").uuid4()),
                event_type=event_type.value,
                timestamp=__import__("datetime").datetime.utcnow(),
                source="workspace",
                session_id=session_id,
                data={"role": role, "tokens_used": tokens_used},
            )
        )

        return message_id

    def end_session(self, session_id: str) -> bool:
        """
        End a session.

        Args:
            session_id: Session ID.

        Returns:
            bool: True if successful.
        """
        success = self.sessions.complete_session(session_id)

        if success:
            # Emit session end event
            self.sync.emit_event(
                SyncEvent(
                    event_id=str(__import__("uuid").uuid4()),
                    event_type=SyncEventType.SESSION_END.value,
                    timestamp=__import__("datetime").datetime.utcnow(),
                    source="workspace",
                    session_id=session_id,
                )
            )

            logger.info(f"Ended session: {session_id}")

        return success

    def recover_session(self, session_id: str) -> Optional[AgentSession]:
        """
        Recover a paused or interrupted session.

        Args:
            session_id: Session ID.

        Returns:
            AgentSession if recovery successful, None otherwise.
        """
        session = self.sessions.recover_session(session_id)

        if session:
            logger.info(f"Recovered session: {session_id}")

        return session

    # ==================== Tool Operations ====================

    def get_available_tools(self) -> List[ToolInfo]:
        """
        Get all available AI tools.

        Returns:
            List of ToolInfo objects.
        """
        return self.tools.list_tools()

    def get_tool_info(self, tool_name: str) -> Optional[ToolInfo]:
        """
        Get information about a specific tool.

        Args:
            tool_name: Tool name.

        Returns:
            ToolInfo or None.
        """
        return self.tools.get_tool(tool_name)

    async def send_message_to_tool(
        self,
        tool_name: str,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Send a message to an AI tool.

        Args:
            tool_name: Tool to use.
            message: Message to send.
            session_id: Optional session ID.
            model: Optional model to use.
            **kwargs: Additional parameters.

        Returns:
            Dict with response data.
        """
        result = await self.tools.send_message(
            tool_name=tool_name, message=message, session_id=session_id, model=model, **kwargs
        )

        # Emit tool call event
        self.sync.emit_event(
            SyncEvent(
                event_id=str(__import__("uuid").uuid4()),
                event_type=SyncEventType.TOOL_CALL.value,
                timestamp=__import__("datetime").datetime.utcnow(),
                source="workspace",
                session_id=session_id,
                tool_name=tool_name,
                data={"model": model, "success": result.get("success", False)},
            )
        )

        return result

    # ==================== Collaboration Operations ====================

    def create_team(self, name: str, owner_id: int, description: str = "") -> Team:
        """
        Create a new team.

        Args:
            name: Team name.
            owner_id: Owner user ID.
            description: Team description.

        Returns:
            Team: The created team.
        """
        return self.collaboration.create_team(name=name, owner_id=owner_id, description=description)

    def share_session(
        self,
        session_id: str,
        shared_by: int,
        shared_by_name: str,
        permission: str = "view",
        share_type: str = "user",
        target_id: Optional[int] = None,
        target_name: str = "",
    ) -> SharedSession:
        """
        Share a session with a user or team.

        Args:
            session_id: Session to share.
            shared_by: User ID sharing the session.
            shared_by_name: Name of user sharing.
            permission: Permission level.
            share_type: 'user', 'team', or 'public'.
            target_id: Target user or team ID.
            target_name: Target name.

        Returns:
            SharedSession: The created share.
        """
        return self.collaboration.share_session(
            session_id=session_id,
            shared_by=shared_by,
            shared_by_name=shared_by_name,
            permission=permission,
            share_type=share_type,
            target_id=target_id,
            target_name=target_name,
        )

    def add_annotation(
        self,
        session_id: str,
        user_id: int,
        username: str,
        content: str,
        message_id: Optional[str] = None,
        annotation_type: str = "comment",
    ) -> Annotation:
        """
        Add an annotation to a session.

        Args:
            session_id: Session ID.
            user_id: User ID.
            username: Username.
            content: Annotation content.
            message_id: Optional message ID.
            annotation_type: Type of annotation.

        Returns:
            Annotation: The created annotation.
        """
        return self.collaboration.add_annotation(
            session_id=session_id,
            user_id=user_id,
            username=username,
            content=content,
            message_id=message_id,
            annotation_type=annotation_type,
        )

    def create_knowledge_entry(
        self,
        title: str,
        content: str,
        author_id: int,
        author_name: str,
        team_id: Optional[str] = None,
        category: str = "general",
        tags: Optional[List[str]] = None,
        is_published: bool = False,
    ) -> KnowledgeEntry:
        """
        Create a knowledge base entry.

        Args:
            title: Entry title.
            content: Entry content.
            author_id: Author user ID.
            author_name: Author name.
            team_id: Optional team ID.
            category: Entry category.
            tags: Optional tags.
            is_published: Whether to publish immediately.

        Returns:
            KnowledgeEntry: The created entry.
        """
        return self.collaboration.create_knowledge_entry(
            title=title,
            content=content,
            author_id=author_id,
            author_name=author_name,
            team_id=team_id,
            category=category,
            tags=tags,
            is_published=is_published,
        )

    # ==================== Statistics ====================

    def get_workspace_stats(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get workspace statistics.

        Args:
            user_id: Optional user ID filter.

        Returns:
            Dict with workspace statistics.
        """
        session_stats = self.sessions.get_session_stats(user_id)
        tool_stats = self.tools.get_tool_stats()
        sync_stats = self.sync.get_stats()

        return {"sessions": session_stats, "tools": tool_stats, "sync": sync_stats}


# Global workspace service instance
_workspace_service: Optional[WorkspaceService] = None


def get_workspace_service() -> WorkspaceService:
    """
    Get the global workspace service instance.

    Returns:
        WorkspaceService: The global instance.
    """
    global _workspace_service
    if _workspace_service is None:
        _workspace_service = WorkspaceService()
    return _workspace_service
