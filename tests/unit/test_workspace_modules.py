#!/usr/bin/env python3
"""
Tests for Workspace Module

Unit tests for prompt_library, session_manager, tool_connector,
state_sync, and collaboration modules.
"""

import os
import tempfile
from datetime import datetime

import pytest

from app.modules.workspace.collaboration import (
    CollaborationManager,
    SharePermission,
)
from app.modules.workspace.prompt_library import PromptCategory, PromptLibrary, PromptTemplate
from app.modules.workspace.session_manager import (
    SessionManager,
    SessionStatus,
    SessionType,
)
from app.modules.workspace.state_sync import StateSyncManager, SyncEvent, SyncEventType
from app.modules.workspace.tool_connector import ToolConnector, ToolInfo, ToolType

# ==================== Fixtures ====================


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def prompt_library(temp_db):
    """Create a PromptLibrary instance with temp database."""
    lib = PromptLibrary(db_path=temp_db)
    lib._ensure_tables()
    return lib


@pytest.fixture
def session_manager(temp_db):
    """Create a SessionManager instance with temp database."""
    mgr = SessionManager(db_path=temp_db)
    mgr._ensure_tables()
    return mgr


@pytest.fixture
def state_sync(temp_db):
    """Create a StateSyncManager instance with temp database."""
    mgr = StateSyncManager(db_path=temp_db)
    mgr._ensure_tables()
    return mgr


@pytest.fixture
def collaboration(temp_db):
    """Create a CollaborationManager instance with temp database."""
    mgr = CollaborationManager(db_path=temp_db)
    mgr._ensure_tables()
    return mgr


# ==================== Prompt Library Tests ====================


class TestPromptLibrary:
    """Tests for PromptLibrary."""

    def test_create_template(self, prompt_library):
        """Test creating a prompt template."""
        template = PromptTemplate(
            name="Test Template",
            description="A test template",
            category=PromptCategory.CODING.value,
            content="Hello {name}, please help with {task}",
            variables=[
                {"name": "name", "description": "User name", "required": True},
                {"name": "task", "description": "Task description", "required": True},
            ],
            tags=["test", "example"],
            is_public=True,
        )

        template_id = prompt_library.create_template(template)
        assert template_id is not None
        assert template_id > 0

    def test_get_template(self, prompt_library):
        """Test retrieving a prompt template."""
        template = PromptTemplate(name="Get Test", content="Test content", is_public=True)
        template_id = prompt_library.create_template(template)

        retrieved = prompt_library.get_template(template_id)
        assert retrieved is not None
        assert retrieved.name == "Get Test"
        assert retrieved.content == "Test content"

    def test_update_template(self, prompt_library):
        """Test updating a prompt template."""
        template = PromptTemplate(name="Update Test", content="Original content")
        template_id = prompt_library.create_template(template)

        template.id = template_id
        template.content = "Updated content"
        success = prompt_library.update_template(template)
        assert success

        retrieved = prompt_library.get_template(template_id)
        assert retrieved.content == "Updated content"

    def test_delete_template(self, prompt_library):
        """Test deleting a prompt template."""
        template = PromptTemplate(name="Delete Test", content="To be deleted")
        template_id = prompt_library.create_template(template)

        success = prompt_library.delete_template(template_id)
        assert success

        retrieved = prompt_library.get_template(template_id)
        assert retrieved is None

    def test_render_template(self, prompt_library):
        """Test rendering a template with variables."""
        template = PromptTemplate(
            name="Render Test",
            content="Hello {name}, your task is {task}",
            variables=[{"name": "name", "required": True}, {"name": "task", "required": True}],
        )
        template_id = prompt_library.create_template(template)

        # Get the template and render it
        retrieved = prompt_library.get_template(template_id)
        rendered = retrieved.render(name="Alice", task="coding")
        assert rendered == "Hello Alice, your task is coding"

    def test_validate_variables(self, prompt_library):
        """Test variable validation."""
        template = PromptTemplate(
            name="Validation Test",
            content="Hello {name}",
            variables=[{"name": "name", "required": True}],
        )

        # Missing required variable
        missing = template.validate_variables()
        assert "name" in missing

        # All variables provided
        missing = template.validate_variables(name="Alice")
        assert len(missing) == 0

    def test_list_templates(self, prompt_library):
        """Test listing templates with pagination."""
        for i in range(5):
            template = PromptTemplate(name=f"List Test {i}", content=f"Content {i}", is_public=True)
            prompt_library.create_template(template)

        result = prompt_library.list_templates(page=1, limit=3)
        assert len(result["templates"]) == 3
        assert result["total"] == 5
        assert result["total_pages"] == 2


# ==================== Session Manager Tests ====================


