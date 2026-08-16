"""
Open ACE - Trigger Log Buffer Module

负责内容过滤规则触发日志的缓冲和批量写入。
"""

import atexit
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from queue import Queue
from typing import Any

logger = logging.getLogger(__name__)


class TriggerLogBuffer:
    """
    触发日志缓冲器，实现进程级缓冲和批量写入。

    功能：
    - 进程级内存缓冲区
    - 线程安全
    - 批量写入策略
    - atexit 钩子保证日志不丢失
    """

    def __init__(
        self, batch_size: int = 100, flush_interval: float = 5.0, max_buffer_size: int = 10000
    ):
        """
        初始化触发日志缓冲器。

        Args:
            batch_size: 批量写入大小
            flush_interval: 刷新间隔（秒）
            max_buffer_size: 最大缓冲区大小
        """
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_buffer_size = max_buffer_size

        # 线程安全的队列
        self._buffer: Queue = Queue(maxsize=max_buffer_size)
        self._lock = threading.Lock()

        # 刷新线程
        self._flush_thread: threading.Thread | None = None
        self._stop_flush = False

        # 统计信息
        self._total_added = 0
        self._total_flushed = 0
        self._flush_errors = 0

        # 注册退出钩子
        atexit.register(self.force_flush)

    def add(
        self,
        rule_id: int,
        action_taken: str,
        matched_content_hash: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
        tenant_id: int | None = None,
    ):
        """
        添加触发日志到缓冲区。

        Args:
            rule_id: 规则ID
            action_taken: 执行的动作
            matched_content_hash: 匹配内容的哈希
            session_id: 会话ID
            user_id: 用户ID
            tenant_id: 租户ID
        """
        log_entry = {
            "rule_id": rule_id,
            "matched_content_hash": matched_content_hash,
            "matched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "action_taken": action_taken,
            "session_id": session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
        }

        try:
            self._buffer.put_nowait(log_entry)
            self._total_added += 1

            # 检查是否需要立即刷新
            if self._buffer.qsize() >= self.batch_size:
                self._trigger_flush()

        except Exception as e:
            logger.error(f"Failed to add trigger log to buffer: {e}")

    def _trigger_flush(self):
        """触发刷新（非阻塞）"""
        if self._lock.acquire(blocking=False):
            try:
                self._flush_batch()
            finally:
                self._lock.release()

    def _flush_batch(self):
        """刷新一批日志到数据库"""
        if self._buffer.empty():
            return

        # 收集一批日志
        batch = []
        while not self._buffer.empty() and len(batch) < self.batch_size:
            try:
                batch.append(self._buffer.get_nowait())
            except:
                break

        if not batch:
            return

        # 写入数据库
        try:
            self._write_to_database(batch)
            self._total_flushed += len(batch)
            logger.debug(f"Flushed {len(batch)} trigger logs to database")

        except Exception as e:
            self._flush_errors += 1
            logger.error(f"Failed to write trigger logs to database: {e}")
            # 写入失败，尝试重新放回缓冲区
            for entry in batch:
                try:
                    self._buffer.put_nowait(entry)
                except:
                    pass  # 缓冲区已满，丢弃

    def _write_to_database(self, batch: list[dict[str, Any]]):
        """写入数据库"""
        from app.repositories.database import adapt_sql, get_connection

        conn = get_connection()
        cursor = conn.cursor()

        try:
            for entry in batch:
                query = adapt_sql("""
                    INSERT INTO filter_rule_trigger_log
                    (rule_id, matched_content_hash, matched_at, action_taken,
                     session_id, user_id, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """)

                cursor.execute(
                    query,
                    (
                        entry["rule_id"],
                        entry["matched_content_hash"],
                        entry["matched_at"],
                        entry["action_taken"],
                        entry["session_id"],
                        entry["user_id"],
                        entry["tenant_id"],
                    ),
                )

            conn.commit()

        except Exception as e:
            conn.rollback()
            raise e

    def force_flush(self):
        """强制刷新所有缓冲的日志"""
        with self._lock:
            while not self._buffer.empty():
                self._flush_batch()

    def start_flush_thread(self):
        """启动定期刷新线程"""
        if self._flush_thread is not None and self._flush_thread.is_alive():
            logger.warning("Flush thread already running")
            return

        self._stop_flush = False
        self._flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self._flush_thread.start()
        logger.info("Started trigger log flush thread")

    def stop_flush_thread(self):
        """停止刷新线程"""
        self._stop_flush = True
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=10)
        # 强制刷新剩余日志
        self.force_flush()
        logger.info("Stopped trigger log flush thread")

    def _periodic_flush(self):
        """定期刷新"""
        while not self._stop_flush:
            time.sleep(self.flush_interval)
            self.force_flush()

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """
        计算内容哈希（避免存储敏感内容）。

        Args:
            content: 原始内容

        Returns:
            SHA256 哈希值（前16字符）
        """
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get_stats(self) -> dict[str, Any]:
        """获取缓冲区统计信息"""
        return {
            "buffer_size": self._buffer.qsize(),
            "max_buffer_size": self.max_buffer_size,
            "batch_size": self.batch_size,
            "flush_interval": self.flush_interval,
            "total_added": self._total_added,
            "total_flushed": self._total_flushed,
            "flush_errors": self._flush_errors,
            "pending": self._total_added - self._total_flushed,
        }
