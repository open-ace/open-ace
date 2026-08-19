"""
Tests for Issue #2750: Audit analysis SQL aggregation, completeness metadata,
days validation, and security-score de-duplication.

These tests verify that:
1. analyze_patterns uses SQL aggregation instead of Python object loading.
2. total_events reflects the real database count (not truncated by limit).
3. Completeness metadata fields are present in the response.
4. Anomaly detectors use specialized SQL instead of loading 10,000 objects.
5. generate_security_score accepts precomputed_anomalies to avoid re-detection.
6. Route-level days parameter validation works correctly.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.compliance.audit import AnomalyDetection, AuditAnalyzer
from app.modules.governance.audit_logger import AuditLogger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# adapt_sql is a pass-through for SQLite; we patch it to be a no-op since
# the test environment has a PostgreSQL DATABASE_URL but we run real SQLite.
def _noop_adapt(q: str) -> str:
    """Pass-through for adapt_sql in tests (SQLite uses ? placeholders)."""
    return q


def _create_sqlite_db():
    """Create an in-memory SQLite database with the audit_logs table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            resource_type TEXT DEFAULT '',
            resource_id TEXT,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            session_id TEXT,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            tenant_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            tenant_id INTEGER
        )
    """)
    conn.commit()
    return conn


def _seed_logs(conn, count, base_time=None, actions=None, user_ids=None):
    """Insert *count* audit log rows with rotating actions/user_ids."""
    if base_time is None:
        base_time = datetime(2026, 6, 1, 12, 0, 0)
    if actions is None:
        actions = ["login", "logout", "data_view", "user_create", "login_failed"]
    if user_ids is None:
        user_ids = [1, 2, 3, 4, 5]

    rows = []
    for i in range(count):
        ts = base_time + timedelta(seconds=i * 60)
        action = actions[i % len(actions)]
        user_id = user_ids[i % len(user_ids)]
        rows.append(
            (
                ts.isoformat(),
                user_id,
                f"user{user_id}",
                action,
                "info",
                "",
                None,
                None,
                None,
                None,
                None,
                1,
                None,
                None,
            )
        )
    conn.executemany(
        """INSERT INTO audit_logs
           (timestamp, user_id, username, action, severity, resource_type,
            resource_id, details, ip_address, user_agent, session_id,
            success, error_message, tenant_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def _make_analyzer_with_conn(conn):
    """Create an AuditAnalyzer backed by a real SQLite connection."""
    mock_db = MagicMock()
    mock_db.fetch_all = MagicMock(return_value=[])
    mock_db.fetch_one = MagicMock(return_value={"count": 0})

    logger = AuditLogger(db=mock_db)

    # Patch _resolve_tenant_id and _normalize_tenant_id to return None (no tenant scope)
    logger._resolve_tenant_id = MagicMock(return_value=None)
    logger._normalize_tenant_id = MagicMock(return_value=None)

    analyzer = AuditAnalyzer(audit_logger=logger)
    return analyzer, conn


# Shared patches for all SQL-based tests
_SQL_PATCHES = [
    patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt),
    patch("app.modules.compliance.audit.is_postgresql", return_value=False),
]


# ---------------------------------------------------------------------------
# Tests: analyze_patterns SQL aggregation
# ---------------------------------------------------------------------------


