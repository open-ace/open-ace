#!/usr/bin/env python3
"""
Open ACE - Collaboration Module

Provides team collaboration features for shared sessions and knowledge base.
Supports session sharing, team workspaces, and collaborative annotations.
"""

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from app.repositories.database import DB_PATH, is_postgresql, get_database_url

logger = logging.getLogger(__name__)


class SharePermission(Enum):
    """Share permission levels."""
    VIEW = 'view'
    COMMENT = 'comment'
    EDIT = 'edit'
    ADMIN = 'admin'


class TeamRole(Enum):
    """Team member roles."""
    MEMBER = 'member'
    ADMIN = 'admin'
    OWNER = 'owner'


@dataclass
class TeamMember:
    """A member of a team."""
    user_id: int
    username: str
    role: str = TeamRole.MEMBER.value
    joined_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'user_id': self.user_id,
            'username': self.username,
            'role': self.role,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
        }


@dataclass
class Team:
    """A team for collaboration."""
    id: Optional[int] = None
    team_id: str = ''
    name: str = ''
    description: str = ''
    owner_id: Optional[int] = None
    members: List[TeamMember] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'team_id': self.team_id,
            'name': self.name,
            'description': self.description,
            'owner_id': self.owner_id,
            'members': [m.to_dict() for m in self.members],
            'settings': self.settings,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class SharedSession:
    """A session shared with users or teams."""
    id: Optional[int] = None
    share_id: str = ''
    session_id: str = ''
    shared_by: Optional[int] = None
    shared_by_name: str = ''
    permission: str = SharePermission.VIEW.value
    share_type: str = 'user'  # user, team, public
    target_id: Optional[int] = None  # user_id or team_id
    target_name: str = ''
    expires_at: Optional[datetime] = None
    allow_comments: bool = True
    allow_copy: bool = True
    created_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'share_id': self.share_id,
            'session_id': self.session_id,
            'shared_by': self.shared_by,
            'shared_by_name': self.shared_by_name,
            'permission': self.permission,
            'share_type': self.share_type,
            'target_id': self.target_id,
            'target_name': self.target_name,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'allow_comments': self.allow_comments,
            'allow_copy': self.allow_copy,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'access_count': self.access_count,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None,
        }

    def is_expired(self) -> bool:
        """Check if share is expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def can_view(self) -> bool:
        """Check if can view."""
        return not self.is_expired()

    def can_edit(self) -> bool:
        """Check if can edit."""
        return not self.is_expired() and self.permission in [
            SharePermission.EDIT.value,
            SharePermission.ADMIN.value
        ]

    def can_comment(self) -> bool:
        """Check if can comment."""
        return not self.is_expired() and self.allow_comments and self.permission in [
            SharePermission.COMMENT.value,
            SharePermission.EDIT.value,
            SharePermission.ADMIN.value
        ]


@dataclass
class Annotation:
    """An annotation on a shared session."""
    id: Optional[int] = None
    annotation_id: str = ''
    session_id: str = ''
    message_id: Optional[str] = None
    user_id: Optional[int] = None
    username: str = ''
    content: str = ''
    annotation_type: str = 'comment'  # comment, highlight, question
    position: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[int] = None  # For threaded comments
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'annotation_id': self.annotation_id,
            'session_id': self.session_id,
            'message_id': self.message_id,
            'user_id': self.user_id,
            'username': self.username,
            'content': self.content,
            'annotation_type': self.annotation_type,
            'position': self.position,
            'parent_id': self.parent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class KnowledgeEntry:
    """An entry in the team knowledge base."""
    id: Optional[int] = None
    entry_id: str = ''
    team_id: Optional[str] = None
    title: str = ''
    content: str = ''
    category: str = 'general'
    tags: List[str] = field(default_factory=list)
    author_id: Optional[int] = None
    author_name: str = ''
    is_published: bool = False
    view_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'entry_id': self.entry_id,
            'team_id': self.team_id,
            'title': self.title,
            'content': self.content,
            'category': self.category,
            'tags': self.tags,
            'author_id': self.author_id,
            'author_name': self.author_name,
            'is_published': self.is_published,
            'view_count': self.view_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class CollaborationManager:
    """
    Manager for team collaboration features.

    Provides:
    - Team management
    - Session sharing
    - Annotations and comments
    - Knowledge base
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the collaboration manager.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)
        self._ensure_tables()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                url = get_database_url()
                conn = psycopg2.connect(url)
                conn.cursor_factory = RealDictCursor
                return conn
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for PostgreSQL. "
                    "Install it with: pip install psycopg2-binary"
                )
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ensure_tables(self) -> None:
        """Ensure required tables exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
        id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # Create teams table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS teams (
                id {id_type},
                team_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                owner_id INTEGER,
                settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create team_members table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS team_members (
                id {id_type},
                team_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                role TEXT DEFAULT 'member',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(team_id, user_id)
            )
        ''')

        # Create shared_sessions table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS shared_sessions (
                id {id_type},
                share_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                shared_by INTEGER,
                shared_by_name TEXT,
                permission TEXT DEFAULT 'view',
                share_type TEXT DEFAULT 'user',
                target_id INTEGER,
                target_name TEXT,
                expires_at TIMESTAMP,
                allow_comments INTEGER DEFAULT 1,
                allow_copy INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP
            )
        ''')

        # Create annotations table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS annotations (
                id {id_type},
                annotation_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                message_id TEXT,
                user_id INTEGER,
                username TEXT,
                content TEXT,
                annotation_type TEXT DEFAULT 'comment',
                position TEXT,
                parent_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create knowledge_base table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id {id_type},
                entry_id TEXT NOT NULL UNIQUE,
                team_id TEXT,
                title TEXT NOT NULL,
                content TEXT,
                category TEXT DEFAULT 'general',
                tags TEXT,
                author_id INTEGER,
                author_name TEXT,
                is_published INTEGER DEFAULT 0,
                view_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shared_sessions_session ON shared_sessions(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shared_sessions_target ON shared_sessions(target_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_team ON knowledge_base(team_id)')

        conn.commit()
        conn.close()

    # ==================== Team Management ====================

    def create_team(
        self,
        name: str,
        owner_id: int,
        description: str = '',
        settings: Optional[Dict[str, Any]] = None
    ) -> Team:
        """
        Create a new team.

        Args:
            name: Team name.
            owner_id: Owner user ID.
            description: Optional description.
            settings: Optional team settings.

        Returns:
            Team: The created team.
        """
        team_id = str(uuid.uuid4())
        now = datetime.utcnow()

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO teams (team_id, name, description, owner_id, settings, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (team_id, name, description, owner_id, json.dumps(settings or {}), now.isoformat(), now.isoformat()))

        team_db_id = cursor.lastrowid

        # Add owner as a member
        cursor.execute('''
            INSERT INTO team_members (team_id, user_id, role, joined_at)
            VALUES (?, ?, ?, ?)
        ''', (team_id, owner_id, TeamRole.OWNER.value, now.isoformat()))

        conn.commit()
        conn.close()

        logger.info(f"Created team: {name} (ID: {team_id})")

        return Team(
            id=team_db_id,
            team_id=team_id,
            name=name,
            description=description,
            owner_id=owner_id,
            members=[TeamMember(user_id=owner_id, username='', role=TeamRole.OWNER.value, joined_at=now)],
            settings=settings or {},
            created_at=now,
            updated_at=now
        )

    def get_team(self, team_id: str) -> Optional[Team]:
        """
        Get a team by ID.

        Args:
            team_id: Team ID.

        Returns:
            Team or None if not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM teams WHERE team_id = ?', (team_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        # Get members
        cursor.execute('SELECT * FROM team_members WHERE team_id = ?', (team_id,))
        member_rows = cursor.fetchall()
        conn.close()

        members = [
            TeamMember(
                user_id=m['user_id'],
                username=m['username'] or '',
                role=m['role'],
                joined_at=datetime.fromisoformat(m['joined_at']) if m['joined_at'] else None
            )
            for m in member_rows
        ]

        return Team(
            id=row['id'],
            team_id=row['team_id'],
            name=row['name'],
            description=row['description'] or '',
            owner_id=row['owner_id'],
            members=members,
            settings=json.loads(row['settings']) if row['settings'] else {},
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
        )

    def add_team_member(
        self,
        team_id: str,
        user_id: int,
        username: str = '',
        role: str = TeamRole.MEMBER.value
    ) -> bool:
        """
        Add a member to a team.

        Args:
            team_id: Team ID.
            user_id: User ID to add.
            username: Optional username.
            role: Member role.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO team_members (team_id, user_id, username, role, joined_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (team_id, user_id, username, role, datetime.utcnow().isoformat()))

            conn.commit()
            conn.close()
            logger.info(f"Added member {user_id} to team {team_id}")
            return True
        except sqlite3.IntegrityError:
            conn.close()
            return False

    def remove_team_member(self, team_id: str, user_id: int) -> bool:
        """
        Remove a member from a team.

        Args:
            team_id: Team ID.
            user_id: User ID to remove.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM team_members WHERE team_id = ? AND user_id = ?', (team_id, user_id))
        success = cursor.rowcount > 0

        conn.commit()
        conn.close()

        if success:
            logger.info(f"Removed member {user_id} from team {team_id}")
        return success

    def list_user_teams(self, user_id: int) -> List[Team]:
        """
        List all teams a user belongs to.

        Args:
            user_id: User ID.

        Returns:
            List of Team objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT t.* FROM teams t
            JOIN team_members tm ON t.team_id = tm.team_id
            WHERE tm.user_id = ?
        ''', (user_id,))

        rows = cursor.fetchall()
        conn.close()

        return [self.get_team(row['team_id']) for row in rows]

    # ==================== Session Sharing ====================

    def share_session(
        self,
        session_id: str,
        shared_by: int,
        shared_by_name: str,
        permission: str = SharePermission.VIEW.value,
        share_type: str = 'user',
        target_id: Optional[int] = None,
        target_name: str = '',
        expires_in_hours: Optional[int] = None,
        allow_comments: bool = True,
        allow_copy: bool = True
    ) -> SharedSession:
        """
        Share a session with a user or team.

        Args:
            session_id: Session ID to share.
            shared_by: User ID sharing the session.
            shared_by_name: Name of user sharing.
            permission: Permission level.
            share_type: 'user', 'team', or 'public'.
            target_id: Target user or team ID.
            target_name: Target name.
            expires_in_hours: Optional expiration in hours.
            allow_comments: Allow comments.
            allow_copy: Allow copying.

        Returns:
            SharedSession: The created share.
        """
        share_id = str(uuid.uuid4())
        now = datetime.utcnow()

        expires_at = None
        if expires_in_hours:
            expires_at = now + timedelta(hours=expires_in_hours)

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO shared_sessions
            (share_id, session_id, shared_by, shared_by_name, permission, share_type,
             target_id, target_name, expires_at, allow_comments, allow_copy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            share_id, session_id, shared_by, shared_by_name, permission, share_type,
            target_id, target_name, expires_at.isoformat() if expires_at else None,
            1 if allow_comments else 0, 1 if allow_copy else 0, now.isoformat()
        ))

        conn.commit()
        conn.close()

        logger.info(f"Shared session {session_id} with {share_type} {target_id or 'public'}")

        return SharedSession(
            share_id=share_id,
            session_id=session_id,
            shared_by=shared_by,
            shared_by_name=shared_by_name,
            permission=permission,
            share_type=share_type,
            target_id=target_id,
            target_name=target_name,
            expires_at=expires_at,
            allow_comments=allow_comments,
            allow_copy=allow_copy,
            created_at=now
        )

    def get_share(self, share_id: str) -> Optional[SharedSession]:
        """
        Get a share by ID.

        Args:
            share_id: Share ID.

        Returns:
            SharedSession or None.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM shared_sessions WHERE share_id = ?', (share_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_shared_session(row)
        return None

    def get_session_shares(self, session_id: str) -> List[SharedSession]:
        """
        Get all shares for a session.

        Args:
            session_id: Session ID.

        Returns:
            List of SharedSession objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM shared_sessions WHERE session_id = ?', (session_id,))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_shared_session(row) for row in rows]

    def get_user_shared_sessions(self, user_id: int) -> List[SharedSession]:
        """
        Get sessions shared with a user.

        Args:
            user_id: User ID.

        Returns:
            List of SharedSession objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get shares directly to user or to user's teams
        cursor.execute('''
            SELECT DISTINCT ss.* FROM shared_sessions ss
            LEFT JOIN team_members tm ON ss.share_type = 'team' AND ss.target_id = tm.team_id
            WHERE (ss.share_type = 'user' AND ss.target_id = ?)
               OR (ss.share_type = 'team' AND tm.user_id = ?)
               OR (ss.share_type = 'public')
            ORDER BY ss.created_at DESC
        ''', (user_id, user_id))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_shared_session(row) for row in rows]

    def revoke_share(self, share_id: str, user_id: Optional[int] = None) -> bool:
        """
        Revoke a share.

        Args:
            share_id: Share ID.
            user_id: Optional user ID for authorization.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id:
            cursor.execute('DELETE FROM shared_sessions WHERE share_id = ? AND shared_by = ?',
                          (share_id, user_id))
        else:
            cursor.execute('DELETE FROM shared_sessions WHERE share_id = ?', (share_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Revoked share: {share_id}")
        return success

    def record_share_access(self, share_id: str) -> bool:
        """
        Record an access to a shared session.

        Args:
            share_id: Share ID.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()
        cursor.execute('''
            UPDATE shared_sessions
            SET access_count = access_count + 1, last_accessed = ?
            WHERE share_id = ?
        ''', (now, share_id))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    # ==================== Annotations ====================

    def add_annotation(
        self,
        session_id: str,
        user_id: int,
        username: str,
        content: str,
        message_id: Optional[str] = None,
        annotation_type: str = 'comment',
        position: Optional[Dict[str, Any]] = None,
        parent_id: Optional[int] = None
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
            position: Optional position info.
            parent_id: Optional parent annotation ID.

        Returns:
            Annotation: The created annotation.
        """
        annotation_id = str(uuid.uuid4())
        now = datetime.utcnow()

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO annotations
            (annotation_id, session_id, message_id, user_id, username, content,
             annotation_type, position, parent_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            annotation_id, session_id, message_id, user_id, username, content,
            annotation_type, json.dumps(position or {}), parent_id,
            now.isoformat(), now.isoformat()
        ))

        annotation_db_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Added annotation to session {session_id}")

        return Annotation(
            id=annotation_db_id,
            annotation_id=annotation_id,
            session_id=session_id,
            message_id=message_id,
            user_id=user_id,
            username=username,
            content=content,
            annotation_type=annotation_type,
            position=position or {},
            parent_id=parent_id,
            created_at=now,
            updated_at=now
        )

    def get_session_annotations(self, session_id: str) -> List[Annotation]:
        """
        Get all annotations for a session.

        Args:
            session_id: Session ID.

        Returns:
            List of Annotation objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM annotations
            WHERE session_id = ?
            ORDER BY created_at ASC
        ''', (session_id,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_annotation(row) for row in rows]

    def delete_annotation(self, annotation_id: str, user_id: Optional[int] = None) -> bool:
        """
        Delete an annotation.

        Args:
            annotation_id: Annotation ID.
            user_id: Optional user ID for authorization.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id:
            cursor.execute('DELETE FROM annotations WHERE annotation_id = ? AND user_id = ?',
                          (annotation_id, user_id))
        else:
            cursor.execute('DELETE FROM annotations WHERE annotation_id = ?', (annotation_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    # ==================== Knowledge Base ====================

    def create_knowledge_entry(
        self,
        title: str,
        content: str,
        author_id: int,
        author_name: str,
        team_id: Optional[str] = None,
        category: str = 'general',
        tags: Optional[List[str]] = None,
        is_published: bool = False
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
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow()

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO knowledge_base
            (entry_id, team_id, title, content, category, tags, author_id, author_name,
             is_published, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entry_id, team_id, title, content, category, json.dumps(tags or []),
            author_id, author_name, 1 if is_published else 0,
            now.isoformat(), now.isoformat()
        ))

        entry_db_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Created knowledge entry: {title}")

        return KnowledgeEntry(
            id=entry_db_id,
            entry_id=entry_id,
            team_id=team_id,
            title=title,
            content=content,
            category=category,
            tags=tags or [],
            author_id=author_id,
            author_name=author_name,
            is_published=is_published,
            created_at=now,
            updated_at=now
        )

    def get_knowledge_entry(self, entry_id: str) -> Optional[KnowledgeEntry]:
        """
        Get a knowledge entry by ID.

        Args:
            entry_id: Entry ID.

        Returns:
            KnowledgeEntry or None.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM knowledge_base WHERE entry_id = ?', (entry_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_knowledge_entry(row)
        return None

    def list_knowledge_entries(
        self,
        team_id: Optional[str] = None,
        category: Optional[str] = None,
        published_only: bool = True,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        List knowledge entries.

        Args:
            team_id: Optional team filter.
            category: Optional category filter.
            published_only: Only show published entries.
            page: Page number.
            limit: Results per page.

        Returns:
            Dict with entries, total, page, limit, total_pages.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = []
        params = []

        if team_id:
            conditions.append('team_id = ?')
            params.append(team_id)

        if category:
            conditions.append('category = ?')
            params.append(category)

        if published_only:
            conditions.append('is_published = 1')

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        # Get total count
        cursor.execute(f'SELECT COUNT(*) as count FROM knowledge_base WHERE {where_clause}', params)
        total = cursor.fetchone()['count']
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        # Get paginated results
        offset = (page - 1) * limit
        from app.repositories.database import adapt_sql
        cursor.execute(adapt_sql(f'''
            SELECT * FROM knowledge_base
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        '''), params + [limit, offset])

        rows = cursor.fetchall()
        conn.close()

        entries = [self._row_to_knowledge_entry(row) for row in rows]

        return {
            'entries': entries,
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': total_pages
        }

    def _row_to_shared_session(self, row: sqlite3.Row) -> SharedSession:
        """Convert row to SharedSession."""
        return SharedSession(
            id=row['id'],
            share_id=row['share_id'],
            session_id=row['session_id'],
            shared_by=row['shared_by'],
            shared_by_name=row['shared_by_name'] or '',
            permission=row['permission'] or SharePermission.VIEW.value,
            share_type=row['share_type'] or 'user',
            target_id=row['target_id'],
            target_name=row['target_name'] or '',
            expires_at=datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None,
            allow_comments=bool(row['allow_comments']),
            allow_copy=bool(row['allow_copy']),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            access_count=row['access_count'] or 0,
            last_accessed=datetime.fromisoformat(row['last_accessed']) if row['last_accessed'] else None,
        )

    def _row_to_annotation(self, row: sqlite3.Row) -> Annotation:
        """Convert row to Annotation."""
        return Annotation(
            id=row['id'],
            annotation_id=row['annotation_id'],
            session_id=row['session_id'],
            message_id=row['message_id'],
            user_id=row['user_id'],
            username=row['username'] or '',
            content=row['content'] or '',
            annotation_type=row['annotation_type'] or 'comment',
            position=json.loads(row['position']) if row['position'] else {},
            parent_id=row['parent_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
        )

    def _row_to_knowledge_entry(self, row: sqlite3.Row) -> KnowledgeEntry:
        """Convert row to KnowledgeEntry."""
        return KnowledgeEntry(
            id=row['id'],
            entry_id=row['entry_id'],
            team_id=row['team_id'],
            title=row['title'] or '',
            content=row['content'] or '',
            category=row['category'] or 'general',
            tags=json.loads(row['tags']) if row['tags'] else [],
            author_id=row['author_id'],
            author_name=row['author_name'] or '',
            is_published=bool(row['is_published']),
            view_count=row['view_count'] or 0,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
        )
