"""Integration tests for the policy wiring inside RemoteSessionManager.

Deterministic (no live CLI — review M9): a real ``control_request`` payload is
fed to ``process_permission_request`` and ``respond_to_permission``, asserting
the dispatch/buffer behaviour, the single consume chokepoint, the model gate,
and the flag-off legacy path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.modules.policy import evaluator as evmod
from app.modules.policy import get_ddl_statements
from app.modules.policy.evaluator import NullPolicyEvaluator, PolicyEvaluator
from app.modules.policy.repo import PolicyRepository
from app.modules.workspace.remote_session_manager import RemoteSessionManager
from app.repositories.database import Database

_PATCH_TARGETS = (
    "app.modules.policy.repo.is_postgresql",
    "app.modules.policy.is_postgresql",
)


def _make_patch(target, value):
    from unittest.mock import patch as _p

    return _p(target, return_value=value)


@pytest.fixture
def policy_db(tmp_path):
    db_path = str(tmp_path / "policy_int.db")
    db = Database(db_url=f"sqlite:///{db_path}")
    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch.object(db_mod, "is_postgresql", return_value=False))
        for target in _PATCH_TARGETS:
            stack.enter_context(_make_patch(target, False))
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            for sql in get_ddl_statements():
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
        yield db


def _make_manager(policy_db, *, enabled=True, rules=None):
    """Build a manager with mocked deps + a real or Null evaluator.

    ``PolicyRepository`` is patched to bind to the isolated test DB so the
    synchronous decision writes land where the consume chokepoint reads them.
    """
    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    mgr._session_manager = MagicMock()
    mgr._agent_manager = MagicMock()
    mgr._agent_manager.get_machine_for_session.return_value = "machine-1"
    mgr._agent_manager.get_machine.return_value = {"tenant_id": 1, "machine_name": "m"}
    mgr._agent_manager.send_command.return_value = True
    # default session mock with real scalar attrs (avoids MagicMock leaking into
    # fingerprint hashing / SQL binding); per-test code may override.
    default_session = MagicMock()
    default_session.project_path = "/proj"
    default_session.user_id = 1
    default_session.context = {"cli_tool": "qwen-code-cli"}
    mgr._session_manager.get_session.return_value = default_session
    mgr._session_permission_modes = {}
    spy = MagicMock()
    spy.is_noop = False
    mgr._run_recorder = spy
    if enabled:
        mgr._policy_evaluator = PolicyEvaluator()
        # inject rules into the evaluator's cache
        patcher = patch.object(evmod, "get_cached_rules", return_value=rules or [])
        patcher.start()
    else:
        mgr._policy_evaluator = NullPolicyEvaluator()
    # bind repo to the test DB for all lazy imports inside the manager
    test_repo = PolicyRepository(db=policy_db)
    patch("app.modules.policy.repo.PolicyRepository", lambda *a, **k: test_repo).start()
    return mgr, test_repo


def _file_request(path="/app/.env", request_id="rq-file"):
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "permission",
            "tool_name": "Write",
            "input": {"file_path": path},
        },
    }


def _command_request(cmd="rm -rf /tmp", request_id="rq-cmd"):
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "permission",
            "tool_name": "Bash",
            "input": {"command": cmd},
        },
    }


class TestAutoDenyPath:
    def test_denied_file_auto_denied_without_buffering(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="env",
                name="env",
                policy_type="file_path",
                effect="deny",
                pattern="**/.env**",
                pattern_type="glob",
                priority=10,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        mgr.process_permission_request("sess", _file_request())

        # Never buffered to the frontend (no human prompt) ...
        mgr._agent_manager.buffer_output.assert_not_called()
        # ... and a deny permission_response was dispatched.
        mgr._agent_manager.send_command.assert_called_once()
        cmd = mgr._agent_manager.send_command.call_args.args[1]
        assert cmd["command"] == "permission_response"
        assert cmd["behavior"] == "deny"
        # decision row persisted synchronously + already consumed (single-use).
        decisions = repo.list_decisions("sess")
        assert len(decisions) == 1
        assert decisions[0].decision == "deny"
        assert decisions[0].consumed_at is not None
        assert decisions[0].reviewer_identity == "policy"

    def test_blocked_command_auto_denied(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="rmrf",
                name="rmrf",
                policy_type="command",
                effect="deny",
                pattern=r"rm\s+-rf",
                pattern_type="regex",
                priority=10,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        mgr.process_permission_request("sess", _command_request())
        mgr._agent_manager.buffer_output.assert_not_called()
        cmd = mgr._agent_manager.send_command.call_args.args[1]
        assert cmd["behavior"] == "deny"


class TestRequireHumanPath:
    def test_benign_request_is_buffered_for_human(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="rmrf",
                name="rmrf",
                policy_type="command",
                effect="deny",
                pattern=r"rm\s+-rf",
                pattern_type="regex",
                priority=10,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        # benign command → no rule matches → require_human fallback → buffered
        mgr.process_permission_request("sess", _command_request(cmd="ls -la"))
        mgr._agent_manager.buffer_output.assert_called_once()
        entry = mgr._agent_manager.buffer_output.call_args.args[1]
        assert entry["stream"] == "permission"
        mgr._agent_manager.send_command.assert_not_called()
        decisions = repo.list_decisions("sess")
        assert decisions[0].decision == "require_human"
        assert decisions[0].consumed_at is None


class TestConsumeChokepoint:
    def test_human_allow_consumes_then_replay_denies(self, policy_db):
        from datetime import timedelta

        from app.modules.policy.models import PolicyRule, _utcnow_naive

        rules = [
            PolicyRule(
                rule_key="approve",
                name="approve",
                policy_type="tool_action",
                effect="require_approval",
                tool_name="Bash",
                priority=10,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        mgr.process_permission_request("sess", _command_request(cmd="echo hi", request_id="rq-h"))
        # human approves
        ok = mgr.respond_to_permission(
            "sess", "rq-h", "allow", "Bash", decided_by=1, decided_by_name="alice"
        )
        assert ok is True
        cmd = mgr._agent_manager.send_command.call_args.args[1]
        assert cmd["behavior"] == "allow"
        d = repo.get_decision_by_request("rq-h")
        assert d.consumed_at is not None
        assert d.reviewer_identity == "alice"
        # replay the same approval → consume fails → fail closed to deny
        mgr._agent_manager.send_command.reset_mock()
        mgr.respond_to_permission(
            "sess", "rq-h", "allow", "Bash", decided_by=1, decided_by_name="alice"
        )
        cmd2 = mgr._agent_manager.send_command.call_args.args[1]
        assert cmd2["behavior"] == "deny"


class TestFlagOff:
    def test_disabled_policy_buffers_legacy_no_auto_deny(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="env",
                name="env",
                policy_type="file_path",
                effect="deny",
                pattern="**/.env**",
                pattern_type="glob",
                priority=10,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=False, rules=rules)
        mgr.process_permission_request("sess", _file_request())
        # NullEvaluator → require_human → buffered (today's behaviour), no auto-deny
        mgr._agent_manager.buffer_output.assert_called_once()
        mgr._agent_manager.send_command.assert_not_called()
        # no policy decision row written when disabled
        assert repo.list_decisions("sess") == []


class TestModelGate:
    def test_update_model_blocked_for_denied_model(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="deny-mini",
                name="deny mini",
                policy_type="model",
                effect="deny",
                pattern=".*mini.*",
                pattern_type="regex",
                priority=5,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        session = MagicMock()
        session.context = {"cli_tool": "qwen-code-cli"}
        session.project_path = "/p"
        session.user_id = 1
        mgr._session_manager.get_session.return_value = session
        result = mgr.update_model("sess", "gpt-4o-mini")
        assert result is False
        mgr._agent_manager.send_command.assert_not_called()

    def test_update_model_allowed_for_permitted_model(self, policy_db):
        from app.modules.policy.models import PolicyRule

        rules = [
            PolicyRule(
                rule_key="deny-mini",
                name="deny mini",
                policy_type="model",
                effect="deny",
                pattern=".*mini.*",
                pattern_type="regex",
                priority=5,
                id=1,
                version=1,
            ),
        ]
        mgr, repo = _make_manager(policy_db, enabled=True, rules=rules)
        session = MagicMock()
        session.context = {"cli_tool": "qwen-code-cli"}
        session.model = "old"
        session.project_path = "/p"
        session.user_id = 1
        mgr._session_manager.get_session.return_value = session
        assert mgr.update_model("sess", "gpt-4o") is True
        mgr._agent_manager.send_command.assert_called_once()
