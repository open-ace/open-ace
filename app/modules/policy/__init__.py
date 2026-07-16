"""
Open ACE - Central Policy & Approval Module (MVP).

A pluggable, self-contained policy engine for remote-agent actions. It owns the
durable *decision* lifecycle, treating the CLI permission request as merely the
input event that starts it (plan 0). All code lives here so the feature is
easy to remove or re-implement behind an external API later; integration points
in existing code are 1-2-line guarded calls and the blueprint registration.

Feature flag: ``policy.enabled`` in ``~/.open-ace/config.json`` (60s TTL). When
disabled, :func:`get_evaluator` returns a :class:`NullPolicyEvaluator` and the
system behaves exactly as before (real-time manual approval).

The canonical table schema lives in the Alembic migration
``migrations/versions/<ts>_add_policy_tables.py``; the per-module
:func:`get_ddl_statements` is the deprecated runtime mirror kept for tests and
mirrors the run-timeline pattern.
"""

from __future__ import annotations

from typing import Any

from app.modules.policy.evaluator import (
    NullPolicyEvaluator,
    PolicyEvaluator,
    get_evaluator,
    reset_evaluator_for_tests,
)
from app.modules.policy.models import (
    Decision,
    PatternType,
    PolicyDecision,
    PolicyEffect,
    PolicyRule,
    PolicyType,
    RequestFingerprint,
)


def is_policy_enabled() -> bool:
    """Re-export of the config flag (kept here for a single import surface)."""
    from app.utils.config import is_policy_enabled as _enabled

    return _enabled()


__all__: list[str] = [
    "is_policy_enabled",
    "get_evaluator",
    "reset_evaluator_for_tests",
    "PolicyEvaluator",
    "NullPolicyEvaluator",
    "PolicyRule",
    "PolicyDecision",
    "RequestFingerprint",
    "PolicyType",
    "PolicyEffect",
    "PatternType",
    "Decision",
]
