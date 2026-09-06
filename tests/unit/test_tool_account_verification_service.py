"""
Unit tests for Tool Account Verification Service

Issue #3273: Tests for tool account mapping verification functionality.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from app.models.user_tool_account import UserToolAccount, VerificationStatus
from app.services.tool_account_verification_service import (
    ToolAccountVerificationService,
    LocalToolVerifier,
    DiscoveredAccountVerifier,
    _is_container_environment,
    LOCAL_TOOL_TYPES,
    EXTERNAL_PLATFORM_TYPES,
    DISCOVERED_TYPES,
)


class TestVerificationStatus:
    """Tests for VerificationStatus enum."""

    def test_unverified_value(self):
        """Test UNVERIFIED enum value."""
        assert VerificationStatus.UNVERIFIED.value == "unverified"

    def test_verified_value(self):
        """Test VERIFIED enum value."""
        assert VerificationStatus.VERIFIED.value == "verified"

    def test_failed_value(self):
        """Test FAILED enum value."""
        assert VerificationStatus.FAILED.value == "failed"


class TestUserToolAccountVerificationFields:
    """Tests for UserToolAccount verification fields."""

    def test_create_with_verification_fields(self):
        """Test creating UserToolAccount with verification fields."""
        account = UserToolAccount(
            id=1,
            user_id=1,
            tool_account="test-qwen",
            tool_type="qwen",
            verification_status=VerificationStatus.VERIFIED.value,
            verification_result="验证通过",
            verified_at=datetime.now(),
        )

        assert account.verification_status == VerificationStatus.VERIFIED.value
        assert account.verification_result == "验证通过"
        assert account.verified_at is not None

    def test_to_dict_includes_verification_fields(self):
        """Test that to_dict includes verification fields."""
        verified_at = datetime(2026, 9, 6, 10, 0, 0)
        account = UserToolAccount(
            id=1,
            user_id=1,
            tool_account="test-qwen",
            tool_type="qwen",
            verification_status=VerificationStatus.VERIFIED.value,
            verification_result="验证通过",
            verified_at=verified_at,
        )

        data = account.to_dict()

        assert "verification_status" in data
        assert data["verification_status"] == VerificationStatus.VERIFIED.value
        assert "verification_result" in data
        assert data["verification_result"] == "验证通过"
        assert "verified_at" in data


class TestContainerEnvironmentDetection:
    """Tests for container environment detection."""

    def test_detect_docker_when_dockerenv_exists(self):
        """Test detection when /.dockerenv exists."""
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            assert _is_container_environment() is True

    def test_detect_kubernetes_when_env_var_set(self):
        """Test detection when KUBERNETES_SERVICE_HOST is set."""
        with patch.dict('os.environ', {'KUBERNETES_SERVICE_HOST': '10.0.0.1'}):
            with patch('os.path.exists') as mock_exists:
                mock_exists.return_value = False
                assert _is_container_environment() is True

    def test_not_container_when_neither_exists(self):
        """Test detection when neither Docker nor Kubernetes."""
        with patch.dict('os.environ', {}, clear=True):
            with patch('os.path.exists') as mock_exists:
                mock_exists.return_value = False
                assert _is_container_environment() is False


class TestLocalToolVerifier:
    """Tests for LocalToolVerifier."""

    def test_verify_passes_with_history_data(self):
        """Test verification passes when history data exists."""
        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 10}

        verifier = LocalToolVerifier(db=mock_db)
        result = verifier.verify(
            tool_account="test-qwen",
            user_id=1,
            context={"user_info": {"username": "testuser"}},
        )

        assert result["success"] is True
        assert result["checks"]["has_history_data"] is True
        # Message should indicate verification passed (in Chinese)
        assert "验证通过" in result["message"]

    def test_verify_fails_without_history_data(self):
        """Test verification fails when no history data exists."""
        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 0}

        verifier = LocalToolVerifier(db=mock_db)
        result = verifier.verify(
            tool_account="test-qwen",
            user_id=1,
            context={"user_info": {"username": "testuser"}},
        )

        assert result["success"] is False
        assert result["checks"]["has_history_data"] is False
        assert VerificationStatus.FAILED.value not in result["message"]

    def test_verify_in_container_skips_user_directory_check(self):
        """Test that user directory check is skipped in container."""
        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 10}

        verifier = LocalToolVerifier(db=mock_db)

        # Mock container environment
        with patch(
            'app.services.tool_account_verification_service._is_container_environment'
        ) as mock_container:
            mock_container.return_value = True
            result = verifier.verify(
                tool_account="test-qwen",
                user_id=1,
                context={"user_info": {"username": "testuser"}},
            )

        assert result["success"] is True
        assert "user_directory_exists" not in result["checks"]
        assert any("容器环境" in w for w in result.get("warnings", []))


class TestDiscoveredAccountVerifier:
    """Tests for DiscoveredAccountVerifier."""

    def test_verify_passes_with_history_data(self):
        """Test verification passes when history data exists."""
        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 5}

        verifier = DiscoveredAccountVerifier(db=mock_db)
        result = verifier.verify(
            tool_account="discovered-account",
            user_id=1,
            context={},
        )

        assert result["success"] is True
        assert result["checks"]["has_history_data"] is True

    def test_verify_fails_without_history_data(self):
        """Test verification fails when no history data exists."""
        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 0}

        verifier = DiscoveredAccountVerifier(db=mock_db)
        result = verifier.verify(
            tool_account="discovered-account",
            user_id=1,
            context={},
        )

        assert result["success"] is False
        assert result["checks"]["has_history_data"] is False


class TestToolAccountVerificationService:
    """Tests for ToolAccountVerificationService."""

    def test_verify_mapping_not_found(self):
        """Test verification when mapping not found."""
        # Mock repositories
        mock_mapping_repo = Mock()
        mock_mapping_repo.get_by_id.return_value = None

        service = ToolAccountVerificationService()
        service.mapping_repo = mock_mapping_repo

        result = service.verify_mapping(999, {})

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_verify_mapping_updates_verification_status(self):
        """Test that verification updates verification status in database."""
        # Create test mapping
        mapping = UserToolAccount(
            id=1,
            user_id=1,
            tool_account="test-qwen",
            tool_type="qwen",
            verification_status=VerificationStatus.UNVERIFIED.value,
        )

        # Mock repositories
        mock_mapping_repo = Mock()
        mock_mapping_repo.get_by_id.return_value = mapping
        mock_mapping_repo.update_verification_status.return_value = mapping

        mock_user_repo = Mock()
        mock_user_repo.get_user_by_id.return_value = {"username": "testuser"}

        # Mock database
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"count": 10}

        service = ToolAccountVerificationService()
        service.mapping_repo = mock_mapping_repo
        service.user_repo = mock_user_repo
        service.local_verifier = LocalToolVerifier(db=mock_db)

        result = service.verify_mapping(1, {})

        assert result["success"] is True
        assert result["verification_status"] == VerificationStatus.VERIFIED.value

        # Verify that update_verification_status was called
        mock_mapping_repo.update_verification_status.assert_called_once()

    def test_get_verifier_for_local_tools(self):
        """Test that correct verifier is selected for local tools."""
        service = ToolAccountVerificationService()

        for tool_type in LOCAL_TOOL_TYPES:
            verifier = service._get_verifier(tool_type)
            assert isinstance(verifier, LocalToolVerifier)

    def test_get_verifier_for_discovered_tools(self):
        """Test that correct verifier is selected for discovered tools."""
        service = ToolAccountVerificationService()

        for tool_type in DISCOVERED_TYPES:
            verifier = service._get_verifier(tool_type)
            assert isinstance(verifier, DiscoveredAccountVerifier)

    def test_get_verifier_for_external_platforms_falls_back(self):
        """Test that external platforms fall back to discovered verifier."""
        service = ToolAccountVerificationService()

        for tool_type in EXTERNAL_PLATFORM_TYPES:
            verifier = service._get_verifier(tool_type)
            # For P0, external platforms should fall back to discovered verifier
            assert isinstance(verifier, DiscoveredAccountVerifier)


class TestToolTypesClassification:
    """Tests for tool type classification."""

    def test_local_tool_types_include_qwen(self):
        """Test that Qwen is classified as local tool."""
        assert "qwen" in LOCAL_TOOL_TYPES

    def test_local_tool_types_include_claude(self):
        """Test that Claude is classified as local tool."""
        assert "claude" in LOCAL_TOOL_TYPES

    def test_external_platform_types_include_feishu(self):
        """Test that Feishu is classified as external platform."""
        assert "feishu" in EXTERNAL_PLATFORM_TYPES

    def test_external_platform_types_include_dingtalk(self):
        """Test that DingTalk is classified as external platform."""
        assert "dingtalk" in EXTERNAL_PLATFORM_TYPES

    def test_discovered_types_include_other(self):
        """Test that Other is classified as discovered type."""
        assert "other" in DISCOVERED_TYPES
