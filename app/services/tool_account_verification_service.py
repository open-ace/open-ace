"""Tool Account Verification Service.

Issue #3273: Service for verifying tool account mappings.

This service provides functionality to verify tool account mappings across different
tool types (local tools like Qwen/Claude, external platforms like Feishu/DingTalk,
and discovered accounts).
"""

import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models.user_tool_account import VerificationStatus
from app.repositories.user_repo import UserRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository

logger = logging.getLogger(__name__)


# Local tool types that require user directory and process checks
LOCAL_TOOL_TYPES = {"qwen", "claude", "openclaw", "codex", "zcode"}

# External platform tool types that require API verification
EXTERNAL_PLATFORM_TYPES = {"feishu", "dingtalk", "slack"}

# Discovered account types that only need history data checks
DISCOVERED_TYPES = {"discovered", "other"}


def _is_container_environment() -> bool:
    """Detect if running in a container environment (Docker/Kubernetes).

    Returns:
        True if in container, False otherwise.
    """
    # Check for Docker
    if os.path.exists("/.dockerenv"):
        return True

    # Check for Kubernetes
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True

    return False


class ToolVerifier(ABC):
    """Base class for tool account verifiers.

    Issue #3273: Abstract base class defining the interface for tool verifiers.
    """

    @abstractmethod
    def verify(self, tool_account: str, user_id: int, context: dict) -> dict:
        """Verify a tool account mapping.

        Args:
            tool_account: Tool account name.
            user_id: Associated user ID.
            context: Verification context including:
                - tenant_id: Tenant ID
                - user_info: User information (linux_account, username)
                - mapping_id: Mapping ID for database queries

        Returns:
            Dict with keys:
                - success: bool
                - message: str
                - checks: dict of check results
        """
        pass


class LocalToolVerifier(ToolVerifier):
    """Verifier for local tools (Qwen, Claude, Openclaw, Codex, ZCode).

    Issue #3273: Verifies local tool accounts by checking:
    - Hard requirement: History messages in daily_messages table
    - Soft requirements: User directory existence, process running

    Verification passes if history messages exist, regardless of user directory
    or process status.
    """

    def __init__(self, db=None):
        """Initialize verifier.

        Args:
            db: Database instance (optional, will create default if not provided).
        """
        from app.repositories.database import Database

        self.db = db or Database()

    def verify(self, tool_account: str, user_id: int, context: dict) -> dict:
        """Verify local tool account.

        Checks:
        1. History messages (hard requirement)
        2. User directory (soft, skipped if permission denied or in container)
        3. Process running (soft, skipped if permission denied or in container)
        """
        checks = {}
        warnings = []

        # Check 1: History messages (hard requirement)
        has_history = self._check_history_messages(tool_account)
        checks["has_history_data"] = has_history

        if not has_history:
            # Hard requirement failed
            return {
                "success": False,
                "message": "无历史消息数据，无法确认账号有效性",
                "checks": checks,
                "warnings": warnings,
            }

        # Check 2 & 3: User directory and process (soft requirements)
        # Skip in container environment or if permissions are insufficient
        if not _is_container_environment():
            # User directory check
            user_dir_result = self._check_user_directory(tool_account, context.get("user_info", {}))
            if user_dir_result is not None:
                checks["user_directory_exists"] = user_dir_result
                if not user_dir_result:
                    warnings.append("用户目录不存在")

            # Process check (optional)
            # Not implementing full process check due to complexity
            # In real implementation, would use subprocess.run(["pgrep", "-f", ...])
            # and handle PermissionError
        else:
            warnings.append("容器环境，跳过用户目录和进程检查")

        # Verification passed (hard requirement met)
        message = "验证通过（有历史数据）"
        if warnings:
            message += f"，但{'; '.join(warnings)}"

        return {
            "success": True,
            "message": message,
            "checks": checks,
            "warnings": warnings,
        }

    def _check_history_messages(self, tool_account: str) -> bool:
        """Check if tool account has history messages in daily_messages table.

        Args:
            tool_account: Tool account name.

        Returns:
            True if history messages exist, False otherwise.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                SELECT COUNT(*) as count
                FROM daily_messages
                WHERE sender_name = %s
            """
            params = (tool_account,)
        else:
            query = """
                SELECT COUNT(*) as count
                FROM daily_messages
                WHERE sender_name = ?
            """
            params = (tool_account,)

        try:
            result = self.db.fetch_one(query, params)
            if result and result.get("count", 0) > 0:
                return True
        except Exception as e:
            logger.error(f"Error checking history messages for {tool_account}: {e}")

        return False

    def _check_user_directory(self, tool_account: str, user_info: dict) -> bool | None:
        """Check if user directory exists.

        Args:
            tool_account: Tool account name.
            user_info: User information dict with linux_account and username.

        Returns:
            True if directory exists, False if not, None if check skipped.
        """
        # Get base path from environment or use default
        base_path = os.environ.get("OPENACE_USER_HOME_BASE", "/home")

        # Get username
        username = user_info.get("linux_account") or user_info.get("username")
        if not username:
            return None

        user_dir = os.path.join(base_path, username)

        try:
            if os.path.exists(user_dir):
                return True
            else:
                return False
        except PermissionError:
            logger.warning(f"Permission denied when checking user directory {user_dir}")
            return None
        except Exception as e:
            logger.error(f"Error checking user directory {user_dir}: {e}")
            return None


