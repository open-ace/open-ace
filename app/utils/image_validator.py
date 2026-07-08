"""
Open ACE - Image Validator

Image validation utilities for file upload.
Provides triple validation: MIME type, extension, and magic number check.
"""

import hashlib
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Image format magic numbers (file signatures)
IMAGE_MAGIC_NUMBERS = {
    # JPEG: starts with FF D8 FF
    b"\xff\xd8\xff": "jpeg",
    # PNG: starts with 89 50 4E 47 0D 0A 1A 0A
    b"\x89PNG\r\n\x1a\n": "png",
    # GIF: starts with GIF87a or GIF89a
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    # WEBP: starts with RIFF....WEBP
    b"RIFF": "webp",  # Needs additional check for WEBP marker
    # BMP: starts with BM
    b"BM": "bmp",
}

# MIME type to extension mapping
MIME_TO_EXTENSION = {
    "image/jpeg": ["jpg", "jpeg"],
    "image/png": ["png"],
    "image/gif": ["gif"],
    "image/webp": ["webp"],
    "image/bmp": ["bmp"],
    "image/svg+xml": ["svg"],
}

# Extension to MIME type mapping
EXTENSION_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
}

# Maximum bytes to read for magic number check
MAGIC_NUMBER_MAX_BYTES = 32


class ImageValidationError(Exception):
    """Image validation error."""

    pass


