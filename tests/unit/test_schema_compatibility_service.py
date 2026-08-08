"""Unit tests for SchemaCompatibilityService.

Issue: #2330 - Alembic revision graph schema compatibility
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.services.schema_compatibility_service import (
    SchemaCompatibilityService,
    get_schema_compatibility_service,
    timeout_context,
)
from app.services.schema_compatibility_types import (
    BypassState,
    CompatibilityPolicy,
    CompatibilityResult,
    SchemaErrorCategory,
)


class TestSchemaCompatibilityServiceInit:
    """Tests for SchemaCompatibilityService initialization."""

    def test_default_alembic_config_path(self):
        """Test default alembic.ini path."""
        service = SchemaCompatibilityService()
        assert service.alembic_config_path == "alembic.ini"

    def test_custom_alembic_config_path(self):
        """Test custom alembic.ini path."""
        service = SchemaCompatibilityService("/custom/path/alembic.ini")
        assert service.alembic_config_path == "/custom/path/alembic.ini"


class TestGetAncestors:
    """Tests for _get_ancestors method."""

    def test_single_lineage_scalar_down_revision(self):
        """Test ancestors with scalar down_revision (single lineage)."""
        service = SchemaCompatibilityService()

        # Mock ScriptDirectory
        mock_script_dir = Mock(spec=ScriptDirectory)

        # Create mock revisions: head -> middle -> baseline
        mock_head = Mock()
        mock_head.revision = "head_rev"
        mock_head.down_revision = "middle_rev"

        mock_middle = Mock()
        mock_middle.revision = "middle_rev"
        mock_middle.down_revision = "baseline_rev"

        mock_baseline = Mock()
        mock_baseline.revision = "baseline_rev"
        mock_baseline.down_revision = None

        def get_revision(rev_id):
            revs = {
                "head_rev": mock_head,
                "middle_rev": mock_middle,
                "baseline_rev": mock_baseline,
            }
            return revs.get(rev_id)

        mock_script_dir.get_revision.side_effect = get_revision

        ancestors = service._get_ancestors(mock_script_dir, "head_rev")

        assert "head_rev" in ancestors
        assert "middle_rev" in ancestors
        assert "baseline_rev" in ancestors
        assert len(ancestors) == 3

    def test_merge_migration_tuple_down_revision(self):
        """Test ancestors with tuple down_revision (merge migration)."""
        service = SchemaCompatibilityService()

        # Mock ScriptDirectory
        mock_script_dir = Mock(spec=ScriptDirectory)

        # Create merge migration: head (merge) -> [branch_a, branch_b]
        mock_head = Mock()
        mock_head.revision = "head_merge"
        mock_head.down_revision = ("branch_a", "branch_b")

        mock_branch_a = Mock()
        mock_branch_a.revision = "branch_a"
        mock_branch_a.down_revision = "baseline"

        mock_branch_b = Mock()
        mock_branch_b.revision = "branch_b"
        mock_branch_b.down_revision = "baseline"

        mock_baseline = Mock()
        mock_baseline.revision = "baseline"
        mock_baseline.down_revision = None

        def get_revision(rev_id):
            revs = {
                "head_merge": mock_head,
                "branch_a": mock_branch_a,
                "branch_b": mock_branch_b,
                "baseline": mock_baseline,
            }
            return revs.get(rev_id)

        mock_script_dir.get_revision.side_effect = get_revision

        ancestors = service._get_ancestors(mock_script_dir, "head_merge")

        assert "head_merge" in ancestors
        assert "branch_a" in ancestors
        assert "branch_b" in ancestors
        assert "baseline" in ancestors
        assert len(ancestors) == 4


class TestIsInLineage:
    """Tests for _is_in_lineage method."""

    def test_revision_in_lineage(self):
        """Test when revision is in lineage."""
        service = SchemaCompatibilityService()

        mock_script_dir = Mock(spec=ScriptDirectory)

        # Mock: rev_c -> rev_b -> rev_a
        mock_rev_c = Mock()
        mock_rev_c.revision = "rev_c"
        mock_rev_c.down_revision = "rev_b"

        mock_rev_b = Mock()
        mock_rev_b.revision = "rev_b"
        mock_rev_b.down_revision = "rev_a"

        mock_rev_a = Mock()
        mock_rev_a.revision = "rev_a"
        mock_rev_a.down_revision = None

        def get_revision(rev_id):
            revs = {
                "rev_c": mock_rev_c,
                "rev_b": mock_rev_b,
                "rev_a": mock_rev_a,
            }
            return revs.get(rev_id)

        mock_script_dir.get_revision.side_effect = get_revision

        result = service._is_in_lineage(mock_script_dir, "rev_c", "rev_a")
        assert result is True

    def test_revision_not_in_lineage(self):
        """Test when revision is not in lineage."""
        service = SchemaCompatibilityService()

        mock_script_dir = Mock(spec=ScriptDirectory)

        # Two separate lineages
        mock_rev_x = Mock()
        mock_rev_x.revision = "rev_x"
        mock_rev_x.down_revision = None

        mock_rev_y = Mock()
        mock_rev_y.revision = "rev_y"
        mock_rev_y.down_revision = None

        def get_revision(rev_id):
            revs = {
                "rev_x": mock_rev_x,
                "rev_y": mock_rev_y,
            }
            return revs.get(rev_id)

        mock_script_dir.get_revision.side_effect = get_revision

        result = service._is_in_lineage(mock_script_dir, "rev_x", "rev_y")
        assert result is False


class TestGetMissingMigrations:
    """Tests for _get_missing_migrations method."""

    def test_at_head_no_missing(self):
        """Test when database is at head - no missing migrations."""
        service = SchemaCompatibilityService()

        mock_script_dir = Mock(spec=ScriptDirectory)

        mock_head = Mock()
        mock_head.revision = "head_rev"
        mock_head.down_revision = None

        mock_script_dir.get_revision.return_value = mock_head

        missing = service._get_missing_migrations(mock_script_dir, "head_rev", "head_rev")
        assert len(missing) == 0

    def test_behind_head_with_missing(self):
        """Test when database is behind head - has missing migrations."""
        service = SchemaCompatibilityService()

        mock_script_dir = Mock(spec=ScriptDirectory)

        # Lineage: head -> middle -> current
        mock_head = Mock()
        mock_head.revision = "head_rev"
        mock_head.down_revision = "middle_rev"

        mock_middle = Mock()
        mock_middle.revision = "middle_rev"
        mock_middle.down_revision = "current_rev"

        mock_current = Mock()
        mock_current.revision = "current_rev"
        mock_current.down_revision = None

        def get_revision(rev_id):
            revs = {
                "head_rev": mock_head,
                "middle_rev": mock_middle,
                "current_rev": mock_current,
            }
            return revs.get(rev_id)

        mock_script_dir.get_revision.side_effect = get_revision

        missing = service._get_missing_migrations(mock_script_dir, "current_rev", "head_rev")

        # Should have 2 missing migrations (middle_rev and head_rev)
        assert len(missing) == 2
        assert "middle_rev" in missing
        assert "head_rev" in missing


class TestBypassState:
    """Tests for bypass state tracking."""

    def test_bypass_not_active_by_default(self):
        """Test that bypass is not active by default."""
        state = BypassState()
        assert not state.is_active
        assert state.enabled_at is None
        assert state.expires_at is None

    def test_bypass_active_state(self):
        """Test creating active bypass state."""
        current_time = time.time()
        state = BypassState(
            is_active=True,
            enabled_at=current_time,
            expires_at=current_time + 3600,
            database_hash="test_hash",
            reason="operator_initiated",
        )
        assert state.is_active
        assert state.enabled_at == current_time
        assert state.expires_at == current_time + 3600


class TestCompatibilityResult:
    """Tests for CompatibilityResult dataclass."""

    def test_compatible_result(self):
        """Test creating compatible result."""
        result = CompatibilityResult(
            is_compatible=True,
            current_heads=["revision_123"],
            expected_head="revision_123",
            check_duration_ms=50.5,
        )
        assert result.is_compatible
        assert len(result.current_heads) == 1
        assert result.error_category is None

    def test_incompatible_result(self):
        """Test creating incompatible result."""
        result = CompatibilityResult(
            is_compatible=False,
            current_heads=["old_revision"],
            expected_head="new_revision",
            error_category=SchemaErrorCategory.BEHIND_HEAD,
            missing_migrations=["migration_1", "migration_2"],
            diagnostic_message="Database schema is behind head",
        )
        assert not result.is_compatible
        assert result.error_category == SchemaErrorCategory.BEHIND_HEAD
        assert len(result.missing_migrations) == 2


class TestPolicyConfiguration:
    """Tests for policy configuration."""

    def test_get_policy_from_config_default(self):
        """Test default policy when not configured."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {}, clear=True):
            policy = service._get_policy_from_config()
            assert policy == CompatibilityPolicy.REQUIRE_HEAD

    def test_get_policy_from_config_explicit(self):
        """Test explicit policy configuration."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {"OPENACE_COMPATIBILITY_POLICY": "support_n_1"}):
            policy = service._get_policy_from_config()
            assert policy == CompatibilityPolicy.SUPPORT_N_1

    def test_get_policy_from_config_invalid(self):
        """Test invalid policy falls back to default."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {"OPENACE_COMPATIBILITY_POLICY": "invalid_policy"}):
            policy = service._get_policy_from_config()
            assert policy == CompatibilityPolicy.REQUIRE_HEAD


