"""Repository integration tests for the cancel/fork redesign (Issue #886).

Runs ``AutonomousWorkflowRepository`` fork-related methods against a real
temporary SQLite database initialized from the authoritative schema:
- list_forks (empty + populated)
- copy_milestones_to_workflow (new IDs, fork-marker stripping)
- create_milestone persistence of fork markers (fork_workflow_id)

Includes the 2nd-generation fork-marker regression (PR #1243 review):
copied milestone history must not inherit an ancestor's fork_workflow_id.

Migrated from tests/issues/886/test_cancel_fork_redesign.py
(TestForkRepoIntegration).
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

pytestmark = [pytest.mark.regression, pytest.mark.issue(886)]

# Modules that import is_postgresql/adapt_sql via "from ... import" and hold
# local references that patch.object on db_mod alone will NOT reach.  We must
# patch them in every module that has already performed the import.
_IS_PG_TARGETS = [
    "app.repositories.database",
    "app.repositories.autonomous_repo",
    "app.modules.workspace.autonomous",
    "app.modules.workspace.autonomous.orchestrator",
]

_ADAPT_SQL_TARGETS = [
    "app.repositories.database",
    "app.repositories.autonomous_repo",
]


def _patch_is_postgresql():
    """Return a list of patchers that force is_postgresql() -> False everywhere."""
    patchers = []
    for mod_path in _IS_PG_TARGETS:
        mod = sys.modules.get(mod_path)
        if mod is not None and hasattr(mod, "is_postgresql"):
            patchers.append(patch.object(mod, "is_postgresql", return_value=False))
    return patchers


def _passthrough_sql(q):
    """Return SQL query unchanged for SQLite compatibility."""
    return q


def _replace_adapt_sql():
    """Replace adapt_sql with passthrough in all target modules; return originals."""
    originals = {}
    for mod_path in _ADAPT_SQL_TARGETS:
        mod = sys.modules.get(mod_path)
        if mod is not None and hasattr(mod, "adapt_sql"):
            originals[mod_path] = mod.adapt_sql
            mod.adapt_sql = _passthrough_sql
    return originals


def _restore_adapt_sql(originals):
    """Restore original adapt_sql functions."""
    for mod_path, orig_fn in originals.items():
        mod = sys.modules.get(mod_path)
        if mod is not None:
            mod.adapt_sql = orig_fn


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables.

    Patches is_postgresql and adapt_sql in *all* modules that hold local
    references so the test is isolated regardless of import order.
    """
    is_pg_patchers = _patch_is_postgresql()
    for p in is_pg_patchers:
        p.start()
    adapt_originals = _replace_adapt_sql()
    try:
        db_path = str(tmp_path / "test_cancel_fork_repo.db")
        db = Database(db_url=f"sqlite:///{db_path}")
        conn = db.get_connection()
        try:
            from app.repositories.schema_init import load_schema_from_file

            # Load the FULL authoritative schema (incl. users.deleted_at) on the empty
            # DB FIRST, then seed — never hand-CREATE an old users table (the column
            # would never be backfilled; legacy tests/issues escape hatch).
            load_schema_from_file(db_url=db.db_url, dialect="sqlite")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, role) " "VALUES (?, ?, ?, ?)",
                ("admin", "admin@test.com", "hash123", "platform_admin"),
            )
            conn.commit()
        finally:
            conn.close()
        yield db
    finally:
        _restore_adapt_sql(adapt_originals)
        for p in reversed(is_pg_patchers):
            p.stop()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _make_workflow(**overrides):
    """Create a sample workflow dict."""
    wf = {
        "workflow_id": "wf-001",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "developing",
        "requirements_text": "Build a feature",
        "project_path": "/tmp/test",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "default",
        "branch_name": "feature/test",
        "branch_strategy": "branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 2,
        "max_pr_review_rounds": 2,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "parent_workflow_id": None,
        "fork_milestone_id": None,
        "user_feedback": "",
        "original_branch_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "paused_at": None,
    }
    wf.update(overrides)
    return wf


