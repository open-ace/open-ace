"""
Open ACE - Policy Evaluator.

Deterministic decision engine. Given a :class:`PolicyContext`, returns an
:class:`EvaluationResult` (``allow`` / ``deny`` / ``require_human``) plus the
typed fingerprint the decision binds to.

Guarantees (plan §2.4, review M1/M2):
- **Fail-closed:** ``evaluate`` NEVER raises. On any internal error the engine
  returns ``require_human`` for tool targets and ``deny`` for model/provider
  selection (so a busted rule store can neither auto-allow nor silently select
  a denied model).
- **Specificity ranking:** rules are ordered by a weighted scope score
  (machine > project > user > team > tenant), then explicit ``priority``, then
  ``created_at``. First match wins. This deterministically orders
  ``(tenant+machine)`` above ``(tenant+user)``.
- **Fallback:** no match → ``require_human`` for tools, ``allow`` for model
  selection (preserving today's behaviour).
- **60s TTL rule cache** via :mod:`app.modules.policy.cache`.

``NullPolicyEvaluator`` covers the flag-off case (model → allow, tool →
require_human), so every call site can call ``evaluate`` unconditionally.
"""

from __future__ import annotations




import fnmatch
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from app.modules.policy.cache import get_cached_rules
from app.modules.policy.fingerprint import (
    PROFILE_GENERIC,
    PROFILE_VERSION,
    build_fingerprint,
    compute_args_digest,
    compute_fingerprint_hash,
)
from app.modules.policy.models import (
    MODEL_SELECTION_TYPES,
    SCOPE_WEIGHTS,
    TOOL_ACTION_TYPES,
    Decision,
    PatternType,
    PolicyEffect,
    PolicyRule,
    RequestFingerprint,
)

logger = logging.getLogger(__name__)

TARGET_MODEL_SELECTION = "model_selection"
TARGET_TOOL_ACTION = "tool_action"

_SCOPE_DIMS = ("tenant_id", "project_path", "machine_id", "user_id", "team_id")


@dataclass
class PolicyContext:
    """Input to the evaluator. Only the fields for the target_kind are used."""

    target_kind: str
    # Scope
    tenant_id: int | None = None
    project_path: str | None = None
    machine_id: str | None = None
    user_id: int | None = None
    team_id: str | None = None
    # Model selection target
    model: str | None = None
    provider: str | None = None
    cli_tool: str | None = None
    # Tool action target
    tool: str | None = None
    action: str | None = None
    resource_target: str | None = None
    control_request: dict[str, Any] | None = None
    home_dir: str | None = None
    # Bookkeeping for decision persistence
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    issued_ts: str | None = None


@dataclass
class EvaluationResult:
    """Outcome of evaluating a context."""

    decision: str  # Decision.* value
    matched_rule: PolicyRule | None = None
    reason: str = ""
    fingerprint: RequestFingerprint | None = None
    fingerprint_hash: str | None = None
    args_digest: str | None = None
    resource_target: str | None = None
    fell_back: bool = False

    @property
    def is_allow(self) -> bool:
        return self.decision == Decision.ALLOW.value

    @property
    def is_deny(self) -> bool:
        return self.decision == Decision.DENY.value

    @property
    def requires_human(self) -> bool:
        return self.decision == Decision.REQUIRE_HUMAN.value


def _effect_to_decision(effect: str) -> str:
    if effect == PolicyEffect.ALLOW.value:
        return Decision.ALLOW.value
    if effect == PolicyEffect.DENY.value:
        return Decision.DENY.value
    return Decision.REQUIRE_HUMAN.value


