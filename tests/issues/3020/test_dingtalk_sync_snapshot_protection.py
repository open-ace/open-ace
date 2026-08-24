#!/usr/bin/env python3
"""Tests for Issue #3020: DingTalk directory read failure empty-snapshot protection.

When the DingTalk directory read fails (all departments, some departments, or
mid-pagination), the sync must NOT proceed with destructive departed-user
cleanup (deactivation + SSO identity deletion) on existing synced users.

Test cases:
1. All departments user fetch fails + existing synced users -> no deactivation
2. Partial department failure -> no deactivation (incomplete snapshot)
3. Mid-pagination failure -> no deactivation
4. Complete snapshot with departed user -> deactivation proceeds normally
5. Complete but empty directory (no users at all) -> no deactivation (defensive)
"""

from __future__ import annotations

import json

import pytest

import app.utils.smtp_crypto as smtp_crypto
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.repositories.user_repo import UserRepository
from app.services.dingtalk_org_sync import (
    DINGTALK_PROVIDER_NAME,
    DingTalkDepartment,
    DingTalkOrgSyncService,
    DingTalkUser,
)


class FakeDingTalkOrgSyncService(DingTalkOrgSyncService):
    """Deterministic DingTalk sync service for tests."""

    def __init__(self, *args, departments=None, users=None, snapshot_complete=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])
        self._snapshot_complete = snapshot_complete

    def _get_access_token(self, app_key: str, app_secret: str) -> str:
        return "test-token"

    def _fetch_directory_snapshot(self, token: str, root_department_id: str, **kwargs):
        return self._departments, self._users, self._snapshot_complete


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Create an isolated SQLite-backed sync environment."""
    from app.utils.encryption_key_registry import reset_registry

    reset_registry()
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-issue-3020-key")
    smtp_crypto._password_manager_instance = None

    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)
    # Patch is_postgresql in collaboration module too (imported at module load time)
    monkeypatch.setattr("app.modules.workspace.collaboration.is_postgresql", lambda: False)

    db = Database(db_url=f"sqlite:///{tmp_path / 'issue3020-sync.db'}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")

    db.execute(
        "INSERT INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (8, "Issue 3020 Test Tenant", "issue3020-test", '{"max_users": 100}'),
    )

    config = {
        "dingtalk": {
            "app_key": "test-app-key",
            "app_secret": "test-app-secret",
            "org_sync_enabled": True,
            "org_sync_tenant_id": 8,
            "org_sync_interval_minutes": 60,
            "org_sync_root_dept_id": "1",
        }
    }

    try:
        yield db, config
    finally:
        smtp_crypto._password_manager_instance = None
        reset_registry()


def _seed_synced_user(db, user_repo, provider_user_id, username, tenant_id=8):
    """Create a local user + DingTalk SSO identity to simulate a prior sync."""
    user_id = user_repo.create_user(
        username=username,
        email=f"{provider_user_id}@dingtalk.local",
        password_hash="!",
        role="user",
        is_active=True,
        tenant_id=tenant_id,
    )
    assert user_id is not None

    provider_data = {
        "user_id": provider_user_id,
        "name": username,
        "email": f"{provider_user_id}@dingtalk.local",
        "department_ids": ["100"],
        "status": {"active": True},
        "synced_by": "dingtalk_org_sync",
        "tenant_id": tenant_id,
    }

    from app.modules.sso.manager import SSOManager

    sso_manager = SSOManager(db=db)
    sso_manager.link_identity(
        user_id=user_id,
        provider_name=DINGTALK_PROVIDER_NAME,
        provider_user_id=provider_user_id,
        provider_data=provider_data,
    )
    return user_id


# ---- Test Case 1: All departments user fetch fails ----


def test_all_departments_fail_does_not_deactivate_existing_users(sync_env):
    """When all department user fetches fail (empty snapshot, incomplete), existing
    synced users must NOT be deactivated or unlinked.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Seed two users from a prior sync
    alice_id = _seed_synced_user(db, user_repo, "dt_alice", "alice_synced")
    bob_id = _seed_synced_user(db, user_repo, "dt_bob", "bob_synced")

    # Simulate a sync where all user fetches failed (incomplete, empty)
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Engineering")],
        users=[],  # No users fetched
        snapshot_complete=False,  # Snapshot is incomplete
    )
    result = service.sync_org()

    # Verify: users are still active
    alice = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (alice_id,))
    bob = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (bob_id,))
    assert bool(alice["is_active"]), "Alice should remain active after incomplete snapshot"
    assert bool(bob["is_active"]), "Bob should remain active after incomplete snapshot"

    # Verify: SSO identities are preserved
    alice_identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (alice_id,)
    )
    bob_identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (bob_id,)
    )
    assert len(alice_identities) == 1, "Alice's SSO identity should be preserved"
    assert len(bob_identities) == 1, "Bob's SSO identity should be preserved"

    # Verify: result has the snapshot_complete flag and warning
    assert result.snapshot_complete is False
    assert any("incomplete" in w.lower() for w in result.warnings)


