#!/usr/bin/env python3
"""
The launchd TERM diagnostic moved to manual_launchd_env.py (#2457).

The driver historically collected as test_qwen_cli took env_name/env_mods
as plain main() arguments, which pytest read as missing fixtures (setup
error on every lane run), and it shells out to the real qwen CLI
(~/.npm-global/bin/qwen or PATH) which lane/CI checkouts never have. It
now lives in manual_launchd_env.py, outside pytest discovery (see its
docstring for the manual entrypoint).

This file stays at the historical path so the lane's file-based shard
layout is unchanged: renaming/removing files under tests/issues/ would
shift the round-robin shard assignment of every file after it and
reshuffle which tests share a pytest process (scripts/run_extended_tests.py,
apply_split) — the exact cohabitation reshuffle the 172 cutover hit.
"""

from pathlib import Path


def test_manual_diagnostic_script_kept_out_of_discovery():
    """The launchd diagnostic lives on as a manual script; guard its presence."""
    manual = Path(__file__).with_name("manual_launchd_env.py")
    assert manual.is_file(), f"manual diagnostic script missing: {manual}"
    text = manual.read_text(encoding="utf-8")
    assert "MANUAL DIAGNOSTIC SCRIPT" in text
    assert "NOT COLLECTED BY PYTEST" in text
    # the driver entry must not be pytest-shaped anymore
    assert "def test_qwen_cli(" not in text
    assert "def run_qwen_cli_diagnostic(" in text
