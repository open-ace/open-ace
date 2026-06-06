"""
Open ACE - Autonomous Development Module

AI autonomous development workflow orchestration.
"""

from app.modules.workspace.autonomous.models import (
    AgentTaskResult,
    AutonomousWorkflow,
    WorkflowEvent,
    WorkflowMilestone,
)
from app.repositories.database import is_postgresql


def get_ddl_statements():
    """Return DDL statements for autonomous workflow tables.

    Uses database-appropriate syntax based on is_postgresql().

    Note: The canonical schema definition lives in the Alembic migration:
    migrations/versions/20260605_050_add_autonomous_workflow_tables.py
    Changes to the schema must be reflected in BOTH places.
    """
    use_pg = is_postgresql()
    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if use_pg else "INTEGER"
    bool_false = "FALSE" if use_pg else "0"
    bool_true = "TRUE" if use_pg else "1"

    return [
        f"""
        CREATE TABLE IF NOT EXISTS autonomous_workflows (
            id {pk_type},
            workflow_id TEXT NOT NULL UNIQUE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            requirements_text TEXT DEFAULT '',
            requirements_issue_url TEXT DEFAULT '',
            project_path TEXT DEFAULT '',
            project_repo_url TEXT DEFAULT '',
            is_new_project {bool_type} DEFAULT {bool_false},
            is_private {bool_type} DEFAULT {bool_true},
            cli_tool TEXT DEFAULT '',
            model TEXT DEFAULT '',
            permission_mode TEXT DEFAULT 'auto-edit',
            branch_name TEXT DEFAULT '',
            branch_strategy TEXT DEFAULT 'new-branch',
            workspace_type TEXT DEFAULT 'local',
            remote_machine_id TEXT DEFAULT '',
            worktree_path TEXT DEFAULT '',
            github_issue_number INTEGER,
            github_pr_number INTEGER,
            github_pr_url TEXT DEFAULT '',
            current_phase TEXT DEFAULT 'preparation',
            current_round INTEGER DEFAULT 0,
            dev_round INTEGER DEFAULT 1,
            max_plan_rounds INTEGER DEFAULT 3,
            max_pr_review_rounds INTEGER DEFAULT 5,
            total_tokens INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            paused_at TIMESTAMP,
            locked_at TIMESTAMP,
            locked_by TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            task_timeout INTEGER
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_workflows_user_status
            ON autonomous_workflows(user_id, status)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS workflow_milestones (
            id {pk_type},
            workflow_id TEXT NOT NULL REFERENCES autonomous_workflows(workflow_id) ON DELETE CASCADE,
            milestone_id TEXT NOT NULL UNIQUE,
            phase TEXT NOT NULL DEFAULT '',
            dev_round INTEGER DEFAULT 1,
            round_number INTEGER DEFAULT 0,
            milestone_type TEXT NOT NULL DEFAULT '',
            status TEXT DEFAULT 'pending',
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            review_session_id TEXT DEFAULT '',
            github_issue_number INTEGER,
            github_pr_number INTEGER,
            github_comment_id TEXT DEFAULT '',
            commit_shas TEXT DEFAULT '',
            diff_stats TEXT DEFAULT '',
            result_summary TEXT DEFAULT '',
            plan_content TEXT DEFAULT '',
            review_content TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            parent_milestone_id TEXT DEFAULT '',
            fork_branch TEXT DEFAULT '',
            metadata TEXT DEFAULT '',
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_milestones_workflow_phase
            ON workflow_milestones(workflow_id, phase, status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_milestones_workflow_round
            ON workflow_milestones(workflow_id, dev_round)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS workflow_events (
            id {pk_type},
            workflow_id TEXT NOT NULL,
            milestone_id TEXT DEFAULT '',
            event_type TEXT NOT NULL DEFAULT '',
            event_data TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_events_workflow_created
            ON workflow_events(workflow_id, created_at)
        """,
    ]


__all__ = [
    "AutonomousWorkflow",
    "WorkflowMilestone",
    "WorkflowEvent",
    "AgentTaskResult",
    "get_ddl_statements",
]
