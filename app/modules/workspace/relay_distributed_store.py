"""Distributed relay state store for terminal and VSCode WebSocket HA.

This module provides Redis-backed storage for relay state, enabling
cross-Pod awareness of which Pod owns a live relay WebSocket connection.

Key features:
- Circuit breaker for Redis failure handling
- TTL-based key expiration (300s)
- Heartbeat mechanism for active relays
- Graceful degradation to in-memory mode when Redis is unavailable
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Redis key prefix for relay state
RELAY_KEY_PREFIX = "relay:"

# Close frame code for redirect
REDIRECT_CLOSE_CODE = 3010

# Close frame code for relay disconnected
RELAY_DISCONNECTED_CODE = 1012

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 30

# Key TTL (seconds)
KEY_TTL = 300


class RelayStatus(str, Enum):
    """Relay connection status."""

    ACTIVE = "active"
    PENDING = "pending"
    DISCONNECTED = "disconnected"


class RelayType(str, Enum):
    """Type of relay connection."""

    TERMINAL = "terminal"
    VSCODE = "vscode"


@dataclass
class RelayState:
    """State of a relay connection."""

    owner_pod: str
    owner_namespace: str
    status: RelayStatus
    registered_at: str
    last_heartbeat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "owner_pod": self.owner_pod,
            "owner_namespace": self.owner_namespace,
            "status": self.status.value,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelayState:
        """Create from dictionary."""
        return cls(
            owner_pod=data["owner_pod"],
            owner_namespace=data["owner_namespace"],
            status=RelayStatus(data["status"]),
            registered_at=data["registered_at"],
            last_heartbeat=data.get("last_heartbeat"),
        )


class CircuitBreaker:
    """Circuit breaker for Redis failure handling.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Redis is unavailable, requests are rejected
    - HALF_OPEN: Testing if Redis has recovered
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening.
            recovery_timeout: Seconds to wait before attempting recovery.
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = "closed"  # closed, open, half_open
        self._lock = threading.Lock()

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._last_failure_time = None

    def record_failure(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = "open"

    def is_open(self) -> bool:
        """Check if circuit breaker is open (Redis unavailable)."""
        with self._lock:
            if self._state == "open":
                # Check if we should transition to half_open
                if (
                    self._last_failure_time
                    and time.time() - self._last_failure_time >= self.recovery_timeout
                ):
                    self._state = "half_open"
                    return False  # Allow one request through
                return True
            return False

    def is_available(self) -> bool:
        """Check if Redis is available (circuit closed or half_open)."""
        return not self.is_open()

    @property
    def state(self) -> str:
        """Get current state."""
        with self._lock:
            return self._state


class RelayDistributedStore:
    """Distributed store for relay state backed by Redis.

    Provides cross-Pod awareness of relay ownership. When Redis is unavailable,
    gracefully degrades to None responses, allowing local-only mode.
    """

    def __init__(self, redis_client: Any = None, pod_name: str | None = None):
        """
        Initialize the distributed relay store.

        Args:
            redis_client: Optional Redis client. If None, will be lazy-initialized.
            pod_name: Pod name for owner identification. Defaults to POD_NAME env var.
        """
        self._redis_client = redis_client
        # pod_name is always str due to fallbacks: parameter -> env var -> "unknown"
        self._pod_name: str = pod_name or os.environ.get("POD_NAME", "") or "unknown"
        self._pod_namespace: str = os.environ.get("POD_NAMESPACE", "") or "open-ace"
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )
        self._initialized = False

    def _get_redis(self) -> Any | None:
        """Get Redis client (lazy initialization)."""
        if self._redis_client is not None:
            return self._redis_client

        try:
            import redis  # type: ignore[import-untyped]

            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            redis_password = os.environ.get("REDIS_PASSWORD") or None
            redis_db = int(os.environ.get("REDIS_DB", "0"))

            self._redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
            # Test connection
            self._redis_client.ping()
            self._initialized = True
            logger.info(
                "RelayDistributedStore: Redis client initialized (host=%s, port=%d)",
                redis_host,
                redis_port,
            )
            return self._redis_client
        except ImportError:
            logger.warning("RelayDistributedStore: redis package not installed")
            return None
        except Exception as e:
            logger.warning("RelayDistributedStore: Redis connection failed: %s", e)
            self._circuit_breaker.record_failure()
            return None

    def _make_key(self, relay_type: RelayType, relay_id: str) -> str:
        """Create Redis key for a relay."""
        return f"{RELAY_KEY_PREFIX}{relay_type.value}:{relay_id}"

    def is_redis_available(self) -> bool:
        """Check if Redis is available (circuit breaker state)."""
        return self._circuit_breaker.is_available()

    def register_relay(
        self,
        relay_type: RelayType,
        relay_id: str,
    ) -> bool:
        """
        Register a relay connection in Redis.

        Uses SETNX to ensure atomic registration. Only succeeds if no other
        Pod has already registered.

        Args:
            relay_type: Type of relay (terminal or vscode).
            relay_id: Unique identifier for the relay.

        Returns:
            True if registration succeeded, False if Redis unavailable or
            relay already registered by another Pod.
        """
        if not self._circuit_breaker.is_available():
            logger.debug(
                "RelayDistributedStore: circuit breaker open, skipping register for %s:%s",
                relay_type.value,
                relay_id[:8],
            )
            return False

        redis = self._get_redis()
        if redis is None:
            return False

        key = self._make_key(relay_type, relay_id)
        now = time.time()
        registered_at = self._format_timestamp(now)

        state = RelayState(
            owner_pod=self._pod_name,
            owner_namespace=self._pod_namespace,
            status=RelayStatus.ACTIVE,
            registered_at=registered_at,
            last_heartbeat=registered_at,
        )

        try:
            # Use SETNX for atomic registration
            # SET key value NX EX ttl
            success = redis.set(
                key,
                json.dumps(state.to_dict()),
                nx=True,
                ex=KEY_TTL,
            )

            if success:
                logger.info(
                    "RelayDistributedStore: registered %s relay %s (pod=%s)",
                    relay_type.value,
                    relay_id[:8],
                    self._pod_name,
                )
                self._circuit_breaker.record_success()
                return True
            else:
                logger.debug(
                    "RelayDistributedStore: relay %s:%s already registered",
                    relay_type.value,
                    relay_id[:8],
                )
                self._circuit_breaker.record_success()
                return False

        except Exception as e:
            logger.warning(
                "RelayDistributedStore: register failed for %s:%s: %s",
                relay_type.value,
                relay_id[:8],
                e,
            )
            self._circuit_breaker.record_failure()
            return False

    def get_relay_owner(
        self,
        relay_type: RelayType,
        relay_id: str,
    ) -> RelayState | None:
        """
        Get the owner of a relay connection.

        Args:
            relay_type: Type of relay (terminal or vscode).
            relay_id: Unique identifier for the relay.

        Returns:
            RelayState if relay is registered, None otherwise.
            Returns None if Redis is unavailable (graceful degradation).
        """
        if not self._circuit_breaker.is_available():
            logger.debug(
                "RelayDistributedStore: circuit breaker open, returning None for %s:%s",
                relay_type.value,
                relay_id[:8],
            )
            return None

        redis = self._get_redis()
        if redis is None:
            return None

        key = self._make_key(relay_type, relay_id)

        try:
            data = redis.get(key)
            if data is None:
                return None

            state_dict = json.loads(data)
            self._circuit_breaker.record_success()
            return RelayState.from_dict(state_dict)

        except Exception as e:
            logger.warning(
                "RelayDistributedStore: get failed for %s:%s: %s",
                relay_type.value,
                relay_id[:8],
                e,
            )
            self._circuit_breaker.record_failure()
            return None

    def unregister_relay(
        self,
        relay_type: RelayType,
        relay_id: str,
    ) -> bool:
        """
        Unregister a relay connection.

        Only the owner Pod can unregister. Other Pods' attempts are ignored.

        Args:
            relay_type: Type of relay (terminal or vscode).
            relay_id: Unique identifier for the relay.

        Returns:
            True if unregistration succeeded, False otherwise.
        """
        if not self._circuit_breaker.is_available():
            logger.debug(
                "RelayDistributedStore: circuit breaker open, skipping unregister for %s:%s",
                relay_type.value,
                relay_id[:8],
            )
            return False

        redis = self._get_redis()
        if redis is None:
            return False

        key = self._make_key(relay_type, relay_id)

        try:
            # Check if we are the owner
            state = self.get_relay_owner(relay_type, relay_id)
            if state is None:
                # Already unregistered or expired
                return True

            if state.owner_pod != self._pod_name:
                logger.debug(
                    "RelayDistributedStore: cannot unregister %s:%s, owner is %s",
                    relay_type.value,
                    relay_id[:8],
                    state.owner_pod,
                )
                return False

            # Delete the key
            redis.delete(key)
            logger.info(
                "RelayDistributedStore: unregistered %s relay %s",
                relay_type.value,
                relay_id[:8],
            )
            self._circuit_breaker.record_success()
            return True

        except Exception as e:
            logger.warning(
                "RelayDistributedStore: unregister failed for %s:%s: %s",
                relay_type.value,
                relay_id[:8],
                e,
            )
            self._circuit_breaker.record_failure()
            return False

    def heartbeat(
        self,
        relay_type: RelayType,
        relay_id: str,
    ) -> bool:
        """
        Send a heartbeat to extend the relay's TTL.

        Updates last_heartbeat timestamp and extends TTL by KEY_TTL seconds.

        Args:
            relay_type: Type of relay (terminal or vscode).
            relay_id: Unique identifier for the relay.

        Returns:
            True if heartbeat succeeded, False otherwise.
        """
        if not self._circuit_breaker.is_available():
            return False

        redis = self._get_redis()
        if redis is None:
            return False

        key = self._make_key(relay_type, relay_id)

        try:
            # Check if we are the owner
            state = self.get_relay_owner(relay_type, relay_id)
            if state is None or state.owner_pod != self._pod_name:
                logger.debug(
                    "RelayDistributedStore: heartbeat skipped for %s:%s, not owner",
                    relay_type.value,
                    relay_id[:8],
                )
                return False

            # Update state with new heartbeat time
            state.last_heartbeat = self._format_timestamp(time.time())

            # Set with new TTL
            redis.set(
                key,
                json.dumps(state.to_dict()),
                ex=KEY_TTL,
            )

            logger.debug(
                "RelayDistributedStore: heartbeat for %s:%s, TTL extended to %ds",
                relay_type.value,
                relay_id[:8],
                KEY_TTL,
            )
            self._circuit_breaker.record_success()
            return True

        except Exception as e:
            logger.warning(
                "RelayDistributedStore: heartbeat failed for %s:%s: %s",
                relay_type.value,
                relay_id[:8],
                e,
            )
            self._circuit_breaker.record_failure()
            return False

    def set_pending(
        self,
        relay_type: RelayType,
        relay_id: str,
    ) -> bool:
        """
        Set relay status to pending (waiting for agent relay).

        This is called when a browser connects before the agent relay.

        Args:
            relay_type: Type of relay (terminal or vscode).
            relay_id: Unique identifier for the relay.

        Returns:
            True if pending status was set, False otherwise.
        """
        if not self._circuit_breaker.is_available():
            return False

        redis = self._get_redis()
        if redis is None:
            return False

        key = self._make_key(relay_type, relay_id)
        now = time.time()

        state = RelayState(
            owner_pod=self._pod_name,
            owner_namespace=self._pod_namespace,
            status=RelayStatus.PENDING,
            registered_at=self._format_timestamp(now),
        )

        try:
            # Use SETNX to avoid overwriting an existing relay
            success = redis.set(
                key,
                json.dumps(state.to_dict()),
                nx=True,
                ex=KEY_TTL,
            )

            if success:
                logger.info(
                    "RelayDistributedStore: set pending for %s:%s (pod=%s)",
                    relay_type.value,
                    relay_id[:8],
                    self._pod_name,
                )
            self._circuit_breaker.record_success()
            return bool(success)

        except Exception as e:
            logger.warning(
                "RelayDistributedStore: set_pending failed for %s:%s: %s",
                relay_type.value,
                relay_id[:8],
                e,
            )
            self._circuit_breaker.record_failure()
            return False

    @staticmethod
    def _format_timestamp(ts: float) -> str:
        """Format a Unix timestamp as ISO 8601 string."""
        from datetime import datetime, timezone

        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None).isoformat()


# Global singleton instance
_relay_distributed_store: RelayDistributedStore | None = None
_relay_distributed_store_lock = threading.Lock()


def get_relay_distributed_store() -> RelayDistributedStore:
    """Get the global RelayDistributedStore instance."""
    global _relay_distributed_store
    if _relay_distributed_store is None:
        with _relay_distributed_store_lock:
            if _relay_distributed_store is None:
                _relay_distributed_store = RelayDistributedStore()
    return _relay_distributed_store
