"""Integration tests for UserRepository against real SQLite database."""

import pytest

from app.repositories.user_repo import UserRepository


def _insert_user(tmp_db, username="testuser", email=None, tenant_id=None):
    """Insert a user row for testing."""
    if email is None:
        email = f"{username}@example.com"
    tenant_val = tenant_id if tenant_id is not None else 1
    cursor = tmp_db.execute(
        "INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)",
        (username, email, "hashed_pw", "user", tenant_val),
    )
    return cursor.lastrowid


def _insert_tenant(tmp_db, name="Test Tenant"):
    """Insert a tenant row for testing."""
    slug = name.lower().replace(" ", "-")
    cursor = tmp_db.execute(
        "INSERT INTO tenants (name, slug, quota) VALUES (?, ?, ?)",
        (name, slug, '{"max_users": 100}'),
    )
    return cursor.lastrowid


class TestUserUpdateTenantId:
    """Tests for updating user tenant_id (Issue #1359)."""

    def test_update_user_tenant_id(self, tmp_db):
        """Verify that update_user correctly updates tenant_id field."""
        repo = UserRepository(db=tmp_db)

        # Create two tenants
        tenant1_id = _insert_tenant(tmp_db, name="Tenant 1")
        tenant2_id = _insert_tenant(tmp_db, name="Tenant 2")

        # Create user in tenant 1
        user_id = _insert_user(tmp_db, username="alice", tenant_id=tenant1_id)

        # Verify initial tenant_id
        user = repo.get_user_by_id(user_id)
        assert user is not None
        assert user["tenant_id"] == tenant1_id

        # Update tenant_id to tenant 2
        result = repo.update_user(user_id, tenant_id=tenant2_id)
        assert result is True

        # Verify tenant_id was updated
        user = repo.get_user_by_id(user_id)
        assert user is not None
        assert user["tenant_id"] == tenant2_id

    def test_update_user_preserves_other_fields(self, tmp_db):
        """Verify that updating tenant_id doesn't affect other fields."""
        repo = UserRepository(db=tmp_db)

        tenant1_id = _insert_tenant(tmp_db, name="Tenant 1")
        tenant2_id = _insert_tenant(tmp_db, name="Tenant 2")

        user_id = _insert_user(tmp_db, username="bob", email="bob@test.com", tenant_id=tenant1_id)

        # Update only tenant_id
        repo.update_user(user_id, tenant_id=tenant2_id)

        # Verify other fields unchanged
        user = repo.get_user_by_id(user_id)
        assert user is not None
        assert user["username"] == "bob"
        assert user["email"] == "bob@test.com"
        assert user["role"] == "user"
        assert user["tenant_id"] == tenant2_id

    def test_update_user_multiple_fields_with_tenant_id(self, tmp_db):
        """Verify that tenant_id can be updated alongside other fields."""
        repo = UserRepository(db=tmp_db)

        tenant1_id = _insert_tenant(tmp_db, name="Tenant 1")
        tenant2_id = _insert_tenant(tmp_db, name="Tenant 2")

        user_id = _insert_user(tmp_db, username="charlie", tenant_id=tenant1_id)

        # Update multiple fields including tenant_id
        result = repo.update_user(
            user_id,
            username="charlie_updated",
            email="charlie@new.com",
            role="admin",
            tenant_id=tenant2_id,
        )
        assert result is True

        # Verify all fields updated
        user = repo.get_user_by_id(user_id)
        assert user is not None
        assert user["username"] == "charlie_updated"
        assert user["email"] == "charlie@new.com"
        assert user["role"] == "admin"
        assert user["tenant_id"] == tenant2_id


class TestUserCRUD:
    """Basic CRUD tests for UserRepository."""

    def test_get_user_by_id(self, tmp_db):
        """Retrieve user by ID."""
        repo = UserRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="dave")

        user = repo.get_user_by_id(user_id)
        assert user is not None
        assert user["username"] == "dave"

    def test_get_user_by_id_not_found(self, tmp_db):
        """Nonexistent user returns None."""
        repo = UserRepository(db=tmp_db)
        user = repo.get_user_by_id(99999)
        assert user is None

    def test_get_user_by_username(self, tmp_db):
        """Retrieve user by username."""
        repo = UserRepository(db=tmp_db)
        _insert_user(tmp_db, username="eve")

        user = repo.get_user_by_username("eve")
        assert user is not None
        assert user["username"] == "eve"

    def test_get_user_by_username_not_found(self, tmp_db):
        """Nonexistent username returns None."""
        repo = UserRepository(db=tmp_db)
        user = repo.get_user_by_username("nonexistent")
        assert user is None

    def test_get_all_users(self, tmp_db):
        """Retrieve all users."""
        repo = UserRepository(db=tmp_db)
        _insert_user(tmp_db, username="user1")
        _insert_user(tmp_db, username="user2")

        users = repo.get_all_users()
        assert len(users) >= 2
        usernames = [u["username"] for u in users]
        assert "user1" in usernames
        assert "user2" in usernames

    def test_update_user_username(self, tmp_db):
        """Update username field."""
        repo = UserRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="oldname")

        result = repo.update_user(user_id, username="newname")
        assert result is True

        user = repo.get_user_by_id(user_id)
        assert user["username"] == "newname"

    def test_update_user_email(self, tmp_db):
        """Update email field."""
        repo = UserRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="frank", email="old@test.com")

        result = repo.update_user(user_id, email="new@test.com")
        assert result is True

        user = repo.get_user_by_id(user_id)
        assert user["email"] == "new@test.com"

    def test_update_user_role(self, tmp_db):
        """Update role field."""
        repo = UserRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="grace")

        result = repo.update_user(user_id, role="admin")
        assert result is True

        user = repo.get_user_by_id(user_id)
        assert user["role"] == "admin"

    def test_update_user_is_active(self, tmp_db):
        """Update is_active field."""
        repo = UserRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="henry")

        # Set inactive
        result = repo.update_user(user_id, is_active=False)
        assert result is True

        user = repo.get_user_by_id(user_id)
        # SQLite stores boolean as 0/1
        active_val = user["is_active"]
        assert active_val == 0 or active_val is False

        # Set active again
        result = repo.update_user(user_id, is_active=True)
        assert result is True

        user = repo.get_user_by_id(user_id)
        active_val = user["is_active"]
        assert active_val == 1 or active_val is True
