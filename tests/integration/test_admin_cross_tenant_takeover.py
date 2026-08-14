"""Tenant boundary on the admin user-management endpoints.

``admin_required`` authenticates ``admin`` / ``platform_admin`` / ``tenant_admin``
and populates ``g.tenant_id``, but never compared that tenant against the tenant
of the resource being operated on. On ``/api/admin/users/<id>/...`` that gap was
a full cross-tenant account takeover: a tenant admin could reset any user's
password -- a platform admin's included -- and read the new password straight
out of the response body.

Each test below is the exploit, run against the fixed code and asserting the
denial. ``test_..._was_the_exploit`` names spell out the attack that used to
work.

Lineage: Issue #2180 (admin-route tenant isolation), which fixed 22 endpoints
but left ``admin_required`` itself boundary-free.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.regression,
    pytest.mark.issue(2180),
]


# Two tenants, and one platform-level account with no tenant at all.
TENANT_A = 1
TENANT_B = 2

USERS: dict[int, dict] = {
    10: {
        "id": 10,
        "username": "alice",
        "email": "alice@a.example",
        "role": "user",
        "tenant_id": TENANT_A,
        "must_change_password": False,
        "daily_token_quota": 100,
        "monthly_token_quota": 1000,
        "daily_request_quota": 10,
        "monthly_request_quota": 100,
    },
    20: {
        "id": 20,
        "username": "bob",
        "email": "bob@b.example",
        "role": "user",
        "tenant_id": TENANT_B,
        "must_change_password": False,
        "daily_token_quota": 100,
        "monthly_token_quota": 1000,
        "daily_request_quota": 10,
        "monthly_request_quota": 100,
    },
    99: {
        "id": 99,
        "username": "root",
        "email": "root@example",
        "role": "platform_admin",
        "tenant_id": None,
        "must_change_password": False,
    },
}

TENANT_A_ADMIN = {
    "id": 11,
    "username": "a-admin",
    "role": "tenant_admin",
    "tenant_id": TENANT_A,
    "must_change_password": False,
}
PLATFORM_ADMIN = {
    "id": 1,
    "username": "platform",
    "role": "platform_admin",
    "tenant_id": None,
    "must_change_password": False,
}


class _FakeUserRepo:
    """Just enough of UserRepository for the routes under test."""

    def __init__(self, users: dict[int, dict] | None = None):
        self.users = users if users is not None else USERS
        self.password_updates: list[int] = []
        self.deleted: list[int] = []
        self.updated: list[int] = []
        self.created: list[dict] = []

    def get_user_by_id(self, user_id):
        user = self.users.get(int(user_id))
        return dict(user) if user else None

    def get_all_users(self, tenant_id=None, **kwargs):
        return [
            dict(u)
            for u in self.users.values()
            if tenant_id is None or u.get("tenant_id") == tenant_id
        ]

    def get_user_by_username(self, username):
        return None

    def get_user_by_email(self, email):
        return None

    def update_password(self, user_id, password_hash):
        self.password_updates.append(int(user_id))
        return True

    def set_must_change_password(self, user_id, value):
        return True

    def delete_user(self, user_id):
        self.deleted.append(int(user_id))
        return True

    def update_user(self, user_id, **kwargs):
        self.updated.append(int(user_id))
        return True

    def create_user(self, username, email, password_hash, role, **kwargs):
        self.created.append({"username": username, "role": role, **kwargs})
        return 500

    def update_user_quota(self, *args, **kwargs):
        return True


def _request(actor, method, path, *, json_body=None, repo=None, query_string=None, guard_repo=None):
    """Issue one authenticated request against a bare admin blueprint.

    Uses a minimal Flask app rather than ``create_app()`` so the shared
    module-level blueprint singletons are not mutated for other test modules
    (same reasoning as tests/integration/test_admin_tenant_isolation_2180.py).

    ``guard_repo`` overrides only the repository the tenant guard resolves
    through, leaving the view's own ``user_repo`` intact -- so a test can make
    the guard's lookup fail without also breaking the endpoint underneath it.
    """
    from app.routes.admin import admin_bp

    repo = repo or _FakeUserRepo()
    guard_repo = guard_repo if guard_repo is not None else repo

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(admin_bp, url_prefix="/api")

    # TenantService is imported inside the view functions and talks to the
    # tenants table; stub it so these tests need no database at all.
    tenant_service = MagicMock()
    tenant_service.list_tenants.return_value = [
        SimpleNamespace(id=TENANT_A, name="tenant-a"),
        SimpleNamespace(id=TENANT_B, name="tenant-b"),
    ]
    tenant_service.can_add_user.return_value = True
    tenant_service.increment_user_count.return_value = True
    tenant_service.decrement_user_count.return_value = True
    tenant_service.get_tenant.return_value = SimpleNamespace(quota=SimpleNamespace(max_users=100))

    with (
        patch("app.auth.decorators._load_user_from_token", return_value=actor),
        # same_tenant_user_required resolves the target through _load_target_user,
        # which constructs its own repository.
        patch("app.repositories.user_repo.UserRepository", return_value=guard_repo),
        patch("app.routes.admin.user_repo", repo),
        patch("app.routes.admin.audit_logger"),
        patch("app.routes.admin.get_security_settings_cached", return_value=None),
        patch("app.services.tenant_service.TenantService", return_value=tenant_service),
    ):
        client = app.test_client()
        response = client.open(
            path,
            method=method,
            json=json_body,
            query_string=query_string,
            headers={"Authorization": "Bearer test-token"},
        )
    return response, repo


class TestPasswordResetTakeover:
    """POST /api/admin/users/<id>/reset-password."""

    def test_tenant_admin_cannot_reset_other_tenants_user_was_the_exploit(self):
        """The P0: tenant A's admin resets tenant B's user and reads the password."""
        response, repo = _request(TENANT_A_ADMIN, "POST", "/api/admin/users/20/reset-password")

        assert response.status_code == 403, response.get_data(as_text=True)
        assert "temporary_password" not in response.get_json()
        assert repo.password_updates == []

    def test_tenant_admin_cannot_reset_platform_admin_was_the_worst_case(self):
        """User 99 is a platform admin with tenant_id=None -- NULL is not a wildcard."""
        response, repo = _request(TENANT_A_ADMIN, "POST", "/api/admin/users/99/reset-password")

        assert response.status_code == 403
        assert repo.password_updates == []

    def test_tenant_admin_can_still_reset_own_tenants_user(self):
        """The fix must not break the legitimate case it is wrapped around."""
        response, repo = _request(TENANT_A_ADMIN, "POST", "/api/admin/users/10/reset-password")

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["temporary_password"]
        assert repo.password_updates == [10]

    def test_platform_admin_keeps_cross_tenant_reach(self):
        response, repo = _request(PLATFORM_ADMIN, "POST", "/api/admin/users/20/reset-password")

        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.password_updates == [20]

    def test_legacy_admin_keeps_cross_tenant_reach(self):
        """Legacy 'admin' is a platform admin while strict mode is off."""
        legacy = {
            "id": 2,
            "username": "legacy",
            "role": "admin",
            "tenant_id": TENANT_A,
            "must_change_password": False,
        }
        response, repo = _request(legacy, "POST", "/api/admin/users/20/reset-password")

        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.password_updates == [20]


class TestOtherUserTargetedEndpoints:
    """The same gap existed on every sibling endpoint keyed by a user id."""

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("PUT", "/api/admin/users/20", {"username": "pwned"}),
            ("DELETE", "/api/admin/users/20", None),
            ("PUT", "/api/admin/users/20/password", {"password": "NewP@ssw0rd123"}),
            ("PUT", "/api/admin/users/20/quota", {"daily_token_quota": 999999}),
        ],
    )
    def test_tenant_admin_denied_across_tenants(self, method, path, body):
        response, repo = _request(TENANT_A_ADMIN, method, path, json_body=body)

        assert (
            response.status_code == 403
        ), f"{method} {path} returned {response.status_code}: {response.get_data(as_text=True)}"
        assert repo.updated == []
        assert repo.deleted == []
        assert repo.password_updates == []


class TestUserEnumeration:
    """GET /api/admin/users -- the reconnaissance step before a takeover."""

    def test_tenant_admin_listing_is_narrowed_to_own_tenant(self):
        """No tenant_id used to mean 'every tenant'."""
        response, _ = _request(TENANT_A_ADMIN, "GET", "/api/admin/users")

        assert response.status_code == 200
        tenants = {u.get("tenant_id") for u in response.get_json()}
        assert tenants == {TENANT_A}

    def test_tenant_admin_cannot_request_another_tenants_listing(self):
        response, _ = _request(
            TENANT_A_ADMIN, "GET", "/api/admin/users", query_string={"tenant_id": TENANT_B}
        )

        assert response.status_code == 403

    def test_platform_admin_still_sees_every_tenant(self):
        response, _ = _request(PLATFORM_ADMIN, "GET", "/api/admin/users")

        assert response.status_code == 200
        assert {u.get("tenant_id") for u in response.get_json()} == {TENANT_A, TENANT_B, None}


class TestPrivilegeEscalation:
    """A tenant admin minting platform-level accounts is takeover by another route."""

    def test_tenant_admin_cannot_create_platform_admin(self):
        response, repo = _request(
            TENANT_A_ADMIN,
            "POST",
            "/api/admin/users",
            json_body={
                "username": "backdoor",
                "email": "backdoor@a.example",
                "password": "S0me!LongPassword",
                "role": "platform_admin",
                "tenant_id": TENANT_A,
            },
        )

        assert response.status_code == 403
        assert repo.created == []

    def test_tenant_admin_cannot_create_into_another_tenant(self):
        response, repo = _request(
            TENANT_A_ADMIN,
            "POST",
            "/api/admin/users",
            json_body={
                "username": "planted",
                "email": "planted@b.example",
                "password": "S0me!LongPassword",
                "role": "user",
                "tenant_id": TENANT_B,
            },
        )

        assert response.status_code == 403
        assert repo.created == []

    def test_tenant_admin_cannot_promote_own_user_to_platform_admin(self):
        response, repo = _request(
            TENANT_A_ADMIN, "PUT", "/api/admin/users/10", json_body={"role": "platform_admin"}
        )

        assert response.status_code == 403
        assert repo.updated == []

    def test_tenant_admin_cannot_move_own_user_into_another_tenant(self):
        response, repo = _request(
            TENANT_A_ADMIN, "PUT", "/api/admin/users/10", json_body={"tenant_id": TENANT_B}
        )

        assert response.status_code == 403
        assert repo.updated == []

    def test_platform_admin_may_still_create_platform_admins(self):
        response, repo = _request(
            PLATFORM_ADMIN,
            "POST",
            "/api/admin/users",
            json_body={
                "username": "peer",
                "email": "peer@example.com",
                "password": "S0me!LongPassword",
                "role": "platform_admin",
                "tenant_id": TENANT_A,
            },
        )

        # Not 403 -- the tenant-scope guard must not fire for a platform admin.
        # (Quota/creation may still fail on the mocked tenant service; the point
        # here is only that authorization let it through.)
        assert response.status_code != 403


class TestTenantlessAdminFailsClosed:
    """A tenant_admin row with no tenant_id has no scope to confine to."""

    def test_tenant_admin_without_tenant_id_is_denied(self):
        broken = {
            "id": 12,
            "username": "no-tenant",
            "role": "tenant_admin",
            "tenant_id": None,
            "must_change_password": False,
        }
        response, repo = _request(broken, "POST", "/api/admin/users/10/reset-password")

        assert response.status_code == 403
        assert repo.password_updates == []


class _ExplodingUserRepo(_FakeUserRepo):
    """Repository whose target lookup fails, e.g. the database is unreachable."""

    def get_user_by_id(self, user_id):
        raise RuntimeError("database unavailable")


class TestLookupFailureIsFailClosedForTenantAdmins:
    """The target lookup gates access for a tenant admin, so it cannot be optional.

    For a platform admin the same lookup only names the tenant in the audit
    entry, so a failure there must not turn a permitted request into a 500 --
    that asymmetry is deliberate.
    """

    def test_tenant_admin_denied_when_target_cannot_be_resolved(self):
        response, repo = _request(
            TENANT_A_ADMIN,
            "POST",
            "/api/admin/users/10/reset-password",
            guard_repo=_ExplodingUserRepo(),
        )

        assert response.status_code == 403
        assert repo.password_updates == []

    def test_tenant_admin_denied_for_a_user_that_does_not_exist(self):
        """Not a 404 fall-through: api_update_user/api_delete_user have no
        not-found branch, so 'unresolved' must never mean 'allowed'."""
        response, repo = _request(TENANT_A_ADMIN, "DELETE", "/api/admin/users/12345")

        assert response.status_code == 403
        assert repo.deleted == []

    def test_platform_admin_unaffected_by_audit_lookup_failure(self):
        """This is the case CI caught: no database, platform admin, must still work."""
        response, repo = _request(
            PLATFORM_ADMIN,
            "POST",
            "/api/admin/users/20/reset-password",
            guard_repo=_ExplodingUserRepo(),
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.password_updates == [20]
