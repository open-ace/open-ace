"""
Unit tests for frontend_build_check module.

Tests the frontend build integrity check functionality.
Issue #3277: Prevent "Open ACE could not render" errors due to missing build artifacts.
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
    check_frontend_build_integrity,
    check_frontend_build_on_startup,
    check_index_html,
    check_main_js,
    check_manifest,
    format_error_message,
    get_dist_dir,
    get_frontend_build_status,
)


@pytest.fixture
def temp_dist_dir(tmp_path: Path) -> Path:
    """Create a temporary dist directory for testing."""
    dist_dir = tmp_path / "static" / "js" / "dist"
    dist_dir.mkdir(parents=True)
    return dist_dir


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
        """Should return OK for valid manifest.json."""
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        manifest_path = vite_dir / "manifest.json"

        manifest_data = {
            "main.js": {"file": "main.abc123.js"},
            "SecurityCenter.js": {"file": "SecurityCenter.def456.js"},
        }
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

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


class TestCheckMainJs:
    """Tests for check_main_js function."""

    def test_missing_directory(self, tmp_path: Path) -> None:
        """Should return MISSING when dist directory doesn't exist."""
        non_existent_dir = tmp_path / "nonexistent"
        result = check_main_js(non_existent_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR

    def test_missing_file(self, temp_dist_dir: Path) -> None:
        """Should return MISSING when main.*.js doesn't exist."""
        result = check_main_js(temp_dist_dir)

        assert result.status == CheckStatus.MISSING
        assert result.error_level == ErrorLevel.ERROR

    def test_valid_file(self, temp_dist_dir: Path) -> None:
        """Should return OK for valid main.*.js."""
        main_js = temp_dist_dir / "main.abc123.js"
        main_js.write_text("console.log('hello')", encoding="utf-8")

        result = check_main_js(temp_dist_dir)

        assert result.status == CheckStatus.OK

    def test_empty_file(self, temp_dist_dir: Path) -> None:
        """Should return INVALID for empty main.*.js."""
        main_js = temp_dist_dir / "main.abc123.js"
        main_js.write_text("", encoding="utf-8")

        result = check_main_js(temp_dist_dir)

        assert result.status == CheckStatus.INVALID
        assert result.error_level == ErrorLevel.ERROR


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

    def test_complete_build(self, temp_dist_dir: Path) -> None:
        """Should pass when all artifacts exist and are valid."""
        # Create index.html
        index_path = temp_dist_dir / "index.html"
        index_path.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")

        # Create manifest.json
        vite_dir = temp_dist_dir / ".vite"
        vite_dir.mkdir()
        manifest_path = vite_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps({"main.js": {"file": "main.abc123.js"}}), encoding="utf-8"
        )

        # Create main.js
        main_js = temp_dist_dir / "main.abc123.js"
        main_js.write_text("console.log('hello')", encoding="utf-8")

        with patch("app.utils.frontend_check.get_dist_dir") as mock_get_dist:
            mock_get_dist.return_value = temp_dist_dir

            result = check_frontend_build_integrity(skip_check=False)

            assert result.success is True
            assert len(result.errors) == 0


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
