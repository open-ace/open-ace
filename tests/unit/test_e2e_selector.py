"""Selector quadrant-table unit tests (Issue #2491 §互斥 lane selection).

The quadrant table in scripts/e2e/selector.py is the normative definition;
these tests assert cell-by-cell, including the N2 determinism cells (active
quarantine is OUTSIDE the PR/nightly closure) and the N3 cell
(required+known-fail is invalid on PR with an atomic-disposition demand).
"""

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

selector = importlib.import_module("e2e_governance_pkg.selector")


def item(
    id: str,
    *,
    home: str = "pr-critical",
    promotion: str = "observing",
    debt: str = "unclassified",
    quarantine: dict | None = None,
) -> selector.Item:
    return selector.Item(
        id=id, home_lane=home, promotion=promotion, debt=debt, quarantine=quarantine
    )


ACTIVE_QUARANTINE = {"owner": "a", "issue": 2491, "expiry": "2099-01-01T00:00:00+00:00"}
EXPIRED_QUARANTINE = {"owner": "a", "issue": 2491, "expiry": "2000-01-01T00:00:00+00:00"}


class TestClassifyItem:
    """One assertion per quadrant-table cell (single deterministic result)."""

    @pytest.mark.parametrize(
        "event,expected", [("pr", "normal"), ("nightly", "normal"), ("weekly", "normal")]
    )
    def test_required_stable_pass_pr_home(self, event, expected):
        got, _ = selector.classify_item(event, item("a", promotion="required", debt="stable-pass"))
        assert got == expected

    def test_required_known_fail_on_pr_is_invalid_with_n3_reason(self):
        got, reason = selector.classify_item(
            "pr", item("a", promotion="required", debt="deterministic-known-fail")
        )
        assert got == "invalid"
        assert "atomically demote or rehome" in reason

    @pytest.mark.parametrize("event", ["nightly", "weekly"])
    def test_required_known_fail_scheduled_is_normal(self, event):
        got, _ = selector.classify_item(
            event, item("a", promotion="required", debt="deterministic-known-fail")
        )
        assert got == "normal"

    def test_required_recovering_on_pr_is_invalid(self):
        got, _ = selector.classify_item("pr", item("a", promotion="required", debt="recovering"))
        assert got == "invalid"

    @pytest.mark.parametrize("event", ["nightly", "weekly"])
    def test_required_recovering_scheduled_is_normal(self, event):
        got, _ = selector.classify_item(event, item("a", promotion="required", debt="recovering"))
        assert got == "normal"

    @pytest.mark.parametrize("event", ["pr", "nightly", "weekly"])
    def test_required_active_flaky_is_invalid_everywhere_never_probe(self, event):
        got, reason = selector.classify_item(
            event,
            item("a", promotion="required", debt="quarantined-flaky", quarantine=ACTIVE_QUARANTINE),
        )
        assert got == "invalid"
        assert "required + quarantined-flaky" in reason

    @pytest.mark.parametrize(
        "debt", ["stable-pass", "deterministic-known-fail", "recovering", "unclassified"]
    )
    @pytest.mark.parametrize("event", ["pr", "nightly", "weekly"])
    def test_observing_candidate_is_advisory_unless_active_flaky(self, debt, event):
        # N2: known-fail/recovering observing items are advisory (issue text),
        # NOT invalid - legacy debt must not permanently redden the PR gate.
        got, _ = selector.classify_item(event, item("a", promotion="observing", debt=debt))
        assert got == "advisory"

    def test_active_flaky_observing_is_probe_on_weekly_only(self):
        it = item(
            "a", promotion="observing", debt="quarantined-flaky", quarantine=ACTIVE_QUARANTINE
        )
        assert selector.classify_item("weekly", it)[0] == "probe"

    def test_expired_quarantine_is_invalid_on_every_event(self):
        it = item("a", debt="quarantined-flaky", quarantine=EXPIRED_QUARANTINE)
        for event in ("pr", "nightly", "weekly"):
            assert selector.classify_item(event, it)[0] == "invalid"

    def test_unknown_debt_or_promotion_is_invalid(self):
        assert selector.classify_item("pr", item("a", debt="bogus"))[0] == "invalid"
        assert selector.classify_item("pr", item("a", promotion="bogus"))[0] == "invalid"

    def test_required_unclassified_is_invalid(self):
        got, _ = selector.classify_item("pr", item("a", promotion="required", debt="unclassified"))
        assert got == "invalid"


