"""
Open ACE - Image Service

Service for image upload, retrieval, and deletion operations.
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.uploaded_image import ImageUploadConfig, UploadedImage
from app.repositories.database import Database, adapt_boolean_condition, adapt_sql, is_postgresql
from app.services.storage_quota_service import get_storage_quota_service
from app.utils.image_validator import (
    calculate_checksum,
    get_image_dimensions,
    sanitize_filename,
    triple_validate_image,
)
from app.utils.path_validator import (
    ensure_directory_exists,
    expand_storage_path,
    get_safe_upload_path,
    is_path_blacklisted,
    is_valid_path,
)

logger = logging.getLogger(__name__)


class ImageService:
    """Service for image upload operations."""

    def __init__(self):
        self.db = Database()
        self.quota_service = get_storage_quota_service()

    def get_config(self) -> ImageUploadConfig:
        """
        Get image upload configuration from config file.

        Returns:
            ImageUploadConfig: Upload configuration.
        """
        try:
            import json

            config_path = os.path.expanduser("~/.open-ace/config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
                return ImageUploadConfig.from_config(config)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")

        return ImageUploadConfig()

    def upload_image(
        self,
        user_id: int,
        file_content: bytes,
        filename: str,
        mime_type: str,
        session_id: Optional[str] = None,
        project_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
    ) -> tuple[Optional[UploadedImage], Optional[str]]:
        """
        Upload and store an image file.

        Args:
            user_id: User ID.
            file_content: File content bytes.
            filename: Original filename.
            mime_type: MIME type from upload.
            session_id: Optional session ID for association.
            project_id: Optional project ID for association.
            tenant_id: Optional tenant ID.

        Returns:
            tuple: (uploaded_image, error_message)
        """
        config = self.get_config()

        # 1. Validate file size
        file_size = len(file_content)
        max_size = config.get_max_size_bytes()
        if file_size > max_size:
            return None, f"File size {file_size / (1024 * 1024):.1f}MB exceeds limit {config.max_size_mb}MB"

        if file_size == 0:
            return None, "File is empty"

        # 2. Triple validation (extension, MIME, magic number)
        is_valid, error = triple_validate_image(
            file_content[:32],  # First 32 bytes for magic number
            filename,
            mime_type,
            config.allowed_types,
            config.allow_svg,
        )
        if not is_valid:
            return None, error

        # 3. Check user quota
        quota_ok, quota_error = self.quota_service.check_quota_available(user_id, file_size)
        if not quota_ok:
            return None, quota_error

        # 4. Prepare storage path
        storage_base = expand_storage_path(config.storage_path)
        if is_path_blacklisted(storage_base):
            return None, f"Storage path '{storage_base}' is not allowed"

        upload_dir = get_safe_upload_path(storage_base, user_id, session_id)

        # 5. Generate stored filename (UUID)
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        stored_filename = f"{uuid.uuid4().hex}.{extension}"
        stored_path = os.path.join(upload_dir, stored_filename)

        # 6. Ensure directory exists
        if not ensure_directory_exists(upload_dir):
            return None, f"Failed to create upload directory"

        # 7. Validate final path
        if not is_valid_path(stored_path, allowed_prefixes=[storage_base]):
            return None, "Invalid storage path"

        # 8. Write file
        try:
            with open(stored_path, "wb") as f:
                f.write(file_content)
        except Exception as e:
            logger.error(f"Failed to write file: {e}")
            return None, f"Failed to save file: {str(e)}"

        # 9. Calculate checksum
        checksum = calculate_checksum(file_content)

        # 10. Get image dimensions (optional)
        width, height = None, None
        try:
            width, height = get_image_dimensions(stored_path)
        except Exception:
            pass  # Dimensions are optional

        # 11. Create database record
        is_svg = extension.lower() == "svg"
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=config.expire_hours)

        try:
            image_id = self._create_image_record(
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                project_id=project_id,
                filename=sanitize_filename(filename),
                stored_filename=stored_filename,
                stored_path=stored_path,
                file_size=file_size,
                mime_type=mime_type,
                checksum=checksum,
                width=width,
                height=height,
                expires_at=expires_at,
                is_svg=is_svg,
            )

            # 12. Update user storage used
            self.quota_service.update_storage_used(user_id, file_size)

            return UploadedImage(
                id=image_id,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                project_id=project_id,
                filename=sanitize_filename(filename),
                stored_filename=stored_filename,
                stored_path=stored_path,
                file_size=file_size,
                mime_type=mime_type,
                checksum=checksum,
                width=width,
                height=height,
                expires_at=expires_at,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                is_svg=is_svg,
            ), None

        except Exception as e:
            # Clean up file if database insert fails
            try:
                os.remove(stored_path)
            except Exception:
                pass
            logger.error(f"Failed to create image record: {e}")
            return None, f"Failed to create image record: {str(e)}"

    def _create_image_record(
        self,
        user_id: int,
        tenant_id: Optional[int],
        session_id: Optional[str],
        project_id: Optional[int],
        filename: str,
        stored_filename: str,
        stored_path: str,
        file_size: int,
        mime_type: str,
        checksum: str,
        width: Optional[int],
        height: Optional[int],
        expires_at: datetime,
        is_svg: bool,
    ) -> int:
        """Create database record for uploaded image."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Convert boolean to integer for SQLite
        is_svg_int = 1 if is_svg else 0 if not is_postgresql() else is_svg

        query = adapt_sql(
            """
            INSERT INTO uploaded_images (
                user_id, tenant_id, session_id, project_id,
                filename, stored_filename, stored_path,
                file_size, mime_type, checksum, width, height,
                expires_at, created_at, is_svg
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )

        self.db.execute(
            query,
            (
                user_id,
                tenant_id,
                session_id,
                project_id,
                filename,
                stored_filename,
                stored_path,
                file_size,
                mime_type,
                checksum,
                width,
                height,
                expires_at.isoformat(),
                now,
                is_svg_int,
            ),
        )

        # Get the inserted ID
        if is_postgresql():
            id_query = "SELECT id FROM uploaded_images WHERE stored_filename = ?"
        else:
            id_query = "SELECT last_insert_rowid() as id"

        if is_postgresql():
            row = self.db.fetch_one(id_query, (stored_filename,))
        else:
            row = self.db.fetch_one(id_query)

        return row["id"] if row else 0

    def get_image(self, image_id: int, user_id: int) -> Optional[UploadedImage]:
        """
        Get image by ID, checking ownership.

        Args:
            image_id: Image ID.
            user_id: User ID for ownership check.

        Returns:
            UploadedImage: Image data or None if not found/not owned.
        """
        try:
            query = adapt_sql(
                """
                SELECT * FROM uploaded_images WHERE id = ? AND user_id = ?
                """
            )
            row = self.db.fetch_one(query, (image_id, user_id))
            if row:
                return UploadedImage.from_dict(row)
            return None
        except Exception as e:
            logger.error(f"Failed to get image: {e}")
            return None

    def get_image_by_id(self, image_id: int) -> Optional[UploadedImage]:
        """
        Get image by ID without ownership check (for admin use).

        Args:
            image_id: Image ID.

        Returns:
            UploadedImage: Image data or None.
        """
        try:
            query = adapt_sql(
                """
                SELECT * FROM uploaded_images WHERE id = ?
                """
            )
            row = self.db.fetch_one(query, (image_id,))
            if row:
                return UploadedImage.from_dict(row)
            return None
        except Exception as e:
            logger.error(f"Failed to get image by id: {e}")
            return None

    def list_user_images(
        self,
        user_id: int,
        session_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[UploadedImage]:
        """
        List images for a user, optionally filtered by session.

        Args:
            user_id: User ID.
            session_id: Optional session ID filter.
            limit: Max results.
            offset: Result offset.

        Returns:
            list: List of UploadedImage objects.
        """
        try:
            if session_id:
                query = adapt_sql(
                    """
                    SELECT * FROM uploaded_images
                    WHERE user_id = ? AND session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """
                )
                rows = self.db.fetch_all(query, (user_id, session_id, limit, offset))
            else:
                query = adapt_sql(
                    """
                    SELECT * FROM uploaded_images
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """
                )
                rows = self.db.fetch_all(query, (user_id, limit, offset))

            return [UploadedImage.from_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to list images: {e}")
            return []

    def delete_image(self, image_id: int, user_id: int) -> tuple[bool, Optional[str]]:
        """
        Delete an image by ID, checking ownership.

        Args:
            image_id: Image ID.
            user_id: User ID for ownership check.

        Returns:
            tuple: (success, error_message)
        """
        try:
            # Get image to delete
            image = self.get_image(image_id, user_id)
            if not image:
                return False, "Image not found or not owned by user"

            # Delete file
            try:
                if os.path.exists(image.stored_path):
                    os.remove(image.stored_path)
            except Exception as e:
                logger.warning(f"Failed to delete file: {e}")

            # Delete database record
            query = adapt_sql(
                """
                DELETE FROM uploaded_images WHERE id = ? AND user_id = ?
                """
            )
            self.db.execute(query, (image_id, user_id))

            # Update user storage used
            self.quota_service.update_storage_used(user_id, -image.file_size)

            return True, None

        except Exception as e:
            logger.error(f"Failed to delete image: {e}")
            return False, f"Failed to delete image: {str(e)}"

    def get_expired_images(self) -> list[UploadedImage]:
        """
        Get all expired images for cleanup.

        Returns:
            list: List of expired UploadedImage objects.
        """
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            query = adapt_sql(
                """
                SELECT * FROM uploaded_images WHERE expires_at < ?
                """
            )
            rows = self.db.fetch_all(query, (now,))
            return [UploadedImage.from_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get expired images: {e}")
            return []

    def cleanup_expired_images(self) -> tuple[int, int]:
        """
        Clean up expired images.

        Returns:
            tuple: (deleted_count, failed_count)
        """
        expired = self.get_expired_images()
        deleted_count = 0
        failed_count = 0

        for image in expired:
            try:
                # Delete file
                if os.path.exists(image.stored_path):
                    os.remove(image.stored_path)

                # Delete database record
                query = adapt_sql(
                    """
                    DELETE FROM uploaded_images WHERE id = ?
                    """
                )
                self.db.execute(query, (image.id,))

                # Update user storage
                self.quota_service.update_storage_used(image.user_id, -image.file_size)

                deleted_count += 1
            except Exception as e:
                logger.warning(f"Failed to cleanup image {image.id}: {e}")
                failed_count += 1

        return deleted_count, failed_count


# Singleton instance
_image_service: Optional[ImageService] = None


def get_image_service() -> ImageService:
    """Get singleton ImageService instance."""
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
    return _image_service