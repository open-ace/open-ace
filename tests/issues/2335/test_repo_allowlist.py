from app.repositories.autonomous_repo import AutonomousWorkflowRepository

ALLOWED_WORKFLOW_FIELDS = AutonomousWorkflowRepository.ALLOWED_WORKFLOW_FIELDS


def test_new_verification_columns_are_writeable():
    for col in [
        "verification_status",
        "verification_merge_sha",
        "verification_started_at",
        "verification_completed_at",
        "verification_attempt",
        "verification_report",
        "issue_acceptance_snapshot",
        "issue_acceptance_hash",
        "verified_by",
        "verification_session_id",
        "issue_closed_by_workflow_at",
    ]:
        assert col in ALLOWED_WORKFLOW_FIELDS, f"{col} must be in the update allowlist"
