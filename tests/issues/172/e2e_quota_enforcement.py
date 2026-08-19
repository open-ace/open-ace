#!/usr/bin/env python3
"""
Quota enforcement e2e moved to manual_e2e_quota_enforcement.py (#2457).

The checks historically in this file target a *deployed* environment —
psql against the production PostgreSQL, greps under /home/openace/, and
journalctl — none of which exist in a lane/CI checkout. They now live in
manual_e2e_quota_enforcement.py, outside pytest discovery (see its
docstring for the manual entrypoint).

This file stays at its historical path so the lane's file-based shard
layout is unchanged: removing a file from tests/issues/ would shift the
round-robin shard assignment of every file after it and reshuffle which
tests share a pytest process (scripts/run_extended_tests.py, apply_split).
"""

from pathlib import Path


def test_manual_diagnostic_script_kept_out_of_discovery():
    """The quota e2e lives on as a manual diagnostic; guard its presence."""
    manual = Path(__file__).with_name("manual_e2e_quota_enforcement.py")
    assert manual.is_file(), f"manual diagnostic script missing: {manual}"
    text = manual.read_text(encoding="utf-8")
    assert "MANUAL DIAGNOSTIC SCRIPT" in text
    assert "NOT COLLECTED BY PYTEST" in text