def detect_image_type_by_magic(file_content: bytes) -> Optional[str]:
    """
    Detect image type by magic number (file signature).

    Args:
        file_content: First few bytes of the file content.

    Returns:
        str: Detected image type (jpeg, png, gif, webp, bmp) or None if not detected.
    """
    if len(file_content) < 8:
        return None

    # Check for JPEG
    if file_content[:3] == b"\xff\xd8\xff":
        return "jpeg"

    # Check for PNG
    if file_content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"

    # Check for GIF
    if file_content[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"

    # Check for WEBP (RIFF....WEBP)
    if file_content[:4] == b"RIFF" and len(file_content) >= 12:
        if file_content[8:12] == b"WEBP":
            return "webp"

    # Check for BMP
    if file_content[:2] == b"BM":
        return "bmp"

    return None


def validate_extension(extension: str, allowed_types: list[str], allow_svg: bool = False) -> bool:
    """
    Validate file extension against allowed types.

    Args:
        extension: File extension (without dot).
        allowed_types: List of allowed extensions.
        allow_svg: Whether SVG is allowed.

    Returns:
        bool: True if extension is valid.
    """
    ext = extension.lower().lstrip(".")

    if ext == "svg":
        return allow_svg

    return ext in allowed_types


def validate_mime_type(
    mime_type: str, extension: str, allowed_types: list[str], allow_svg: bool = False
) -> bool:
    """
    Validate MIME type matches extension and is allowed.

    Args:
        mime_type: MIME type from file upload.
        extension: File extension.
        allowed_types: List of allowed extensions.
        allow_svg: Whether SVG is allowed.

    Returns:
        bool: True if MIME type is valid and matches extension.
    """
    # Handle SVG specially
    if extension.lower().lstrip(".") == "svg":
        if not allow_svg:
            return False
        return mime_type == "image/svg+xml"

    # Check MIME type is recognized
    if mime_type not in MIME_TO_EXTENSION:
        return False

    # Check extension matches MIME type
    allowed_extensions = MIME_TO_EXTENSION.get(mime_type, [])
    return extension.lower().lstrip(".") in allowed_extensions


def validate_magic_number(file_content: bytes, extension: str, allow_svg: bool = False) -> bool:
    """
    Validate file magic number matches extension.

    Args:
        file_content: First bytes of file content.
        extension: File extension.
        allow_svg: Whether SVG is allowed.

    Returns:
        bool: True if magic number matches extension.
    """
    ext = extension.lower().lstrip(".")

    # SVG is XML, no magic number check
    if ext == "svg":
        if not allow_svg:
            return False
        # SVG files start with < or <?xml
        content_str = file_content[:100].decode("utf-8", errors="ignore").strip()
        return content_str.startswith("<") or content_str.startswith("<?xml")

    detected_type = detect_image_type_by_magic(file_content)
    if detected_type is None:
        return False

    # Check detected type matches extension
    return detected_type == ext


def calculate_checksum(file_content: bytes) -> str:
    """
    Calculate SHA256 checksum of file content.

    Args:
        file_content: Full file content.

    Returns:
        str: SHA256 checksum as hex string.
    """
    return hashlib.sha256(file_content).hexdigest()


def triple_validate_image(
    file_content: bytes,
    filename: str,
    mime_type: str,
    allowed_types: list[str],
    allow_svg: bool = False,
) -> tuple[bool, Optional[str]]:
    """
    Perform triple validation: extension, MIME type, and magic number.

    Args:
        file_content: First bytes of file content (for magic number check).
        filename: Original filename.
        mime_type: MIME type from upload.
        allowed_types: List of allowed extensions.
        allow_svg: Whether SVG is allowed.

    Returns:
        tuple: (is_valid, error_message)
    """
    # Extract extension
    extension = ""
    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()

    if not extension:
        return False, "File has no extension"

    # 1. Extension validation
    if not validate_extension(extension, allowed_types, allow_svg):
        if extension == "svg":
            return False, "SVG files are not allowed"
        return False, f"Extension '{extension}' is not allowed. Allowed: {', '.join(allowed_types)}"

    # 2. MIME type validation
    if not validate_mime_type(mime_type, extension, allowed_types, allow_svg):
        expected_mime = EXTENSION_TO_MIME.get(extension)
        return False, f"MIME type '{mime_type}' does not match extension '{extension}'. Expected: {expected_mime}"

    # 3. Magic number validation (for non-SVG)
    if extension != "svg":
        if not validate_magic_number(file_content, extension, allow_svg):
            return False, f"File content does not match extension '{extension}' (magic number mismatch)"

    return True, None


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename by removing dangerous characters.

    Args:
        filename: Original filename.

    Returns:
        str: Sanitized filename.
    """
    # Remove path separators and special characters
    filename = re.sub(r'[^\w\-_.]', '_', filename)

    # Prevent hidden files
    if filename.startswith('.'):
        filename = '_' + filename[1:]

    # Limit length
    if len(filename) > 255:
        # Keep extension
        if '.' in filename:
            ext = filename.rsplit('.', 1)[-1]
            base = filename[:255 - len(ext) - 1]
            filename = f"{base}.{ext}"
        else:
            filename = filename[:255]

    return filename


def get_image_dimensions(file_path: str) -> tuple[Optional[int], Optional[int]]:
    """
    Get image dimensions without loading full image.
    Uses basic file header parsing for common formats.

    Args:
        file_path: Path to image file.

    Returns:
        tuple: (width, height) or (None, None) if unable to determine.
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(24)

        # PNG dimensions
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            # PNG IHDR chunk is at offset 8-24
            width = int.from_bytes(header[16:20], 'big')
            height = int.from_bytes(header[20:24], 'big')
            return width, height

        # JPEG dimensions (more complex, need to find SOF marker)
        if header[:3] == b'\xff\xd8\xff':
            # Need to parse JPEG markers - simplified check
            # This is a basic implementation; full JPEG parsing is complex
            return None, None

        # GIF dimensions
        if header[:6] in (b'GIF87a', b'GIF89a'):
            width = int.from_bytes(header[6:8], 'little')
            height = int.from_bytes(header[8:10], 'little')
            return width, height

        # BMP dimensions
        if header[:2] == b'BM':
            # BMP header: width at offset 18, height at offset 22
            f.seek(18)
            width_bytes = f.read(4)
            height_bytes = f.read(4)
            width = int.from_bytes(width_bytes, 'little')
            height = int.from_bytes(height_bytes, 'little')
            return width, height

    except Exception as e:
        logger.warning(f"Failed to get image dimensions: {e}")

    return None, None