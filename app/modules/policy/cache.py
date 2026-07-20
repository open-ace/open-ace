"""
Open ACE - Policy rule cache.

A 60-second TTL cache of the rules used on the evaluation hot path, so a
permission prompt never hits the database. Mirrors the config-cache pattern in
:mod:`app.utils.config`. The admin CRUD handlers call
:func:`invalidate_policy_rule_cache` so an edit propagates immediately instead
of waiting for the TTL.
"""

from __future__ import annotations


import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.policy.models import PolicyRule

_lock = threading.Lock()
_entry: dict[str, Any] = {"ts": 0.0, "rules": None}
_ttl: float = 60.0  # seconds


def get_cached_rules() -> list[PolicyRule]:
    """Return the current+enabled rules, refreshing after the TTL."""
    now = time.time()
    with _lock:
        rules = _entry["rules"]
        if rules is not None and now - float(_entry["ts"]) < _ttl:
            return rules  # type: ignore[no-any-return]

    from app.modules.policy.repo import PolicyRepository

    fresh = PolicyRepository().get_rules_for_evaluation()
    with _lock:
        _entry["ts"] = now
        _entry["rules"] = fresh
    return fresh


def invalidate_policy_rule_cache() -> None:
    """Force the next evaluation to re-read rules from the DB."""
    with _lock:
        _entry["rules"] = None
        _entry["ts"] = 0.0


__all__ = ["get_cached_rules", "invalidate_policy_rule_cache"]