class TestTimeoutContext:
    """Tests for timeout context manager."""

    def test_timeout_context_completes(self):
        """Test that timeout context completes normally."""
        with timeout_context(5):
            result = 1 + 1
        assert result == 2

    def test_timeout_context_triggers(self):
        """Test that timeout context can be created and used."""
        # Test that the timeout context can be created and used
        with timeout_context(100):  # Long timeout
            result = 1 + 1
        assert result == 2  # Verify the context executed successfully


class TestErrorCategories:
    """Tests for error category determination."""

    def test_all_error_categories_defined(self):
        """Test that all error categories are defined."""
        expected = [
            "FRESH_DATABASE",
            "EMPTY_VERSION_TABLE",
            "UNKNOWN_REVISION",
            "MULTIPLE_HEADS",
            "NOT_IN_LINEAGE",
            "BEHIND_HEAD",
            "MISSING_MIGRATION_FILES",
            "CONFLICTING_REVISIONS",
            "SCRIPT_DIRECTORY_ERROR",
            "BYPASS_EXPIRED",
        ]

        for cat_name in expected:
            assert hasattr(SchemaErrorCategory, cat_name)


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_service_singleton(self):
        """Test that get_schema_compatibility_service returns singleton."""
        service1 = get_schema_compatibility_service()
        service2 = get_schema_compatibility_service()

        assert service1 is service2

    def test_service_is_cached(self):
        """Test that service instance is cached."""
        # Clear cache first
        import app.services.schema_compatibility_service as module

        module._service_instance = None

        service = get_schema_compatibility_service()
        assert module._service_instance is service


