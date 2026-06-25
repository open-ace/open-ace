"""Tests for QuotaManager read-path coverage of local autonomous sessions.

P0 fix for #1269: check_quota's meter must see local autonomous spend
(local agents bypass the LLM proxy, so their tokens only land in
agent_sessions with workspace_type='local'). These tests pin the
_get_usage_in_range / get_all_quota_statuses queries so a regression
(re-filtering to workspace_type='remote') is caught.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.quota_manager import QuotaManager


def _make_qm(db):
    """Build a QuotaManager wired to a fake db + user_repo."""
    qm = QuotaManager()
    qm.db = db
    qm.user_repo = MagicMock()
    qm.user_repo.get_user_by_id.return_value = {"id": 1, "username": "alice"}
    return qm


class TestGetUsageInRangeCountsLocalAutonomous:
    """_get_usage_in_range must sum local (not just remote) agent_sessions."""

    def test_local_session_tokens_counted(self):
        # fast path (user_daily_stats) returns zero → forces legacy path.
        db = MagicMock()
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},  # user_daily_stats fast path (empty)
            {"tokens": 50000, "requests": 3},  # agent_sessions (local autonomous)
            {"tokens": 0, "requests": 0},  # daily_messages unbound (none)
        ]
        qm = _make_qm(db)

        usage = qm._get_usage_in_range(1, "2026-06-25", "2026-06-25")

        # The legacy agent_sessions query must be the second fetch_one call.
        session_query = db.fetch_one.call_args_list[1].args[0]
        # Must NOT be restricted to workspace_type='remote'; local autonomous
        # sessions (workspace_type='local') must be included.
        assert "workspace_type = 'remote'" not in session_query
        assert "workspace_type IN ('local', 'remote', 'terminal')" in session_query
        assert usage["tokens"] == 50000
        assert usage["requests"] == 3

    def test_remote_only_filter_regression_guard(self):
        """If someone reverts to workspace_type='remote', local tokens vanish."""
        db = MagicMock()
        # Simulate the OLD buggy query: remote filter → returns 0 for a local user.
        db.fetch_one.side_effect = [
            {"tokens": 0, "requests": 0},  # fast path empty
            {"tokens": 0, "requests": 0},  # remote-only would miss local spend
            {"tokens": 0, "requests": 0},  # daily_messages
        ]
        qm = _make_qm(db)
        qm._get_usage_in_range(1, "2026-06-25", "2026-06-25")
        # The query string itself is the contract — assert it includes local.
        session_query = db.fetch_one.call_args_list[1].args[0]
        assert "'local'" in session_query


class TestGetAllQuotaStatusesCountsLocalAutonomous:
    """get_all_quota_statuses (manage page) must also count local sessions."""

    def test_batch_query_includes_local(self):
        users = [{"id": 1, "username": "alice", "daily_token_quota": 1}]
        qm = QuotaManager()
        qm.db = MagicMock()
        qm.user_repo = MagicMock()
        qm.user_repo.get_all_users.return_value = users

        # session_usage_rows, local_usage_rows, alert_rows
        qm.db.fetch_all.side_effect = [
            [{"user_id": 1, "tokens": 80000, "requests": 5}],  # sessions (local autonomous)
            [],  # daily_messages unbound
            [],  # alerts
        ]

        statuses = qm.get_all_quota_statuses()

        # The batch agent_sessions query must include local workspace_type.
        session_query = qm.db.fetch_all.call_args_list[0].args[0]
        assert "workspace_type = 'remote'" not in session_query
        assert "workspace_type IN ('local', 'remote', 'terminal')" in session_query
        assert statuses[0].tokens_used == 80000
