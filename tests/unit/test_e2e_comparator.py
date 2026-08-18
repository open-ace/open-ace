"""Comparator + classifier unit tests (Issue #2491, N1 infra priority rules)."""

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

comparator = importlib.import_module("e2e_governance_pkg.comparator")
state_mod = importlib.import_module("e2e_governance_pkg.state")
common = importlib.import_module("e2e_governance_pkg.common")


class TestInfraPriority:
    """N1: server-level evidence is the only infra trigger."""

    def test_connection_refused_with_live_server_is_not_infra(self):
        failure = {"exception_class": "ConnectionRefusedError", "timestamp": 50}
        evidence = {
            "readiness_achieved": True,
            "exit": {"abnormal": False},
            "liveness_failures": [],
        }
        assert comparator.is_infra_failure(failure, evidence) is False
        assert comparator.classify_failure(failure, evidence) == "test_body_exception"

    def test_readiness_not_achieved_is_infra(self):
        failure = {"exception_class": "ConnectionRefusedError", "timestamp": 50}
        evidence = {"readiness_achieved": False}
        assert comparator.is_infra_failure(failure, evidence) is True
        assert comparator.classify_failure(failure, evidence) == "infrastructure_error"

    def test_abnormal_server_exit_is_infra(self):
        evidence = {"readiness_achieved": True, "exit": {"abnormal": True, "code": 2}}
        assert comparator.is_infra_failure({"timestamp": 99}, evidence) is True

    def test_liveness_window_covering_attempt_is_infra(self):
        failure = {"timestamp": 100}
        evidence = {
            "readiness_achieved": True,
            "exit": {"abnormal": False},
            "liveness_failures": [{"start": 90, "end": 120}],
        }
        assert comparator.is_infra_failure(failure, evidence) is True

    def test_liveness_window_outside_attempt_is_not_infra(self):
        failure = {"timestamp": 50}
        evidence = {
            "readiness_achieved": True,
            "exit": {"abnormal": False},
            "liveness_failures": [{"start": 90, "end": 120}],
        }
        assert comparator.is_infra_failure(failure, evidence) is False

    def test_environment_missing_is_infra(self):
        evidence = {
            "readiness_achieved": True,
            "exit": {"abnormal": False},
            "environment_missing": True,
        }
        assert comparator.is_infra_failure({"timestamp": 10}, evidence) is True

    def test_no_server_evidence_is_never_infra(self):
        assert comparator.is_infra_failure({"exception_class": "TimeoutError"}, None) is False

    def test_assertion_failure_with_live_server_is_assertion(self):
        failure = {"exception_class": "AssertionError", "message": "x != y"}
        evidence = {"readiness_achieved": True, "exit": {"abnormal": False}}
        assert comparator.classify_failure(failure, evidence) == "assertion_failure"

    def test_setup_phase_failure_is_setup_error(self):
        failure = {"phase": "setup", "exception_class": "Exception", "message": "boom"}
        assert comparator.classify_failure(failure, None) == "setup_error"


class TestThreeWay:
    def _run(self, outcomes, fingerprints=None):
        fingerprints = fingerprints or ["f1"] * len(outcomes)
        return [
            {
                "first_attempt_outcome": o,
                "category": "assertion_failure" if o == "fail" else "assertion_failure",
                "fingerprint": f,
            }
            for o, f in zip(outcomes, fingerprints)
        ]

    def test_all_first_attempt_pass_is_stable_pass(self):
        assert comparator.classify_three_way(self._run(["pass", "pass", "pass"])) == "stable-pass"

    def test_same_outcome_and_fingerprint_is_deterministic_known_fail(self):
        runs = self._run(["fail", "fail", "fail"], ["abc", "abc", "abc"])
        assert comparator.classify_three_way(runs) == "deterministic-known-fail"

    def test_inconsistent_runs_are_quarantined_flaky(self):
        runs = self._run(["fail", "pass", "fail"])
        assert comparator.classify_three_way(runs) == "quarantined-flaky"

    def test_same_outcome_different_fingerprint_is_flaky(self):
        runs = self._run(["fail", "fail", "fail"], ["a", "b", "a"])
        assert comparator.classify_three_way(runs) == "quarantined-flaky"

    def test_fewer_than_three_runs_rejected(self):
        with pytest.raises(common.GovernanceError):
            comparator.classify_three_way(self._run(["pass", "pass"]))

    def test_infra_in_reference_runs_never_classifies(self):
        runs = self._run(["fail", "fail", "fail"])
        runs[1]["category"] = "infrastructure_error"
        with pytest.raises(common.GovernanceError, match="infrastructure_error"):
            comparator.classify_three_way(runs)


