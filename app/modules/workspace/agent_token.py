"""Agent token utilities for remote agent identity management.

Provides token generation, hashing, and validation functions for the
two-tier token model:

1. Registration tokens: One-time, short-lived tokens used during agent
   installation. Persisted in the `registration_tokens` database table.
2. Agent tokens: Long-lived per-machine credentials used for ongoing
   agent-to-server communication. Only the SHA-256 hash is stored
   server-side in the `agent_tokens` table.

Issue: #754
"""

import hashlib
import hmac
import secrets


def generate_agent_token() -> str:
    """Generate a new agent token (256-bit random hex string).

    Returns:
        A 64-character hex string suitable for use as a Bearer token.
    """
    return secrets.token_hex(32)


def hash_agent_token(token: str) -> str:
    """Compute the SHA-256 hash of an agent token.

    Follows the same pattern as api_key_proxy._hash_key() for consistency.

    Args:
        token: The plaintext agent token string.

    Returns:
        The hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def validate_agent_token_hash(token_hash: str, plaintext: str) -> bool:
    """Validate a plaintext agent token against a stored hash.

    Uses hmac.compare_digest for timing-safe comparison to prevent
    timing attacks.

    Args:
        token_hash: The stored SHA-256 hash from the database.
        plaintext: The plaintext token to verify.

    Returns:
        True if the token matches, False otherwise.
    """
    computed = hash_agent_token(plaintext)
    return hmac.compare_digest(computed, token_hash)
