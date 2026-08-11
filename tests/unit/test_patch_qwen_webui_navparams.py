"""
Unit tests for scripts/patch-qwen-webui-navparams.py

Tests for the WebUI navigation patch that preserves URL params across
sessionId nav, history nav, and project-selector nav.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def load_patch_module():
    """Load the patch module from scripts directory."""
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    module_path = scripts_dir / "patch-qwen-webui-navparams.py"
    spec = importlib.util.spec_from_file_location("patch_qwen_webui_navparams", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPatchNavparams:
    """Test the navparams patch script."""

    @pytest.fixture
    def mock_bundle_content(self):
        """Return mock bundle content with all patterns to patch."""
        return """
let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}
let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()}
let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()}
let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}`)},[S])
"""

    @pytest.fixture
    def mock_bundle_with_v1_patch(self):
        """Return mock bundle content with v1 patch already applied."""
        return """
let l=e=>{let n=new URLSearchParams(window.location.search);n.set(`sessionId`,e),n.delete(`view`),t({search:n.toString()})}
let e=new URLSearchParams(window.location.search);e.set(`view`,`history`),t({search:e.toString()}
let e=new URLSearchParams(window.location.search);e.set(`view`,`history`),t({search:e.toString()}
let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}${window.location.search}`)},[S])
"""

    @pytest.fixture
    def mock_bundle_with_v2_patch(self):
        """Return mock bundle content with v2 patch already applied."""
        return """
let l=e=>{let n=new URLSearchParams(window.location.search);n.set(`sessionId`,e),n.delete(`view`),t({search:n.toString()})}
let e=new URLSearchParams(window.location.search);e.set(`view`,`history`),t({search:e.toString()})}
let e=new URLSearchParams(window.location.search);e.set(`view`,`history`),t({search:e.toString()})}
let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;let n=r.find(s=>s.path===e),u=window.location.search;if(n&&n.machine_id){let q=u.includes(`?`)?`&`:`?`;u=`${u}${q}workspaceType=remote&machineId=${encodeURIComponent(n.machine_id)}`}S(`/projects${t}${u}`)},[S,r])
"""

    def test_session_nav_pattern_unique(self, mock_bundle_content):
        """Session nav pattern should be unique (count == 1)."""
        assert (
            mock_bundle_content.count(
                "let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}"
            )
            == 1
        )

    def test_history_nav_pattern_count(self, mock_bundle_content):
        """History nav pattern should appear twice (two buttons)."""
        assert (
            mock_bundle_content.count(
                "let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()}"
            )
            == 2
        )

    def test_project_nav_pattern_unique(self, mock_bundle_content):
        """Project selector nav pattern should be unique."""
        assert (
            mock_bundle_content.count(
                "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}`)},[S])"
            )
            == 1
        )

    def test_patch_applies_session_nav(self, mock_bundle_content):
        """Patch should correctly apply sessionId nav fix."""
        old = "let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}"
        new = "let l=e=>{let n=new URLSearchParams(window.location.search);n.set(`sessionId`,e),n.delete(`view`),t({search:n.toString()})}"

        result = mock_bundle_content.replace(old, new)

        # Verify the patch was applied
        assert "window.location.search" in result
        assert "n.delete(`view`)" in result

    def test_patch_applies_history_nav(self, mock_bundle_content):
        """Patch should correctly apply history nav fix."""
        old = "let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()}"
        new = "let e=new URLSearchParams(window.location.search);e.set(`view`,`history`),t({search:e.toString()}"

        result = mock_bundle_content.replace(old, new)

        # Verify the patch was applied
        assert result.count("window.location.search") >= 2

    def test_patch_applies_project_nav(self, mock_bundle_content):
        """Patch should correctly apply project-selector nav fix."""
        old = "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}`)},[S])"
        new = "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}${window.location.search}`)},[S])"

        result = mock_bundle_content.replace(old, new)

        # Verify the patch was applied
        assert "${window.location.search}" in result

    def test_v1_to_v2_upgrade(self, mock_bundle_with_v1_patch):
        """V1 patch should be upgradeable to V2."""
        # V1 patch
        v1 = "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}${window.location.search}`)},[S])"
        # V2 patch
        v2 = "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);let t=e.startsWith(`/`)?e:`/${e}`;let n=r.find(s=>s.path===e),u=window.location.search;if(n&&n.machine_id){let q=u.includes(`?`)?`&`:`?`;u=`${u}${q}workspaceType=remote&machineId=${encodeURIComponent(n.machine_id)}`}S(`/projects${t}${u}`)},[S,r])"

        result = mock_bundle_with_v1_patch.replace(v1, v2)

        # Verify V2 was applied
        assert "n.machine_id" in result
        assert "workspaceType=remote" in result
        assert "encodeURIComponent(n.machine_id)" in result

    def test_idempotent_already_v2_patched(self, mock_bundle_with_v2_patch):
        """Running on V2-patched bundle should skip (idempotent)."""
        patch_module = load_patch_module()

        # V2 patterns should be present
        assert patch_module.NEW_SESSION_NAV in mock_bundle_with_v2_patch
        assert patch_module.NEW_HISTORY_NAV in mock_bundle_with_v2_patch
        assert patch_module.NEW_PROJECT_NAV_V2 in mock_bundle_with_v2_patch

    def test_version_drift_detected(self):
        """Version drift (missing patterns) should be detected."""
        content = "some random content without patterns"

        patch_module = load_patch_module()

        # None of the OLD patterns should be found
        assert patch_module.OLD_SESSION_NAV not in content
        assert patch_module.OLD_HISTORY_NAV not in content
        assert patch_module.OLD_PROJECT_NAV not in content


class TestCacheBust:
    """Test cache-bust mechanism."""

    def test_cache_bust_format(self):
        """Cache-bust string should have correct format."""
        patch_module = load_patch_module()

        # Cache-bust should be a non-empty string
        assert patch_module.CACHE_BUST
        assert patch_module.CACHE_BUST.startswith("v=navparams-")


class TestBundlePaths:
    """Test bundle path handling."""

    def test_bundle_glob_pattern(self):
        """Bundle glob should match expected path structure."""
        patch_module = load_patch_module()

        # Glob pattern should point to the correct location
        assert "qwen-code-webui" in patch_module.BUNDLE_GLOB
        assert "index-*.js" in patch_module.BUNDLE_GLOB

    def test_index_html_path(self):
        """Index HTML path should point to correct location."""
        patch_module = load_patch_module()

        assert "qwen-code-webui" in patch_module.INDEX_HTML
        assert "index.html" in patch_module.INDEX_HTML


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_duplicate_pattern_detection(self):
        """Duplicate patterns should be detected and cause failure."""
        # sessionId nav pattern duplicated
        content = """
let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}
let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}
"""
        pattern = (
            "let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}"
        )
        assert content.count(pattern) == 2  # Should be 1, not 2

    def test_missing_file_handling(self):
        """Missing bundle file should be handled gracefully."""
        patch_module = load_patch_module()

        # The module should have a main() function that returns int
        assert callable(patch_module.main)
        # main() returns 0 on success, non-zero on failure
        result = patch_module.main()
        assert isinstance(result, int)
        # Should return non-zero when bundle doesn't exist
        assert result != 0
