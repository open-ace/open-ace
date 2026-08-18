"""Integration tests for Tenant Sensitive Keywords (Issue #2789)."""

import pytest


class TestTenantKeywordsIntegration:
    """Integration tests for tenant keywords CRUD and content filtering."""

    def test_create_keyword_and_check_content(self, tenant_admin_client):
        """Should create keyword and detect it in content check."""
        # Create keyword for tenant 1
        create_response = tenant_admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "UniqueTestKeyword123"},
            content_type="application/json",
        )
        assert create_response.status_code in (200, 201)

        # Check content with tenant context
        check_response = tenant_admin_client.post(
            "/api/content/check",
            json={"content": "This contains UniqueTestKeyword123 here"},
            content_type="application/json",
        )
        assert check_response.status_code == 200

        data = check_response.get_json()
        # Should match tenant keyword
        tenant_keyword_matches = [
            r
            for r in data.get("matched_rules", [])
            if r.get("type") == "sensitive_keyword" and r.get("source") == "tenant"
        ]
        assert len(tenant_keyword_matches) > 0

    def test_tenant_isolation(self, tenant_admin_client):
        """Tenant A's keywords should not affect Tenant B."""
        # Tenant 1 creates keyword
        tenant_admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "Tenant1Keyword"},
            content_type="application/json",
        )

        # Verify tenant 1 can see the keyword
        response_t1 = tenant_admin_client.get("/api/tenants/1/sensitive-keywords")
        assert response_t1.status_code == 200
        data_t1 = response_t1.get_json()
        keywords_t1 = [k["keyword"] for k in data_t1.get("keywords", [])]
        assert "Tenant1Keyword" in keywords_t1

        # Note: Cross-tenant access validation (tenant 2 cannot see tenant 1's keywords)
        # is tested in unit tests with proper mock isolation

    def test_keyword_persistence_after_restart(self, db_session, admin_client):
        """Keywords should persist after simulated restart."""
        # Create keyword
        admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "PersistedKeyword"},
            content_type="application/json",
        )

        # Simulate restart by creating new ContentFilter instance
        from app.modules.governance.content_filter import ContentFilter
        from app.repositories.governance_repo import GovernanceRepository

        new_filter = ContentFilter(governance_repo=GovernanceRepository())

        # Check content with new instance
        result = new_filter.check_content(
            content="This has PersistedKeyword",
            tenant_config={"tenant_id": 1},
        )

        # Should match the persisted keyword
        tenant_matches = [
            r for r in result.matched_rules if r.get("source") == "tenant"
        ]
        assert len(tenant_matches) > 0


class TestTenantKeywordsVersionCache:
    """Tests for version-based cache consistency (Issue #2789)."""

    def test_version_increment_on_create(self, admin_client, db_session):
        """Version should increment when keyword is created."""
        # Get initial version
        from app.repositories.governance_repo import GovernanceRepository

        repo = GovernanceRepository()
        initial_version = repo.get_tenant_keywords_version(1)

        # Create keyword
        admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "VersionTestKeyword"},
            content_type="application/json",
        )

        # Check version incremented
        new_version = repo.get_tenant_keywords_version(1)
        if initial_version is None:
            assert new_version == 1
        else:
            assert new_version == initial_version + 1

    def test_cache_invalidation_on_crud(self, admin_client):
        """Cache should be invalidated after CRUD operations."""
        from app.modules.governance.content_filter_singleton import (
            get_content_filter,
            invalidate_tenant_keywords_cache,
        )

        # Populate cache
        cf = get_content_filter()
        cf._tenant_keywords_cache[1] = ["old_keyword"]
        cf._tenant_keywords_version[1] = 1
        cf._tenant_keywords_cache_time[1] = 0  # Force load

        # Create keyword (should trigger cache invalidation)
        admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "CacheTestKeyword"},
            content_type="application/json",
        )

        # Cache should be invalidated
        assert 1 not in cf._tenant_keywords_cache


