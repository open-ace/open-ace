"""
Open ACE - Rule Version Manager Service

负责内容过滤规则的版本快照和回滚功能。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)


class RuleVersionManager:
    """
    规则版本管理器，负责版本快照和回滚。

    功能：
    - 创建规则版本快照
    - 查询版本历史
    - 回滚到指定版本
    - 回滚后的审批状态处理
    """

    def __init__(self, governance_repo: GovernanceRepository):
        """
        初始化版本管理器。

        Args:
            governance_repo: 治理数据仓库实例
        """
        self.governance_repo = governance_repo

    def create_version(
        self, rule_id: int, user_id: int, change_reason: str | None = None
    ) -> int | None:
        """
        为规则创建版本快照。

        Args:
            rule_id: 规则ID
            user_id: 创建者ID
            change_reason: 变更原因

        Returns:
            版本ID或None
        """
        try:
            # 获取当前规则
            rule = self.governance_repo.get_filter_rule(rule_id)
            if rule is None:
                logger.error(f"Rule {rule_id} not found")
                return None

            # 获取下一个版本号
            next_version = self._get_next_version_number(rule_id)

            # 创建快照
            snapshot = self._create_snapshot(rule)

            # 写入数据库
            version_id = self._save_version(
                rule_id=rule_id,
                version_number=next_version,
                snapshot=snapshot,
                user_id=user_id,
                change_reason=change_reason,
            )

            if version_id:
                logger.info(
                    f"Created version {next_version} for rule {rule_id} " f"by user {user_id}"
                )

            return version_id

        except Exception as e:
            logger.error(f"Failed to create version for rule {rule_id}: {e}")
            return None

    def get_versions(self, rule_id: int, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """
        获取规则的版本历史。

        Args:
            rule_id: 规则ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            版本列表
        """
        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            query = adapt_sql("""
                SELECT id, rule_id, version_number, created_by, created_at, change_reason
                FROM filter_rule_versions
                WHERE rule_id = ?
                ORDER BY version_number DESC
                LIMIT ? OFFSET ?
            """)

            cursor.execute(query, (rule_id, limit, offset))
            rows = cursor.fetchall()

            versions = []
            for row in rows:
                versions.append(
                    {
                        "id": row[0],
                        "rule_id": row[1],
                        "version_number": row[2],
                        "created_by": row[3],
                        "created_at": row[4],
                        "change_reason": row[5],
                    }
                )

            return versions

        except Exception as e:
            logger.error(f"Failed to get versions for rule {rule_id}: {e}")
            return []

    def rollback_to_version(
        self, rule_id: int, version_number: int, user_id: int, rollback_reason: str | None = None
    ) -> bool:
        """
        回滚规则到指定版本。

        Args:
            rule_id: 规则ID
            version_number: 目标版本号
            user_id: 执行回滚的用户ID
            rollback_reason: 回滚原因

        Returns:
            是否成功
        """
        try:
            # 获取目标版本快照
            snapshot = self._get_version_snapshot(rule_id, version_number)
            if snapshot is None:
                logger.error(f"Version {version_number} not found for rule {rule_id}")
                return False

            # 确定回滚后的审批状态
            new_approval_status = self._determine_approval_status_after_rollback(snapshot)

            # 恢复规则
            success = self._restore_rule_from_snapshot(
                rule_id=rule_id, snapshot=snapshot, new_approval_status=new_approval_status
            )

            if not success:
                return False

            # 创建新版本快照（记录回滚）
            self.create_version(
                rule_id=rule_id,
                user_id=user_id,
                change_reason=f"Rollback to version {version_number}: {rollback_reason}",
            )

            # 记录审批日志
            self._log_rollback(
                rule_id=rule_id,
                version_number=version_number,
                user_id=user_id,
                rollback_reason=rollback_reason,
            )

            logger.info(
                f"Rolled back rule {rule_id} to version {version_number} " f"by user {user_id}"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to rollback rule {rule_id} to version {version_number}: {e}")
            return False

    def _get_next_version_number(self, rule_id: int) -> int:
        """获取下一个版本号"""
        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            query = adapt_sql("""
                SELECT MAX(version_number) as max_version
                FROM filter_rule_versions
                WHERE rule_id = ?
            """)

            cursor.execute(query, (rule_id,))
            row = cursor.fetchone()

            if row and row[0] is not None:
                return row[0] + 1
            else:
                return 1

        except Exception as e:
            logger.error(f"Failed to get next version number: {e}")
            return 1

    def _create_snapshot(self, rule: dict[str, Any]) -> dict[str, Any]:
        """创建规则快照"""
        # 移除不需要的字段
        snapshot = {k: v for k, v in rule.items() if k not in ["id", "created_at", "updated_at"]}
        return snapshot

    def _save_version(
        self,
        rule_id: int,
        version_number: int,
        snapshot: dict[str, Any],
        user_id: int,
        change_reason: str | None,
    ) -> int | None:
        """保存版本到数据库"""
        try:
            from app.repositories.database import adapt_sql, get_connection, is_postgresql

            conn = get_connection()
            cursor = conn.cursor()

            if is_postgresql():
                query = adapt_sql("""
                    INSERT INTO filter_rule_versions
                    (rule_id, version_number, rule_snapshot, created_by, created_at, change_reason)
                    VALUES (?, ?, ?::jsonb, ?, ?, ?)
                    RETURNING id
                """)

                cursor.execute(
                    query,
                    (
                        rule_id,
                        version_number,
                        json.dumps(snapshot),
                        user_id,
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        change_reason,
                    ),
                )

                row = cursor.fetchone()
                version_id = row[0] if row else None

            else:
                query = adapt_sql("""
                    INSERT INTO filter_rule_versions
                    (rule_id, version_number, rule_snapshot, created_by, created_at, change_reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                """)

                cursor.execute(
                    query,
                    (
                        rule_id,
                        version_number,
                        json.dumps(snapshot),
                        user_id,
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        change_reason,
                    ),
                )

                version_id = cursor.lastrowid

            conn.commit()
            return version_id

        except Exception as e:
            logger.error(f"Failed to save version: {e}")
            return None

    def _get_version_snapshot(self, rule_id: int, version_number: int) -> dict[str, Any] | None:
        """获取版本快照"""
        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            query = adapt_sql("""
                SELECT rule_snapshot
                FROM filter_rule_versions
                WHERE rule_id = ? AND version_number = ?
            """)

            cursor.execute(query, (rule_id, version_number))
            row = cursor.fetchone()

            if row:
                snapshot_json = row[0]
                return (
                    json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
                )

            return None

        except Exception as e:
            logger.error(f"Failed to get version snapshot: {e}")
            return None

    def _determine_approval_status_after_rollback(self, snapshot: dict[str, Any]) -> str:
        """
        确定回滚后的审批状态。

        规则：
        - 如果快照的 approval_status 为 'approved'，保持 'approved'
        - 如果快照的 approval_status 为 'pending' 或 'rejected'，设置为 'pending'
        """
        snapshot_status = snapshot.get("approval_status", "approved")

        if snapshot_status == "approved":
            return "approved"
        else:
            # pending 或 rejected 都需要重新审批
            return "pending"

    def _restore_rule_from_snapshot(
        self, rule_id: int, snapshot: dict[str, Any], new_approval_status: str
    ) -> bool:
        """从快照恢复规则"""
        try:
            # 更新规则（保留 ID、created_at 等）
            update_data = {**snapshot}
            update_data["approval_status"] = new_approval_status

            success = self.governance_repo.update_filter_rule(rule_id=rule_id, **update_data)

            return success

        except Exception as e:
            logger.error(f"Failed to restore rule from snapshot: {e}")
            return False

    def _log_rollback(
        self, rule_id: int, version_number: int, user_id: int, rollback_reason: str | None
    ):
        """记录回滚到审批日志"""
        try:

            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            details = {
                "version_number": version_number,
                "rollback_reason": rollback_reason,
            }

            query = adapt_sql("""
                INSERT INTO filter_rule_approval_log
                (rule_id, action, actor_user_id, timestamp, details)
                VALUES (?, ?, ?, ?, ?)
            """)

            cursor.execute(
                query,
                (
                    rule_id,
                    "rollback",
                    user_id,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    json.dumps(details),
                ),
            )

            conn.commit()

        except Exception as e:
            logger.error(f"Failed to log rollback: {e}")
