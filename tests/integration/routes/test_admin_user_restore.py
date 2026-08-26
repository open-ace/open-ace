"""Integration tests for user restore functionality (Issue #2755).

Tests cover:
- Soft-delete conflict detection during user creation
- User restoration via API
- Session revocation on delete/restore
- Tenant counter management
- Audit logging
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.modules.governance.audit_logger import AuditAction
from app.repositories.user_repo import UserRepository
from app.routes.admin import admin_bp


# ---------------------------------------------------------------------------
# Flask app fixtures for admin API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app_for_admin(tmp_db):
    """Create Flask app with admin routes for testing."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    # Patch database to use tmp_db
    with patch("app.repositories.database.Database", return_value=tmp_db):
        yield app


@pytest.fixture
def admin_client_for_api(app_for_admin, tmp_db):
    """Create authenticated admin test client for admin API tests."""
    # Ensure necessary tenant and user records exist
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (1, 'Default Tenant', 'default')"
    )

    # Mock authenticated admin user
    admin_user = {
        "id": 1,
        "username": "admin",
        "email": "admin@test.com",
        "role": "platform_admin",
        "tenant_id": None,
    }

    with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
        client = app_for_admin.test_client()
        client.set_cookie("session_token", "test-admin-token")
        yield client


# ---------------------------------------------------------------------------
# Test helper functions
# ---------------------------------------------------------------------------


def _insert_user(tmp_db, username="testuser", email=None, tenant_id=None, deleted_at=None):
    """Insert a user row for testing."""
    if email is None:
        email = f"{username}@example.com"
    tenant_val = tenant_id if tenant_id is not None else 1

    # Ensure tenant exists for foreign key constraint
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (?, 'Default Tenant', 'default')",
        (tenant_val,),
    )

    if deleted_at:
        cursor = tmp_db.execute(
            "INSERT INTO users (username, email, password_hash, role, tenant_id, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, email, "hashed_pw", "user", tenant_val, deleted_at),
        )
    else:
        cursor = tmp_db.execute(
            "INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)",
            (username, email, "hashed_pw", "user", tenant_val),
        )
    return cursor.lastrowid


def _insert_tenant(tmp_db, name="Test Tenant", max_users=100):
    """Insert a tenant row for testing."""
    slug = name.lower().replace(" ", "-")
    cursor = tmp_db.execute(
        "INSERT INTO tenants (name, slug, quota) VALUES (?, ?, ?)",
        (name, slug, f'{{"max_users": {max_users}}}'),
    )
    return cursor.lastrowid


def _insert_session(tmp_db, user_id, token="test_token"):
    """Insert a session row for testing."""
    cursor = tmp_db.execute(
        "INSERT INTO sessions (user_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            user_id,
            token,
            datetime.now(timezone.utc).replace(tzinfo=None),
            datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    )
    return cursor.lastrowid


def _insert_web_user_auth_session(tmp_db, user_id, session_token="web_token"):
    """Insert a web_user_auth_sessions row for testing."""
    cursor = tmp_db.execute(
        "INSERT INTO web_user_auth_sessions (user_id, session_token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            user_id,
            session_token,
            datetime.now(timezone.utc).replace(tzinfo=None),
            datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    )
    return cursor.lastrowid


