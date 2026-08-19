"""
Integration tests for Issue #2761: Tool Account Mapping Source/Status Support

SQLite version of test_tool_account_2761_pg.py.

Tests for:
- Predeclared account creation (pending status)
- Activation flow (pending -> active)
- Conflict detection (type, owner, tenant conflicts)
- Stale detection
- Optimistic locking for concurrent activation
"""

from datetime import datetime, timedelta

import pytest

from app.models.user_tool_account import MappingSource, MappingStatus
from app.repositories.user_tool_account_repo import UserToolAccountRepository


def _insert_user(tmp_db, username="testuser", email=None, tenant_id=None):
    """Insert a user row for foreign key references."""
    if email is None:
        email = f"{username}@example.com"
    cursor = tmp_db.execute(
        "INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)",
        (username, email, "hashed_pw", "user", tenant_id),
    )
    return cursor.lastrowid


def _insert_tenant(tmp_db, name="test_tenant"):
    """Insert a tenant row."""
    slug = name.replace("_", "-").lower()
    cursor = tmp_db.execute(
        "INSERT INTO tenants (name, slug) VALUES (?, ?)",
        (name, slug),
    )
    return cursor.lastrowid


class TestPredeclaredAccount:
    """Tests for creating predeclared accounts with pending status."""

    def test_create_predeclared_account(self, tmp_db):
        """Create a predeclared account with pending status."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="alice")
        tenant_id = _insert_tenant(tmp_db, name="tenant1")

        account = repo.create(
            user_id=user_id,
            tool_account="alice-macbook-qwen",
            tool_type="qwen",
            mapping_source=MappingSource.PREDECLARED.value,
            mapping_status=MappingStatus.PENDING.value,
            created_by=user_id,
            tenant_id=tenant_id,
        )

        assert account is not None
        assert account.mapping_source == MappingSource.PREDECLARED.value
        assert account.mapping_status == MappingStatus.PENDING.value
        assert account.created_by == user_id
        assert account.tenant_id == tenant_id
        assert account.discovered_at is None
        assert account.observed_message_count == 0

    def test_create_manual_account_defaults_to_active(self, tmp_db):
        """Manually created accounts default to active status."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="bob")

        account = repo.create(
            user_id=user_id,
            tool_account="bob-qwen",
            tool_type="qwen",
        )

        assert account is not None
        assert account.mapping_source == MappingSource.MANUAL.value
        assert account.mapping_status == MappingStatus.ACTIVE.value

    def test_get_by_status_pending(self, tmp_db):
        """Query accounts by pending status."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="charlie")
        tenant_id = _insert_tenant(tmp_db, name="tenant2")

        # Create pending and active accounts
        pending = repo.create(
            user_id=user_id,
            tool_account="charlie-pending",
            mapping_status=MappingStatus.PENDING.value,
            tenant_id=tenant_id,
        )
        active = repo.create(
            user_id=user_id,
            tool_account="charlie-active",
            mapping_status=MappingStatus.ACTIVE.value,
            tenant_id=tenant_id,
        )

        pending_accounts = repo.get_by_status(MappingStatus.PENDING.value, tenant_id=tenant_id)
        assert len(pending_accounts) == 1
        assert pending_accounts[0].tool_account == "charlie-pending"


class TestActivationFlow:
    """Tests for activating pending accounts when data arrives."""

    def test_activate_pending_account(self, tmp_db):
        """Activate a pending account when matching sender_name found."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="dave")
        tenant_id = _insert_tenant(tmp_db, name="tenant3")

        # Create pending predeclared account
        pending = repo.create(
            user_id=user_id,
            tool_account="dave-qwen",
            tool_type="qwen",
            mapping_source=MappingSource.PREDECLARED.value,
            mapping_status=MappingStatus.PENDING.value,
            tenant_id=tenant_id,
        )
        assert pending is not None
        assert pending.mapping_status == MappingStatus.PENDING.value

        # Simulate data arrival - activate the account
        activated = repo.activate_mapping(pending.id, expected_version=pending.version)

        assert activated is not None
        assert activated.mapping_status == MappingStatus.ACTIVE.value
        assert activated.discovered_at is not None
        assert activated.last_activity_at is not None
        assert activated.version == pending.version + 1

    def test_get_pending_for_activation(self, tmp_db):
        """Batch query pending accounts by sender_names."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="eve")
        tenant_id = _insert_tenant(tmp_db, name="tenant4")

        # Create multiple pending accounts
        repo.create(
            user_id=user_id,
            tool_account="eve-qwen",
            mapping_status=MappingStatus.PENDING.value,
            tenant_id=tenant_id,
        )
        repo.create(
            user_id=user_id,
            tool_account="eve-claude",
            mapping_status=MappingStatus.PENDING.value,
            tenant_id=tenant_id,
        )
        repo.create(
            user_id=user_id,
            tool_account="eve-active",
            mapping_status=MappingStatus.ACTIVE.value,
            tenant_id=tenant_id,
        )

        # Query pending accounts for activation
        pending = repo.get_pending_for_activation(
            ["eve-qwen", "eve-claude", "eve-active", "unknown"],
            tenant_id=tenant_id,
        )

        assert len(pending) == 2
        pending_names = {p.tool_account for p in pending}
        assert pending_names == {"eve-qwen", "eve-claude"}

    def test_activate_fails_on_version_mismatch(self, tmp_db):
        """Activation fails when version doesn't match (optimistic lock)."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="frank")

        pending = repo.create(
            user_id=user_id,
            tool_account="frank-qwen",
            mapping_status=MappingStatus.PENDING.value,
        )

        # Try to activate with wrong version
        result = repo.activate_mapping(pending.id, expected_version=999)
        assert result is None

        # Verify status unchanged
        still_pending = repo.get_by_id(pending.id)
        assert still_pending.mapping_status == MappingStatus.PENDING.value

    def test_update_status_with_version(self, tmp_db):
        """Update status atomically with version check."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="grace")

        account = repo.create(
            user_id=user_id,
            tool_account="grace-qwen",
            mapping_status=MappingStatus.PENDING.value,
        )

        # Update with correct version
        updated = repo.update_status_with_version(
            account.id,
            MappingStatus.ACTIVE.value,
            expected_version=account.version,
        )
        assert updated is not None
        assert updated.mapping_status == MappingStatus.ACTIVE.value
        assert updated.version == account.version + 1

    def test_touch_activity(self, tmp_db):
        """Update last_activity_at timestamp."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="henry")

        account = repo.create(
            user_id=user_id,
            tool_account="henry-qwen",
            mapping_status=MappingStatus.ACTIVE.value,
        )

        result = repo.touch_activity(account.id)
        assert result is True

        updated = repo.get_by_id(account.id)
        assert updated.last_activity_at is not None

    def test_increment_message_count(self, tmp_db):
        """Increment observed_message_count."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="ivy")

        account = repo.create(
            user_id=user_id,
            tool_account="ivy-qwen",
            mapping_status=MappingStatus.ACTIVE.value,
        )

        result = repo.increment_message_count(account.id, count=10)
        assert result is True

        updated = repo.get_by_id(account.id)
        assert updated.observed_message_count == 10

        # Increment again
        repo.increment_message_count(account.id, count=5)
        updated = repo.get_by_id(account.id)
        assert updated.observed_message_count == 15


class TestConflictDetection:
    """Tests for conflict detection (type, owner, tenant)."""

    def test_create_or_ignore_on_conflict(self, tmp_db):
        """create_or_ignore returns None when account exists."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="jack")

        first = repo.create_or_ignore(
            user_id=user_id,
            tool_account="jack-qwen",
            tool_type="qwen",
        )
        assert first is not None

        # Duplicate should return None
        second = repo.create_or_ignore(
            user_id=user_id,
            tool_account="jack-qwen",
            tool_type="qwen",
        )
        assert second is None

    def test_conflict_status_enum_values(self, tmp_db):
        """Verify conflict status enum values are valid."""
        assert MappingStatus.CONFLICT_TYPE.value == "conflict_type"
        assert MappingStatus.CONFLICT_OWNER.value == "conflict_owner"
        assert MappingStatus.CONFLICT_TENANT.value == "conflict_tenant"


