"""
Open ACE - Archive Backend Interface

Abstract interface and implementations for archive storage backends.
Issue #2329: Real archive implementation with verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ArchiveResult:
    """Result of archive write operation."""

    success: bool
    archive_id: str | None = None
    archive_path: str | None = None
    record_count: int = 0
    checksum: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None


@dataclass
class VerifyResult:
    """Result of archive verification operation."""

    success: bool
    archive_id: str | None = None
    record_count: int = 0
    checksum_match: bool = False
    error_message: str | None = None


class ArchiveBackend(ABC):
    """Abstract base class for archive storage backends."""

    @abstractmethod
    def write(
        self,
        batch_id: int,
        records: list[dict[str, Any]],
        tenant_id: int,
        data_type: str,
        execution_id: str,
    ) -> ArchiveResult:
        """Write records to archive.

        Args:
            batch_id: Batch ID.
            records: List of records to archive.
            tenant_id: Tenant ID.
            data_type: Data type (e.g., 'audit_logs').
            execution_id: Execution ID.

        Returns:
            ArchiveResult with archive_id and verification details.
        """
        pass

    @abstractmethod
    def verify(self, archive_id: str) -> VerifyResult:
        """Verify archive integrity.

        Args:
            archive_id: Archive identifier.

        Returns:
            VerifyResult with verification details.
        """
        pass

    @abstractmethod
    def get_location(self, archive_id: str) -> str | None:
        """Get archive file location.

        Args:
            archive_id: Archive identifier.

        Returns:
            Archive file path or None.
        """
        pass

    @abstractmethod
    def check_capacity(self, required_bytes: int) -> bool:
        """Check if sufficient capacity available.

        Args:
            required_bytes: Required bytes.

        Returns:
            True if sufficient capacity, False otherwise.
        """
        pass

    @abstractmethod
    def delete_archive(self, archive_id: str) -> bool:
        """Delete an archive file.

        Args:
            archive_id: Archive identifier.

        Returns:
            True if deleted, False otherwise.
        """
        pass


class LocalFileArchiveBackend(ArchiveBackend):
    """Local filesystem archive backend implementation."""

    # Maximum record size for capacity planning (conservative estimate)
    MAX_RECORD_SIZE_BYTES = 2048  # 2KB per record

    def __init__(self, base_path: str = "/var/lib/openace/archives"):
        """Initialize local file archive backend.

        Args:
            base_path: Base directory for archives.
        """
        self.base_path = Path(base_path)
        self._ensure_base_directory()

    def _ensure_base_directory(self) -> None:
        """Ensure base archive directory exists."""
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Archive base directory ensured: {self.base_path}")
        except Exception as e:
            logger.error(f"Failed to create archive base directory: {e}")
            raise

    def write(
        self,
        batch_id: int,
        records: list[dict[str, Any]],
        tenant_id: int,
        data_type: str,
        execution_id: str,
    ) -> ArchiveResult:
        """Write records to local file archive.

        Implements atomic write pattern:
        1. Write to temporary file
        2. Verify integrity
        3. Rename to final location

        Args:
            batch_id: Batch ID.
            records: List of records to archive.
            tenant_id: Tenant ID.
            data_type: Data type.
            execution_id: Execution ID.

        Returns:
            ArchiveResult with archive details.
        """
        if not records:
            return ArchiveResult(
                success=False, error_message="No records to archive"
            )

        # Check capacity before write
        estimated_size = len(records) * self.MAX_RECORD_SIZE_BYTES * 2  # Safety factor
        if not self.check_capacity(estimated_size):
            return ArchiveResult(
                success=False,
                error_message=f"Insufficient disk space. Required: {estimated_size} bytes",
                error_details={"required_bytes": estimated_size}
            )

        # Create archive directory structure
        archive_dir = self.base_path / str(tenant_id) / data_type / execution_id
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ArchiveResult(
                success=False,
                error_message=f"Failed to create archive directory: {e}",
                error_details={"archive_dir": str(archive_dir)}
            )

        # Generate archive ID and filenames
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_filename = f"archive_{timestamp}_{batch_id}.tar.gz"
        manifest_filename = f"manifest_{timestamp}_{batch_id}.json"

        # Archive ID is the relative path from base_path
        # This makes it easy to reconstruct the full path later
        archive_id = f"{tenant_id}/{data_type}/{execution_id}/{archive_filename}"

        # Write to temporary location first (atomic write pattern)
        temp_dir = Path(tempfile.mkdtemp(prefix="archive_temp_"))
        try:
            # Write data file
            data_file = temp_dir / "data.json"
            with open(data_file, 'w') as f:
                json.dump(records, f, default=str)

            # Calculate checksum before compression
            checksum = self._calculate_checksum(data_file)

            # Create manifest
            manifest = {
                "execution_id": execution_id,
                "tenant_id": tenant_id,
                "data_type": data_type,
                "batch_id": batch_id,
                "record_count": len(records),
                "checksum_sha256": checksum,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "archive_file": archive_filename,
                "source_deleted": False,
            }

            manifest_file = temp_dir / manifest_filename
            with open(manifest_file, 'w') as f:
                json.dump(manifest, f, indent=2)

            # Create compressed archive with both data and manifest
            temp_archive = temp_dir / archive_filename
            with tarfile.open(temp_archive, "w:gz") as tar:
                tar.add(data_file, arcname="data.json")
                tar.add(manifest_file, arcname=manifest_filename)

            # Verify archive before moving
            if not self._verify_archive_integrity(temp_archive, len(records), checksum):
                return ArchiveResult(
                    success=False,
                    error_message="Archive verification failed before move",
                    error_details={"archive_id": archive_id}
                )

            # Atomic move to final location
            final_archive = archive_dir / archive_filename
            shutil.move(str(temp_archive), str(final_archive))

            # Move manifest
            final_manifest = archive_dir / manifest_filename
            shutil.move(str(manifest_file), str(final_manifest))

            logger.info(
                f"Archive created successfully: {archive_id} "
                f"({len(records)} records, checksum: {checksum[:16]}...)"
            )

            return ArchiveResult(
                success=True,
                archive_id=archive_id,
                archive_path=str(final_archive),
                record_count=len(records),
                checksum=checksum,
            )

        except Exception as e:
            logger.error(f"Failed to create archive: {e}")
            return ArchiveResult(
                success=False,
                error_message=f"Archive write failed: {e}",
                error_details={"archive_id": archive_id, "error": str(e)}
            )
        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    def verify(self, archive_id: str) -> VerifyResult:
        """Verify archive integrity.

        Checks:
        1. Archive file exists
        2. Manifest file exists
        3. Record count matches manifest
        4. Checksum matches manifest

        Args:
            archive_id: Archive identifier (relative path from base_path).

        Returns:
            VerifyResult with verification details.
        """
        try:
            # Get archive location
            archive_path = self.get_location(archive_id)
            if not archive_path:
                return VerifyResult(
                    success=False,
                    archive_id=archive_id,
                    error_message="Archive file not found"
                )

            archive_file = Path(archive_path)
            if not archive_file.exists():
                return VerifyResult(
                    success=False,
                    archive_id=archive_id,
                    error_message="Archive file not found"
                )

            # Extract and verify manifest
            with tarfile.open(archive_file, "r:gz") as tar:
                try:
                    # Find manifest file in archive
                    manifest_members = [m for m in tar.getmembers() if "manifest" in m.name]
                    if not manifest_members:
                        return VerifyResult(
                            success=False,
                            archive_id=archive_id,
                            error_message="Manifest not found in archive"
                        )

                    manifest_member = manifest_members[0]
                    manifest_fh = tar.extractfile(manifest_member)
                    if not manifest_fh:
                        return VerifyResult(
                            success=False,
                            archive_id=archive_id,
                            error_message="Cannot read manifest from archive"
                        )
                    manifest = json.load(manifest_fh)
                except Exception as e:
                    return VerifyResult(
                        success=False,
                        archive_id=archive_id,
                        error_message=f"Failed to read manifest: {e}"
                    )

            record_count = manifest.get("record_count", 0)
            expected_checksum = manifest.get("checksum_sha256", "")

            # Extract and verify data
            with tempfile.TemporaryDirectory() as temp_dir:
                with tarfile.open(archive_file, "r:gz") as tar:
                    tar.extractall(temp_dir)

                data_file = Path(temp_dir) / "data.json"
                if not data_file.exists():
                    return VerifyResult(
                        success=False,
                        archive_id=archive_id,
                        error_message="Data file not found in archive"
                    )

                # Calculate and verify checksum
                actual_checksum = self._calculate_checksum(data_file)
                checksum_match = actual_checksum == expected_checksum

                # Verify record count
                with open(data_file, 'r') as f:
                    records = json.load(f)
                    actual_count = len(records)

                if actual_count != record_count:
                    logger.warning(
                        f"Record count mismatch: expected {record_count}, got {actual_count}"
                    )

                success = checksum_match and actual_count == record_count

                return VerifyResult(
                    success=success,
                    archive_id=archive_id,
                    record_count=actual_count,
                    checksum_match=checksum_match,
                    error_message=None if success else "Verification failed"
                )

        except Exception as e:
            logger.error(f"Archive verification failed: {e}")
            return VerifyResult(
                success=False,
                archive_id=archive_id,
                error_message=f"Verification error: {e}"
            )

    def get_location(self, archive_id: str) -> str | None:
        """Get archive file location.

        Args:
            archive_id: Archive identifier (relative path from base_path).

        Returns:
            Archive file path or None.
        """
        try:
            # archive_id is the relative path from base_path
            archive_path = self.base_path / archive_id
            return str(archive_path) if archive_path.exists() else None
        except Exception:
            return None

    def check_capacity(self, required_bytes: int) -> bool:
        """Check if sufficient disk space available.

        Args:
            required_bytes: Required bytes.

        Returns:
            True if sufficient capacity, False otherwise.
        """
        try:
            stat = os.statvfs(self.base_path)
            available_bytes = stat.f_bavail * stat.f_frsize
            return available_bytes >= required_bytes
        except Exception as e:
            logger.error(f"Failed to check disk capacity: {e}")
            return False

    def delete_archive(self, archive_id: str) -> bool:
        """Delete an archive file and its manifest.

        Args:
            archive_id: Archive identifier (relative path from base_path).

        Returns:
            True if deleted, False otherwise.
        """
        try:
            location = self.get_location(archive_id)
            if not location:
                return False

            archive_file = Path(location)
            # Manifest is in the same directory with manifest_ prefix
            manifest_file = archive_file.parent / archive_file.name.replace(
                "archive_", "manifest_"
            ).replace(".tar.gz", ".json")

            # Delete archive file
            archive_file.unlink()

            # Delete manifest file
            if manifest_file.exists():
                manifest_file.unlink()

            logger.info(f"Archive deleted: {archive_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete archive {archive_id}: {e}")
            return False

    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA-256 checksum of a file.

        Args:
            file_path: Path to file.

        Returns:
            Hexadecimal checksum string.
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _verify_archive_integrity(
        self, archive_path: Path, expected_count: int, expected_checksum: str
    ) -> bool:
        """Verify archive integrity before final move.

        Args:
            archive_path: Path to archive file.
            expected_count: Expected record count.
            expected_checksum: Expected checksum.

        Returns:
            True if verification passes, False otherwise.
        """
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                with tarfile.open(archive_path, "r:gz") as tar:
                    tar.extractall(temp_dir)

                data_file = Path(temp_dir) / "data.json"
                if not data_file.exists():
                    return False

                # Verify checksum
                actual_checksum = self._calculate_checksum(data_file)
                if actual_checksum != expected_checksum:
                    logger.error("Checksum mismatch in pre-move verification")
                    return False

                # Verify record count
                with open(data_file, 'r') as f:
                    records = json.load(f)
                    if len(records) != expected_count:
                        logger.error(
                            f"Record count mismatch: expected {expected_count}, "
                            f"got {len(records)}"
                        )
                        return False

                return True

        except Exception as e:
            logger.error(f"Archive integrity verification failed: {e}")
            return False


# Factory function for getting archive backend
def get_archive_backend(backend_type: str = "local_file", **kwargs) -> ArchiveBackend:
    """Get archive backend instance.

    Args:
        backend_type: Backend type ('local_file').
        **kwargs: Backend-specific configuration.

    Returns:
        ArchiveBackend instance.

    Raises:
        ValueError: If backend type not supported.
    """
    if backend_type == "local_file":
        return LocalFileArchiveBackend(**kwargs)
    else:
        raise ValueError(f"Unsupported archive backend type: {backend_type}")