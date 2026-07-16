"""Persistence + concurrency tests for PolicyRepository (SQLite-backed).

Exercises the real SQL on an isolated temp database: immutable versioning, the
atomic single-use consume (concurrent-safe), expiry, replay, and record
integrity (review A1/A3, plan persistence/concurrency matrix).
"""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.modules.policy.models import Decision
from app.modules.policy.repo import PolicyRepository
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file

_PATCH_TARGETS = ("app.modules.policy.repo.is_postgresql",)


@pytest.fixture
def policy_db(tmp_path):
    db_path = str(tmp_path / "policy_test.db")
    db = Database(db_url=f"sqlite:///{db_path}")
    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch.object(db_mod, "is_postgresql", return_value=False))
        for target in _PATCH_TARGETS:
            stack.enter_context(_make_patch(target, False))
        load_schema_from_file(db_url=db.db_url, dialect="sqlite")
        yield db


def _make_patch(target, value):
    from unittest.mock import patch as _p

    return _p(target, return_value=value)


@pytest.fixture
def repo(policy_db):
    return PolicyRepository(db=policy_db)


# ── rule versioning ─────────────────────────────────────────────────────────


class TestRuleVersioning:
    def test_create_then_edit_creates_new_version(self, repo):
        r1 = repo.create_rule(
            rule_key="k",
            name="n",
            policy_type="command",
            effect="deny",
            pattern="rm",
            pattern_type="glob",
            created_by=1,
        )
        assert r1.version == 1 and r1.is_current is True
        r2 = repo.create_rule(
            rule_key="k",
            name="n2",
            policy_type="command",
            effect="allow",
            pattern="rm",
            pattern_type="glob",
            created_by=1,
        )
        assert r2.version == 2 and r2.is_current is True
        # old version superseded but immutable
        old = repo.get_rule(r1.id)
        assert old.is_current is False
        assert old.effect == "deny"  # unchanged snapshot
        # only one current row surfaces in evaluation listing
        current = repo.list_current_rules()
        assert len(current) == 1
        assert current[0].version == 2

    def test_decision_pins_immutable_rule_snapshot(self, repo):
        r1 = repo.create_rule(
            rule_key="k",
            name="n",
            policy_type="command",
            effect="allow",
            pattern="x",
            pattern_type="glob",
        )
        did = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="allow",
            policy_rule_id=r1.id,
            policy_rule_version=r1.version,
            fingerprint_hash="h",
            expires_at=_future(hours=1),
        )
        # edit the rule (new version)
        repo.create_rule(
            rule_key="k",
            name="n",
            policy_type="command",
            effect="deny",
            pattern="x",
            pattern_type="glob",
        )
        # the existing decision still references the original (allow) snapshot
        d = repo.get_decision(did)
        assert d.policy_rule_version == 1
        assert d.decision == "allow"


# ── atomic consume ──────────────────────────────────────────────────────────


def _future(hours=1):
    from app.modules.policy.models import _utcnow_naive

    return _utcnow_naive() + timedelta(hours=hours)


class TestConsumeAtomicity:
    def test_consume_succeeds_once_then_replay_fails(self, repo):
        did = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="require_human",
            fingerprint_hash="h",
            expires_at=_future(1),
        )
        assert repo.consume_decision(did, resolved_decision="allow", reviewer_identity="alice") == 1
        assert repo.consume_decision(did, resolved_decision="allow", reviewer_identity="alice") == 0
        d = repo.get_decision(did)
        assert d.consumed_at is not None
        assert d.decision == "allow"
        assert d.reviewer_identity == "alice"

    def test_expired_decision_cannot_be_consumed(self, repo):
        from app.modules.policy.models import _utcnow_naive

        did = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="allow",
            fingerprint_hash="h",
            expires_at=_utcnow_naive() - timedelta(hours=1),
        )
        assert repo.consume_decision(did, resolved_decision="allow") == 0

    def test_fingerprint_mismatch_blocks_consume(self, repo):
        did = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="allow",
            fingerprint_hash="real",
            expires_at=_future(1),
        )
        assert repo.consume_decision(did, resolved_decision="allow", fingerprint_hash="wrong") == 0
        assert repo.consume_decision(did, resolved_decision="allow", fingerprint_hash="real") == 1

    def test_concurrent_consume_exactly_one_winner(self, repo):
        did = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="require_human",
            fingerprint_hash="h",
            expires_at=_future(1),
        )
        results: list[int] = []
        barrier = threading.Barrier(8)

        def race():
            barrier.wait()
            results.append(
                repo.consume_decision(did, resolved_decision="allow", reviewer_identity="t")
            )

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(1) == 1
        assert results.count(0) == 7


# ── record integrity ────────────────────────────────────────────────────────


class TestRecordIntegrity:
    def test_full_field_set_persisted(self, repo):
        rule = repo.create_rule(
            rule_key="k",
            name="n",
            policy_type="file_path",
            effect="deny",
            pattern="**/.env**",
            pattern_type="glob",
            priority=5,
            approval_ttl_seconds=120,
            tenant_id=3,
            description="protect env",
        )
        did = repo.insert_decision(
            request_id="rq-9",
            run_id="sess",
            session_id="sess",
            tenant_id=3,
            workspace_scope="/proj",
            machine_id="m1",
            model=None,
            provider=None,
            tool_name="Write",
            action="permission",
            resource_target="/app/.env",
            args_digest="abc",
            normalization_profile_id="file-path",
            normalization_profile_version=1,
            fingerprint_hash="hh",
            policy_rule_id=rule.id,
            policy_rule_version=rule.version,
            decision="deny",
            reason="protected file",
            reviewer_identity=None,
            expires_at=_future(1),
        )
        d = repo.get_decision(did)
        assert d.request_id == "rq-9"
        assert d.tool_name == "Write"
        assert d.resource_target == "/app/.env"
        assert d.fingerprint_hash == "hh"
        assert d.policy_rule_version == 1
        assert d.reason == "protected file"

    def test_get_decision_by_request_returns_latest(self, repo):
        repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="require_human",
            expires_at=_future(1),
        )
        did2 = repo.insert_decision(
            request_id="rq",
            run_id="s",
            session_id="s",
            decision="require_human",
            expires_at=_future(1),
        )
        d = repo.get_decision_by_request("rq")
        assert d.decision_id == did2

    def test_list_decisions_scoped_to_session(self, repo):
        repo.insert_decision(request_id="a", run_id="s1", session_id="s1", decision="allow")
        repo.insert_decision(request_id="b", run_id="s2", session_id="s2", decision="deny")
        assert len(repo.list_decisions("s1")) == 1
        assert len(repo.list_decisions("s2")) == 1
