"""Regression tests for E2E modules with standalone import stubs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.regression
def test_personal_files_e2e_route_stub_imports() -> None:
    """The standalone fs route stub must track production workspace imports."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                # The default run_name is "<run_path>", so the module's
                # __main__ browser/server entry point is intentionally skipped.
                "import runpy; "
                "runpy.run_path('tests/e2e/work/e2e_personal_files_upload_download.py')"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
