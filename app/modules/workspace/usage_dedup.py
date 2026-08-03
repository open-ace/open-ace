"""
Open ACE - Usage Dedup Module

Request-level deduplication for usage recording.
Issue #2184: Multi-provider usage recording with deduplication.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from app.modules.workspace.usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)

# Try to import cachetools, fallback to simple dict if not available
try:
    from cachetools import TTLCache

    HAS_CACHETOOLS = True
except ImportError:
    HAS_CACHETOOLS = False
    logger.warning("cachetools not available, using simple dict-based dedup cache")


class SimpleTTLCache:
    """Simple TTL cache fallback when cachetools is not available."""

    def __init__(self, maxsize: int = 10000, ttl: float = 300):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            self._cleanup()
            return key in self._cache

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._cleanup()
            self._cache[key] = (value, time.time() + self._ttl)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            self._cleanup()
            if key in self._cache:
                return self._cache[key][0]
            raise KeyError(key)

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, (_, exp) in self._cache.items() if exp < now]
        for k in expired:
            del self._cache[k]

        # Also trim if over maxsize
        if len(self._cache) > self._maxsize:
            # Remove oldest entries (approximate LRU)
            keys_to_remove = list(self._cache.keys())[: len(self._cache) - self._maxsize]
            for k in keys_to_remove:
                del self._cache[k]


class UsageDedupCache:
    """Two-level deduplication cache for usage recording.

    Level 1: Strict match by request_id
    Level 2: Loose match by composite key (session, provider, model, tokens, timestamp)

    Thread-safe: uses threading.Lock for all operations.
    """

    def __init__(self, maxsize: int = 10000, ttl: float = 300):
        """Initialize dedup cache.

        Args:
            maxsize: Maximum number of entries per cache level.
            ttl: Time-to-live in seconds (default 5 minutes).
        """
        cache_class = TTLCache if HAS_CACHETOOLS else SimpleTTLCache
        self._request_id_cache = cache_class(maxsize=maxsize, ttl=ttl)
        self._composite_cache = cache_class(maxsize=maxsize, ttl=ttl)
        self._lock = threading.Lock()

    def check_and_record(self, evidence: UsageEvidence) -> UsageEvidence | None:
        """Check if evidence is a duplicate and record if not.

        Args:
            evidence: Usage evidence to check.

        Returns:
            Existing evidence if duplicate, None if new (and recorded).
        """
        with self._lock:
            # Level 1: Strict match by request_id
            if evidence.request_id:
                request_id_key = f"{evidence.request_id}"
                if request_id_key in self._request_id_cache:
                    logger.debug(
                        "Duplicate usage detected (request_id): request_id=%s, session_id=%s",
                        evidence.request_id,
                        evidence.session_id,
                    )
                    return self._request_id_cache[request_id_key]

                # Record new entry
                self._request_id_cache[request_id_key] = evidence
                return None

            # Level 2: Loose match by composite key
            composite_key = self._compute_composite_key(evidence)
            if composite_key in self._composite_cache:
                logger.debug(
                    "Duplicate usage detected (composite): session_id=%s, provider=%s",
                    evidence.session_id,
                    evidence.provider,
                )
                return self._composite_cache[composite_key]

            # Record new entry
            self._composite_cache[composite_key] = evidence
            return None

    def _compute_composite_key(self, evidence: UsageEvidence) -> str:
        """Compute composite key for loose deduplication.

        Includes timestamp bucket (current minute) to avoid cross-request collisions.

        Args:
            evidence: Usage evidence.

        Returns:
            Composite key string.
        """
        timestamp_bucket = int(time.time() // 60)  # Current minute
        key_data = (
            f"{evidence.session_id}|"
            f"{evidence.provider}|"
            f"{evidence.model or 'none'}|"
            f"{evidence.input_tokens}|"
            f"{evidence.output_tokens}|"
            f"{timestamp_bucket}"
        )
        return hashlib.md5(key_data.encode()).hexdigest()

    def clear(self) -> None:
        """Clear all entries from cache."""
        with self._lock:
            if HAS_CACHETOOLS:
                self._request_id_cache.clear()
                self._composite_cache.clear()
            else:
                self._request_id_cache._cache.clear()
                self._composite_cache._cache.clear()

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics.
        """
        with self._lock:
            if HAS_CACHETOOLS:
                return {
                    "request_id_cache_size": len(self._request_id_cache),
                    "composite_cache_size": len(self._composite_cache),
                }
            else:
                return {
                    "request_id_cache_size": len(self._request_id_cache._cache),
                    "composite_cache_size": len(self._composite_cache._cache),
                }


# Module-level singleton for process-wide deduplication
_dedup_cache: UsageDedupCache | None = None
_dedup_cache_lock = threading.Lock()


def get_dedup_cache() -> UsageDedupCache:
    """Get the process-wide dedup cache singleton.

    Returns:
        UsageDedupCache instance.
    """
    global _dedup_cache
    if _dedup_cache is None:
        with _dedup_cache_lock:
            if _dedup_cache is None:
                _dedup_cache = UsageDedupCache()
    return _dedup_cache


def reset_dedup_cache_for_tests() -> None:
    """Reset the dedup cache singleton for tests."""
    global _dedup_cache
    with _dedup_cache_lock:
        _dedup_cache = None