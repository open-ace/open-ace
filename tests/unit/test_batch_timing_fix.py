"""Model/repository regression tests for the batch timing fix (Issue #1552).

Background: batch workflows pin a shared ``base_commit_sha`` so concurrently
created branches cannot end up on diverged base commits. The behavioral
branches (locking, timing-issue detection) are asserted in
``tests/unit/test_phase_pr_review.py``; the assertion-free placeholder
tests from the original fix were removed by #2429 batch 5 (34-stub governance).
"""

import pytest

from app.modules.workspace.autonomous.models import AutonomousWorkflow
from app.repositories.autonomous_repo import AutonomousWorkflowRepository

pytestmark = [pytest.mark.regression, pytest.mark.issue(1552)]


class TestModelsUpdate:
    """Verify AutonomousWorkflow model includes base_commit_sha."""

    def test_model_has_base_commit_sha_field(self):
        """Verify AutonomousWorkflow dataclass has base_commit_sha field."""
        workflow = AutonomousWorkflow()
        assert hasattr(workflow, "base_commit_sha")
        assert workflow.base_commit_sha is None  # Default value

    def test_model_to_dict_includes_base_commit_sha(self):
        """Verify to_dict() includes base_commit_sha."""
        workflow = AutonomousWorkflow(base_commit_sha="abc123def456")
        data = workflow.to_dict()
        assert "base_commit_sha" in data
        assert data["base_commit_sha"] == "abc123def456"

    def test_model_from_dict_reads_base_commit_sha(self):
        """Verify from_dict() reads base_commit_sha."""
        data = {"base_commit_sha": "abc123def456"}
        workflow = AutonomousWorkflow.from_dict(data)
        assert workflow.base_commit_sha == "abc123def456"


class TestRepositoryUpdate:
    """Verify AutonomousWorkflowRepository allows base_commit_sha updates."""

    def test_base_commit_sha_in_allowed_fields(self):
        """Verify base_commit_sha is in ALLOWED_WORKFLOW_FIELDS."""
        repo = AutonomousWorkflowRepository()
        assert "base_commit_sha" in repo.ALLOWED_WORKFLOW_FIELDS