class TestReferenceRunGate:
    def test_infra_nonzero_voids_runs(self):
        runs = [
            {"commit_sha": "s", "contract_key": "k", "outcomes": [], "category": None},
            {
                "commit_sha": "s",
                "contract_key": "k",
                "outcomes": [],
                "category": "infrastructure_error",
                "nodeid": "tests/e2e/x.py::t",
            },
            {"commit_sha": "s", "contract_key": "k", "outcomes": []},
        ]
        errors = comparator.validate_reference_runs(runs)
        assert any("infrastructure_error" in e for e in errors)

    def test_mixed_sha_or_contract_rejected(self):
        runs = [
            {"commit_sha": "a", "contract_key": "k", "outcomes": []},
            {"commit_sha": "b", "contract_key": "k", "outcomes": []},
            {"commit_sha": "a", "contract_key": "k", "outcomes": []},
        ]
        errors = comparator.validate_reference_runs(runs)
        assert any("commit SHA" in e for e in errors)


class TestCompareRun:
    def test_known_only_clean_run_exits_zero(self):
        diff = comparator.compare_run(
            ["a", "b"],
            {
                "a": {"final_outcome": "pass"},
                "b": {
                    "final_outcome": "fail",
                    "category": "assertion_failure",
                    "fingerprint": "f1",
                },
            },
            {"b": {"debt": "deterministic-known-fail", "fingerprint": "f1"}},
        )
        assert diff["verdict_exit_code"] == 0
        assert diff["known_only"] is False  # not a known-ONLY run (a passed)

    def test_all_known_failures_only_exits_zero(self):
        diff = comparator.compare_run(
            ["b"],
            {"b": {"final_outcome": "fail", "category": "assertion_failure", "fingerprint": "f1"}},
            {"b": {"debt": "deterministic-known-fail", "fingerprint": "f1"}},
        )
        assert diff["verdict_exit_code"] == 0
        assert diff["known_only"] is True  # interpretation (a) of the R6 clause

    def test_new_failure_blocks(self):
        diff = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "fail", "category": "assertion_failure", "fingerprint": "f2"}},
            {"a": {"debt": "stable-pass"}},
        )
        assert diff["new_failures"] == ["a"]
        assert diff["verdict_exit_code"] == 1

    def test_changed_fingerprint_blocks(self):
        diff = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "fail", "category": "assertion_failure", "fingerprint": "zz"}},
            {"a": {"debt": "deterministic-known-fail", "fingerprint": "f1"}},
        )
        assert diff["changed"] == ["a"]
        assert diff["verdict_exit_code"] == 1

    def test_missing_and_unexpected_block(self):
        diff = comparator.compare_run(["a"], {"b": {"final_outcome": "pass"}}, {})
        assert diff["missing"] == ["a"]
        assert diff["unexpected"] == ["b"]
        assert diff["verdict_exit_code"] == 1

    def test_unexpected_skip_blocks(self):
        diff = comparator.compare_run(
            ["a"], {"a": {"final_outcome": "skip"}}, {"a": {"debt": "stable-pass"}}
        )
        assert diff["unexpected_skips"] == ["a"]
        assert diff["verdict_exit_code"] == 1

    def test_expected_skip_with_reason_passes(self):
        diff = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "skip"}},
            {
                "a": {
                    "debt": "stable-pass",
                    "expected_skip": {
                        "reason": "r",
                        "owner": "o",
                        "issue": 1,
                        "expiry": "2099-01-01T00:00:00+00:00",
                    },
                }
            },
        )
        assert diff["verdict_exit_code"] == 0

    def test_expected_xfail_covers_xfail_but_not_skip(self):
        record = {"reason": "r", "owner": "o", "issue": 1, "expiry": "2099-01-01T00:00:00+00:00"}
        ok = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "xfail"}},
            {"a": {"debt": "stable-pass", "expected_xfail": record}},
        )
        assert ok["verdict_exit_code"] == 0
        flipped = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "skip"}},
            {"a": {"debt": "stable-pass", "expected_xfail": record}},
        )
        assert flipped["unexpected_skips"] == ["a"]  # skip<->xfail flip is caught

    def test_infra_observed_blocks_even_if_known(self):
        diff = comparator.compare_run(
            ["a"],
            {"a": {"final_outcome": "fail", "category": "infrastructure_error"}},
            {"a": {"debt": "deterministic-known-fail", "fingerprint": "f1"}},
        )
        assert "a" in diff["invalid"]
        assert diff["verdict_exit_code"] == 1

    def test_lane_timeout_is_invalid_timeout_cancel_not_missing(self):
        diff = comparator.compare_run(
            ["a", "b"], {}, {}, job_conclusion="timed_out", envelope_present=False
        )
        assert set(diff["invalid"]) == {"a", "b"}
        assert all("timeout/cancel" in reason for reason in diff["invalid"].values())
        assert diff["missing"] == []

    def test_missing_envelope_fails_closed(self):
        diff = comparator.compare_run(["a"], {}, {}, envelope_present=False)
        assert "missing/corrupt run envelope" in diff["invalid"]["a"]

    def test_resolved_pending_shrink_blocks(self):
        diff = comparator.compare_run(
            ["a"], {"a": {"final_outcome": "pass"}}, {"a": {"debt": "resolved"}}
        )
        assert diff["resolved_pending_shrink"] == ["a"]
        assert diff["verdict_exit_code"] == 1

    def test_single_recovery_does_not_shrink(self):
        # known-fail item passes once: not resolved, not blocking (state
        # machine advances it to recovering on the scheduled path)
        diff = comparator.compare_run(
            ["a"], {"a": {"final_outcome": "pass"}}, {"a": {"debt": "deterministic-known-fail"}}
        )
        assert diff["verdict_exit_code"] == 0