class TestSessionManager:
    """Tests for SessionManager."""

    def test_create_session(self, session_manager):
        """Test creating a session."""
        session = session_manager.create_session(
            tool_name="claude", user_id=1, session_type=SessionType.CHAT.value, title="Test Session"
        )

        assert session.session_id is not None
        assert session.tool_name == "claude"
        assert session.status == SessionStatus.ACTIVE.value
        assert session.title == "Test Session"

    def test_get_session(self, session_manager):
        """Test retrieving a session."""
        created = session_manager.create_session(tool_name="qwen")

        retrieved = session_manager.get_session(created.session_id)
        assert retrieved is not None
        assert retrieved.session_id == created.session_id

    def test_complete_session(self, session_manager):
        """Test completing a session."""
        session = session_manager.create_session(tool_name="claude")

        success = session_manager.complete_session(session.session_id)
        assert success

        retrieved = session_manager.get_session(session.session_id)
        assert retrieved.status == SessionStatus.COMPLETED.value
        assert retrieved.completed_at is not None

    def test_delete_session(self, session_manager):
        """Test deleting a session."""
        session = session_manager.create_session(tool_name="claude")

        success = session_manager.delete_session(session.session_id)
        assert success

        retrieved = session_manager.get_session(session.session_id)
        assert retrieved is None

    def test_list_sessions(self, session_manager):
        """Test listing sessions."""
        for i in range(3):
            session_manager.create_session(tool_name="claude", user_id=1, title=f"Session {i}")

        result = session_manager.list_sessions(user_id=1)
        assert len(result["sessions"]) == 3

    def test_session_expiration(self, session_manager):
        """Test session expiration."""
        session = session_manager.create_session(tool_name="claude", expires_in_hours=1)

        assert session.expires_at is not None
        assert not session.is_expired()

    def test_session_stats(self, session_manager):
        """Test session statistics."""
        session_manager.create_session(tool_name="claude", user_id=1)
        session_manager.create_session(tool_name="qwen", user_id=1)

        stats = session_manager.get_session_stats(user_id=1)
        assert stats["total_sessions"] == 2
        assert stats["active_sessions"] == 2


# ==================== Tool Connector Tests ====================


class TestToolConnector:
    """Tests for ToolConnector."""

    def test_list_tools(self):
        """Test listing available tools."""
        connector = ToolConnector()
        tools = connector.list_tools()

        assert len(tools) > 0
        tool_names = [t.name for t in tools]
        assert "claude" in tool_names
        assert "qwen" in tool_names

    def test_get_tool(self):
        """Test getting tool info."""
        connector = ToolConnector()
        tool = connector.get_tool("claude")

        assert tool is not None
        assert tool.name == "claude"
        assert tool.display_name == "Claude (Anthropic)"

    def test_get_available_models(self):
        """Test getting available models."""
        connector = ToolConnector()
        models = connector.get_available_models("claude")

        assert len(models) > 0
        assert any(m["tool"] == "claude" for m in models)

    def test_register_tool(self):
        """Test registering a custom tool."""
        connector = ToolConnector()

        custom_tool = ToolInfo(
            name="custom_tool",
            display_name="Custom Tool",
            tool_type=ToolType.CHAT.value,
            models=["model-1"],
            default_model="model-1",
        )

        connector.register_tool(custom_tool)
        retrieved = connector.get_tool("custom_tool")

        assert retrieved is not None
        assert retrieved.name == "custom_tool"

    def test_tool_stats(self):
        """Test tool statistics."""
        connector = ToolConnector()
        stats = connector.get_tool_stats()

        assert "total" in stats
        assert "online" in stats
        assert stats["total"] > 0


# ==================== State Sync Tests ====================


class TestStateSync:
    """Tests for StateSyncManager."""

    def test_register_client(self, state_sync):
        """Test registering a client."""
        state = state_sync.register_client(user_id=1)

        assert state.client_id is not None
        assert state.user_id == 1
        assert state.connected_at is not None

    def test_unregister_client(self, state_sync):
        """Test unregistering a client."""
        state = state_sync.register_client()

        success = state_sync.unregister_client(state.client_id)
        assert success

        retrieved = state_sync.get_client_state(state.client_id)
        assert retrieved is None

    def test_emit_event(self, state_sync):
        """Test emitting an event."""
        event = SyncEvent(
            event_id="test-event-1",
            event_type=SyncEventType.SESSION_START.value,
            timestamp=datetime.utcnow(),
            source="test",
            session_id="session-1",
            data={"key": "value"},
        )

        state_sync.emit_event(event)

        # Retrieve the event
        events = state_sync.get_events(session_id="session-1")
        assert len(events) == 1
        assert events[0].event_id == "test-event-1"

    def test_subscribe(self, state_sync):
        """Test client subscription."""
        state = state_sync.register_client()

        state_sync.subscribe(state.client_id, ["session_start", "message_sent"])
        assert "session_start" in state_sync._clients[state.client_id].subscriptions

    def test_get_stats(self, state_sync):
        """Test sync statistics."""
        state_sync.register_client()
        state_sync.register_client()

        stats = state_sync.get_stats()
        assert stats["connected_clients"] == 2