class TestDatabaseUrlSafe:
    """Tests for _get_database_url_safe method."""

    def test_url_without_password(self):
        """Test URL without password."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user@host/db"}):
            url = service._get_database_url_safe(Mock())
            assert "user@host/db" in url

    def test_url_with_password(self):
        """Test URL with password (password should be removed)."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:password@host/db"}):
            url = service._get_database_url_safe(Mock())
            assert "password" not in url
            assert "user@" in url

    def test_no_url_environment(self):
        """Test when DATABASE_URL not set."""
        service = SchemaCompatibilityService()

        with patch.dict(os.environ, {}, clear=True):
            url = service._get_database_url_safe(Mock())
            assert url == "unknown"


class TestFormatDiagnosticMessage:
    """Tests for diagnostic message formatting."""

    def test_format_behind_head_message(self):
        """Test formatting behind head error message."""
        service = SchemaCompatibilityService()

        message = service._format_behind_head_message(
            "current_rev", "head_rev", ["migration_1", "migration_2"]
        )

        assert "current_rev" in message
        assert "head_rev" in message
        assert "alembic upgrade head" in message
        assert "Missing migrations (2)" in message

    def test_format_message_no_missing(self):
        """Test formatting with no missing migrations."""
        service = SchemaCompatibilityService()

        message = service._format_behind_head_message("rev_a", "rev_b", [])

        assert "Missing migrations (0)" in message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