class TestAnalyzePatternsSQLAggregation:
    """Test that analyze_patterns uses SQL aggregation."""

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_total_events_reflects_real_count(self, _mock_pg, _mock_adapt, mock_get_conn):
        """total_events must equal COUNT(*), not be capped at 10,000."""
        conn = _create_sqlite_db()
        # Insert 15,000 logs — more than the old limit of 10,000
        _seed_logs(conn, 15000)
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        assert result["total_events"] == 15000
        assert result["matching_events"] == 15000
        assert result["analyzed_events"] == 15000

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_completeness_metadata_present(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Response must include completeness metadata fields."""
        conn = _create_sqlite_db()
        _seed_logs(conn, 100)
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        assert "matching_events" in result
        assert "analyzed_events" in result
        assert "truncated" in result
        assert "coverage_ratio" in result
        assert "oldest_analyzed_at" in result
        assert result["truncated"] is False
        assert result["coverage_ratio"] == 1.0

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_distributions_cover_full_range(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Hourly/daily/action distributions must cover all 15,000 logs."""
        conn = _create_sqlite_db()
        _seed_logs(conn, 15000)
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        # Sum of hourly distribution must equal total
        hourly_sum = sum(result["hourly_distribution"].values())
        assert hourly_sum == 15000

        # Sum of daily distribution must equal total
        daily_sum = sum(result["daily_distribution"].values())
        assert daily_sum == 15000

        # Sum of action distribution must equal total
        action_sum = sum(result["action_distribution"].values())
        assert action_sum == 15000

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_top_users_and_unique_users(self, _mock_pg, _mock_adapt, mock_get_conn):
        """top_users and unique_users must be computed correctly."""
        conn = _create_sqlite_db()
        _seed_logs(conn, 100, user_ids=[10, 20, 30])
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        assert result["unique_users"] == 3
        assert len(result["top_users"]) == 3
        # Each user has about 33-34 events
        for uid, cnt in result["top_users"]:
            assert uid in (10, 20, 30)
            assert cnt > 0

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_login_hourly_distribution(self, _mock_pg, _mock_adapt, mock_get_conn):
        """login_hourly_distribution must only count login actions."""
        conn = _create_sqlite_db()
        # Insert 50 logins + 50 other actions
        _seed_logs(conn, 100, actions=["login", "data_view"])
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        login_sum = sum(result["login_hourly_distribution"].values())
        assert login_sum == 50  # Half are "login" actions

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_oldest_analyzed_at_populated(self, _mock_pg, _mock_adapt, mock_get_conn):
        """oldest_analyzed_at must reflect the earliest log timestamp."""
        conn = _create_sqlite_db()
        base = datetime(2026, 5, 15, 8, 30, 0)
        _seed_logs(conn, 10, base_time=base)
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 5, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        assert result["oldest_analyzed_at"] is not None
        assert "2026-05-15" in result["oldest_analyzed_at"]

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_backward_compatible_total_events(self, _mock_pg, _mock_adapt, mock_get_conn):
        """total_events must still be present for backward compatibility."""
        conn = _create_sqlite_db()
        _seed_logs(conn, 50)
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        result = analyzer.analyze_patterns(start_time=start, end_time=end)

        assert "total_events" in result
        assert result["total_events"] == 50
        assert result["total_events"] == result["matching_events"]


# ---------------------------------------------------------------------------
# Tests: Anomaly detection SQL-based
# ---------------------------------------------------------------------------


class TestAnomalyDetectionSQL:
    """Test that anomaly detectors use SQL aggregation."""

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_failed_login_detection(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Failed login detector must find users exceeding threshold."""
        conn = _create_sqlite_db()
        # Insert 20 failed logins for user 1 (threshold default = 5)
        base = datetime(2026, 6, 1, 10, 0, 0)
        rows = []
        for i in range(20):
            ts = base + timedelta(minutes=i)
            rows.append(
                (
                    ts.isoformat(),
                    1,
                    "user1",
                    "login_failed",
                    "warning",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    "invalid password",
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 7, 1, 0, 0, 0)

        anomaly = analyzer._detect_failed_login_anomaly(start, end)

        assert anomaly is not None
        assert anomaly.anomaly_type == "excessive_failed_logins"
        assert 1 in anomaly.affected_users
        assert anomaly.occurrences == 20

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_failed_login_below_threshold(self, _mock_pg, _mock_adapt, mock_get_conn):
        """No anomaly when failed logins are below threshold."""
        conn = _create_sqlite_db()
        # Insert only 3 failed logins (threshold default = 5)
        base = datetime(2026, 6, 1, 10, 0, 0)
        rows = []
        for i in range(3):
            ts = base + timedelta(minutes=i)
            rows.append(
                (
                    ts.isoformat(),
                    1,
                    "user1",
                    "login_failed",
                    "warning",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    None,
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 7, 1, 0, 0, 0)

        anomaly = analyzer._detect_failed_login_anomaly(start, end)
        assert anomaly is None

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_rapid_activity_detection(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Rapid activity detector must find users exceeding hourly threshold."""
        conn = _create_sqlite_db()
        # Insert 60 actions by user 1 within one hour (threshold default = 50)
        base = datetime(2026, 6, 1, 10, 0, 0)
        rows = []
        for i in range(60):
            ts = base + timedelta(seconds=i)
            rows.append(
                (
                    ts.isoformat(),
                    1,
                    "user1",
                    "data_view",
                    "info",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    1,
                    None,
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 7, 1, 0, 0, 0)

        anomalies = analyzer._detect_rapid_activity_anomaly(start, end)

        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == "rapid_activity"
        assert 1 in anomalies[0].affected_users
        assert anomalies[0].occurrences == 60

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_off_hours_detection(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Off-hours detector must find users active between 22:00-06:00."""
        conn = _create_sqlite_db()
        # Insert 15 actions at 23:00 (off-hours) for user 1 (threshold default = 10)
        base = datetime(2026, 6, 1, 23, 0, 0)
        rows = []
        for i in range(15):
            ts = base + timedelta(minutes=i)
            rows.append(
                (
                    ts.isoformat(),
                    1,
                    "user1",
                    "data_view",
                    "info",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    1,
                    None,
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 7, 1, 0, 0, 0)

        anomalies = analyzer._detect_off_hours_anomaly(start, end)

        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == "off_hours_activity"
        assert 1 in anomalies[0].affected_users

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_action_pattern_role_changes(self, _mock_pg, _mock_adapt, mock_get_conn):
        """Action pattern detector must detect frequent role changes."""
        conn = _create_sqlite_db()
        # Insert 10 role changes (threshold default = 5)
        base = datetime(2026, 6, 1, 10, 0, 0)
        rows = []
        for i in range(10):
            ts = base + timedelta(minutes=i)
            rows.append(
                (
                    ts.isoformat(),
                    (i % 3) + 1,
                    f"user{(i % 3) + 1}",
                    "user_role_change",
                    "info",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    1,
                    None,
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 7, 1, 0, 0, 0)

        anomalies = analyzer._detect_action_pattern_anomaly(start, end)

        role_change_anomalies = [a for a in anomalies if a.anomaly_type == "frequent_role_changes"]
        assert len(role_change_anomalies) == 1
        assert role_change_anomalies[0].occurrences == 10


# ---------------------------------------------------------------------------
# Tests: generate_security_score accepts precomputed_anomalies
# ---------------------------------------------------------------------------


class TestSecurityScorePrecomputedAnomalies:
    """Test that security score can use precomputed anomalies."""

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_precomputed_anomalies_used(self, _mock_pg, _mock_adapt, mock_get_conn):
        """generate_security_score must use precomputed_anomalies if provided."""
        conn = _create_sqlite_db()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)

        precomputed = [
            AnomalyDetection(
                anomaly_type="excessive_failed_logins",
                severity="high",
                description="test",
                affected_users=[1],
                occurrences=100,
                first_seen=datetime(2026, 6, 1),
                last_seen=datetime(2026, 6, 30),
                details={},
            ),
        ]

        # Patch detect_anomalies to ensure it's NOT called
        with patch.object(analyzer, "detect_anomalies") as mock_detect:
            result = analyzer.generate_security_score(
                start_time=datetime(2026, 6, 1),
                end_time=datetime(2026, 7, 1),
                precomputed_anomalies=precomputed,
            )
            mock_detect.assert_not_called()

        assert result["anomaly_count"] == 1
        assert result["high_severity_count"] == 1
        assert result["score"] < 100  # Should be deducted

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_no_precomputed_calls_detect(self, _mock_pg, _mock_adapt, mock_get_conn):
        """generate_security_score without precomputed must call detect_anomalies."""
        conn = _create_sqlite_db()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)

        with patch.object(analyzer, "detect_anomalies", return_value=[]) as mock_detect:
            result = analyzer.generate_security_score(
                start_time=datetime(2026, 6, 1),
                end_time=datetime(2026, 7, 1),
            )
            mock_detect.assert_called_once()

        assert result["anomaly_count"] == 0
        assert result["score"] == 100


# ---------------------------------------------------------------------------
# Tests: Route-level days validation
# ---------------------------------------------------------------------------


class TestDaysValidation:
    """Test the _validate_days helper in compliance routes."""

    def _make_request_with_args(self, args):
        """Create a mock request with given args dict."""
        mock_request = MagicMock()
        mock_request.args = args
        return mock_request

    def test_default_value(self):
        """Missing days parameter returns default."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({})):
            assert _validate_days(default=30) == 30

    def test_valid_value(self):
        """Valid days within range is returned as-is."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "14"})):
            assert _validate_days(default=30) == 14

    def test_negative_clamped(self):
        """Negative days is clamped to minimum."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "-1"})):
            assert _validate_days(default=30) == 1

    def test_zero_clamped(self):
        """Zero days is clamped to minimum."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "0"})):
            assert _validate_days(default=30) == 1

    def test_over_max_clamped(self):
        """Days exceeding maximum is clamped."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "9999"})):
            assert _validate_days(default=30, max_val=365) == 365

    def test_non_integer_returns_default(self):
        """Non-integer days returns default."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "abc"})):
            assert _validate_days(default=30) == 30

    def test_boundary_values(self):
        """Test boundary values: 1, 365."""
        from app.routes.compliance import _validate_days

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "1"})):
            assert _validate_days(default=30) == 1

        with patch("app.routes.compliance.request", self._make_request_with_args({"days": "365"})):
            assert _validate_days(default=30) == 365


# ---------------------------------------------------------------------------
# Tests: Large dataset - anomalies at beginning of time range
# ---------------------------------------------------------------------------


class TestLargeDatasetAnomaliesNotIgnored:
    """
    Verify that anomalies placed at the beginning of the time range are
    detected even when total logs exceed the old 10,000 limit.
    """

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_early_anomalies_detected(self, _mock_pg, _mock_adapt, mock_get_conn):
        """
        Insert 20,000 logs: 20 failed logins at the very beginning (day 1),
        then 19,980 normal logs.  The old DESC LIMIT approach would miss the
        failed logins entirely.  The SQL aggregation must find them.
        """
        conn = _create_sqlite_db()

        # 20 failed logins at the beginning
        base = datetime(2026, 6, 1, 0, 0, 0)
        rows = []
        for i in range(20):
            ts = base + timedelta(minutes=i)
            rows.append(
                (
                    ts.isoformat(),
                    99,
                    "attacker",
                    "login_failed",
                    "warning",
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    "invalid password",
                    None,
                )
            )
        conn.executemany(
            """INSERT INTO audit_logs
               (timestamp, user_id, username, action, severity, resource_type,
                resource_id, details, ip_address, user_agent, session_id,
                success, error_message, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

        # 19,980 normal logs after the failed logins (no login_failed actions)
        _seed_logs(
            conn,
            19980,
            base_time=base + timedelta(hours=1),
            actions=["login", "logout", "data_view", "user_create"],
        )
        conn.commit()

        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)
        start = datetime(2026, 6, 1, 0, 0, 0)
        end = datetime(2026, 8, 1, 0, 0, 0)

        # The failed login anomaly must be detected
        anomaly = analyzer._detect_failed_login_anomaly(start, end)
        assert anomaly is not None
        assert 99 in anomaly.affected_users
        assert anomaly.occurrences == 20

        # Total events must be 20,000
        result = analyzer.analyze_patterns(start_time=start, end_time=end)
        assert result["total_events"] == 20000


# ---------------------------------------------------------------------------
# Tests: detect_anomalies orchestration
# ---------------------------------------------------------------------------


class TestDetectAnomaliesOrchestration:
    """Test that detect_anomalies calls all sub-detectors."""

    @patch("app.modules.compliance.audit.get_db_connection")
    @patch("app.modules.compliance.audit.adapt_sql", side_effect=_noop_adapt)
    @patch("app.modules.compliance.audit.is_postgresql", return_value=False)
    def test_all_detectors_called(self, _mock_pg, _mock_adapt, mock_get_conn):
        """detect_anomalies must call all four sub-detectors."""
        conn = _create_sqlite_db()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        analyzer, _ = _make_analyzer_with_conn(conn)

        with (
            patch.object(analyzer, "_detect_failed_login_anomaly", return_value=None) as m1,
            patch.object(analyzer, "_detect_rapid_activity_anomaly", return_value=[]) as m2,
            patch.object(analyzer, "_detect_off_hours_anomaly", return_value=[]) as m3,
            patch.object(analyzer, "_detect_action_pattern_anomaly", return_value=[]) as m4,
        ):
            start = datetime(2026, 6, 1)
            end = datetime(2026, 7, 1)
            result = analyzer.detect_anomalies(start_time=start, end_time=end)

            m1.assert_called_once_with(start, end)
            m2.assert_called_once_with(start, end)
            m3.assert_called_once_with(start, end)
            m4.assert_called_once_with(start, end)
            assert result == []