# ==================== Collaboration Tests ====================


class TestCollaboration:
    """Tests for CollaborationManager."""

    def test_create_team(self, collaboration):
        """Test creating a team."""
        team = collaboration.create_team(name="Test Team", owner_id=1, description="A test team")

        assert team.team_id is not None
        assert team.name == "Test Team"
        assert team.owner_id == 1

    def test_add_team_member(self, collaboration):
        """Test adding a team member."""
        team = collaboration.create_team(name="Member Test", owner_id=1)

        success = collaboration.add_team_member(
            team_id=team.team_id, user_id=2, username="testuser", role="member"
        )
        assert success

        retrieved = collaboration.get_team(team.team_id)
        assert len(retrieved.members) == 2  # Owner + new member

    def test_share_session(self, collaboration):
        """Test sharing a session."""
        share = collaboration.share_session(
            session_id="session-1",
            shared_by=1,
            shared_by_name="Owner",
            permission=SharePermission.VIEW.value,
            share_type="user",
            target_id=2,
            target_name="User",
        )

        assert share.share_id is not None
        assert share.session_id == "session-1"
        assert share.permission == SharePermission.VIEW.value

    def test_get_user_shared_sessions(self, collaboration):
        """Test getting sessions shared with a user."""
        collaboration.share_session(
            session_id="session-1",
            shared_by=1,
            shared_by_name="Owner",
            share_type="user",
            target_id=2,
        )

        shares = collaboration.get_user_shared_sessions(user_id=2)
        assert len(shares) == 1
        assert shares[0].session_id == "session-1"

    def test_add_annotation(self, collaboration):
        """Test adding an annotation."""
        annotation = collaboration.add_annotation(
            session_id="session-1",
            user_id=1,
            username="User",
            content="This is a comment",
            annotation_type="comment",
        )

        assert annotation.annotation_id is not None
        assert annotation.content == "This is a comment"

    def test_create_knowledge_entry(self, collaboration):
        """Test creating a knowledge entry."""
        entry = collaboration.create_knowledge_entry(
            title="Test Knowledge",
            content="This is knowledge content",
            author_id=1,
            author_name="Author",
            category="general",
            is_published=True,
        )

        assert entry.entry_id is not None
        assert entry.title == "Test Knowledge"
        assert entry.is_published

    def test_list_knowledge_entries(self, collaboration):
        """Test listing knowledge entries."""
        for i in range(3):
            collaboration.create_knowledge_entry(
                title=f"Entry {i}",
                content=f"Content {i}",
                author_id=1,
                author_name="Author",
                is_published=True,
            )

        result = collaboration.list_knowledge_entries()
        assert len(result["entries"]) == 3


# ==================== Integration Tests ====================


class TestWorkspaceIntegration:
    """Integration tests for workspace modules."""

    def test_full_session_workflow(self, temp_db):
        """Test a complete session workflow."""
        # Create session
        session_mgr = SessionManager(db_path=temp_db)
        session = session_mgr.create_session(
            tool_name="claude", user_id=1, title="Integration Test"
        )

        # Emit sync events
        sync_mgr = StateSyncManager(db_path=temp_db)
        sync_mgr.emit_event(
            SyncEvent(
                event_id="event-1",
                event_type=SyncEventType.SESSION_START.value,
                timestamp=datetime.utcnow(),
                source="test",
                session_id=session.session_id,
            )
        )

        # Complete session
        session_mgr.complete_session(session.session_id)

        # Verify
        retrieved = session_mgr.get_session(session.session_id)
        assert retrieved.status == SessionStatus.COMPLETED.value

        events = sync_mgr.get_events(session_id=session.session_id)
        assert len(events) == 1

    def test_collaboration_workflow(self, temp_db):
        """Test a complete collaboration workflow."""
        collab = CollaborationManager(db_path=temp_db)

        # Create team
        team = collab.create_team(name="Workflow Team", owner_id=1)

        # Add member
        collab.add_team_member(team_id=team.team_id, user_id=2, username="member")

        # Share session
        collab.share_session(
            session_id="session-1",
            shared_by=1,
            shared_by_name="Owner",
            share_type="team",
            target_id=team.team_id,
        )

        # Add annotation
        collab.add_annotation(
            session_id="session-1", user_id=2, username="member", content="Great session!"
        )

        # Verify
        shares = collab.get_user_shared_sessions(user_id=2)
        assert len(shares) > 0

        annotations = collab.get_session_annotations("session-1")
        assert len(annotations) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
