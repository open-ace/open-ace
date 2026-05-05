"""
Open ACE - Prompt Library Module

Provides prompt template management for AI interactions.
Users can create, organize, and share prompt templates.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union

from app.repositories.database import (
    DB_PATH,
    adapt_boolean_condition,
    adapt_sql,
    get_database_url,
    is_postgresql,
)

logger = logging.getLogger(__name__)


class PromptCategory(Enum):
    """Prompt template categories."""

    GENERAL = "general"
    CODING = "coding"
    WRITING = "writing"
    ANALYSIS = "analysis"
    TRANSLATION = "translation"
    SUMMARIZATION = "summarization"
    CUSTOM = "custom"


@dataclass
class PromptTemplate:
    """Prompt template data model."""

    id: Optional[int] = None
    name: str = ""
    description: str = ""
    category: str = PromptCategory.GENERAL.value
    content: str = ""
    variables: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    author_id: Optional[int] = None
    author_name: str = ""
    is_public: bool = False
    is_featured: bool = False
    use_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "content": self.content,
            "variables": self.variables,
            "tags": self.tags,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "is_public": self.is_public,
            "is_featured": self.is_featured,
            "use_count": self.use_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptTemplate":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", PromptCategory.GENERAL.value),
            content=data.get("content", ""),
            variables=data.get("variables", []),
            tags=data.get("tags", []),
            author_id=data.get("author_id"),
            author_name=data.get("author_name", ""),
            is_public=data.get("is_public", False),
            is_featured=data.get("is_featured", False),
            use_count=data.get("use_count", 0),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
        )

    def render(self, **kwargs) -> str:
        """Render the prompt template with provided variables."""
        result = self.content
        for var in self.variables:
            var_name = var.get("name", "")
            var_default = var.get("default", "")
            value = kwargs.get(var_name, var_default)
            result = result.replace(f"{{{var_name}}}", str(value))
        return result

    def validate_variables(self, **kwargs) -> list[str]:
        """Validate that all required variables are provided."""
        missing = []
        for var in self.variables:
            var_name = var.get("name", "")
            var_required = var.get("required", False)
            if var_required and var_name not in kwargs:
                missing.append(var_name)
        return missing


class PromptLibrary:
    """Prompt template library manager."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the prompt library.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)

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
                ) from None
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
        bool_false = "BOOLEAN DEFAULT FALSE" if is_postgresql() else "INTEGER DEFAULT 0"

        # Create prompt_templates table
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id {id_type},
                name TEXT NOT NULL,
                description TEXT,
                category TEXT DEFAULT 'general',
                content TEXT NOT NULL,
                variables TEXT,
                tags TEXT,
                author_id INTEGER,
                author_name TEXT,
                is_public {bool_false},
                is_featured {bool_false},
                use_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompt_templates_category
            ON prompt_templates(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompt_templates_author
            ON prompt_templates(author_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompt_templates_public
            ON prompt_templates(is_public)
        """)

        conn.commit()
        conn.close()

    def create_template(self, template: PromptTemplate) -> int:
        """
        Create a new prompt template.

        Args:
            template: PromptTemplate object to create.

        Returns:
            int: ID of the created template.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        if is_postgresql():
            # PostgreSQL: use RETURNING clause
            cursor.execute(
                """
                INSERT INTO prompt_templates
                (name, description, category, content, variables, tags, author_id,
                 author_name, is_public, is_featured, use_count, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,
                (
                    template.name,
                    template.description,
                    template.category,
                    template.content,
                    json.dumps(template.variables),
                    json.dumps(template.tags),
                    template.author_id,
                    template.author_name,
                    template.is_public,
                    template.is_featured,
                    template.use_count,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
            template_id = row["id"] if row else None
        else:
            # SQLite: use lastrowid
            cursor.execute(
                """
                INSERT INTO prompt_templates
                (name, description, category, content, variables, tags, author_id,
                 author_name, is_public, is_featured, use_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    template.name,
                    template.description,
                    template.category,
                    template.content,
                    json.dumps(template.variables),
                    json.dumps(template.tags),
                    template.author_id,
                    template.author_name,
                    template.is_public,
                    template.is_featured,
                    template.use_count,
                    now,
                    now,
                ),
            )
            template_id = cursor.lastrowid

        conn.commit()
        conn.close()

        logger.info(f"Created prompt template: {template.name} (ID: {template_id})")
        return int(str(template_id or 0))

    def get_template(self, template_id: int) -> Optional[PromptTemplate]:
        """
        Get a prompt template by ID.

        Args:
            template_id: ID of the template to retrieve.

        Returns:
            PromptTemplate or None if not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(adapt_sql("SELECT * FROM prompt_templates WHERE id = ?"), (template_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_template(row)
        return None

    def update_template(self, template: PromptTemplate) -> bool:
        """
        Update an existing prompt template.

        Args:
            template: PromptTemplate object with updated data.

        Returns:
            bool: True if update was successful.
        """
        if template.id is None:
            return False

        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()
        cursor.execute(
            adapt_sql("""
            UPDATE prompt_templates
            SET name = ?, description = ?, category = ?, content = ?,
                variables = ?, tags = ?, is_public = ?, is_featured = ?,
                updated_at = ?
            WHERE id = ?
        """),
            (
                template.name,
                template.description,
                template.category,
                template.content,
                json.dumps(template.variables),
                json.dumps(template.tags),
                template.is_public,
                template.is_featured,
                now,
                template.id,
            ),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Updated prompt template: {template.name} (ID: {template.id})")
        return success

    def delete_template(self, template_id: int, user_id: Optional[int] = None) -> bool:
        """
        Delete a prompt template.

        Args:
            template_id: ID of the template to delete.
            user_id: Optional user ID for authorization check.

        Returns:
            bool: True if deletion was successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id is not None:
            cursor.execute(
                adapt_sql("DELETE FROM prompt_templates WHERE id = ? AND author_id = ?"),
                (template_id, user_id),
            )
        else:
            cursor.execute(adapt_sql("DELETE FROM prompt_templates WHERE id = ?"), (template_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Deleted prompt template ID: {template_id}")
        return success

    def list_templates(
        self,
        category: Optional[str] = None,
        user_id: Optional[int] = None,
        include_public: bool = True,
        search: Optional[str] = None,
        tags: Optional[list[str]] = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        List prompt templates with filters.

        Args:
            category: Filter by category.
            user_id: Filter by author user ID.
            include_public: Include public templates.
            search: Search term for name/description.
            tags: Filter by tags.
            page: Page number (1-indexed).
            limit: Number of results per page.

        Returns:
            Dict with templates, total, page, limit, total_pages.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Build query conditions
        conditions = []
        params: list[Any] = []

        if user_id is not None and include_public:
            conditions.append(f"(author_id = ? OR {adapt_boolean_condition('is_public', True)})")
            params.append(user_id)
        elif user_id is not None:
            conditions.append("author_id = ?")
            params.append(user_id)
        elif include_public:
            conditions.append(adapt_boolean_condition("is_public", True))

        if category:
            conditions.append("category = ?")
            params.append(category)

        if search:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        cursor.execute(
            adapt_sql(f"SELECT COUNT(*) as count FROM prompt_templates WHERE {where_clause}"),
            params,
        )
        total = cursor.fetchone()["count"]
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        # Get paginated results
        offset = (page - 1) * limit
        cursor.execute(
            adapt_sql(f"""
            SELECT * FROM prompt_templates
            WHERE {where_clause}
            ORDER BY is_featured DESC, use_count DESC, created_at DESC
            LIMIT ? OFFSET ?
        """),
            params + [limit, offset],
        )

        rows = cursor.fetchall()
        conn.close()

        templates = [self._row_to_template(row) for row in rows]

        return {
            "templates": templates,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }

    def increment_use_count(self, template_id: int) -> bool:
        """
        Increment the use count for a template.

        Args:
            template_id: ID of the template.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql("""
            UPDATE prompt_templates
            SET use_count = use_count + 1
            WHERE id = ?
        """),
            (template_id,),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def get_featured_templates(self, limit: int = 10) -> list[PromptTemplate]:
        """
        Get featured templates.

        Args:
            limit: Maximum number of templates to return.

        Returns:
            List of featured PromptTemplate objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(f"""
            SELECT * FROM prompt_templates
            WHERE {adapt_boolean_condition('is_featured', True)} AND {adapt_boolean_condition('is_public', True)}
            ORDER BY use_count DESC
            LIMIT ?
        """),
            (limit,),
        )

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_template(row) for row in rows]

    def get_categories(self) -> list[dict[str, Any]]:
        """
        Get all categories with template counts.

        Returns:
            List of category info dictionaries.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT category, COUNT(*) as count
            FROM prompt_templates
            WHERE {adapt_boolean_condition('is_public', True)}
            GROUP BY category
            ORDER BY count DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        return [{"category": row["category"], "count": row["count"]} for row in rows]

    def get_popular_tags(self, limit: int = 20) -> list[dict[str, Any]]:
        """
        Get popular tags with usage counts.

        Args:
            limit: Maximum number of tags to return.

        Returns:
            List of tag info dictionaries.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT tags FROM prompt_templates
            WHERE {adapt_boolean_condition('is_public', True)} AND tags IS NOT NULL
        """)

        rows = cursor.fetchall()
        conn.close()

        # Count tag occurrences
        tag_counts: dict[str, int] = {}
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                for tag in tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        # Sort by count and return top tags
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"tag": tag, "count": count} for tag, count in sorted_tags]

    def _row_to_template(self, row: sqlite3.Row) -> PromptTemplate:
        """Convert a database row to PromptTemplate."""
        # Handle datetime fields - PostgreSQL returns datetime objects, SQLite returns strings
        created_at_val = row["created_at"]
        updated_at_val = row["updated_at"]

        if created_at_val:
            if isinstance(created_at_val, datetime):
                created_at = created_at_val
            elif isinstance(created_at_val, str):
                created_at = datetime.fromisoformat(created_at_val)
            else:
                created_at = None
        else:
            created_at = None

        if updated_at_val:
            if isinstance(updated_at_val, datetime):
                updated_at = updated_at_val
            elif isinstance(updated_at_val, str):
                updated_at = datetime.fromisoformat(updated_at_val)
            else:
                updated_at = None
        else:
            updated_at = None

        return PromptTemplate(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            category=row["category"] or PromptCategory.GENERAL.value,
            content=row["content"] or "",
            variables=json.loads(row["variables"]) if row["variables"] else [],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            author_id=row["author_id"],
            author_name=row["author_name"] or "",
            is_public=bool(row["is_public"]),
            is_featured=bool(row["is_featured"]),
            use_count=row["use_count"] or 0,
            created_at=created_at,
            updated_at=updated_at,
        )

    def seed_default_templates(self) -> None:
        """Seed the library with default prompt templates."""
        default_templates = [
            PromptTemplate(
                name="Code Review",
                description="Review code for best practices, bugs, and improvements",
                category=PromptCategory.CODING.value,
                content="Please review the following code and provide feedback on:\n1. Code quality and readability\n2. Potential bugs or issues\n3. Performance considerations\n4. Best practices and improvements\n\nCode:\n```\n{code}\n```",
                variables=[
                    {
                        "name": "code",
                        "description": "The code to review",
                        "required": True,
                        "default": "",
                    }
                ],
                tags=["code", "review", "quality"],
                is_public=True,
                is_featured=True,
            ),
            PromptTemplate(
                name="Summarize Text",
                description="Summarize long text into key points",
                category=PromptCategory.SUMMARIZATION.value,
                content="Please summarize the following text into key points. Be concise and capture the main ideas.\n\nText:\n{text}",
                variables=[
                    {
                        "name": "text",
                        "description": "The text to summarize",
                        "required": True,
                        "default": "",
                    }
                ],
                tags=["summarize", "text", "key-points"],
                is_public=True,
                is_featured=True,
            ),
            PromptTemplate(
                name="Translate",
                description="Translate text between languages",
                category=PromptCategory.TRANSLATION.value,
                content="Please translate the following text from {source_language} to {target_language}:\n\n{text}",
                variables=[
                    {
                        "name": "text",
                        "description": "The text to translate",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "source_language",
                        "description": "Source language",
                        "required": True,
                        "default": "English",
                    },
                    {
                        "name": "target_language",
                        "description": "Target language",
                        "required": True,
                        "default": "Chinese",
                    },
                ],
                tags=["translate", "language", "multilingual"],
                is_public=True,
                is_featured=True,
            ),
            PromptTemplate(
                name="Explain Concept",
                description="Explain a complex concept in simple terms",
                category=PromptCategory.GENERAL.value,
                content='Please explain the concept of "{concept}" in simple terms that a {audience} would understand. Include examples if helpful.',
                variables=[
                    {
                        "name": "concept",
                        "description": "The concept to explain",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "audience",
                        "description": "Target audience level",
                        "required": False,
                        "default": "beginner",
                    },
                ],
                tags=["explain", "concept", "learning"],
                is_public=True,
                is_featured=False,
            ),
            PromptTemplate(
                name="Write Documentation",
                description="Generate documentation for code or APIs",
                category=PromptCategory.WRITING.value,
                content="Please write documentation for the following {doc_type}:\n\n{content}\n\nInclude:\n- Description\n- Parameters/Arguments\n- Return values\n- Usage examples",
                variables=[
                    {
                        "name": "content",
                        "description": "The code or API to document",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "doc_type",
                        "description": "Type of documentation (function, class, API)",
                        "required": False,
                        "default": "function",
                    },
                ],
                tags=["documentation", "code", "api"],
                is_public=True,
                is_featured=False,
            ),
        ]

        for template in default_templates:
            # Check if template already exists
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("SELECT id FROM prompt_templates WHERE name = ?"), (template.name,)
            )
            exists = cursor.fetchone()
            conn.close()

            if not exists:
                self.create_template(template)
                logger.info(f"Seeded default template: {template.name}")


def get_ddl_statements() -> list[str]:
    """Return DDL statements for prompt library tables."""
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_false = "BOOLEAN DEFAULT FALSE" if is_postgresql() else "INTEGER DEFAULT 0"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id {id_type},
            name TEXT NOT NULL,
            description TEXT,
            category TEXT DEFAULT 'general',
            content TEXT NOT NULL,
            variables TEXT,
            tags TEXT,
            author_id INTEGER,
            author_name TEXT,
            is_public {bool_false},
            is_featured {bool_false},
            use_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_prompt_templates_category
        ON prompt_templates(category)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_prompt_templates_author
        ON prompt_templates(author_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_prompt_templates_public
        ON prompt_templates(is_public)
        """,
    ]


# Module-level singleton
_instance: Optional[PromptLibrary] = None


def get_prompt_library() -> PromptLibrary:
    """Get the module-level PromptLibrary singleton."""
    global _instance
    if _instance is None:
        _instance = PromptLibrary()
    return _instance
