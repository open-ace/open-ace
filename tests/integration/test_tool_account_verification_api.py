"""
Integration tests for Tool Account Verification API

Issue #3273: Integration tests for verification endpoints.
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from app.models.user_tool_account import VerificationStatus


def _insert_user(tmp_db, username="testuser", email=None, tenant_id=None):
    """Insert a user row for foreign key references."""
    if email is None:
        email = f"{username}@example.com"
    cursor = tmp_db.execute(
        "INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)",
        (username, email, "hashed_pw", "platform_admin", tenant_id),
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


def _get_admin_user(user_id, tenant_id=None):
    """Get mock admin user for authentication."""
    return {
        "id": user_id,
        "username": "platform_admin",
        "email": "admin@test.com",
        "role": "platform_admin",
        "tenant_id": tenant_id,
    }


class TestVerificationAPIIntegration:
    """Integration tests for verification API endpoints."""

    def test_verify_mapping_endpoint(self, tool_accounts_client, tmp_db):
        """Test POST /api/tool-accounts/<id>/verify endpoint."""
        # Create user and mapping
        user_id = _insert_user(tmp_db, username="alice")
        tenant_id = _insert_tenant(tmp_db, name="tenant1")

        # Insert a tool account mapping
        cursor = tmp_db.execute(
            """
            INSERT INTO user_tool_accounts
            (user_id, tool_account, tool_type, mapping_status, tenant_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, "alice-qwen", "qwen", "active", tenant_id),
        )
        mapping_id = cursor.lastrowid

        # Insert some history data
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, tool_name, host_name, message_id, role, sender_name, message_source, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-09-01", "qwen", "localhost", "msg-001", "user", "alice-qwen", "qwen", user_id),
        )

        # Mock authentication
        admin_user = _get_admin_user(user_id, tenant_id)
        with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
            # Call verify endpoint
            response = tool_accounts_client.post(f"/api/tool-accounts/{mapping_id}/verify")

        assert response.status_code == 200
        data = response.get_json()

        assert data["success"] is True
        assert data["verification_status"] == VerificationStatus.VERIFIED.value
        assert "验证通过" in data["verification_result"]
        assert data["verified_at"] is not None

        # Check that database was updated
        row = tmp_db.fetch_one(
            "SELECT verification_status, verification_result FROM user_tool_accounts WHERE id = ?",
            (mapping_id,),
        )

        assert row is not None
        assert row["verification_status"] == VerificationStatus.VERIFIED.value
        assert "验证通过" in row["verification_result"]

    def test_verify_mapping_without_history_data(self, tool_accounts_client, tmp_db):
        """Test verification fails when no history data exists."""
        # Create user and mapping
        user_id = _insert_user(tmp_db, username="bob")
        tenant_id = _insert_tenant(tmp_db, name="tenant2")

        # Insert a tool account mapping without history data
        cursor = tmp_db.execute(
            """
            INSERT INTO user_tool_accounts
            (user_id, tool_account, tool_type, mapping_status, tenant_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, "bob-claude", "claude", "active", tenant_id),
        )
        mapping_id = cursor.lastrowid

        # Mock authentication
        admin_user = _get_admin_user(user_id, tenant_id)
        with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
            # Call verify endpoint
            response = tool_accounts_client.post(f"/api/tool-accounts/{mapping_id}/verify")

        assert response.status_code == 200
        data = response.get_json()

        assert data["success"] is True
        assert data["verification_status"] == VerificationStatus.FAILED.value
        assert "无历史消息数据" in data["verification_result"]

    def test_verify_nonexistent_mapping(self, tool_accounts_client, tmp_db):
        """Test verification of non-existent mapping returns 404."""
        user_id = _insert_user(tmp_db, username="charlie")

        # Mock authentication
        admin_user = _get_admin_user(user_id)
        with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
            # Call verify endpoint for non-existent mapping
            response = tool_accounts_client.post("/api/tool-accounts/999/verify")

        assert response.status_code == 404

    def test_verify_mapping_tenant_isolation(self, tool_accounts_client, tmp_db):
        """Test that tenant_admin can only verify mappings in their tenant."""
        # Create two tenants and users
        tenant1_id = _insert_tenant(tmp_db, name="tenant1")
        tenant2_id = _insert_tenant(tmp_db, name="tenant2")

        user1_id = _insert_user(tmp_db, username="user1", tenant_id=tenant1_id)
        user2_id = _insert_user(tmp_db, username="user2", tenant_id=tenant2_id)

        # Insert mapping for user2 (tenant2)
        cursor = tmp_db.execute(
            """
            INSERT INTO user_tool_accounts
            (user_id, tool_account, tool_type, mapping_status, tenant_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user2_id, "user2-qwen", "qwen", "active", tenant2_id),
        )
        mapping_id = cursor.lastrowid

        # Mock tenant_admin from tenant1
        tenant_user = {
            "id": user1_id,
            "username": "tenant_admin",
            "email": "tenant_admin@test.com",
            "role": "tenant_admin",
            "tenant_id": tenant1_id,
        }

        with patch("app.auth.decorators._load_user_from_token", return_value=tenant_user):
            # Try to verify mapping from tenant2
            response = tool_accounts_client.post(f"/api/tool-accounts/{mapping_id}/verify")

        # Should get 404 (not found in their tenant)
        assert response.status_code == 404


class TestVerificationStatusInAPIResponses:
    """Tests for verification status in API responses."""

    def test_get_user_tool_accounts_includes_verification_fields(
        self, tool_accounts_client, tmp_db
    ):
        """Test that GET /api/tool-accounts/user/<user_id> includes verification fields."""
        user_id = _insert_user(tmp_db, username="dave")
        tenant_id = _insert_tenant(tmp_db, name="tenant3")

        # Insert a tool account mapping with verification status
        tmp_db.execute(
            """
            INSERT INTO user_tool_accounts
            (user_id, tool_account, tool_type, mapping_status, tenant_id,
             verification_status, verification_result, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "dave-qwen",
                "qwen",
                "active",
                tenant_id,
                VerificationStatus.VERIFIED.value,
                "验证通过",
                datetime.now().isoformat(),
            ),
        )

        # Mock authentication
        admin_user = _get_admin_user(user_id, tenant_id)
        with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
            # Get user tool accounts
            response = tool_accounts_client.get(f"/api/tool-accounts/user/{user_id}")

        assert response.status_code == 200
        data = response.get_json()

        assert len(data) > 0
        account = data[0]

        assert "verification_status" in account
        assert account["verification_status"] == VerificationStatus.VERIFIED.value
        assert "verification_result" in account
        assert account["verification_result"] == "验证通过"
        assert "verified_at" in account
        assert account["verified_at"] is not None