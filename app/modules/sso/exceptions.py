"""
Open ACE - SSO Exceptions

Custom exceptions for SSO module.
"""

from __future__ import annotations


class SSOConfigError(Exception):
    """Base exception for SSO configuration errors."""

    pass


class SSOConfigDecryptionError(SSOConfigError):
    """Exception raised when SSO provider configuration decryption fails.

    This exception is raised when the encrypted client_secret cannot be
    decrypted, typically due to key rotation or data corruption.

    Attributes:
        provider_name: The name of the provider whose configuration failed to decrypt.
        original_error: The underlying exception that caused the decryption failure.
    """

    def __init__(self, provider_name: str, original_error: Exception) -> None:
        self.provider_name = provider_name
        self.original_error = original_error
        super().__init__(
            f"Failed to decrypt client_secret for provider '{provider_name}': {original_error}"
        )