"""Unit tests for PermissionService."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.permission_service import (
    DEFAULT_ROLES,
    Permission,
    PermissionService,
    Role,
    get_ddl_statements,
)


class TestPermissionEnum:
    """Test Permission enum."""

    def test_all_permissions_exist(self):
        expected = [
            "view_dashboard",
            "view_messages",
            "export_messages",
            "view_analysis",
            "run_analysis",
            "export_analysis",
            "view_users",
            "create_user",
            "edit_user",
            "delete_user",
            "manage_permissions",
            "view_quota",
            "manage_quota",
            "view_audit_logs",
            "export_audit_logs",
            "view_content_filter",
            "manage_content_filter",
            "admin_access",
            "system_config",
        ]
        values = [p.value for p in Permission]
        for perm in expected:
            assert perm in values, f"Missing permission: {perm}"

    def test_permission_count(self):
        assert len(Permission) == 19

    def test_permission_value_is_string(self):
        for perm in Permission:
            assert isinstance(perm.value, str)


class TestRole:
    """Test Role dataclass."""

    def test_role_creation(self):
        role = Role(name="test", description="Test role", permissions={"view_dashboard"})
        assert role.name == "test"
        assert role.description == "Test role"
        assert role.permissions == {"view_dashboard"}

    def test_role_default_permissions_empty(self):
        role = Role(name="empty", description="Empty")
        assert role.permissions == set()

    def test_has_permission_true(self):
        role = Role(name="test", description="Test", permissions={"view_dashboard"})
        assert role.has_permission("view_dashboard") is True

    def test_has_permission_false(self):
        role = Role(name="test", description="Test", permissions={"view_dashboard"})
        assert role.has_permission("export_messages") is False

    def test_has_permission_admin_grants_all(self):
        role = Role(
            name="admin_role",
            description="Admin",
            permissions={"admin_access"},
        )
        # ADMIN_ACCESS should grant any permission via has_permission
        assert role.has_permission("view_dashboard") is True
        assert role.has_permission("system_config") is True
        assert role.has_permission("nonexistent_perm") is True

    def test_has_permission_no_admin_no_match(self):
        role = Role(
            name="user_role",
            description="User",
            permissions={"view_dashboard", "view_messages"},
        )
        assert role.has_permission("view_dashboard") is True
        assert role.has_permission("export_messages") is False


class TestDefaultRoles:
    """Test DEFAULT_ROLES configuration."""

    def test_admin_has_all_permissions(self):
        admin = DEFAULT_ROLES["admin"]
        all_perm_values = {p.value for p in Permission}
        assert admin.permissions == all_perm_values

    def test_manager_permissions(self):
        manager = DEFAULT_ROLES["manager"]
        assert "view_dashboard" in manager.permissions
        assert "export_messages" in manager.permissions
        assert "run_analysis" in manager.permissions
        assert "export_audit_logs" in manager.permissions
        # Manager should NOT have admin_access or system_config
        assert "admin_access" not in manager.permissions
        assert "system_config" not in manager.permissions
        assert "manage_permissions" not in manager.permissions
        assert "delete_user" not in manager.permissions

    def test_user_permissions(self):
        user = DEFAULT_ROLES["user"]
        assert "view_dashboard" in user.permissions
        assert "view_messages" in user.permissions
        assert "view_analysis" in user.permissions
        assert "view_quota" in user.permissions
        # User should NOT have admin or export permissions
        assert "admin_access" not in user.permissions
        assert "export_messages" not in user.permissions
        assert "manage_permissions" not in user.permissions

    def test_readonly_permissions(self):
        readonly = DEFAULT_ROLES["readonly"]
        assert readonly.permissions == {"view_dashboard"}

    def test_all_default_roles_have_descriptions(self):
        for name, role in DEFAULT_ROLES.items():
            assert role.description, f"Role '{name}' has no description"
            assert role.name == name

    def test_four_default_roles(self):
        assert set(DEFAULT_ROLES.keys()) == {"admin", "manager", "user", "readonly"}


class TestPermissionServiceInit:
    """Test PermissionService initialization."""

    def _make_service(self, db_rows=None):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = db_rows or []
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo

    def test_init_with_defaults(self):
        svc, mock_db, _ = self._make_service()
        assert svc.db is mock_db
        # Roles should be loaded from DEFAULT_ROLES
        assert "admin" in svc.roles
        assert "user" in svc.roles
        assert "manager" in svc.roles
        assert "readonly" in svc.roles

    def test_init_loads_custom_role_permissions(self):
        db_rows = [
            {"role_name": "custom_role", "permission": "view_dashboard"},
            {"role_name": "custom_role", "permission": "view_messages"},
        ]
        svc, mock_db, _ = self._make_service(db_rows=db_rows)
        assert "custom_role" in svc.roles
        assert "view_dashboard" in svc.roles["custom_role"].permissions
        assert "view_messages" in svc.roles["custom_role"].permissions

    def test_init_ignores_rows_with_missing_fields(self):
        db_rows = [
            {"role_name": None, "permission": "view_dashboard"},
            {"role_name": "custom", "permission": None},
            {},  # empty row
        ]
        svc, _, _ = self._make_service(db_rows=db_rows)
        # Should not create any extra roles
        assert "custom" not in svc.roles


class TestPermissionServiceGetRole:
    """Test PermissionService.get_role."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc

    def test_get_existing_role(self):
        svc = self._make_service()
        role = svc.get_role("admin")
        assert role is not None
        assert role.name == "admin"

    def test_get_nonexistent_role(self):
        svc = self._make_service()
        role = svc.get_role("nonexistent")
        assert role is None

    def test_get_all_roles(self):
        svc = self._make_service()
        roles = svc.get_all_roles()
        assert "admin" in roles
        assert "user" in roles
        assert isinstance(roles, dict)


