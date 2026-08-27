"""
Open ACE - Repository Exceptions

Custom exceptions for repository layer operations.
"""


class SecretDecryptionError(Exception):
    """Exception raised when encrypted secret cannot be decrypted.

    This exception is raised when an encrypted configuration value (e.g., App Secret,
    API key, password) cannot be decrypted, typically due to:
    - Encryption key rotation without proper migration
    - Data corruption
    - Incompatible encryption context

    The exception intentionally does NOT expose the underlying cryptographic error
    details to prevent information leakage while still allowing proper error handling.

    Attributes:
        field_name: The name of the field that failed to decrypt.
        integration_kind: The type of integration (e.g., "feishu", "dingtalk").
    """

    def __init__(self, field_name: str, integration_kind: str) -> None:
        self.field_name = field_name
        self.integration_kind = integration_kind
        super().__init__(
            f"Failed to decrypt {field_name} for {integration_kind} configuration. "
            "The saved secret is incompatible with the current encryption context. "
            "Please re-enter the secret and save the configuration."
        )
