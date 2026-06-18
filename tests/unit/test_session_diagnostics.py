"""Unit tests for the per-session-average root-cause classifier.

See app/utils/session_diagnostics.py. The classifier is a pure function and
the testable subject of the data-gathering gate (replaces the earlier
"test non-existent logic" contradiction).
"""

import pytest

from app.utils.session_diagnostics import classify_session_avg_rootcause


class TestClassifySessionAvgRootcause:
    def test_scenario_b_numerator_near_zero(self):
        # daily_stats aggregates are empty/stale -> fixing denominator is useless
        sample = {
            "total_tokens": 0,
            "total_messages": 0,
            "unique_days": 30,
            "unique_tools": 3,
            "distinct": 0,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "b"
        assert verdict["proceed"] is False

    def test_scenario_d_messages_lack_session_ids(self):
        # activity exists but distinct ~0 -> fix would still show 0
        sample = {
            "total_tokens": 5_000_000,
            "total_messages": 100_000,
            "unique_days": 30,
            "unique_tools": 3,
            "distinct": 0,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "d"
        assert verdict["proceed"] is False

    def test_scenario_d_takes_precedence_over_a_when_distinct_zero(self):
        # Without the (d) guard, distinct=0 would make approx > distinct*K
        # trivially true and mis-classify as (a).
        sample = {
            "total_tokens": 1_000,
            "total_messages": 50,
            "unique_days": 30,
            "unique_tools": 3,  # approx = 90
            "distinct": 0,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "d"
        assert verdict["proceed"] is False

    def test_scenario_a_approx_inflated_fix_correct(self):
        # tool_name not normalized -> unique_tools inflated; real distinct smaller
        sample = {
            "total_tokens": 5_000_000,
            "total_messages": 100_000,
            "unique_days": 30,
            "unique_tools": 200,  # approx = 6000 >> distinct=500
            "distinct": 500,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "a"
        assert verdict["proceed"] is True

    def test_scenario_c_real_much_larger_than_approx(self):
        # requirement's self-described case: real >> approx -> fix makes it worse
        sample = {
            "total_tokens": 5_000_000,
            "total_messages": 100_000,
            "unique_days": 30,
            "unique_tools": 3,  # approx = 90 << distinct=5000
            "distinct": 5000,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "c"
        assert verdict["proceed"] is False

    def test_unknown_when_inconclusive(self):
        # approx and distinct within an order of magnitude
        sample = {
            "total_tokens": 5_000_000,
            "total_messages": 100_000,
            "unique_days": 30,
            "unique_tools": 3,  # approx = 90, distinct = 200 -> ratio ~4.5x
            "distinct": 200,
        }
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] == "unknown"
        assert verdict["proceed"] is False

    def test_thresholds_are_parameterizable(self):
        # approx=500 sits between distinct*K(=3)=300 and distinct*K(=10)=1000,
        # so it is "unknown" at the default K but "a" with a tighter K=3.
        sample = {
            "total_tokens": 5_000_000,
            "total_messages": 100_000,
            "unique_days": 10,
            "unique_tools": 50,  # approx = 500
            "distinct": 100,
        }
        assert classify_session_avg_rootcause(sample)["class"] == "unknown"
        verdict = classify_session_avg_rootcause(sample, ratio_k=3.0)
        assert verdict["class"] == "a"
        assert verdict["proceed"] is True

    @pytest.mark.parametrize("missing", ["total_tokens", "unique_days", "distinct"])
    def test_missing_keys_treated_as_zero(self, missing):
        sample = {
            "total_tokens": 1_000_000,
            "total_messages": 10_000,
            "unique_days": 30,
            "unique_tools": 5,
            "distinct": 100,
        }
        del sample[missing]
        # Should not raise; classifies deterministically
        verdict = classify_session_avg_rootcause(sample)
        assert verdict["class"] in {"a", "b", "c", "d", "unknown"}