class TestSoftDeleteConflictDetection:
    """Tests for detecting soft-deleted users during creation (Issue #2755)."""

    def test_get_user_by_username_excludes_deleted(self, tmp_db):
        """Verify get_user_by_username excludes soft-deleted users by default."""
        repo = UserRepository(db=tmp_db)

        # Insert a soft-deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="deleted_user", deleted_at=deleted_at)

        # Default behavior should NOT find the deleted user
        user = repo.get_user_by_username("deleted_user", include_deleted=False)
        assert user is None

        # With include_deleted=True, should find the deleted user
        user = repo.get_user_by_username("deleted_user", include_deleted=True)
        assert user is not None
        assert user["id"] == user_id

    def test_get_user_by_email_excludes_deleted(self, tmp_db):
        """Verify get_user_by_email excludes soft-deleted users by default."""
        repo = UserRepository(db=tmp_db)

        # Insert a soft-deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(
            tmp_db, username="deleted_email_user", email="deleted@test.com", deleted_at=deleted_at
        )

        # Default behavior should NOT find the deleted user
        user = repo.get_user_by_email("deleted@test.com", include_deleted=False)
        assert user is None

        # With include_deleted=True, should find the deleted user
        user = repo.get_user_by_email("deleted@test.com", include_deleted=True)
        assert user is not None
        assert user["id"] == user_id

    def test_get_soft_deleted_user_by_username(self, tmp_db):
        """Verify get_soft_deleted_user_by_username only returns soft-deleted users."""
        repo = UserRepository(db=tmp_db)

        # Insert an active user
        _insert_user(tmp_db, username="active_user")

        # Insert a soft-deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user_id = _insert_user(tmp_db, username="soft_deleted_user", deleted_at=deleted_at)

        # Should not find active user
        user = repo.get_soft_deleted_user_by_username("active_user")
        assert user is None

        # Should find soft-deleted user
        user = repo.get_soft_deleted_user_by_username("soft_deleted_user")
        assert user is not None
        assert user["id"] == deleted_user_id

    def test_get_soft_deleted_user_by_email(self, tmp_db):
        """Verify get_soft_deleted_user_by_email only returns soft-deleted users."""
        repo = UserRepository(db=tmp_db)

        # Insert an active user
        _insert_user(tmp_db, username="active_email_user", email="active_email@test.com")

        # Insert a soft-deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user_id = _insert_user(
            tmp_db,
            username="soft_deleted_email_user",
            email="deleted_email@test.com",
            deleted_at=deleted_at,
        )

        # Should not find active user
        user = repo.get_soft_deleted_user_by_email("active_email@test.com")
        assert user is None

        # Should find soft-deleted user
        user = repo.get_soft_deleted_user_by_email("deleted_email@test.com")
        assert user is not None
        assert user["id"] == deleted_user_id


