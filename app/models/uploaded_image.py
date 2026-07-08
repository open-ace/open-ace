"""
Open ACE - Uploaded Image Models

Data models for image upload management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class UploadedImage:
    """Uploaded image data model."""

    id: Optional[int] = None
    user_id: int = 0
    tenant_id: Optional[int] = None
    session_id: Optional[str] = None
    project_id: Optional[int] = None
    filename: str = ""  # Original filename (sanitized)
    stored_filename: str = ""  # UUID.ext
    stored_path: str = ""  # Full server path
    file_size: int = 0  # Bytes
    mime_type: str = ""
    checksum: str = ""  # SHA256
    width: Optional[int] = None  # Image width in pixels
    height: Optional[int] = None  # Image height in pixels
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    is_svg: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "filename": self.filename,
            "stored_filename": self.stored_filename,
            "stored_path": self.stored_path,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_svg": self.is_svg,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UploadedImage":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            user_id=data.get("user_id", 0),
            tenant_id=data.get("tenant_id"),
            session_id=data.get("session_id"),
            project_id=data.get("project_id"),
            filename=data.get("filename", ""),
            stored_filename=data.get("stored_filename", ""),
            stored_path=data.get("stored_path", ""),
            file_size=data.get("file_size", 0),
            mime_type=data.get("mime_type", ""),
            checksum=data.get("checksum", ""),
            width=data.get("width"),
            height=data.get("height"),
            expires_at=(
                datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
            ),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            is_svg=data.get("is_svg", False),
        )

    def is_expired(self) -> bool:
        """Check if image has expired."""
        if not self.expires_at:
            return False
        return datetime.now() > self.expires_at

    def get_extension(self) -> str:
        """Get file extension from stored_filename."""
        if "." in self.stored_filename:
            return self.stored_filename.rsplit(".", 1)[-1].lower()
        return ""

    def get_preview_url(self) -> str:
        """Get preview URL for the image."""
        if self.id:
            return f"/api/images/serve/{self.id}"
        return ""


@dataclass
class ImageUploadConfig:
    """Image upload configuration."""

    max_size_mb: int = 10
    allowed_types: list[str] = field(default_factory=lambda: ["png", "jpg", "jpeg", "gif", "webp", "bmp"])
    allow_svg: bool = False
    svg_force_download: bool = True
    expire_hours: int = 24
    storage_path: str = "~/.open-ace/uploads"
    user_quota_mb: int = 100
    space_threshold_pct: int = 80
    max_concurrent_uploads: int = 5

    @classmethod
    def from_config(cls, config: dict) -> "ImageUploadConfig":
        """Create from config dict."""
        upload_config = config.get("upload", {})
        image_config = upload_config.get("image", {})
        return cls(
            max_size_mb=image_config.get("max_size_mb", 10),
            allowed_types=image_config.get(
                "allowed_types", ["png", "jpg", "jpeg", "gif", "webp", "bmp"]
            ),
            allow_svg=image_config.get("allow_svg", False),
            svg_force_download=image_config.get("svg_force_download", True),
            expire_hours=image_config.get("expire_hours", 24),
            storage_path=image_config.get("storage_path", "~/.open-ace/uploads"),
            user_quota_mb=image_config.get("user_quota_mb", 100),
            space_threshold_pct=image_config.get("space_threshold_pct", 80),
            max_concurrent_uploads=image_config.get("max_concurrent_uploads", 5),
        )

    def get_max_size_bytes(self) -> int:
        """Get max file size in bytes."""
        return self.max_size_mb * 1024 * 1024

    def get_user_quota_bytes(self) -> int:
        """Get user quota in bytes."""
        return self.user_quota_mb * 1024 * 1024

    def is_type_allowed(self, extension: str) -> bool:
        """Check if file extension is allowed."""
        ext = extension.lower().lstrip(".")
        if ext == "svg":
            return self.allow_svg
        return ext in self.allowed_types