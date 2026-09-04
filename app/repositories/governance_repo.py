"""
Open ACE - Governance Repository

Repository for governance data access operations:
- Content filter rules
- Security settings
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, cast

from app.repositories.database import CONFIG_DIR, Database

logger = logging.getLogger(__name__)

# Settings file path
SETTINGS_FILE = os.path.join(CONFIG_DIR, "governance_settings.json")


class GovernanceRepository:
    """Repository for governance data operations."""

    def __init__(self, db: Database | None = None):
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

    def get_filter_rule(self, rule_id: int) -> dict | None:
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

    def get_filter_rule_by_pattern(self, pattern: str) -> dict | None:
        """
        Get a filter rule by pattern.

        Args:
            pattern: Pattern to search for.

        Returns:
            Optional[Dict]: Rule data or None.
        """
        query = "SELECT * FROM content_filter_rules WHERE pattern = ?"
        rule = self.db.fetch_one(query, (pattern,))

        if rule:
            rule["is_enabled"] = bool(rule.get("is_enabled", 1))

        return rule

    def get_filter_rules_paginated(
        self,
        limit: int = 100,
        offset: int = 0,
        rule_type: str | None = None,
        severity: str | None = None,
        is_enabled: bool | None = None,
    ) -> tuple[list[dict], int]:
        """
        Get filter rules with pagination and filtering.

        Args:
            limit: Maximum number of records to return (default 100, max 1000).
            offset: Number of records to skip (default 0).
            rule_type: Optional filter by type (keyword, regex, pii).
            severity: Optional filter by severity (low, medium, high).
            is_enabled: Optional filter by enabled status.

        Returns:
            Tuple[List[Dict], int]: (list of rules, total count).
        """
        from app.repositories.database import adapt_sql

        # Clamp limit
        limit = min(max(limit, 1), 1000)

        # Build query with filters
        where_clauses: list[str] = []
        params: list[Any] = []

        if rule_type is not None:
            where_clauses.append("type = ?")
            params.append(rule_type)

        if severity is not None:
            where_clauses.append("severity = ?")
            params.append(severity)

        if is_enabled is not None:
            where_clauses.append("is_enabled = ?")
            is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
            params.append(is_enabled_val)

        where_clause = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Get total count
        count_query = f"SELECT COUNT(*) as count FROM content_filter_rules {where_clause}"
        count_result = self.db.fetch_one(adapt_sql(count_query), tuple(params))
        total = count_result["count"] if count_result else 0

        # Get paginated results
        params.extend([limit, offset])
        query = f"SELECT * FROM content_filter_rules {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        rules = self.db.fetch_all(adapt_sql(query), tuple(params))

        # Convert is_enabled to boolean
        for rule in rules:
            rule["is_enabled"] = bool(rule.get("is_enabled", 1))

        return rules, total

    def create_filter_rule(
        self,
        pattern: str,
        rule_type: str = "keyword",
        severity: str = "medium",
        action: str = "warn",
        description: str | None = None,
        is_enabled: bool = True,
    ) -> int | None:
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
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    ),
                )
                return cast("int | None", cursor.lastrowid)
        except Exception as e:
            logger.error(f"Error creating filter rule: {e}")
            return None

    def create_filter_rule_idempotent(
        self,
        pattern: str,
        rule_type: str = "keyword",
        severity: str = "medium",
        action: str = "warn",
        description: str | None = None,
        is_enabled: bool = True,
    ) -> tuple[dict | None, bool]:
        """
        Create a filter rule (idempotent).

        If a rule with the same pattern already exists, returns the existing
        record with is_new=False instead of creating a duplicate.

        Args:
            pattern: Pattern to match.
            rule_type: Type of pattern (keyword, regex, pii).
            severity: Severity level (low, medium, high).
            action: Action to take (warn, block, redact).
            description: Optional description.
            is_enabled: Whether rule is enabled.

        Returns:
            Tuple[Optional[Dict], bool]: (rule record, is_new).
                is_new is True if a new record was created,
                False if the pattern already existed.
        """
        # Check if pattern already exists
        existing = self.get_filter_rule_by_pattern(pattern)
        if existing:
            return existing, False

        # Create new rule
        rule_id = self.create_filter_rule(
            pattern=pattern,
            rule_type=rule_type,
            severity=severity,
            action=action,
            description=description,
            is_enabled=is_enabled,
        )

        if rule_id:
            new_rule = self.get_filter_rule(rule_id)
            return new_rule, True

        return None, False

    def update_filter_rule(
        self,
        rule_id: int,
        pattern: str | None = None,
        rule_type: str | None = None,
        severity: str | None = None,
        action: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
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
        params.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
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
            # Audit anomaly thresholds
            "audit_failed_login_threshold": 5,
            "audit_rapid_action_threshold": 50,
            "audit_off_hours_threshold": 10,
            "audit_role_change_threshold": 5,
            "audit_permission_change_threshold": 10,
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

    def get_password_policy(self) -> dict[str, Any]:
        """
        Get password policy settings (subset of security settings).

        Returns only password-related fields for regular user access.
        This is used by the /api/password-policy endpoint which is accessible
        to all authenticated users (not just admins).

        Defaults are owned by ``get_security_settings()`` (its
        ``default_settings`` always contains these keys), so they are read
        directly here without duplicating fallback values.

        Returns:
            Dict: Password policy settings with 5 fields:
                - password_min_length: Minimum password length
                - password_require_uppercase: Whether uppercase letters required
                - password_require_lowercase: Whether lowercase letters required
                - password_require_number: Whether numbers required
                - password_require_special: Whether special characters required
        """
        settings = self.get_security_settings()
        password_keys = (
            "password_min_length",
            "password_require_uppercase",
            "password_require_lowercase",
            "password_require_number",
            "password_require_special",
        )
        return {k: settings[k] for k in password_keys}

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

    # =========================================================================
    # SSRF Configuration (Issue #3328)
    # =========================================================================

    def _get_ssrf_config_from_db(self) -> dict[str, Any]:
        """Get SSRF configuration from database.

        Returns:
            Dict with SSRF config keys and their values.
        """
        from app.repositories.database import adapt_sql

        config = {}
        keys = ["outbound_port_whitelist", "global_allowlist_hosts", "ssrf_config_version"]

        query = adapt_sql(
            "SELECT setting_key, setting_value FROM security_settings "
            "WHERE setting_key IN (?, ?, ?)"
        )
        rows = self.db.fetch_all(query, tuple(keys))

        for row in rows:
            key = row["setting_key"]
            value = row["setting_value"]

            if key == "ssrf_config_version":
                config[key] = int(value) if value else 1
            elif value:
                try:
                    config[key] = json.loads(value)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in SSRF config {key}")
                    config[key] = None
            else:
                config[key] = None

        return config

    def _get_config_version(self) -> int:
        """Get current SSRF config version."""
        config = self._get_ssrf_config_from_db()
        return config.get("ssrf_config_version", 1)

    def _increment_config_version(self) -> int:
        """Increment config version and return new version.

        Returns:
            New version number.
        """
        from app.repositories.database import adapt_sql

        # Get current version
        current = self._get_config_version()
        new_version = current + 1

        # Update in database
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("""
                INSERT INTO security_settings (setting_key, setting_value, updated_at)
                VALUES ('ssrf_config_version', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = CURRENT_TIMESTAMP
            """),
                (str(new_version),),
            )
            conn.commit()

        return new_version

    def _delete_ssrf_config_item(self, key: str) -> bool:
        """Delete a specific SSRF config item from database.

        Args:
            key: Config key to delete (e.g., 'outbound_port_whitelist').

        Returns:
            True if successful.
        """
        from app.repositories.database import adapt_sql

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql("DELETE FROM security_settings WHERE setting_key = ?"),
                    (key,),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to delete SSRF config {key}: {e}")
            return False

    def get_ssrf_status(self) -> dict[str, Any]:
        """Get SSRF protection status and configuration.

        Issue #3328: Returns SSRF protection status, default policy,
        current configuration, and interception statistics.

        Returns:
            Dict with SSRF status information.
        """

        from app.utils.llm_proxy_url_validator import get_allowed_hosts
        from app.utils.outbound_url_guard import (
            _DEFAULT_ALLOWED_PORTS,
            BLOCKED_HOSTNAMES,
            get_allowed_ports,
        )

        # Get database config
        db_config = self._get_ssrf_config_from_db()

        # Get effective port whitelist
        db_ports = db_config.get("outbound_port_whitelist")
        if db_ports is not None:
            port_whitelist = db_ports
            port_is_customized = True
            port_source = "database"
        else:
            port_whitelist = sorted(get_allowed_ports())
            port_is_customized = False
            port_source = (
                "environment" if os.environ.get("OPENACE_OUTBOUND_ALLOWED_PORTS") else "default"
            )

        # Get effective global allowlist
        db_hosts = db_config.get("global_allowlist_hosts")
        if db_hosts is not None:
            global_allowlist = db_hosts
            allowlist_is_customized = True
            allowlist_source = "database"
        else:
            allowed_hosts = get_allowed_hosts()
            global_allowlist = allowed_hosts.get(0, [])
            allowlist_is_customized = False
            allowlist_source = (
                "environment" if os.environ.get("OPENACE_LLM_PROXY_ALLOWED_HOSTS") else "default"
            )

        # Get tenant allowlist info
        allowed_hosts = get_allowed_hosts()
        tenant_ids = [tid for tid in allowed_hosts.keys() if tid != 0]
        tenant_count = len(tenant_ids)

        # Determine config source
        if port_source == "database" or allowlist_source == "database":
            config_source = "database"
        elif port_source == "environment" or allowlist_source == "environment":
            config_source = "environment"
        else:
            config_source = "default"

        # Check emergency mode
        emergency_mode = (
            os.environ.get("OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK", "").lower() == "true"
        )

        # Check if can reset
        can_reset = port_is_customized or allowlist_is_customized

        # Get interception stats
        interception_stats = self._get_interception_stats()

        # Build response
        return {
            "ssrf_protection_enabled": not emergency_mode,
            "emergency_mode": emergency_mode,
            "config_source": config_source,
            "config_version": db_config.get("ssrf_config_version", 1),
            "port_whitelist": {
                "value": port_whitelist,
                "is_customized": port_is_customized,
                "default_value": sorted(_DEFAULT_ALLOWED_PORTS),
            },
            "global_allowlist": {
                "count": len(global_allowlist),
                "entries": [{"host": host, "type": "hostname"} for host in global_allowlist],
                "is_customized": allowlist_is_customized,
            },
            "tenant_allowlist": {
                "enabled": tenant_count > 0,
                "tenant_count": tenant_count,
            },
            "default_policy": {
                "blocked_hostnames": sorted(BLOCKED_HOSTNAMES),
                "blocked_private_networks": [
                    "127.0.0.0/8",
                    "10.0.0.0/8",
                    "172.16.0.0/12",
                    "192.168.0.0/16",
                    "169.254.0.0/16",
                ],
                "default_port_whitelist": sorted(_DEFAULT_ALLOWED_PORTS),
            },
            "interception_stats": interception_stats,
            "can_reset": can_reset,
        }

    def _get_interception_stats(self) -> dict[str, int]:
        """Get SSRF interception statistics from audit log.

        Returns:
            Dict with interception counts for 24h, 7d, 30d.
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        stats = {
            "last_24h": 0,
            "last_7d": 0,
            "last_30d": 0,
        }

        try:
            # Query for each time range
            for label, days in [("last_24h", 1), ("last_7d", 7), ("last_30d", 30)]:
                start_time = now - timedelta(days=days)
                query = (
                    "SELECT COUNT(*) as count FROM audit_log WHERE action = ? AND timestamp >= ?"
                )
                result = self.db.fetch_one(query, ("LLM_PROXY_URL_BLOCKED", start_time.isoformat()))
                stats[label] = result["count"] if result else 0
        except Exception as e:
            logger.warning(f"Failed to get interception stats: {e}")

        return stats

    def reset_ssrf_config(
        self, reset_ports: bool, reset_global_allowlist: bool, expected_version: int
    ) -> dict[str, Any]:
        """Reset SSRF configuration to default.

        Issue #3328: Delete database overrides and invalidate caches.

        Args:
            reset_ports: Whether to reset port whitelist.
            reset_global_allowlist: Whether to reset global allowlist.
            expected_version: Expected config version (optimistic locking).

        Returns:
            Dict with reset result.

        Raises:
            ValueError: If version conflict or no custom config.
        """
        # Verify version (optimistic locking)
        current_version = self._get_config_version()
        if current_version != expected_version:
            raise ValueError(
                f"Config version conflict: current {current_version}, expected {expected_version}"
            )

        # Check if there's anything to reset
        if not reset_ports and not reset_global_allowlist:
            raise ValueError("No reset items specified")

        reset_items = []

        # Reset port whitelist
        if reset_ports:
            if not self._delete_ssrf_config_item("outbound_port_whitelist"):
                logger.warning("Failed to delete port whitelist config")
            reset_items.append("port_whitelist")

        # Reset global allowlist
        if reset_global_allowlist:
            if not self._delete_ssrf_config_item("global_allowlist_hosts"):
                logger.warning("Failed to delete global allowlist config")
            reset_items.append("global_allowlist")

        # Increment version
        new_version = self._increment_config_version()

        # Invalidate caches
        try:
            from app.utils.llm_proxy_url_validator import invalidate_dns_cache
            from app.utils.outbound_url_guard import invalidate_port_cache

            if reset_ports:
                invalidate_port_cache()
                logger.info("Invalidated port whitelist cache")

            if reset_global_allowlist:
                invalidate_dns_cache()
                logger.info("Invalidated DNS cache")
        except ImportError as e:
            logger.warning(f"Failed to invalidate caches: {e}")

        return {
            "reset_items": reset_items,
            "new_config_version": new_version,
        }

    # =========================================================================
    # Tenant Sensitive Keywords (Issue #2789)
    # =========================================================================

    def get_tenant_keywords(
        self,
        tenant_id: int,
        limit: int = 100,
        offset: int = 0,
        is_enabled: bool | None = None,
    ) -> list[dict]:
        """
        Get tenant sensitive keywords with pagination.

        Args:
            tenant_id: Tenant ID.
            limit: Maximum number of records to return.
            offset: Number of records to skip.
            is_enabled: Optional filter for enabled status.

        Returns:
            List[Dict]: List of tenant keywords.
        """
        from app.repositories.database import adapt_sql

        base_query = "SELECT * FROM tenant_sensitive_keywords WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]

        if is_enabled is not None:
            base_query += " AND is_enabled = ?"
            is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
            params.append(is_enabled_val)

        base_query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        keywords = self.db.fetch_all(adapt_sql(base_query), tuple(params))

        # Convert is_enabled to boolean
        for kw in keywords:
            kw["is_enabled"] = bool(kw.get("is_enabled", 1))

        return keywords

    def get_tenant_keywords_count(
        self,
        tenant_id: int,
        is_enabled: bool | None = None,
    ) -> int:
        """
        Get count of tenant sensitive keywords.

        Args:
            tenant_id: Tenant ID.
            is_enabled: Optional filter for enabled status.

        Returns:
            int: Count of keywords.
        """
        from app.repositories.database import adapt_sql

        query = "SELECT COUNT(*) as count FROM tenant_sensitive_keywords WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]

        if is_enabled is not None:
            query += " AND is_enabled = ?"
            is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
            params.append(is_enabled_val)

        result = self.db.fetch_one(adapt_sql(query), tuple(params))
        return cast("int", result["count"] if result else 0)

    def get_tenant_keyword(self, tenant_id: int, keyword_id: int) -> dict | None:
        """
        Get a specific tenant keyword.

        Args:
            tenant_id: Tenant ID.
            keyword_id: Keyword ID.

        Returns:
            Optional[Dict]: Keyword data or None.
        """
        from app.repositories.database import adapt_sql

        query = adapt_sql("SELECT * FROM tenant_sensitive_keywords WHERE tenant_id = ? AND id = ?")
        keyword = self.db.fetch_one(query, (tenant_id, keyword_id))

        if keyword:
            keyword["is_enabled"] = bool(keyword.get("is_enabled", 1))

        return keyword

    def create_tenant_keyword(
        self,
        tenant_id: int,
        keyword: str,
        created_by: int | None = None,
    ) -> tuple[dict | None, bool]:
        """
        Create a tenant keyword (idempotent).

        If the keyword already exists for this tenant, returns the existing
        record with is_new=False instead of creating a duplicate.

        Args:
            tenant_id: Tenant ID.
            keyword: Keyword to add.
            created_by: User ID who created this keyword.

        Returns:
            Tuple[Optional[Dict], bool]: (keyword record, is_new).
                is_new is True if a new record was created,
                False if the keyword already existed.
        """
        from app.repositories.database import adapt_sql, is_postgresql

        normalized_keyword = keyword.lower().strip()

        if not normalized_keyword:
            logger.error("Cannot create empty keyword")
            return None, False

        # Check if keyword already exists
        existing_query = adapt_sql(
            "SELECT * FROM tenant_sensitive_keywords WHERE tenant_id = ? AND normalized_keyword = ?"
        )
        existing = self.db.fetch_one(existing_query, (tenant_id, normalized_keyword))

        if existing:
            existing["is_enabled"] = bool(existing.get("is_enabled", 1))
            return existing, False

        # Create new keyword
        try:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            if is_postgresql():
                result = self.db.fetch_one(
                    """
                    INSERT INTO tenant_sensitive_keywords
                    (tenant_id, keyword, normalized_keyword, is_enabled, created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """,
                    (tenant_id, keyword, normalized_keyword, True, created_by, created_at),
                    commit=True,
                )
                if result:
                    result["is_enabled"] = bool(result.get("is_enabled", 1))
                return result, True
            else:
                cursor = self.db.execute(
                    """
                    INSERT INTO tenant_sensitive_keywords
                    (tenant_id, keyword, normalized_keyword, is_enabled, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (tenant_id, keyword, normalized_keyword, 1, created_by, created_at),
                )

                if cursor.lastrowid:
                    # Fetch the created record
                    new_record = self.db.fetch_one(
                        "SELECT * FROM tenant_sensitive_keywords WHERE id = ?",
                        (cursor.lastrowid,),
                    )
                    if new_record:
                        new_record["is_enabled"] = bool(new_record.get("is_enabled", 1))
                    return new_record, True

                return None, False

        except Exception as e:
            logger.error(f"Error creating tenant keyword: {e}")
            return None, False

    def update_tenant_keyword(
        self,
        tenant_id: int,
        keyword_id: int,
        is_enabled: bool | None = None,
    ) -> bool:
        """
        Update a tenant keyword.

        Args:
            tenant_id: Tenant ID.
            keyword_id: Keyword ID.
            is_enabled: New enabled status.

        Returns:
            bool: True if successful.
        """
        from app.repositories.database import adapt_sql

        if is_enabled is None:
            return False

        updates = ["is_enabled = ?", "updated_at = ?"]
        is_enabled_val = is_enabled if self.db.is_postgresql else (1 if is_enabled else 0)
        params: list[Any] = [
            is_enabled_val,
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            tenant_id,
            keyword_id,
        ]

        query = adapt_sql(
            f"UPDATE tenant_sensitive_keywords SET {', '.join(updates)} WHERE tenant_id = ? AND id = ?"
        )

        try:
            cursor = self.db.execute(query, tuple(params))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Error updating tenant keyword: {e}")
            return False

    def delete_tenant_keyword(self, tenant_id: int, keyword_id: int) -> bool:
        """
        Delete a tenant keyword.

        Args:
            tenant_id: Tenant ID.
            keyword_id: Keyword ID.

        Returns:
            bool: True if successful.
        """
        from app.repositories.database import adapt_sql

        query = adapt_sql("DELETE FROM tenant_sensitive_keywords WHERE tenant_id = ? AND id = ?")

        try:
            cursor = self.db.execute(query, (tenant_id, keyword_id))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Error deleting tenant keyword: {e}")
            return False

    def get_enabled_tenant_keywords(self, tenant_id: int) -> list[str]:
        """
        Get all enabled keywords for a tenant.

        This method is used by ContentFilter to load tenant keywords
        for content checking.

        Args:
            tenant_id: Tenant ID.

        Returns:
            List[str]: List of normalized keywords.
        """
        from app.repositories.database import adapt_sql

        query = adapt_sql(
            "SELECT normalized_keyword FROM tenant_sensitive_keywords WHERE tenant_id = ? AND is_enabled = ?"
        )
        is_enabled_val = True if self.db.is_postgresql else 1

        try:
            rows = self.db.fetch_all(query, (tenant_id, is_enabled_val))
            return [row["normalized_keyword"] for row in rows]
        except Exception as e:
            logger.error(f"Error getting enabled tenant keywords: {e}")
            return []

    def get_tenant_keywords_version(self, tenant_id: int) -> int | None:
        """
        Get the current version number for tenant keywords.

        Args:
            tenant_id: Tenant ID.

        Returns:
            Optional[int]: Version number or None if no record exists.
        """
        from app.repositories.database import adapt_sql

        query = adapt_sql("SELECT version FROM tenant_keywords_version WHERE tenant_id = ?")

        try:
            result = self.db.fetch_one(query, (tenant_id,))
            return cast("int | None", result["version"] if result else None)
        except Exception as e:
            logger.error(f"Error getting tenant keywords version: {e}")
            return None

    def increment_tenant_keywords_version(self, tenant_id: int) -> bool:
        """
        Increment the version number for tenant keywords.

        Uses UPSERT to automatically create a record if one doesn't exist.
        Also updates the updated_at timestamp.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        from app.repositories.database import is_postgresql

        try:
            if is_postgresql():
                self.db.execute(
                    """
                    INSERT INTO tenant_keywords_version (tenant_id, version, updated_at)
                    VALUES (%s, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        version = tenant_keywords_version.version + 1,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (tenant_id,),
                )
            else:
                # SQLite: Use INSERT OR REPLACE with subquery for version increment
                # SQLite doesn't support ON CONFLICT DO UPDATE syntax
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO tenant_keywords_version (tenant_id, version, updated_at)
                    VALUES (?,
                        COALESCE((SELECT version FROM tenant_keywords_version WHERE tenant_id = ?), 0) + 1,
                        CURRENT_TIMESTAMP)
                """,
                    (tenant_id, tenant_id),
                )

            return True
        except Exception as e:
            logger.error(f"Error incrementing tenant keywords version: {e}")
            return False