# ---- Test Case 2: Partial department failure ----


def test_partial_department_failure_does_not_deactivate(sync_env):
    """When some departments succeed but others fail (non-empty but incomplete
    snapshot), departed-user cleanup must NOT run.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Seed users: one in dept 100 (succeeds), one in dept 200 (fails)
    dept100_user_id = _seed_synced_user(db, user_repo, "dt_dept100", "user_dept100")
    dept200_user_id = _seed_synced_user(db, user_repo, "dt_dept200", "user_dept200")

    # Simulate: only dept 100's users fetched, dept 200 failed
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[
            DingTalkDepartment(department_id="100", name="Dept 100"),
            DingTalkDepartment(department_id="200", name="Dept 200"),
        ],
        users=[
            DingTalkUser(
                user_id="dt_dept100",
                name="User Dept 100",
                department_ids=["100"],
            ),
        ],  # Only partial users
        snapshot_complete=False,  # Incomplete due to dept 200 failure
    )
    result = service.sync_org()

    # Both users should remain active
    user_100 = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (dept100_user_id,))
    user_200 = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (dept200_user_id,))
    assert bool(user_100["is_active"]), "Dept 100 user should remain active"
    assert bool(user_200["is_active"]), "Dept 200 user should remain active (not falsely cleaned)"

    assert result.snapshot_complete is False


# ---- Test Case 3: Mid-pagination failure ----


def test_mid_pagination_failure_does_not_deactivate(sync_env):
    """When pagination succeeds for some pages but fails mid-way (incomplete
    snapshot), departed-user cleanup must NOT run.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Seed users
    existing_user_id = _seed_synced_user(db, user_repo, "dt_existing", "existing_user")

    # Simulate: some users fetched before pagination failure
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Engineering")],
        users=[
            DingTalkUser(
                user_id="dt_newuser1",
                name="New User 1",
                department_ids=["100"],
            ),
        ],  # Some users, but pagination didn't complete
        snapshot_complete=False,
    )
    result = service.sync_org()

    # Existing user should not be deactivated
    existing = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (existing_user_id,))
    assert bool(
        existing["is_active"]
    ), "Existing user should remain active after partial pagination"

    # SSO identity preserved
    identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (existing_user_id,)
    )
    assert len(identities) == 1, "SSO identity should be preserved"

    assert result.snapshot_complete is False


# ---- Test Case 4: Complete snapshot with departed user ----


