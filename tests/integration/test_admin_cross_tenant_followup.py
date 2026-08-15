"""Tenant boundary on the remaining admin resource endpoints (follow-up to #2180).

The account-takeover fix closed the user-keyed admin endpoints. ``admin_required``
still authenticates ``admin`` / ``platform_admin`` / ``tenant_admin`` without
comparing that tenant against the resource being touched, so the resource-keyed
and request-scoped remainder of the admin surface was left boundary-free. This
module is the exploit-and-denial suite for that remainder:

* **policy rules** -- a rule's ``tenant_id`` is a scope field (which tenant the
  rule governs; ``None`` = global). A tenant admin could create/supersede/toggle
  another tenant's rule, or a global rule that governs everyone.
* **content-filter mutations** -- ``content_filter_rules`` has no tenant column
  at all; the table is global, so mutating it is a platform-level operation and
  the endpoints are now ``platform_admin_required``.
* **quota alert acknowledge** -- ``quota_alerts`` has no tenant column but
  ``user_id`` is NOT NULL, so the owning tenant is resolved through the user.
* **compliance report read/generate** -- ``compliance_reports`` carries a
  ``tenant_id``; a tenant admin could read another tenant's report by id, and
  ``generate_report`` served the caller's own report instead of denying when a
  tenant admin named someone else's tenant.

Each denial test is the exploit run against the fixed code. The
``test_..._is_denied`` / ``..._was_the_hole`` names spell out what used to work.

Lineage: Issue #2180 (admin-route tenant isolation).
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


TENANT_A = 1
TENANT_B = 2

TENANT_A_ADMIN = {
    "id": 11,
    "username": "a-admin",
    "role": "tenant_admin",
    "tenant_id": TENANT_A,
    "must_change_password": False,
}
TENANT_B_ADMIN = {
    "id": 21,
    "username": "b-admin",
    "role": "tenant_admin",
    "tenant_id": TENANT_B,
    "must_change_password": False,
}
PLATFORM_ADMIN = {
    "id": 1,
    "username": "platform",
    "role": "platform_admin",
    "tenant_id": None,
    "must_change_password": False,
}
# Legacy 'admin' is a platform-level role while strict mode is off (the default).
LEGACY_ADMIN = {
    "id": 2,
    "username": "legacy",
    "role": "admin",
    "tenant_id": TENANT_A,
    "must_change_password": False,
}
# A plain tenant user must never reach these admin endpoints at all.
TENANT_A_USER = {
    "id": 12,
    "username": "a-user",
    "role": "user",
    "tenant_id": TENANT_A,
    "must_change_password": False,
}

# Alert owners, keyed by user id, for the quota-alert acknowledge tests.
ALERT_USERS: dict[int, dict] = {
    10: {"id": 10, "username": "alice", "role": "user", "tenant_id": TENANT_A},
    20: {"id": 20, "username": "bob", "role": "user", "tenant_id": TENANT_B},
}


class _FakeUserRepo:
    """Just enough of UserRepository for the alert-acknowledge reverse lookup."""

    def __init__(self, users: dict[int, dict] | None = None):
        self.users = users if users is not None else ALERT_USERS

    def get_user_by_id(self, user_id):
        user = self.users.get(int(user_id))
        return dict(user) if user else None


class _FakePolicyRepo:
    """Records what the guarded policy routes attempt to write."""

    def __init__(self, by_id=None, by_key=None):
        self.by_id: dict[int, object] = by_id or {}
        self.by_key: dict[str, object] = by_key or {}
        self.created: list[dict] = []
        self.enabled_calls: list[tuple[int, bool]] = []

    def get_rule(self, rule_id):
        return self.by_id.get(int(rule_id))

    def get_current_rule_by_key(self, rule_key):
        return self.by_key.get(rule_key)

    def create_rule(self, **fields):
        self.created.append(fields)
        return SimpleNamespace(
            rule_key=fields.get("rule_key"),
            version=1,
            to_dict=lambda: {
                "rule_key": fields.get("rule_key"),
                "tenant_id": fields.get("tenant_id"),
            },
        )

    def set_rule_enabled(self, rule_id, enabled):
        self.enabled_calls.append((int(rule_id), bool(enabled)))
        return 1


def _rule(tenant_id):
    """A policy rule carrying just the scope field the guard reads."""
    return SimpleNamespace(tenant_id=tenant_id)


# ── request helpers, one per blueprint ────────────────────────────────────
#
# Each spins up a bare Flask app with only the blueprint under test so the
# shared module-level blueprint singletons are never mutated for other test
# modules (same reasoning as test_admin_cross_tenant_takeover.py). The
# cross-tenant audit writer is stubbed everywhere so no test needs a database.


def _policy_request(actor, method, path, *, json_body=None, policy_repo=None):
    from app.routes.policy import policy_bp

    policy_repo = policy_repo or _FakePolicyRepo()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(policy_bp, url_prefix="/api")

    with (
        patch("app.auth.decorators._load_user_from_token", return_value=actor),
        patch("app.utils.config.is_policy_enabled", return_value=True),
        patch("app.modules.policy.repo.PolicyRepository", return_value=policy_repo),
        patch("app.routes.policy.invalidate_policy_rule_cache"),
        patch("app.auth.decorators._log_cross_tenant_operation") as audit,
    ):
        client = app.test_client()
        response = client.open(
            path,
            method=method,
            json=json_body,
            headers={"Authorization": "Bearer test-token"},
        )
    return response, policy_repo, audit


def _governance_request(
    actor,
    method,
    path,
    *,
    json_body=None,
    quota_mgr=None,
    user_repo=None,
    gov_repo=None,
    content_filter=None,
):
    from app.routes.governance import governance_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(governance_bp, url_prefix="/api")

    gov_repo = gov_repo if gov_repo is not None else MagicMock()
    if gov_repo is not None and isinstance(gov_repo, MagicMock):
        gov_repo.create_filter_rule.return_value = 123
        gov_repo.update_filter_rule.return_value = True
        gov_repo.delete_filter_rule.return_value = True
    content_filter = content_filter if content_filter is not None else MagicMock()

    with (
        patch("app.auth.decorators._load_user_from_token", return_value=actor),
        patch("app.routes.governance.quota_manager", quota_mgr or MagicMock()),
        patch("app.routes.governance.governance_repo", gov_repo),
        patch("app.routes.governance.content_filter", content_filter),
        patch("app.routes.governance.audit_logger"),
        patch(
            "app.repositories.user_repo.UserRepository", return_value=user_repo or _FakeUserRepo()
        ),
        patch("app.auth.decorators._log_cross_tenant_operation") as audit,
    ):
        client = app.test_client()
        response = client.open(
            path,
            method=method,
            json=json_body,
            headers={"Authorization": "Bearer test-token"},
        )
    return response, audit


def _compliance_request(actor, method, path, *, json_body=None, report_gen=None):
    from app.routes.compliance import compliance_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    # compliance_bp already carries url_prefix="/api/compliance".
    app.register_blueprint(compliance_bp)

    with (
        patch("app.auth.decorators._load_user_from_token", return_value=actor),
        patch("app.routes.compliance.report_generator", report_gen or MagicMock()),
        patch("app.auth.decorators._log_cross_tenant_operation") as audit,
    ):
        client = app.test_client()
        response = client.open(
            path,
            method=method,
            json=json_body,
            headers={"Authorization": "Bearer test-token"},
        )
    return response, audit


# ── policy rules ──────────────────────────────────────────────────────────


class TestPolicyToggleTenantBoundary:
    """PATCH /api/policy/rules/<rule_id>/enabled."""

    def test_tenant_admin_cannot_toggle_another_tenants_rule_was_the_hole(self):
        repo = _FakePolicyRepo(by_id={5: _rule(TENANT_B)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PATCH",
            "/api/policy/rules/5/enabled",
            json_body={"enabled": False},
            policy_repo=repo,
        )
        assert response.status_code == 403, response.get_data(as_text=True)
        assert repo.enabled_calls == []

    def test_tenant_admin_cannot_toggle_a_global_rule(self):
        """A NULL-tenant rule governs every tenant; deny rather than rescope."""
        repo = _FakePolicyRepo(by_id={5: _rule(None)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PATCH",
            "/api/policy/rules/5/enabled",
            json_body={"enabled": False},
            policy_repo=repo,
        )
        assert response.status_code == 403
        assert repo.enabled_calls == []

    def test_missing_rule_is_denied_for_tenant_admin_without_oracle(self):
        """A missing rule and a cross-tenant rule both return 403 -- no existence oracle."""
        repo = _FakePolicyRepo(by_id={})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PATCH",
            "/api/policy/rules/999/enabled",
            json_body={"enabled": False},
            policy_repo=repo,
        )
        assert response.status_code == 403
        assert repo.enabled_calls == []

    def test_tenant_admin_can_toggle_own_tenant_rule(self):
        repo = _FakePolicyRepo(by_id={5: _rule(TENANT_A)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PATCH",
            "/api/policy/rules/5/enabled",
            json_body={"enabled": False},
            policy_repo=repo,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.enabled_calls == [(5, False)]

    def test_platform_admin_can_toggle_any_rule(self):
        repo = _FakePolicyRepo(by_id={5: _rule(TENANT_B)})
        response, repo, audit = _policy_request(
            PLATFORM_ADMIN,
            "PATCH",
            "/api/policy/rules/5/enabled",
            json_body={"enabled": True},
            policy_repo=repo,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.enabled_calls == [(5, True)]
        audit.assert_called_once()  # cross-tenant reach is recorded


class TestPolicyCreateTenantBoundary:
    """POST /api/policy/rules."""

    def _body(self, **over):
        body = {
            "rule_key": "k1",
            "name": "Rule 1",
            "policy_type": "tool_action",
            "effect": "require_approval",
        }
        body.update(over)
        return body

    def test_tenant_admin_cannot_create_rule_for_another_tenant(self):
        repo = _FakePolicyRepo()
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "POST",
            "/api/policy/rules",
            json_body=self._body(tenant_id=TENANT_B),
            policy_repo=repo,
        )
        assert response.status_code == 403, response.get_data(as_text=True)
        assert repo.created == []

    def test_tenant_admin_create_without_tenant_is_scoped_to_own_tenant(self):
        """No body tenant_id must NOT mean a global rule for a tenant admin."""
        repo = _FakePolicyRepo()
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "POST",
            "/api/policy/rules",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        assert len(repo.created) == 1
        assert repo.created[0]["tenant_id"] == TENANT_A

    def test_platform_admin_can_create_a_global_rule(self):
        repo = _FakePolicyRepo()
        response, repo, _ = _policy_request(
            PLATFORM_ADMIN,
            "POST",
            "/api/policy/rules",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        assert repo.created[0]["tenant_id"] is None

    def test_platform_admin_can_create_for_a_named_tenant(self):
        repo = _FakePolicyRepo()
        response, repo, _ = _policy_request(
            PLATFORM_ADMIN,
            "POST",
            "/api/policy/rules",
            json_body=self._body(tenant_id=TENANT_B),
            policy_repo=repo,
        )
        assert response.status_code == 201
        assert repo.created[0]["tenant_id"] == TENANT_B


class TestPolicyUpdateTenantBoundary:
    """PUT /api/policy/rules/<rule_key> -- versioned supersede."""

    def _body(self, **over):
        body = {
            "name": "Rule 1 v2",
            "policy_type": "tool_action",
            "effect": "deny",
        }
        body.update(over)
        return body

    def test_tenant_admin_cannot_supersede_another_tenants_key_was_the_hole(self):
        """The supersede UPDATE is keyed on rule_key alone -- it rewrites the owner."""
        repo = _FakePolicyRepo(by_key={"k1": _rule(TENANT_B)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PUT",
            "/api/policy/rules/k1",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 403, response.get_data(as_text=True)
        assert repo.created == []

    def test_tenant_admin_cannot_supersede_a_global_key(self):
        repo = _FakePolicyRepo(by_key={"k1": _rule(None)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PUT",
            "/api/policy/rules/k1",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 403
        assert repo.created == []

    def test_tenant_admin_cannot_hijack_own_key_into_another_tenant(self):
        """Owns the key, but may not move the new version's scope elsewhere."""
        repo = _FakePolicyRepo(by_key={"k1": _rule(TENANT_A)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PUT",
            "/api/policy/rules/k1",
            json_body=self._body(tenant_id=TENANT_B),
            policy_repo=repo,
        )
        assert response.status_code == 403
        assert repo.created == []

    def test_tenant_admin_can_supersede_own_key_and_stays_scoped(self):
        repo = _FakePolicyRepo(by_key={"k1": _rule(TENANT_A)})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PUT",
            "/api/policy/rules/k1",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.created[0]["tenant_id"] == TENANT_A

    def test_tenant_admin_can_create_new_key_scoped_to_own_tenant(self):
        """A PUT to an unused key is a create; a tenant admin may do that for itself."""
        repo = _FakePolicyRepo(by_key={})
        response, repo, _ = _policy_request(
            TENANT_A_ADMIN,
            "PUT",
            "/api/policy/rules/brand-new",
            json_body=self._body(),
            policy_repo=repo,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.created[0]["tenant_id"] == TENANT_A

    def test_platform_admin_can_supersede_any_key(self):
        repo = _FakePolicyRepo(by_key={"k1": _rule(TENANT_B)})
        response, repo, audit = _policy_request(
            PLATFORM_ADMIN,
            "PUT",
            "/api/policy/rules/k1",
            json_body=self._body(tenant_id=TENANT_B),
            policy_repo=repo,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert repo.created[0]["tenant_id"] == TENANT_B
        audit.assert_called_once()


# ── content-filter mutations (global table -> platform admin only) ─────────


CONTENT_FILTER_MUTATIONS = [
    ("POST", "/api/content/filter/patterns", {"name": "n", "pattern": "p"}),
    ("POST", "/api/content/filter/keywords", {"keyword": "kw"}),
    ("POST", "/api/filter-rules", {"pattern": "p"}),
    ("PUT", "/api/filter-rules/7", {"pattern": "p2"}),
    ("DELETE", "/api/filter-rules/7", None),
]


class TestContentFilterMutationsArePlatformAdminOnly:
    """content_filter_rules and custom patterns/keywords are global config."""

    @pytest.mark.parametrize(("method", "path", "body"), CONTENT_FILTER_MUTATIONS)
    def test_tenant_admin_is_denied(self, method, path, body):
        response, _ = _governance_request(TENANT_A_ADMIN, method, path, json_body=body)
        assert response.status_code == 403, response.get_data(as_text=True)

    @pytest.mark.parametrize(("method", "path", "body"), CONTENT_FILTER_MUTATIONS)
    def test_plain_user_is_denied(self, method, path, body):
        response, _ = _governance_request(TENANT_A_USER, method, path, json_body=body)
        assert response.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), CONTENT_FILTER_MUTATIONS)
    def test_platform_admin_is_allowed(self, method, path, body):
        response, _ = _governance_request(PLATFORM_ADMIN, method, path, json_body=body)
        assert response.status_code not in (401, 403), response.get_data(as_text=True)

    @pytest.mark.parametrize(("method", "path", "body"), CONTENT_FILTER_MUTATIONS)
    def test_legacy_admin_is_allowed_in_non_strict_mode(self, method, path, body):
        response, _ = _governance_request(LEGACY_ADMIN, method, path, json_body=body)
        assert response.status_code not in (401, 403), response.get_data(as_text=True)


# ── quota alert acknowledge (tenant resolved through the alert's user) ─────


def _quota_mgr(alert_user_id):
    m = MagicMock()
    m.get_alert.return_value = (
        SimpleNamespace(user_id=alert_user_id) if alert_user_id is not None else None
    )
    m.acknowledge_alert.return_value = True
    return m


class TestQuotaAlertAckTenantBoundary:
    """POST /api/quota/alerts/<alert_id>/acknowledge."""

    def test_tenant_admin_cannot_ack_another_tenants_alert_was_the_hole(self):
        mgr = _quota_mgr(20)  # alert belongs to bob in tenant B
        response, _ = _governance_request(
            TENANT_A_ADMIN, "POST", "/api/quota/alerts/1/acknowledge", quota_mgr=mgr
        )
        assert response.status_code == 403, response.get_data(as_text=True)
        mgr.acknowledge_alert.assert_not_called()

    def test_missing_alert_is_denied_for_tenant_admin(self):
        mgr = _quota_mgr(None)  # get_alert -> None
        response, _ = _governance_request(
            TENANT_A_ADMIN, "POST", "/api/quota/alerts/999/acknowledge", quota_mgr=mgr
        )
        assert response.status_code == 403
        mgr.acknowledge_alert.assert_not_called()

    def test_alert_for_deleted_user_is_denied_for_tenant_admin(self):
        mgr = _quota_mgr(777)  # user 777 not in ALERT_USERS -> None
        response, _ = _governance_request(
            TENANT_A_ADMIN, "POST", "/api/quota/alerts/1/acknowledge", quota_mgr=mgr
        )
        assert response.status_code == 403
        mgr.acknowledge_alert.assert_not_called()

    def test_tenant_admin_can_ack_own_tenants_alert(self):
        mgr = _quota_mgr(10)  # alice in tenant A
        response, _ = _governance_request(
            TENANT_A_ADMIN, "POST", "/api/quota/alerts/1/acknowledge", quota_mgr=mgr
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        mgr.acknowledge_alert.assert_called_once()

    def test_platform_admin_can_ack_any_alert(self):
        mgr = _quota_mgr(20)  # bob in tenant B
        response, audit = _governance_request(
            PLATFORM_ADMIN, "POST", "/api/quota/alerts/1/acknowledge", quota_mgr=mgr
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        mgr.acknowledge_alert.assert_called_once()
        audit.assert_called_once()


# ── compliance report read + generate ─────────────────────────────────────


def _report(tenant_id):
    r = MagicMock()
    r.metadata.tenant_id = tenant_id
    r.to_dict.return_value = {"report_id": "R1", "tenant_id": tenant_id}
    return r


class TestComplianceReportReadTenantBoundary:
    """GET /api/compliance/reports/<report_id>."""

    def test_tenant_admin_cannot_read_another_tenants_report_was_the_hole(self):
        gen = MagicMock()
        gen.get_saved_report.return_value = _report(TENANT_B)
        response, _ = _compliance_request(
            TENANT_A_ADMIN, "GET", "/api/compliance/reports/R1", report_gen=gen
        )
        assert response.status_code == 403, response.get_data(as_text=True)

    def test_missing_report_is_denied_for_tenant_admin_without_oracle(self):
        gen = MagicMock()
        gen.get_saved_report.return_value = None
        response, _ = _compliance_request(
            TENANT_A_ADMIN, "GET", "/api/compliance/reports/does-not-exist", report_gen=gen
        )
        assert response.status_code == 403

    def test_tenant_admin_can_read_own_tenant_report(self):
        gen = MagicMock()
        gen.get_saved_report.return_value = _report(TENANT_A)
        response, _ = _compliance_request(
            TENANT_A_ADMIN, "GET", "/api/compliance/reports/R1", report_gen=gen
        )
        assert response.status_code == 200, response.get_data(as_text=True)

    def test_platform_admin_can_read_any_report(self):
        gen = MagicMock()
        gen.get_saved_report.return_value = _report(TENANT_B)
        response, audit = _compliance_request(
            PLATFORM_ADMIN, "GET", "/api/compliance/reports/R1", report_gen=gen
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        audit.assert_called_once()

    def test_platform_admin_missing_report_still_404(self):
        gen = MagicMock()
        gen.get_saved_report.return_value = None
        response, _ = _compliance_request(
            PLATFORM_ADMIN, "GET", "/api/compliance/reports/nope", report_gen=gen
        )
        assert response.status_code == 404


class TestComplianceGenerateReportTenantBoundary:
    """POST /api/compliance/reports -- tenant admin may not name another tenant."""

    def test_tenant_admin_naming_another_tenant_is_denied(self):
        gen = MagicMock()
        response, _ = _compliance_request(
            TENANT_A_ADMIN,
            "POST",
            "/api/compliance/reports",
            json_body={"report_type": "usage_summary", "tenant_id": TENANT_B},
            report_gen=gen,
        )
        assert response.status_code == 403, response.get_data(as_text=True)
        gen.generate_report.assert_not_called()

    def test_tenant_admin_own_tenant_proceeds(self):
        gen = MagicMock()
        gen.generate_report.return_value.to_dict.return_value = {"report_id": "R1"}
        gen.save_report.return_value = True
        response, _ = _compliance_request(
            TENANT_A_ADMIN,
            "POST",
            "/api/compliance/reports",
            json_body={"report_type": "usage_summary", "tenant_id": TENANT_A},
            report_gen=gen,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        gen.generate_report.assert_called_once()

    def test_tenant_admin_without_tenant_id_proceeds_scoped_to_own(self):
        gen = MagicMock()
        gen.generate_report.return_value.to_dict.return_value = {"report_id": "R1"}
        gen.save_report.return_value = True
        response, _ = _compliance_request(
            TENANT_A_ADMIN,
            "POST",
            "/api/compliance/reports",
            json_body={"report_type": "usage_summary"},
            report_gen=gen,
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        # The report was generated for the caller's own tenant, not all tenants.
        _, kwargs = gen.generate_report.call_args
        assert kwargs["tenant_id"] == TENANT_A
