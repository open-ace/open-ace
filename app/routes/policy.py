"""
Open ACE - Central Policy & Approval API Routes.

Admin-only CRUD for policy rules + a read endpoint for decisions. Lives in its
own blueprint so the whole feature is removed with one registration line.
Mirrors ``run_timeline_bp``: a ``before_request`` guard returns
``{success: False, disabled: True}`` when ``policy.enabled`` is false, and
every endpoint requires the admin role.

MVP is API-first (a minimal UI may follow).
"""

from __future__ import annotations

import fnmatch
import logging
import re

from flask import Blueprint, jsonify, request

from app.auth.decorators import admin_required
from app.modules.policy.cache import invalidate_policy_rule_cache

logger = logging.getLogger(__name__)

policy_bp = Blueprint("policy", __name__)

_PATTERN_TYPES = ("glob", "regex")
_EFFECTS = ("allow", "deny", "require_approval")
_POLICY_TYPES = ("model", "provider", "tool_action", "file_path", "command")


@policy_bp.before_request
def _guard():
    """Feature-flag gate for every policy request (mirrors run_timeline_bp)."""
    from app.utils.config import is_policy_enabled

    if not is_policy_enabled():
        return jsonify({"success": False, "disabled": True}), 200


def _validate_pattern(pattern_type: str | None, pattern: str | None):
    """Compile/validate a pattern up front (review M5). Returns an error string or None."""
    if not pattern:
        return None
    ptype = pattern_type or "glob"
    if ptype == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            return f"invalid regex pattern: {e}"
    else:
        # glob always translates; sanity-check it is non-empty after translation.
        if not fnmatch.translate(pattern):
            return "invalid glob pattern"
    return None


def _parse_rule_body(data: dict):
    """Extract + validate rule fields from a JSON body. Returns (fields, error)."""
    rule_key = (data.get("rule_key") or "").strip()
    name = (data.get("name") or "").strip()
    policy_type = data.get("policy_type")
    effect = data.get("effect", "require_approval")

    if not rule_key:
        return None, "rule_key is required"
    if not name:
        return None, "name is required"
    if policy_type not in _POLICY_TYPES:
        return None, f"policy_type must be one of {_POLICY_TYPES}"
    if effect not in _EFFECTS:
        return None, f"effect must be one of {_EFFECTS}"

    pattern_type = data.get("pattern_type", "glob")
    if pattern_type not in _PATTERN_TYPES:
        return None, f"pattern_type must be one of {_PATTERN_TYPES}"
    pattern = data.get("pattern")
    err = _validate_pattern(pattern_type, pattern)
    if err:
        return None, err

    value_list = data.get("value_list")
    if value_list is not None and not isinstance(value_list, list):
        return None, "value_list must be a list"

    fields = {
        "rule_key": rule_key,
        "name": name,
        "policy_type": policy_type,
        "effect": effect,
        "pattern_type": pattern_type,
        "pattern": pattern,
        "value_list": [str(v) for v in value_list] if value_list else None,
        "tool_name": data.get("tool_name"),
        "action": data.get("action"),
        "tenant_id": data.get("tenant_id"),
        "project_path": data.get("project_path"),
        "machine_id": data.get("machine_id"),
        "user_id": data.get("user_id"),
        "team_id": data.get("team_id"),
        "priority": int(data.get("priority", 100)),
        "is_default": bool(data.get("is_default", False)),
        "enabled": bool(data.get("enabled", True)),
        "approval_ttl_seconds": data.get("approval_ttl_seconds"),
        "description": data.get("description"),
    }
    return fields, None


@policy_bp.route("/policy/rules", methods=["GET"])
@admin_required
def list_policy_rules():
    """List current (latest version) policy rules."""
    from app.modules.policy.repo import PolicyRepository

    include_disabled = request.args.get("include_disabled", "false").lower() == "true"
    rules = PolicyRepository().list_current_rules(include_disabled=include_disabled)
    return jsonify({"success": True, "rules": [r.to_dict() for r in rules], "total": len(rules)})


@policy_bp.route("/policy/rules", methods=["POST"])
@admin_required
def create_policy_rule():
    """Create a new policy rule (first version of a rule_key)."""
    from app.modules.policy.repo import PolicyRepository

    data = request.get_json(silent=True) or {}
    fields, err = _parse_rule_body(data)
    if err:
        return jsonify({"error": err}), 400
    fields["created_by"] = g_user_id()
    rule = PolicyRepository().create_rule(**fields)
    invalidate_policy_rule_cache()
    logger.info("Policy rule created: %s v%d", rule.rule_key, rule.version)
    return jsonify({"success": True, "rule": rule.to_dict()}), 201


@policy_bp.route("/policy/rules/<rule_key>", methods=["PUT"])
@admin_required
def update_policy_rule(rule_key: str):
    """Versioned edit: supersede the current version and insert a new one.

    The body is the full new rule definition (same fields as POST). Prior
    versions remain immutable for audit; existing decisions keep their snapshot.
    """
    from app.modules.policy.repo import PolicyRepository

    data = request.get_json(silent=True) or {}
    data["rule_key"] = rule_key
    fields, err = _parse_rule_body(data)
    if err:
        return jsonify({"error": err}), 400
    fields["created_by"] = g_user_id()
    rule = PolicyRepository().create_rule(**fields)
    invalidate_policy_rule_cache()
    logger.info("Policy rule updated: %s v%d", rule.rule_key, rule.version)
    return jsonify({"success": True, "rule": rule.to_dict()})


@policy_bp.route("/policy/rules/<int:rule_id>/enabled", methods=["PATCH"])
@admin_required
def toggle_policy_rule(rule_id: int):
    """Toggle enabled on the current version of a rule."""
    from app.modules.policy.repo import PolicyRepository

    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "enabled is required"}), 400
    updated = PolicyRepository().set_rule_enabled(rule_id, bool(data["enabled"]))
    if not updated:
        return jsonify({"error": "Rule not found or not current"}), 404
    invalidate_policy_rule_cache()
    return jsonify({"success": True})


@policy_bp.route("/policy/decisions", methods=["GET"])
@admin_required
def list_policy_decisions():
    """List policy decisions, filtered by session_id (and optionally request_id)."""
    from app.modules.policy.repo import PolicyRepository

    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    limit = min(max(request.args.get("limit", default=100, type=int), 1), 1000)
    decisions = PolicyRepository().list_decisions(session_id, limit=limit)
    return jsonify(
        {"success": True, "decisions": [d.to_dict() for d in decisions], "total": len(decisions)}
    )


def g_user_id():
    """Best-effort current admin user id (set by admin_required)."""
    from flask import g

    return getattr(g, "user_id", None)
