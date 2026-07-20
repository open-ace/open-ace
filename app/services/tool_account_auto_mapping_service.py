"""
Open ACE - Tool Account Auto Mapping Service

Service for automatic tool account mapping based on:
1. Username/email matching in sender_name
2. Custom mapping rules
3. Unmapped account notification

This service runs after data collection to auto-assign tool accounts to users.
"""
from __future__ import annotations


import logging
from dataclasses import dataclass

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.models.user import User
from app.repositories.database import Database, adapt_boolean_condition
from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository

logger = logging.getLogger(__name__)


@dataclass
class AutoMappingResult:
    """Result of an auto-mapping operation."""

    tool_account: str
    user_id: int
    username: str
    matched_by: str  # "email", "username", "rule", "system_account"
    rule_id: int | None = None
    created_mapping_id: int | None = None


class ToolAccountAutoMappingService:
    """Service for automatic tool account mapping."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()
        self.rule_repo = ToolAccountMappingRuleRepository(db)
        self.mapping_repo = UserToolAccountRepository(db)
        self._users_cache: dict[int, User] | None = None  # Cache for N+1 optimization

    def _get_users_cache(self) -> dict[int, User]:
        """Get cached user dict (keyed by user_id) for N+1 optimization."""
        if self._users_cache is None:
            users = self.get_all_users()
            self._users_cache = {user.id: user for user in users if user.id is not None}
        return self._users_cache

    def get_all_users(self) -> list[User]:
        """Get all active users with auto_mapping enabled."""
        query = f"""
            SELECT id, username, email, role, is_active, auto_mapping_enabled
            FROM users
            WHERE {adapt_boolean_condition('is_active', True)}
              AND (auto_mapping_enabled IS NULL OR {adapt_boolean_condition('auto_mapping_enabled', True)})
        """
        rows = self.db.fetch_all(query)
        return [
            User(
                id=row.get("id"),
                username=row.get("username", ""),
                email=row.get("email", ""),
                role=row.get("role", "user"),
                is_active=row.get("is_active", True),
            )
            for row in rows
        ]

    def get_unmapped_accounts(self) -> list[dict]:
        """Get unmapped tool accounts from daily_messages."""
        return self.mapping_repo.get_unmapped_tool_accounts()

    def try_match_by_username_or_email(
        self, tool_account: str, users: list[User]
    ) -> AutoMappingResult | None:
        """
        Try to match tool_account by username or email.

        Sender name format is typically: {system_account}-{hostname}-{tool}
        Example: alice-macbook-pro-qwen

        We check if:
        1. The first part (system_account) exactly matches username
        2. The tool_account contains username
        3. The tool_account contains email (or email prefix before @)
        """
        if not tool_account:
            return None

        # Extract system_account from sender_name (first part before first '-')
        parts = tool_account.split("-")
        system_account = parts[0] if parts else None

        for user in users:
            # Skip users without valid id
            if user.id is None:
                continue

            # Match 1: system_account exactly matches username
            if system_account and system_account.lower() == user.username.lower():
                return AutoMappingResult(
                    tool_account=tool_account,
                    user_id=user.id,
                    username=user.username,
                    matched_by="username",
                )

            # Match 2: system_account matches email prefix (before @)
            if user.email and system_account:
                email_prefix = user.email.split("@")[0].lower()
                if system_account.lower() == email_prefix:
                    return AutoMappingResult(
                        tool_account=tool_account,
                        user_id=user.id,
                        username=user.username,
                        matched_by="email",
                    )

            # Match 3: tool_account contains username
            if user.username.lower() in tool_account.lower():
                return AutoMappingResult(
                    tool_account=tool_account,
                    user_id=user.id,
                    username=user.username,
                    matched_by="username_contains",
                )

            # Match 4: tool_account contains email prefix
            if user.email:
                email_prefix = user.email.split("@")[0].lower()
                if email_prefix in tool_account.lower():
                    return AutoMappingResult(
                        tool_account=tool_account,
                        user_id=user.id,
                        username=user.username,
                        matched_by="email_contains",
                    )

        return None

    def try_match_by_rules(
        self, tool_account: str, tool_type: str | None = None
    ) -> AutoMappingResult | None:
        """
        Try to match tool_account using custom rules.

        Rules are checked in priority order (higher priority first).
        Uses cached users to avoid N+1 queries.
        """
        rules = self.rule_repo.get_auto_rules()
        users_cache = self._get_users_cache()  # Use cache instead of individual queries

        for rule in rules:
            if rule.matches(tool_account, tool_type):
                # Get username from cache (no DB query)
                user = users_cache.get(rule.user_id)
                username = user.username if user else ""

                return AutoMappingResult(
                    tool_account=tool_account,
                    user_id=rule.user_id,
                    username=username,
                    matched_by="rule",
                    rule_id=rule.id,
                )

        return None

    def auto_map_account(
        self, tool_account: str, tool_type: str | None = None
    ) -> AutoMappingResult | None:
        """
        Auto-map a single tool_account using all available methods.

        Priority:
        1. Custom rules (highest priority rules first)
        2. Username/email matching

        Returns the mapping result if successful, None if no match found.
        """
        # Check if already mapped
        existing = self.mapping_repo.get_by_tool_account(tool_account)
        if existing:
            return None  # Already mapped

        users = self.get_all_users()

        # Try rule matching first (rules have explicit priority)
        result = self.try_match_by_rules(tool_account, tool_type)
        if result:
            return result

        # Try username/email matching
        result = self.try_match_by_username_or_email(tool_account, users)
        if result:
            return result

        return None

    def apply_mapping(self, result: AutoMappingResult) -> int | None:
        """Apply the auto-mapping result by creating a tool account mapping."""
        mapping = self.mapping_repo.create(
            user_id=result.user_id,
            tool_account=result.tool_account,
            tool_type=None,  # Can be inferred later
            description=f"Auto-mapped by {result.matched_by}",
        )
        if mapping:
            # Update daily_messages user_id
            self.mapping_repo.update_daily_messages_user_id(result.tool_account, result.user_id)
            return mapping.id
        return None

    def run_auto_mapping(self, dry_run: bool = False) -> tuple[list[AutoMappingResult], list[dict]]:
        """
        Run auto-mapping for all unmapped tool accounts.

        Args:
            dry_run: If True, only report what would be mapped without creating mappings

        Returns:
            Tuple of (successful_mappings, remaining_unmapped)
        """
        # Clear cache to ensure fresh user data
        self._users_cache = None

        unmapped = self.get_unmapped_accounts()
        results = []
        still_unmapped = []

        for account in unmapped:
            tool_account = account.get("sender_name", "")
            tool_type = self._infer_tool_type(tool_account)

            result = self.auto_map_account(tool_account, tool_type)

            if result:
                if not dry_run:
                    mapping_id = self.apply_mapping(result)
                    result.created_mapping_id = mapping_id
                    logger.info(
                        f"Auto-mapped {tool_account} to user {result.username} "
                        f"via {result.matched_by}"
                    )
                results.append(result)
            else:
                still_unmapped.append(account)

        return results, still_unmapped

    def _infer_tool_type(self, tool_account: str) -> str | None:
        """Infer tool type from tool_account suffix."""
        known_tools = ["qwen", "claude", "openclaw", "codex", "zcode"]
        for tool in known_tools:
            if tool_account.lower().endswith(tool):
                return tool
        return None

    def create_default_rules_for_user(self, user_id: int) -> list[ToolAccountMappingRule]:
        """
        Create default mapping rules for a user based on their username/email.

        Default rules:
        1. {username}-* (prefix match) - matches sender_name starting with username
        2. *-{username}* (contains match) - fallback for other patterns
        """
        query = "SELECT username, email FROM users WHERE id = ?"
        row = self.db.fetch_one(query, (user_id,))
        if not row:
            return []

        username = row.get("username", "")
        email_prefix = row.get("email", "").split("@")[0] if row.get("email") else ""

        rules = []

        # Rule 1: username prefix match (highest priority)
        if username:
            rule = self.rule_repo.create(
                user_id=user_id,
                pattern=f"{username}-*",
                match_type="prefix",
                priority=10,
                is_auto=True,
                description=f"Auto-generated: username prefix match for {username}",
            )
            if rule:
                rules.append(rule)

        # Rule 2: email prefix match
        if email_prefix and email_prefix != username:
            rule = self.rule_repo.create(
                user_id=user_id,
                pattern=f"{email_prefix}-*",
                match_type="prefix",
                priority=9,
                is_auto=True,
                description=f"Auto-generated: email prefix match for {email_prefix}",
            )
            if rule:
                rules.append(rule)

        # Rule 3: username contains match (lower priority fallback)
        if username:
            rule = self.rule_repo.create(
                user_id=user_id,
                pattern=f"*{username}*",
                match_type="contains",
                priority=5,
                is_auto=True,
                description=f"Auto-generated: username contains match for {username}",
            )
            if rule:
                rules.append(rule)

        return rules

    def get_mapping_stats(self) -> dict:
        """Get statistics about mapping status."""
        unmapped = self.get_unmapped_accounts()
        mapped = self.mapping_repo.get_all()

        # Count unmapped by inferred tool type
        unmapped_by_tool: dict[str, int] = {}
        for account in unmapped:
            tool_account = account.get("sender_name", "")
            tool_type = self._infer_tool_type(tool_account) or "unknown"
            unmapped_by_tool[tool_type] = unmapped_by_tool.get(tool_type, 0) + 1

        # Count mapped by tool type
        mapped_by_tool: dict[str, int] = {}
        for mapping in mapped:
            tool_type = mapping.tool_type or "unknown"
            mapped_by_tool[tool_type] = mapped_by_tool.get(tool_type, 0) + 1

        return {
            "total_unmapped": len(unmapped),
            "total_mapped": len(mapped),
            "unmapped_by_tool": unmapped_by_tool,
            "mapped_by_tool": mapped_by_tool,
            "unmapped_accounts": unmapped[:20],  # First 20 for display
        }
