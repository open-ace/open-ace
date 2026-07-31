"""
Open ACE - Secret Holder

Provides secure in-memory storage for sensitive secrets (client_secret, etc.)
to prevent accidental logging or serialization.
"""

import threading
import time
from typing import Any, cast


class SecretHolder:
    """Secure container for sensitive secrets.

    Stores an encrypted blob and decrypts on-demand using the provided
    password manager. Integrates with TTL cache mechanisms to ensure
    secrets are not kept in memory indefinitely.

    Security properties:
    - Never exposes plaintext in __repr__, __str__, or dict()
    - Raises TypeError on serialization attempts (prevents accidental logging)
    - Decrypts on-demand, not on initialization
    - Thread-safe decrypt-on-demand mechanism

    Note: Python's memory management doesn't guarantee secure zeroing.
    This class provides best-effort protection against accidental exposure.
    """

    def __init__(
        self,
        encrypted_blob: str,
        password_manager: Any,
        ttl_seconds: int = 300,
    ):
        """Initialize SecretHolder with encrypted blob and decryptor.

        Args:
            encrypted_blob: The encrypted secret value
            password_manager: Password manager instance with decrypt() method
            ttl_seconds: Time-to-live for cached plaintext (default: 5 minutes)
        """
        self._encrypted = encrypted_blob
        self._decryptor = password_manager
        self._ttl_seconds = ttl_seconds
        self._plaintext_cache: str | None = None
        self._cache_timestamp: float = 0
        self._lock = threading.Lock()

    def get(self) -> str:
        """Decrypt and return the secret value.

        Uses thread-safe on-demand decryption. Caches the result for
        the TTL duration to avoid repeated decryption overhead.

        Returns:
            Decrypted secret value (empty string if encrypted blob is empty)

        Raises:
            Exception: If decryption fails
        """
        current_time = time.time()

        with self._lock:
            # Check if cache is valid (exists and not expired)
            if (
                self._plaintext_cache is not None
                and current_time - self._cache_timestamp < self._ttl_seconds
            ):
                return self._plaintext_cache

            # Handle empty encrypted blob (return empty string immediately)
            if not self._encrypted:
                self._plaintext_cache = ""
                self._cache_timestamp = current_time
                return ""

            # Decrypt and cache
            decrypted = self._decryptor.decrypt(self._encrypted)
            self._plaintext_cache = decrypted
            self._cache_timestamp = current_time
            return cast("str", decrypted)

    def clear_cache(self) -> None:
        """Clear the cached plaintext value.

        Called by the cache eviction logic to ensure secrets are not
        kept in memory longer than necessary.
        """
        with self._lock:
            self._plaintext_cache = None
            self._cache_timestamp = 0

    def __repr__(self) -> str:
        """Return redacted representation to prevent accidental logging."""
        return "<SecretHolder (redacted)>"

    def __str__(self) -> str:
        """Return redacted string to prevent accidental logging."""
        return "<SecretHolder (redacted)>"

    def __getstate__(self) -> None:
        """Prevent pickling to avoid accidental exposure."""
        raise TypeError("SecretHolder cannot be pickled. " "Use .get() to access the secret value.")

    def __del__(self) -> None:
        """Attempt to clear memory on deletion (best effort).

        Note: Python's memory management does not guarantee secure zeroing.
        This is a best-effort protection against accidental exposure.
        """
        # Clear the cache
        self._plaintext_cache = None

        # Attempt to zero the encrypted blob memory (best effort)
        # This may not work reliably in Python due to string immutability
        # and garbage collection behavior, but we try anyway.
        try:
            import ctypes

            if self._encrypted:
                # ctypes.memset may not work on immutable strings,
                # but we try as a best-effort measure
                ctypes.memset(id(self._encrypted), 0, len(self._encrypted))
        except Exception:
            # Memory zeroing failed, which is expected for immutable strings
            pass
