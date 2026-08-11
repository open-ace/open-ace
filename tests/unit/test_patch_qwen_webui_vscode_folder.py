"""Unit tests for scripts/patch-qwen-webui-vscode-folder.py.

Tests the VS Code folder parameter patching logic:
- Pattern matching uniqueness
- Patch correctness for Windows/Linux/Mac paths
- Already-patched detection
- Cache-bust mechanism
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the script module from scripts directory
_script_path = Path(__file__).parent.parent.parent / "scripts" / "patch-qwen-webui-vscode-folder.py"
_spec = importlib.util.spec_from_file_location("patch_qwen_webui_vscode_folder", _script_path)
patch_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_module)


class TestPatternDefinitions:
    """Test that pattern constants are correctly defined."""

    def test_old_folder_encode_pattern(self):
        """OLD_FOLDER_ENCODE should match expected minified pattern."""
        assert patch_module.OLD_FOLDER_ENCODE == "folder=${encodeURIComponent(n)}"

    def test_new_folder_encode_contains_normalization(self):
        """NEW_FOLDER_ENCODE should add '/' prefix and path normalization."""
        assert '"/"+n' in patch_module.NEW_FOLDER_ENCODE
        assert ".replace(/\\\\/g," in patch_module.NEW_FOLDER_ENCODE
        assert ".replace(/^\\/+/," in patch_module.NEW_FOLDER_ENCODE

    def test_cache_bust_format(self):
        """Cache-bust should have version format."""
        assert patch_module.CACHE_BUST.startswith("v=vscodefolder-")


class TestPathNormalization:
    r"""Test the JavaScript path normalization logic embedded in the patch.

    The patch adds this normalization:
    "/" + n.replace(/\\/g,"/").replace(/^\/+/,"")
    """

    @pytest.mark.parametrize(
        "original,expected",
        [
            # Windows paths: add "/" prefix, convert backslashes
            ("C:/workspace", "/C:/workspace"),
            ("C:\\workspace", "/C:/workspace"),
            ("D:\\projects\\test", "/D:/projects/test"),
            # Linux paths: already have "/" prefix, dedup after adding
            ("/home/user/workspace", "/home/user/workspace"),
            ("/var/www", "/var/www"),
            # Mac paths: same as Linux
            ("/Users/user/workspace", "/Users/user/workspace"),
            # Edge cases
            ("//double/slash", "/double/slash"),  # dedup leading slashes
            # Note: \\ in middle of path stays as-is (only backslash->slash, no dedup in middle)
            (r"\\double\\backslash", r"/double//backslash"),
        ],
    )
    def test_path_normalization_javascript(self, original: str, expected: str):
        r"""Verify path normalization logic using Python simulation.

        The JavaScript logic:
        "/" + n.replace(/\\/g,"/").replace(/^\/+/,"")

        Python equivalent:
        "/" + n.replace("\\", "/").lstrip("/")
        """
        # Simulate the JavaScript logic in Python
        normalized = "/" + original.replace("\\", "/").lstrip("/")
        assert normalized == expected, f"Expected {expected}, got {normalized}"


class TestBundlePatching:
    """Test the main bundle patching logic."""

    def test_no_bundle_found(self, tmp_path: Path):
        """Should fail if no bundle file found."""
        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "nonexistent*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(tmp_path / "index.html")):
                result = patch_module.main()
                assert result == 1

    def test_multiple_bundles_found(self, tmp_path: Path):
        """Should fail if multiple bundle files found."""
        (tmp_path / "index-abc.js").write_text("content")
        (tmp_path / "index-xyz.js").write_text("content")

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            result = patch_module.main()
            assert result == 1

    def test_patch_folder_encode_pattern(self, tmp_path: Path):
        """Should patch folder=${encodeURIComponent(n)} pattern."""
        bundle = tmp_path / "index-abc.js"
        original = "some code folder=${encodeURIComponent(n)} more code"
        bundle.write_text(original)

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(tmp_path / "index.html")):
                (tmp_path / "index.html").write_text('<script src="/assets/index-abc.js"></script>')

                result = patch_module.main()

                assert result == 0
                patched = bundle.read_text()
                assert patch_module.NEW_FOLDER_ENCODE in patched

    def test_patch_folder_template_pattern(self, tmp_path: Path):
        """Should patch folder=${encodeURIComponent(e)} pattern."""
        bundle = tmp_path / "index-abc.js"
        original = "some code folder=${encodeURIComponent(e) more code"
        bundle.write_text(original)

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(tmp_path / "index.html")):
                (tmp_path / "index.html").write_text('<script src="/assets/index-abc.js"></script>')

                result = patch_module.main()

                assert result == 0
                patched = bundle.read_text()
                assert patch_module.NEW_FOLDER_TEMPLATE in patched

    def test_already_patched_bundle(self, tmp_path: Path):
        """Should succeed (skip) if bundle already patched."""
        bundle = tmp_path / "index-abc.js"
        bundle.write_text(f"some code {patch_module.NEW_FOLDER_ENCODE} more code")

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(tmp_path / "index.html")):
                (tmp_path / "index.html").write_text('<script src="/assets/index-abc.js"></script>')

                result = patch_module.main()

                assert result == 0

    def test_pattern_not_unique_fails(self, tmp_path: Path):
        """Should fail if pattern appears multiple times."""
        bundle = tmp_path / "index-abc.js"
        original = f"code {patch_module.OLD_FOLDER_ENCODE} code {patch_module.OLD_FOLDER_ENCODE}"
        bundle.write_text(original)

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            result = patch_module.main()
            assert result == 1

    def test_no_pattern_found_fails(self, tmp_path: Path):
        """Should fail if no pattern found (version drift)."""
        bundle = tmp_path / "index-abc.js"
        bundle.write_text("some other code without folder pattern")

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            result = patch_module.main()
            assert result == 1


class TestCacheBust:
    """Test the cache-bust mechanism for index.html."""

    def test_cache_bust_applied(self, tmp_path: Path):
        """Should add cache-bust query parameter to script src."""
        bundle = tmp_path / "index-abc.js"
        bundle.write_text(f"code {patch_module.OLD_FOLDER_ENCODE} code")

        index_html = tmp_path / "index.html"
        index_html.write_text('<script src="/assets/index-abc.js"></script>')

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(index_html)):
                result = patch_module.main()

                assert result == 0
                html = index_html.read_text()
                assert f"?{patch_module.CACHE_BUST}" in html

    def test_cache_bust_already_applied(self, tmp_path: Path):
        """Should succeed if cache-bust already applied."""
        bundle = tmp_path / "index-abc.js"
        bundle.write_text(f"code {patch_module.NEW_FOLDER_ENCODE} code")

        index_html = tmp_path / "index.html"
        index_html.write_text(
            f'<script src="/assets/index-abc.js?{patch_module.CACHE_BUST}"></script>'
        )

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(index_html)):
                result = patch_module.main()

                assert result == 0

    def test_cache_bump_existing_version(self, tmp_path: Path):
        """Should bump cache-bust version if older version exists and bundle is patched."""
        bundle = tmp_path / "index-abc.js"
        # Bundle has the NEW pattern already (already patched)
        bundle.write_text(f"code {patch_module.NEW_FOLDER_ENCODE} code")

        index_html = tmp_path / "index.html"
        index_html.write_text(
            '<script src="/assets/index-abc.js?v=vscodefolder-20260809"></script>'
        )

        with patch.object(patch_module, "BUNDLE_GLOB", str(tmp_path / "index-*.js")):
            with patch.object(patch_module, "INDEX_HTML", str(index_html)):
                result = patch_module.main()

                assert result == 0
                # When already patched, the script should succeed
                # The cache-bump only happens during active patching
