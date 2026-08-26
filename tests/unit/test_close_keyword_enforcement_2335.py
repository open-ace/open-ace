import pytest

from app.modules.workspace.autonomous.phases.pr_review import build_pr_body_close_ref

pytestmark = [
    pytest.mark.regression,
    pytest.mark.issue(2335),
    pytest.mark.usefixtures("_enable_acceptance_verification"),
]


def test_pr_body_uses_implements_not_closes():
    body = build_pr_body_close_ref(issue_number=2335)
    assert "Implements #2335" in body
    assert "Closes #2335" not in body
    assert "Fixes #2335" not in body and "Resolves #2335" not in body


def test_pr_body_none_when_no_issue():
    assert build_pr_body_close_ref(issue_number=None) == ""
