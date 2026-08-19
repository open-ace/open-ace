"""Regression tests for sharded Full E2E envelope merging."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "e2e_governance_pkg",
    ROOT / "scripts" / "e2e" / "__init__.py",
    submodule_search_locations=[str(ROOT / "scripts" / "e2e")],
)
assert SPEC and SPEC.loader
_pkg = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("e2e_governance_pkg", _pkg)
SPEC.loader.exec_module(_pkg)

common = __import__("importlib").import_module("e2e_governance_pkg.common")
merge_mod = __import__("importlib").import_module("e2e_governance_pkg.envelope_merge")


def _envelope(target: str, outcome: str = "pass"):
    return {
        "schema_name": common.RUN_ENVELOPE_SCHEMA_NAME,
        "schema_version": 1,
        "category": "e2e",
        "base_url": "http://127.0.0.1:1",
        "started_at": "2026-08-19T00:00:00Z",
        "completed_at": "2026-08-19T00:10:00Z",
        "duration_seconds": 600,
        "duration_minutes": 10,
        "commit_sha": "sha",
        "contract_key": "contract",
        "job_conclusion": "success",
        "return_code": 0,
        "python": "3.11",
        "selected_targets": [target],
        "outcomes": [{"nodeid": target, "final_outcome": outcome}],
        "server": {"readiness_achieved": True, "exit": {"abnormal": False, "code": None}},
    }


def test_merge_combines_disjoint_shards_and_uses_wall_clock_budget():
    second = _envelope("standalone::tests/e2e/legacy.py")
    second["duration_seconds"] = 300
    merged = merge_mod.merge([_envelope("tests/e2e/browser/test_login.py::test_ok"), second])

    assert merged["selected_targets"] == [
        "standalone::tests/e2e/legacy.py",
        "tests/e2e/browser/test_login.py::test_ok",
    ]
    assert merged["duration_seconds"] == 600
    assert merged["artifacts"]["shard_count"] == 2


def test_merge_rejects_overlapping_shards():
    envelope = _envelope("tests/e2e/browser/test_login.py::test_ok")
    with pytest.raises(common.GovernanceError, match="multiple shards"):
        merge_mod.merge([envelope, envelope])
