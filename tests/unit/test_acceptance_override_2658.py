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

    def test_unrelated_user_gets_403_without_side_effects(self, app_client):
        client, repo, _ = app_client
        gh = MagicMock()
        with (
            ExitStack() as stack,
            patch(
                "app.modules.workspace.autonomous.github_ops.GitHubOps",
                return_value=gh,
            ) as gh_cls,
        ):
            stack.enter_context(_mock_auth(user_id=99, role="user", username="stranger"))
            resp = _post_override(client)
        assert resp.status_code == 403
        # The 403 must land BEFORE any side effect: no workflow mutation, no
        # issue comment/close, no audit event (review #2659 Minor 3).
        repo.update_workflow.assert_not_called()
        repo.create_event.assert_not_called()
        gh.add_issue_comment.assert_not_called()
        gh.close_issue.assert_not_called()
        assert gh_cls.call_count == 0  # never constructed: 403 precedes all guards


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

    def test_not_paused_400(self, app_client):
        """#2659 review: mid-verification runs carry a stale prior verdict
        (resume-with-feedback clears the merge SHA but not verification_status)
        — overriding there would race the verifier on an unknown merge."""
        client, repo, _ = app_client
        repo.get_workflow.return_value = _workflow_row(status="running", verification_merge_sha="")
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client)
        assert resp.status_code == 400

    def test_empty_verification_status_400(self, app_client):
        client, repo, _ = app_client
        repo.get_workflow.return_value = _workflow_row(verification_status=None)
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client)
        assert resp.status_code == 400

    def test_reason_over_2000_chars_400(self, app_client):
        client, _repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client, {"reason": "x" * 2001})
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

    def test_resume_clears_stale_error_message(self, app_client):
        """#2491 UX: the header banner renders ``error_message`` verbatim, so
        the stale "rejected; awaiting review" text survived the resume and the
        page kept telling the user the workflow awaited review. Resuming with
        feedback IS the review outcome — the route must clear it."""
        client, repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=7, role="user", username="owner"))
            resp = self._post_feedback(client)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        updates = repo.update_workflow.call_args.args[1]
        assert updates["error_message"] == ""


_REPORT_REJECTED = (
    '{"status": "rejected", "merge_sha": "abc123",'
    ' "scope": [], "gates": ['
    '{"item": "call-chain", "verdict": "confirmed", "evidence": []}'
    "],"
    ' "verifier": ['
    '{"item": "wire CI lanes", "verdict": "rejected", "evidence": [],'
    ' "rationale": "no producer job in workflows"},'
    '{"item": "summary artifact", "verdict": "indeterminate",'
    ' "evidence": [{"note": "single stdlib implementation"}]}'
    "]}"
)


class TestAcceptanceFeedbackPrefill:
    """The resume-with-feedback modal pre-fills the verifier's failed-items
    list so the user can submit it as feedback verbatim or lightly edit it.
    Derived server-side from the stored ``verification_report`` so the issue
    comment and the prefill share one formatter."""

    def _get_workflow(self, client, **overrides):
        client_app = client.application
        with client_app.test_request_context():
            from app.routes.autonomous import _workflow_response

            return _workflow_response(_workflow_row(**overrides))

    def test_rejected_pause_gets_prefill_with_failed_items(self, app_client):
        client, _, _ = app_client
        resp = self._get_workflow(client, verification_report=_REPORT_REJECTED)
        prefill = resp["acceptance_feedback_prefill"]
        assert "- [verifier] `wire CI lanes` (rejected) — no producer job in workflows" in prefill
        # rationale missing → falls back to the first evidence note
        assert "`summary artifact` (indeterminate)" in prefill
        assert "single stdlib implementation" in prefill
        # confirmed gates must not leak into the prefill
        assert "call-chain" not in prefill

    def test_prefill_survives_malformed_report(self, app_client):
        client, _, _ = app_client
        resp = self._get_workflow(client, verification_report="{not json")
        assert resp.get("acceptance_feedback_prefill", "") == ""

    def test_non_rejected_pause_has_no_prefill(self, app_client):
        client, _, _ = app_client
        resp = self._get_workflow(
            client,
            verification_status="confirmed",
            verification_report='{"status": "confirmed", "verifier": []}',
        )
        assert resp.get("acceptance_feedback_prefill", "") == ""

    def test_waiting_row_has_no_prefill(self, app_client):
        client, _, _ = app_client
        resp = self._get_workflow(
            client,
            status="waiting",
            current_phase="wait",
            verification_report=_REPORT_REJECTED,
        )
        assert resp.get("acceptance_feedback_prefill", "") == ""
