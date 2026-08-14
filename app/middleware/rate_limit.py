"""
Open ACE - Rate Limit Middleware

API 速率限制中间件。
支持多进程环境：优先使用 Redis，回退到数据库。
"""

import functools
import logging
import time
from collections.abc import Callable
from threading import Lock

from flask import g, request

logger = logging.getLogger(__name__)


class RateLimiterBackend:
    """速率限制后端抽象基类"""

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        """检查是否允许请求"""
        raise NotImplementedError

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        """获取剩余请求数"""
        raise NotImplementedError


class InMemoryRateLimiterBackend(RateLimiterBackend):
    """进程内内存速率限制（仅用于单进程开发/测试）"""

    def __init__(self):
        # 结构: {key: [(timestamp, count), ...]}
        self._requests: dict[str, list[tuple[float, int]]] = {}
        self._lock = Lock()
        self._cleanup_interval = 60
        self._last_cleanup = time.time()

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        now = time.time()
        cutoff = now - window

        with self._lock:
            self._cleanup_expired(cutoff)

            requests = self._requests.get(key, [])
            # 移除时间窗口外的请求
            requests[:] = [(ts, cnt) for ts, cnt in requests if ts > cutoff]
            self._requests[key] = requests

            current_count = sum(cnt for _, cnt in requests)

            if current_count >= max_requests:
                return False

            requests.append((now, 1))
            return True

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        now = time.time()
        cutoff = now - window

        with self._lock:
            requests = self._requests.get(key, [])
            current_count = sum(cnt for ts, cnt in requests if ts > cutoff)
            return max(0, max_requests - current_count)

    def _cleanup_expired(self, cutoff: float):
        """清理过期记录"""
        if time.time() - self._last_cleanup > self._cleanup_interval:
            for key in list(self._requests.keys()):
                self._requests[key][:] = [
                    (ts, cnt) for ts, cnt in self._requests[key] if ts > cutoff
                ]
                if not self._requests[key]:
                    del self._requests[key]
            self._last_cleanup = time.time()


class RedisRateLimiterBackend(RateLimiterBackend):
    """Redis 速率限制（用于生产环境多进程）"""

    def __init__(self, redis_client):
        self._redis = redis_client
        self._script_sha = None

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        """使用 Redis Lua 脚本实现原子性速率限制"""
        lua_script = """
        local key = KEYS[1]
        local max_requests = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])

        -- 移除窗口外的记录
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

        -- 获取当前计数
        local count = redis.call('ZCARD', key)

        if count >= max_requests then
            return 0
        end

        -- 添加新请求
        redis.call('ZADD', key, now, now .. '-' .. math.random())
        redis.call('EXPIRE', key, window)

        return 1
        """

        now = time.time()
        result = self._redis.eval(lua_script, 1, key, max_requests, window, now)
        return result == 1

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        now = time.time()
        cutoff = now - window

        # 移除过期记录并获取计数
        self._redis.zremrangebyscore(key, 0, cutoff)
        count = self._redis.zcard(key)
        return max(0, max_requests - count)


class DatabaseRateLimiterBackend(RateLimiterBackend):
    """数据库速率限制（用于生产环境多进程，无 Redis 时回退）"""

    def __init__(self, get_connection_func=None):
        """
        初始化数据库后端。

        Args:
            get_connection_func: 获取数据库连接的函数
        """
        self._get_connection = get_connection_func

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        """使用数据库实现速率限制"""
        if self._get_connection is None:
            return True  # 无数据库连接时允许请求

        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = self._get_connection() if self._get_connection else get_connection()
            cursor = conn.cursor()

            now = time.time()
            cutoff = now - window

            # 清理过期记录
            delete_sql = adapt_sql("DELETE FROM rate_limit_log WHERE timestamp < ?")
            cursor.execute(delete_sql, (cutoff,))

            # 统计当前窗口内的请求数
            count_sql = adapt_sql(
                "SELECT COUNT(*) FROM rate_limit_log WHERE key = ? AND timestamp > ?"
            )
            cursor.execute(count_sql, (key, cutoff))
            count = cursor.fetchone()[0]

            if count >= max_requests:
                conn.commit()
                return False

            # 记录本次请求
            insert_sql = adapt_sql(
                "INSERT INTO rate_limit_log (key, timestamp) VALUES (?, ?)"
            )
            cursor.execute(insert_sql, (key, now))
            conn.commit()

            return True

        except Exception as e:
            logger.error(f"Database rate limit error: {e}")
            return True  # 出错时允许请求，避免阻塞服务

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        if self._get_connection is None:
            return max_requests

        try:
            from app.repositories.database import adapt_sql, get_connection

            conn = self._get_connection() if self._get_connection else get_connection()
            cursor = conn.cursor()

            now = time.time()
            cutoff = now - window

            count_sql = adapt_sql(
                "SELECT COUNT(*) FROM rate_limit_log WHERE key = ? AND timestamp > ?"
            )
            cursor.execute(count_sql, (key, cutoff))
            count = cursor.fetchone()[0]

            return max(0, max_requests - count)

        except Exception as e:
            logger.error(f"Database rate limit error: {e}")
            return max_requests


class RateLimiter:
    """速率限制器（支持多进程环境）"""

    _instance = None
    _lock = Lock()

    def __new__(cls, backend: RateLimiterBackend | None = None):
        """单例模式，但使用共享后端支持多进程"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._backend = backend or cls._create_default_backend()
                    cls._instance = instance
        return cls._instance

    @classmethod
    def _create_default_backend(cls) -> RateLimiterBackend:
        """创建默认后端（优先 Redis，其次数据库，最后内存）"""
        # 尝试使用 Redis
        try:
            from flask import current_app

            if current_app.config.get("REDIS_URL"):
                import redis

                redis_client = redis.from_url(current_app.config["REDIS_URL"])
                return RedisRateLimiterBackend(redis_client)
        except Exception:
            pass

        # 尝试使用数据库
        try:
            from app.repositories.database import get_connection

            return DatabaseRateLimiterBackend(get_connection)
        except Exception:
            pass

        # 回退到内存（仅用于单进程开发）
        logger.warning(
            "Using in-memory rate limiter. "
            "This will not work correctly in multi-process environments. "
            "Configure Redis or database for production."
        )
        return InMemoryRateLimiterBackend()

    def is_allowed(self, key: str, max_requests: int, window: int) -> bool:
        return self._backend.is_allowed(key, max_requests, window)

    def get_remaining(self, key: str, max_requests: int, window: int) -> int:
        return self._backend.get_remaining(key, max_requests, window)

    @classmethod
    def reset_instance(cls):
        """重置单例（仅用于测试）"""
        with cls._lock:
            cls._instance = None


# 全局速率限制器实例（延迟初始化）
_rate_limiter: RateLimiter | None = None


def _get_rate_limiter() -> RateLimiter:
    """获取速率限制器实例（延迟初始化）"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limit(max_requests: int = 10, window: int = 60):
    """
    装饰器：限制 API 请求速率。

    支持多进程环境（通过 Redis 或数据库）。

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

            limiter = _get_rate_limiter()

            # 检查速率限制
            if not limiter.is_allowed(key, max_requests, window):
                remaining = limiter.get_remaining(key, max_requests, window)
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
    limiter = _get_rate_limiter()
    remaining = limiter.get_remaining(key, max_requests, window)
    return {
        "X-RateLimit-Limit": str(max_requests),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(window),
    }