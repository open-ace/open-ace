"""
Unit tests for frontend_build_check module.

Tests the frontend build integrity check functionality.
Issue #3277: Prevent "Open ACE could not render" errors due to missing build artifacts.
Issue #3394: The entry JS check derives the expected filename from the Vite
manifest (index.html -> file) instead of a hardcoded main.*.js glob - fixtures
here mirror the real vite build shape (manifest.json + index.<hash>.js, no
main.*.js).
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.frontend_check import (
    CheckStatus,
    ErrorLevel,
    check_entry_js,
    check_frontend_build_integrity,
    check_frontend_build_on_startup,
    check_index_html,
    check_manifest,
    format_error_message,
    get_dist_dir,
    get_frontend_build_status,
    load_vite_manifest,
)

# Hashed filenames mimicking a real `npm run build` (vite entry chunk is named
# after its input, index.html, per frontend/vite.config.ts entryFileNames).
REAL_ENTRY_JS = "index.COLWQw-4.js"
REAL_ENTRY_CSS = "index.DOmhtBxr.css"


def write_real_manifest(dist_dir: Path, entry_file: str = REAL_ENTRY_JS) -> Path:
    """Write a .vite/manifest.json in the real build shape (index.html entry)."""
    vite_dir = dist_dir / ".vite"
    vite_dir.mkdir(exist_ok=True)
    manifest_path = vite_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {
                    "file": entry_file,
                    "css": [REAL_ENTRY_CSS],
                    "src": "index.html",
                },
                "src/components/SecurityCenter.tsx": {
                    "file": "components.BxKvNp7l.js",
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


@pytest.fixture
def temp_dist_dir(tmp_path: Path) -> Path:
    """Create a temporary dist directory for testing."""
    dist_dir = tmp_path / "static" / "js" / "dist"
    dist_dir.mkdir(parents=True)
    return dist_dir


@pytest.fixture
def real_shape_dist(temp_dist_dir: Path) -> Path:
    """A dist directory shaped like a real vite production build.

    Contains index.html, .vite/manifest.json declaring the hashed entry JS,
    and the hashed entry artifacts on disk - and NO main.*.js (the real build
    never produces one; Issue #3394).
    """
    (temp_dist_dir / "index.html").write_text(
        "<!DOCTYPE html><html><body></body></html>", encoding="utf-8"
    )
    write_real_manifest(temp_dist_dir)
    (temp_dist_dir / REAL_ENTRY_JS).write_text("console.log('entry')", encoding="utf-8")
    (temp_dist_dir / REAL_ENTRY_CSS).write_text("body{}", encoding="utf-8")
    return temp_dist_dir


class TestGetDistDir:
    """Tests for get_dist_dir function."""

    def test_returns_path_object(self) -> None:
        """Should return a Path object."""
        result = get_dist_dir()
        assert isinstance(result, Path)

    def test_points_to_correct_location(self) -> None:
        """Should point to static/js/dist."""
        result = get_dist_dir()
        assert result.name == "dist"
        assert result.parent.name == "js"
        assert result.parent.parent.name == "static"


class TestCheckIndexHtml:
    """Tests for check_index_html function."""

    def test_missing_directory(self, tmp_path: Path) -> None:
        """Should return MISSING when dist directory doesn't exist."""
        non_existent_dir = tmp_path / "nonexistent"
        result = check_index_html(non_existent_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR
        assert "directory does not exist" in result.message

    def test_missing_file(self, temp_dist_dir: Path) -> None:
        """Should return MISSING when index.html doesn't exist."""
        result = check_index_html(temp_dist_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR
        assert "not found" in result.message

    def test_valid_file(self, temp_dist_dir: Path) -> None:
        """Should return OK for valid index.html."""
        index_path = temp_dist_dir / "index.html"
        index_path.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")

        result = check_index_html(temp_dist_dir)

        assert result.status == CheckStatus.OK
        assert result.error_level is None

    def test_invalid_html(self, temp_dist_dir: Path) -> None:
        """Should return INVALID for non-HTML file."""
        index_path = temp_dist_dir / "index.html"
        index_path.write_text("not html", encoding="utf-8")

        result = check_index_html(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR


class TestCheckManifest:
    """Tests for check_manifest function."""

    def test_missing_file(self, temp_dist_dir: Path) -> None:
        """Should return MISSING when manifest.json doesn't exist."""
        result = check_manifest(temp_dist_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR

    def test_valid_manifest(self, temp_dist_dir: Path) -> None:
        """Should return OK for valid manifest.json (real build shape)."""
        write_real_manifest(temp_dist_dir)

        result = check_manifest(temp_dist_dir)

        assert result.status == CheckStatus.OK
        assert "2 entries" in result.message

    def test_invalid_json(self, temp_dist_dir: Path) -> None:
        """Should return INVALID for corrupt JSON."""
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        manifest_path = vite_dir / "manifest.json"
        manifest_path.write_text("not valid json", encoding="utf-8")

        result = check_manifest(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR

    def test_empty_manifest(self, temp_dist_dir: Path) -> None:
        """Should return INVALID for empty manifest."""
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        manifest_path = vite_dir / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")

        result = check_manifest(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR


class TestCheckEntryJs:
    """Tests for check_entry_js function (Issue #3394)."""

    def test_missing_directory(self, tmp_path: Path) -> None:
        """Should return MISSING when dist directory doesn't exist."""
        non_existent_dir = tmp_path / "nonexistent"
        result = check_entry_js(non_existent_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR

    def test_real_build_shape_passes(self, temp_dist_dir: Path) -> None:
        """P0 regression case (Issue #3394): manifest-declared index.<hash>.js
        present and non-empty, with NO main.*.js anywhere, must PASS.

        This exact shape is what `npm run build` produces (verified against
        the Dockerfile frontend-builder stage) and what the old main.*.js
        glob rejected, crash-looping production boots.
        """
        write_real_manifest(temp_dist_dir)
        (temp_dist_dir / REAL_ENTRY_JS).write_text("console.log('entry')", encoding="utf-8")

        # Sanity: the fixture really contains no legacy main.*.js.
        assert list(temp_dist_dir.glob("main.*.js")) == []

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.OK
        assert result.error_level is None
        assert REAL_ENTRY_JS in result.message

    def test_manifest_entry_missing_on_disk(self, temp_dist_dir: Path) -> None:
        """Manifest declares the entry but the file is absent -> FAIL."""
        write_real_manifest(temp_dist_dir)
        # REAL_ENTRY_JS deliberately NOT written

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR
        assert REAL_ENTRY_JS in result.message

    def test_manifest_entry_empty_file(self, temp_dist_dir: Path) -> None:
        """Manifest entry file exists but is 0 bytes -> FAIL."""
        write_real_manifest(temp_dist_dir)
        (temp_dist_dir / REAL_ENTRY_JS).write_text("", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR
        assert REAL_ENTRY_JS in result.message

    def test_manifest_without_index_html_entry(self, temp_dist_dir: Path) -> None:
        """Manifest exists but declares no index.html entry -> INVALID."""
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        (vite_dir / "manifest.json").write_text(
            json.dumps({"src/components/SecurityCenter.tsx": {"file": "components.abc.js"}}),
            encoding="utf-8",
        )
        (temp_dist_dir / REAL_ENTRY_JS).write_text("console.log('entry')", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR

    def test_manifest_entry_escaping_dist_dir(self, temp_dist_dir: Path) -> None:
        """Manifest entry file pointing outside dist (traversal/absolute) is rejected."""
        write_real_manifest(temp_dist_dir, entry_file="../evil.js")
        (temp_dist_dir.parent / "evil.js").write_text("boom", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR

    def test_unreadable_manifest_with_legacy_main_js(self, temp_dist_dir: Path) -> None:
        """Corrupt manifest + non-empty legacy main.<hash>.js -> PASS (legacy tolerance).

        #2879's emptyOutDir: false means deployments can carry mixed
        old/new artifacts; an unreadable manifest must not brick the boot.
        """
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        (vite_dir / "manifest.json").write_text("not valid json", encoding="utf-8")
        (temp_dist_dir / "main.abc123.js").write_text("console.log('legacy')", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.OK
        assert "legacy" in result.message.lower()

    def test_no_manifest_legacy_main_js_passes(self, temp_dist_dir: Path) -> None:
        """No manifest at all + non-empty legacy main.<hash>.js -> PASS."""
        (temp_dist_dir / "main.abc123.js").write_text("console.log('legacy')", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.OK
        assert "legacy" in result.message.lower()

    def test_no_manifest_no_main_js(self, temp_dist_dir: Path) -> None:
        """No manifest and no main.*.js -> MISSING."""
        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR

    def test_no_manifest_empty_main_js(self, temp_dist_dir: Path) -> None:
        """No manifest, only a 0-byte main.<hash>.js -> INVALID."""
        (temp_dist_dir / "main.abc123.js").write_text("", encoding="utf-8")

        result = check_entry_js(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR


class TestLoadViteManifest:
    """Tests for load_vite_manifest helper."""

    def test_missing_manifest_returns_none(self, temp_dist_dir: Path) -> None:
        assert load_vite_manifest(temp_dist_dir) is None

    def test_invalid_json_returns_none(self, temp_dist_dir: Path) -> None:
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        (vite_dir / "manifest.json").write_text("not json", encoding="utf-8")

        assert load_vite_manifest(temp_dist_dir) is None

    def test_non_object_json_returns_none(self, temp_dist_dir: Path) -> None:
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        (vite_dir / "manifest.json").write_text('["not", "an", "object"]', encoding="utf-8")

        assert load_vite_manifest(temp_dist_dir) is None

    def test_valid_manifest_returns_dict(self, temp_dist_dir: Path) -> None:
        write_real_manifest(temp_dist_dir)

        manifest = load_vite_manifest(temp_dist_dir)

        assert isinstance(manifest, dict)
        assert manifest["index.html"]["file"] == REAL_ENTRY_JS


class TestCheckFrontendBuildIntegrity:
    """Tests for check_frontend_build_integrity function."""

    def test_skip_check(self) -> None:
        """Should skip check when skip_check=True."""
        result = check_frontend_build_integrity(skip_check=True)

        assert result.success is True
        assert len(result.warnings) > 0
        assert "skipped" in result.warnings[0].lower()

    def test_missing_all_artifacts(self, tmp_path: Path) -> None:
        """Should fail when all artifacts are missing."""
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = tmp_path / "nonexistent"

            result = check_frontend_build_integrity(skip_check=False)

            assert result.success is False
            assert len(result.errors) > 0

    def test_complete_build_real_shape(self, real_shape_dist: Path) -> None:
        """Should pass for a real vite build shape (manifest + index.<hash>.js, no main.*.js)."""
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = real_shape_dist

            result = check_frontend_build_integrity(skip_check=False)

            assert result.success is True
            assert len(result.errors) == 0
            assert [c.name for c in result.checks] == [
                "index.html",
                "manifest.json",
                "entry.js",
            ]


class TestCheckFrontendBuildOnStartup:
    """Tests for check_frontend_build_on_startup function."""

    def test_skip_via_env_var(self) -> None:
        """Should skip check when OPENACE_SKIP_FRONTEND_CHECK=1."""
        # Should not raise
        check_frontend_build_on_startup(flask_env="production", skip_env_var="1")
        # Verify no exception was raised
        assert True

    def test_development_mode(self) -> None:
        """Should not raise in development mode even if build is missing."""
        # Should not raise
        check_frontend_build_on_startup(flask_env="development", skip_env_var="")
        # Verify no exception was raised
        assert True

    def test_testing_mode(self) -> None:
        """Should not raise in testing mode even if build is missing."""
        # Should not raise
        check_frontend_build_on_startup(flask_env="testing", skip_env_var="")
        # Verify no exception was raised
        assert True

    def test_production_mode_missing_build(self) -> None:
        """Should raise RuntimeError in production mode if build is missing."""
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            # Return non-existent directory
            mock_get_dist.return_value = Path("/nonexistent/path")

            with pytest.raises(RuntimeError) as exc_info:
                check_frontend_build_on_startup(flask_env="production", skip_env_var="")

            assert "Frontend build artifacts missing" in str(exc_info.value)

    def test_production_mode_real_build_boots(self, real_shape_dist: Path) -> None:
        """P0 regression guard (Issue #3394): a real vite build shape (no
        main.*.js) must NOT halt a production boot."""
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = real_shape_dist

            # Must not raise
            check_frontend_build_on_startup(flask_env="production", skip_env_var="")

    def test_development_mode_missing_build_only_warns(self) -> None:
        """Dev keeps warn-only semantics: a missing build must not raise."""
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = Path("/nonexistent/path")

            # Must not raise
            check_frontend_build_on_startup(flask_env="development", skip_env_var="")


class TestFormatErrorMessage:
    """Tests for format_error_message function."""

    def test_format_with_errors(self) -> None:
        """Should format error message with all check results."""
        from app.utils.frontend_check import CheckResult, FrontendBuildCheckResult

        result = FrontendBuildCheckResult(success=False)
        result.checks = [
            CheckResult(
                name="index.html",
                status=CheckStatus.MISSING,
                message="not found",
                error_level=ErrorLevel.ERROR,
            ),
        ]
        result.add_error("index.html: not found")

        message = format_error_message(result)

        assert "ERROR: Frontend build artifacts missing" in message
        assert "index.html: MISSING" in message
        assert "npm run build" in message


class TestGetFrontendBuildStatus:
    """Tests for get_frontend_build_status function."""

    def test_returns_dict(self) -> None:
        """Should return a dictionary."""
        result = get_frontend_build_status()

        assert isinstance(result, dict)
        assert "status" in result
        assert "checks" in result

    def test_status_values(self) -> None:
        """Should have valid status values."""
        result = get_frontend_build_status()

        assert result["status"] in ("ok", "missing")
        assert isinstance(result["checks"], dict)

    def test_check_keys_real_shape(self, real_shape_dist: Path) -> None:
        """Health endpoint keys should reflect the real checks (Issue #3394).

        The old lie ("main.js" derived from the main.*.js glob) is replaced by
        the manifest-derived entry check surfaced as "entry.js".
        """
        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = real_shape_dist

            result = get_frontend_build_status()

            assert result["status"] == "ok"
            assert set(result["checks"].keys()) == {
                "index.html",
                "manifest.json",
                "entry.js",
            }
            assert all(c["status"] == "ok" for c in result["checks"].values())