class TestPermissionServiceGetUserPermissions:
    """Test PermissionService.get_user_permissions."""

    def _make_service(self, db_rows=None):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = db_rows or []
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo

    def test_user_with_admin_role(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 1, "role": "admin"}
        mock_db.fetch_all.return_value = []  # no custom permissions

        perms = svc.get_user_permissions(1)
        # Admin should have all permissions
        all_perm_values = {p.value for p in Permission}
        assert perms == all_perm_values

    def test_user_with_user_role(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 2, "role": "user"}
        mock_db.fetch_all.return_value = []

        perms = svc.get_user_permissions(2)
        assert "view_dashboard" in perms
        assert "view_messages" in perms
        assert "admin_access" not in perms

    def test_user_with_custom_permissions(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 3, "role": "user"}
        # Service already initialized, so set return_value for the custom permissions query
        mock_db.fetch_all.return_value = [
            {"permission": "export_messages"},
            {"permission": "run_analysis"},
        ]

        perms = svc.get_user_permissions(3)
        assert "export_messages" in perms
        assert "run_analysis" in perms
        # Also has base user perms
        assert "view_dashboard" in perms

    def test_user_not_found(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = None

        perms = svc.get_user_permissions(999)
        assert perms == set()

    def test_user_with_unknown_role_defaults_to_user(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 4, "role": "unknown_role"}
        mock_db.fetch_all.return_value = []

        perms = svc.get_user_permissions(4)
        # Should fall back to 'user' role permissions
        assert "view_dashboard" in perms
        assert "admin_access" not in perms

    def test_user_with_no_role_field_defaults_to_user(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 5}  # no 'role' key
        mock_db.fetch_all.return_value = []

        perms = svc.get_user_permissions(5)
        assert "view_dashboard" in perms


class TestPermissionServiceHasPermission:
    """Test PermissionService.has_permission."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo

    def test_has_permission_true(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 1, "role": "manager"}
        mock_db.fetch_all.return_value = []

        assert svc.has_permission(1, "view_dashboard") is True
        assert svc.has_permission(1, "export_messages") is True

    def test_has_permission_false(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 2, "role": "user"}
        mock_db.fetch_all.return_value = []

        assert svc.has_permission(2, "admin_access") is False
        assert svc.has_permission(2, "export_messages") is False

    def test_admin_has_any_permission(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 1, "role": "admin"}
        mock_db.fetch_all.return_value = []

        assert svc.has_permission(1, "view_dashboard") is True
        assert svc.has_permission(1, "system_config") is True
        assert svc.has_permission(1, "delete_user") is True

    def test_nonexistent_user_has_no_permission(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = None
        mock_db.fetch_all.return_value = []

        assert svc.has_permission(999, "view_dashboard") is False


class TestPermissionServiceCheckPermission:
    """Test PermissionService.check_permission."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo

    def test_check_permission_granted(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 1, "role": "admin"}
        mock_db.fetch_all.return_value = []

        result, error = svc.check_permission(1, "view_dashboard")
        assert result is True
        assert error is None

    def test_check_permission_denied(self):
        svc, mock_db, mock_user_repo = self._make_service()
        mock_user_repo.get_user_by_id.return_value = {"id": 2, "role": "readonly"}
        mock_db.fetch_all.return_value = []

        result, error = svc.check_permission(2, "export_messages")
        assert result is False
        assert "export_messages" in error
        assert "Permission denied" in error


class TestPermissionServiceGrantPermission:
    """Test PermissionService.grant_permission."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo, mock_cursor

    def test_grant_permission_success(self):
        svc, mock_db, _, mock_cursor = self._make_service()

        result = svc.grant_permission(1, "export_messages", granted_by=99)
        assert result is True
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "INSERT" in call_args[0][0]
        assert call_args[0][1] == (1, "export_messages", 99)

    def test_grant_permission_db_error(self):
        svc, mock_db, _, mock_cursor = self._make_service()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = svc.grant_permission(1, "export_messages", granted_by=99)
        assert result is False


class TestPermissionServiceRevokePermission:
    """Test PermissionService.revoke_permission."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo, mock_cursor

    def test_revoke_permission_success(self):
        svc, mock_db, _, mock_cursor = self._make_service()

        result = svc.revoke_permission(1, "export_messages")
        assert result is True
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "DELETE" in call_args[0][0]
        assert call_args[0][1] == (1, "export_messages")

    def test_revoke_permission_db_error(self):
        svc, mock_db, _, mock_cursor = self._make_service()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = svc.revoke_permission(1, "export_messages")
        assert result is False


class TestPermissionServiceCreateRole:
    """Test PermissionService.create_role."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo, mock_cursor

    def test_create_role_success(self):
        svc, _, _, mock_cursor = self._make_service()

        result = svc.create_role(
            "analyst",
            "Data analyst role",
            {"view_dashboard", "view_analysis", "run_analysis"},
        )
        assert result is True
        assert "analyst" in svc.roles
        assert svc.roles["analyst"].description == "Data analyst role"
        assert "view_dashboard" in svc.roles["analyst"].permissions
        # Should have inserted 3 rows
        assert mock_cursor.execute.call_count == 3

    def test_create_role_already_exists(self):
        svc, _, _, _ = self._make_service()

        result = svc.create_role("admin", "Duplicate admin", {"admin_access"})
        assert result is False

    def test_create_role_db_error(self):
        svc, _, _, mock_cursor = self._make_service()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = svc.create_role("new_role", "New role", {"view_dashboard"})
        assert result is False
        # Role should not be added to in-memory dict
        assert "new_role" not in svc.roles

    def test_create_role_with_empty_permissions(self):
        svc, _, _, mock_cursor = self._make_service()

        result = svc.create_role("empty_role", "No permissions", set())
        assert result is True
        assert "empty_role" in svc.roles
        assert svc.roles["empty_role"].permissions == set()
        # No INSERT calls since permissions set is empty
        mock_cursor.execute.assert_not_called()


class TestPermissionServiceUpdateRolePermissions:
    """Test PermissionService.update_role_permissions."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo, mock_cursor

    def test_update_role_permissions_success(self):
        svc, _, _, mock_cursor = self._make_service()

        new_perms = {"view_dashboard", "view_messages", "export_messages"}
        result = svc.update_role_permissions("user", new_perms)
        assert result is True
        assert svc.roles["user"].permissions == new_perms
        # DELETE + 3 INSERTs = 4 calls
        assert mock_cursor.execute.call_count == 4

    def test_update_role_nonexistent(self):
        svc, _, _, _ = self._make_service()

        result = svc.update_role_permissions("nonexistent", {"view_dashboard"})
        assert result is False

    def test_update_role_db_error(self):
        svc, _, _, mock_cursor = self._make_service()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = svc.update_role_permissions("user", {"view_dashboard"})
        assert result is False


class TestPermissionServiceDeleteRole:
    """Test PermissionService.delete_role."""

    def _make_service(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db, mock_user_repo, mock_cursor

    def test_delete_custom_role(self):
        svc, _, _, mock_cursor = self._make_service()
        # Create a custom role first
        svc.roles["custom"] = Role(
            name="custom", description="Custom", permissions={"view_dashboard"}
        )

        result = svc.delete_role("custom")
        assert result is True
        assert "custom" not in svc.roles
        mock_cursor.execute.assert_called_once()

    def test_delete_default_role_denied(self):
        svc, _, _, _ = self._make_service()

        result = svc.delete_role("admin")
        assert result is False
        assert "admin" in svc.roles

    def test_delete_all_default_roles_denied(self):
        svc, _, _, _ = self._make_service()

        for role_name in ["admin", "manager", "user", "readonly"]:
            assert svc.delete_role(role_name) is False
            assert role_name in svc.roles

    def test_delete_nonexistent_role_returns_true(self):
        svc, _, _, _ = self._make_service()

        # Deleting a role that doesn't exist is treated as success
        result = svc.delete_role("nonexistent")
        assert result is True

    def test_delete_role_db_error(self):
        svc, _, _, mock_cursor = self._make_service()
        svc.roles["custom"] = Role(name="custom", description="Custom", permissions=set())
        mock_cursor.execute.side_effect = Exception("DB error")

        result = svc.delete_role("custom")
        assert result is False
        # Role should still be in memory since DB failed
        assert "custom" in svc.roles


class TestPermissionServiceGetUsersWithPermission:
    """Test PermissionService.get_users_with_permission."""

    def _make_service(self, admin_user=None, regular_user=None):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_user_repo = MagicMock()

        # Reset DEFAULT_ROLES to prevent test interaction
        for role_name, role in DEFAULT_ROLES.items():
            if role_name == "admin":
                role.permissions = {p.value for p in Permission}
            elif role_name == "manager":
                role.permissions = {
                    "view_dashboard",
                    "view_messages",
                    "export_messages",
                    "view_analysis",
                    "run_analysis",
                    "export_analysis",
                    "view_users",
                    "view_quota",
                    "view_audit_logs",
                    "export_audit_logs",
                    "view_content_filter",
                }
            elif role_name == "user":
                role.permissions = {
                    "view_dashboard",
                    "view_messages",
                    "view_analysis",
                    "view_quota",
                }
            elif role_name == "readonly":
                role.permissions = {"view_dashboard"}

        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)

        admin = admin_user or {"id": 1, "username": "admin_user", "role": "admin"}
        regular = regular_user or {"id": 2, "username": "regular_user", "role": "user"}
        mock_user_repo.get_all_users.return_value = [admin, regular]

        # Set up get_user_by_id to return the right user by id
        def get_user(user_id):
            if user_id == 1:
                return admin
            elif user_id == 2:
                return regular
            return None

        mock_user_repo.get_user_by_id.side_effect = get_user
        return svc, mock_db, mock_user_repo

    def test_get_users_with_admin_permission(self):
        svc, _, _ = self._make_service()

        users = svc.get_users_with_permission("system_config")
        assert len(users) == 1
        assert users[0]["username"] == "admin_user"

    def test_get_users_with_basic_permission(self):
        svc, _, _ = self._make_service()

        users = svc.get_users_with_permission("view_dashboard")
        # Both admin and regular user should have view_dashboard
        assert len(users) == 2

    def test_get_users_with_restricted_permission(self):
        svc, _, _ = self._make_service()

        users = svc.get_users_with_permission("export_messages")
        # Only admin has export_messages
        assert len(users) == 1
        assert users[0]["username"] == "admin_user"


class TestPermissionServiceGetPermissionAuditLog:
    """Test PermissionService.get_permission_audit_log."""

    def _make_service(self, user_rows=None, all_rows=None):
        mock_db = MagicMock()

        # Side effect to return different results based on query
        def fetch_all_side_effect(query, params=None):
            if "WHERE up.user_id" in query:
                return user_rows or []
            return all_rows or []

        mock_db.fetch_all.side_effect = fetch_all_side_effect
        mock_user_repo = MagicMock()
        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        return svc, mock_db

    def test_audit_log_for_specific_user(self):
        user_rows = [
            {
                "user_id": 1,
                "permission": "export_messages",
                "granted_by": 99,
                "granted_by_username": "admin",
            },
        ]
        svc, mock_db = self._make_service(user_rows=user_rows)

        result = svc.get_permission_audit_log(user_id=1)
        assert len(result) == 1
        assert result[0]["permission"] == "export_messages"

    def test_audit_log_for_all_users(self):
        all_rows = [
            {
                "user_id": 1,
                "permission": "export_messages",
                "username": "user1",
                "granted_by_username": "admin",
            },
            {
                "user_id": 2,
                "permission": "run_analysis",
                "username": "user2",
                "granted_by_username": "admin",
            },
        ]
        svc, mock_db = self._make_service(all_rows=all_rows)

        result = svc.get_permission_audit_log()
        assert len(result) == 2

    def test_audit_log_empty(self):
        svc, _ = self._make_service()

        result = svc.get_permission_audit_log(user_id=999)
        assert result == []


class TestGetDDLStatements:
    """Test get_ddl_statements function."""

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_ddl_for_sqlite(self, mock_pg):
        statements = get_ddl_statements()
        assert len(statements) == 4
        # Check SQLite syntax
        for stmt in statements[:2]:
            assert "INTEGER PRIMARY KEY AUTOINCREMENT" in stmt
        assert "idx_user_permissions_user" in statements[2]
        assert "idx_role_permissions_role" in statements[3]

    @patch("app.repositories.database.is_postgresql", return_value=True)
    def test_ddl_for_postgresql(self, mock_pg):
        statements = get_ddl_statements()
        assert len(statements) == 4
        for stmt in statements[:2]:
            assert "SERIAL PRIMARY KEY" in stmt

    def test_ddl_creates_required_tables(self):
        with patch("app.repositories.database.is_postgresql", return_value=False):
            statements = get_ddl_statements()
            table_sql = " ".join(statements)
            assert "user_permissions" in table_sql
            assert "role_permissions" in table_sql


class TestPermissionServiceEnsureTables:
    """Test PermissionService._ensure_tables."""

    def test_ensure_tables_creates_tables_and_indexes(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_db.is_postgresql = False
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        mock_user_repo = MagicMock()

        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        svc._ensure_tables()

        # Should execute: 2 CREATE TABLE + 2 CREATE INDEX = 4 calls
        assert mock_cursor.execute.call_count >= 4
        mock_conn.commit.assert_called()


class TestPermissionServiceLoadRolePermissions:
    """Test PermissionService._load_role_permissions."""

    def test_load_adds_permissions_to_existing_role(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"role_name": "user", "permission": "export_messages"},
        ]
        mock_user_repo = MagicMock()

        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        # user role should now have export_messages in addition to defaults
        assert "export_messages" in svc.roles["user"].permissions
        # Original permissions should still be there
        assert "view_dashboard" in svc.roles["user"].permissions

    def test_load_creates_new_custom_role(self):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"role_name": "super_editor", "permission": "edit_user"},
            {"role_name": "super_editor", "permission": "create_user"},
        ]
        mock_user_repo = MagicMock()

        svc = PermissionService(db=mock_db, user_repo=mock_user_repo)
        assert "super_editor" in svc.roles
        assert svc.roles["super_editor"].description == "Custom role: super_editor"
        assert "edit_user" in svc.roles["super_editor"].permissions
        assert "create_user" in svc.roles["super_editor"].permissions
