"""
Tests for issue #2749 – anomaly status identity includes time bucket.

Verifies that:
  - The same user + anomaly_type in different hours produces different anomaly_ids.
  - The same user + anomaly_type in different days produces different anomaly_ids.
  - Different tenants with same type/users/time produce different anomaly_ids.
  - Status updates target a single anomaly_id (no cross-instance pollution).
  - generate_security_score respects pending/processed/ignored.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.modules.compliance.audit import AnomalyDetection, AuditAnalyzer, make_anomaly_id


# ──────────────────────────────────────────────────────────────
# make_anomaly_id
# ──────────────────────────────────────────────────────────────
class TestMakeAnomalyId:
    def test_same_user_different_hours_yields_different_ids(self):
        id_h1 = make_anomaly_id("rapid_activity", [42], "2026-08-19 10")
        id_h2 = make_anomaly_id("rapid_activity", [42], "2026-08-19 11")
        assert id_h1 != id_h2, "Same user/type but different hour buckets must have distinct IDs"

    def test_same_user_different_days_yields_different_ids(self):
        id_d1 = make_anomaly_id("excessive_failed_logins", [42], "2026-08-19")
        id_d2 = make_anomaly_id("excessive_failed_logins", [42], "2026-08-20")
        assert id_d1 != id_d2, "Same user/type but different day buckets must have distinct IDs"

    def test_different_tenants_yields_different_ids(self):
        id_t1 = make_anomaly_id("rapid_activity", [42], "2026-08-19 10", tenant_id=1)
        id_t2 = make_anomaly_id("rapid_activity", [42], "2026-08-19 10", tenant_id=2)
        assert id_t1 != id_t2, "Different tenants must have distinct anomaly IDs"

    def test_stable_across_calls(self):
        id_a = make_anomaly_id("rapid_activity", [42, 7], "2026-08-19 10", tenant_id=3)
        id_b = make_anomaly_id("rapid_activity", [7, 42], "2026-08-19 10", tenant_id=3)
        assert id_a == id_b, "User list ordering must not affect the anomaly_id (sorted)"

    def test_returns_hex_string(self):
        aid = make_anomaly_id("rapid_activity", [1], "2026-08-19 10")
        assert isinstance(aid, str)
        assert len(aid) == 24
        int(aid, 16)  # must be valid hex


# ──────────────────────────────────────────────────────────────
# detect_anomalies – each hour of rapid activity is unique
# ──────────────────────────────────────────────────────────────
class TestDetectAnomaliesIdentity:
    def test_new_anomaly_does_not_inherit_old_status(self):
        """Two anomalies with same type+user but different hours must
        have different anomaly_ids, so a status update on one
        cannot affect the other."""
        id1 = make_anomaly_id("rapid_activity", [42], "2026-08-19 10")
        id2 = make_anomaly_id("rapid_activity", [42], "2026-08-19 11")

        statuses = {id1: {"status": "processed"}}
        # Simulate looking up status for anomaly2
        assert statuses.get(id2) is None, "New anomaly must not inherit old status"


# ──────────────────────────────────────────────────────────────
# generate_security_score – respects pending/processed/ignored
# ──────────────────────────────────────────────────────────────
class TestSecurityScoreRespectsStatus:
    def test_processed_anomaly_excluded_from_deduction(self):
        analyzer = AuditAnalyzer.__new__(AuditAnalyzer)
        analyzer.rapid_action_threshold = 2
        analyzer.failed_login_threshold = 5
        analyzer.off_hours_threshold = 10
        analyzer.role_change_threshold = 5
        analyzer.permission_change_threshold = 10

        a1 = AnomalyDetection(
            anomaly_type="rapid_activity",
            severity="medium",
            description="test 1",
            affected_users=[1],
            occurrences=10,
            first_seen=datetime(2026, 8, 19, 10),
            last_seen=datetime(2026, 8, 19, 11),
            details={},
            anomaly_id="id_1",
        )
        a2 = AnomalyDetection(
            anomaly_type="rapid_activity",
            severity="medium",
            description="test 2",
            affected_users=[1],
            occurrences=10,
            first_seen=datetime(2026, 8, 19, 11),
            last_seen=datetime(2026, 8, 19, 12),
            details={},
            anomaly_id="id_2",
        )

        analyzer.audit_logger = MagicMock()
        with patch.object(analyzer, "detect_anomalies", return_value=[a1, a2]):
            # No statuses → both pending → deductions apply
            score_all_pending = analyzer.generate_security_score(anomaly_statuses={})

            # a1 is processed → only a2 contributes
            score_one_processed = analyzer.generate_security_score(
                anomaly_statuses={"id_1": {"status": "processed"}}
            )

            # Both processed → no deductions → score = 100
            score_all_processed = analyzer.generate_security_score(
                anomaly_statuses={
                    "id_1": {"status": "processed"},
                    "id_2": {"status": "processed"},
                }
            )

        assert (
            score_all_pending["score"] < score_one_processed["score"]
        ), "Processing one anomaly should improve the score"
        assert score_all_processed["score"] == 100, "All processed anomalies → score should be 100"
        assert score_all_processed["pending_count"] == 0
        assert score_all_processed["processed_count"] == 2
