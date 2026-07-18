"""Hardening tests for Feishu org synchronization (review findings on PR #1773).

Each test corresponds to one confirmed review finding and is written to fail
against the unfixed code so the fixes can be verified green afterwards.
"""

from __future__ import annotations

import logging

import pytest

import app.utils.smtp_crypto as smtp_crypto
from app.modules.sso.manager import SSOManager
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.repositories.user_repo import UserRepository
from app.services.feishu_org_sync import (
    FEISHU_PROVIDER_NAME,
    FeishuDepartment,
    FeishuOrgSyncService,
    FeishuUser,
)


class FakeFeishuOrgSyncService(FeishuOrgSyncService):
    """Deterministic Feishu sync service that bypasses live API calls."""

    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_tenant_access_token(self, app_id: str, app_secret: str) -> str:
        return "test-token"

    def _fetch_directory_snapshot(self, token: str):
        return self._departments, self._users


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Create an isolated SQLite-backed sync environment."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-feishu-org-sync-key")
    smtp_crypto._password_manager_instance = None

    # In dev environments a real Postgres URL may be configured globally; the
    # module-level is_postgresql()/adapt_sql() helpers read that global, which
    # would corrupt SQLite-bound repo calls. Force SQLite dialect for the test.
    import app.repositories.database as db_module

    monkeypatch.setattr(db_module, "is_postgresql", lambda: False)

    db = Database(db_url=f"sqlite:///{tmp_path / 'feishu-sync.db'}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")

    config = {
        "feishu": {
            "app_id": "test-app-id",
            "app_secret": "test-app-secret",
            "org_sync_enabled": True,
            "org_sync_tenant_id": 7,
            "org_sync_interval_minutes": 60,
        }
    }

    try:
        yield db, config
    finally:
        smtp_crypto._password_manager_instance = None


# Finding 1: Email-based linking trusts an unverified Feishu email.
def test_sync_does_not_auto_link_to_preexisting_password_account(sync_env):
    """An unverified Feishu email must not bind an SSO identity onto an
    unrelated pre-existing password account. A fresh local user should be
    provisioned instead, leaving the password account untouched.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    preexisting_id = user_repo.create_user(
        username="alice_local",
        email="alice@example.com",
        password_hash="real-bcrypt-hash",
        tenant_id=7,
    )
    assert preexisting_id is not None

    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[FeishuDepartment(department_id="dep-eng", name="Engineering")],
        users=[
            FeishuUser(
                open_id="ou_alice",
                name="Alice",
                email="alice@example.com",
                department_ids=["dep-eng"],
            )
        ],
    )

    result = service.sync_org()
    assert result.users_created == 1

    # The pre-existing password account must keep its original (real) password
    # hash and must NOT gain a Feishu SSO identity binding.
    refreshed = user_repo.get_user_by_id(preexisting_id)
    assert refreshed["password_hash"] == "real-bcrypt-hash"

    sso_for_preexisting = SSOManager(db=db).get_user_by_sso_identity(
        FEISHU_PROVIDER_NAME, "ou_alice"
    )
    assert sso_for_preexisting != preexisting_id

    # The Feishu identity should be linked to the freshly provisioned user.
    assert sso_for_preexisting is not None
    assert sso_for_preexisting != preexisting_id


# Finding 2: Provisioned users created with is_active=True.
def test_provisioned_user_is_inactive_and_marked(sync_env):
    """Auto-provisioned users must be created inactive so they cannot be used
    for login until explicitly activated, and must carry a provenance marker.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[FeishuDepartment(department_id="dep-eng", name="Engineering")],
        users=[
            FeishuUser(
                open_id="ou_bob",
                name="Bob",
                department_ids=["dep-eng"],
            )
        ],
    )

    service.sync_org()

    provisioned = user_repo.get_user_by_username("bob")
    assert provisioned is not None
    assert bool(provisioned["is_active"]) is False
    assert provisioned.get("system_account") == "feishu_org_sync"


