"""Feature Flag management for Open ACE.

Provides runtime configuration for query session token policy (Issue #1896).

Policy states:
- observe: Log usage, don't reject (Phase 0 - observation)
- warn: Log usage, add warning header (Phase 1 - warning)
- reject: Reject and log audit (Phase 2 - enforcement)
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from flask import g

logger = logging.getLogger(__name__)

# Valid policy values
QuerySessionTokenPolicy = Literal["observe", "warn", "reject"]
VALID_POLICIES = ("observe", "warn", "reject")

# Default policy for safety
DEFAULT_POLICY: QuerySessionTokenPolicy = "observe"

# Environment variable name
ENV_VAR_NAME = "QUERY_SESSION_TOKEN_POLICY"


def get_query_token_policy() -> QuerySessionTokenPolicy:
    """Get the current query session token policy.

    Reads from environment variable and caches in request context for
    consistency within a single request (prevents race conditions).

    Returns:
        Current policy: "observe", "warn", or "reject"
    """
    # Check request context cache first
    if hasattr(g, "_query_token_policy"):
        return g._query_token_policy

    # Read from environment variable
    policy = os.environ.get(ENV_VAR_NAME, DEFAULT_POLICY)

    # Validate policy value
    if policy not in VALID_POLICIES:
        logger.warning(
            "Invalid QUERY_SESSION_TOKEN_POLICY value '%s', falling back to '%s'",
            policy,
            DEFAULT_POLICY,
        )
        policy = DEFAULT_POLICY

    # Cache in request context
    g._query_token_policy = policy  # type: ignore[attr-defined]

    return policy  # type: ignore[return-value]


def set_query_token_policy(policy: QuerySessionTokenPolicy) -> None:
    """Set the query session token policy for the current request.

    This is primarily used for testing. In production, policy is set
    via environment variable.

    Args:
        policy: Policy value to set
    """
    if policy not in VALID_POLICIES:
        raise ValueError(f"Invalid policy '{policy}'. Must be one of: {VALID_POLICIES}")
    g._query_token_policy = policy


def is_query_session_token_rejected() -> bool:
    """Check if query session tokens should be rejected.

    Returns:
        True if policy is "reject", False otherwise
    """
    return get_query_token_policy() == "reject"


def should_add_warning_header() -> bool:
    """Check if deprecation warning header should be added.

    Returns:
        True if policy is "warn", False otherwise
    """
    return get_query_token_policy() == "warn"


def is_observe_mode() -> bool:
    """Check if in observation mode (log but don't reject).

    Returns:
        True if policy is "observe", False otherwise
    """
    return get_query_token_policy() == "observe"


__all__ = [
    "get_query_token_policy",
    "set_query_token_policy",
    "is_query_session_token_rejected",
    "should_add_warning_header",
    "is_observe_mode",
    "QuerySessionTokenPolicy",
    "VALID_POLICIES",
    "DEFAULT_POLICY",
    "ENV_VAR_NAME",
]