class TestClosure:
    """N2: buckets are pairwise disjoint and exactly cover the applicable set."""

    def _all_items(self) -> list[selector.Item]:
        return [
            item("pr-required-pass", home="pr-critical", promotion="required", debt="stable-pass"),
            item(
                "pr-required-knownfail",
                home="pr-critical",
                promotion="required",
                debt="deterministic-known-fail",
            ),
            item(
                "pr-required-flaky",
                home="pr-critical",
                promotion="required",
                debt="quarantined-flaky",
                quarantine=ACTIVE_QUARANTINE,
            ),
            item(
                "pr-observing-flaky",
                home="pr-critical",
                debt="quarantined-flaky",
                quarantine=ACTIVE_QUARANTINE,
            ),
            item("pr-observing-knownfail", home="pr-critical", debt="deterministic-known-fail"),
            item("pr-observing-pass", home="pr-critical", debt="stable-pass"),
            item("night-observing", home="nightly", debt="stable-pass"),
            item(
                "night-required-knownfail",
                home="nightly",
                promotion="required",
                debt="deterministic-known-fail",
            ),
            item("week-observing", home="weekly", debt="stable-pass"),
        ]

    def test_pr_closure(self):
        selection = selector.select("pr", self._all_items())
        assert selection.normal == ["pr-required-pass"]
        assert sorted(selection.advisory) == ["pr-observing-knownfail", "pr-observing-pass"]
        assert set(selection.invalid) == {"pr-required-knownfail"}
        # active quarantines are OUTSIDE the PR closure entirely (N2), and
        # required+flaky never falls into probe
        assert selection.probe == []
        assert "pr-required-flaky" in selection.not_applicable
        assert "pr-observing-flaky" in selection.not_applicable
        applicable, _ = selector.applicable_ids("pr", self._all_items())
        assert selection.closure_errors(applicable) == []

    def test_nightly_closure_reruns_pr_critical(self):
        selection = selector.select("nightly", self._all_items())
        assert set(selection.normal) == {
            "pr-required-pass",
            "pr-required-knownfail",  # scheduled lanes allow required+known-fail
            "night-required-knownfail",
        }
        # nothing invalid on nightly: known-fail is legal here
        assert selection.invalid == {}
        assert selection.probe == []
        assert "week-observing" in selection.not_applicable
        applicable, _ = selector.applicable_ids("nightly", self._all_items())
        assert selection.closure_errors(applicable) == []

    def test_weekly_closure_probe_is_only_cross_lane_redirect(self):
        selection = selector.select("weekly", self._all_items())
        # required+flaky NEVER drops into probe, even on weekly (rule 1 wins)
        assert set(selection.probe) == {"pr-observing-flaky"}
        assert set(selection.invalid) == {"pr-required-flaky"}
        # required+known-fail stays normal on nightly (previous test); on
        # weekly its home-lane items are simply not applicable (not re-run)
        assert selection.normal == []
        assert "night-required-knownfail" in selection.not_applicable
        assert "week-observing" in selection.advisory
        # nightly/pr-critical home items without quarantine are not applicable
        assert "night-observing" in selection.not_applicable
        applicable, _ = selector.applicable_ids("weekly", self._all_items())
        assert selection.closure_errors(applicable) == []

    def test_closure_errors_detect_missing_and_overlap(self):
        selection = selector.Selection(event="pr", normal=["a"], advisory=["a"])
        assert selection.closure_errors({"a", "b"}) != []

    def test_select_rejects_duplicate_ids(self):
        from e2e_governance_pkg.common import GovernanceError

        with pytest.raises(GovernanceError):
            selector.select("pr", [item("a"), item("a")])

    def test_manual_demo_items_never_selected(self):
        inventory = {
            "schema_name": "openace-e2e-inventory",
            "schema_version": 1,
            "entries": [
                {
                    "path": "tests/e2e/browser/test_login.py",
                    "mode": "manual-demo",
                    "home_lane": "pr-critical",
                    "executor": "none",
                },
                {
                    "path": "tests/e2e/browser/test_nav.py",
                    "mode": "pytest-automated",
                    "home_lane": "pr-critical",
                    "executor": "pytest",
                },
            ],
        }
        items = selector.build_items(
            inventory,
            {"entries": {}},
            {"entries": {}},
            ["tests/e2e/browser/test_login.py::test_x", "tests/e2e/browser/test_nav.py::test_y"],
        )
        assert [i.id for i in items] == ["tests/e2e/browser/test_nav.py::test_y"]

    def test_standalone_entries_are_selectable(self):
        inventory = {
            "schema_name": "openace-e2e-inventory",
            "schema_version": 1,
            "entries": [
                {
                    "path": "tests/e2e/remote/e2e_x.py",
                    "mode": "standalone-automated",
                    "home_lane": "weekly",
                    "executor": "standalone",
                    "entry_ids": ["standalone::tests/e2e/remote/e2e_x.py"],
                },
            ],
        }
        items = selector.build_items(inventory, {"entries": {}}, {"entries": {}}, [])
        assert [i.id for i in items] == ["standalone::tests/e2e/remote/e2e_x.py"]
        selection = selector.select("weekly", items)
        assert selection.advisory == ["standalone::tests/e2e/remote/e2e_x.py"]

    def test_standalone_files_do_not_also_expand_manifest_nodeids(self):
        inventory = {
            "schema_name": "openace-e2e-inventory",
            "schema_version": 1,
            "entries": [
                {
                    "path": "tests/e2e/remote/e2e_x.py",
                    "mode": "standalone-automated",
                    "home_lane": "nightly",
                    "executor": "standalone",
                    "entry_ids": ["standalone::tests/e2e/remote/e2e_x.py"],
                },
            ],
        }
        items = selector.build_items(
            inventory,
            {"entries": {}},
            {"entries": {}},
            ["tests/e2e/remote/e2e_x.py::test_helper"],
        )
        assert [i.id for i in items] == ["standalone::tests/e2e/remote/e2e_x.py"]
