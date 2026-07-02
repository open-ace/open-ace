# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Policy data models.

Storage records for the central policy & approval MVP. These dataclasses carry
no behaviour beyond (de)serialising their JSON ``value_list`` columns; dicts
read from the DB are coerced into them via ``from_row``.

Design (see plan §0, §2.2):
- ``policy_rules`` rows are IMMUTABLE versioned snapshots. Editing a rule
  inserts a new row (``version + 1``, ``is_current = true``) and marks the
  previous row ``is_current = false``. A ``PolicyDecision`` therefore pins a
  fixed ``(rule_id, rule_version)`` so a later edit can never retroactively
  change a past decision.
- ``policy_decisions`` is the authoritative control-plane decision object the
  executor trusts. It is SOFT-linked to the CLI input event
  (``agent_approvals``) via ``request_id`` (no hard FK — the input event is
  persisted asynchronously, so a hard FK would race).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class PolicyType(str, Enum):
    """What a rule targets."""

    MODEL = "model"
    PROVIDER = "provider"
    TOOL_ACTION = "tool_action"
    FILE_PATH = "file_path"
    COMMAND = "command"


class PatternType(str, Enum):
    """How a rule's ``pattern`` is interpreted."""

    GLOB = "glob"
    REGEX = "regex"


class PolicyEffect(str, Enum):
    """The effect a rule produces on match (rule-side vocabulary)."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Decision(str, Enum):
    """The resolved decision (control-plane vocabulary).

    ``require_human`` (not ``require_approval``) is persisted so the decision
    record reads unambiguously as "a human actor is required".
    """

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HUMAN = "require_human"


# Rules relevant to each evaluation target kind.
MODEL_SELECTION_TYPES = (PolicyType.MODEL.value, PolicyType.PROVIDER.value)
TOOL_ACTION_TYPES = (
    PolicyType.TOOL_ACTION.value,
    PolicyType.FILE_PATH.value,
    PolicyType.COMMAND.value,
)

# Specificity weights for scope dimensions (most specific first). Ties on score
# break on explicit ``priority`` then ``created_at`` (see evaluator).
SCOPE_WEIGHTS = {
    "machine_id": 100,
    "project_path": 50,
    "user_id": 20,
    "team_id": 10,
    "tenant_id": 5,
}


def _utcnow_naive() -> datetime:
    """Timezone-naive UTC now (matches the rest of the codebase)."""
    return datetime.utcnow()


def _parse_json(value: Any) -> Any:
    """Best-effort parse a JSON TEXT column into Python, never raising."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _dump_json(value: Any) -> str | None:
    """Serialise a Python value to a JSON string, or None when empty."""
    if value is None:
        return None
    try:
        if isinstance(value, (dict, list)) and len(value) == 0:
            return None
    except TypeError:
        pass
    return json.dumps(value, ensure_ascii=False, default=str)


