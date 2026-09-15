"""Issue #3396 review findings 2+3: admin routes must enroll accounts into
the TENANT-scoped shared group, and a minimal tenant move must still drop the
old tenant's group.

Finding 2: ``POST /api/admin/users`` used to call
``ensure_system_user(system_account, uid=uid)`` without ``tenant_id`` even
though the tenant IS in scope — every admin-created user landed in the
``openace-shared-0`` platform pseudo-tenant group permanently.

Finding 3: the old-tenant group drop in ``PUT /api/admin/users/<id>`` was
gated on ``if system_account:`` where ``system_account`` came from the
REQUEST BODY — a legal minimal move ``{"tenant_id": 2}`` silently skipped
BOTH the new-tenant enroll and the old-group removal, so the account kept
OS read/write on the old tenant's shared projects forever.

Route-level tests (admin blueprint under a stubbed auth layer), following
the test_project_revocation_reclaim_3396.py convention.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.routes.admin import admin_bp

pytestmark = [pytest.mark.regression, pytest.mark.issue(3396)]

PLATFORM_ADMIN = {
    "id": 1,
    "username": "root-admin",
    "role": "platform_admin",
    "tenant_id": None,
}


@pytest.fixture
def admin_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(admin_bp, url_prefix="/api")
    return app


def _auth_stubs():
    """Request-time auth stubs: a platform admin via Bearer token.

    The decorators resolve identity at REQUEST time
    (_load_user_from_token / resolve_admin_tenant_scope), so patching those
    works regardless of module import order."""
    return (
        patch("app.auth.decorators._load_user_from_token", return_value=dict(PLATFORM_ADMIN)),
        patch("app.auth.decorators.enforce_password_change_requirement", return_value=None),
        patch("app.auth.decorators.resolve_admin_tenant_scope", return_value=(None, None)),
        patch("app.auth.decorators._load_target_user", return_value=None),
    )


def _tenant_scope_stub():
    # admin.py's own scope guard: pass the requested tenant through.
    return patch(
        "app.routes.admin.enforce_requested_tenant_scope",
        side_effect=lambda tid: (tid, None),
    )


class TestAdminCreateUserTenantEnrollment:
    def test_create_enrolls_into_requested_tenant_group(self, admin_app):
        repo = MagicMock()
        repo.get_user_by_username.return_value = None
        repo.get_user_by_email.return_value = None
        repo.get_soft_deleted_user_by_username.return_value = None
        repo.get_soft_deleted_user_by_email.return_value = None
        repo.create_user.return_value = 42
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = True
        tenant_service.increment_user_count.return_value = True
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.validate_username", return_value=True),
                patch("app.routes.admin.validate_email", return_value=True),
                patch("app.routes.admin.validate_password", return_value=(True, "")),
                patch("app.routes.admin.hash_password", return_value="hashed"),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().post(
                    "/api/admin/users",
                    json={
                        "username": "dave",
                        "email": "dave@example.com",
                        "password": "pw-long-enough",
                        "role": "user",
                        "tenant_id": 5,
                        "system_account": "dave-acct",
                    },
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()

        assert resp.status_code == 201, resp.get_json()
        mock_ensure.assert_called_once_with("dave-acct", uid=None, tenant_id=5)

    def test_create_never_enrolls_into_pseudo_group_zero(self, admin_app):
        """The regression this finding describes: tenant_id omitted from the
        ensure_system_user call mapped the account onto openace-shared-0."""
        repo = MagicMock()
        repo.get_user_by_username.return_value = None
        repo.get_user_by_email.return_value = None
        repo.get_soft_deleted_user_by_username.return_value = None
        repo.get_soft_deleted_user_by_email.return_value = None
        repo.create_user.return_value = 43
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = True
        tenant_service.increment_user_count.return_value = True
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.validate_username", return_value=True),
                patch("app.routes.admin.validate_email", return_value=True),
                patch("app.routes.admin.validate_password", return_value=(True, "")),
                patch("app.routes.admin.hash_password", return_value="hashed"),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().post(
                    "/api/admin/users",
                    json={
                        "username": "erin",
                        "email": "erin@example.com",
                        "password": "pw-long-enough",
                        "role": "user",
                        "tenant_id": 7,
                        "system_account": "erin-acct",
                    },
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()

        assert resp.status_code == 201, resp.get_json()
        assert mock_ensure.call_count == 1
        passed_tenant = mock_ensure.call_args.kwargs.get("tenant_id")
        assert passed_tenant == 7, "must enroll into the request tenant, not the pseudo-tenant 0"


class TestAdminTenantMoveGroupDrop:
    DB_ROW = {
        "id": 9,
        "username": "mover",
        "system_account": "mover-acct",
        "tenant_id": 1,
        "role": "user",
        "is_active": True,
    }

    def test_minimal_move_body_drops_old_tenant_group(self, admin_app):
        """PUT {"tenant_id": 2} with NO system_account field must still drop
        the old tenant's group — the DB row's account is derived like the
        deactivation path does."""
        repo = MagicMock()
        repo.get_user_by_id.return_value = dict(self.DB_ROW)
        repo.update_user.return_value = True
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = True
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.utils.workspace.remove_user_from_shared_group") as mock_remove,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().put(
                    "/api/admin/users/9",
                    json={"tenant_id": 2},
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()

        assert resp.status_code == 200, resp.get_json()
        mock_remove.assert_called_once_with("mover-acct", tenant_id=1)
        mock_ensure.assert_called_once_with("mover-acct", uid=None, tenant_id=2)
        # Round-2 review N2: the derivation must NOT leak into the DB write —
        # the body omitted system_account, so update_user gets None
        # (unchanged) rather than a backfilled account.
        assert repo.update_user.call_args.kwargs.get("system_account") is None

    def test_minimal_move_enrolls_target_tenant_not_pseudo_zero(self, admin_app):
        repo = MagicMock()
        repo.get_user_by_id.return_value = dict(self.DB_ROW)
        repo.update_user.return_value = True
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = True
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.utils.workspace.remove_user_from_shared_group"),
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().put(
                    "/api/admin/users/9",
                    json={"tenant_id": 3},
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()

        assert resp.status_code == 200, resp.get_json()
        assert mock_ensure.call_args.kwargs.get("tenant_id") == 3

    def _move(self, admin_app, row, body, *, quota_ok=True):
        """PUT the body against /api/admin/users/9 with standard stubs;
        returns (response, repo, mock_ensure, mock_remove)."""
        repo = MagicMock()
        repo.get_user_by_id.return_value = dict(row)
        repo.update_user.return_value = True
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = quota_ok
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.utils.workspace.remove_user_from_shared_group") as mock_remove,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().put(
                    "/api/admin/users/9",
                    json=body,
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()
        return resp, repo, mock_ensure, mock_remove

    def test_move_on_mapping_less_user_never_backfills_username(self, admin_app):
        """Round-2 review N2: a minimal move on a row with system_account
        NULL must not provision username as an OS account nor write a
        system_account mapping back — multi-user mode removed that
        auto-backfill convention."""
        row = dict(self.DB_ROW, system_account=None)
        resp, repo, mock_ensure, mock_remove = self._move(admin_app, row, {"tenant_id": 2})
        assert resp.status_code == 200, resp.get_json()
        mock_ensure.assert_not_called()
        mock_remove.assert_not_called()
        assert repo.update_user.call_args.kwargs.get("system_account") is None

    def test_clear_mapping_move_still_drops_old_tenant_group(self, admin_app):
        """Round-2 review N3a: an explicit "" (clear-the-mapping) combined
        with a tenant move must still drop the OLD account from the old
        tenant's group — clearing the mapping does not un-share the files
        the old account can still reach."""
        resp, repo, mock_ensure, mock_remove = self._move(
            admin_app, dict(self.DB_ROW), {"system_account": "", "tenant_id": 2}
        )
        assert resp.status_code == 200, resp.get_json()
        mock_remove.assert_called_once_with("mover-acct", tenant_id=1)
        # "" is honored for the DB write (clear), and there is nothing left
        # to enroll under.
        assert repo.update_user.call_args.kwargs.get("system_account") == ""
        mock_ensure.assert_not_called()

    def test_quota_rejected_move_leaves_no_group_grant(self, admin_app):
        """Round-3 review R1: a move rejected by the target tenant's quota
        must leave NO trace — no enrollment into the target tenant's shared
        group, no old-group drop, no DB write. The pre-R1 order enrolled
        before the quota check, permanently granting a rejected request's
        account OS read/write on the target tenant's shared projects."""
        tenant_service = MagicMock()
        tenant_service.can_add_user.return_value = False
        tenant_service.get_tenant.return_value = MagicMock(quota=MagicMock(max_users=1))
        repo = MagicMock()
        repo.get_user_by_id.return_value = dict(self.DB_ROW)
        auth_stubs = _auth_stubs()
        for s in auth_stubs:
            s.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                patch("app.services.tenant_service.TenantService", return_value=tenant_service),
                _tenant_scope_stub(),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.utils.workspace.remove_user_from_shared_group") as mock_remove,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().put(
                    "/api/admin/users/9",
                    json={"tenant_id": 2},
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s in auth_stubs:
                s.stop()

        assert resp.status_code == 400
        mock_ensure.assert_not_called()
        mock_remove.assert_not_called()
        repo.update_user.assert_not_called()

    def test_remap_move_drops_OLD_account_not_the_new_one(self, admin_app):
        """Round-2 review N3b: remap + move in one request must drop the
        PRE-WRITE row account from the old tenant's group — the body's new
        account was never a member there — while enrolling the new account
        for the target tenant."""
        resp, repo, mock_ensure, mock_remove = self._move(
            admin_app, dict(self.DB_ROW), {"system_account": "new-acct", "tenant_id": 2}
        )
        assert resp.status_code == 200, resp.get_json()
        mock_remove.assert_called_once_with("mover-acct", tenant_id=1)
        mock_ensure.assert_called_once_with("new-acct", uid=None, tenant_id=2)
        assert repo.update_user.call_args.kwargs.get("system_account") == "new-acct"


