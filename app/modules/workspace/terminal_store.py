"""In-memory store for terminal session info with TTL-based cleanup.

This store is process-local. Kubernetes and other multi-pod deployments must
use sticky routing for active terminal sessions until issue #1782 externalizes
the shareable remote-session runtime state.
"""

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
        self._terminal_index: dict[str, str] = {}
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
        evicted_terminal_ids: list[str] = []
        with self._lock:
            info["_updated_at"] = time.time()
            self._store[(machine_id, terminal_id)] = info
            self._terminal_index[terminal_id] = machine_id
            # Evict oldest entries if over capacity
            if len(self._store) > MAX_ENTRIES:
                oldest = sorted(self._store.items(), key=lambda kv: kv[1].get("_updated_at", 0))
                for k, _ in oldest[: len(self._store) - MAX_ENTRIES]:
                    del self._store[k]
                    self._terminal_index.pop(k[1], None)
                    evicted_terminal_ids.append(k[1])
        for evicted_terminal_id in evicted_terminal_ids:
            self._close_bridges(evicted_terminal_id)

    def get(self, machine_id: str, terminal_id: str) -> dict | None:
        with self._lock:
            return self._store.get((machine_id, terminal_id))

    def find_by_terminal_id(self, terminal_id: str) -> tuple[str, dict] | None:
        """Find terminal info by terminal ID when machine ID is not in the request path."""
        with self._lock:
            machine_id = self._terminal_index.get(terminal_id)
            if machine_id:
                info = self._store.get((machine_id, terminal_id))
                if info is not None:
                    return machine_id, info
                self._terminal_index.pop(terminal_id, None)
        return None

    def pop(self, machine_id: str, terminal_id: str) -> dict | None:
        with self._lock:
            self._terminal_index.pop(terminal_id, None)
            info = self._store.pop((machine_id, terminal_id), None)
        if info is not None:
            self._close_bridges(terminal_id)
        return info

    def cleanup_stale(self) -> int:
        """Remove entries older than TTL. Returns number of removed entries."""
        now = time.time()
        removed = 0
        stale_terminal_ids: list[str] = []
        with self._lock:
            stale_keys = [
                k for k, v in self._store.items() if now - v.get("_updated_at", 0) > self._ttl
            ]
            for k in stale_keys:
                del self._store[k]
                self._terminal_index.pop(k[1], None)
                stale_terminal_ids.append(k[1])
                removed += 1
        for terminal_id in stale_terminal_ids:
            self._close_bridges(terminal_id)
        if removed:
            logger.info("Cleaned up %d stale terminal entries", removed)
        return removed

    def _close_bridges(self, terminal_id: str) -> None:
        try:
            from app.modules.workspace.terminal_ws_bridge import close_terminal_bridges

            close_terminal_bridges(terminal_id)
        except Exception as e:
            logger.debug("Failed to close terminal bridges for %s: %s", terminal_id[:8], e)


# Module-level singleton
terminal_info_store = TerminalInfoStore()
terminal_info_store.start_cleanup_timer()
