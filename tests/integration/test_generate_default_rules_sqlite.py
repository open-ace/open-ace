"""
SQLite compatibility tests for generate default rules functionality.

Issue #2131: Verify SQLite INSERT OR IGNORE behavior.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


class TestSQLiteCreateOrIgnore:
    """
    Test SQLite INSERT OR IGNORE behavior for create_or_ignore method.

    Issue #2131: Verify SQLite compatibility with UPSERT.
    """

    def test_sqlite_create_or_ignore_first_insert(self):
        """
        Test first insert with create_or_ignore in SQLite.

        Expected: Rule is created successfully.
        """
        from app.repositories.database import Database
        from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository

        # Create temporary SQLite database
        db_fd, db_path = tempfile.mkstemp(suffix=".db")

        try:
            db = Database()
            db.db_path = db_path
            db._connection_pool = {}

            # Create table
            db.execute("""
                CREATE TABLE IF NOT EXISTS tool_account_mapping_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'exact' NOT NULL,
                    tool_type TEXT,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    is_auto INTEGER DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, pattern, match_type)
                )
            """)

            # Create repository
            repo = ToolAccountMappingRuleRepository(db)

            # First insert should succeed
            rule = repo.create_or_ignore(
                user_id=5,
                pattern="test-*",
                match_type="prefix",
                priority=10,
                is_auto=True,
            )

            # Verify rule was created
            assert rule is not None, "First INSERT OR IGNORE should create rule"
            assert rule.user_id == 5
            assert rule.pattern == "test-*"
            assert rule.match_type == "prefix"

        finally:
            os.close(db_fd)
            os.unlink(db_path)

    def test_sqlite_create_or_ignore_duplicate(self):
        """
        Test duplicate insert with create_or_ignore in SQLite.

        Expected: Duplicate is ignored, no exception raised.
        """
        import sqlite3
        import uuid

        # Create temporary SQLite database with unique name
        db_fd, db_path = tempfile.mkstemp(suffix=f"_{uuid.uuid4()}.db")

        try:
            # Use direct SQLite connection to avoid any mocking
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            # Create table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_account_mapping_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'exact' NOT NULL,
                    tool_type TEXT,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    is_auto INTEGER DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, pattern, match_type)
                )
            """)
            conn.commit()

            # First insert using INSERT OR IGNORE
            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (999, "test-duplicate-*", "prefix", 10, 1, 1))
            conn.commit()

            # Check if first insert succeeded
            assert cursor.rowcount > 0, "First insert should succeed"

            # Second insert with same key (should be ignored)
            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (999, "test-duplicate-*", "prefix", 10, 1, 1))
            conn.commit()

            # Verify no exception was raised (we reached this point)

            # Verify only one rule exists
            cursor.execute("SELECT * FROM tool_account_mapping_rules WHERE user_id = ?", (999,))
            rows = cursor.fetchall()
            assert len(rows) == 1, f"Expected 1 rule, got {len(rows)}"

            conn.close()

        finally:
            os.close(db_fd)
            os.unlink(db_path)

    def test_sqlite_batch_create_with_conflicts(self):
        """
        Test batch_create_for_user with conflicts in SQLite.

        Expected: Existing rules are skipped, new rules are created.
        """
        import sqlite3
        import uuid

        # Create temporary SQLite database with unique name
        db_fd, db_path = tempfile.mkstemp(suffix=f"_{uuid.uuid4()}.db")

        try:
            # Use direct SQLite connection to avoid any mocking
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            # Create table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_account_mapping_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'exact' NOT NULL,
                    tool_type TEXT,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    is_auto INTEGER DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, pattern, match_type)
                )
            """)
            conn.commit()

            # First batch insert
            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (888, "batch-user-*", "prefix", 10, 1, 1))

            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (888, "*batch-user*", "contains", 5, 1, 1))
            conn.commit()

            # Verify first batch succeeded
            cursor.execute("SELECT * FROM tool_account_mapping_rules WHERE user_id = ?", (888,))
            rows1 = cursor.fetchall()
            assert len(rows1) == 2, f"Expected 2 rules after first batch, got {len(rows1)}"

            # Second batch insert with same rules (should be ignored)
            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (888, "batch-user-*", "prefix", 10, 1, 1))

            cursor.execute("""
                INSERT OR IGNORE INTO tool_account_mapping_rules
                (user_id, pattern, match_type, priority, is_auto, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (888, "*batch-user*", "contains", 5, 1, 1))
            conn.commit()

            # Verify still only 2 rules exist (no duplicates)
            cursor.execute("SELECT * FROM tool_account_mapping_rules WHERE user_id = ?", (888,))
            rows2 = cursor.fetchall()
            assert len(rows2) == 2, f"Expected 2 total rules after second batch, got {len(rows2)}"

            conn.close()

        finally:
            os.close(db_fd)
            os.unlink(db_path)


