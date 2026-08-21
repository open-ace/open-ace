"""Governance validator + writer unit tests (Issue #2491: R2 dual clocks,
N3 atomic dispositions, A1 single-writer atomicity, budgets, policy)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

governance = importlib.import_module("e2e_governance_pkg.governance")
inventory_mod = importlib.import_module("e2e_governance_pkg.inventory")
common = importlib.import_module("e2e_governance_pkg.common")

NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def _promo(entries=None, default_review_by="2099-01-01T00:00:00+00:00"):
    return {
        "schema_name": "openace-e2e-promotion",
        "schema_version": 1,
        "metadata": {"default_review_by": default_review_by},
        "entries": entries or {},
    }


class TestPromotionClocks:
    def test_review_by_expiry_fails(self):
        promo = _promo({"a": {"state": "observing", "review_by": "2026-08-01T00:00:00+00:00"}})
        errors = governance.validate_promotion_clocks(promo, now=NOW)
        assert any("expired" in e for e in errors)

    def test_observing_run_cap_5(self):
        promo = _promo({"a": {"state": "observing", "effective_runs": 6}})
        errors = governance.validate_promotion_clocks(promo, now=NOW)
        assert any("effective_runs=6 > cap 5" in e for e in errors)

    def test_candidate_run_cap_25(self):
        promo = _promo({"a": {"state": "candidate", "effective_runs": 26}})
        errors = governance.validate_promotion_clocks(promo, now=NOW)
        assert any("cap 25" in e for e in errors)

    def test_within_caps_passes(self):
        promo = _promo(
            {
                "a": {"state": "observing", "effective_runs": 5},
                "b": {
                    "state": "candidate",
                    "effective_runs": 25,
                    "review_by": "2099-01-01T00:00:00+00:00",
                },
            }
        )
        assert governance.validate_promotion_clocks(promo, now=NOW) == []


class TestRequiredLegality:
    def test_required_flaky_at_rest_is_an_error(self):
        state = {"entries": {"a": {"debt": "quarantined-flaky"}}}
        promo = _promo({"a": {"state": "required"}})
        errors = governance.validate_required_legality(state, promo)
        assert any("atomically demote" in e for e in errors)

    def test_required_known_fail_without_disposition_is_an_error(self):
        state = {"entries": {"a": {"debt": "deterministic-known-fail"}}}
        promo = _promo({"a": {"state": "required"}})
        errors = governance.validate_required_legality(state, promo)
        assert any("atomically demote" in e for e in errors)

    def test_required_known_fail_with_atomic_disposition_ok(self):
        state = {
            "entries": {
                "a": {
                    "debt": "deterministic-known-fail",
                    "atomic_disposition": {"action": "rehome", "lane": "nightly"},
                }
            }
        }
        promo = _promo({"a": {"state": "required"}})
        assert governance.validate_required_legality(state, promo) == []

    def test_required_stable_pass_ok(self):
        state = {"entries": {"a": {"debt": "stable-pass"}}}
        promo = _promo({"a": {"state": "required"}})
        assert governance.validate_required_legality(state, promo) == []


class TestExecutionDispositions:
    def test_nodeid_state_is_rejected_after_a_file_becomes_standalone(self):
        inventory = {
            "entries": [
                {
                    "path": "tests/e2e/manage/legacy.py",
                    "mode": "standalone-automated",
                }
            ]
        }
        state = {"entries": {"tests/e2e/manage/legacy.py::test_old": {"debt": "stable-pass"}}}
        assert governance.validate_execution_dispositions(inventory, state, _promo()) == [
            "tests/e2e/manage/legacy.py::test_old: state entry requires pytest-automated disposition"
        ]

    def test_standalone_state_is_rejected_after_a_file_becomes_pytest(self):
        inventory = {
            "entries": [{"path": "tests/e2e/manage/legacy.py", "mode": "pytest-automated"}]
        }
        state = {"entries": {"standalone::tests/e2e/manage/legacy.py": {"debt": "stable-pass"}}}
        assert governance.validate_execution_dispositions(inventory, state, _promo()) == [
            "standalone::tests/e2e/manage/legacy.py: state entry requires standalone-automated disposition"
        ]


class TestQuarantineAndSkipValidation:
    def _state(self, entries):
        return {"entries": entries}

    def test_quarantine_requires_owner_issue_expiry(self):
        quarantine = {"owner": "a", "expiry": "2099-01-01T00:00:00+00:00"}
        errors = governance.validate_quarantines(
            self._state({"a": {"quarantine": quarantine}}), now=NOW
        )
        assert any("missing 'issue'" in e for e in errors)

    def test_quarantine_window_capped_at_30_days(self):
        quarantine = {
            "owner": "a",
            "issue": 2491,
            "created": "2026-08-01T00:00:00+00:00",
            "expiry": "2026-10-01T00:00:00+00:00",  # 61 days
        }
        errors = governance.validate_quarantines(
            self._state({"a": {"quarantine": quarantine}}), now=NOW
        )
        assert any("window > 30d" in e for e in errors)

    def test_expired_quarantine_fails_closed(self):
        quarantine = {
            "owner": "a",
            "issue": 1,
            "created": "2026-07-01T00:00:00+00:00",
            "expiry": "2026-08-01T00:00:00+00:00",
        }
        errors = governance.validate_quarantines(
            self._state({"a": {"quarantine": quarantine}}), now=NOW
        )
        assert any("expired" in e for e in errors)

    def test_expected_skip_needs_all_fields(self):
        record = {"reason": "r", "owner": "o", "issue": 1, "expiry": "2000-01-01T00:00:00+00:00"}
        errors = governance.validate_expected_skips(
            self._state({"a": {"expected_skip": record}}), now=NOW
        )
        assert any("expired" in e for e in errors)
        errors = governance.validate_expected_skips(
            self._state({"a": {"expected_skip": {"reason": "r"}}}), now=NOW
        )
        assert any("missing 'owner'" in e for e in errors)


class TestBudgets:
    def test_pr_hard_timeout_30m(self):
        assert governance.check_budgets("pr-critical", 30.1, {}) != []

    def test_pr_per_item_cap_120s(self):
        assert governance.check_budgets("pr-critical", 10, {"a": 121}) != []
        assert governance.check_budgets("pr-critical", 10, {"a": 120}) == []

    def test_nightly_and_weekly_budgets(self):
        assert governance.check_budgets("nightly", 121, {}) != []
        assert governance.check_budgets("weekly", 181, {}) != []
        assert governance.check_budgets("nightly", 120, {}) == []


class TestEffectiveSamples:
    def test_counts_only_current_contract_key(self):
        history = [
            {"contract_key": "k1"},
            {"contract_key": "k1"},
            {"contract_key": "k2"},
            {"contract_key": None},
        ]
        assert governance.effective_samples(history, "k1") == 2
        assert governance.effective_samples(history, "k3") == 0


class TestWriter:
    """A1: the writer is the only legal mutation path and refuses bad states."""

    def test_set_disposition_updates_executor_and_collects_together(self, tmp_path):
        tests_dir = tmp_path / "tests" / "e2e"
        tests_dir.mkdir(parents=True)
        target = tests_dir / "legacy_playwright.py"
        target.write_text("# legacy standalone script\n", encoding="utf-8")
        inventory_path = tmp_path / "inventory.json"
        common.dump_artifact(
            inventory_path,
            {
                "entries": [
                    {
                        "path": "tests/e2e/legacy_playwright.py",
                        "mode": "pytest-automated",
                        "owner": "e2e-governance",
                        "issue": 2491,
                        "home_lane": "nightly",
                        "executor": "pytest",
                        "collects": True,
                    }
                ]
            },
            "openace-e2e-inventory",
        )

        rc = governance.main(
            [
                "set-disposition",
                "--path",
                "tests/e2e/legacy_playwright.py",
                "--mode",
                "standalone-automated",
                "--inventory",
                str(inventory_path),
                "--root",
                str(tmp_path),
            ]
        )
        assert rc == 0
        inventory = common.load_artifact(inventory_path, "openace-e2e-inventory")
        row = inventory["entries"][0]
        assert row["mode"] == "standalone-automated"
        assert row["executor"] == "standalone"
        assert row["collects"] is False

    def test_set_disposition_rejects_orphaned_pytest_state(self, tmp_path):
        tests_dir = tmp_path / "tests" / "e2e"
        tests_dir.mkdir(parents=True)
        (tests_dir / "legacy_playwright.py").write_text("# legacy script\n", encoding="utf-8")
        inventory_path = tmp_path / "inventory.json"
        state_path = tmp_path / "state.json"
        promotion_path = tmp_path / "promotion.json"
        common.dump_artifact(
            inventory_path,
            {
                "entries": [
                    {
                        "path": "tests/e2e/legacy_playwright.py",
                        "mode": "pytest-automated",
                        "owner": "e2e-governance",
                        "issue": 2491,
                        "home_lane": "nightly",
                        "executor": "pytest",
                        "collects": True,
                    }
                ]
            },
            "openace-e2e-inventory",
        )
        common.dump_artifact(
            state_path,
            {"entries": {"tests/e2e/legacy_playwright.py::test_legacy": {"debt": "stable-pass"}}},
            "openace-e2e-state",
        )
        common.dump_artifact(promotion_path, {"entries": {}}, "openace-e2e-promotion")

        rc = governance.main(
            [
                "set-disposition",
                "--path",
                "tests/e2e/legacy_playwright.py",
                "--mode",
                "standalone-automated",
                "--inventory",
                str(inventory_path),
                "--state",
                str(state_path),
                "--promotion",
                str(promotion_path),
                "--root",
                str(tmp_path),
            ]
        )

        assert rc == 1
        inventory = common.load_artifact(inventory_path, "openace-e2e-inventory")
        assert inventory["entries"][0]["mode"] == "pytest-automated"

    def test_quarantine_demotes_required_atomically(self, tmp_path):
        state_path = tmp_path / "state.json"
        promo_path = tmp_path / "promotion.json"
        common.dump_artifact(
            state_path,
            {"entries": {"a": {"debt": "quarantined-flaky"}}},
            "openace-e2e-state",
        )
        common.dump_artifact(
            promo_path,
            {"entries": {"a": {"state": "required"}}},
            "openace-e2e-promotion",
        )
        rc = governance.main(
            [
                "quarantine",
                "--id",
                "a",
                "--owner",
                "eng",
                "--issue",
                "2491",
                "--days",
                "14",
                "--state",
                str(state_path),
                "--promotion",
                str(promo_path),
            ]
        )
        assert rc == 0
        state = common.load_artifact(state_path, "openace-e2e-state")
        promo = common.load_artifact(promo_path, "openace-e2e-promotion")
        quarantine = state["entries"]["a"]["quarantine"]
        assert quarantine["owner"] == "eng" and quarantine["issue"] == 2491
        assert promo["entries"]["a"]["state"] == "observing"  # atomic demotion
        # resulting pair passes the at-rest legality check
        assert governance.validate_required_legality(state, promo) == []

    def test_quarantine_rejects_window_over_30_days(self, tmp_path, capsys):
        state_path = tmp_path / "state.json"
        common.dump_artifact(
            state_path, {"entries": {"a": {"debt": "quarantined-flaky"}}}, "openace-e2e-state"
        )
        rc = governance.main(
            [
                "quarantine",
                "--id",
                "a",
                "--owner",
                "e",
                "--issue",
                "1",
                "--days",
                "31",
                "--state",
                str(state_path),
                "--promotion",
                str(tmp_path / "p.json"),
            ]
        )
        assert rc == 1
        assert "31d > 30d" in capsys.readouterr().err

    def test_promote_to_required_requires_evidence(self, tmp_path, capsys):
        state_path = tmp_path / "state.json"
        promo_path = tmp_path / "promotion.json"
        common.dump_artifact(
            state_path, {"entries": {"a": {"debt": "stable-pass"}}}, "openace-e2e-state"
        )
        common.dump_artifact(promo_path, {"entries": {}}, "openace-e2e-promotion")
        rc = governance.main(
            [
                "promote",
                "--id",
                "a",
                "--to",
                "required",
                "--state",
                str(state_path),
                "--promotion",
                str(promo_path),
            ]
        )
        assert rc == 1  # no evidence file -> refuse

    def test_promote_to_required_rejects_flaky_debt(self, tmp_path, capsys):
        state_path = tmp_path / "state.json"
        promo_path = tmp_path / "promotion.json"
        evidence = tmp_path / "ev.json"
        evidence.write_text(
            json.dumps({"effective_samples": 25, "flaky_count": 0, "p95_minutes": 10})
        )
        common.dump_artifact(
            state_path, {"entries": {"a": {"debt": "quarantined-flaky"}}}, "openace-e2e-state"
        )
        common.dump_artifact(promo_path, {"entries": {}}, "openace-e2e-promotion")
        rc = governance.main(
            [
                "promote",
                "--id",
                "a",
                "--to",
                "required",
                "--evidence",
                str(evidence),
                "--state",
                str(state_path),
                "--promotion",
                str(promo_path),
            ]
        )
        assert rc == 1

    def test_promote_to_required_rejects_thin_samples(self, tmp_path, capsys):
        state_path = tmp_path / "state.json"
        promo_path = tmp_path / "promotion.json"
        evidence = tmp_path / "ev.json"
        evidence.write_text(
            json.dumps({"effective_samples": 19, "flaky_count": 0, "p95_minutes": 10})
        )
        common.dump_artifact(
            state_path, {"entries": {"a": {"debt": "stable-pass"}}}, "openace-e2e-state"
        )
        common.dump_artifact(promo_path, {"entries": {}}, "openace-e2e-promotion")
        rc = governance.main(
            [
                "promote",
                "--id",
                "a",
                "--to",
                "required",
                "--evidence",
                str(evidence),
                "--state",
                str(state_path),
                "--promotion",
                str(promo_path),
            ]
        )
        assert rc == 1
        assert "effective runs" in capsys.readouterr().err

    def test_classify_refuses_infra_reference_runs(self, tmp_path, capsys):
        run_files = []
        for idx in range(3):
            p = tmp_path / f"run{idx}.json"
            outcomes = [
                {
                    "nodeid": "tests/e2e/x.py::t",
                    "first_attempt_outcome": "fail",
                    "category": "assertion_failure",
                    "fingerprint": "f1",
                }
            ]
            if idx == 1:
                outcomes[0]["category"] = "infrastructure_error"
            p.write_text(json.dumps({"commit_sha": "s", "contract_key": "k", "outcomes": outcomes}))
            run_files.append(str(p))
        state_path = tmp_path / "state.json"
        rc = governance.main(["classify", "--runs", *run_files, "--state", str(state_path)])
        assert rc == 1
        assert "infrastructure_error" in capsys.readouterr().err

    def test_classify_builds_three_way_state(self, tmp_path):
        run_files = []
        for idx in range(3):
            p = tmp_path / f"run{idx}.json"
            p.write_text(
                json.dumps(
                    {
                        "commit_sha": "s",
                        "contract_key": "k",
                        "outcomes": [
                            {
                                "nodeid": "tests/e2e/x.py::passing",
                                "first_attempt_outcome": "pass",
                                "category": "assertion_failure",
                                "fingerprint": None,
                            },
                            {
                                "nodeid": "tests/e2e/x.py::failing",
                                "first_attempt_outcome": "fail",
                                "category": "assertion_failure",
                                "fingerprint": "f1",
                            },
                        ],
                    }
                )
            )
            run_files.append(str(p))
        state_path = tmp_path / "state.json"
        rc = governance.main(["classify", "--runs", *run_files, "--state", str(state_path)])
        assert rc == 0
        state = common.load_artifact(state_path, "openace-e2e-state")
        assert state["entries"]["tests/e2e/x.py::passing"]["debt"] == "stable-pass"
        assert state["entries"]["tests/e2e/x.py::failing"]["debt"] == "deterministic-known-fail"

    def test_classify_cli_entrypoint_runs_as_script(self, tmp_path):
        run_files = []
        for idx in range(3):
            path = tmp_path / f"run{idx}.json"
            path.write_text(
                json.dumps(
                    {
                        "commit_sha": "s",
                        "contract_key": "k",
                        "outcomes": [
                            {
                                "nodeid": "tests/e2e/x.py::passing",
                                "first_attempt_outcome": "pass",
                                "category": "assertion_failure",
                                "fingerprint": None,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_files.append(str(path))
        state_path = tmp_path / "state.json"

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/e2e/governance.py",
                "classify",
                "--runs",
                *run_files,
                "--state",
                str(state_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["entries"]["tests/e2e/x.py::passing"]["debt"] == "stable-pass"

    def test_remove_deletes_from_both_artifacts(self, tmp_path):
        state_path = tmp_path / "state.json"
        promo_path = tmp_path / "promotion.json"
        common.dump_artifact(
            state_path, {"entries": {"a": {"debt": "stable-pass"}}}, "openace-e2e-state"
        )
        common.dump_artifact(
            promo_path, {"entries": {"a": {"state": "observing"}}}, "openace-e2e-promotion"
        )
        rc = governance.main(
            ["remove", "--id", "a", "--state", str(state_path), "--promotion", str(promo_path)]
        )
        assert rc == 0
        state = common.load_artifact(state_path, "openace-e2e-state")
        promo = common.load_artifact(promo_path, "openace-e2e-promotion")
        assert "a" not in state["entries"] and "a" not in promo["entries"]


class TestInventoryPolicy:
    """tests/e2e files must all carry an inventory disposition (repo gate)."""

    def test_repo_inventory_bidirectional_completeness(self):
        inventory = inventory_mod.load_inventory()
        issues = inventory_mod.validate_inventory(inventory, ROOT)
        assert issues == []

    def test_repo_governance_validation_green(self):
        errors, report = governance.validate_all(project_root=ROOT)
        # P1 lands with empty state/promotion: unclassified defaults are the
        # documented observation-lane start, so validation must be green now
        assert errors == [], errors[:5]
        assert report["counts"]["inventory_entries"] == len(
            inventory_mod.entries(inventory_mod.load_inventory())
        )

    def test_manual_demo_must_declare_executor_none(self, tmp_path):
        (tmp_path / "tests").mkdir()
        base = tmp_path / "tests" / "e2e"
        base.mkdir()
        (base / "conftest.py").write_text("")
        entry = {
            "path": "tests/e2e/conftest.py",
            "mode": "manual-demo",
            "owner": "o",
            "issue": 1,
            "home_lane": "nightly",
            "executor": "pytest",
        }
        inventory = {
            "schema_name": "openace-e2e-inventory",
            "schema_version": 1,
            "entries": [entry],
        }
        issues = inventory_mod.validate_inventory(inventory, tmp_path)
        assert any("executor=none" in i for i in issues)
