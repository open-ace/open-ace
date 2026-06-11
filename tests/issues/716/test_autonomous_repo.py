"""Integration tests for AutonomousWorkflowRepository using temp SQLite database."""

import json
import os
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.repositories.database import Database


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables initialized."""
    orig_adapt_sql = db_mod.adapt_sql
    db_mod.adapt_sql = lambda q: q
    try:
        # Patch is_postgresql in BOTH the database module AND the autonomous module
        # (the latter does `from ... import is_postgresql` which creates a separate reference)
        with (
            patch.object(db_mod, "is_postgresql", return_value=False),
            patch("app.modules.workspace.autonomous.is_postgresql", return_value=False),
        ):
            db_path = str(tmp_path / "test_auto.db")
            db = Database(db_url=f"sqlite:///{db_path}")

            # Create users table (required by FK)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT DEFAULT 'user',
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )
                # Insert a test user
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("testuser", "test@test.com", "hash123", "user"),
                )
                conn.commit()

                # Create autonomous tables
                from app.modules.workspace.autonomous import get_ddl_statements

                for sql in get_ddl_statements():
                    cursor.execute(sql)
                conn.commit()
            finally:
                conn.close()

            yield db
    finally:
        db_mod.adapt_sql = orig_adapt_sql
        try:
            os.unlink(db_path)
        except OSError:
            pass


class TestWorkflowCRUD:
    """Tests for workflow CRUD operations."""

    def test_create_workflow(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        data = {
            "user_id": 1,
            "title": "Test Task",
            "requirements_text": "Build a feature",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "project_path": "/tmp/test-project",
            "definition_snapshot": json.dumps({"requirements_mode": "text"}),
        }
        result = repo.create_workflow(data)
        assert result is not None
        assert result["workflow_id"] != ""
        assert result["title"] == "Test Task"
        assert result["status"] == "pending"
        assert result["cli_tool"] == "claude-code"
        assert json.loads(result["definition_snapshot"])["requirements_mode"] == "text"

    def test_get_workflow(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        data = {
            "user_id": 1,
            "title": "Get Test",
            "requirements_text": "test",
            "cli_tool": "cc",
            "project_path": "/tmp",
        }
        created = repo.create_workflow(data)
        wf_id = created["workflow_id"]

        fetched = repo.get_workflow(wf_id)
        assert fetched is not None
        assert fetched["title"] == "Get Test"

    def test_get_workflow_not_found(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        assert repo.get_workflow("nonexistent") is None

    def test_list_workflows(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        for i in range(3):
            repo.create_workflow(
                {
                    "user_id": 1,
                    "title": f"Task {i}",
                    "requirements_text": f"Requirement {i}",
                    "cli_tool": "cc",
                    "project_path": "/tmp",
                }
            )

        workflows = repo.list_workflows()
        assert len(workflows) == 3
        assert repo.count_workflows() == 3

    def test_list_workflows_filter_user(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        repo.create_workflow(
            {
                "user_id": 1,
                "title": "U1",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        repo.create_workflow(
            {
                "user_id": 1,
                "title": "U1-2",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

        result = repo.list_workflows(user_id=1)
        assert len(result) == 2

        result = repo.list_workflows(user_id=999)
        assert len(result) == 0

    def test_list_workflows_filter_status(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "S1",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        repo.update_workflow(wf["workflow_id"], {"status": "planning"})

        result = repo.list_workflows(status="pending")
        assert len(result) == 0

        result = repo.list_workflows(status="planning")
        assert len(result) == 1

    def test_list_workflows_comma_separated_status(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf1 = repo.create_workflow(
            {
                "user_id": 1,
                "title": "T1",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        wf2 = repo.create_workflow(
            {
                "user_id": 1,
                "title": "T2",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        repo.update_workflow(wf1["workflow_id"], {"status": "planning"})
        repo.update_workflow(wf2["workflow_id"], {"status": "developing"})

        result = repo.list_workflows(status="planning,developing")
        assert len(result) == 2

    def test_list_workflows_search_and_pagination(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        first = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Batch Issue 123",
                "requirements_text": "Add billing export",
                "cli_tool": "claude-code",
                "model": "sonnet",
                "project_path": "/tmp/billing",
                "branch_name": "feature/billing-export",
            }
        )
        repo.create_workflow(
            {
                "user_id": 1,
                "title": "Other Task",
                "requirements_text": "Unrelated",
                "cli_tool": "codex",
                "project_path": "/tmp/other",
            }
        )

        result = repo.list_workflows(search="BILLING", limit=1, offset=0)
        assert len(result) == 1
        assert result[0]["workflow_id"] == first["workflow_id"]
        assert repo.count_workflows(search="billing") == 1

    def test_update_workflow(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Original",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

        updated = repo.update_workflow(
            wf["workflow_id"],
            {
                "title": "Updated",
                "status": "planning",
                "current_phase": "planning",
            },
        )
        assert updated["title"] == "Updated"
        assert updated["status"] == "planning"

    def test_update_workflow_empty(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "No Update",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        result = repo.update_workflow(wf["workflow_id"], {})
        assert result["title"] == "No Update"

    def test_update_workflow_tokens(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Tokens",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

        repo.update_workflow_tokens(
            wf["workflow_id"],
            {
                "total_tokens": 500,
                "total_input_tokens": 300,
                "total_output_tokens": 200,
                "total_requests": 1,
            },
        )

        fetched = repo.get_workflow(wf["workflow_id"])
        assert fetched["total_tokens"] == 500
        assert fetched["total_input_tokens"] == 300
        assert fetched["total_output_tokens"] == 200
        assert fetched["total_requests"] == 0

        # Accumulate more
        repo.update_workflow_tokens(
            wf["workflow_id"],
            {
                "total_tokens": 100,
                "total_input_tokens": 60,
                "total_output_tokens": 40,
                "total_requests": 1,
            },
        )

        fetched = repo.get_workflow(wf["workflow_id"])
        assert fetched["total_tokens"] == 600
        assert fetched["total_requests"] == 0

    def test_get_active_workflows(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf1 = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Active",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        wf2 = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Done",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        repo.update_workflow(wf2["workflow_id"], {"status": "completed"})

        active = repo.get_active_workflows()
        assert len(active) == 1
        assert active[0]["workflow_id"] == wf1["workflow_id"]

    def test_delete_workflow(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Delete",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        wf_id = wf["workflow_id"]

        repo.delete_workflow(wf_id)
        assert repo.get_workflow(wf_id) is None

    def test_delete_workflow_cascades(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Cascade",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        wf_id = wf["workflow_id"]

        # Create milestone and event
        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "planning", "milestone_type": "plan_created"}
        )
        repo.create_event({"workflow_id": wf_id, "event_type": "test"})

        repo.delete_workflow(wf_id)
        assert repo.get_workflow(wf_id) is None
        assert repo.list_milestones(wf_id) == []
        assert repo.list_events(wf_id) == []


class TestMilestoneCRUD:
    """Tests for milestone CRUD operations."""

    def _create_workflow(self, repo):
        return repo.create_workflow(
            {
                "user_id": 1,
                "title": "Test WF",
                "requirements_text": "test",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

    def test_create_milestone(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)

        ms = repo.create_milestone(
            {
                "workflow_id": wf["workflow_id"],
                "phase": "planning",
                "milestone_type": "plan_created",
                "title": "Plan created",
                "status": "completed",
                "plan_content": "# Plan\n1. Step 1\n2. Step 2",
            }
        )
        assert ms is not None
        assert ms["milestone_id"] != ""
        assert ms["phase"] == "planning"
        assert ms["milestone_type"] == "plan_created"
        assert ms["plan_content"] == "# Plan\n1. Step 1\n2. Step 2"

    def test_get_milestone(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        ms = repo.create_milestone(
            {"workflow_id": wf["workflow_id"], "phase": "dev", "milestone_type": "dev_started"}
        )

        fetched = repo.get_milestone(ms["milestone_id"])
        assert fetched is not None
        assert fetched["milestone_type"] == "dev_started"

    def test_list_milestones(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "preparation", "milestone_type": "issue_created"}
        )
        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "planning", "milestone_type": "plan_created"}
        )
        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "development", "milestone_type": "dev_started"}
        )

        all_ms = repo.list_milestones(wf_id)
        assert len(all_ms) == 3

    def test_list_milestones_filter_phase(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "planning", "milestone_type": "plan_created"}
        )
        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "development", "milestone_type": "dev_started"}
        )

        planning = repo.list_milestones(wf_id, phase="planning")
        assert len(planning) == 1
        assert planning[0]["phase"] == "planning"

    def test_list_milestones_filter_dev_round(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "dev", "milestone_type": "dev_started", "dev_round": 1}
        )
        repo.create_milestone(
            {"workflow_id": wf_id, "phase": "dev", "milestone_type": "dev_started", "dev_round": 2}
        )

        r1 = repo.list_milestones(wf_id, dev_round=1)
        assert len(r1) == 1

    def test_update_milestone(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        ms = repo.create_milestone(
            {
                "workflow_id": wf["workflow_id"],
                "phase": "planning",
                "milestone_type": "plan_created",
                "status": "in_progress",
            }
        )

        updated = repo.update_milestone(
            ms["milestone_id"],
            {
                "status": "completed",
                "plan_content": "Final plan",
                "result_summary": "Plan finalized",
            },
        )
        assert updated["status"] == "completed"
        assert updated["plan_content"] == "Final plan"

    def test_cancel_milestones_after(self, auto_db):
        """Cancel milestones after a given milestone by creation order."""
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        ms1 = repo.create_milestone(
            {
                "workflow_id": wf_id,
                "phase": "prep",
                "milestone_type": "issue_created",
                "status": "completed",
            }
        )
        ms2 = repo.create_milestone(
            {
                "workflow_id": wf_id,
                "phase": "planning",
                "milestone_type": "plan_created",
                "status": "completed",
            }
        )
        ms3 = repo.create_milestone(
            {
                "workflow_id": wf_id,
                "phase": "dev",
                "milestone_type": "dev_started",
                "status": "in_progress",
            }
        )

        cancelled = repo.cancel_milestones_after(wf_id, ms1["milestone_id"])
        assert len(cancelled) >= 1

        # ms2 and ms3 should be cancelled
        ms2_fetched = repo.get_milestone(ms2["milestone_id"])
        ms3_fetched = repo.get_milestone(ms3["milestone_id"])
        assert ms2_fetched["status"] == "cancelled"
        assert ms3_fetched["status"] == "cancelled"


class TestEventCRUD:
    """Tests for event CRUD operations."""

    def _create_workflow(self, repo):
        return repo.create_workflow(
            {
                "user_id": 1,
                "title": "Event Test",
                "requirements_text": "test",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

    def test_create_event(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)

        ev = repo.create_event(
            {
                "workflow_id": wf["workflow_id"],
                "event_type": "phase_change",
                "event_data": json.dumps({"phase": "planning"}),
            }
        )
        assert ev is not None
        assert ev["workflow_id"] == wf["workflow_id"]
        assert ev["event_type"] == "phase_change"

    def test_list_events(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        for i in range(5):
            repo.create_event(
                {
                    "workflow_id": wf_id,
                    "event_type": f"event_{i}",
                    "event_data": json.dumps({"i": i}),
                }
            )

        events = repo.list_events(wf_id)
        assert len(events) == 5

    def test_list_events_with_limit(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        for i in range(10):
            repo.create_event(
                {
                    "workflow_id": wf_id,
                    "event_type": f"event_{i}",
                }
            )

        events = repo.list_events(wf_id, limit=3)
        assert len(events) == 3

    def test_events_ordered_by_created_at(self, auto_db):
        repo = AutonomousWorkflowRepository(auto_db)
        wf = self._create_workflow(repo)
        wf_id = wf["workflow_id"]

        repo.create_event({"workflow_id": wf_id, "event_type": "first"})
        repo.create_event({"workflow_id": wf_id, "event_type": "second"})
        repo.create_event({"workflow_id": wf_id, "event_type": "third"})

        events = repo.list_events(wf_id)
        types = [e["event_type"] for e in events]
        assert types == ["first", "second", "third"]


class TestAllowedFieldsFiltering:
    """Tests for ALLOWED_WORKFLOW_FIELDS whitelist security filtering."""

    def test_update_allows_only_whitelisted_fields(self, auto_db):
        """Only fields in ALLOWED_WORKFLOW_FIELDS are persisted."""
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Whitelist Test",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

        # Attempt to update with both allowed and disallowed fields
        repo.update_workflow(
            wf["workflow_id"],
            {
                "title": "Updated Title",  # allowed
                "status": "planning",  # allowed
                "malicious_field": "HACKED",  # NOT in whitelist
                "DROP TABLE": "sql_injection",  # NOT in whitelist
            },
        )

        updated = repo.get_workflow(wf["workflow_id"])
        assert updated["title"] == "Updated Title"
        assert updated["status"] == "planning"
        # Disallowed fields should NOT appear as columns
        assert "malicious_field" not in updated
        assert "DROP TABLE" not in updated

    def test_update_filters_out_empty_update_set(self, auto_db):
        """When all fields are non-allowed, no update occurs (returns current state)."""
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Original",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )

        result = repo.update_workflow(
            wf["workflow_id"],
            {
                "evil_column": "should_be_ignored",
                "another_bad_field": "also_ignored",
            },
        )

        # Title should remain unchanged
        assert result["title"] == "Original"

    def test_allowed_fields_set_completeness(self):
        """Verify ALLOWED_WORKFLOW_FIELDS contains critical security fields."""
        repo = AutonomousWorkflowRepository(MagicMock())
        assert "status" in repo.ALLOWED_WORKFLOW_FIELDS
        assert "title" in repo.ALLOWED_WORKFLOW_FIELDS
        assert "error_message" in repo.ALLOWED_WORKFLOW_FIELDS
        assert "current_phase" in repo.ALLOWED_WORKFLOW_FIELDS
        assert "branch_name" in repo.ALLOWED_WORKFLOW_FIELDS
        # These should NOT be in the whitelist
        assert "user_id" not in repo.ALLOWED_WORKFLOW_FIELDS
        assert "workflow_id" not in repo.ALLOWED_WORKFLOW_FIELDS

    def test_update_workflow_coerces_booleans(self, auto_db):
        """Boolean fields are coerced properly via the whitelist."""
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "Bool Coerce",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
                "is_new_project": False,
            }
        )

        updated = repo.update_workflow(
            wf["workflow_id"],
            {"is_new_project": "true"},  # String 'true' should be coerced
        )
        # SQLite returns 1/0 for booleans, not True/False
        assert updated["is_new_project"]  # truthy

        updated = repo.update_workflow(
            wf["workflow_id"],
            {"is_private": 0},  # int 0 should be coerced to False
        )
        assert not updated["is_private"]  # falsy

    def test_update_milestone_allowed_fields_filter(self, auto_db):
        """Milestone updates also filter through ALLOWED_MILESTONE_FIELDS."""
        repo = AutonomousWorkflowRepository(auto_db)
        wf = repo.create_workflow(
            {
                "user_id": 1,
                "title": "MS Filter",
                "requirements_text": "t",
                "cli_tool": "cc",
                "project_path": "/tmp",
            }
        )
        ms = repo.create_milestone(
            {"workflow_id": wf["workflow_id"], "phase": "dev", "milestone_type": "test"}
        )

        updated = repo.update_milestone(
            ms["milestone_id"],
            {
                "status": "completed",  # allowed
                "plan_content": "Plan text",  # allowed
                "malicious_column": "EVIL",  # NOT in whitelist
            },
        )

        assert updated["status"] == "completed"
        assert updated["plan_content"] == "Plan text"
        assert "malicious_column" not in updated


# Required import for TestAllowedFieldsFiltering
from unittest.mock import MagicMock