def _iso(value: Any) -> str | None:
    """Normalise a timestamp-ish DB value to an ISO string (or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass
class PolicyRule:
    """One immutable versioned snapshot of a policy rule."""

    id: int | None = None
    rule_key: str = ""
    name: str = ""
    version: int = 1
    is_current: bool = True
    enabled: bool = True
    # Scope (all nullable = wildcard)
    tenant_id: int | None = None
    project_path: str | None = None
    machine_id: str | None = None
    user_id: int | None = None
    team_id: str | None = None
    # Target
    policy_type: str = PolicyType.TOOL_ACTION.value
    # Match
    pattern_type: str = PatternType.GLOB.value
    pattern: str | None = None
    value_list: list[str] = field(default_factory=list)
    tool_name: str | None = None
    action: str | None = None
    # Result
    effect: str = PolicyEffect.REQUIRE_APPROVAL.value
    # Ordering
    priority: int = 100
    is_default: bool = False
    # Approval TTL override (seconds); None = use config default
    approval_ttl_seconds: int | None = None
    # Audit
    created_by: int | None = None
    created_at: str | None = None
    superseded_at: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["value_list"] = self.value_list or []
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PolicyRule:
        return cls(
            id=row.get("id"),
            rule_key=row.get("rule_key") or "",
            name=row.get("name") or "",
            version=int(row.get("version") or 1),
            is_current=bool(row.get("is_current")),
            enabled=bool(row.get("enabled")),
            tenant_id=row.get("tenant_id"),
            project_path=row.get("project_path"),
            machine_id=row.get("machine_id"),
            user_id=row.get("user_id"),
            team_id=row.get("team_id"),
            policy_type=row.get("policy_type") or PolicyType.TOOL_ACTION.value,
            pattern_type=row.get("pattern_type") or PatternType.GLOB.value,
            pattern=row.get("pattern"),
            value_list=_parse_json(row.get("value_list")) or [],
            tool_name=row.get("tool_name"),
            action=row.get("action"),
            effect=row.get("effect") or PolicyEffect.REQUIRE_APPROVAL.value,
            priority=int(row.get("priority") or 100),
            is_default=bool(row.get("is_default")),
            approval_ttl_seconds=row.get("approval_ttl_seconds"),
            created_by=row.get("created_by"),
            created_at=_iso(row.get("created_at")),
            superseded_at=_iso(row.get("superseded_at")),
            description=row.get("description"),
        )


@dataclass
class PolicyDecision:
    """The authoritative control-plane decision record."""

    id: int | None = None
    decision_id: str = ""
    request_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    # Scope
    tenant_id: int | None = None
    workspace_scope: str | None = None
    machine_id: str | None = None
    # Target
    model: str | None = None
    provider: str | None = None
    tool_name: str | None = None
    action: str | None = None
    resource_target: str | None = None
    # Fingerprint
    args_digest: str | None = None
    normalization_profile_id: str | None = None
    normalization_profile_version: int | None = None
    fingerprint_hash: str | None = None
    # Matched rule (immutable snapshot ref)
    policy_rule_id: int | None = None
    policy_rule_version: int | None = None
    # Decision
    decision: str = Decision.REQUIRE_HUMAN.value
    reason: str | None = None
    # Lifecycle
    reviewer_identity: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    consumed_at: str | None = None
    remote_response_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PolicyDecision:
        return cls(
            id=row.get("id"),
            decision_id=row.get("decision_id") or "",
            request_id=row.get("request_id"),
            run_id=row.get("run_id"),
            session_id=row.get("session_id"),
            tenant_id=row.get("tenant_id"),
            workspace_scope=row.get("workspace_scope"),
            machine_id=row.get("machine_id"),
            model=row.get("model"),
            provider=row.get("provider"),
            tool_name=row.get("tool_name"),
            action=row.get("action"),
            resource_target=row.get("resource_target"),
            args_digest=row.get("args_digest"),
            normalization_profile_id=row.get("normalization_profile_id"),
            normalization_profile_version=row.get("normalization_profile_version"),
            fingerprint_hash=row.get("fingerprint_hash"),
            policy_rule_id=row.get("policy_rule_id"),
            policy_rule_version=row.get("policy_rule_version"),
            decision=row.get("decision") or Decision.REQUIRE_HUMAN.value,
            reason=row.get("reason"),
            reviewer_identity=row.get("reviewer_identity"),
            issued_at=_iso(row.get("issued_at")),
            expires_at=_iso(row.get("expires_at")),
            consumed_at=_iso(row.get("consumed_at")),
            remote_response_id=row.get("remote_response_id"),
            created_at=_iso(row.get("created_at")),
            updated_at=_iso(row.get("updated_at")),
        )


@dataclass
class RequestFingerprint:
    """Typed, re-computable identity of a request (not a display string).

    ``fingerprint_hash`` is sha256 over the canonical, deterministic-order
    serialisation of these fields, so a later verifier can recompute it
    independently and detect drift / a changed normalization profile.
    """

    tool: str | None = None
    action: str | None = None
    args_digest: str | None = None
    normalization_profile_id: str | None = None
    normalization_profile_version: int | None = None
    machine_id: str | None = None
    workspace_scope: str | None = None
    resource_target: str | None = None
    policy_rule_id: int | None = None
    policy_rule_version: int | None = None
    request_id: str | None = None
    issued_ts: str | None = None

    def to_canonical_payload(self) -> dict[str, Any]:
        """Deterministic-order mapping used for hashing (excludes request_id)."""
        return {
            "tool": self.tool or "",
            "action": self.action or "",
            "args_digest": self.args_digest or "",
            "normalization_profile_id": self.normalization_profile_id or "",
            "normalization_profile_version": self.normalization_profile_version or 0,
            "machine_id": self.machine_id or "",
            "workspace_scope": self.workspace_scope or "",
            "resource_target": self.resource_target or "",
            "policy_rule_id": self.policy_rule_id or 0,
            "policy_rule_version": self.policy_rule_version or 0,
            # NB: request_id and issued_ts intentionally excluded — a re-issued
            # decision for the *same* request under the same profile/policy
            # should hash identically (request_id/issued_ts are per-issuance,
            # not part of request identity).
        }


__all__ = [
    "PolicyType",
    "PatternType",
    "PolicyEffect",
    "Decision",
    "MODEL_SELECTION_TYPES",
    "TOOL_ACTION_TYPES",
    "SCOPE_WEIGHTS",
    "PolicyRule",
    "PolicyDecision",
    "RequestFingerprint",
    "_utcnow_naive",
    "_dump_json",
    "_parse_json",
    "_iso",
]