def _make_milestone(**overrides):
    """Create a sample milestone dict."""
    ms = {
        "milestone_id": "ms-001",
        "workflow_id": "wf-001",
        "phase": "development",
        "dev_round": 1,
        "round_number": 1,
        "milestone_type": "dev_completed",
        "status": "completed",
        "title": "Development round 1 completed",
        "description": "",
        "session_id": "sess-001",
        "review_session_id": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_comment_id": "",
        "commit_shas": "abc123",
        "diff_stats": '{"additions": 10, "deletions": 2, "files": 3, "commits": 1}',
        "result_summary": "Implemented feature",
        "plan_content": "",
        "review_content": "",
        "error_message": "",
        "parent_milestone_id": "",
        "fork_branch": "",
        "fork_workflow_id": "",
        "metadata": "{}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    ms.update(overrides)
    return ms


class TestForkRepoIntegration:
    """Integration tests for fork-related repository methods with real DB."""

    def test_list_forks_empty(self, auto_db):
        """list_forks returns empty list when no forks exist."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create a parent workflow first
        wf_data = _make_workflow()
        repo.create_workflow(wf_data)

        forks = repo.list_forks("wf-001")
        assert forks == []

    def test_create_and_list_forks(self, auto_db):
        """list_forks returns child workflows after creation."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create parent
        parent = _make_workflow()
        repo.create_workflow(parent)

        # Create fork child
        child = _make_workflow(
            workflow_id="wf-fork-001",
            title="Test [Fork]",
            parent_workflow_id="wf-001",
            fork_milestone_id="ms-001",
        )
        repo.create_workflow(child)

        forks = repo.list_forks("wf-001")
        assert len(forks) == 1
        assert forks[0]["workflow_id"] == "wf-fork-001"
        assert forks[0]["parent_workflow_id"] == "wf-001"

    def test_copy_milestones_to_workflow(self, auto_db):
        """copy_milestones_to_workflow copies milestones with new IDs."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Create source workflow
        src = _make_workflow()
        repo.create_workflow(src)

        # Create milestones for source
        ms1 = _make_milestone(milestone_id="ms-1", phase="planning", milestone_type="plan_created")
        ms2 = _make_milestone(
            milestone_id="ms-2", phase="development", milestone_type="dev_completed"
        )
        ms3 = _make_milestone(milestone_id="ms-3", phase="development", milestone_type="tests_run")
        repo.create_milestone(ms1)
        repo.create_milestone(ms2)
        repo.create_milestone(ms3)

        # Create destination workflow
        dst = _make_workflow(workflow_id="wf-fork-dst", title="Fork Target")
        repo.create_workflow(dst)

        # Copy up to and including ms-2
        copied = repo.copy_milestones_to_workflow("wf-001", "wf-fork-dst", "ms-2")
        assert len(copied) == 2

        # Verify copied milestones belong to destination
        dst_milestones = repo.list_milestones("wf-fork-dst")
        assert len(dst_milestones) == 2
        # The original milestones have new IDs (UUIDs)
        original_ids = {"ms-1", "ms-2"}
        copied_ids = {ms["milestone_id"] for ms in dst_milestones}
        assert copied_ids.isdisjoint(original_ids), "Copied milestones should have new IDs"

    def test_create_milestone_persists_fork_workflow_id(self, auto_db):
        """Fork marker milestones persist fork_workflow_id for timeline fork visualization."""
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        repo.create_workflow(_make_workflow())
        created = repo.create_milestone(
            _make_milestone(
                milestone_id="ms-fork",
                milestone_type="workflow_forked",
                title="Forked to new workflow",
                fork_branch="fork/from-ms-fork",
                fork_workflow_id="wf-fork-001",
            )
        )

        assert created["fork_workflow_id"] == "wf-fork-001"

        fetched = repo.get_milestone("ms-fork")
        assert fetched is not None
        assert fetched["fork_workflow_id"] == "wf-fork-001"

    def test_copy_milestones_strips_inherited_fork_workflow_id(self, auto_db):
        """Copied milestones must not carry an ancestor's fork_workflow_id.

        Regression for 2nd-generation forks (PR #1243 review): when A forks to
        B (marker on A with fork_workflow_id=wf-B) and A later forks to C at a
        milestone *after* the A->B marker, C's copied history would otherwise
        inherit fork_workflow_id=wf-B. That makes C's parent-view fork split
        resolve at A's earlier branch point instead of C's real one. Copied
        rows are the child's own history, so fork_workflow_id must be cleared.
        """
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(auto_db)

        # Workflow A: M1 is the fork point for B.
        repo.create_workflow(_make_workflow(workflow_id="wf-A"))
        repo.create_milestone(_make_milestone(milestone_id="M1", workflow_id="wf-A"))

        # Fork A -> B at M1 (per route order: copy history, then fork marker).
        repo.create_workflow(
            _make_workflow(workflow_id="wf-B", parent_workflow_id="wf-A", fork_milestone_id="M1")
        )
        repo.copy_milestones_to_workflow("wf-A", "wf-B", "M1")
        repo.create_milestone(
            _make_milestone(
                milestone_id="MS-AFORK",
                workflow_id="wf-A",
                milestone_type="workflow_forked",
                fork_branch="fb-AB",
                fork_workflow_id="wf-B",
                parent_milestone_id="M1",
            )
        )

        # A continues to M2 (after MS-AFORK), then forks to C at M2. C's copy
        # scope (id <= M2) *includes* the A->B fork marker MS-AFORK.
        repo.create_milestone(_make_milestone(milestone_id="M2", workflow_id="wf-A"))
        repo.create_workflow(
            _make_workflow(workflow_id="wf-C", parent_workflow_id="wf-A", fork_milestone_id="M2")
        )
        repo.copy_milestones_to_workflow("wf-A", "wf-C", "M2")

        c_history = repo.list_milestones("wf-C")
        # The ancestor's fork marker type is preserved (still describes history)
        # but its fork_workflow_id must be cleared so it doesn't masattribute a
        # split to wf-B when C is later rendered as a parent.
        inherited_markers = [ms for ms in c_history if ms["milestone_type"] == "workflow_forked"]
        assert inherited_markers, "C should carry the inherited workflow_forked marker"
        for ms in inherited_markers:
            assert ms["fork_workflow_id"] == "", (
                "Copied ancestor fork marker must not retain fork_workflow_id; "
                f"got {ms['fork_workflow_id']!r}"
            )
