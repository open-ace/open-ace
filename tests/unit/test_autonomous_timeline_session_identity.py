from unittest.mock import MagicMock, patch


def test_enrich_milestones_keeps_tracking_id_and_resolves_actual_provider():
    from app.routes import autonomous as autonomous_route

    milestone = {
        "milestone_id": "ms-1",
        "session_id": "track-main-1",
        "review_session_id": "",
        "phase_total_tokens": 321,
        "phase_request_count": 4,
    }

    repo = MagicMock()
    repo.get_milestone_usage_summary.return_value = {
        "ms-1": {
            "llm_session_id": "track-main-1",
            "llm_total_tokens": 321,
            "llm_request_count": 4,
        }
    }

    session_row = MagicMock()
    session_row.cli_session_id = "actual-claude-1"

    with patch.object(autonomous_route, "auto_repo", repo):
        with patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session.return_value = session_row
            mock_sm_cls.return_value = mock_sm

            enriched = autonomous_route._enrich_milestones_with_usage("wf-1", [milestone])

    assert enriched[0]["tracking_llm_session_id"] == "track-main-1"
    assert enriched[0]["llm_session_id"] == "track-main-1"
    assert enriched[0]["actual_llm_session_id"] == "actual-claude-1"
    assert enriched[0]["llm_total_tokens"] == 321
    assert enriched[0]["llm_request_count"] == 4


def test_enrich_milestones_falls_back_to_tracking_id_without_mapping():
    from app.routes import autonomous as autonomous_route

    milestone = {
        "milestone_id": "ms-2",
        "session_id": "track-main-2",
        "review_session_id": "",
    }

    repo = MagicMock()
    repo.get_milestone_usage_summary.return_value = {}

    with patch.object(autonomous_route, "auto_repo", repo):
        with patch("app.modules.workspace.session_manager.SessionManager") as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session.return_value = None
            mock_sm_cls.return_value = mock_sm

            enriched = autonomous_route._enrich_milestones_with_usage("wf-2", [milestone])

    assert enriched[0]["tracking_llm_session_id"] == "track-main-2"
    assert enriched[0]["llm_session_id"] == "track-main-2"
    assert enriched[0]["actual_llm_session_id"] == "track-main-2"