# Finding 3: Membership reconciliation overwrites manually-assigned roles.
def test_reconcile_preserves_existing_owner_role(sync_env):
    """When a user is moved between Feishu-synced teams within one sync run,
    a manually-promoted role (owner/leader) must survive the reconcile instead
    of being reset back to 'member'.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[
            FeishuDepartment(department_id="dep-eng", name="Engineering"),
            FeishuDepartment(department_id="dep-qa", name="QA"),
        ],
        users=[
            FeishuUser(
                open_id="ou_alice",
                name="Alice",
                email="alice@example.com",
                department_ids=["dep-eng"],
            )
        ],
    )

    service.sync_org()
    eng = db.fetch_one("SELECT team_id FROM teams WHERE name = ?", ("Engineering",))
    qa = db.fetch_one("SELECT team_id FROM teams WHERE name = ?", ("QA",))
    alice = user_repo.get_user_by_email("alice@example.com")
    assert eng is not None and qa is not None and alice is not None

    # Simulate an admin promoting Alice to owner of Engineering.
    db.execute(
        "UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?",
        ("owner", eng["team_id"], alice["id"]),
    )

    # Next sync: Alice has moved from Engineering to QA. Reconcile must remove
    # her from Engineering and add her to QA, preserving the promoted role.
    moved_service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[
            FeishuDepartment(department_id="dep-eng", name="Engineering"),
            FeishuDepartment(department_id="dep-qa", name="QA"),
        ],
        users=[
            FeishuUser(
                open_id="ou_alice",
                name="Alice",
                email="alice@example.com",
                department_ids=["dep-qa"],
            )
        ],
    )
    second = moved_service.sync_org()
    assert second.memberships_removed == 1
    assert second.memberships_added == 1

    qa_role = db.fetch_one(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (qa["team_id"], alice["id"]),
    )
    assert qa_role is not None
    assert qa_role["role"] == "owner"


# Finding 5: Users removed from Feishu are never deactivated locally.
def test_removed_feishu_user_is_deactivated(sync_env):
    """A user present in the first sync but absent from a later snapshot must
    be deactivated locally (is_active=False) so the stale access surface is
    closed.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[FeishuDepartment(department_id="dep-eng", name="Engineering")],
        users=[
            FeishuUser(
                open_id="ou_alice",
                name="Alice",
                email="alice@example.com",
                department_ids=["dep-eng"],
            ),
            FeishuUser(
                open_id="ou_bob",
                name="Bob",
                email="bob@example.com",
                department_ids=["dep-eng"],
            ),
        ],
    )

    service.sync_org()
    bob = user_repo.get_user_by_email("bob@example.com")
    assert bob is not None
    # Activate Bob to simulate a legitimate user who has been using the system.
    user_repo.update_user(user_id=bob["id"], is_active=True)
    bob_before = user_repo.get_user_by_id(bob["id"])
    assert bool(bob_before["is_active"]) is True

    # Second sync where Bob has been removed from the Feishu directory.
    removed_service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[FeishuDepartment(department_id="dep-eng", name="Engineering")],
        users=[
            FeishuUser(
                open_id="ou_alice",
                name="Alice",
                email="alice@example.com",
                department_ids=["dep-eng"],
            )
        ],
    )
    removed_service.sync_org()

    bob_after = user_repo.get_user_by_id(bob["id"])
    assert bob_after is not None
    assert bool(bob_after["is_active"]) is False


# Finding 6: link_identity UPSERT can silently re-bind identity to a new user.
def test_link_identity_refuses_silent_rebind(sync_env):
    """link_identity must not silently move an SSO identity from one local user
    to another; it should refuse (return False) and leave the binding intact.
    """
    db, _ = sync_env
    user_repo = UserRepository(db=db)
    sso = SSOManager(db=db)

    user_a = user_repo.create_user(
        username="a", email="a@example.com", password_hash="h", tenant_id=7
    )
    user_b = user_repo.create_user(
        username="b", email="b@example.com", password_hash="h", tenant_id=7
    )
    assert user_a and user_b

    assert sso.link_identity(
        user_id=user_a,
        provider_name=FEISHU_PROVIDER_NAME,
        provider_user_id="ou_shared",
        provider_data={"open_id": "ou_shared"},
    )

    # Attempt to rebind the same identity to a different user must be refused.
    rebound = sso.link_identity(
        user_id=user_b,
        provider_name=FEISHU_PROVIDER_NAME,
        provider_user_id="ou_shared",
        provider_data={"open_id": "ou_shared"},
    )
    assert rebound is False

    owner = sso.get_user_by_sso_identity(FEISHU_PROVIDER_NAME, "ou_shared")
    assert owner == user_a


# Finding 7: Broad except in scheduler hook hides traceback.
def test_scheduler_hook_logs_exception_with_traceback(sync_env, caplog):
    """The scheduled Feishu sync hook must log with exception info (traceback),
    not a bare warning that discards it.
    """
    from app.services.data_fetch_scheduler import DataFetchScheduler

    scheduler = DataFetchScheduler.__new__(DataFetchScheduler)

    import app.services.feishu_org_sync as feishu_module

    original = feishu_module.FeishuOrgSyncService

    class BoomService:
        def maybe_sync_from_scheduler(self):
            raise RuntimeError("boom-for-test")

    feishu_module.FeishuOrgSyncService = BoomService
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.data_fetch_scheduler"):
            scheduler._maybe_sync_feishu_org()
    finally:
        feishu_module.FeishuOrgSyncService = original

    matched = [r for r in caplog.records if "Feishu org sync failed" in r.getMessage()]
    assert matched, "expected a failure log record"
    assert matched[0].exc_info is not None, "failure log must include traceback (exc_info)"