class PolicyEvaluator:
    """Live, fail-closed, deterministic policy evaluator."""

    is_noop: bool = False

    def __init__(self) -> None:
        # Compiled-pattern cache: (pattern_type, pattern) -> compiled matcher.
        self._matcher_cache: dict[tuple[str, str], Any] = {}
        self._matcher_lock = threading.Lock()

    # ── public ─────────────────────────────────────────────────────

    def evaluate(self, ctx: PolicyContext) -> EvaluationResult:
        """Evaluate, never raising. Fail-closed on any internal error."""
        try:
            self._normalize_ctx(ctx)
            return self._evaluate_unsafe(ctx)
        except Exception as e:  # noqa: BLE001 - fail-closed contract
            logger.warning("policy evaluator failed; failing closed: %s", e)
            if ctx.target_kind == TARGET_MODEL_SELECTION:
                return EvaluationResult(
                    decision=Decision.DENY.value,
                    reason=f"policy evaluation error: {e}",
                    fell_back=True,
                )
            return EvaluationResult(
                decision=Decision.REQUIRE_HUMAN.value,
                reason=f"policy evaluation error: {e}",
                fell_back=True,
            )

    @staticmethod
    def _normalize_ctx(ctx: PolicyContext) -> None:
        """Fill tool/action/resource_target from a raw control_request when unset."""
        if ctx.target_kind != TARGET_TOOL_ACTION or not ctx.control_request:
            return
        from app.modules.policy.fingerprint import extract_request_fields

        fields = extract_request_fields(ctx.control_request)
        if ctx.tool is None:
            ctx.tool = fields.get("tool")
        if ctx.action is None:
            ctx.action = fields.get("action")
        if ctx.resource_target is None:
            ctx.resource_target = fields.get("resource_target")
        if ctx.request_id is None:
            ctx.request_id = fields.get("request_id")

    def _evaluate_unsafe(self, ctx: PolicyContext) -> EvaluationResult:
        candidate_types = (
            MODEL_SELECTION_TYPES
            if ctx.target_kind == TARGET_MODEL_SELECTION
            else TOOL_ACTION_TYPES
        )
        rules = [r for r in get_cached_rules() if r.policy_type in candidate_types]

        # Build the fingerprint + target value up front.
        fingerprint, target_value = self._build_identity(ctx)
        resource_target = fingerprint.resource_target if fingerprint else None

        # Scope-match, then rank by specificity → priority → created_at.
        scoped = [r for r in rules if self._scope_match(r, ctx)]
        scoped.sort(key=lambda r: (-self._specificity(r), r.priority, r.created_at or ""))

        for rule in scoped:
            if self._target_match(rule, ctx, target_value, resource_target):
                decision = _effect_to_decision(rule.effect)
                self._bind_rule(fingerprint, rule)
                return EvaluationResult(
                    decision=decision,
                    matched_rule=rule,
                    reason=rule.description or rule.name or f"rule {rule.rule_key}",
                    fingerprint=fingerprint,
                    fingerprint_hash=compute_fingerprint_hash(fingerprint) if fingerprint else None,
                    args_digest=fingerprint.args_digest if fingerprint else None,
                    resource_target=resource_target,
                )

        # No match → configurable fallback.
        fb = (
            Decision.ALLOW.value
            if ctx.target_kind == TARGET_MODEL_SELECTION
            else Decision.REQUIRE_HUMAN.value
        )
        self._bind_rule(fingerprint, None)
        return EvaluationResult(
            decision=fb,
            reason="no matching policy rule (fallback default)",
            fingerprint=fingerprint,
            fingerprint_hash=compute_fingerprint_hash(fingerprint) if fingerprint else None,
            args_digest=fingerprint.args_digest if fingerprint else None,
            resource_target=resource_target,
            fell_back=True,
        )

    # ── identity / fingerprint ─────────────────────────────────────

    def _build_identity(self, ctx: PolicyContext) -> tuple[RequestFingerprint | None, str | None]:
        """Build the (partial) fingerprint and return it + the target value."""
        if ctx.target_kind == TARGET_MODEL_SELECTION:
            args = {"model": ctx.model, "provider": ctx.provider}
            digest = compute_args_digest(PROFILE_GENERIC, PROFILE_VERSION, args)
            fp = RequestFingerprint(
                tool=None,
                action=TARGET_MODEL_SELECTION,
                args_digest=digest,
                normalization_profile_id=PROFILE_GENERIC,
                normalization_profile_version=PROFILE_VERSION,
                machine_id=ctx.machine_id,
                workspace_scope=ctx.project_path,
                resource_target=ctx.model,
                request_id=ctx.request_id,
                issued_ts=ctx.issued_ts,
            )
            return fp, ctx.model

        # tool action
        if ctx.control_request:
            fp, _, resource_target = build_fingerprint(
                ctx.control_request,
                machine_id=ctx.machine_id,
                workspace_scope=ctx.project_path,
                home_dir=ctx.home_dir,
                issued_ts=ctx.issued_ts,
            )
            return fp, resource_target
        # Synthetic fingerprint when only fields are known (no raw request).
        args = {"tool": ctx.tool, "action": ctx.action, "target": ctx.resource_target}
        digest = compute_args_digest(PROFILE_GENERIC, PROFILE_VERSION, args)
        fp = RequestFingerprint(
            tool=ctx.tool,
            action=ctx.action,
            args_digest=digest,
            normalization_profile_id=PROFILE_GENERIC,
            normalization_profile_version=PROFILE_VERSION,
            machine_id=ctx.machine_id,
            workspace_scope=ctx.project_path,
            resource_target=ctx.resource_target,
            request_id=ctx.request_id,
            issued_ts=ctx.issued_ts,
        )
        return fp, ctx.resource_target

    @staticmethod
    def _bind_rule(fingerprint: RequestFingerprint | None, rule: PolicyRule | None) -> None:
        if fingerprint is None:
            return
        fingerprint.policy_rule_id = rule.id if rule else None
        fingerprint.policy_rule_version = rule.version if rule else None

    # ── scope + specificity ────────────────────────────────────────

    @staticmethod
    def _scope_match(rule: PolicyRule, ctx: PolicyContext) -> bool:
        for dim in _SCOPE_DIMS:
            rv = getattr(rule, dim)
            if rv is not None and rv != getattr(ctx, dim):
                return False
        return True

    @staticmethod
    def _specificity(rule: PolicyRule) -> int:
        return sum(SCOPE_WEIGHTS[dim] for dim in _SCOPE_DIMS if getattr(rule, dim) is not None)

    # ── target matching ────────────────────────────────────────────

    def _target_match(
        self,
        rule: PolicyRule,
        ctx: PolicyContext,
        target_value: str | None,
        resource_target: str | None,
    ) -> bool:
        ptype = rule.policy_type

        if ptype in ("model", "provider"):
            value = ctx.model if ptype == "model" else ctx.provider
            return self._value_match(rule, value)

        # tool_action / file_path / command
        if rule.tool_name and rule.tool_name != ctx.tool:
            return False
        if rule.action and rule.action != ctx.action:
            return False
        if ptype == "tool_action":
            if rule.pattern:
                return self._pattern_match(rule, resource_target)
            return True  # tool/action filter only
        # file_path / command: require a pattern match against the resource
        if not rule.pattern:
            return False
        return self._pattern_match(rule, resource_target)

    def _value_match(self, rule: PolicyRule, value: str | None) -> bool:
        """Match a model/provider value against value_list and/or pattern."""
        if rule.value_list:
            return bool(value) and value in rule.value_list
        if rule.pattern:
            return self._pattern_match(rule, value)
        return True  # catch-all for this type

    def _pattern_match(self, rule: PolicyRule, value: str | None) -> bool:
        if not value or not rule.pattern:
            return False
        matcher = self._compile(rule.pattern_type, rule.pattern)
        if matcher is None:
            return False
        if rule.pattern_type == PatternType.REGEX.value:
            return matcher.search(value) is not None  # type: ignore[union-attr]
        return matcher(value) is not None  # glob -> fnmatch predicate

    def _compile(self, pattern_type: str, pattern: str) -> Any:
        key = (pattern_type, pattern)
        with self._matcher_lock:
            if key in self._matcher_cache:
                return self._matcher_cache[key]
        try:
            if pattern_type == PatternType.REGEX.value:
                compiled: Any = re.compile(pattern)
            else:
                regex = fnmatch.translate(pattern)
                compiled = re.compile(regex).match
        except re.error as e:
            logger.warning("policy rule has invalid %s pattern %r: %s", pattern_type, pattern, e)
            compiled = None
        with self._matcher_lock:
            self._matcher_cache[key] = compiled
        return compiled