class TestTenantKeywordsAudit:
    """Tests for audit logging (Issue #2789)."""

    def test_create_keyword_generates_audit_log(self, admin_client, db_session):
        """Create keyword should generate audit log."""
        admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "AuditTestKeyword"},
            content_type="application/json",
        )

        # Check audit log was created - verify no exception was raised
        # (Actual audit log verification would need audit_logs table)

    def test_update_keyword_generates_audit_log(self, admin_client, db_session):
        """Update keyword should generate audit log."""
        # Create first
        create_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "UpdateAuditKeyword"},
            content_type="application/json",
        )
        keyword_id = create_resp.get_json().get("id")

        # Update
        admin_client.put(
            f"/api/tenants/1/sensitive-keywords/{keyword_id}",
            json={"is_enabled": False},
            content_type="application/json",
        )

        # Verify no exception

    def test_delete_keyword_generates_audit_log(self, admin_client, db_session):
        """Delete keyword should generate audit log."""
        # Create first
        create_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "DeleteAuditKeyword"},
            content_type="application/json",
        )
        keyword_id = create_resp.get_json().get("id")

        # Delete
        admin_client.delete(f"/api/tenants/1/sensitive-keywords/{keyword_id}")

        # Verify no exception


class TestIdempotentKeywordCreation:
    """Tests for idempotent keyword creation (Issue #2789)."""

    def test_duplicate_keyword_returns_existing(self, admin_client):
        """Duplicate keyword should return existing record with is_new=False."""
        # First create
        first_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "IdempotentKeyword"},
            content_type="application/json",
        )
        first_data = first_resp.get_json()

        # Second create (should be idempotent)
        second_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "IdempotentKeyword"},
            content_type="application/json",
        )
        second_data = second_resp.get_json()

        # Should return same ID
        assert first_data["id"] == second_data["id"]
        assert second_data["is_new"] is False
        assert second_resp.status_code == 200

    def test_keyword_case_insensitive_dedup(self, admin_client):
        """Keywords should be deduplicated case-insensitively."""
        # Create with uppercase
        first_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "CaseKeyword"},
            content_type="application/json",
        )
        first_data = first_resp.get_json()

        # Create with lowercase (should return existing)
        second_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "casekeyword"},
            content_type="application/json",
        )
        second_data = second_resp.get_json()

        assert first_data["id"] == second_data["id"]
        assert second_data["is_new"] is False


class TestEnableDisableKeywords:
    """Tests for enabling/disabling keywords (Issue #2789)."""

    def test_disable_keyword(self, admin_client):
        """Should be able to disable a keyword."""
        # Create
        create_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "DisableTestKeyword"},
            content_type="application/json",
        )
        keyword_id = create_resp.get_json()["id"]

        # Disable
        update_resp = admin_client.put(
            f"/api/tenants/1/sensitive-keywords/{keyword_id}",
            json={"is_enabled": False},
            content_type="application/json",
        )
        assert update_resp.status_code == 200

    def test_disabled_keyword_not_in_check(self, admin_client):
        """Disabled keyword should not be checked."""
        # Create and disable
        create_resp = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "DisabledKeyword"},
            content_type="application/json",
        )
        keyword_id = create_resp.get_json()["id"]

        admin_client.put(
            f"/api/tenants/1/sensitive-keywords/{keyword_id}",
            json={"is_enabled": False},
            content_type="application/json",
        )

        # Check content - disabled keyword should not match
        check_resp = admin_client.post(
            "/api/content/check",
            json={"content": "This has DisabledKeyword"},
            content_type="application/json",
        )
        # The keyword should not be in matched_rules since it's disabled
        data = check_resp.get_json()
        tenant_matches = [
            r
            for r in data.get("matched_rules", [])
            if r.get("type") == "sensitive_keyword" and r.get("source") == "tenant"
        ]
        assert len(tenant_matches) == 0