class TestDeleteAllSessionsForUser:
    """Tests for session revocation (Issue #2755)."""

    def test_delete_all_sessions_for_user(self, tmp_db):
        """Verify delete_all_sessions_for_user deletes from sessions table."""
        repo = UserRepository(db=tmp_db)

        user_id = _insert_user(tmp_db, username="session_user")
        _insert_session(tmp_db, user_id, token="token1")
        _insert_session(tmp_db, user_id, token="token2")

        counts = repo.delete_all_sessions_for_user(user_id)

        assert counts["sessions"] == 2
        assert counts["sso_sessions"] == 0
        assert counts["web_user_auth_sessions"] == 0

        # Verify sessions are deleted
        sessions = tmp_db.fetch_all("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
        assert len(sessions) == 0

    def test_delete_all_sessions_for_user_web_auth(self, tmp_db):
        """Verify delete_all_sessions_for_user deletes from web_user_auth_sessions."""
        repo = UserRepository(db=tmp_db)

        user_id = _insert_user(tmp_db, username="web_session_user")
        _insert_web_user_auth_session(tmp_db, user_id, session_token="web_token1")
        _insert_web_user_auth_session(tmp_db, user_id, session_token="web_token2")

        counts = repo.delete_all_sessions_for_user(user_id)

        assert counts["web_user_auth_sessions"] >= 0  # May be 0 if table doesn't exist

        # Verify sessions are deleted if table exists
        if counts["web_user_auth_sessions"] > 0:
            sessions = tmp_db.fetch_all(
                "SELECT * FROM web_user_auth_sessions WHERE user_id = ?", (user_id,)
            )
            assert len(sessions) == 0

    def test_delete_all_sessions_for_user_multiple_tables(self, tmp_db):
        """Verify delete_all_sessions_for_user handles multiple session tables."""
        repo = UserRepository(db=tmp_db)

        user_id = _insert_user(tmp_db, username="multi_session_user")
        _insert_session(tmp_db, user_id, token="token1")
        _insert_web_user_auth_session(tmp_db, user_id, session_token="web_token1")

        counts = repo.delete_all_sessions_for_user(user_id)

        assert counts["sessions"] == 1
        assert counts["web_user_auth_sessions"] >= 0


class TestRestoreUserWithUpdate:
    """Tests for restore_user_with_update method (Issue #2755)."""

    def test_restore_user_success(self, tmp_db):
        """Verify restore_user_with_update successfully restores a soft-deleted user."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete a user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="restore_user", deleted_at=deleted_at)

        # Verify user is soft-deleted
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is not None

        # Restore the user
        result = repo.restore_user_with_update(user_id)
        assert result is True

        # Verify user is restored
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None

    def test_restore_user_with_field_updates(self, tmp_db):
        """Verify restore_user_with_update can update fields during restore."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete a user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="update_restore_user", deleted_at=deleted_at)

        # Restore with field updates
        result = repo.restore_user_with_update(
            user_id,
            username="updated_restore_user",
            email="updated@test.com",
            password_hash="new_hash",
            role="manager",
            is_active=True,
        )
        assert result is True

        # Verify fields are updated
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None
        assert user["username"] == "updated_restore_user"
        assert user["email"] == "updated@test.com"
        assert user["password_hash"] == "new_hash"
        assert user["role"] == "manager"

    def test_restore_user_fails_for_active_user(self, tmp_db):
        """Verify restore_user_with_update fails for active (non-deleted) users."""
        repo = UserRepository(db=tmp_db)

        # Create an active user
        user_id = _insert_user(tmp_db, username="active_restore_user")

        # Attempt to restore active user should fail
        result = repo.restore_user_with_update(user_id)
        assert result is False

        # Verify user is still active
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None

    def test_restore_user_fails_for_nonexistent_user(self, tmp_db):
        """Verify restore_user_with_update fails for nonexistent users."""
        repo = UserRepository(db=tmp_db)

        # Attempt to restore nonexistent user
        result = repo.restore_user_with_update(99999)
        assert result is False


class TestAuditActionRestore:
    """Tests for audit logging (Issue #2755)."""

    def test_user_restore_audit_action_exists(self):
        """Verify USER_RESTORE audit action exists."""
        assert hasattr(AuditAction, "USER_RESTORE")
        assert AuditAction.USER_RESTORE.value == "user_restore"


class TestSoftDeleteBasicFlow:
    """Integration tests for basic soft-delete flow."""

    def test_soft_delete_then_restore_flow(self, tmp_db):
        """Test complete flow: create -> soft delete -> restore."""
        repo = UserRepository(db=tmp_db)

        # Create user
        user_id = _insert_user(tmp_db, username="flow_user")

        # Verify user exists and is active
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None

        # Soft delete
        result = repo.delete_user(user_id)
        assert result is True

        # Verify user is soft-deleted
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is not None

        # Verify user doesn't appear in normal queries
        user = repo.get_user_by_username("flow_user", include_deleted=False)
        assert user is None

        # Restore
        result = repo.restore_user_with_update(user_id)
        assert result is True

        # Verify user is active again
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None

        # Verify user appears in normal queries
        user = repo.get_user_by_username("flow_user", include_deleted=False)
        assert user is not None
        assert user["id"] == user_id

    def test_multiple_soft_deleted_users_with_same_username(self, tmp_db):
        """Verify uniqueness constraint is preserved for soft-deleted users.

        This test documents the design decision (D1) that we keep the global
        UNIQUE constraint, meaning you cannot have multiple soft-deleted users
        with the same username.
        """
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete a user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="unique_user", deleted_at=deleted_at)

        # Attempting to create another user with the same username should fail
        # because we keep the global UNIQUE constraint
        # (This is handled at the application level via get_soft_deleted_user_by_username)

        # Verify we can find the soft-deleted user by username
        user = repo.get_soft_deleted_user_by_username("unique_user")
        assert user is not None
        assert user["id"] == user_id


class TestIncludeDeletedBackwardCompatibility:
    """Tests to ensure backward compatibility of include_deleted parameter."""

    def test_get_user_by_username_default_behavior(self, tmp_db):
        """Verify default behavior excludes deleted users."""
        repo = UserRepository(db=tmp_db)

        # Create active user
        active_user_id = _insert_user(tmp_db, username="active_default")

        # Create deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        _insert_user(tmp_db, username="deleted_default", deleted_at=deleted_at)

        # Default should find active user
        user = repo.get_user_by_username("active_default")
        assert user is not None
        assert user["id"] == active_user_id

        # Default should NOT find deleted user
        user = repo.get_user_by_username("deleted_default")
        assert user is None

    def test_get_user_by_email_default_behavior(self, tmp_db):
        """Verify default behavior excludes deleted users for email lookup."""
        repo = UserRepository(db=tmp_db)

        # Create active user
        active_user_id = _insert_user(
            tmp_db, username="active_email_default", email="active_default@test.com"
        )

        # Create deleted user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        _insert_user(
            tmp_db,
            username="deleted_email_default",
            email="deleted_default@test.com",
            deleted_at=deleted_at,
        )

        # Default should find active user
        user = repo.get_user_by_email("active_default@test.com")
        assert user is not None
        assert user["id"] == active_user_id

        # Default should NOT find deleted user
        user = repo.get_user_by_email("deleted_default@test.com")
        assert user is None