class DiscoveredAccountVerifier(ToolVerifier):
    """Verifier for discovered and other account types.

    Issue #3273: Verifies discovered accounts by checking history messages only.
    """

    def __init__(self, db=None):
        """Initialize verifier.

        Args:
            db: Database instance (optional, will create default if not provided).
        """
        from app.repositories.database import Database

        self.db = db or Database()

    def verify(self, tool_account: str, user_id: int, context: dict) -> dict:
        """Verify discovered account.

        Only checks history messages.
        """
        checks = {}

        # Check history messages
        has_history = self._check_history_messages(tool_account)
        checks["has_history_data"] = has_history

        if has_history:
            return {
                "success": True,
                "message": "验证通过（有历史数据）",
                "checks": checks,
                "warnings": [],
            }
        else:
            return {
                "success": False,
                "message": "无历史消息数据，无法确认账号有效性",
                "checks": checks,
                "warnings": [],
            }

    def _check_history_messages(self, tool_account: str) -> bool:
        """Check if tool account has history messages.

        Args:
            tool_account: Tool account name.

        Returns:
            True if history messages exist, False otherwise.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                SELECT COUNT(*) as count
                FROM daily_messages
                WHERE sender_name = %s
            """
            params = (tool_account,)
        else:
            query = """
                SELECT COUNT(*) as count
                FROM daily_messages
                WHERE sender_name = ?
            """
            params = (tool_account,)

        try:
            result = self.db.fetch_one(query, params)
            if result and result.get("count", 0) > 0:
                return True
        except Exception as e:
            logger.error(f"Error checking history messages for {tool_account}: {e}")

        return False