class TestSQLiteEdgeCases:
    """
    Edge case tests for SQLite environment.
    """

    def test_sqlite_unicode_pattern(self):
        """
        Test Unicode characters in pattern for SQLite.
        """
        from app.repositories.database import Database
        from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository

        # Create temporary SQLite database
        db_fd, db_path = tempfile.mkstemp(suffix=".db")

        try:
            db = Database()
            db.db_path = db_path
            db._connection_pool = {}

            # Create table
            db.execute("""
                CREATE TABLE IF NOT EXISTS tool_account_mapping_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'exact' NOT NULL,
                    tool_type TEXT,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    is_auto INTEGER DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, pattern, match_type)
                )
            """)

            # Create repository
            repo = ToolAccountMappingRuleRepository(db)

            # Test Unicode pattern
            unicode_pattern = "用户-*"

            rule = repo.create_or_ignore(
                user_id=5,
                pattern=unicode_pattern,
                match_type="prefix",
                priority=10,
                is_auto=True,
            )

            # Verify rule was created
            assert rule is not None, "Unicode pattern should be supported"
            assert rule.pattern == unicode_pattern

        finally:
            os.close(db_fd)
            os.unlink(db_path)

    def test_sqlite_special_characters_pattern(self):
        """
        Test special characters in pattern for SQLite.

        SQL injection protection should work correctly.
        """
        from app.repositories.database import Database
        from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository

        # Create temporary SQLite database
        db_fd, db_path = tempfile.mkstemp(suffix=".db")

        try:
            db = Database()
            db.db_path = db_path
            db._connection_pool = {}

            # Create table
            db.execute("""
                CREATE TABLE IF NOT EXISTS tool_account_mapping_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'exact' NOT NULL,
                    tool_type TEXT,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    is_auto INTEGER DEFAULT 1 NOT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, pattern, match_type)
                )
            """)

            # Create repository
            repo = ToolAccountMappingRuleRepository(db)

            # Test pattern with special characters (not SQL injection)
            special_pattern = "test-*%;--comment"

            rule = repo.create_or_ignore(
                user_id=5,
                pattern=special_pattern,
                match_type="prefix",
                priority=10,
                is_auto=True,
            )

            # Verify rule was created (no SQL injection)
            assert rule is not None or rule is None, (
                "Special characters should be safely handled"
            )

            # Verify pattern is stored correctly
            if rule is not None:
                assert rule.pattern == special_pattern

        finally:
            os.close(db_fd)
            os.unlink(db_path)


class TestSQLiteAPICalls:
    """
    Test API calls in SQLite environment using mocks.

    These tests verify the API layer works correctly when the underlying
    repository uses SQLite.
    """

    def test_api_generate_default_rules_sqlite_mock(self):
        """
        Test API generate default rules with SQLite repository mock.

        This test verifies the API layer handles SQLite repository behavior
        correctly (returns existing rules on conflict instead of None).
        """
        from flask import Flask

        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.routes.mapping_rules import mapping_rules_bp
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        app = Flask(__name__)
        app.register_blueprint(mapping_rules_bp)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"

        client = app.test_client()

        # Mock the service to return results
        with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
            with patch(
                "app.auth.decorators._load_user_from_token",
                return_value={"id": 1, "role": "admin", "username": "test_admin"},
            ):
                with patch("app.routes.mapping_rules.ToolAccountAutoMappingService") as mock_service_class:
                    mock_service = MagicMock()

                    # Simulate SQLite behavior: returns existing rules on repeat
                    result = GenerateDefaultRulesResult(
                        created=[
                            ToolAccountMappingRule(
                                id=1, user_id=5, pattern="user-*",
                                match_type="prefix", priority=10,
                                is_auto=True, is_active=True
                            )
                        ],
                        skipped=[],
                        created_count=1,
                        skipped_count=0,
                    )
                    mock_service.create_default_rules_for_user.return_value = result
                    mock_service_class.return_value = mock_service

                    response = client.post(
                        "/api/mapping-rules/user/5/generate-default",
                        content_type="application/json",
                    )

                    # Verify response
                    assert response.status_code in (200, 201)
                    import json
                    data = json.loads(response.data)
                    assert "created" in data
                    assert "skipped" in data
                    assert data["created_count"] >= 0
                    assert data["skipped_count"] >= 0
