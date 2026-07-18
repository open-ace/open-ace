#!/usr/bin/env python3
"""TDD tests for PR #1810 round-3 review (severe findings only).

严重#1: scripts/fetch_openclaw.py — a DingTalk message carrying ONLY a
        ``"sender_id"`` metadata field (no ``userid:`` text prefix) must still
        resolve sender_id. Previously the metadata hit assigned a local var and
        fell through, so sender_id was silently dropped.

严重#2: app/services/dingtalk_org_sync.py._deactivate_departed_users — the
        SELECT had no tenant filter, so tenant A's sync would deactivate tenant
        B's DingTalk identities. provider_data now stores ``tenant_id`` and the
        deactivation only touches identities whose tenant_id matches the
        syncing tenant.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import app.utils.smtp_crypto as smtp_crypto
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.repositories.user_repo import UserRepository
from app.services.dingtalk_org_sync import DingTalkDepartment, DingTalkOrgSyncService, DingTalkUser

# ---------------------------------------------------------------------------
# 严重#1 — fetch_openclaw sender_id metadata-only message
# ---------------------------------------------------------------------------


def load_fetch_openclaw():
    module_path = Path(__file__).resolve().parents[3] / "scripts" / "fetch_openclaw.py"
    module_dir = module_path.parent
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    spec = importlib.util.spec_from_file_location("fetch_openclaw", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_dingtalk_metadata_only_message_resolves_sender():
    """A DingTalk message that carries ONLY a "sender_id" metadata field and NO
    ``userid:`` text prefix must still resolve sender_id.

    The round-2 implementation matched the metadata regex but only assigned a
    local variable and fell through to the Slack branch, so the sender was
    silently dropped (sender_id=None). This is the case the existing
    ``test_dingtalk_simple_userid_message_resolves_sender`` test missed, because
    its fixture text ALSO carried a ``manager789:`` prefix that hit the
    return-ing sub-branch.
    """
    mod = load_fetch_openclaw()
    # Metadata only — NO "manager789:" text prefix anywhere.
    text = (
        "Some relayed DingTalk body text here.\n\n"
        '{"message_source": "dingtalk", "sender_id": "manager789"}'
    )
    meta = mod.extract_user_message_metadata(text)
    assert meta["message_source"] == "dingtalk", f"expected message_source=dingtalk, got {meta!r}"
    assert (
        meta.get("sender_id") == "manager789"
    ), f"sender_id not resolved for metadata-only DingTalk message: {meta!r}"


# ---------------------------------------------------------------------------
# 严重#2 — cross-tenant isolation in _deactivate_departed_users
# ---------------------------------------------------------------------------


class FakeDingTalkOrgSyncService(DingTalkOrgSyncService):
    def __init__(self, *args, departments=None, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._departments = list(departments or [])
        self._users = list(users or [])

    def _get_access_token(self, app_key, app_secret):
        return "test-token"

    def _fetch_directory_snapshot(self, token, root_department_id, **kwargs):
        return self._departments, self._users


def _config_for(tenant_id: int) -> dict:
    return {
        "dingtalk": {
            "app_key": "test-app-key",
            "app_secret": "test-app-secret",
            "org_sync_enabled": True,
            "org_sync_tenant_id": tenant_id,
            "org_sync_interval_minutes": 60,
            "org_sync_root_dept_id": "1",
        }
    }


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-dingtalk-round3-key")
    # Pin the dialect to sqlite regardless of the host's configured DATABASE_URL.
    # The org-sync code path calls the module-global is_postgresql(), which reads
    # the host config and would otherwise route sqlite cursors through the
    # psycopg2 RealDictCursor code path and crash. (PR-review item #9.)
    db_path = tmp_path / "dingtalk-round3.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from app.repositories import database as _db_module

    monkeypatch.setattr(_db_module, "is_postgresql", lambda: False)
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{db_path}")
    load_schema_from_file(db_url=db.db_url, dialect="sqlite")

    try:
        yield db
    finally:
        smtp_crypto._password_manager_instance = None


def test_deactivate_departed_users_isolates_by_tenant(sync_env):
    """A sync run for tenant A must NOT deactivate DingTalk identities that
    belong to tenant B, even though both were created by dingtalk_org_sync and
    tenant B's userid is absent from tenant A's snapshot.
    """
    db = sync_env
    user_repo = UserRepository(db=db)
    dept = DingTalkDepartment(department_id="100", name="Engineering")

    # --- Tenant A sync: provisions a DingTalk user in tenant A. ---
    tenant_a_cfg = _config_for(8)
    service_a = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=tenant_a_cfg,
        departments=[dept],
        users=[
            DingTalkUser(
                user_id="dt_alice",
                name="Alice DingTalk",
                department_ids=["100"],
            )
        ],
    )
    service_a.sync_org()
    alice = db.fetch_one("SELECT id FROM users WHERE username LIKE 'alice%'")
    assert alice is not None, "tenant A user should have been provisioned"

    # --- Tenant B sync: provisions a DIFFERENT DingTalk user in tenant B. ---
    tenant_b_cfg = _config_for(9)
    service_b = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=tenant_b_cfg,
        departments=[dept],
        users=[
            DingTalkUser(
                user_id="dt_bob",
                name="Bob DingTalk",
                department_ids=["100"],
            )
        ],
    )
    service_b.sync_org()
    bob = db.fetch_one("SELECT id FROM users WHERE username LIKE 'bob%'")
    assert bob is not None, "tenant B user should have been provisioned"

    # Both identities exist after their respective syncs.
    sso_all = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE provider_name = ?",
        ("dingtalk",),
    )
    assert {r["provider_user_id"] for r in sso_all} == {"dt_alice", "dt_bob"}

    # Simulate Bob being actively used in tenant B (an admin activated him, or he
    # linked another SSO identity). Newly-provisioned DingTalk users start
    # is_active=False by design, so we must activate him explicitly to make the
    # cross-tenant-deactivation assertion meaningful.
    db.execute("UPDATE users SET is_active = 1 WHERE id = ?", (bob["id"],))

    # --- Tenant A re-syncs with an EMPTY snapshot (everyone departed from A). ---
    # This must deactivate dt_alice (tenant A) but must NOT touch dt_bob
    # (tenant B), which is simply not in tenant A's directory.
    service_a2 = FakeDingTalkOrgSyncService(
        db=db,
        user_repo=user_repo,
        config_override=tenant_a_cfg,
        departments=[dept],
        users=[],
    )
    service_a2.sync_org()

    # Tenant A's identity must be deactivated + unlinked.
    alice_after = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (alice["id"],))
    is_active = alice_after["is_active"]
    is_active_bool = bool(is_active) if isinstance(is_active, int) else is_active
    assert not is_active_bool, "tenant A departed user should have been deactivated"
    alice_sso = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?",
        (alice["id"],),
    )
    assert alice_sso == [], "tenant A departed user's SSO identity should be unlinked"

    # Tenant B's identity must be UNTOUCHED: still active + still linked. If
    # tenant A's sync had deactivated Bob or deleted his SSO identity row, that
    # would be a cross-tenant leak.
    bob_after = db.fetch_one("SELECT is_active FROM users WHERE id = ?", (bob["id"],))
    is_active_b = bob_after["is_active"]
    is_active_b_bool = bool(is_active_b) if isinstance(is_active_b, int) else is_active_b
    assert is_active_b_bool, (
        "tenant B user was incorrectly deactivated by tenant A's sync " "(cross-tenant leak)"
    )
    bob_sso = db.fetch_all(
        "SELECT provider_user_id FROM sso_identities WHERE user_id = ?",
        (bob["id"],),
    )
    assert [r["provider_user_id"] for r in bob_sso] == [
        "dt_bob"
    ], "tenant B user's SSO identity was incorrectly removed by tenant A's sync"


# ---------------------------------------------------------------------------
# 建议#8 — DingTalk webhook detection should tolerate a trailing slash
# ---------------------------------------------------------------------------


def test_dingtalk_webhook_detection_tolerates_trailing_slash():
    """A user-configured DingTalk webhook URL with a trailing slash
    (``/robot/send/``) must still be detected as a DingTalk target rather than
    falling through to the default-payload path. The official endpoint is
    ``/robot/send`` (no slash), but we should not penalize operators who append
    one.
    """
    from app.modules.governance.alert_notifier import AlertNotifier

    notifier = AlertNotifier()
    assert (
        notifier._is_dingtalk_webhook("https://oapi.dingtalk.com/robot/send/") is True
    ), "trailing-slash DingTalk webhook was not recognized"
    # The canonical no-slash form must keep working too.
    assert notifier._is_dingtalk_webhook("https://oapi.dingtalk.com/robot/send") is True
    # And a non-send path must still be rejected.
    assert notifier._is_dingtalk_webhook("https://oapi.dingtalk.com/robot/other/") is False
