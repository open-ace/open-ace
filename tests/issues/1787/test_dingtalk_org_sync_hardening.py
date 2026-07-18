#!/usr/bin/env python3
"""TDD tests for DingTalk org-sync hardening (PR #1787 findings).

Covers:
- HIGH: unsafe email-linking + empty-password provisioning must not bind/activate
- HIGH: membership reconciliation must preserve manually-promoted owner/leader roles
- MEDIUM: departed users (not in current snapshot) must be deactivated/unlinked
- LOW: DingTalk API errcode aborts whole run with raw payload (should warn/skip)
- LOW: DingTalk webhook path detection should anchor to /robot/send
- LOW: scheduler hook should log tracebacks (logger.exception), not just message
"""

from __future__ import annotations

import logging

import pytest

import app.utils.smtp_crypto as smtp_crypto
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.repositories.user_repo import UserRepository
from app.services.dingtalk_org_sync import DingTalkDepartment, DingTalkOrgSyncService, DingTalkUser


class FakeDingTalkOrgSyncService(DingTalkOrgSyncService):
    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_access_token(self, app_key, app_secret):
        return "test-token"

    def _fetch_directory_snapshot(self, token, root_department_id, **kwargs):
        return self._departments, self._users


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-dingtalk-hardening-key")
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{tmp_path / 'dingtalk-hardening.db'}")
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


# ---- HIGH: email-linking + empty-password provisioning ----


def test_sync_does_not_auto_link_to_unverified_email_account(sync_env):
    """An existing local account sharing an email must NOT be silently bound to a
    DingTalk SSO identity (email is unverified -> privilege escalation footgun).
    The matched user must be skipped with a warning instead.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)

    # Pre-existing local account that owns the same email.
    pre_existing = user_repo.create_user(
        username="alice_local",
        email="alice@example.com",
        password_hash="real-bcrypt-hash",
        tenant_id=8,
    )
    assert pre_existing is not None

    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Engineering")],
        users=[
            DingTalkUser(
                user_id="dt_manager123",
                name="Alice DingTalk",
                email="alice@example.com",
                department_ids=["100"],
            )
        ],
    )

    result = service.sync_org()

    # Must NOT link: neither a new user created from the pre-existing one, nor linked.
    assert result.users_linked == 0
    # The DingTalk user must have been provisioned as its own (separate) account.
    assert result.users_created == 1
    assert any("alice@example.com" in w for w in result.warnings)

    # The pre-existing local account must NOT have gained a dingtalk SSO identity.
    sso_rows = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (pre_existing,)
    )
    assert sso_rows == []


def test_sync_provisioned_users_are_not_active_with_empty_password(sync_env):
    """Newly provisioned DingTalk users must NOT be active with an empty password hash
    (would allow trivial login). They must be created inactive (or with a non-empty hash).
    """
    db, config = sync_env
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=UserRepository(db=db),
        config_override=config,
        departments=[DingTalkDepartment(department_id="100", name="Engineering")],
        users=[
            DingTalkUser(
                user_id="dt_user1",
                name="Bob DingTalk",
                department_ids=["100"],
            )
        ],
    )

    service.sync_org()

    rows = db.fetch_all(
        "SELECT username, password_hash, is_active FROM users WHERE username LIKE 'bob%'"
    )
    assert rows, "expected the provisioned Bob user to exist"
    row = rows[0]
    # An empty password_hash on an active account is an authentication bypass.
    assert not (
        row["password_hash"] == "" and row["is_active"] in (1, True)
    ), f"provisioned user is active with empty password hash: {row}"


# ---- HIGH: preserve manually-promoted roles on transient dept moves ----


def test_membership_recreate_preserves_promoted_role(sync_env):
    """A user who was promoted to owner/leader on a synced team must keep that role
    when they leave and rejoin the synced department on later sync runs (no silent
    downgrade to 'member').

    Carol stays in the DingTalk directory throughout (so her SSO identity survives
    and she is NOT deactivated), but she temporarily moves out of the synced
    department. The sync-driven remove/add cycle must preserve her promoted role.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)
    dept100 = DingTalkDepartment(department_id="100", name="Engineering")
    # A second department that is NOT in the synced team set, so moving Carol there
    # drops her Engineering membership without deactivating her.
    carol_in_100 = DingTalkUser(
        user_id="dt_carol",
        name="Carol DingTalk",
        department_ids=["100"],
    )
    carol_in_other = DingTalkUser(
        user_id="dt_carol",
        name="Carol DingTalk",
        department_ids=["999"],
    )

    # First sync: Carol is a member of Engineering.
    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept100],
        users=[carol_in_100],
    )
    service.sync_org()
    team = db.fetch_one("SELECT team_id FROM teams WHERE name = ?", ("Engineering",))
    carol = db.fetch_one("SELECT id, username FROM users WHERE username LIKE 'carol%'")
    # Promote Carol manually.
    db.execute(
        "UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?",
        ("owner", team["team_id"], carol["id"]),
    )

    # Second sync: Carol moved to a non-synced department. Her Engineering membership
    # is removed by the sync, but she is still in the directory (not deactivated).
    service2 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept100],
        users=[carol_in_other],
    )
    service2.sync_org()
    members_after_leave = db.fetch_all(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (team["team_id"], carol["id"]),
    )
    assert members_after_leave == [], "expected Carol's Engineering membership removed"

    # Third sync: Carol is back in Engineering. Her promoted role must be restored.
    service3 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept100],
        users=[carol_in_100],
    )
    service3.sync_org()
    members_after_rejoin = db.fetch_all(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (team["team_id"], carol["id"]),
    )
    assert members_after_rejoin, "expected Carol's membership to be restored"
    assert (
        members_after_rejoin[0]["role"] == "owner"
    ), f"promoted role downgraded to member on re-create: {members_after_rejoin}"


