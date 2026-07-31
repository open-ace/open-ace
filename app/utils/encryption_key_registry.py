"""
Open ACE - Encryption Key Registry

Provides centralized management of encryption keys with support for:
- Multiple key versions (active + deprecated + revoked)
- Hot reload via environment variable changes
- Automatic config version hashing for multi-replica synchronization
- Thread-safe operations with lazy time-window checks
- Backward compatibility with single-key configuration

This module replaces the per-module key management approach with a global
singleton that coordinates all encryption/decryption operations across:
- API key encryption (api_key_proxy.py)
- SMTP password encryption (smtp_crypto.py)
- SSO client_secret encryption (sso/manager.py)
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, cast

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

# Maximum number of keys allowed in configuration
MAX_KEY_COUNT = 5

# Time window for lazy hot-reload checks (seconds)
HOT_RELOAD_CHECK_INTERVAL_SECONDS = 5.0

# Key ID for legacy format (no key_id prefix)
LEGACY_KEY_ID = 0

# Fernet ciphertext prefix format: <version_byte><key_id_byte>.
# Version byte is always 0x01 for now (future expansion).
FERNET_PREFIX_VERSION = 0x01


class KeyStatus(str, Enum):
    """Status of an encryption key."""

    ACTIVE = "active"  # Primary key for encryption
    DEPRECATED = "deprecated"  # Historical key for decryption only
    REVOKED = "revoked"  # Compromised key, emergency decryption only


@dataclass
class EncryptionKey:
    """Represents a single encryption key with metadata."""

    key_id: int
    derived_key: bytes  # sha256(key_value) derived
    status: KeyStatus
    created_at: float  # Unix timestamp


# ============================================================================
# Global Singleton
# ============================================================================

_registry_instance: Optional["EncryptionKeyRegistry"] = None
_registry_lock = threading.Lock()


def get_registry() -> "EncryptionKeyRegistry":
    """
    Get the singleton EncryptionKeyRegistry instance.

    Initializes on first call with lazy loading. Thread-safe.

    Returns:
        EncryptionKeyRegistry instance.
    """
    global _registry_instance

    if _registry_instance is None:
        with _registry_lock:
            # Double-check after acquiring lock
            if _registry_instance is None:
                _registry_instance = EncryptionKeyRegistry()

    return _registry_instance


def reset_registry() -> None:
    """
    Reset the singleton instance (for testing only).

    This function is intended for test cleanup and should not be used
    in production code.
    """
    global _registry_instance

    with _registry_lock:
        _registry_instance = None


# ============================================================================
# EncryptionKeyRegistry Class
# ============================================================================


class EncryptionKeyRegistry:
    """
    Global registry for encryption key management.

    Features:
    - Multi-key support with active/deprecated/revoked states
    - Hot reload via environment variable changes
    - Automatic config version hashing for multi-replica sync
    - Thread-safe operations with lazy time-window checks
    - Backward compatibility with single-key configuration
    """

    def __init__(self) -> None:
        """Initialize registry with keys from environment variables."""
        self._lock = threading.RLock()
        self._keys: dict[int, EncryptionKey] = {}
        self._primary_key_id: int = 0
        self._config_hash: str = ""
        self._config_version: int = 0
        self._last_check_time: float = 0.0

        # Initial load
        self._load_keys()

    def _load_keys(self) -> None:
        """
        Load encryption keys from environment variables.

        Supports two formats:
        1. Single key: OPENACE_ENCRYPTION_KEY="key-value"
        2. Multiple keys: OPENACE_ENCRYPTION_KEYS='{"keys": [...], "primary_key_id": 2}'

        Raises:
            RuntimeError: If key configuration is invalid in production.
        """
        with self._lock:
            # Try multi-key format first
            keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")

            if keys_json:
                self._load_multi_key_config(keys_json)
            else:
                # Fall back to single-key format
                self._load_single_key_config()

            # Compute config hash and version
            self._compute_config_hash_and_version()

    def _load_multi_key_config(self, keys_json: str) -> None:
        """
        Load keys from OPENACE_ENCRYPTION_KEYS JSON format.

        Args:
            keys_json: JSON string with keys array and primary_key_id.

        Raises:
            RuntimeError: If configuration is invalid.
        """
        try:
            config = json.loads(keys_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"OPENACE_ENCRYPTION_KEYS is not valid JSON: {e}")

        keys_list = config.get("keys", [])
        if not isinstance(keys_list, list):
            raise RuntimeError("OPENACE_ENCRYPTION_KEYS must have 'keys' array")

        if len(keys_list) == 0:
            raise RuntimeError("OPENACE_ENCRYPTION_KEYS must have at least one key")

        if len(keys_list) > MAX_KEY_COUNT:
            raise RuntimeError(f"OPENACE_ENCRYPTION_KEYS cannot have more than {MAX_KEY_COUNT} keys")

        # Validate primary_key_id
        primary_key_id = config.get("primary_key_id")
        if primary_key_id is None:
            raise RuntimeError("OPENACE_ENCRYPTION_KEYS must have 'primary_key_id'")

        # Parse keys
        new_keys: dict[int, EncryptionKey] = {}
        active_count = 0

        for key_info in keys_list:
            key_id = key_info.get("id")
            key_value = key_info.get("value")
            status_str = key_info.get("status", "deprecated")

            if key_id is None or key_value is None:
                raise RuntimeError("Each key must have 'id' and 'value'")

            if not isinstance(key_id, int) or not (1 <= key_id <= 255):
                raise RuntimeError(f"key_id must be an integer between 1 and 255, got {key_id}")

            if key_id in new_keys:
                raise RuntimeError(f"Duplicate key_id: {key_id}")

            # Derive key using SHA-256 (consistent with existing code)
            derived_key = hashlib.sha256(key_value.encode()).digest()

            # Parse status
            try:
                status = KeyStatus(status_str)
            except ValueError:
                raise RuntimeError(f"Invalid key status: {status_str}")

            new_keys[key_id] = EncryptionKey(
                key_id=key_id,
                derived_key=derived_key,
                status=status,
                created_at=time.time(),
            )

            if status == KeyStatus.ACTIVE:
                active_count += 1

        # Validate exactly one active key
        if active_count != 1:
            raise RuntimeError(f"Must have exactly 1 active key, found {active_count}")

        # Validate primary_key_id is active
        if primary_key_id not in new_keys:
            raise RuntimeError(f"primary_key_id {primary_key_id} not found in keys")

        if new_keys[primary_key_id].status != KeyStatus.ACTIVE:
            raise RuntimeError(f"primary_key_id {primary_key_id} must be active")

        self._keys = new_keys
        self._primary_key_id = primary_key_id

    def _load_single_key_config(self) -> None:
        """
        Load key from OPENACE_ENCRYPTION_KEY single-key format.

        Uses key_id=0 for backward compatibility.

        Raises:
            RuntimeError: If key is missing in production.
        """
        key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")

        # Check for development fallback
        from app.utils.security_env import get_encryption_key_material

        try:
            key_value = get_encryption_key_material(purpose="EncryptionKeyRegistry initialization")
        except RuntimeError:
            # In production without key - let it fail
            raise

        # Derive key
        derived_key = hashlib.sha256(key_value.encode()).digest()

        # Use key_id=0 for legacy single-key format
        self._keys = {
            0: EncryptionKey(
                key_id=0,
                derived_key=derived_key,
                status=KeyStatus.ACTIVE,
                created_at=time.time(),
            )
        }
        self._primary_key_id = 0

    def _compute_config_hash_and_version(self) -> None:
        """Compute config hash and version from current key configuration."""
        # Build config dict for hashing
        # Include hash of key values to distinguish different keys
        config_for_hash: dict = {
            "keys": [
                {
                    "id": k.key_id,
                    "status": k.status.value,
                    # Include hash of derived key to distinguish different keys
                    "key_hash": hashlib.sha256(k.derived_key).hexdigest()[:16],
                }
                for k in sorted(self._keys.values(), key=lambda x: x.key_id)
            ],
            "primary_key_id": self._primary_key_id,
        }

        # Compute SHA-256 hash of config
        config_str = json.dumps(config_for_hash, sort_keys=True)
        self._config_hash = hashlib.sha256(config_str.encode()).hexdigest()

        # Derive version from first 4 bytes of hash (32-bit)
        self._config_version = int.from_bytes(
            bytes.fromhex(self._config_hash[:8]), byteorder="big"
        )

    def reload(self) -> None:
        """
        Reload keys from environment variables (thread-safe).

        Called automatically during lazy checks or manually for immediate reload.
        """
        with self._lock:
            self._load_keys()
            logger.info(
                f"EncryptionKeyRegistry reloaded: "
                f"primary_key_id={self._primary_key_id}, "
                f"total_keys={len(self._keys)}, "
                f"config_version={self._config_version}"
            )

    def _check_and_reload_if_needed(self) -> None:
        """
        Check if hot reload is needed based on time window and config changes.

        Performance optimization: Only check environment variable hash every 5 seconds.
        """
        now = time.time()

        # Fast path: within time window, skip check
        if now - self._last_check_time < HOT_RELOAD_CHECK_INTERVAL_SECONDS:
            return

        # Slow path: time window elapsed, check config
        with self._lock:
            # Double-check after acquiring lock
            if now - self._last_check_time < HOT_RELOAD_CHECK_INTERVAL_SECONDS:
                return

            # Compute current config hash from environment
            current_hash = self._compute_env_config_hash()

            # Reload if hash changed
            if current_hash != self._config_hash:
                logger.info("Detected encryption key config change, reloading...")
                self._load_keys()

            # Update last check time
            self._last_check_time = now

    def _compute_env_config_hash(self) -> str:
        """
        Compute hash of current environment variable configuration.

        Returns:
            SHA-256 hash of config string.
        """
        # Check multi-key format first
        keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")

        if keys_json:
            # Parse and hash (include hash of key values)
            try:
                config = json.loads(keys_json)
                keys_for_hash = []
                for k in config.get("keys", []):
                    key_value = k.get("value", "")
                    derived_key = hashlib.sha256(key_value.encode()).digest()
                    keys_for_hash.append({
                        "id": k.get("id"),
                        "status": k.get("status", "deprecated"),
                        "key_hash": hashlib.sha256(derived_key).hexdigest()[:16],
                    })
                config_for_hash = {
                    "keys": keys_for_hash,
                    "primary_key_id": config.get("primary_key_id"),
                }
                config_str = json.dumps(config_for_hash, sort_keys=True)
                return hashlib.sha256(config_str.encode()).hexdigest()
            except (json.JSONDecodeError, KeyError):
                # Fall through to single-key check
                pass

        # Single-key format
        key_value = os.environ.get("OPENACE_ENCRYPTION_KEY", "")
        derived_key = hashlib.sha256(key_value.encode()).digest()
        key_hash = hashlib.sha256(derived_key).hexdigest()[:16]
        config_str = f"single:{key_hash}"
        return hashlib.sha256(config_str.encode()).hexdigest()

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext using the primary key.

        Args:
            plaintext: Plain text to encrypt.

        Returns:
            Ciphertext with key_id prefix: "v1k<key_id>:<fernet_ciphertext>"
            For legacy key_id=0, returns plain Fernet ciphertext (no prefix).

        Raises:
            ValueError: If plaintext is empty or encryption fails.
        """
        if not plaintext:
            return ""

        # Check for hot reload before operation
        self._check_and_reload_if_needed()

        with self._lock:
            primary_key = self._keys.get(self._primary_key_id)
            if primary_key is None:
                raise RuntimeError(f"Primary key {self._primary_key_id} not found")

            # Import Fernet here to avoid import errors at module load
            try:
                from cryptography.fernet import Fernet
            except ImportError as e:
                raise ImportError(
                    "cryptography package is required for encryption. "
                    "Install with: pip install cryptography"
                ) from e

            # Create Fernet instance with derived key
            fernet_key = base64.urlsafe_b64encode(primary_key.derived_key)
            fernet = Fernet(fernet_key)

            # Encrypt
            ciphertext = fernet.encrypt(plaintext.encode()).decode()

            # For legacy key_id=0, don't add prefix (backward compatibility)
            if primary_key.key_id == 0:
                return ciphertext

            # Add key_id prefix for new format
            # Format: v1k<key_id>:<fernet_ciphertext>
            # Using ':' as separator (not in Fernet alphabet)
            return f"v1k{primary_key.key_id}:{ciphertext}"

    def decrypt(self, ciphertext: str) -> Optional[tuple[str, int]]:
        """
        Decrypt ciphertext using all available keys.

        Supports both new format (with key_id prefix) and legacy format (without prefix).

        Args:
            ciphertext: Ciphertext to decrypt (may have key_id prefix or be legacy format).

        Returns:
            Tuple of (decrypted_text, key_id) or None if decryption fails.
        """
        if not ciphertext:
            return ("", 0)

        # Check for hot reload before operation
        self._check_and_reload_if_needed()

        with self._lock:
            # Try to parse new format with key_id prefix
            key_id, actual_ciphertext = self._parse_ciphertext_prefix(ciphertext)

            if key_id is not None:
                # New format: try specific key first, then fall back to others
                result = self._try_decrypt_with_key(actual_ciphertext, key_id)
                if result is not None:
                    return (result, key_id)

                # Key-specific decryption failed, try all keys
                logger.warning(
                    f"Decryption with key_id {key_id} failed, trying all keys"
                )

                # Use actual_ciphertext for trying all keys (has key_id prefix removed)
                ciphertext_to_try = actual_ciphertext
            else:
                # Legacy format: use original ciphertext
                ciphertext_to_try = ciphertext

            # Try all available keys (for legacy format or fallback)
            for kid, key in self._keys.items():
                # Skip revoked keys unless emergency
                if key.status == KeyStatus.REVOKED:
                    continue

                result = self._try_decrypt_with_key(ciphertext_to_try, kid)
                if result is not None:
                    return (result, kid)

            # All keys failed
            logger.error(f"Decryption failed for all {len(self._keys)} keys")
            return None

    def _parse_ciphertext_prefix(self, ciphertext: str) -> tuple[Optional[int], str]:
        """
        Parse key_id prefix from ciphertext if present.

        Format: "v1k<key_id>:<fernet_ciphertext>"
        where ':' is used as separator (not in Fernet alphabet)

        Args:
            ciphertext: Full ciphertext string.

        Returns:
            Tuple of (key_id or None, actual_ciphertext).
            Returns (None, original_ciphertext) if not prefixed.
        """
        # Check for new format prefix: starts with "v1k" and contains ":"
        if not ciphertext.startswith("v1k"):
            # Legacy format without prefix
            return (None, ciphertext)

        # Split by ":"
        if ":" not in ciphertext:
            return (None, ciphertext)

        parts = ciphertext.split(":", 1)
        if len(parts) != 2:
            return (None, ciphertext)

        prefix, actual_ciphertext = parts

        # Parse key_id from prefix (v1k<key_id>)
        try:
            key_id_str = prefix[3:]  # Remove "v1k" prefix
            key_id = int(key_id_str)

            # key_id=0 is reserved for legacy format - treat as legacy
            if key_id == 0:
                return (None, actual_ciphertext)

            # Validate key_id range (1-255)
            if not (1 <= key_id <= 255):
                logger.warning(f"key_id out of range: {key_id}")
                return (None, ciphertext)

            return (key_id, actual_ciphertext)

        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse ciphertext prefix: {e}")
            return (None, ciphertext)

    def _try_decrypt_with_key(self, ciphertext: str, key_id: int) -> Optional[str]:
        """
        Try to decrypt ciphertext with a specific key.

        Args:
            ciphertext: Ciphertext to decrypt.
            key_id: Key ID to use.

        Returns:
            Decrypted text or None if decryption fails.
        """
        key = self._keys.get(key_id)
        if key is None:
            return None

        try:
            from cryptography.fernet import Fernet, InvalidToken

            fernet_key = base64.urlsafe_b64encode(key.derived_key)
            fernet = Fernet(fernet_key)

            # Decrypt
            decrypted = fernet.decrypt(ciphertext.encode()).decode()
            return decrypted

        except InvalidToken:
            # Wrong key or corrupted data
            return None
        except Exception as e:
            logger.debug(f"Decryption failed with key_id {key_id}: {e}")
            return None

    def get_fernet(self, key_id: int) -> Optional["Fernet"]:
        """
        Get a Fernet instance for a specific key.

        Args:
            key_id: Key ID.

        Returns:
            Fernet instance or None if key not found.
        """
        with self._lock:
            key = self._keys.get(key_id)
            if key is None:
                return None

            try:
                from cryptography.fernet import Fernet

                fernet_key = base64.urlsafe_b64encode(key.derived_key)
                return Fernet(fernet_key)
            except ImportError:
                return None

    def get_hmac_key(self, key_id: int) -> Optional[bytes]:
        """
        Get HMAC key bytes for a specific key.

        Args:
            key_id: Key ID.

        Returns:
            Derived key bytes or None if key not found.
        """
        with self._lock:
            key = self._keys.get(key_id)
            if key is None:
                return None
            return key.derived_key

    def get_config_version(self) -> int:
        """
        Get current configuration version number.

        Used for health checks to verify multi-replica synchronization.

        Returns:
            Config version integer (derived from hash).
        """
        with self._lock:
            return self._config_version

    def get_primary_key_id(self) -> int:
        """Get the primary (active) key ID."""
        with self._lock:
            return self._primary_key_id

    def get_key_count(self) -> int:
        """Get total number of keys in registry."""
        with self._lock:
            return len(self._keys)

    def get_active_key_count(self) -> int:
        """Get number of active keys (should always be 1)."""
        with self._lock:
            return sum(1 for k in self._keys.values() if k.status == KeyStatus.ACTIVE)

    def revoke_all_proxy_tokens(self, reason: str = "emergency_key_rotation") -> int:
        """
        Emergency method to revoke all proxy tokens.

        This method is called when a key is compromised and all tokens
        must be immediately invalidated.

        Note: This is a placeholder that should be integrated with
        APIKeyProxyService's token management.

        Args:
            reason: Reason for revocation (for audit log).

        Returns:
            Number of tokens revoked (placeholder: 0).
        """
        logger.critical(f"Proxy token revocation requested: {reason}")

        # This would need to be integrated with APIKeyProxyService
        # For now, log the request
        logger.warning(
            "revoke_all_proxy_tokens() called but integration with "
            "APIKeyProxyService not yet implemented"
        )

        return 0