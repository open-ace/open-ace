"""Inventory / manifest / attempts-plugin unit tests (Issue #2491 P0+P1)."""

from __future__ import annotations

import importlib.util
import json
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

inventory_mod = importlib.import_module("e2e_governance_pkg.inventory")
manifest_mod = importlib.import_module("e2e_governance_pkg.manifest")
attempts = importlib.import_module("e2e_governance_pkg.pytest_attempts")


class TestManifestParsing:
    def test_parse_collect_output(self):
        text = (
            "tests/e2e/browser/test_login.py::test_a\n"
            "tests/e2e/browser/test_login.py::test_b[param]\n"
            "garbage line\n"
            "3 tests collected in 0.01s\n"
        )
        assert manifest_mod.parse_collect_output(text) == [
            "tests/e2e/browser/test_login.py::test_a",
            "tests/e2e/browser/test_login.py::test_b[param]",
        ]

    def test_repo_manifest_matches_live_collection(self):
        """Committed manifest must equal a fresh --collect-only (drift gate)."""
        manifest = manifest_mod.load_artifact(
            ROOT / "ci" / "e2e-expected-nodeids.json", "openace-e2e-expected-nodeids"
        )
        live = manifest_mod.collect(ROOT)
        assert manifest["nodeids"] == live


class TestManifestValidation:
    def _inventory(self, *entries):
        return {
            "schema_name": "openace-e2e-inventory",
            "schema_version": 1,
            "entries": list(entries),
        }

    def _entry(self, path, mode="pytest-automated", collects=True, home="nightly"):
        row = {
            "path": path,
            "mode": mode,
            "owner": "o",
            "issue": 1,
            "home_lane": home,
            "executor": "pytest" if mode == "pytest-automated" else "none",
            "collects": collects,
        }
        if mode == "standalone-automated":
            row["executor"] = "standalone"
        return row

    def test_manual_demo_collecting_nodeids_is_flagged(self):
        inventory = self._inventory(self._entry("tests/e2e/ui/demo_x.py", mode="manual-demo"))
        manifest = {
            "schema_name": "openace-e2e-expected-nodeids",
            "schema_version": 1,
            "nodeids": ["tests/e2e/ui/demo_x.py::t"],
        }
        issues = manifest_mod.validate_manifest(manifest, inventory=inventory)
        assert any("masquerade" in i for i in issues)

    def test_collects_true_without_nodeids_is_flagged(self):
        inventory = self._inventory(self._entry("tests/e2e/ui/test_x.py", collects=True))
        manifest = {
            "schema_name": "openace-e2e-expected-nodeids",
            "schema_version": 1,
            "nodeids": [],
        }
        issues = manifest_mod.validate_manifest(manifest, inventory=inventory)
        assert any("collects none" in i for i in issues)

    def test_collects_false_with_nodeids_is_drift(self):
        inventory = self._inventory(self._entry("tests/e2e/ui/test_x.py", collects=False))
        manifest = {
            "schema_name": "openace-e2e-expected-nodeids",
            "schema_version": 1,
            "nodeids": ["tests/e2e/ui/test_x.py::t"],
        }
        issues = manifest_mod.validate_manifest(manifest, inventory=inventory)
        assert any("manifest drift" in i for i in issues)

    def test_repo_manifest_validation_green(self):
        manifest = manifest_mod.load_artifact(
            ROOT / "ci" / "e2e-expected-nodeids.json", "openace-e2e-expected-nodeids"
        )
        assert manifest_mod.validate_manifest(manifest) == []


class TestInventoryValidation:
    def test_missing_disposition_detected(self, tmp_path):
        base = tmp_path / "tests" / "e2e"
        base.mkdir(parents=True)
        (base / "test_new.py").write_text("def test_x(): pass\n")
        issues = inventory_mod.validate_inventory(
            {"schema_name": "openace-e2e-inventory", "schema_version": 1, "entries": []},
            tmp_path,
        )
        assert any("no inventory disposition" in i for i in issues)

    def test_missing_on_disk_detected(self, tmp_path):
        entry = {
            "path": "tests/e2e/ghost.py",
            "mode": "pytest-automated",
            "owner": "o",
            "issue": 1,
            "home_lane": "nightly",
            "executor": "pytest",
        }
        issues = inventory_mod.validate_inventory(
            {"schema_name": "openace-e2e-inventory", "schema_version": 1, "entries": [entry]},
            tmp_path,
        )
        assert any("missing on disk" in i for i in issues)

    def test_non_manual_needs_exactly_one_executor(self, tmp_path):
        base = tmp_path / "tests" / "e2e"
        base.mkdir(parents=True)
        (base / "test_a.py").write_text("")
        entry = {
            "path": "tests/e2e/test_a.py",
            "mode": "standalone-automated",
            "owner": "o",
            "issue": 1,
            "home_lane": "weekly",
            "executor": "pytest",
        }
        issues = inventory_mod.validate_inventory(
            {"schema_name": "openace-e2e-inventory", "schema_version": 1, "entries": [entry]},
            tmp_path,
        )
        assert any("exactly one executor" in i for i in issues)


