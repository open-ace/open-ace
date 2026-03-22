#!/usr/bin/env python3
"""
Open ACE - Cache Module

Provides caching capabilities for improved performance.
Supports both in-memory and Redis caching.
"""

import hashlib
import logging
import os
import pickle
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class CacheEntry:
    """Cache entry with metadata."""
    value: Any
    expires_at: Optional[float] = None
    created_at: float = 0.0
    hits: int = 0

    def is_expired(self) -> bool:
        """Check if entry is expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class CacheBackend(ABC):
    """Abstract cache backend."""

    @abstractmethod
    def get(self, key: str) -> Optional[CacheEntry]:
        """Get cache entry by key."""
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set cache entry."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete cache entry."""
        pass

    @abstractmethod
    def clear(self) -> bool:
        """Clear all cache entries."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists."""
        pass


class MemoryCache(CacheBackend):
    """In-memory cache backend with LRU eviction."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        """
        Initialize memory cache.

        Args:
            max_size: Maximum number of entries.
            default_ttl: Default TTL in seconds.
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[CacheEntry]:
        """Get cache entry by key."""
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None

            entry.hits += 1
            self._hits += 1
            return entry

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set cache entry."""
        with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_size and key not in self._cache:
                self._evict()

            ttl = ttl or self.default_ttl
            expires_at = time.time() + ttl if ttl > 0 else None

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
                created_at=time.time(),
            )

            return True

    def delete(self, key: str) -> bool:
        """Delete cache entry."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> bool:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            return True

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._cache[key]
                return False
            return True

    def _evict(self) -> None:
        """Evict least recently used entries."""
        if not self._cache:
            return

        # Remove expired entries first
        expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
        for key in expired_keys:
            del self._cache[key]

        # If still at capacity, remove least hit entries
        while len(self._cache) >= self.max_size:
            if not self._cache:
                break
            # Remove entry with fewest hits
            lru_key = min(self._cache.keys(), key=lambda k: self._cache[k].hits)
            del self._cache[lru_key]

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0

            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': round(hit_rate, 4),
                'entries': len(self._cache),
            }