class TestStaleDetection:
    """Tests for detecting stale accounts."""

    def test_get_stale_mappings(self, tmp_db):
        """Get accounts with no activity for specified days."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="kate")
        tenant_id = _insert_tenant(tmp_db, name="tenant5")

        # Create active account with old activity
        old_account = repo.create(
            user_id=user_id,
            tool_account="kate-old",
            mapping_status=MappingStatus.ACTIVE.value,
            tenant_id=tenant_id,
        )

        # Manually set last_activity_at to 30 days ago
        tmp_db.execute(
            "UPDATE user_tool_accounts SET last_activity_at = ? WHERE id = ?",
            (datetime.now() - timedelta(days=30), old_account.id),
        )

        # Create recent active account
        recent_account = repo.create(
            user_id=user_id,
            tool_account="kate-recent",
            mapping_status=MappingStatus.ACTIVE.value,
            tenant_id=tenant_id,
        )
        repo.touch_activity(recent_account.id)

        # Query stale accounts (7 days threshold)
        stale = repo.get_stale_mappings(stale_days=7, tenant_id=tenant_id)

        stale_ids = {s.id for s in stale}
        assert old_account.id in stale_ids
        assert recent_account.id not in stale_ids

    def test_get_stale_mappings_no_activity(self, tmp_db):
        """Accounts with no activity_at are considered stale."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="leo")

        account = repo.create(
            user_id=user_id,
            tool_account="leo-qwen",
            mapping_status=MappingStatus.ACTIVE.value,
        )

        # Don't touch activity - should be stale
        stale = repo.get_stale_mappings(stale_days=7)

        # Should include accounts with NULL last_activity_at
        assert account.id in {s.id for s in stale}


