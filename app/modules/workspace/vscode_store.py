"""In-memory store for VSCode (code-server) session info with TTL-based cleanup."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Entries older than this (seconds) are considered stale and removed.
TTL_SECONDS = 24 * 3600  # 24 hours
CLEANUP_INTERVAL = 10 * 60  # 10 minutes
MAX_ENTRIES = 1000


class VSCodeInfoStore:
    """Thread-safe store keyed by (machine_id, vscode_id)."""

    def __init__(self, ttl: float = TTL_SECONDS):
        self._lock = threading.Lock()
        self._store: dict[tuple[str, str], dict] = {}
        self._vscode_index: dict[str, str] = {}
        self._ttl = ttl
        self._cleanup_timer: threading.Timer | None = None
        self._timer_started = False

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

    def put(self, machine_id: str, vscode_id: str, info: dict) -> None:
        with self._lock:
            # Lazy-start cleanup timer on first put to avoid spawning a
            # background thread at module import time.
            if not self._timer_started:
                self._timer_started = True
                self._schedule_cleanup()

            info["_updated_at"] = time.time()
            self._store[(machine_id, vscode_id)] = info
            self._vscode_index[vscode_id] = machine_id
            # Evict oldest entries if over capacity
            if len(self._store) > MAX_ENTRIES:
                oldest = sorted(self._store.items(), key=lambda kv: kv[1].get("_updated_at", 0))
                for k, _ in oldest[: len(self._store) - MAX_ENTRIES]:
                    del self._store[k]
                    self._vscode_index.pop(k[1], None)

    def get(self, machine_id: str, vscode_id: str) -> dict | None:
        with self._lock:
            return self._store.get((machine_id, vscode_id))

    def find_by_vscode_id(self, vscode_id: str) -> tuple[str, dict] | None:
        """Find VSCode info by vscode_id when machine_id is not in the request path."""
        with self._lock:
            machine_id = self._vscode_index.get(vscode_id)
            if machine_id:
                info = self._store.get((machine_id, vscode_id))
                if info is not None:
                    return machine_id, info
                self._vscode_index.pop(vscode_id, None)
        return None

    def pop(self, machine_id: str, vscode_id: str) -> dict | None:
        with self._lock:
            self._vscode_index.pop(vscode_id, None)
            return self._store.pop((machine_id, vscode_id), None)

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
                self._vscode_index.pop(k[1], None)
                removed += 1
        if removed:
            logger.info("Cleaned up %d stale VSCode entries", removed)
        return removed


# Module-level singleton — cleanup timer is started lazily on first use
# to avoid spawning a background thread at import time.
vscode_info_store = VSCodeInfoStore()
