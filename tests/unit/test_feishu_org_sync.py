"""Unit tests for Feishu org synchronization."""

from __future__ import annotations

import pytest

import app.utils.smtp_crypto as smtp_crypto
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
    """Deterministic Feishu sync service for tests."""

    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_tenant_access_token(self, app_id: str, app_secret: str) -> str:
        assert app_id == "test-app-id"
        assert app_secret == "test-app-secret"
        return "test-token"

    def _fetch_directory_snapshot(self, token: str):
        assert token == "test-token"
        return self._departments, self._users


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Create an isolated SQLite-backed sync environment."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-feishu-org-sync-key")
    smtp_crypto._password_manager_instance = None

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


def test_sync_creates_users_teams_and_memberships(sync_env):
    """A sync run should provision users, teams, memberships, and SSO links."""
    db, config = sync_env
    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=config,
        departments=[
            FeishuDepartment(department_id="dep-eng", name="Engineering"),
            FeishuDepartment(
                department_id="dep-qa",
                name="QA",
                parent_department_id="dep-eng",
            ),
        ],
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
                department_ids=["dep-qa"],
            ),
        ],
    )

    result = service.sync_org()

    assert result.tenant_id == 7
    assert result.departments_seen == 2
    assert result.users_seen == 2
    assert result.teams_created == 2
    assert result.users_created == 2
    assert result.memberships_added == 2
    assert result.warnings == []

    users = db.fetch_all("SELECT username, email, tenant_id FROM users ORDER BY email ASC")
    assert users == [
        {
            "username": "alice",
            "email": "alice@example.com",
            "tenant_id": 7,
        },
        {
            "username": "bob",
            "email": "ou_bob@feishu.local",
            "tenant_id": 7,
        },
    ]

    teams = db.fetch_all("SELECT team_id, name, settings FROM teams ORDER BY name ASC")
    assert [team["name"] for team in teams] == ["Engineering", "QA"]
    assert all(FEISHU_PROVIDER_NAME in team["settings"] for team in teams)

    identities = db.fetch_all(
        """
        SELECT provider_name, provider_user_id
        FROM sso_identities
        ORDER BY provider_user_id ASC
        """
    )
    assert identities == [
        {"provider_name": FEISHU_PROVIDER_NAME, "provider_user_id": "ou_alice"},
        {"provider_name": FEISHU_PROVIDER_NAME, "provider_user_id": "ou_bob"},
    ]

    memberships = db.fetch_all(
        """
        SELECT tm.user_id, t.name AS team_name
        FROM team_members tm
        JOIN teams t ON t.team_id = tm.team_id
        ORDER BY team_name ASC
        """
    )
    assert [row["team_name"] for row in memberships] == ["Engineering", "QA"]


def test_sync_provisions_new_user_and_removes_stale_membership(sync_env):
    """Sync must NOT adopt a pre-existing password account by unverified email
    (security: avoid account takeover). It provisions a fresh local user instead
    and prunes stale memberships on Feishu-managed teams.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Pre-existing password account sharing Alice's email must NOT be adopted.
    existing_user_id = user_repo.create_user(
        username="alice_local",
        email="alice@example.com",
        password_hash="hash",
        tenant_id=7,
    )
    stale_user_id = user_repo.create_user(
        username="stale",
        email="stale@example.com",
        password_hash="hash",
        tenant_id=7,
    )
    assert existing_user_id is not None
    assert stale_user_id is not None

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

    first_result = service.sync_org()
    # A new local user is provisioned; the password account is left untouched.
    assert first_result.users_created == 1
    assert first_result.users_linked == 0

    synced_team = db.fetch_one("SELECT team_id FROM teams WHERE name = ?", ("Engineering",))
    assert synced_team is not None
    db.execute(
        """
        INSERT INTO team_members (team_id, user_id, username, role, joined_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            synced_team["team_id"],
            stale_user_id,
            "stale",
            "member",
            "2026-07-17T00:00:00",
        ),
    )

    second_result = service.sync_org()
    assert second_result.memberships_removed == 1

    members = db.fetch_all(
        "SELECT user_id FROM team_members WHERE team_id = ? ORDER BY user_id ASC",
        (synced_team["team_id"],),
    )
    # Only the freshly provisioned Feishu user remains (not the stale row).
    assert len(members) == 1
    assert members[0]["user_id"] != stale_user_id


def test_scheduler_gate_runs_only_when_enabled(sync_env):
    """Scheduled sync should obey config and interval gates."""
    db, config = sync_env
    service = FakeFeishuOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=config,
        departments=[],
        users=[],
    )

    service.__class__._last_scheduled_sync_at = None

    first = service.maybe_sync_from_scheduler()
    assert first is not None

    second = service.maybe_sync_from_scheduler()
    assert second is None

    disabled = FakeFeishuOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override={"feishu": {**config["feishu"], "org_sync_enabled": False}},
        departments=[],
        users=[],
    )
    assert disabled.maybe_sync_from_scheduler() is None
