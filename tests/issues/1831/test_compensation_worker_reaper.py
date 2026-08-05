"""Tests for AlertCompensationWorker reaper integration (Issue #1831, #2).

The worker's periodic loop now also advances due webhook deliveries via the
alert notifier reaper, isolated from the failure-queue path so a reaper error
can't break alert-creation compensation.
"""

from unittest.mock import patch

from app.services.alert_compensation_worker import AlertCompensationWorker


def test_process_due_deliveries_invokes_reaper_and_tracks_count():
    worker = AlertCompensationWorker()

    with patch("app.modules.governance.alert_notifier.get_alert_notifier") as mock_get_notifier:
        mock_get_notifier.return_value.process_due_deliveries.return_value = 3
        before = worker._total_deliveries_retried

        attempted = worker._process_due_deliveries()

    assert attempted == 3
    mock_get_notifier.return_value.process_due_deliveries.assert_called_once()
    assert worker._total_deliveries_retried == before + 3


def test_process_due_deliveries_swallows_errors_and_returns_zero():
    """A reaper failure must not propagate (it runs inside the compensation loop)."""
    worker = AlertCompensationWorker()

    with patch(
        "app.modules.governance.alert_notifier.get_alert_notifier",
        side_effect=RuntimeError("boom"),
    ):
        attempted = worker._process_due_deliveries()

    assert attempted == 0


def test_status_reports_new_stat():
    worker = AlertCompensationWorker()
    stats = worker.get_status()["stats"]
    assert "total_deliveries_retried" in stats
