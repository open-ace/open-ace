"""
Unit tests for project path encoding/decoding functions.

Issue #2136: Fix encodedProjectName encoding defect causing 403 errors
"""

import pytest

from app.routes.workspace import decode_project_name, encode_project_path


class TestEncodeProjectPath:
    """Tests for encode_project_path function."""

    def test_encode_simple_path(self):
        """Test encoding a simple Unix path."""
        result = encode_project_path("/home/user/demo-project")
        assert result.startswith("b64:")
        # Decode to verify
        import base64

        b64_data = result[4:]
        padding = (4 - (len(b64_data) % 4)) % 4
        decoded = base64.urlsafe_b64decode(b64_data + "=" * padding).decode("utf-8")
        assert decoded == "/home/user/demo-project"

    def test_encode_path_with_hyphen(self):
        """Test encoding path containing hyphens (the bug scenario)."""
        # This is the key test case - hyphen should NOT be converted to /
        result = encode_project_path("/home/user/demo-project")
        decoded = decode_project_name(result)
        assert decoded == "/home/user/demo-project"
        assert "-" in decoded  # Hyphen preserved

    def test_encode_path_with_multiple_hyphens(self):
        """Test encoding path with multiple hyphens."""
        path = "/home/user/my-demo-project-v2"
        result = encode_project_path(path)
        decoded = decode_project_name(result)
        assert decoded == path
        assert decoded.count("-") == 3  # All hyphens preserved

    def test_encode_windows_path(self):
        """Test encoding Windows path with drive letter."""
        # Windows path may be passed as /C:/Users/...
        result = encode_project_path("/C:/Users/demo/project")
        decoded = decode_project_name(result)
        # Should normalize to C:/Users/demo/project (without leading /)
        assert "C:/Users/demo/project" in decoded

    def test_encode_empty_path(self):
        """Test encoding empty string."""
        assert encode_project_path("") == ""

    def test_encode_none_path(self):
        """Test encoding None value."""
        assert encode_project_path(None) == ""

    def test_encode_path_with_special_chars(self):
        """Test encoding path with special characters."""
        path = "/home/user/project with spaces/子目录"
        result = encode_project_path(path)
        decoded = decode_project_name(result)
        assert decoded == path

    def test_encode_relative_path(self):
        """Test encoding relative path (should still work)."""
        path = "relative/path/to/project"
        result = encode_project_path(path)
        decoded = decode_project_name(result)
        assert decoded == path


class TestDecodeProjectName:
    """Tests for decode_project_name function."""

    def test_decode_new_format(self):
        """Test decoding new b64: format."""
        # First encode, then decode
        original = "/home/user/demo-project"
        encoded = encode_project_path(original)
        decoded = decode_project_name(encoded)
        assert decoded == original

    def test_decode_legacy_format(self):
        """Test decoding legacy format (backward compatibility)."""
        # Legacy format: -home-user-demo-project
        legacy = "-home-user-demo-project"
        decoded = decode_project_name(legacy)
        assert decoded == "/home/user/demo/project"

    def test_decode_legacy_format_simple(self):
        """Test decoding simple legacy format."""
        legacy = "-home-user-project"
        decoded = decode_project_name(legacy)
        assert decoded == "/home/user/project"

    def test_decode_empty_string(self):
        """Test decoding empty string."""
        assert decode_project_name("") == ""

    def test_decode_none_value(self):
        """Test decoding None value."""
        assert decode_project_name(None) == ""

    def test_decode_unencoded_string(self):
        """Test decoding unencoded string (should return as-is)."""
        unencoded = "some-random-string"
        decoded = decode_project_name(unencoded)
        assert decoded == unencoded

    def test_decode_invalid_base64(self):
        """Test decoding invalid base64 string."""
        invalid = "b64:!!!invalid!!!"
        decoded = decode_project_name(invalid)
        # Should return empty string or original on error
        assert decoded == ""

    def test_decode_preserves_hyphen(self):
        """Test that decoding preserves hyphens in project names."""
        original = "/home/user/my-awesome-project-v2"
        encoded = encode_project_path(original)
        decoded = decode_project_name(encoded)
        assert decoded == original
        assert "my-awesome-project-v2" in decoded


class TestEncodeDecodeRoundTrip:
    """Tests for encode/decode round-trip integrity."""

    @pytest.mark.parametrize(
        "path",
        [
            "/home/user/demo-project",
            "/home/user/my-demo-project",
            "/home/user/project-with-many-hyphens-v1",
            "/opt/apps/open-ace-02/open-ace",
            "/home/user/中文项目/子目录",
            "/home/user/project with spaces",
        ],
    )
    def test_roundtrip(self, path):
        """Test that encoding and decoding preserves the original path."""
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path, f"Round-trip failed for {path}"

    def test_legacy_format_can_be_re_encoded(self):
        """Test that legacy format can be decoded and re-encoded."""
        legacy = "-home-user-demo-project"
        decoded = decode_project_name(legacy)
        assert decoded == "/home/user/demo/project"
        # Re-encode with new format
        re_encoded = encode_project_path(decoded)
        re_decoded = decode_project_name(re_encoded)
        assert re_decoded == "/home/user/demo/project"


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_path_with_trailing_slash(self):
        """Test path with trailing slash."""
        path = "/home/user/project/"
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path

    def test_root_path(self):
        """Test encoding root path."""
        path = "/"
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path

    def test_very_long_path(self):
        """Test encoding very long path."""
        path = "/home/user/" + "/".join(["level"] * 20)
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path

    def test_path_with_numbers(self):
        """Test path containing numbers."""
        path = "/home/user/project-2024-v1"
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path

    def test_consecutive_hyphens_in_path(self):
        """Test path with consecutive hyphens."""
        path = "/home/user/my--project"
        encoded = encode_project_path(path)
        decoded = decode_project_name(encoded)
        assert decoded == path
        assert "--" in decoded  # Consecutive hyphens preserved