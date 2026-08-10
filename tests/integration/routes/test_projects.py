#!/usr/bin/env python3
"""
Tests for Projects API security annotations.

Issue #1897: Verify @security_annotated decorator is applied correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestProjectsSecurityAnnotations:
    """Test projects.py security annotations."""

    def test_get_project_has_annotation(self):
        """Test that api_get_project has @security_annotated."""
        from app.routes.projects import api_get_project

        assert hasattr(api_get_project, "_security_annotation")
        assert api_get_project._security_annotation is not None

    def test_daily_stats_has_annotation(self):
        """Test that api_get_project_daily_stats has @security_annotated."""
        from app.routes.projects import api_get_project_daily_stats

        assert hasattr(api_get_project_daily_stats, "_security_annotation")
        assert api_get_project_daily_stats._security_annotation is not None

    def test_get_project_annotation_content(self):
        """Test that api_get_project annotation mentions ownership check."""
        from app.routes.projects import api_get_project

        annotation = api_get_project._security_annotation
        assert "ownership" in annotation.lower() or "get_user_project" in annotation.lower()

    def test_daily_stats_annotation_content(self):
        """Test that daily stats annotation mentions ownership check."""
        from app.routes.projects import api_get_project_daily_stats

        annotation = api_get_project_daily_stats._security_annotation
        assert "ownership" in annotation.lower() or "get_user_project" in annotation.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