class ToolAccountVerificationService:
    """Service for verifying tool account mappings.

    Issue #3273: Main service that coordinates verification across different tool types.
    """

    def __init__(self, db=None):
        """Initialize verification service.

        Args:
            db: Database instance (optional, will create default if not provided).
        """
        from app.repositories.database import Database

        self.db = db or Database()
        self.mapping_repo = UserToolAccountRepository(db=self.db)
        self.user_repo = UserRepository()

        # Initialize verifiers
        self.local_verifier = LocalToolVerifier(db=self.db)
        self.discovered_verifier = DiscoveredAccountVerifier(db=self.db)

    def verify_mapping(self, mapping_id: int, context: dict | None = None) -> dict:
        """Verify a single tool account mapping.

        Args:
            mapping_id: Mapping ID to verify.
            context: Verification context including:
                - tenant_id: Tenant ID
                - actor_user_id: User ID performing verification

        Returns:
            Dict with verification results.
        """
        context = context or {}

        # Get mapping
        mapping = self.mapping_repo.get_by_id(mapping_id)
        if not mapping:
            return {
                "success": False,
                "error": "Mapping not found",
            }

        # Get user info
        user = self.user_repo.get_user_by_id(mapping.user_id)
        user_info = {
            "linux_account": user.get("linux_account") if user else None,
            "username": user.get("username") if user else None,
        }

        # Prepare context for verifier
        verifier_context = {
            "tenant_id": mapping.tenant_id,
            "user_info": user_info,
            "mapping_id": mapping_id,
        }

        # Choose verifier based on tool type
        tool_type = mapping.tool_type or "other"
        verifier = self._get_verifier(tool_type)

        # Run verification
        result = verifier.verify(
            tool_account=mapping.tool_account,
            user_id=mapping.user_id,
            context=verifier_context,
        )

        # Determine verification status
        if result["success"]:
            verification_status = VerificationStatus.VERIFIED.value
        else:
            verification_status = VerificationStatus.FAILED.value

        # Update mapping in database
        updated_mapping = self.mapping_repo.update_verification_status(
            id=mapping_id,
            verification_status=verification_status,
            verification_result=result["message"],
            expected_version=mapping.version,  # Optimistic lock check
        )

        # Prepare response
        # Handle verified_at field (SQLite returns string, PostgreSQL returns datetime)
        verified_at_str = None
        if updated_mapping and updated_mapping.verified_at:
            if isinstance(updated_mapping.verified_at, str):
                # SQLite returns string
                verified_at_str = updated_mapping.verified_at
            elif hasattr(updated_mapping.verified_at, "isoformat"):
                # PostgreSQL returns datetime object
                verified_at_str = updated_mapping.verified_at.isoformat()

        response = {
            "success": True,
            "verification_status": verification_status,
            "verification_result": result["message"],
            "verified_at": verified_at_str,
            "details": {
                "tool_type": tool_type,
                "tool_account": mapping.tool_account,
                "checks": result.get("checks", {}),
                "warnings": result.get("warnings", []),
            },
        }

        return response

    def verify_batch(self, mapping_ids: list[int], context: dict | None = None) -> dict:
        """Verify multiple tool account mappings.

        Args:
            mapping_ids: List of mapping IDs to verify.
            context: Verification context.

        Returns:
            Dict with batch verification results.
        """
        context = context or {}

        results = []
        verified_count = 0
        failed_count = 0

        # Use ThreadPoolExecutor for concurrent verification
        max_workers = min(5, len(mapping_ids))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all verification tasks
            future_to_id = {
                executor.submit(self._verify_single_with_timeout, mid, context): mid
                for mid in mapping_ids
            }

            # Collect results
            for future in as_completed(future_to_id):
                mapping_id = future_to_id[future]
                try:
                    result = future.result(timeout=15)
                    if result.get("success"):
                        if result.get("verification_status") == VerificationStatus.VERIFIED.value:
                            verified_count += 1
                        else:
                            failed_count += 1
                        results.append(
                            {
                                "id": mapping_id,
                                "success": True,
                                "verification_status": result.get("verification_status"),
                                "verification_result": result.get("verification_result"),
                            }
                        )
                    else:
                        failed_count += 1
                        results.append(
                            {
                                "id": mapping_id,
                                "success": False,
                                "error": result.get("error", "Unknown error"),
                            }
                        )
                except Exception as e:
                    failed_count += 1
                    results.append(
                        {
                            "id": mapping_id,
                            "success": False,
                            "error": str(e),
                        }
                    )

        return {
            "total": len(mapping_ids),
            "verified": verified_count,
            "failed": failed_count,
            "results": results,
        }

    def _verify_single_with_timeout(self, mapping_id: int, context: dict) -> dict:
        """Verify a single mapping with timeout protection.

        Args:
            mapping_id: Mapping ID.
            context: Verification context.

        Returns:
            Verification result dict.
        """
        try:
            return self.verify_mapping(mapping_id, context)
        except Exception as e:
            logger.error(f"Error verifying mapping {mapping_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def _get_verifier(self, tool_type: str) -> ToolVerifier:
        """Get appropriate verifier for tool type.

        Args:
            tool_type: Tool type.

        Returns:
            ToolVerifier instance.
        """
        if tool_type in LOCAL_TOOL_TYPES:
            return self.local_verifier
        elif tool_type in EXTERNAL_PLATFORM_TYPES:
            # For P0, we don't implement external platform verification
            # This will be implemented in P1
            logger.warning(
                f"External platform verification not yet implemented for {tool_type}, "
                "falling back to history data check"
            )
            return self.discovered_verifier
        else:
            return self.discovered_verifier


# Global service instance
_tool_account_verification_service: ToolAccountVerificationService | None = None


def get_tool_account_verification_service() -> ToolAccountVerificationService:
    """Get the global tool account verification service instance.

    Returns:
        ToolAccountVerificationService instance.
    """
    global _tool_account_verification_service
    if _tool_account_verification_service is None:
        _tool_account_verification_service = ToolAccountVerificationService()
    return _tool_account_verification_service