class RedisCache(CacheBackend):
    """Redis cache backend."""

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        prefix: str = 'openace:',
        default_ttl: int = 300
    ):
        """
        Initialize Redis cache.

        Args:
            host: Redis host.
            port: Redis port.
            db: Redis database number.
            password: Redis password.
            prefix: Key prefix.
            default_ttl: Default TTL in seconds.
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._client = None

    def _get_client(self):
        """Get Redis client (lazy initialization)."""
        if self._client is None:
            try:
                import redis
                self._client = redis.Redis(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    password=self.password,
                    decode_responses=False,
                )
            except ImportError:
                logger.warning("Redis package not installed, falling back to memory cache")
                raise
        return self._client

    def _make_key(self, key: str) -> str:
        """Create prefixed key."""
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Optional[CacheEntry]:
        """Get cache entry by key."""
        try:
            client = self._get_client()
            data = client.get(self._make_key(key))

            if data is None:
                return None

            entry = pickle.loads(data)
            return entry

        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set cache entry."""
        try:
            client = self._get_client()
            ttl = ttl or self.default_ttl

            entry = CacheEntry(
                value=value,
                created_at=time.time(),
            )

            data = pickle.dumps(entry)

            if ttl > 0:
                client.setex(self._make_key(key), ttl, data)
            else:
                client.set(self._make_key(key), data)

            return True

        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete cache entry."""
        try:
            client = self._get_client()
            client.delete(self._make_key(key))
            return True
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False

    def clear(self) -> bool:
        """Clear all cache entries with prefix."""
        try:
            client = self._get_client()
            keys = client.keys(f"{self.prefix}*")
            if keys:
                client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"Redis clear error: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            client = self._get_client()
            return bool(client.exists(self._make_key(key)))
        except Exception as e:
            logger.error(f"Redis exists error: {e}")
            return False


class CacheManager:
    """
    Cache manager for Open ACE.

    Provides a unified interface for caching with automatic backend selection.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        backend: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize cache manager.

        Args:
            backend: Cache backend ('memory' or 'redis').
            **kwargs: Backend-specific arguments.
        """
        if hasattr(self, '_initialized') and self._initialized:
            return

        backend = backend or os.environ.get('CACHE_BACKEND', 'memory')

        if backend == 'redis':
            try:
                self._backend = RedisCache(
                    host=kwargs.get('host', os.environ.get('REDIS_HOST', 'localhost')),
                    port=kwargs.get('port', int(os.environ.get('REDIS_PORT', 6379))),
                    db=kwargs.get('db', 0),
                    password=kwargs.get('password', os.environ.get('REDIS_PASSWORD')),
                    default_ttl=kwargs.get('default_ttl', 300),
                )
                # Test connection
                self._backend._get_client().ping()
                logger.info("Using Redis cache backend")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis: {e}, falling back to memory cache")
                self._backend = MemoryCache(
                    max_size=kwargs.get('max_size', 1000),
                    default_ttl=kwargs.get('default_ttl', 300),
                )
        else:
            self._backend = MemoryCache(
                max_size=kwargs.get('max_size', 1000),
                default_ttl=kwargs.get('default_ttl', 300),
            )
            logger.info("Using in-memory cache backend")

        self._initialized = True

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        entry = self._backend.get(key)
        return entry.value if entry else None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache."""
        return self._backend.set(key, value, ttl)

    def delete(self, key: str) -> bool:
        """Delete value from cache."""
        return self._backend.delete(key)

    def clear(self) -> bool:
        """Clear all cache."""
        return self._backend.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        return self._backend.exists(key)

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if isinstance(self._backend, MemoryCache):
            return self._backend.stats()
        return {'backend': 'redis'}

    @staticmethod
    def make_key(*args, **kwargs) -> str:
        """
        Generate a cache key from arguments.

        Args:
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            str: Cache key.
        """
        key_parts = [str(arg) for arg in args]
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        key_string = ":".join(key_parts)

        # Hash long keys
        if len(key_string) > 200:
            return hashlib.md5(key_string.encode()).hexdigest()

        return key_string


def cached(
    ttl: int = 300,
    key_prefix: str = '',
    skip_args: Optional[list] = None
):
    """
    Decorator for caching function results.

    Args:
        ttl: Time to live in seconds.
        key_prefix: Prefix for cache key.
        skip_args: Arguments to skip in key generation.

    Returns:
        Decorator function.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        cache = CacheManager()

        def wrapper(*args, **kwargs) -> T:
            # Skip self/cls for methods
            effective_args = args
            if skip_args:
                effective_args = tuple(
                    a for i, a in enumerate(args) if i not in skip_args
                )

            # Generate cache key
            key = CacheManager.make_key(key_prefix, func.__name__, *effective_args, **kwargs)

            # Try to get from cache
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value

            # Compute and cache
            result = func(*args, **kwargs)
            cache.set(key, result, ttl)

            return result

        # Add cache control methods
        wrapper.cache_clear = lambda: cache.clear()
        wrapper.cache_stats = lambda: cache.stats()

        return wrapper

    return decorator


# Global cache instance
_cache: Optional[CacheManager] = None


def get_cache() -> CacheManager:
    """Get the global cache instance."""
    global _cache
    if _cache is None:
        _cache = CacheManager()
    return _cache


def init_cache(backend: str = 'memory', **kwargs) -> CacheManager:
    """
    Initialize the global cache.

    Args:
        backend: Cache backend ('memory' or 'redis').
        **kwargs: Backend-specific arguments.

    Returns:
        CacheManager: Cache manager instance.
    """
    global _cache
    _cache = CacheManager(backend=backend, **kwargs)
    return _cache