class TestConcurrentActivation:
    """Tests for concurrent activation scenarios."""

    def test_concurrent_activation_version_check(self, tmp_db):
        """Verify optimistic lock prevents concurrent activation."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="mike")

        account = repo.create(
            user_id=user_id,
            tool_account="mike-qwen",
            mapping_status=MappingStatus.PENDING.value,
        )

        # First activation succeeds
        first = repo.activate_mapping(account.id, expected_version=account.version)
        assert first is not None
        assert first.mapping_status == MappingStatus.ACTIVE.value

        # Second activation with old version fails
        second = repo.activate_mapping(account.id, expected_version=account.version)
        assert second is None

        # Account remains active
        still_active = repo.get_by_id(account.id)
        assert still_active.mapping_status == MappingStatus.ACTIVE.value
        assert still_active.version == account.version + 1


class TestMigrationScenarios:
    """Tests for data migration scenarios."""

    def test_migrate_discovered_account(self, tmp_db):
        """Migrate existing account to discovered status."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="nancy")
        tenant_id = _insert_tenant(tmp_db, name="tenant6")

        # Simulate legacy account (no mapping_source/status)
        account = repo.create(
            user_id=user_id,
            tool_account="nancy-qwen",
            tenant_id=tenant_id,
        )

        # Migration: update to discovered/active
        updated = repo.update_status_with_version(
            account.id,
            MappingStatus.ACTIVE.value,
            expected_version=account.version,
        )
        assert updated is not None

    def test_migrate_predeclared_without_data(self, tmp_db):
        """Predeclared account without data remains pending."""
        repo = UserToolAccountRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="oscar")

        account = repo.create(
            user_id=user_id,
            tool_account="oscar-nonexistent",
            mapping_source=MappingSource.LEGACY_PREDECLARED.value,
            mapping_status=MappingStatus.PENDING.value,
        )

        # Should remain pending since no data arrived
        fetched = repo.get_by_id(account.id)
        assert fetched.mapping_status == MappingStatus.PENDING.value