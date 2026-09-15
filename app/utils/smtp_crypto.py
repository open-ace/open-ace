"""
Open ACE - SMTP Password Manager

Provides encryption/decryption and masking for SMTP passwords.
Uses Fernet (AES-128-CBC) for symmetric encryption, consistent with API key encryption.
"""

import base64
import functools
import hashlib
import logging
from typing import cast

from app.utils.security_env import get_encryption_key_material
from app.utils.security_mode import is_weak_secret_value

logger = logging.getLogger(__name__)

# Minimum length for explicit candidate keys, mirroring the runtime rule for
# OPENACE_ENCRYPTION_KEY (validate_secret_strength default min_length=32).
EXPLICIT_KEY_MIN_LENGTH = 32


class SMTPPasswordManager:
    """Manager for SMTP password encryption, decryption, and masking."""

    def __init__(self, encryption_key: str | None = None):
        """Initialize password manager with encryption key.

        Args:
            encryption_key: Explicit key material (e.g. a candidate key during
                rotation). When omitted, the key is derived from the
                OPENACE_ENCRYPTION_KEY environment variable as before.

        Raises:
            ValueError: If an explicitly provided key is empty, a known weak
                placeholder, or shorter than EXPLICIT_KEY_MIN_LENGTH. Unlike
                the environment path (which only warns in development mode),
                an invalid EXPLICIT key is always rejected: there is no
                auto-generation fallback for a caller-supplied value, and a
                rotation completed with such a key would leave ciphertext the
                production runtime then refuses to load.
        """
        self._encryption_key = self._get_encryption_key(encryption_key)

    @classmethod
    def for_legacy_rotation_key(cls, encryption_key: str) -> "SMTPPasswordManager":
        """Migration-only constructor for the CURRENT (old) key.

        The unconditional strength validation on explicit keys exists so a
        rotation can never COMPLETE with a key the production runtime would
        refuse. The OLD key is the opposite case: it already protects the
        existing ciphertext, and migrating AWAY from a weak/short/historical
        key is exactly what rotation is for — rejecting it would strand
        legacy deployments on the non-compliant key forever. Only requires
        non-empty key material; derives with the same historical SHA-256.
        """
        if not encryption_key:
            raise ValueError("Legacy rotation key must not be empty")
        manager = cls.__new__(cls)
        manager._encryption_key = hashlib.sha256(encryption_key.encode()).digest()
        return manager

    def _get_encryption_key(self, encryption_key: str | None = None) -> bytes:
        """Derive the Fernet encryption key from OPENACE_ENCRYPTION_KEY.

        The key material is hashed with SHA-256 to produce a 32-byte
        key, which is then base64-encoded for Fernet compatibility.
        """
        if encryption_key is not None:
            if (
                not encryption_key
                or is_weak_secret_value(encryption_key)
                or len(encryption_key) < EXPLICIT_KEY_MIN_LENGTH
            ):
                raise ValueError(
                    "Explicit encryption key is empty, weak, or shorter than "
                    f"{EXPLICIT_KEY_MIN_LENGTH} chars; generate one: "
                    'python3 -c "import secrets; print(secrets.token_hex(32))"'
                )
            key_env = encryption_key
        else:
            key_env = get_encryption_key_material(purpose="SMTP password encryption")
        # Derive a 32-byte key using SHA-256
        return hashlib.sha256(key_env.encode()).digest()

    def generate_key(self) -> str:
        """
        Generate a new Fernet encryption key.

        Returns:
            Base64-encoded 32-byte key suitable for Fernet encryption.
        """
        from cryptography.fernet import Fernet

        return cast("str", Fernet.generate_key().decode())

    def encrypt(self, password: str) -> str:
        """
        Encrypt a password using Fernet (AES-128-CBC with HMAC).

        Args:
            password: Plain text password to encrypt.

        Returns:
            Encrypted password as base64 string, or empty string if password is empty.

        Raises:
            ImportError: If cryptography package is not installed.
        """
        # Empty password returns empty string (no encryption needed)
        if not password:
            return ""

        try:
            from cryptography.fernet import Fernet

            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return cast("str", f.encrypt(password.encode()).decode())
        except ImportError:
            raise ImportError(
                "cryptography package is required for SMTP password encryption. "
                "Install with: pip install cryptography"
            )

    def decrypt(self, encrypted_password: str) -> str:
        """
        Decrypt an encrypted password.

        Args:
            encrypted_password: Encrypted password as base64 string.

        Returns:
            Decrypted plain text password, or empty string if encrypted_password is empty.

        Raises:
            ImportError: If cryptography package is not installed.
            ValueError: If decryption fails (invalid key or corrupted data).
        """
        # Empty string returns empty string
        if not encrypted_password:
            return ""

        try:
            from cryptography.fernet import Fernet

            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return cast("str", f.decrypt(encrypted_password.encode()).decode())
        except ImportError:
            raise ImportError(
                "cryptography package is required for SMTP password decryption. "
                "Install with: pip install cryptography"
            )
        except Exception as e:
            logger.error(f"Failed to decrypt SMTP password: {e}")
            raise ValueError(f"Failed to decrypt password: {e}")

    def mask_password(self, password: str) -> str:
        """
        Mask a password for display (show first 4 characters, rest as asterisks).

        Args:
            password: Plain text password to mask.

        Returns:
            Masked password string preserving original length.
            - Empty password returns ""
            - Short password (<=4 chars) returns "***"
            - Normal password: first 4 chars + asterisks to match original length
        """
        if not password:
            return ""
        if len(password) <= 4:
            # Short password: show asterisks only (test expects "***")
            return "***"
        # Show first 4 characters, fill rest with asterisks to match original length
        masked_len = len(password) - 4
        return f"{password[:4]}{'*' * masked_len}"


@functools.lru_cache(maxsize=1)
def get_password_manager() -> SMTPPasswordManager:
    """
    Get the singleton SMTPPasswordManager instance.

    Uses lru_cache for thread-safe singleton pattern without explicit locking.

    Returns:
        SMTPPasswordManager instance.
    """
    return SMTPPasswordManager()
