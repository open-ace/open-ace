"""In-memory store for terminal session info with TTL-based cleanup."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Entries older than this (seconds) are considered stale and removed.
TTL_SECONDS = 24 * 3600  # 24 hours
CLEANUP_INTERVAL = 10 * 60  # 10 minutes
MAX_ENTRIES = 1000


class TerminalInfoStore:
    """Thread-safe store keyed by (machine_id, terminal_id)."""

    def __init__(self, ttl: float = TTL_SECONDS):
        self._lock = threading.Lock()
        self._store: dict[tuple[str, str], dict] = {}
        self._ttl = ttl
        self._cleanup_timer: threading.Timer | None = None

    def start_cleanup_timer(self) -> None:
        """Start periodic cleanup of stale entries."""
        self._schedule_cleanup()

    def stop_cleanup_timer(self) -> None:
        """Stop the periodic cleanup timer."""
        if self._cleanup_timer:
            self._cleanup_timer.cancel()
            self._cleanup_timer = None

    def _schedule_cleanup(self) -> None:
        self._cleanup_timer = threading.Timer(CLEANUP_INTERVAL, self._cleanup_loop)
        self._cleanup_timer.daemon = True
        self._cleanup_timer.start()

    def _cleanup_loop(self) -> None:
        self.cleanup_stale()
        self._schedule_cleanup()

    def put(self, machine_id: str, terminal_id: str, info: dict) -> None:
        with self._lock:
            info["_updated_at"] = time.time()
            self._store[(machine_id, terminal_id)] = info
            # Evict oldest entries if over capacity
            if len(self._store) > MAX_ENTRIES:
                oldest = sorted(self._store.items(), key=lambda kv: kv[1].get("_updated_at", 0))
                for k, _ in oldest[: len(self._store) - MAX_ENTRIES]:
                    del self._store[k]

    def get(self, machine_id: str, terminal_id: str) -> dict | None:
        with self._lock:
            return self._store.get((machine_id, terminal_id))

    def pop(self, machine_id: str, terminal_id: str) -> dict | None:
        with self._lock:
            return self._store.pop((machine_id, terminal_id), None)

    def cleanup_stale(self) -> int:
        """Remove entries older than TTL. Returns number of removed entries."""
        now = time.time()
        removed = 0
        with self._lock:
            stale_keys = [
                k for k, v in self._store.items() if now - v.get("_updated_at", 0) > self._ttl
            ]
            for k in stale_keys:
                del self._store[k]
                removed += 1
        if removed:
            logger.info("Cleaned up %d stale terminal entries", removed)
        return removed


# Module-level singleton
terminal_info_store = TerminalInfoStore()
terminal_info_store.start_cleanup_timer()
