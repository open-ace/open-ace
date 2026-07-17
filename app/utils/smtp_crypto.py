"""
Open ACE - SMTP Password Manager

Provides encryption/decryption and masking for SMTP passwords.
Uses Fernet (AES-128-CBC) for symmetric encryption, consistent with API key encryption.
"""

import base64
import hashlib
import logging
from typing import Optional, cast

from app.utils.security_env import get_encryption_key_material

logger = logging.getLogger(__name__)

# Singleton instance
_password_manager_instance: Optional["SMTPPasswordManager"] = None


class SMTPPasswordManager:
    """Manager for SMTP password encryption, decryption, and masking."""

    def __init__(self):
        """Initialize password manager with encryption key."""
        self._encryption_key = self._get_encryption_key()

    def _get_encryption_key(self) -> bytes:
        """Get the AES encryption key from environment variable."""
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


def get_password_manager() -> SMTPPasswordManager:
    """
    Get the singleton SMTPPasswordManager instance.

    Returns:
        SMTPPasswordManager instance.
    """
    global _password_manager_instance
    if _password_manager_instance is None:
        _password_manager_instance = SMTPPasswordManager()
    return _password_manager_instance