class TestAttemptsPlugin:
    """P0 probe conclusions locked in as regression tests."""

    @pytest.fixture
    def probe_run(self, tmp_path):
        test_file = tmp_path / "test_probe_unit.py"
        marker = tmp_path / "marker.txt"
        test_file.write_text(
            "import pathlib\n"
            "MARK = pathlib.Path(__file__).parent / 'marker.txt'\n"
            "def test_always_pass():\n"
            "    assert True\n"
            "def test_always_fail():\n"
            "    assert False, 'deterministic failure'\n"
            "def test_flaky_second_attempt():\n"
            "    if not MARK.exists():\n"
            "        MARK.write_text('x')\n"
            "        assert False, 'first attempt fails'\n"
        )
        attempts_path = tmp_path / "attempts.jsonl"
        rc = pytest.main(
            [
                str(test_file),
                "--reruns",
                "1",
                f"--e2e-attempts={attempts_path}",
                "-o",
                "addopts=",
                # unique tmp dirs per fixture call share the module basename;
                # importlib mode keeps in-process re-runs from colliding
                "--import-mode=importlib",
                "-q",
                "--no-header",
            ],
            plugins=[attempts],
        )
        attempts._configure_sink(None)  # reset module sink for later tests
        marker.unlink(missing_ok=True)
        return rc, attempts_path

    def test_jsonl_records_every_attempt_and_phase(self, probe_run):
        rc, attempts_path = probe_run
        records = [json.loads(line) for line in attempts_path.read_text().splitlines()]
        by_node: dict[str, list[dict]] = {}
        for record in records:
            by_node.setdefault(record["nodeid"], []).append(record)
        flaky = next(n for n in by_node if "flaky" in n)
        # two attempts (rerun), each with setup+call phases preserved
        assert {r["attempt"] for r in by_node[flaky]} == {1, 2}
        assert any(r["phase"] == "call" and r["outcome"] == "rerun" for r in by_node[flaky])
        failing = next(n for n in by_node if "always_fail" in n)
        assert any(
            r["exception_class"] == "AssertionError" and r["message"] == "deterministic failure"
            for r in by_node[failing]
        )

    def test_summarize_attempts_keeps_flaky_signal(self, probe_run):
        rc, attempts_path = probe_run
        lines = attempts_path.read_text().splitlines()
        summary = attempts.summarize_attempts(lines)
        flaky = next(k for k in summary if "flaky" in k)
        # JUnit would show a clean pass; the summary keeps first=fail/attempt=2
        assert summary[flaky]["final_outcome"] == "pass"
        assert summary[flaky]["first_outcome"] == "fail"
        assert summary[flaky]["attempts"] == 2
        failing = next(k for k in summary if "always_fail" in k)
        assert summary[failing]["final_outcome"] == "fail"

    def test_summarize_pure_function(self):
        lines = [
            json.dumps({"nodeid": "a", "attempt": 1, "phase": "setup", "outcome": "passed"}),
            json.dumps(
                {
                    "nodeid": "a",
                    "attempt": 1,
                    "phase": "call",
                    "outcome": "failed",
                    "exception_class": "AssertionError",
                    "message": "x",
                }
            ),
            json.dumps({"nodeid": "a", "attempt": 2, "phase": "call", "outcome": "passed"}),
        ]
        summary = attempts.summarize_attempts(lines)
        assert summary["a"] == {
            "final_outcome": "pass",
            "first_outcome": "fail",
            "attempts": 2,
            "exception_class": "AssertionError",
            "message": "x",
        }

    def test_summarize_setup_error_first_attempt_keeps_fail_signal(self):
        # attempt 1 dies in setup (no call record); the first-attempt signal
        # must not silently fall back to the final (rerun-passed) outcome
        lines = [
            json.dumps(
                {
                    "nodeid": "a",
                    "attempt": 1,
                    "phase": "setup",
                    "outcome": "failed",
                    "exception_class": "FixtureError",
                    "message": "fixture boom",
                }
            ),
            json.dumps({"nodeid": "a", "attempt": 2, "phase": "setup", "outcome": "passed"}),
            json.dumps({"nodeid": "a", "attempt": 2, "phase": "call", "outcome": "passed"}),
        ]
        summary = attempts.summarize_attempts(lines)
        assert summary["a"]["first_outcome"] == "fail"
        assert summary["a"]["final_outcome"] == "pass"
        assert summary["a"]["attempts"] == 2
