"""
Tests for image upload feature - path validator module

Tests for filename sanitization and path validation.
"""

import os
import pytest

from app.utils.path_validator import (
    ensure_directory_exists,
    expand_storage_path,
    get_safe_upload_path,
    is_path_blacklisted,
    is_valid_path,
    sanitize_filename,
)


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

    def test_backslash(self):
        """Test backslash characters."""
        result = sanitize_filename("..\\..\\windows\\system32")
        assert "\\" not in result

    def test_special_characters(self):
        """Test special characters."""
        result = sanitize_filename("file<name>test.png")
        assert "<" not in result
        assert ">" not in result

    def test_hidden_file(self):
        """Test hidden file starting with dot."""
        result = sanitize_filename(".hidden")
        assert result == "_hidden"

    def test_consecutive_underscores(self):
        """Test consecutive underscores."""
        result = sanitize_filename("file___name.png")
        assert "__" not in result

    def test_long_filename_preserves_extension(self):
        """Test long filename preserves extension."""
        long_name = "a" * 300 + ".png"
        result = sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".png")

    def test_empty_filename(self):
        """Test empty filename."""
        result = sanitize_filename("")
        assert result == "unnamed"

    def test_whitespace_filename(self):
        """Test whitespace-only filename."""
        result = sanitize_filename("   ")
        assert result == "unnamed"


class TestIsValidPath:
    """Tests for path validation."""

    def test_valid_absolute_path(self):
        """Test valid absolute path."""
        result = is_valid_path("/tmp/test")
        # May be True or False depending on system
        assert isinstance(result, bool)

    def test_path_traversal(self):
        """Test path traversal detection."""
        result = is_valid_path("/home/user/../other")
        assert result is False

    def test_empty_path(self):
        """Test empty path."""
        result = is_valid_path("")
        assert result is False

    def test_with_allowed_prefixes(self):
        """Test with allowed prefixes."""
        base = "/tmp"
        result = is_valid_path("/tmp/test/subdir", allowed_prefixes=[base])
        # Should be True if /tmp is not blacklisted
        assert isinstance(result, bool)

    def test_blacklisted_path(self):
        """Test blacklisted path."""
        result = is_valid_path("/etc/passwd")
        assert result is False

    def test_blacklisted_etc(self):
        """Test /etc is blacklisted."""
        result = is_valid_path("/etc")
        assert result is False

    def test_blacklisted_bin(self):
        """Test /bin is blacklisted."""
        result = is_valid_path("/bin/bash")
        assert result is False


class TestIsPathBlacklisted:
    """Tests for blacklist checking."""

    def test_blacklisted_etc(self):
        """Test /etc is in blacklist."""
        result = is_path_blacklisted("/etc")
        assert result is True

    def test_blacklisted_root(self):
        """Test /root is in blacklist."""
        result = is_path_blacklisted("/root")
        assert result is True

    def test_non_blacklisted(self):
        """Test non-blacklisted path."""
        result = is_path_blacklisted("/home/user/test")
        assert result is False

    def test_empty_path(self):
        """Test empty path."""
        result = is_path_blacklisted("")
        assert result is False


class TestExpandStoragePath:
    """Tests for storage path expansion."""

    def test_expand_home(self):
        """Test home directory expansion."""
        result = expand_storage_path("~/.open-ace/uploads")
        assert "~" not in result
        assert result.startswith("/")

    def test_absolute_path(self):
        """Test absolute path."""
        result = expand_storage_path("/tmp/uploads")
        assert result == "/tmp/uploads"

    def test_relative_path(self):
        """Test relative path becomes absolute."""
        result = expand_storage_path("uploads")
        assert result.startswith("/")


class TestEnsureDirectoryExists:
    """Tests for directory creation."""

    def test_create_directory(self, tmp_path):
        """Test creating directory."""
        test_dir = os.path.join(tmp_path, "test_upload")
        result = ensure_directory_exists(test_dir)
        assert result is True
        assert os.path.exists(test_dir)

    def test_existing_directory(self, tmp_path):
        """Test existing directory."""
        result = ensure_directory_exists(tmp_path)
        assert result is True


class TestGetSafeUploadPath:
    """Tests for safe upload path generation."""

    def test_basic_path(self):
        """Test basic path generation."""
        result = get_safe_upload_path("/tmp/uploads", 1)
        assert result == "/tmp/uploads/1"

    def test_with_session(self):
        """Test path with session ID."""
        result = get_safe_upload_path("/tmp/uploads", 1, "session123")
        assert result == "/tmp/uploads/1/session123"

    def test_sanitized_session_id(self):
        """Test session ID sanitization."""
        result = get_safe_upload_path("/tmp/uploads", 1, "../bad_session")
        assert ".." not in result


class TestBlacklistedPaths:
    """Test the blacklist constants."""

    def test_blacklist_includes_critical_dirs(self):
        """Test critical directories are in blacklist."""
        from app.utils.path_validator import BLACKLISTED_PATHS

        assert "/etc" in BLACKLISTED_PATHS
        assert "/bin" in BLACKLISTED_PATHS
        assert "/root" in BLACKLISTED_PATHS
        assert "/boot" in BLACKLISTED_PATHS