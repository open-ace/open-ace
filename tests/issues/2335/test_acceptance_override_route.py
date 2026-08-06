"""#2335 S6: human override route for an indeterminate acceptance workflow.

An admin may override a workflow paused at ``acceptance_verification`` with
``verification_status="indeterminate"``: setting it to ``confirmed`` with an
audit trail, then closing the issue (as @open-ace-bot) and completing the
workflow. Non-admins get 403; a non-indeterminate workflow is rejected.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


def _workflow_row(**overrides):
    base = {
        "id": 1,
        "workflow_id": "wf-override",
        "user_id": 7,
        "status": "paused",
        "current_phase": "acceptance_verification",
        "verification_status": "indeterminate",
        "verification_merge_sha": "abc123",
        "verification_report": '{"status": "indeterminate"}',
        "github_issue_number": 4242,
        "github_pr_number": 99,
        "project_path": "/srv/open-ace",
        "worktree_path": "/srv/open-ace/.worktrees/wf-override",
        "system_account": "openace",
        "error_message": "Acceptance indeterminate: awaiting evidence",
    }
    base.update(overrides)
    return base


def _user_dict(user_id=1, role="admin", username="admin"):
    return {
        "id": user_id,
        "username": username,
        "email": f"{username}@test.com",
        "role": role,
        "tenant_id": None,
    }


def _mock_auth(user_id=1, role="admin", username="admin"):
    """Patch _load_user_from_token so the test client bypasses real auth."""
    user = _user_dict(user_id, role, username)
    return (patch("app.auth.decorators._load_user_from_token", return_value=user),)


@pytest.fixture
def app_client():
    """Flask test client with the autonomous blueprint + a mocked repo.

    Yields (client, repo) where ``repo.get_workflow`` returns the live workflow
    dict (mutated by update_workflow so the response reflects post-override
    state) and ``repo.create_event`` records audit events.
    """
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
    with (patch("app.routes.autonomous._get_repo", return_value=repo),):
        client = app.test_client()
        client.set_cookie("session_token", "test-token")
        yield client, repo, workflow


def _post_override(client, body=None):
    return client.post(
        "/api/autonomous/workflows/wf-override/verification_override",
        json=body or {"reason": "manual verification by operator"},
    )


def test_admin_override_confirms_indeterminate_and_closes_issue(app_client):
    client, repo, _ = app_client
    gh = MagicMock()
    with (
        ExitStack() as stack,
        patch(
            "app.modules.workspace.autonomous.github_ops.GitHubOps",
            return_value=gh,
        ),
    ):
        for p in _mock_auth(role="admin"):
            stack.enter_context(p)
        resp = _post_override(client)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["workflow"]["verification_status"] == "confirmed"
    assert payload["workflow"]["status"] == "completed"
    assert payload["workflow"]["current_phase"] == "acceptance_verification"

    # The issue was closed by the bot and a report comment was posted.
    gh.close_issue.assert_called_once_with(4242)
    gh.add_issue_comment.assert_called_once()
    comment = gh.add_issue_comment.call_args.args[1]
    assert "override" in comment.lower()

    # Persistence: verified_by marks the human override + audit event emitted.
    updates = repo.update_workflow.call_args.args[1]
    assert updates["verification_status"] == "confirmed"
    assert str(updates["verified_by"]).startswith("human-override:")
    assert updates["issue_closed_by_workflow_at"]
    assert updates["status"] == "completed"
    repo.create_event.assert_called()
    evt = repo.create_event.call_args.args[0]
    assert evt["event_type"] == "acceptance_override"


def test_non_admin_override_returns_403(app_client):
    client, _repo, _ = app_client
    with ExitStack() as stack:
        for p in _mock_auth(role="user", user_id=7, username="owner"):
            stack.enter_context(p)
        resp = _post_override(client)
    assert resp.status_code == 403


def test_override_rejects_non_indeterminate_workflow(app_client):
    client, repo, _ = app_client
    # Workflow already confirmed — override is not allowed.
    repo.get_workflow.return_value = _workflow_row(verification_status="confirmed")
    with ExitStack() as stack:
        for p in _mock_auth(role="admin"):
            stack.enter_context(p)
        resp = _post_override(client)
    assert resp.status_code == 400


def test_override_404_unknown_workflow(app_client):
    client, repo, _ = app_client
    repo.get_workflow.return_value = None
    with ExitStack() as stack:
        for p in _mock_auth(role="admin"):
            stack.enter_context(p)
        resp = _post_override(client)
    assert resp.status_code == 404


def test_override_marks_verified_by_with_username(app_client):
    client, _repo, _ = app_client
    with (
        ExitStack() as stack,
        patch(
            "app.modules.workspace.autonomous.github_ops.GitHubOps",
            return_value=MagicMock(),
        ),
        patch("app.routes.autonomous.UserRepository") as user_repo_cls,
    ):
        for p in _mock_auth(role="admin", user_id=1, username="rhuang"):
            stack.enter_context(p)
        user_repo_cls.return_value.get_user_by_id.return_value = {"username": "rhuang"}
        resp = _post_override(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["workflow"]["verified_by"] == "human-override:rhuang"
