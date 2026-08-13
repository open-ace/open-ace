"""
Open ACE - Rate Limit Middleware

API 速率限制中间件。
"""

import functools
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

from flask import g, request

logger = logging.getLogger(__name__)


class RateLimiter:
    """速率限制器"""

    def __init__(self):
        """初始化速率限制器"""
        # 存储每个用户的请求记录
        # 结构: {key: [(timestamp, count), ...]}
        self._requests: dict[str, list[tuple[float, int]]] = defaultdict(list)
        self._lock = Lock()

        # 清理间隔（秒）
        self._cleanup_interval = 60
        self._last_cleanup = time.time()

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        """
        检查是否允许请求。

        Args:
            key: 限制键（通常是用户ID + API路径）
            max_requests: 时间窗口内最大请求数
            window: 时间窗口（秒）

        Returns:
            是否允许
        """
        now = time.time()
        cutoff = now - window

        with self._lock:
            # 清理过期记录
            self._cleanup_expired(cutoff)

            # 获取该键的请求记录
            requests = self._requests[key]

            # 移除时间窗口外的请求
            requests[:] = [(ts, cnt) for ts, cnt in requests if ts > cutoff]

            # 计算当前窗口内的请求数
            current_count = sum(cnt for _, cnt in requests)

            # 检查是否超过限制
            if current_count >= max_requests:
                return False

            # 记录本次请求
            requests.append((now, 1))

            return True

    def _cleanup_expired(self, cutoff: float):
        """清理过期记录"""
        # 定期清理所有键的过期记录
        if time.time() - self._last_cleanup > self._cleanup_interval:
            for key in list(self._requests.keys()):
                self._requests[key][:] = [
                    (ts, cnt) for ts, cnt in self._requests[key] if ts > cutoff
                ]
                # 如果列表为空，删除该键
                if not self._requests[key]:
                    del self._requests[key]

            self._last_cleanup = time.time()

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        """
        获取剩余请求数。

        Args:
            key: 限制键
            max_requests: 最大请求数
            window: 时间窗口（秒）

        Returns:
            剩余请求数
        """
        now = time.time()
        cutoff = now - window

        with self._lock:
            requests = self._requests.get(key, [])
            current_count = sum(cnt for ts, cnt in requests if ts > cutoff)
            return max(0, max_requests - current_count)


# 全局速率限制器实例
_rate_limiter = RateLimiter()


def rate_limit(max_requests: int = 10, window: int = 60):
    """
    装饰器：限制 API 请求速率。

    Args:
        max_requests: 时间窗口内最大请求数
        window: 时间窗口（秒）

    Returns:
        装饰器函数
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # 生成限制键
            user_id = g.user.get("id") if hasattr(g, "user") and g.user else "anonymous"
            endpoint = request.endpoint or request.path
            key = f"rate_limit:{user_id}:{endpoint}"

            # 检查速率限制
            if not _rate_limiter.is_allowed(key, max_requests, window):
                remaining = _rate_limiter.get_remaining(key, max_requests, window)
                logger.warning(
                    f"Rate limit exceeded: user={user_id}, endpoint={endpoint}, "
                    f"max_requests={max_requests}, window={window}s"
                )
                return {
                    "error": "Rate limit exceeded",
                    "message": f"Maximum {max_requests} requests per {window} seconds",
                    "remaining": remaining,
                }, 429

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def get_rate_limit_headers(key: str, max_requests: int, window: int) -> dict[str, str]:
    """
    获取速率限制响应头。

    Args:
        key: 限制键
        max_requests: 最大请求数
        window: 时间窗口（秒）

    Returns:
        响应头字典
    """
    remaining = _rate_limiter.get_remaining(key, max_requests, window)
    return {
        "X-RateLimit-Limit": str(max_requests),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(window),
    }
