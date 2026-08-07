"""
Unit tests for Archive Backend Interface and LocalFile Implementation.

Issue #2329: Real archive implementation with verification.
"""

import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.archive_backend import (
    ArchiveBackend,
    ArchiveResult,
    LocalFileArchiveBackend,
    VerifyResult,
    get_archive_backend,
)


class TestLocalFileArchiveBackend:
    """Test LocalFileArchiveBackend implementation."""

    @pytest.fixture
    def temp_archive_dir(self):
        """Create temporary archive directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield temp_dir

    @pytest.fixture
    def backend(self, temp_archive_dir):
        """Create backend instance with temporary directory."""
        return LocalFileArchiveBackend(base_path=temp_archive_dir)

    @pytest.fixture
    def sample_records(self):
        """Sample records for testing."""
        return [
            {"id": 1, "username": "user1", "action": "login", "timestamp": "2026-01-01T10:00:00"},
            {"id": 2, "username": "user2", "action": "logout", "timestamp": "2026-01-01T11:00:00"},
            {"id": 3, "username": "user3", "action": "delete", "timestamp": "2026-01-01T12:00:00"},
        ]

    def test_init_creates_base_directory(self, temp_archive_dir):
        """Test that initialization creates base directory."""
        base_path = os.path.join(temp_archive_dir, "archives")
        backend = LocalFileArchiveBackend(base_path=base_path)
        assert os.path.exists(base_path)

    def test_write_creates_archive(self, backend, sample_records, temp_archive_dir):
        """Test successful archive creation."""
        result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result.success is True
        assert result.archive_id is not None
        assert result.record_count == 3
        assert result.checksum is not None
        assert result.error_message is None

        # Verify archive file exists
        location = backend.get_location(result.archive_id)
        assert location is not None
        assert os.path.exists(location)

    def test_write_creates_directory_structure(self, backend, sample_records, temp_archive_dir):
        """Test that write creates proper directory structure."""
        result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=123,
            data_type="sessions",
            execution_id="exec_456",
        )

        assert result.success is True

        # Check directory structure
        archive_dir = Path(temp_archive_dir) / "123" / "sessions" / "exec_456"
        assert archive_dir.exists()
        assert archive_dir.is_dir()

    def test_write_with_empty_records_fails(self, backend):
        """Test that writing empty records fails."""
        result = backend.write(
            batch_id=1,
            records=[],
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result.success is False
        assert "No records to archive" in result.error_message

    def test_write_creates_manifest(self, backend, sample_records):
        """Test that archive includes manifest file."""
        result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result.success is True

        # Extract and check manifest
        location = backend.get_location(result.archive_id)
        with tarfile.open(location, "r:gz") as tar:
            members = tar.getnames()
            assert any("manifest" in m for m in members)

            # Extract and read manifest
            manifest_file = [m for m in members if "manifest" in m][0]
            tar.extract(manifest_file)
            with open(manifest_file, 'r') as f:
                manifest = json.load(f)

            assert manifest["execution_id"] == "exec_123"
            assert manifest["tenant_id"] == 1
            assert manifest["data_type"] == "audit_logs"
            assert manifest["batch_id"] == 1
            assert manifest["record_count"] == 3
            assert "checksum_sha256" in manifest
            assert manifest["source_deleted"] is False

            # Clean up extracted file
            os.remove(manifest_file)

    def test_write_checksum_calculation(self, backend, sample_records):
        """Test that checksum is correctly calculated."""
        result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result.success is True
        assert result.checksum is not None
        # SHA-256 produces 64 hex characters
        assert len(result.checksum) == 64

    def test_verify_success(self, backend, sample_records):
        """Test successful archive verification."""
        # Write archive
        write_result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )
        assert write_result.success is True

        # Verify archive
        verify_result = backend.verify(write_result.archive_id)

        assert verify_result.success is True
        assert verify_result.archive_id == write_result.archive_id
        assert verify_result.record_count == 3
        assert verify_result.checksum_match is True
        assert verify_result.error_message is None

    def test_verify_detects_checksum_mismatch(self, backend, sample_records, temp_archive_dir):
        """Test that verification detects checksum mismatch."""
        # Write archive
        write_result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )
        assert write_result.success is True

        # Corrupt the archive (replace data with different data)
        location = backend.get_location(write_result.archive_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            # Extract archive
            with tarfile.open(location, "r:gz") as tar:
                tar.extractall(temp_dir)

            # Modify data
            data_file = Path(temp_dir) / "data.json"
            with open(data_file, 'w') as f:
                json.dump([{"id": 999, "modified": True}], f)

            # Re-pack archive
            temp_archive = Path(temp_dir) / "corrupted.tar.gz"
            with tarfile.open(temp_archive, "w:gz") as tar:
                tar.add(data_file, arcname="data.json")

            # Replace original archive
            import shutil
            shutil.move(str(temp_archive), location)

        # Verify should fail
        verify_result = backend.verify(write_result.archive_id)
        assert verify_result.success is False
        assert verify_result.checksum_match is False

    def test_verify_handles_missing_archive(self, backend):
        """Test that verification handles missing archive."""
        result = backend.verify("nonexistent_archive_id")
        assert result.success is False
        assert "not found" in result.error_message.lower() or "invalid" in result.error_message.lower()

    def test_verify_handles_invalid_archive_id(self, backend):
        """Test that verification handles invalid archive ID."""
        result = backend.verify("invalid_id")
        assert result.success is False
        assert result.error_message is not None

    def test_get_location_returns_path(self, backend, sample_records):
        """Test that get_location returns correct path."""
        write_result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        location = backend.get_location(write_result.archive_id)
        assert location is not None
        assert os.path.exists(location)
        assert location.endswith(".tar.gz")

    def test_get_location_returns_none_for_missing(self, backend):
        """Test that get_location returns None for missing archive."""
        location = backend.get_location("nonexistent_archive")
        assert location is None

    def test_check_capacity_returns_true_when_sufficient(self, backend):
        """Test capacity check with sufficient space."""
        # Small request should succeed
        result = backend.check_capacity(1024)
        assert result is True

    def test_check_capacity_returns_false_on_error(self, temp_archive_dir):
        """Test capacity check handles errors."""
        # Create backend with path that can't be created
        # Use a path that exists but will fail statvfs
        backend = LocalFileArchiveBackend(base_path=temp_archive_dir)
        # Mock os.statvfs to raise an exception
        with patch('os.statvfs', side_effect=OSError("Mocked error")):
            result = backend.check_capacity(1024)
            assert result is False

    def test_delete_archive_removes_files(self, backend, sample_records):
        """Test that delete_archive removes archive and manifest."""
        # Write archive
        write_result = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )
        assert write_result.success is True

        location = backend.get_location(write_result.archive_id)
        assert os.path.exists(location)

        # Delete archive
        delete_result = backend.delete_archive(write_result.archive_id)
        assert delete_result is True

        # Verify deleted
        location = backend.get_location(write_result.archive_id)
        assert location is None

    def test_delete_archive_handles_missing(self, backend):
        """Test delete handles missing archive."""
        result = backend.delete_archive("nonexistent_archive")
        assert result is False

    def test_atomic_write_rollback_on_failure(self, backend, sample_records, temp_archive_dir):
        """Test that atomic write rolls back on failure."""
        # Mock verification to fail
        with patch.object(backend, '_verify_archive_integrity', return_value=False):
            result = backend.write(
                batch_id=1,
                records=sample_records,
                tenant_id=1,
                data_type="audit_logs",
                execution_id="exec_123",
            )

        assert result.success is False
        assert "verification failed" in result.error_message.lower()

    def test_multiple_batches_same_execution(self, backend, sample_records):
        """Test multiple batches for same execution."""
        result1 = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        result2 = backend.write(
            batch_id=2,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result1.success is True
        assert result2.success is True
        assert result1.archive_id != result2.archive_id

        # Both should be in same execution directory
        loc1 = backend.get_location(result1.archive_id)
        loc2 = backend.get_location(result2.archive_id)
        assert Path(loc1).parent == Path(loc2).parent

    def test_tenant_isolation(self, backend, sample_records):
        """Test that archives are isolated by tenant."""
        result1 = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=1,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        result2 = backend.write(
            batch_id=1,
            records=sample_records,
            tenant_id=2,
            data_type="audit_logs",
            execution_id="exec_123",
        )

        assert result1.success is True
        assert result2.success is True

        loc1 = Path(backend.get_location(result1.archive_id))
        loc2 = Path(backend.get_location(result2.archive_id))

        # Should be in different tenant directories
        assert loc1.parts[-4] == "1"
        assert loc2.parts[-4] == "2"


class TestGetArchiveBackend:
    """Test factory function."""

    def test_get_local_file_backend(self):
        """Test getting local file backend."""
        with tempfile.TemporaryDirectory() as temp_dir:
            backend = get_archive_backend("local_file", base_path=temp_dir)
            assert isinstance(backend, LocalFileArchiveBackend)
            assert backend.base_path == Path(temp_dir)

    def test_unsupported_backend_raises(self):
        """Test unsupported backend type raises."""
        with pytest.raises(ValueError, match="Unsupported"):
            get_archive_backend("unsupported_type")


class TestArchiveResult:
    """Test ArchiveResult dataclass."""

    def test_success_result(self):
        """Test successful result creation."""
        result = ArchiveResult(
            success=True,
            archive_id="test_id",
            archive_path="/path/to/archive.tar.gz",
            record_count=100,
            checksum="abc123",
        )
        assert result.success is True
        assert result.error_message is None

    def test_failure_result(self):
        """Test failure result creation."""
        result = ArchiveResult(
            success=False,
            error_message="Test error",
            error_details={"key": "value"},
        )
        assert result.success is False
        assert result.archive_id is None


class TestVerifyResult:
    """Test VerifyResult dataclass."""

    def test_success_result(self):
        """Test successful verification result."""
        result = VerifyResult(
            success=True,
            archive_id="test_id",
            record_count=100,
            checksum_match=True,
        )
        assert result.success is True
        assert result.checksum_match is True

    def test_failure_result(self):
        """Test failed verification result."""
        result = VerifyResult(
            success=False,
            archive_id="test_id",
            error_message="Checksum mismatch",
        )
        assert result.success is False
        assert result.checksum_match is False