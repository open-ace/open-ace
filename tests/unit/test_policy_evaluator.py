"""Unit tests for the PolicyEvaluator.

Pure logic — rules are injected via the cache, no DB. Covers model allowlist,
provider gating, file/command patterns, specificity ranking, effect branches,
fallback, and fail-closed (review M1/M2, plan test matrix).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.policy import evaluator as evmod
from app.modules.policy.evaluator import (
    TARGET_MODEL_SELECTION,
    TARGET_TOOL_ACTION,
    NullPolicyEvaluator,
    PolicyContext,
    PolicyEvaluator,
)
from app.modules.policy.models import Decision


class _Rule:
    """Minimal stand-in rules for the evaluator (avoid DB entirely)."""

    def __init__(self, **kw):
        from app.modules.policy.models import PolicyRule

        self.r = PolicyRule(**kw)


def _rules(*rules):
    return [r.r for r in rules]


@pytest.fixture
def evaluator():
    ev = PolicyEvaluator()

    def _setup(*rules):
        patcher = patch.object(evmod, "get_cached_rules", return_value=_rules(*rules))
        patcher.start()
        return ev

    yield _setup
    patch.stopall()


# ── model selection ─────────────────────────────────────────────────────────


class TestModelPolicy:
    def test_allowlist_allows_listed_model(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="allow",
                name="allow",
                policy_type="model",
                effect="allow",
                value_list=["gpt-4o"],
                priority=10,
            ),
        )
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="gpt-4o", provider="openai")
        )
        assert r.decision == Decision.ALLOW.value
        assert r.matched_rule.rule_key == "allow"

    def test_unlisted_model_falls_back_to_allow(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="allow",
                name="allow",
                policy_type="model",
                effect="allow",
                value_list=["gpt-4o"],
                priority=10,
            ),
        )
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="other", provider="openai")
        )
        assert r.decision == Decision.ALLOW.value
        assert r.fell_back is True

    def test_deny_pattern_blocks_model(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="deny-mini",
                name="deny mini",
                policy_type="model",
                effect="deny",
                pattern=".*mini.*",
                pattern_type="regex",
                priority=5,
            ),
        )
        r = ev.evaluate(
            PolicyContext(
                target_kind=TARGET_MODEL_SELECTION, model="gpt-4o-mini", provider="openai"
            )
        )
        assert r.decision == Decision.DENY.value

    def test_empty_rule_set_allows_model(self, evaluator):
        ev = evaluator()
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="any", provider="x")
        )
        assert r.decision == Decision.ALLOW.value


# ── provider gating ─────────────────────────────────────────────────────────


class TestProviderPolicy:
    def test_provider_allowlist(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="prov",
                name="prov",
                policy_type="provider",
                effect="allow",
                value_list=["anthropic"],
                priority=10,
            ),
        )
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="claude", provider="anthropic")
        )
        assert r.decision == Decision.ALLOW.value
        r2 = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="claude", provider="openai")
        )
        assert r2.decision == Decision.ALLOW.value  # fallback allow (provider not listed)

    def test_provider_deny(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="deny-oai",
                name="deny oai",
                policy_type="provider",
                effect="deny",
                pattern="openai",
                pattern_type="regex",
                priority=5,
            ),
        )
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="x", provider="openai")
        )
        assert r.decision == Decision.DENY.value


# ── tool / file / command ───────────────────────────────────────────────────


class TestToolPolicy:
    def test_file_glob_deny(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="env",
                name="env",
                policy_type="file_path",
                effect="deny",
                pattern="**/.env**",
                pattern_type="glob",
                priority=10,
            ),
        )
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Write",
                "input": {"file_path": "/app/.env"},
            }
        }
        r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr))
        assert r.decision == Decision.DENY.value
        assert r.resource_target == "/app/.env"

    def test_command_regex_deny(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="rmrf",
                name="rmrf",
                policy_type="command",
                effect="deny",
                pattern=r"rm\s+-rf",
                pattern_type="regex",
                priority=10,
            ),
        )
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Bash",
                "input": {"command": "rm -rf /tmp"},
            }
        }
        r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr))
        assert r.decision == Decision.DENY.value

    def test_benign_action_requires_human_fallback(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="rmrf",
                name="rmrf",
                policy_type="command",
                effect="deny",
                pattern=r"rm\s+-rf",
                pattern_type="regex",
                priority=10,
            ),
        )
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Bash",
                "input": {"command": "ls -la"},
            }
        }
        r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr))
        assert r.decision == Decision.REQUIRE_HUMAN.value
        assert r.fell_back is True

    def test_tool_action_explicit_allow(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="allow-read",
                name="allow read",
                policy_type="tool_action",
                effect="allow",
                tool_name="Read",
                priority=10,
            ),
        )
        cr = {
            "request": {"subtype": "permission", "tool_name": "Read", "input": {"file_path": "/a"}}
        }
        r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr))
        assert r.decision == Decision.ALLOW.value

    def test_tool_action_require_approval(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="approve-bash",
                name="approve bash",
                policy_type="tool_action",
                effect="require_approval",
                tool_name="Bash",
                priority=10,
            ),
        )
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Bash",
                "input": {"command": "echo hi"},
            }
        }
        r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr))
        assert r.decision == Decision.REQUIRE_HUMAN.value


# ── specificity ranking ─────────────────────────────────────────────────────


class TestSpecificity:
    def test_machine_scoped_beats_user_scoped(self, evaluator):
        # both match the same request; machine-scoped (more specific) must win
        ev = evaluator(
            _Rule(
                rule_key="user-allow",
                name="u",
                policy_type="command",
                effect="allow",
                pattern=".*",
                pattern_type="regex",
                user_id=5,
                priority=1,
            ),
            _Rule(
                rule_key="machine-deny",
                name="m",
                policy_type="command",
                effect="deny",
                pattern=".*",
                pattern_type="regex",
                machine_id="m1",
                priority=1,
            ),
        )
        cr = {"request": {"subtype": "permission", "tool_name": "Bash", "input": {"command": "x"}}}
        r = ev.evaluate(
            PolicyContext(
                target_kind=TARGET_TOOL_ACTION, control_request=cr, user_id=5, machine_id="m1"
            )
        )
        assert r.decision == Decision.DENY.value
        assert r.matched_rule.rule_key == "machine-deny"

    def test_scope_mismatch_excludes_rule(self, evaluator):
        ev = evaluator(
            _Rule(
                rule_key="deny-machine",
                name="m",
                policy_type="command",
                effect="deny",
                pattern=".*",
                pattern_type="regex",
                machine_id="m-other",
                priority=1,
            ),
        )
        cr = {"request": {"subtype": "permission", "tool_name": "Bash", "input": {"command": "x"}}}
        r = ev.evaluate(
            PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request=cr, machine_id="m1")
        )
        assert r.decision == Decision.REQUIRE_HUMAN.value  # rule didn't match scope → fallback


# ── fail-closed & null evaluator ────────────────────────────────────────────


class TestFailClosed:
    def test_exception_on_tool_fails_to_require_human(self, evaluator):
        ev = evaluator()
        with patch.object(ev, "_evaluate_unsafe", side_effect=RuntimeError("db down")):
            r = ev.evaluate(PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request={}))
            assert r.decision == Decision.REQUIRE_HUMAN.value

    def test_exception_on_model_fails_to_deny(self, evaluator):
        ev = evaluator()
        with patch.object(ev, "_evaluate_unsafe", side_effect=RuntimeError("db down")):
            r = ev.evaluate(PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="x"))
            assert r.decision == Decision.DENY.value


class TestNullEvaluator:
    def test_model_allowed_when_disabled(self):
        r = NullPolicyEvaluator().evaluate(
            PolicyContext(target_kind=TARGET_MODEL_SELECTION, model="x")
        )
        assert r.decision == Decision.ALLOW.value

    def test_tool_requires_human_when_disabled(self):
        r = NullPolicyEvaluator().evaluate(
            PolicyContext(target_kind=TARGET_TOOL_ACTION, control_request={})
        )
        assert r.decision == Decision.REQUIRE_HUMAN.value