class TestDeactivatedUserNoReenroll:
    """PR #3402 review: gate the post-success enroll on the user's FINAL
    active state. A PUT without ``is_active`` on a DEACTIVATED row (e.g.
    ``{"role": "manager"}`` from an API client — the UI always sends
    ``is_active``, API clients often do not) used to re-run
    ensure_system_user on the exists-path: the tenant content group the
    deactivation dropped was re-granted, the nologin placeholder was
    re-shelled, and the boot sync (which mirrors the DB row) preserved the
    grant — the deactivate-drop only fires when ``is_active=false`` is
    REQUESTED, so it could not undo this."""

    ROW = {
        "id": 11,
        "username": "sleeper",
        "system_account": "sleeper-acct",
        "tenant_id": 4,
        "role": "user",
        "is_active": False,
    }

    def _put(self, admin_app, row, body):
        repo = MagicMock()
        repo.get_user_by_id.return_value = dict(row)
        repo.update_user.return_value = True
        auth_stubs = _auth_stubs()
        for s_ in auth_stubs:
            s_.start()
        try:
            with (
                patch("app.routes.admin.user_repo", repo),
                _tenant_scope_stub(),
                patch("app.routes.admin.ensure_system_user") as mock_ensure,
                patch("app.utils.workspace.remove_user_from_shared_group") as mock_remove,
                patch("app.routes.admin.audit_logger"),
            ):
                resp = admin_app.test_client().put(
                    "/api/admin/users/11",
                    json=body,
                    headers={"Authorization": "Bearer t"},
                )
        finally:
            for s_ in auth_stubs:
                s_.stop()
        return resp, mock_ensure, mock_remove

    def test_put_without_is_active_on_deactivated_row_does_not_enroll(self, admin_app):
        resp, mock_ensure, mock_remove = self._put(admin_app, dict(self.ROW), {"role": "manager"})
        assert resp.status_code == 200, resp.get_json()
        mock_ensure.assert_not_called(), "a user who ends up INACTIVE must never be enrolled"
        mock_remove.assert_not_called(), "no is_active=false requested — nothing to drop"

    def test_explicit_reactivation_enrolls_again(self, admin_app):
        """The gate's other edge: a PUT that makes the user ACTIVE (final
        state) must still enroll — the enroll must not become dead code."""
        resp, mock_ensure, _mock_remove = self._put(admin_app, dict(self.ROW), {"is_active": True})
        assert resp.status_code == 200, resp.get_json()
        mock_ensure.assert_called_once_with("sleeper-acct", uid=None, tenant_id=4)
