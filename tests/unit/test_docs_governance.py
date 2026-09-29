"""CI gate for docs governance (2026-09-29 restructure).

Runs scripts/lint/check_docs_bilingual.py as a subprocess so CI enforces the
same checks as pre-commit: bilingual anchors, index coverage, no language
directories, and endpoint/env reference coverage.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_docs_governance_gate_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/lint/check_docs_bilingual.py"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, "docs governance gate failed:\n" + result.stdout + result.stderr
    assert "docs governance: OK" in result.stdout