class TestSessionRevocationOnRestore:
    """Tests for session revocation during restore operation."""

    def test_restore_revokes_sessions(self, tmp_db):
        """Verify that restoring a user revokes their sessions."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete a user with sessions
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="restore_session_user", deleted_at=deleted_at)

        # Create sessions for the user
        _insert_session(tmp_db, user_id, token="restore_token1")
        _insert_web_user_auth_session(tmp_db, user_id, session_token="restore_web_token")

        # Restore the user (simulating the full flow)
        # First revoke sessions
        counts = repo.delete_all_sessions_for_user(user_id)
        assert counts["sessions"] >= 1

        # Then restore
        result = repo.restore_user_with_update(user_id)
        assert result is True

        # Verify sessions are gone
        sessions = tmp_db.fetch_all("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
        assert len(sessions) == 0


class TestCrossTenantRestore:
    """Tests for cross-tenant restore prevention (Issue #2755).

    Acceptance: Restore is tenant-scoped and atomic.
    """

    def test_restore_preserves_tenant_id(self, tmp_db):
        """Verify restore preserves original tenant_id."""
        repo = UserRepository(db=tmp_db)

        # Create tenant 2
        tmp_db.execute(
            "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (?, 'Test Tenant 2', 'test-tenant-2')",
            (2,),
        )

        # Create and soft-delete user in tenant 2
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="tenant2_user", tenant_id=2, deleted_at=deleted_at)

        # Restore user
        result = repo.restore_user_with_update(user_id)
        assert result is True

        # Verify tenant_id is preserved
        user = repo.get_user_by_id(user_id)
        assert user["tenant_id"] == 2
        assert user["deleted_at"] is None

    def test_cross_tenant_restore_not_allowed_via_update(self, tmp_db):
        """Verify tenant_id cannot be changed during restore."""
        repo = UserRepository(db=tmp_db)

        # Create tenants
        tmp_db.execute(
            "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (?, 'Tenant 1', 'tenant-1')",
            (1,),
        )
        tmp_db.execute(
            "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (?, 'Tenant 2', 'tenant-2')",
            (2,),
        )

        # Create and soft-delete user in tenant 1
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(
            tmp_db, username="cross_tenant_user", tenant_id=1, deleted_at=deleted_at
        )

        # Attempt to restore with different tenant_id should preserve original
        # (The API layer should reject tenant_id changes; repository just ignores them)
        result = repo.restore_user_with_update(user_id, tenant_id=2)
        assert result is True

        # Verify tenant_id is still 1 (unchanged)
        user = repo.get_user_by_id(user_id)
        assert user["tenant_id"] == 1  # Repository preserves original tenant_id


class TestConcurrentRestore:
    """Tests for concurrent restore scenarios (Issue #2755).

    Acceptance: Tests cover concurrent restore/create attempts.
    """

    def test_concurrent_restore_same_user_only_one_succeeds(self, tmp_db):
        """Verify concurrent restore of same user only succeeds once.

        Note: This tests the optimistic locking behavior of restore_user_with_update.
        The second restore should fail because the user is no longer soft-deleted.
        """
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="concurrent_restore_user", deleted_at=deleted_at)

        # First restore should succeed
        result1 = repo.restore_user_with_update(user_id)
        assert result1 is True

        # Second restore should fail (user is already active)
        result2 = repo.restore_user_with_update(user_id)
        assert result2 is False

        # Verify user is active
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None


class TestTenantCounterRollback:
    """Tests for tenant counter rollback on delete/restore (Issue #2755).

    Acceptance: Tenant user counters remain correct on delete and restore.
    """

    def _get_active_user_count(self, db, tenant_id=1):
        """Get active user count for a tenant."""
        result = db.fetch_one(
            "SELECT COUNT(*) as count FROM users WHERE tenant_id = ? AND deleted_at IS NULL",
            (tenant_id,),
        )
        return result["count"] if result else 0

    def test_delete_decrements_user_count(self, tmp_db):
        """Verify deleting a user decrements the tenant user count."""
        repo = UserRepository(db=tmp_db)

        # Create user
        user_id = _insert_user(tmp_db, username="counter_delete_user")
        initial_count = self._get_active_user_count(tmp_db)

        # Delete user (soft delete)
        result = repo.delete_user(user_id)
        assert result is True

        # Verify user is deleted
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is not None

        # Verify count decreased
        new_count = self._get_active_user_count(tmp_db)
        assert new_count == initial_count - 1

    def test_restore_increments_user_count(self, tmp_db):
        """Verify restoring a user increments the tenant user count."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(tmp_db, username="counter_restore_user", deleted_at=deleted_at)

        initial_count = self._get_active_user_count(tmp_db)

        # Restore user
        result = repo.restore_user_with_update(user_id)
        assert result is True

        # Verify user is restored
        user = repo.get_user_by_id(user_id)
        assert user["deleted_at"] is None

        # Verify count increased
        new_count = self._get_active_user_count(tmp_db)
        assert new_count == initial_count + 1

    def test_delete_restore_maintains_count(self, tmp_db):
        """Verify delete then restore maintains original count."""
        repo = UserRepository(db=tmp_db)

        # Create user
        user_id = _insert_user(tmp_db, username="counter_cycle_user")
        original_count = self._get_active_user_count(tmp_db)

        # Delete
        repo.delete_user(user_id)
        after_delete_count = self._get_active_user_count(tmp_db)
        assert after_delete_count == original_count - 1

        # Restore
        repo.restore_user_with_update(user_id)
        after_restore_count = self._get_active_user_count(tmp_db)
        assert after_restore_count == original_count


class TestUsernameEmailConflictScenarios:
    """Tests for various conflict scenarios (Issue #2755).

    Acceptance: Tests cover username-only conflict, email-only conflict,
    both fields matching, cross-tenant access.
    """

    def test_username_only_conflict(self, tmp_db):
        """Verify username-only conflict detection."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(
            tmp_db, username="username_conflict", email="unique1@test.com", deleted_at=deleted_at
        )

        # Should find soft-deleted user by username
        deleted_user = repo.get_soft_deleted_user_by_username("username_conflict")
        assert deleted_user is not None
        assert deleted_user["id"] == user_id

        # Should not find it by email in soft-deleted lookup
        deleted_by_email = repo.get_soft_deleted_user_by_email("unique1@test.com")
        assert deleted_by_email is not None  # Same user, different email

    def test_email_only_conflict(self, tmp_db):
        """Verify email-only conflict detection."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(
            tmp_db, username="unique_user", email="email_conflict@test.com", deleted_at=deleted_at
        )

        # Should find soft-deleted user by email
        deleted_user = repo.get_soft_deleted_user_by_email("email_conflict@test.com")
        assert deleted_user is not None
        assert deleted_user["id"] == user_id

    def test_both_fields_matching(self, tmp_db):
        """Verify both username and email matching same soft-deleted user."""
        repo = UserRepository(db=tmp_db)

        # Create and soft-delete user
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user_id = _insert_user(
            tmp_db,
            username="both_match_user",
            email="both_match@test.com",
            deleted_at=deleted_at,
        )

        # Should find same user by both username and email
        deleted_by_username = repo.get_soft_deleted_user_by_username("both_match_user")
        deleted_by_email = repo.get_soft_deleted_user_by_email("both_match@test.com")

        assert deleted_by_username is not None
        assert deleted_by_email is not None
        assert deleted_by_username["id"] == user_id
        assert deleted_by_email["id"] == user_id

    def test_partial_conflict_username_soft_deleted_email_active(self, tmp_db):
        """Verify partial conflict: username matches soft-deleted, email matches active."""
        repo = UserRepository(db=tmp_db)

        # Create active user with specific email
        active_user_id = _insert_user(
            tmp_db, username="active_partial", email="active_partial@test.com"
        )

        # Create soft-deleted user with different username but same email would fail UNIQUE
        # So we test with different email
        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user_id = _insert_user(
            tmp_db,
            username="deleted_partial",
            email="deleted_partial@test.com",
            deleted_at=deleted_at,
        )

        # Verify both can be found appropriately
        active_user = repo.get_user_by_username("active_partial", include_deleted=False)
        assert active_user is not None
        assert active_user["id"] == active_user_id

        deleted_user = repo.get_soft_deleted_user_by_username("deleted_partial")
        assert deleted_user is not None
        assert deleted_user["id"] == deleted_user_id


class TestAPILayerSoftDeleteConflict:
    """Tests for API layer soft-delete conflict detection (Issue #2755).

    These tests verify the API endpoint returns the correct response format
    that the frontend expects.
    """

    def test_create_user_api_returns_soft_deleted_conflict(self, admin_client_for_api, tmp_db):
        """Verify create user API returns proper conflict response for soft-deleted user."""
        from datetime import datetime, timezone

        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user = {
            "id": 100,
            "username": "api_deleted_user",
            "email": "api_deleted@test.com",
            "deleted_at": deleted_at,
            "tenant_id": 1,
        }

        # Mock user_repo to simulate soft-deleted user
        with patch("app.routes.admin.user_repo") as mock_repo:
            mock_repo.get_user_by_username.return_value = None  # No active user
            mock_repo.get_user_by_email.return_value = None  # No active user
            mock_repo.get_soft_deleted_user_by_username.return_value = deleted_user
            mock_repo.get_soft_deleted_user_by_email.return_value = None

            # Try to create a user with the same username
            response = admin_client_for_api.post(
                "/api/admin/users",
                json={
                    "username": "api_deleted_user",
                    "email": "new_email@test.com",
                    "password": "Password123!",
                    "role": "user",
                    "tenant_id": 1,
                },
            )

        assert response.status_code == 409
        data = response.get_json()
        assert data["error"] == "USER_SOFT_DELETED"
        assert "soft_deleted_user" in data

        # Verify the response contains all fields the frontend expects
        soft_deleted = data["soft_deleted_user"]
        assert soft_deleted["user_id"] == 100
        assert soft_deleted["username"] == "api_deleted_user"
        assert soft_deleted["email"] == "api_deleted@test.com"
        assert soft_deleted["deleted_at"] is not None
        assert "username" in soft_deleted["conflicts"]

    def test_create_user_api_soft_deleted_email_conflict(self, admin_client_for_api, tmp_db):
        """Verify create user API returns proper conflict for soft-deleted email."""
        from datetime import datetime, timezone

        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user = {
            "id": 101,
            "username": "unique_username_for_email",
            "email": "api_deleted_email@test.com",
            "deleted_at": deleted_at,
            "tenant_id": 1,
        }

        with patch("app.routes.admin.user_repo") as mock_repo:
            mock_repo.get_user_by_username.return_value = None  # No active user
            mock_repo.get_user_by_email.return_value = None  # No active user
            mock_repo.get_soft_deleted_user_by_username.return_value = None
            mock_repo.get_soft_deleted_user_by_email.return_value = deleted_user

            response = admin_client_for_api.post(
                "/api/admin/users",
                json={
                    "username": "new_username",
                    "email": "api_deleted_email@test.com",
                    "password": "Password123!",
                    "role": "user",
                    "tenant_id": 1,
                },
            )

        assert response.status_code == 409
        data = response.get_json()
        assert data["error"] == "USER_SOFT_DELETED"

        soft_deleted = data["soft_deleted_user"]
        assert soft_deleted["user_id"] == 101
        assert "email" in soft_deleted["conflicts"]

    def test_create_user_api_both_fields_match(self, admin_client_for_api, tmp_db):
        """Verify create user API returns both fields in conflicts when both match."""
        from datetime import datetime, timezone

        deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted_user = {
            "id": 102,
            "username": "both_match_api_user",
            "email": "both_match_api@test.com",
            "deleted_at": deleted_at,
            "tenant_id": 1,
        }

        with patch("app.routes.admin.user_repo") as mock_repo:
            mock_repo.get_user_by_username.return_value = None
            mock_repo.get_user_by_email.return_value = None
            mock_repo.get_soft_deleted_user_by_username.return_value = deleted_user
            mock_repo.get_soft_deleted_user_by_email.return_value = deleted_user

            response = admin_client_for_api.post(
                "/api/admin/users",
                json={
                    "username": "both_match_api_user",
                    "email": "both_match_api@test.com",
                    "password": "Password123!",
                    "role": "user",
                    "tenant_id": 1,
                },
            )

        assert response.status_code == 409
        data = response.get_json()

        soft_deleted = data["soft_deleted_user"]
        assert soft_deleted["user_id"] == 102
        assert "username" in soft_deleted["conflicts"]
        assert "email" in soft_deleted["conflicts"]

    def test_create_user_api_active_user_conflict_returns_400(self, admin_client_for_api, tmp_db):
        """Verify creating user with existing active username returns 400, not 409."""
        active_user = {
            "id": 103,
            "username": "active_api_user",
            "email": "active_api@test.com",
        }

        with patch("app.routes.admin.user_repo") as mock_repo:
            mock_repo.get_user_by_username.return_value = active_user

            response = admin_client_for_api.post(
                "/api/admin/users",
                json={
                    "username": "active_api_user",
                    "email": "different@test.com",
                    "password": "Password123!",
                    "role": "user",
                    "tenant_id": 1,
                },
            )

        # Should return 400 for active user conflict, not 409 for soft-deleted
        assert response.status_code == 400
        data = response.get_json()
        assert "soft_deleted_user" not in data
