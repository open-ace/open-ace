"""
Open ACE - Agent Token Module

Provides secure token generation, hashing, and validation for remote agent
authentication. Registration tokens are one-time-use tokens for enrolling new
machines. Agent tokens (Bearer tokens) are long-lived credentials used by
agents to authenticate heartbeat and message endpoints.
"""

from __future__ import annotations

import hashlib
import secrets

# Length of the token hash prefix used for audit logging and rate-limiting.
HASH_PREFIX_LENGTH = 8


def generate_agent_token() -> str:
    """Generate a cryptographically random agent token (64-char hex).

    The token is the secret presented by the agent in the Authorization
    header. The server stores only the SHA-256 hash of this token.
    """
    return secrets.token_hex(32)


def generate_registration_token() -> str:
    """Generate a cryptographically random one-time registration token (64-char hex).

    Used by admins to authorize the enrollment of a new remote machine.
    The server stores only the SHA-256 hash of this token.
    """
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """Compute SHA-256 hash of a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_hash_prefix(token: str) -> str:
    """Return the first 8 characters of a token's SHA-256 hash.

    Used for audit log deduplication and rate-limiting so that the raw
    token is never logged.
    """
    return hash_token(token)[:HASH_PREFIX_LENGTH]