# ---- MEDIUM: deactivate departed users ----


def test_departed_users_are_deactivated(sync_env):
    """Users present in a prior sync but absent from the current snapshot must be
    deactivated (and their DingTalk SSO identity unlinked) so a recycled DingTalk
    userid cannot re-resolve to the previous local account.
    """
    db, config = sync_env
    user_repo = UserRepository(db=db)
    dept = DingTalkDepartment(department_id="100", name="Engineering")

    service = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept],
        users=[
            DingTalkUser(
                user_id="dt_dave",
                name="Dave DingTalk",
                department_ids=["100"],
            )
        ],
    )
    service.sync_org()
    dave = db.fetch_one("SELECT id FROM users WHERE username LIKE 'dave%'")
    assert dave is not None
    dave_id = int(dave["id"])
    sso_before = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (dave_id,)
    )
    assert sso_before, "expected Dave to have a dingtalk SSO identity after first sync"

    # Second sync: Dave is no longer in the directory.
    service2 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=config,
        departments=[dept],
        users=[],
    )
    service2.sync_org()

    dave_after = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (dave_id,))
    is_active_val = dave_after["is_active"]
    is_active_bool = bool(is_active_val) if isinstance(is_active_val, int) else is_active_val
    assert (
        not is_active_bool
    ), f"departed DingTalk user was left active (is_active={dave_after['is_active']})"
    sso_after = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?", (dave_id,)
    )
    assert sso_after == [], f"departed user's DingTalk SSO identity was not unlinked: {sso_after}"


# ---- LOW: errcode aborts whole run with raw payload ----


def test_transient_user_lookup_errcode_warns_and_skips():
    """A non-zero errcode on one user lookup must warn-and-skip, not raise and abort
    the entire sync run with a raw payload.
    """

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FlakyHttp:
        def post(self, url, **kwargs):
            if url.endswith("/user/listid"):
                return FakeResponse(
                    {"errcode": 0, "result": {"userid_list": ["dt_erin"], "has_more": False}}
                )
            if url.endswith("/user/get"):
                # Transient DingTalk error (rate-limit / quota).
                return FakeResponse({"errcode": -1, "errmsg": "rate limit"})
            raise AssertionError(f"unexpected URL {url}")

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FlakyHttp(),
    )

    warnings: list[str] = []
    # Must NOT raise.
    users = service._fetch_department_users("token", "100", warnings=warnings)
    assert users == [], "expected the flaky user to be skipped, not returned"
    assert any(
        "dt_erin" in w for w in warnings
    ), f"expected a warning about the failed user; got {warnings}"


def test_request_oapi_error_message_does_not_echo_raw_payload():
    """The RuntimeError raised on a non-zero errcode must carry only errcode/errmsg,
    not the full request payload.
    """

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 88, "errmsg": "quota", "sensitive_field": "secret"}

    class FakeHttp:
        def post(self, url, **kwargs):
            return FakeResponse()

    service = DingTalkOrgSyncService(
        config_override={"dingtalk": {"app_key": "k", "app_secret": "s"}},
        http_session=FakeHttp(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        service._request_oapi(
            "https://oapi.dingtalk.com/topapi/v2/user/get", "tok", {"userid": "x"}
        )
    message = str(exc_info.value)
    assert "sensitive_field" not in message, f"raw payload leaked into error: {message}"
    assert "errcode=88" in message


# ---- LOW: webhook path detection anchored to /robot/send ----


def test_dingtalk_webhook_path_detection_anchored():
    """_is_dingtalk_webhook should match /robot/send and not loosely match any /robot/ path."""
    from app.modules.governance.alert_notifier import AlertNotifier

    notifier = AlertNotifier()
    assert notifier._is_dingtalk_webhook("https://oapi.dingtalk.com/robot/send") is True
    # A different /robot/<x> path that is NOT the send endpoint must not be treated as DingTalk.
    assert (
        notifier._is_dingtalk_webhook("https://oapi.dingtalk.com/robot/other") is False
    ), "loose '/robot/' substring matched a non-send path"


# ---- LOW: scheduler hook logs traceback ----


def test_scheduler_dingtalk_hook_logs_traceback(monkeypatch, caplog):
    """The scheduled DingTalk org-sync hook must log a full traceback (logger.exception),
    not just the exception message string.
    """
    from app.services import data_fetch_scheduler as dfs

    # Force the import inside the hook to raise, exercising the except branch.
    original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def raising_import(name, *args, **kwargs):
        if name == "app.services.dingtalk_org_sync":
            raise ValueError("simulated sync import failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", raising_import)

    scheduler = dfs.DataFetchScheduler()
    with caplog.at_level(logging.ERROR, logger=dfs.__name__):
        scheduler._maybe_sync_dingtalk_org()

    # logger.exception emits records with exc_info attached.
    exc_records = [r for r in caplog.records if r.exc_info]
    assert exc_records, (
        "expected scheduler hook to log a traceback (logger.exception); "
        f"got {[r.message for r in caplog.records]}"
    )
