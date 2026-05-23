"""Unit tests for cache module."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.utils.cache import (
    CacheEntry,
    CacheManager,
    MemoryCache,
    RedisCache,
    cached,
    get_cache,
    init_cache,
)


class TestCacheEntry:
    """Test CacheEntry dataclass."""

    def test_default_values(self):
        entry = CacheEntry(value="test")
        assert entry.value == "test"
        assert entry.expires_at is None
        assert entry.created_at == 0.0
        assert entry.hits == 0

    @pytest.mark.parametrize(
        "expires_at_offset,expected_expired",
        [
            (None, False),
            (3600, False),  # future
            (-10, True),  # past
            (None, True),  # special case: expires_at=0
        ],
        ids=["no_expiry", "future", "past", "zero"],
    )
    def test_is_expired(self, expires_at_offset, expected_expired):
        if expires_at_offset is None and expected_expired:
            # Special case: expires_at=0
            entry = CacheEntry(value="test", expires_at=0)
        elif expires_at_offset is None:
            entry = CacheEntry(value="test", expires_at=None)
        else:
            entry = CacheEntry(value="test", expires_at=time.time() + expires_at_offset)
        assert entry.is_expired() is expected_expired

    def test_hits_counter(self):
        entry = CacheEntry(value="test")
        assert entry.hits == 0
        entry.hits += 1
        assert entry.hits == 1


class TestMemoryCache:
    """Test MemoryCache backend."""

    def test_set_and_get(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        entry = cache.get("key1")
        assert entry is not None
        assert entry.value == "value1"

    def test_get_nonexistent(self):
        cache = MemoryCache()
        entry = cache.get("nonexistent")
        assert entry is None

    def test_set_with_ttl(self):
        cache = MemoryCache(default_ttl=60)
        cache.set("key1", "value1", ttl=1)
        entry = cache.get("key1")
        assert entry is not None
        assert entry.value == "value1"

    def test_set_with_zero_ttl(self):
        cache = MemoryCache()
        cache.set("key1", "value1", ttl=0)
        entry = cache.get("key1")
        assert entry is not None
        # Zero TTL is falsy, so default_ttl is used instead, meaning expires_at is set
        assert entry.expires_at is not None

    def test_negative_ttl_never_expires(self):
        cache = MemoryCache()
        cache.set("key1", "value1", ttl=-1)
        # ttl=-1: truthy but -1 > 0 is False, so expires_at is None (never expires)
        entry = cache.get("key1")
        assert entry is not None
        assert entry.value == "value1"

    def test_negative_ttl_entry_remains_in_cache(self):
        cache = MemoryCache()
        cache.set("key1", "value1", ttl=-1)
        # ttl=-1 results in expires_at=None (never expires), so entry remains
        assert "key1" in cache._cache

    def test_delete_existing(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        result = cache.delete("key1")
        assert result is True
        assert cache.get("key1") is None

    def test_delete_nonexistent(self):
        cache = MemoryCache()
        result = cache.delete("nonexistent")
        assert result is False

    def test_clear(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        result = cache.clear()
        assert result is True
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_exists(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        assert cache.exists("key1") is True
        assert cache.exists("nonexistent") is False

    def test_exists_negative_ttl_always_true(self):
        cache = MemoryCache()
        cache.set("key1", "value1", ttl=-1)
        # ttl=-1 results in expires_at=None (never expires), so exists returns True
        assert cache.exists("key1") is True

    def test_hit_counter(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("key1")
        cache.get("key1")
        entry = cache.get("key1")
        assert entry.hits == 4  # 4th get

    def test_stats(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("nonexistent")  # miss

        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] > 0

    def test_stats_empty(self):
        cache = MemoryCache()
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0

    def test_eviction_at_capacity(self):
        cache = MemoryCache(max_size=2, default_ttl=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Should trigger eviction

        # One entry should have been evicted
        assert len(cache._cache) <= 2

    def test_eviction_removes_expired_first(self):
        cache = MemoryCache(max_size=3, default_ttl=60)
        cache.set("key1", "value1", ttl=-1)  # expired
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        cache.set("key4", "value4")

        # Expired entry should have been removed
        assert "key1" not in cache._cache

    def test_overwrite_existing_key(self):
        cache = MemoryCache()
        cache.set("key1", "value1")
        cache.set("key1", "value2")
        entry = cache.get("key1")
        assert entry.value == "value2"

    def test_overwrite_does_not_evict(self):
        cache = MemoryCache(max_size=2, default_ttl=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key1", "updated")  # Overwrite, no eviction needed

        assert len(cache._cache) == 2
        assert cache.get("key1").value == "updated"


class TestRedisCache:
    """Test RedisCache backend."""

    @pytest.mark.parametrize(
        "prefix,key,expected",
        [
            ("openace:", "test", "openace:test"),
            ("custom:", "key", "custom:key"),
        ],
        ids=["default_prefix", "custom_prefix"],
    )
    def test_make_key(self, prefix, key, expected):
        cache = RedisCache(prefix=prefix)
        assert cache._make_key(key) == expected

    @pytest.mark.parametrize(
        "method_name,method_args,expected_result",
        [
            ("get", ("key1",), None),
            ("set", ("key1", "value1"), False),
            ("delete", ("key1",), False),
            ("clear", (), False),
            ("exists", ("key1",), False),
        ],
        ids=["get", "set", "delete", "clear", "exists"],
    )
    def test_error_returns_fallback(self, method_name, method_args, expected_result):
        cache = RedisCache()
        with patch.object(cache, "_get_client", side_effect=Exception("Connection error")):
            method = getattr(cache, method_name)
            result = method(*method_args)
            assert result == expected_result

    def test_get_success(self):
        import pickle

        cache = RedisCache()
        entry = CacheEntry(value="test_value", created_at=time.time())
        mock_client = MagicMock()
        mock_client.get.return_value = pickle.dumps(entry)

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.get("key1")
            assert result is not None
            assert result.value == "test_value"

    def test_get_none_data(self):
        cache = RedisCache()
        mock_client = MagicMock()
        mock_client.get.return_value = None

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.get("key1")
            assert result is None

    def test_set_with_ttl(self):
        import pickle

        cache = RedisCache()
        mock_client = MagicMock()

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.set("key1", "value1", ttl=300)
            assert result is True
            mock_client.setex.assert_called_once()

    def test_set_without_ttl(self):
        cache = RedisCache(default_ttl=0)
        mock_client = MagicMock()

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.set("key1", "value1")
            assert result is True
            mock_client.set.assert_called_once()

    def test_delete_success(self):
        cache = RedisCache()
        mock_client = MagicMock()

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.delete("key1")
            assert result is True

    @pytest.mark.parametrize(
        "keys,expect_delete_called",
        [
            ([b"openace:key1", b"openace:key2"], True),
            ([], False),
        ],
        ids=["with_keys", "no_keys"],
    )
    def test_clear(self, keys, expect_delete_called):
        cache = RedisCache()
        mock_client = MagicMock()
        mock_client.keys.return_value = keys

        with patch.object(cache, "_get_client", return_value=mock_client):
            result = cache.clear()
            assert result is True
            if expect_delete_called:
                mock_client.delete.assert_called_once()
            else:
                mock_client.delete.assert_not_called()

    @pytest.mark.parametrize(
        "exists_return,expected",
        [
            (1, True),
            (0, False),
        ],
        ids=["exists_true", "exists_false"],
    )
    def test_exists(self, exists_return, expected):
        cache = RedisCache()
        mock_client = MagicMock()
        mock_client.exists.return_value = exists_return

        with patch.object(cache, "_get_client", return_value=mock_client):
            assert cache.exists("key1") is expected


class TestCacheManager:
    """Test CacheManager."""

    def setup_method(self):
        get_cache().clear()

    def test_singleton_pattern(self):
        cm1 = CacheManager()
        cm2 = CacheManager()
        assert cm1 is cm2

    def test_default_memory_backend(self):
        cm = CacheManager()
        assert isinstance(cm._backend, MemoryCache)

    def test_memory_backend_operations(self):
        cm = CacheManager()
        cm.set("key1", "value1")
        assert cm.get("key1") == "value1"

    def test_delete(self):
        cm = CacheManager()
        cm.set("key1", "value1")
        assert cm.delete("key1") is True
        assert cm.get("key1") is None

    def test_clear(self):
        cm = CacheManager()
        cm.set("key1", "value1")
        assert cm.clear() is True
        assert cm.get("key1") is None

    def test_exists(self):
        cm = CacheManager()
        cm.set("key1", "value1")
        assert cm.exists("key1") is True
        assert cm.exists("nonexistent") is False

    def test_stats_memory_backend(self):
        cm = CacheManager()
        stats = cm.stats()
        assert "size" in stats
        assert "hits" in stats
        assert "misses" in stats

    def test_stats_redis_backend(self):
        cm = CacheManager()
        original_backend = cm._backend
        cm._backend = RedisCache()
        try:
            stats = cm.stats()
            assert stats["backend"] == "redis"
        finally:
            cm._backend = original_backend

    def test_get_returns_value_not_entry(self):
        cm = CacheManager()
        cm.set("key1", "value1")
        result = cm.get("key1")
        assert result == "value1"
        assert not isinstance(result, CacheEntry)

    def test_get_nonexistent_returns_none(self):
        cm = CacheManager()
        assert cm.get("nonexistent") is None

    def test_make_key_simple(self):
        key = CacheManager.make_key("prefix", "func_name", "arg1")
        assert "prefix" in key
        assert "func_name" in key
        assert "arg1" in key

    def test_make_key_with_kwargs(self):
        key = CacheManager.make_key("prefix", "func", a=1, b=2)
        assert "a=1" in key
        assert "b=2" in key

    def test_make_key_long_key_hashed(self):
        long_arg = "x" * 300
        key = CacheManager.make_key("prefix", long_arg)
        assert len(key) <= 32  # MD5 hex digest

    def test_make_key_short_key_not_hashed(self):
        key = CacheManager.make_key("prefix", "short")
        assert len(key) <= 200

    def test_redis_backend_fallback(self):
        # Singleton is already initialized with MemoryCache (fallback from Redis)
        cm = CacheManager()
        assert isinstance(cm._backend, MemoryCache)


class TestCachedDecorator:
    """Test cached decorator."""

    def setup_method(self):
        get_cache().clear()

    def test_caches_result(self):
        call_count = 0

        @cached(ttl=60)
        def expensive_func(x, y):
            nonlocal call_count
            call_count += 1
            return x + y

        result1 = expensive_func(1, 2)
        result2 = expensive_func(1, 2)
        assert result1 == 3
        assert result2 == 3
        assert call_count == 1  # Only computed once

    def test_different_args_different_cache(self):
        call_count = 0

        @cached(ttl=60)
        def func(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        assert func(1) == 2
        assert func(2) == 4
        assert call_count == 2

    def test_with_key_prefix(self):
        @cached(ttl=60, key_prefix="myprefix")
        def func(x):
            return x

        result = func(1)
        assert result == 1

    def test_cache_clear_method(self):
        call_count = 0

        @cached(ttl=60)
        def func(x):
            nonlocal call_count
            call_count += 1
            return x

        func(1)
        func.cache_clear()
        func(1)
        assert call_count == 2  # Recomputed after clear

    def test_cache_stats_method(self):
        @cached(ttl=60)
        def func(x):
            return x

        func(1)
        stats = func.cache_stats()
        assert "size" in stats

    def test_none_result_not_cached(self):
        """None results should not be cached since get returns None for misses."""
        call_count = 0

        @cached(ttl=60)
        def func(x):
            nonlocal call_count
            call_count += 1
            return None

        func(1)
        func(1)
        assert call_count == 2  # Recomputed because None is treated as cache miss

    def test_skip_args(self):
        """Test skipping arguments in cache key generation."""
        call_count = 0

        @cached(ttl=60, skip_args=[0])
        def func(self_arg, x):
            nonlocal call_count
            call_count += 1
            return x

        result = func("self_value", 1)
        assert result == 1
        # Even with different first arg, should use cached value since it's skipped
        result2 = func("different_self", 1)
        assert result2 == 1
        assert call_count == 1


class TestGetCache:
    """Test get_cache function."""

    def test_returns_cache_manager(self):
        result = get_cache()
        assert isinstance(result, CacheManager)

    def test_returns_same_instance(self):
        c1 = get_cache()
        c2 = get_cache()
        assert c1 is c2


class TestInitCache:
    """Test init_cache function."""

    def test_init_memory_backend(self):
        # init_cache returns the existing singleton (already MemoryCache)
        cm = init_cache(backend="memory")
        assert isinstance(cm, CacheManager)

    def test_init_returns_cache_manager(self):
        cm = init_cache(backend="memory")
        assert isinstance(cm, CacheManager)

    def test_init_sets_global(self):
        import app.utils.cache as cache_mod

        cm = init_cache(backend="memory")
        assert cache_mod._cache is cm