class NullPolicyEvaluator:
    """Flag-off evaluator: model → allow, tool → require_human.

    Keeps every call site unconditional (no ``if enabled`` guards at the call).
    """

    is_noop: bool = True

    def evaluate(self, ctx: PolicyContext) -> EvaluationResult:
        if ctx.target_kind == TARGET_MODEL_SELECTION:
            return EvaluationResult(decision=Decision.ALLOW.value, reason="policy disabled")
        return EvaluationResult(decision=Decision.REQUIRE_HUMAN.value, reason="policy disabled")


# ── process-wide factory (cached; flag flip needs restart, like the recorder)

_evaluator_lock = threading.Lock()
_evaluator_instance: PolicyEvaluator | NullPolicyEvaluator | None = None


def get_evaluator() -> PolicyEvaluator | NullPolicyEvaluator:
    """Return the process-wide evaluator (Null when the feature is disabled)."""
    global _evaluator_instance
    if _evaluator_instance is not None:
        return _evaluator_instance
    with _evaluator_lock:
        if _evaluator_instance is None:
            from app.utils.config import is_policy_enabled

            _evaluator_instance = (
                PolicyEvaluator() if is_policy_enabled() else NullPolicyEvaluator()
            )
    return _evaluator_instance


def reset_evaluator_for_tests() -> None:
    """Clear the cached evaluator singleton (tests only)."""
    global _evaluator_instance
    with _evaluator_lock:
        _evaluator_instance = None


__all__ = [
    "PolicyContext",
    "EvaluationResult",
    "PolicyEvaluator",
    "NullPolicyEvaluator",
    "get_evaluator",
    "reset_evaluator_for_tests",
    "TARGET_MODEL_SELECTION",
    "TARGET_TOOL_ACTION",
]