class TestRecoveryStateMachine:
    def test_known_fail_needs_three_consecutive_cleans(self):
        entry = {"debt": "deterministic-known-fail"}
        entry, note = state_mod.apply_scheduled_result(entry, True)
        assert (entry["debt"], note) == ("recovering", "recovering")
        entry, _ = state_mod.apply_scheduled_result(entry, True)
        assert entry["debt"] == "recovering" and entry["clean_streak"] == 2
        entry, note = state_mod.apply_scheduled_result(entry, True)
        assert (entry["debt"], note) == ("resolved", "resolved")

    def test_failure_resets_streak(self):
        entry, _ = state_mod.apply_scheduled_result({"debt": "deterministic-known-fail"}, True)
        entry, note = state_mod.apply_scheduled_result(entry, False)
        assert (entry["debt"], entry["clean_streak"], note) == (
            "deterministic-known-fail",
            0,
            "fail-reset",
        )

    def test_flaky_needs_five_cleans(self):
        entry = {"debt": "quarantined-flaky"}
        for i in range(4):
            entry, note = state_mod.apply_scheduled_result(entry, True)
            assert entry["debt"] == "recovering", i
        entry, note = state_mod.apply_scheduled_result(entry, True)
        assert note == "resolved"

    def test_stable_pass_untouched(self):
        entry, note = state_mod.apply_scheduled_result({"debt": "stable-pass"}, False)
        assert (entry["debt"], note) == ("stable-pass", "fail")


