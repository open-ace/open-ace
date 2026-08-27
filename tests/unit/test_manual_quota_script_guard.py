"""Guard for the manual quota-enforcement diagnostic (Issues #172/#2457).

The quota e2e checks target a *deployed* environment — psql against the
production PostgreSQL, greps under /home/openace/, and journalctl — none of
which exist in a lane/CI checkout. They live in
scripts/manual_e2e_quota_enforcement.py, outside pytest discovery.

Migrated from tests/issues/172/e2e_quota_enforcement.py; the guard's target
path was retargeted from the old same-directory neighbor
(``Path(__file__).with_name(...)``) to the script's new repo-root scripts/
home. The legacy file's historical-path/shard-layout rationale is obsolete:
the whole tests/issues/172 directory is drained by this batch (#2429).
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(172)]


def test_manual_diagnostic_script_kept_out_of_discovery():
    """The quota e2e lives on as a manual diagnostic; guard its presence."""
    manual = Path(__file__).resolve().parents[2] / "scripts" / "manual_e2e_quota_enforcement.py"
    assert manual.is_file(), f"manual diagnostic script missing: {manual}"
    text = manual.read_text(encoding="utf-8")
    assert "MANUAL DIAGNOSTIC SCRIPT" in text
    assert "NOT COLLECTED BY PYTEST" in text
