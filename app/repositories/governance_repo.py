"""
Open ACE - Governance Repository

Repository for governance data access operations:
- Content filter rules
- Security settings
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional, cast

from app.repositories.database import CONFIG_DIR, Database

logger = logging.getLogger(__name__)

# Settings file path
SETTINGS_FILE = os.path.join(CONFIG_DIR, "governance_settings.json")


class GovernanceRepository:
    """Repository for governance data operations."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()
        # Table structure managed by Alembic migrations

    def _ensure_config_dir(self) -> None:
        """Ensure configuration directory exists."""
        os.makedirs(CONFIG_DIR, exist_ok=True)

    # =========================================================================
    # Content Filter Rules
    # =========================================================================

    def get_filter_rules(self) -> list[dict]:
        """
        Get all content filter rules.

        Returns:
            List[Dict]: List of filter rules.
        """
        query = "SELECT * FROM content_filter_rules ORDER BY created_at DESC"
        rules = self.db.fetch_all(query)

        # Convert is_enabled to boolean
        for rule in rules:
            rule["is_enabled"] = bool(rule.get("is_enabled", 1))

        return rules

    def get_filter_rule(self, rule_id: int) -> Optional[dict]:
        """
        Get a specific filter rule.

        Args:
            rule_id: Rule ID.

        Returns:
            Optional[Dict]: Rule data or None.
        """
        query = "SELECT * FROM content_filter_rules WHERE id = ?"
        rule = self.db.fetch_one(query, (rule_id,))

        if rule:
            rule["is_enabled"] = bool(rule.get("is_enabled", 1))

        return rule

    def create_filter_rule(
        self,
        pattern: str,
        rule_type: str = "keyword",
        severity: str = "medium",
        action: str = "warn",
        description: Optional[str] = None,
        is_enabled: bool = True,
    ) -> Optional[int]:
        """
        Create a new filter rule.

        Args:
            pattern: Pattern to match.
            rule_type: Type of pattern (keyword, regex, pii).
            severity: Severity level (low, medium, high).
            action: Action to take (warn, block, redact).
            description: Optional description.
            is_enabled: Whether rule is enabled.

        Returns:
            Optional[int]: Rule ID if successful.
        """
        try:
            from app.repositories.database import is_postgresql

            # Use RETURNING for PostgreSQL
            if is_postgresql():
                result = self.db.fetch_one(
                    """
                    INSERT INTO content_filter_rules
                    (pattern, type, severity, action, is_enabled, description, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """,
                    (
                        pattern,
                        rule_type,
                        severity,
                        action,
                        is_enabled,
                        description,
                        datetime.utcnow().isoformat(),
                    ),
                    commit=True,
                )
                return result["id"] if result else None
            else:
                # SQLite uses 1/0 for boolean, PostgreSQL uses TRUE/FALSE
                is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
                cursor = self.db.execute(
                    """
                    INSERT INTO content_filter_rules
                    (pattern, type, severity, action, is_enabled, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        pattern,
                        rule_type,
                        severity,
                        action,
                        is_enabled_val,
                        description,
                        datetime.utcnow().isoformat(),
                    ),
                )
                return cast("Optional[int]", cursor.lastrowid)
        except Exception as e:
            logger.error(f"Error creating filter rule: {e}")
            return None

    def update_filter_rule(
        self,
        rule_id: int,
        pattern: Optional[str] = None,
        rule_type: Optional[str] = None,
        severity: Optional[str] = None,
        action: Optional[str] = None,
        description: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> bool:
        """
        Update a filter rule.

        Args:
            rule_id: Rule ID.
            pattern: New pattern.
            rule_type: New type.
            severity: New severity.
            action: New action.
            description: New description.
            is_enabled: New enabled status.

        Returns:
            bool: True if successful.
        """
        updates = []
        params: list[Any] = []

        if pattern is not None:
            updates.append("pattern = ?")
            params.append(pattern)
        if rule_type is not None:
            updates.append("type = ?")
            params.append(rule_type)
        if severity is not None:
            updates.append("severity = ?")
            params.append(severity)
        if action is not None:
            updates.append("action = ?")
            params.append(action)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if is_enabled is not None:
            updates.append("is_enabled = ?")
            # PostgreSQL uses TRUE/FALSE, SQLite uses 1/0
            is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
            params.append(is_enabled_val)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(rule_id)

        query = f"UPDATE content_filter_rules SET {', '.join(updates)} WHERE id = ?"

        try:
            cursor = self.db.execute(query, tuple(params))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Error updating filter rule: {e}")
            return False

    def delete_filter_rule(self, rule_id: int) -> bool:
        """
        Delete a filter rule.

        Args:
            rule_id: Rule ID.

        Returns:
            bool: True if successful.
        """
        query = "DELETE FROM content_filter_rules WHERE id = ?"

        try:
            cursor = self.db.execute(query, (rule_id,))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Error deleting filter rule: {e}")
            return False

    # =========================================================================
    # Security Settings
    # =========================================================================

    def get_security_settings(self) -> dict[str, Any]:
        """
        Get security settings from database.

        Returns:
            Dict: Security settings.
        """
        default_settings = {
            "session_timeout": 30,
            "max_login_attempts": 5,
            "password_min_length": 8,
            "password_require_uppercase": True,
            "password_require_lowercase": True,
            "password_require_number": True,
            "password_require_special": False,
            "two_factor_enabled": False,
            "ip_whitelist": [],
        }

        try:
            # Try to load from database first
            rows = self.db.fetch_all("SELECT setting_key, setting_value FROM security_settings")

            if rows:
                for row in rows:
                    key = row["setting_key"]
                    value = row["setting_value"]

                    # Parse value based on key
                    if key == "ip_whitelist":
                        default_settings[key] = json.loads(value) if value else []
                    elif value.lower() in ("true", "false"):
                        default_settings[key] = value.lower() == "true"
                    elif value.isdigit():
                        default_settings[key] = int(value)
                    else:
                        default_settings[key] = value

                return default_settings

        except Exception as e:
            logger.debug(f"Security settings table not available, using defaults: {e}")

        # Fallback to file-based settings for backward compatibility
        try:
            self._ensure_config_dir()
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE) as f:
                    saved_settings = json.load(f)
                    default_settings.update(saved_settings)
        except Exception as e:
            logger.error(f"Error loading security settings: {e}")

        return default_settings

    def update_security_settings(self, settings: dict[str, Any]) -> bool:
        """
        Update security settings in database.

        Args:
            settings: New settings to save.

        Returns:
            bool: True if successful.
        """
        try:
            # Try to save to database first
            from app.repositories.database import adapt_sql

            with self.db.connection() as conn:
                cursor = conn.cursor()

                for key, value in settings.items():
                    # Convert value to string for storage
                    if isinstance(value, bool):
                        str_value = "true" if value else "false"
                    elif isinstance(value, (list, dict)):
                        str_value = json.dumps(value)
                    else:
                        str_value = str(value)

                    cursor.execute(
                        adapt_sql("""
                        INSERT INTO security_settings (setting_key, setting_value, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(setting_key) DO UPDATE SET
                            setting_value = excluded.setting_value,
                            updated_at = CURRENT_TIMESTAMP
                    """),
                        (key, str_value),
                    )

                conn.commit()

            return True

        except Exception as e:
            logger.debug(f"Security settings table not available, falling back to file: {e}")

            # Fallback to file-based storage
            try:
                self._ensure_config_dir()
                current_settings = self.get_security_settings()
                current_settings.update(settings)

                with open(SETTINGS_FILE, "w") as f:
                    json.dump(current_settings, f, indent=2)

                return True
            except Exception as e2:
                logger.error(f"Error saving security settings: {e2}")
                return False
