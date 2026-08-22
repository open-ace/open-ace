#!/usr/bin/env python3
"""
The stream-json diagnostic moved to manual_stream_json_mode.py (#2457).

Same story as the launchd driver: test_stream_json_mode took
env_name/env_mods as plain main() arguments (pytest fixture errors on
every lane run) and drives the real qwen CLI, which lane/CI checkouts
never have. It now lives in manual_stream_json_mode.py, outside pytest
discovery.

This file stays at the historical path so the lane's file-based shard
layout is unchanged (see test_launchd_env.py for the rationale).
"""

from pathlib import Path


def test_manual_diagnostic_script_kept_out_of_discovery():
    """The stream-json diagnostic lives on as a manual script; guard its presence."""
    manual = Path(__file__).with_name("manual_stream_json_mode.py")
    assert manual.is_file(), f"manual diagnostic script missing: {manual}"
    text = manual.read_text(encoding="utf-8")
    assert "MANUAL DIAGNOSTIC SCRIPT" in text
    assert "NOT COLLECTED BY PYTEST" in text
    # the driver entry must not be pytest-shaped anymore
    assert "def test_stream_json_mode(" not in text
    assert "def run_stream_json_diagnostic(" in text
