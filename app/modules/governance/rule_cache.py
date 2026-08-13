"""
Open ACE - Rule Cache Module

负责内容过滤规则的缓存管理和多实例同步。
"""

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from app.modules.governance.rule_loader import RuleLoader

logger = logging.getLogger(__name__)


class RuleCache:
    """
    规则缓存管理器，负责规则缓存和失效。

    功能：
    - 规则缓存（按租户隔离）
    - 缓存失效通知
    - 指数退避轮询同步
    - 编译正则缓存
    """

    def __init__(
        self,
        rule_loader: RuleLoader,
        governance_repo=None,
        initial_poll_interval: float = 5.0,
        max_poll_interval: float = 60.0,
    ):
        """
        初始化规则缓存管理器。

        Args:
            rule_loader: 规则加载器
            governance_repo: 治理数据仓库（用于同步表查询）
            initial_poll_interval: 初始轮询间隔（秒）
            max_poll_interval: 最大轮询间隔（秒）
        """
        self.rule_loader = rule_loader
        self.governance_repo = governance_repo

        # 缓存存储
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_timestamps: dict[str, datetime] = {}
        self._cache_valid: dict[str, bool] = {}

        # 编译正则缓存（LRU）
        self._compiled_patterns: OrderedDict[str, Any] = OrderedDict()
        self._max_compiled_cache_size = 100

        # 同步状态
        self._last_sync_check = datetime.now(timezone.utc).replace(tzinfo=None)
        self._poll_interval = initial_poll_interval
        self._initial_poll_interval = initial_poll_interval
        self._max_poll_interval = max_poll_interval

        # 线程锁
        self._lock = threading.Lock()

        # 同步轮询线程
        self._sync_thread: threading.Thread | None = None
        self._stop_sync = False

    def get_rules(self, tenant_id: int | None = None) -> list[dict[str, Any]]:
        """
        获取规则（优先从缓存获取）。

        Args:
            tenant_id: 租户ID

        Returns:
            规则列表
        """
        cache_key = self._get_cache_key(tenant_id)

        # 检查缓存有效性
        with self._lock:
            if self._cache_valid.get(cache_key, False) and cache_key in self._cache:
                return self._cache[cache_key]

        # 缓存失效，重新加载
        rules = self.rule_loader.load_rules(tenant_id=tenant_id)

        # 更新缓存
        with self._lock:
            self._cache[cache_key] = rules
            self._cache_timestamps[cache_key] = datetime.now(timezone.utc).replace(tzinfo=None)
            self._cache_valid[cache_key] = True

        return rules

    def invalidate(self, tenant_id: int | None = None):
        """
        使缓存失效。

        Args:
            tenant_id: 租户ID（None表示所有租户）
        """
        with self._lock:
            if tenant_id is None:
                # 失效所有租户的缓存
                self._cache_valid.clear()
                logger.debug("All rule caches invalidated")
            else:
                # 失效指定租户的缓存
                cache_key = self._get_cache_key(tenant_id)
                self._cache_valid[cache_key] = False
                logger.debug(f"Rule cache invalidated for tenant {tenant_id}")

    def notify_sync(self, rule_id: int, action: str, tenant_id: int | None = None):
        """
        通知规则变更（写入同步表）。

        Args:
            rule_id: 规则ID
            action: 动作类型（created/updated/deleted）
            tenant_id: 租户ID
        """
        if self.governance_repo is None:
            return

        try:
            # 写入同步通知表
            self._write_sync_notification(rule_id, action, tenant_id)

            # 立即失效缓存
            self.invalidate(tenant_id)

            # 重置轮询间隔
            self._poll_interval = self._initial_poll_interval

            logger.debug(f"Sync notification written: rule_id={rule_id}, action={action}")

        except Exception as e:
            logger.error(f"Failed to write sync notification: {e}")

    def _write_sync_notification(self, rule_id: int, action: str, tenant_id: int | None):
        """写入同步通知到数据库"""
        if self.governance_repo is None:
            return

        # 这里需要直接操作数据库写入 rule_cache_sync 表
        # 由于 GovernanceRepository 可能没有这个方法，我们需要直接使用 db
        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            query = adapt_sql("""
                INSERT INTO rule_cache_sync (rule_id, action, tenant_id, timestamp, processed)
                VALUES (?, ?, ?, ?, ?)
            """)

            cursor.execute(
                query,
                (
                    rule_id,
                    action,
                    tenant_id,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    False,
                ),
            )

            conn.commit()

        except Exception as e:
            logger.error(f"Failed to write sync notification to database: {e}")

    def start_sync_polling(self):
        """启动同步轮询线程"""
        if self._sync_thread is not None and self._sync_thread.is_alive():
            logger.warning("Sync polling thread already running")
            return

        self._stop_sync = False
        self._sync_thread = threading.Thread(target=self._poll_sync_events, daemon=True)
        self._sync_thread.start()
        logger.info("Started rule cache sync polling thread")

    def stop_sync_polling(self):
        """停止同步轮询线程"""
        self._stop_sync = True
        if self._sync_thread is not None:
            self._sync_thread.join(timeout=5)
        logger.info("Stopped rule cache sync polling thread")

    def _poll_sync_events(self):
        """轮询同步事件（指数退避）"""
        while not self._stop_sync:
            try:
                self._check_sync_events()
            except Exception as e:
                logger.error(f"Error in sync polling: {e}")

            # 等待下次轮询
            time.sleep(self._poll_interval)

    def _check_sync_events(self):
        """检查同步事件"""
        if self.governance_repo is None:
            return

        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = get_connection()
            cursor = conn.cursor()

            # 查询未处理的同步事件
            query = adapt_sql("""
                SELECT id, rule_id, action, tenant_id, timestamp
                FROM rule_cache_sync
                WHERE processed = 0 AND timestamp > ?
                ORDER BY timestamp ASC
                LIMIT 100
            """)

            cursor.execute(query, (self._last_sync_check.isoformat(),))
            events = cursor.fetchall()

            if events:
                # 处理事件
                for event in events:
                    event_id = event[0]
                    _rule_id = event[1]  # noqa: F841 - extracted for clarity but not used
                    _action = event[2]  # noqa: F841 - extracted for clarity but not used
                    tenant_id = event[3]

                    # 失效缓存
                    self.invalidate(tenant_id)

                    # 标记为已处理
                    update_query = adapt_sql("""
                        UPDATE rule_cache_sync
                        SET processed = 1
                        WHERE id = ?
                    """)
                    cursor.execute(update_query, (event_id,))

                conn.commit()

                # 更新最后检查时间
                self._last_sync_check = datetime.now(timezone.utc).replace(tzinfo=None)

                # 有更新，重置轮询间隔
                self._poll_interval = self._initial_poll_interval

                logger.debug(f"Processed {len(events)} sync events")

            else:
                # 无更新，增加轮询间隔（指数退避）
                self._poll_interval = min(self._poll_interval * 1.5, self._max_poll_interval)

        except Exception as e:
            logger.error(f"Failed to check sync events: {e}")

    def _get_cache_key(self, tenant_id: int | None) -> str:
        """生成缓存键"""
        if tenant_id is None:
            return "rules:global"
        return f"rules:tenant:{tenant_id}"

    def get_compiled_pattern(self, pattern: str) -> Any | None:
        """
        获取编译后的正则表达式（从缓存）。

        Args:
            pattern: 正则表达式模式

        Returns:
            编译后的正则表达式对象
        """
        import re

        with self._lock:
            if pattern in self._compiled_patterns:
                # 移到末尾（LRU）
                self._compiled_patterns.move_to_end(pattern)
                return self._compiled_patterns[pattern]

            # 编译新模式
            try:
                compiled = re.compile(pattern, re.IGNORECASE)

                # 添加到缓存
                self._compiled_patterns[pattern] = compiled

                # 检查缓存大小
                while len(self._compiled_patterns) > self._max_compiled_cache_size:
                    # 移除最旧的
                    self._compiled_patterns.popitem(last=False)

                return compiled

            except re.error as e:
                logger.error(f"Invalid regex pattern: {pattern}, error: {e}")
                return None

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        with self._lock:
            return {
                "rule_cache_count": len(self._cache),
                "rule_cache_keys": list(self._cache.keys()),
                "compiled_pattern_cache_size": len(self._compiled_patterns),
                "current_poll_interval": self._poll_interval,
                "max_poll_interval": self._max_poll_interval,
            }
