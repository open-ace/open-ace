"""
Tests for image upload feature - image validator module

Tests for triple validation: extension, MIME type, and magic number.
"""

import pytest

from app.utils.image_validator import (
    calculate_checksum,
    detect_image_type_by_magic,
    sanitize_filename,
    triple_validate_image,
    validate_extension,
    validate_mime_type,
    validate_magic_number,
)


class TestDetectImageTypeByMagic:
    """Tests for magic number detection."""

    def test_detect_jpeg(self):
        """Test JPEG detection."""
        content = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        result = detect_image_type_by_magic(content)
        assert result == "jpeg"

    def test_detect_png(self):
        """Test PNG detection."""
        content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        result = detect_image_type_by_magic(content)
        assert result == "png"

    def test_detect_gif87a(self):
        """Test GIF87a detection."""
        content = b"GIF87a"
        result = detect_image_type_by_magic(content)
        assert result == "gif"

    def test_detect_gif89a(self):
        """Test GIF89a detection."""
        content = b"GIF89a"
        result = detect_image_type_by_magic(content)
        assert result == "gif"

    def test_detect_webp(self):
        """Test WEBP detection."""
        content = b"RIFF\x00\x00\x00\x00WEBPVP8 "
        result = detect_image_type_by_magic(content)
        assert result == "webp"

    def test_detect_bmp(self):
        """Test BMP detection."""
        content = b"BM"
        result = detect_image_type_by_magic(content)
        assert result == "bmp"

    def test_detect_unknown(self):
        """Test unknown format detection."""
        content = b"\x00\x01\x02\x03\x04\x05\x06\x07"
        result = detect_image_type_by_magic(content)
        assert result is None

    def test_detect_empty(self):
        """Test empty content."""
        content = b""
        result = detect_image_type_by_magic(content)
        assert result is None


class TestValidateExtension:
    """Tests for extension validation."""

    def test_valid_extension(self):
        """Test valid extension."""
        result = validate_extension("png", ["png", "jpg", "jpeg"])
        assert result is True

    def test_invalid_extension(self):
        """Test invalid extension."""
        result = validate_extension("txt", ["png", "jpg", "jpeg"])
        assert result is False

    def test_svg_allowed(self):
        """Test SVG when allowed."""
        result = validate_extension("svg", ["png", "jpg"], allow_svg=True)
        assert result is True

    def test_svg_not_allowed(self):
        """Test SVG when not allowed."""
        result = validate_extension("svg", ["png", "jpg"], allow_svg=False)
        assert result is False

    def test_extension_with_dot(self):
        """Test extension with leading dot."""
        result = validate_extension(".png", ["png", "jpg"])
        assert result is True


class TestValidateMimeType:
    """Tests for MIME type validation."""

    def test_valid_mime_png(self):
        """Test valid PNG MIME type."""
        result = validate_mime_type("image/png", "png", ["png"])
        assert result is True

    def test_valid_mime_jpeg(self):
        """Test valid JPEG MIME type."""
        result = validate_mime_type("image/jpeg", "jpg", ["jpg", "jpeg"])
        assert result is True

    def test_invalid_mime(self):
        """Test invalid MIME type."""
        result = validate_mime_type("text/plain", "png", ["png"])
        assert result is False

    def test_mime_mismatch(self):
        """Test MIME type mismatch."""
        result = validate_mime_type("image/png", "jpg", ["png", "jpg"])
        assert result is False


class TestTripleValidateImage:
    """Tests for triple validation."""

    def test_valid_png(self):
        """Test valid PNG file."""
        content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        is_valid, error = triple_validate_image(
            content, "test.png", "image/png", ["png", "jpg"]
        )
        assert is_valid is True
        assert error is None

    def test_valid_jpeg(self):
        """Test valid JPEG file."""
        content = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        is_valid, error = triple_validate_image(
            content, "test.jpg", "image/jpeg", ["png", "jpg", "jpeg"]
        )
        assert is_valid is True
        assert error is None

    def test_invalid_extension(self):
        """Test invalid extension."""
        content = b"\x89PNG\r\n\x1a\n"
        is_valid, error = triple_validate_image(
            content, "test.txt", "image/png", ["png", "jpg"]
        )
        assert is_valid is False
        assert "not allowed" in error

    def test_mime_mismatch(self):
        """Test MIME type mismatch."""
        content = b"\x89PNG\r\n\x1a\n"
        is_valid, error = triple_validate_image(
            content, "test.png", "image/jpeg", ["png", "jpg"]
        )
        assert is_valid is False
        assert "does not match" in error

    def test_magic_mismatch(self):
        """Test magic number mismatch."""
        content = b"\xff\xd8\xff"  # JPEG magic
        is_valid, error = triple_validate_image(
            content, "test.png", "image/png", ["png"]
        )
        assert is_valid is False
        assert "magic number mismatch" in error

    def test_svg_with_allowed(self):
        """Test SVG with allow_svg=True."""
        content = b"<svg xmlns='http://www.w3.org/2000/svg'>"
        is_valid, error = triple_validate_image(
            content, "test.svg", "image/svg+xml", ["png", "svg"], allow_svg=True
        )
        assert is_valid is True
        assert error is None

    def test_svg_without_allowed(self):
        """Test SVG with allow_svg=False."""
        content = b"<svg xmlns='http://www.w3.org/2000/svg'>"
        is_valid, error = triple_validate_image(
            content, "test.svg", "image/svg+xml", ["png", "svg"], allow_svg=False
        )
        assert is_valid is False
        assert "SVG files are not allowed" in error


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_normal_filename(self):
        """Test normal filename."""
        result = sanitize_filename("image.png")
        assert result == "image.png"

    def test_path_traversal(self):
        """Test path traversal characters."""
        result = sanitize_filename("../../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_special_characters(self):
        """Test special characters."""
        result = sanitize_filename("file<name>.png")
        assert "<" not in result
        assert ">" not in result

    def test_hidden_file(self):
        """Test hidden file."""
        result = sanitize_filename(".hidden")
        assert result.startswith("_")

    def test_long_filename(self):
        """Test long filename."""
        long_name = "a" * 300 + ".png"
        result = sanitize_filename(long_name)
        assert len(result) <= 255

    def test_empty_filename(self):
        """Test empty filename."""
        result = sanitize_filename("")
        assert result == "unnamed"


class TestCalculateChecksum:
    """Tests for checksum calculation."""

    def test_checksum_consistency(self):
        """Test checksum consistency."""
        content = b"test content"
        checksum1 = calculate_checksum(content)
        checksum2 = calculate_checksum(content)
        assert checksum1 == checksum2

    def test_checksum_different(self):
        """Test different checksums for different content."""
        checksum1 = calculate_checksum(b"content1")
        checksum2 = calculate_checksum(b"content2")
        assert checksum1 != checksum2

    def test_checksum_format(self):
        """Test checksum format (SHA256 hex)."""
        content = b"test"
        checksum = calculate_checksum(content)
        assert len(checksum) == 64  # SHA256 hex length
        assert all(c in "0123456789abcdef" for c in checksum)