def test_complete_snapshot_still_deactivates_departed_users(sync_env):
    """When the snapshot is complete and a user is genuinely absent, deactivation
    should proceed normally.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)
    dept = DingTalkDepartment(department_id="100", name="Engineering")

    # First sync: two users
    service1 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept],
        users=[
            DingTalkUser(
                user_id="dt_staying",
                name="Staying User",
                department_ids=["100"],
            ),
            DingTalkUser(
                user_id="dt_leaving",
                name="Leaving User",
                department_ids=["100"],
            ),
        ],
        snapshot_complete=True,
    )
    result1 = service1.sync_org()
    assert result1.users_created == 2

    # Find the leaving user's local ID
    leaving = db.fetch_one("SELECT id FROM users WHERE username LIKE 'leaving%'")
    assert leaving is not None
    leaving_id = int(leaving["id"])

    # Second sync: only one user remains (complete snapshot)
    service2 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept],
        users=[
            DingTalkUser(
                user_id="dt_staying",
                name="Staying User",
                department_ids=["100"],
            ),
        ],
        snapshot_complete=True,  # Complete snapshot
    )
    result2 = service2.sync_org()

    # Leaver should be deactivated
    leaver_after = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (leaving_id,))
    is_active = (
        bool(leaver_after["is_active"])
        if isinstance(leaver_after["is_active"], int)
        else leaver_after["is_active"]
    )
    assert not is_active, "Departed user should be deactivated with complete snapshot"

    # Leaver's SSO identity should be deleted
    leaver_identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (leaving_id,)
    )
    assert len(leaver_identities) == 0, "Departed user's SSO identity should be deleted"

    # Staying user's SSO identity should be preserved (not deactivated)
    staying = db.fetch_one("SELECT id FROM users WHERE username LIKE 'staying%'")
    staying_identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (int(staying["id"]),)
    )
    assert len(staying_identities) == 1, "Staying user's SSO identity should be preserved"

    assert result2.snapshot_complete is True


# ---- Test Case 5: Complete but empty directory ----


def test_complete_empty_directory_does_not_deactivate(sync_env):
    """When the snapshot is complete but empty (genuinely no users), the defensive
    empty-set guard prevents mass deactivation. This is a safe default: a truly
    empty org is rare, and it's safer to not mass-deactivate.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Seed a user from a prior sync
    existing_id = _seed_synced_user(db, user_repo, "dt_existing", "existing_user")

    # Simulate a complete but empty snapshot (org was emptied)
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Empty Dept")],
        users=[],  # No users at all
        snapshot_complete=True,  # But the snapshot IS complete
    )
    result = service.sync_org()

    # Defensive guard: even with complete snapshot, empty seen-set skips cleanup
    existing_after = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (existing_id,))
    assert bool(
        existing_after["is_active"]
    ), "Empty complete snapshot should NOT deactivate users (defensive guard)"

    identities = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (existing_id,)
    )
    assert len(identities) == 1, "SSO identity should be preserved with empty snapshot"

    assert result.snapshot_complete is True


# ---- Test: snapshot_complete field in result ----


def test_result_includes_snapshot_complete_field(sync_env):
    """The sync result should expose the snapshot_complete flag for API consumers."""
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Complete sync
    service_ok = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Eng")],
        users=[DingTalkUser(user_id="u1", name="User", department_ids=["100"])],
        snapshot_complete=True,
    )
    result_ok = service_ok.sync_org()
    assert result_ok.snapshot_complete is True
    result_dict = result_ok.to_dict()
    assert "snapshot_complete" in result_dict
    assert result_dict["snapshot_complete"] is True

    # Incomplete sync
    service_fail = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[],
        users=[],
        snapshot_complete=False,
    )
    result_fail = service_fail.sync_org()
    assert result_fail.snapshot_complete is False
    result_fail_dict = result_fail.to_dict()
    assert result_fail_dict["snapshot_complete"] is False


# ---- Test: _fetch_department_users returns completeness tuple ----


def test_fetch_department_users_returns_completeness_tuple(monkeypatch):
    """_fetch_department_users should return (users, complete) tuple."""
    import app.services.dingtalk_org_sync as dt_module

    monkeypatch.setattr(dt_module, "_TRANSIENT_SLEEP", lambda _s: None)

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    # Test successful fetch returns (users, True)
    class OkHttp:
        def post(self, url, **kwargs):
            return FakeResponse(
                {
                    "errcode": 0,
                    "result": {
                        "has_more": False,
                        "list": [{"userid": "u1", "name": "User 1", "dept_id_list": [100]}],
                    },
                }
            )

    service_ok = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=OkHttp(),
    )
    users, complete = service_ok._fetch_department_users("token", "100")
    assert complete is True
    assert len(users) == 1
    assert users[0].user_id == "u1"

    # Test failed fetch returns (users, False)
    class FailHttp:
        def post(self, url, **kwargs):
            return FakeResponse({"errcode": 60011, "errmsg": "no permission"})

    service_fail = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FailHttp(),
    )
    warnings = []
    users_fail, complete_fail = service_fail._fetch_department_users(
        "token", "100", warnings=warnings
    )
    assert complete_fail is False
    assert users_fail == []