class TestFingerprintNormalization:
    def test_fingerprint_stable_across_noise(self):
        a = common.failure_fingerprint("AssertionError", "port 19888 not reachable", None)
        b = common.failure_fingerprint("AssertionError", "port 19999 not reachable", None)
        assert a == b

    def test_fingerprint_differs_on_exception_class(self):
        a = common.failure_fingerprint("AssertionError", "boom <n>")
        b = common.failure_fingerprint("TimeoutError", "boom <n>")
        assert a != b

    def test_nodeid_normalization(self):
        n = common.normalize_nodeid(
            "/runner/xyz/tests/e2e/ui/test_a.py::t[12345678-1234-1234-1234-123456789012]"
        )
        assert n == "tests/e2e/ui/test_a.py::t[<uuid>]"


class TestComparatorCli:
    def test_compare_cli_writes_artifacts_and_fails_closed_on_budget_and_selection_errors(
        self, tmp_path
    ):
        selection = {
            "event": "nightly",
            "normal": ["tests/e2e/browser/test_login.py::test_ok"],
            "advisory": [],
            "invalid": {"tests/e2e/browser/test_bad.py::test_bad": ["missing metadata"]},
            "closure_errors": ["nightly lane is not closed"],
            "counts": {"normal": 1, "advisory": 0, "invalid": 1},
        }
        envelope = {
            "schema_name": common.RUN_ENVELOPE_SCHEMA_NAME,
            "schema_version": 1,
            "job_conclusion": "success",
            "duration_minutes": 130,
            "server": {"readiness_achieved": True, "exit": {"abnormal": False, "code": 0}},
            "outcomes": [
                {
                    "nodeid": "tests/e2e/browser/test_login.py::test_ok",
                    "final_outcome": "pass",
                    "duration_seconds": 10,
                    "attempts": 1,
                }
            ],
        }
        state = {"schema_name": "openace-e2e-state", "schema_version": 1, "entries": {}}
        governance_report = {"counts": {"inventory": 153}}
        selection_path = tmp_path / "selection.json"
        envelope_path = tmp_path / "envelope.json"
        state_path = tmp_path / "state.json"
        governance_path = tmp_path / "governance.json"
        json_output = tmp_path / "out" / "diff.json"
        markdown_output = tmp_path / "out" / "summary.md"
        selection_path.write_text(json.dumps(selection) + "\n", encoding="utf-8")
        envelope_path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        governance_path.write_text(json.dumps(governance_report) + "\n", encoding="utf-8")

        exit_code = comparator.run_cli(
            [
                "compare",
                "--selection",
                str(selection_path),
                "--envelope",
                str(envelope_path),
                "--state",
                str(state_path),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
                "--governance-report",
                str(governance_path),
            ]
        )

        assert exit_code == 1
        payload = json.loads(json_output.read_text(encoding="utf-8"))
        assert payload["schema_name"] == common.COMPARE_RESULT_SCHEMA_NAME
        assert "__budget__" in payload["diff"]["invalid"]
        assert "__closure__" in payload["diff"]["invalid"]
        assert "__selection__" in payload["diff"]["invalid"]
        markdown = markdown_output.read_text(encoding="utf-8")
        assert "Full E2E Governance" in markdown
        assert "nightly lane is not closed" in markdown

    def test_compare_selection_run_flags_duplicate_outcomes(self):
        diff, _ = comparator.compare_selection_run(
            {"normal": ["a"], "advisory": []},
            {
                "job_conclusion": "success",
                "duration_minutes": 1,
                "outcomes": [
                    {"nodeid": "a", "final_outcome": "pass", "duration_seconds": 1},
                    {"nodeid": "a", "final_outcome": "pass", "duration_seconds": 1},
                ],
            },
            {},
        )

        assert diff["verdict_exit_code"] == 1
        assert diff["invalid"]["a"] == "duplicate observed results (2)"
