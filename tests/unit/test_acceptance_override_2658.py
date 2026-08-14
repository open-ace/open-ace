"""#2658: acceptance override for rejected AND indeterminate, owner+admin.

Regression for the route extension: the workflow owner (not just admins) may
override a paused acceptance verdict — confirming it, closing the issue and
completing the workflow. Rejected verdicts are overridable; confirmed ones
still 400; unrelated users still 403. resume-with-feedback must clear the
cached verification merge SHA so the next acceptance run re-verifies instead
of replaying the prior verdict on a stale (merge_sha, snapshot) pair.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from app import create_app

pytestmark = [pytest.mark.regression, pytest.mark.issue(2658)]


def _workflow_row(**overrides):
    base = {
        "id": 1,
        "workflow_id": "wf-override",
        "user_id": 7,
        "status": "paused",
        "current_phase": "acceptance_verification",
        "verification_status": "rejected",
        "verification_merge_sha": "abc123",
        "verification_report": '{"status": "rejected"}',
        "github_issue_number": 4242,
        "github_pr_number": 99,
        "project_path": "/srv/open-ace",
        "worktree_path": "/srv/open-ace/.worktrees/wf-override",
        "system_account": "openace",
        "error_message": "Acceptance verification rejected; awaiting review",
    }
    base.update(overrides)
    return base


def _user_dict(user_id, role, username):
    return {
        "id": user_id,
        "username": username,
        "email": f"{username}@test.com",
        "role": role,
        "tenant_id": None,
    }


def _mock_auth(user_id=1, role="admin", username="admin"):
    user = _user_dict(user_id, role, username)
    return patch("app.auth.decorators._load_user_from_token", return_value=user)


@pytest.fixture
def app_client():
    workflow = _workflow_row()
    repo = MagicMock()
    repo.get_workflow.return_value = dict(workflow)

    def _update(workflow_id, updates):
        workflow.update(updates)
        repo.get_workflow.return_value = dict(workflow)
        return dict(workflow)

    repo.update_workflow.side_effect = _update
    repo.create_event.return_value = {}

    app = create_app({"TESTING": True})
    with patch("app.routes.autonomous._get_repo", return_value=repo):
        client = app.test_client()
        client.set_cookie("session_token", "test-token")
        yield client, repo, workflow


def _post_override(client, body=None):
    return client.post(
        "/api/autonomous/workflows/wf-override/verification_override",
        json=body or {"reason": "inspected the merged code; acceptable"},
    )


class TestOverridePermission:
    def test_owner_can_override_rejected(self, app_client):
        client, repo, _ = app_client
        gh = MagicMock()
        with (
            ExitStack() as stack,
            patch(
                "app.modules.workspace.autonomous.github_ops.GitHubOps",
                return_value=gh,
            ),
        ):
            stack.enter_context(_mock_auth(user_id=7, role="user", username="owner"))
            resp = _post_override(client)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        updates = repo.update_workflow.call_args.args[1]
        assert updates["verification_status"] == "confirmed"
        assert updates["status"] == "completed"
        gh.close_issue.assert_called_once_with(4242)
        # The issue comment must record that a rejection was overturned.
        comment = gh.add_issue_comment.call_args.args[1]
        assert "rejection overturned" in comment

    def test_owner_can_override_indeterminate(self, app_client):
        client, repo, _ = app_client
        repo.get_workflow.return_value = _workflow_row(verification_status="indeterminate")
        gh = MagicMock()
        with (
            ExitStack() as stack,
            patch(
                "app.modules.workspace.autonomous.github_ops.GitHubOps",
                return_value=gh,
            ),
        ):
            stack.enter_context(_mock_auth(user_id=7, role="user", username="owner"))
            resp = _post_override(client)
        assert resp.status_code == 200
        comment = gh.add_issue_comment.call_args.args[1]
        assert "rejection overturned" not in comment

    def test_admin_can_override_rejected(self, app_client):
        client, repo, _ = app_client
        with (
            ExitStack() as stack,
            patch(
                "app.modules.workspace.autonomous.github_ops.GitHubOps",
                return_value=MagicMock(),
            ),
        ):
            stack.enter_context(_mock_auth(user_id=1, role="admin", username="root"))
            resp = _post_override(client)
        assert resp.status_code == 200

    def test_unrelated_user_gets_403(self, app_client):
        client, _repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=99, role="user", username="stranger"))
            resp = _post_override(client)
        assert resp.status_code == 403


class TestOverrideStatusGuard:
    def test_confirmed_still_400(self, app_client):
        client, repo, _ = app_client
        repo.get_workflow.return_value = _workflow_row(verification_status="confirmed")
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client)
        assert resp.status_code == 400

    def test_wrong_phase_400(self, app_client):
        client, repo, _ = app_client
        repo.get_workflow.return_value = _workflow_row(current_phase="development")
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client)
        assert resp.status_code == 400


class TestResumeWithFeedbackClearsVerificationCache:
    """#2658: a fresh dev round must not replay the prior acceptance verdict.

    The acceptance phase caches ``verification_merge_sha`` on the workflow and
    its idempotency replays a terminal verdict for the same
    (merge_sha, snapshot) pair. Nothing else ever cleared the SHA between
    rounds, so resume-with-feedback → new dev round → new merge would still
    re-verify the OLD merge. The route must clear the cached SHA.
    """

    def _post_feedback(self, client):
        return client.post(
            "/api/autonomous/workflows/wf-override/resume-with-feedback",
            json={"user_feedback": "please also handle the edge case"},
        )

    def test_resume_clears_cached_merge_sha(self, app_client):
        client, repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=7, role="user", username="owner"))
            resp = self._post_feedback(client)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        updates = repo.update_workflow.call_args.args[1]
        assert updates["verification_merge_sha"] == ""
        assert updates["current_phase"] == "wait"
        assert updates["status"] == "waiting"
        assert updates["user_feedback"] == "please also handle the edge case"
