"""ContentFilter 单例模块 - 确保全局唯一实例

解决 Issue #2768：规则 CRUD 后对话代理缓存未刷新的问题。

通过单例模式统一 ContentFilter 实例，确保所有模块使用同一个实例，
规则变更后刷新该实例的缓存即可生效。

多进程部署说明：
    本方案解决单进程内多实例问题。
    多进程部署场景，各进程仍有独立单例。
    后续可通过 Redis Pub/Sub 或数据库版本号机制实现跨进程同步。
"""

import logging
import threading
from typing import Optional

from app.modules.governance.content_filter import ContentFilter
from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)

# 单例实例和锁
_lock = threading.Lock()
_instance: Optional[ContentFilter] = None


def get_content_filter() -> ContentFilter:
    """获取全局 ContentFilter 单例实例。

    使用双重检查锁定（double-checked locking）确保线程安全。

    Returns:
        ContentFilter: 全局唯一的 ContentFilter 实例。
    """
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                governance_repo = GovernanceRepository()
                _instance = ContentFilter(governance_repo=governance_repo)
                logger.debug("ContentFilter singleton instance created")
    return _instance


def invalidate_content_filter_cache() -> None:
    """刷新全局 ContentFilter 单例的缓存。

    在规则 CRUD 操作后调用，确保新规则立即生效。
    """
    global _instance
    with _lock:
        if _instance is not None:
            _instance.invalidate_cache()
            logger.debug("ContentFilter singleton cache invalidated")