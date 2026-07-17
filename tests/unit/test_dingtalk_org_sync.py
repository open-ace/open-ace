"""Unit tests for DingTalk org synchronization."""

from __future__ import annotations

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

    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_access_token(self, app_key: str, app_secret: str) -> str:
        assert app_key == "test-app-key"
        assert app_secret == "test-app-secret"
        return "test-token"

    def _fetch_directory_snapshot(self, token: str, root_department_id: str):
        assert token == "test-token"
        assert root_department_id == "1"
        return self._departments, self._users


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Create an isolated SQLite-backed sync environment."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-dingtalk-org-sync-key")
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{tmp_path / 'dingtalk-sync.db'}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")

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


def test_sync_creates_users_teams_memberships_and_sso_links(sync_env):
    """A sync run should provision DingTalk users, teams, memberships, and SSO links."""
    db, config = sync_env
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=config,
        departments=[
            DingTalkDepartment(department_id="100", name="Engineering"),
            DingTalkDepartment(
                department_id="200",
                name="QA",
                parent_department_id="100",
            ),
        ],
        users=[
            DingTalkUser(
                user_id="manager123",
                name="Alice DingTalk",
                email="alice@example.com",
                department_ids=["100"],
            ),
            DingTalkUser(
                user_id="staff456",
                name="Bob DingTalk",
                department_ids=["200"],
            ),
        ],
    )

    result = service.sync_org()

    assert result.tenant_id == 8
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
            "tenant_id": 8,
        },
        {
            "username": "bob_dingtalk",
            "email": "staff456@dingtalk.local",
            "tenant_id": 8,
        },
    ]

    teams = db.fetch_all("SELECT team_id, name, settings FROM teams ORDER BY name ASC")
    assert [team["name"] for team in teams] == ["Engineering", "QA"]
    assert all(DINGTALK_PROVIDER_NAME in team["settings"] for team in teams)

    identities = db.fetch_all(
        """
        SELECT provider_name, provider_user_id
        FROM sso_identities
        ORDER BY provider_user_id ASC
        """
    )
    assert identities == [
        {"provider_name": DINGTALK_PROVIDER_NAME, "provider_user_id": "manager123"},
        {"provider_name": DINGTALK_PROVIDER_NAME, "provider_user_id": "staff456"},
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


def test_sync_reuses_existing_user_by_email_and_removes_stale_membership(sync_env):
    """Sync should link by email and prune memberships on DingTalk-managed teams."""
    db, config = sync_env
    user_repo = UserRepository(db=db)

    existing_user_id = user_repo.create_user(
        username="alice_local",
        email="alice@example.com",
        password_hash="hash",
        tenant_id=8,
    )
    stale_user_id = user_repo.create_user(
        username="stale",
        email="stale@example.com",
        password_hash="hash",
        tenant_id=8,
    )
    assert existing_user_id is not None
    assert stale_user_id is not None

    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Engineering")],
        users=[
            DingTalkUser(
                user_id="manager123",
                name="Alice DingTalk",
                email="alice@example.com",
                department_ids=["100"],
            )
        ],
    )

    first_result = service.sync_org()
    assert first_result.users_created == 0
    assert first_result.users_linked == 1

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
            "2026-07-18T00:00:00",
        ),
    )

    second_result = service.sync_org()
    assert second_result.memberships_removed == 1

    members = db.fetch_all(
        "SELECT user_id FROM team_members WHERE team_id = ? ORDER BY user_id ASC",
        (synced_team["team_id"],),
    )
    assert members == [{"user_id": existing_user_id}]


def test_scheduler_gate_runs_only_when_enabled(sync_env):
    """Scheduled sync should obey config and interval gates."""
    db, config = sync_env
    service = FakeDingTalkOrgSyncService(
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

    disabled = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override={"dingtalk": {**config["dingtalk"], "org_sync_enabled": False}},
        departments=[],
        users=[],
    )
    assert disabled.maybe_sync_from_scheduler() is None


def test_fetch_directory_snapshot_uses_dingtalk_department_and_user_apis():
    """API parsing should match DingTalk department/user endpoint shapes."""

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeHttp:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/department/listsub"):
                dept_id = kwargs["json"]["dept_id"]
                if dept_id == 1:
                    return FakeResponse(
                        {
                            "errcode": 0,
                            "result": [{"dept_id": 100, "name": "Engineering"}],
                        }
                    )
                return FakeResponse({"errcode": 0, "result": []})
            if url.endswith("/user/listid"):
                dept_id = kwargs["json"]["dept_id"]
                userids = ["manager123"] if dept_id == 100 else []
                return FakeResponse(
                    {
                        "errcode": 0,
                        "result": {"userid_list": userids, "has_more": False},
                    }
                )
            if url.endswith("/user/get"):
                return FakeResponse(
                    {
                        "errcode": 0,
                        "result": {
                            "userid": "manager123",
                            "name": "Alice DingTalk",
                            "email": "alice@example.com",
                            "dept_id_list": [100],
                        },
                    }
                )
            raise AssertionError(f"unexpected URL {url}")

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "unused", "app_secret": "unused"}},
        http_session=FakeHttp(),
    )

    departments, users = service._fetch_directory_snapshot("token", "1")

    assert departments == [DingTalkDepartment(department_id="100", name="Engineering")]
    assert users == [
        DingTalkUser(
            user_id="manager123",
            name="Alice DingTalk",
            email="alice@example.com",
            department_ids=["100"],
            status={},
        )
    ]
