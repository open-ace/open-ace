"""Terminal and deletion routes must drop carried CLI transcripts (#3237).

The orchestrator's ``_update_workflow`` hook covers the status writes IT
makes, and an earlier version of this feature assumed that was every route to
a terminal state. It is not. ``stop_workflow`` and the acceptance override
write to the repository directly, and the delete routes remove the row with no
status write at all — so a workflow could be stopped, completed or deleted
while its whole transcript directory stayed on disk.

Deletion is the one that cannot be recovered from later: once the row is gone,
no status hook can ever identify that workflow again, and the age reaper only
runs at scheduler startup. The purge at delete time is the last chance there
will ever be.

Fixtures follow tests/integration/routes/test_autonomous_stop_pause_740.py.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]


@pytest.fixture
def auto_db(tmp_path):
    """A temporary SQLite database carrying the authoritative schema."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_purge.db")
            db = Database(db_url=f"sqlite:///{db_path}")
            conn = db.get_connection()
            try:
                from app.repositories.schema_init import load_schema_from_file

                load_schema_from_file(db_url=db.db_url, dialect="sqlite")
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "platform_admin"),
                )
                conn.commit()
            finally:
                conn.close()
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


@pytest.fixture
def client(auto_db, monkeypatch):
    """Flask test client bound to the fixture database."""
    from app import create_app
    from app.repositories.user_repo import UserRepository

    monkeypatch.setenv("DATABASE_URL", auto_db.db_url)
    monkeypatch.setattr("app.routes.autonomous._workflow_rate_limiter._hits", {})
    monkeypatch.setenv("OPENACE_ALLOW_SHARED_CHECKOUT", "1")
    app = create_app({"TESTING": True})
    monkeypatch.setattr("app.routes.autonomous.user_repo", UserRepository(db=auto_db))
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


def _mock_auth(user_id=1, role="admin"):
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": user_id,
            "username": "admin",
            "email": f"{role}@test.com",
            "role": role,
        },
    )


@pytest.fixture
def purged(monkeypatch):
    """Record which workflow ids the route asked to purge."""
    seen: list[str] = []
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.orchestrator.purge_agent_state",
        lambda workflow_id, store=None: seen.append(workflow_id),
    )
    return seen


def test_stop_purges_the_transcripts(client, purged):
    """stop_workflow writes "cancelled" straight through the repository."""
    repo = MagicMock()
    repo.get_workflow.return_value = {
        "workflow_id": "wf-stop",
        "user_id": 1,
        "status": "developing",
        "batch_id": None,
    }

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.post("/api/autonomous/workflows/wf-stop/stop")

    assert resp.status_code == 200
    assert (
        "wf-stop" in purged
    ), "the workflow was cancelled but its carried transcripts were left on disk"


def test_stop_also_purges_the_siblings_it_cancels(client, purged):
    """The batch siblings reach a terminal state by the same bypass."""
    repo = MagicMock()
    repo.get_workflow.return_value = {
        "workflow_id": "wf-stop",
        "user_id": 1,
        "status": "developing",
        "batch_id": "b-1",
    }
    repo.cancel_queued_batch_workflows.return_value = 2
    repo.list_batch_workflows.return_value = [
        {"workflow_id": "wf-stop", "status": "cancelled"},
        {"workflow_id": "wf-sib-1", "status": "cancelled"},
        {"workflow_id": "wf-sib-2", "status": "cancelled"},
        {"workflow_id": "wf-running", "status": "developing"},
    ]

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.post("/api/autonomous/workflows/wf-stop/stop")

    assert resp.status_code == 200
    assert {"wf-stop", "wf-sib-1", "wf-sib-2"} <= set(purged), purged
    assert "wf-running" not in purged, "a workflow that is still running was purged"


def test_delete_purges_the_transcripts(client, purged):
    """The last chance: after this the row is gone and nothing can find it."""
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-del", "user_id": 1}

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.delete("/api/autonomous/workflows/wf-del")

    assert resp.status_code == 200
    assert "wf-del" in purged, (
        "the row was deleted with its transcripts still on disk; no later "
        "status hook can identify the workflow again"
    )


def test_a_failed_delete_does_not_purge(client, purged):
    """Only a SUCCESSFUL delete may drop the state.

    Purging after a failed delete would destroy the history of a workflow that
    is still very much alive.
    """
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-del", "user_id": 1}
    repo.delete_workflow.side_effect = RuntimeError("constraint violation")

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.delete("/api/autonomous/workflows/wf-del")

    assert resp.status_code == 500
    assert not purged, "state was purged even though the workflow still exists"


def test_delete_batch_purges_every_workflow(client, purged):
    """Batch deletion drops many rows, so it must purge many ids."""
    repo = MagicMock()
    repo.list_batch_workflows.return_value = [
        {"workflow_id": "wf-a", "user_id": 1},
        {"workflow_id": "wf-b", "user_id": 1},
        {"workflow_id": "wf-c", "user_id": 1},
    ]
    repo.delete_batch.return_value = 3

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.delete("/api/autonomous/batches/b-1")

    assert resp.status_code == 200
    assert set(purged) == {
        "wf-a",
        "wf-b",
        "wf-c",
    }, f"batch deletion left some transcripts behind: {purged}"


def test_a_purge_failure_never_breaks_the_route(client, monkeypatch):
    """Tidying up must not turn a successful stop into a 500."""
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.orchestrator.purge_agent_state",
        MagicMock(side_effect=OSError("disk gone")),
    )
    repo = MagicMock()
    repo.get_workflow.return_value = {
        "workflow_id": "wf-stop",
        "user_id": 1,
        "status": "developing",
        "batch_id": None,
    }

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        resp = client.post("/api/autonomous/workflows/wf-stop/stop")

    assert resp.status_code == 200


def test_the_override_purges_even_when_the_audit_insert_fails(client, purged):
    """Ordering, not just presence: the purge must precede every fallible step.

    `repo.update_workflow` commits "completed" in its own transaction, but the
    audit `create_event` that follows is NOT best-effort. Sequencing the purge
    after it meant an audit failure returned 500 with the workflow already
    terminal and its whole transcript directory still on disk — a retention
    leak reachable through an ordinary database hiccup.
    """
    repo = MagicMock()
    repo.get_workflow.return_value = {
        "workflow_id": "wf-override",
        "user_id": 1,
        "status": "paused",
        "current_phase": "acceptance_verification",
        "verification_status": "indeterminate",
        "verification_merge_sha": "abc123",
        "github_issue_number": 7,
    }
    repo.create_event.side_effect = RuntimeError("audit insert failed")

    with _mock_auth(), patch("app.routes.autonomous.auto_repo", repo):
        client.post(
            "/api/autonomous/workflows/wf-override/verification_override",
            json={"reason": "verified by hand"},
        )

    # However the request ends, the workflow was already written terminal, so
    # its transcripts must be gone.
    assert repo.update_workflow.called, "the fixture never reached the terminal write"
    assert "wf-override" in purged, (
        "the workflow was marked completed but the audit failure skipped the "
        "purge, leaving its transcripts on disk"
    )
