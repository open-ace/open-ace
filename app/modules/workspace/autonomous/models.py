"""
Open ACE - Autonomous Development Models

Data models for the AI autonomous development workflow.

These dataclass models serve as the canonical type definition for autonomous
workflow data. While the repository layer currently uses raw dicts for DB
compatibility, these models provide:
- Type documentation and IDE autocompletion
- Validation via from_dict() / to_dict() roundtrips
- A migration path toward typed ORM in future iterations
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AutonomousWorkflow:
    """Represents an autonomous development workflow."""

    id: Optional[int] = None
    workflow_id: str = ""
    user_id: Optional[int] = None
    title: str = ""
    status: str = (
        "pending"  # queued|pending|preparing|planning|developing|pr_review|reporting|waiting|merging|completed|failed|cancelled|paused
    )
    requirements_text: str = ""
    requirements_issue_url: str = ""
    project_path: str = ""
    project_repo_url: str = ""
    is_new_project: bool = False
    is_private: bool = True
    cli_tool: str = ""
    model: str = ""
    permission_mode: str = "auto-edit"
    branch_name: str = ""
    branch_strategy: str = "new-branch"  # new-branch|worktree|current
    workspace_type: str = "local"  # local|remote
    remote_machine_id: str = ""
    worktree_path: str = ""
    github_issue_number: Optional[int] = None
    github_pr_number: Optional[int] = None
    github_pr_url: str = ""
    batch_id: Optional[str] = None
    batch_order: Optional[int] = None
    batch_total: Optional[int] = None
    auto_merge: bool = True  # Auto merge PR and proceed to next workflow in batch
    definition_snapshot: Optional[dict] = None
    current_phase: str = (
        "preparation"  # preparation|planning|development|pr_review|report|wait|merge
    )
    current_round: int = 0
    dev_round: int = 1
    max_plan_rounds: int = 3
    max_pr_review_rounds: int = 5
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    error_message: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None

    ACTIVE_STATUSES = (
        "pending",
        "preparing",
        "planning",
        "developing",
        "pr_review",
        "reporting",
        "waiting",
        "merging",
        "queued",
    )

    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    def is_paused(self) -> bool:
        return self.status == "paused"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "user_id": self.user_id,
            "title": self.title,
            "status": self.status,
            "requirements_text": self.requirements_text,
            "requirements_issue_url": self.requirements_issue_url,
            "project_path": self.project_path,
            "project_repo_url": self.project_repo_url,
            "is_new_project": self.is_new_project,
            "is_private": self.is_private,
            "cli_tool": self.cli_tool,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "branch_name": self.branch_name,
            "branch_strategy": self.branch_strategy,
            "workspace_type": self.workspace_type,
            "remote_machine_id": self.remote_machine_id,
            "worktree_path": self.worktree_path,
            "github_issue_number": self.github_issue_number,
            "github_pr_number": self.github_pr_number,
            "github_pr_url": self.github_pr_url,
            "batch_id": self.batch_id,
            "batch_order": self.batch_order,
            "batch_total": self.batch_total,
            "auto_merge": self.auto_merge,
            "definition_snapshot": self.definition_snapshot,
            "current_phase": self.current_phase,
            "current_round": self.current_round,
            "dev_round": self.dev_round,
            "max_plan_rounds": self.max_plan_rounds,
            "max_pr_review_rounds": self.max_pr_review_rounds,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_requests": self.total_requests,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "paused_at": self.paused_at.isoformat() if self.paused_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutonomousWorkflow":
        if not data:
            return cls()
        return cls(
            id=data.get("id"),
            workflow_id=data.get("workflow_id", ""),
            user_id=data.get("user_id"),
            title=data.get("title", ""),
            status=data.get("status", "pending"),
            requirements_text=data.get("requirements_text", ""),
            requirements_issue_url=data.get("requirements_issue_url", ""),
            project_path=data.get("project_path", ""),
            project_repo_url=data.get("project_repo_url", ""),
            is_new_project=bool(data.get("is_new_project", False)),
            is_private=bool(data.get("is_private", True)),
            cli_tool=data.get("cli_tool", ""),
            model=data.get("model", ""),
            permission_mode=data.get("permission_mode", "auto-edit"),
            branch_name=data.get("branch_name", ""),
            branch_strategy=data.get("branch_strategy", "new-branch"),
            workspace_type=data.get("workspace_type", "local"),
            remote_machine_id=data.get("remote_machine_id", ""),
            worktree_path=data.get("worktree_path", ""),
            github_issue_number=data.get("github_issue_number"),
            github_pr_number=data.get("github_pr_number"),
            github_pr_url=data.get("github_pr_url", ""),
            batch_id=data.get("batch_id"),
            batch_order=data.get("batch_order"),
            batch_total=data.get("batch_total"),
            auto_merge=bool(data.get("auto_merge", True)),
            definition_snapshot=data.get("definition_snapshot"),
            current_phase=data.get("current_phase", "preparation"),
            current_round=data.get("current_round", 0),
            dev_round=data.get("dev_round", 1),
            max_plan_rounds=data.get("max_plan_rounds", 3),
            max_pr_review_rounds=data.get("max_pr_review_rounds", 5),
            total_tokens=data.get("total_tokens", 0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            total_requests=data.get("total_requests", 0),
            error_message=data.get("error_message", ""),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            paused_at=(
                datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None
            ),
        )


@dataclass
class WorkflowMilestone:
    """Represents a milestone in the workflow timeline."""

    id: Optional[int] = None
    workflow_id: str = ""
    milestone_id: str = ""
    phase: str = ""
    dev_round: int = 1
    round_number: int = 0
    milestone_type: str = ""  # repo_setup|issue_created|branch_created|plan_created|...
    status: str = "pending"  # pending|in_progress|completed|failed|cancelled|forked
    title: str = ""
    description: str = ""
    session_id: str = ""
    review_session_id: str = ""
    github_issue_number: Optional[int] = None
    github_pr_number: Optional[int] = None
    github_comment_id: str = ""
    commit_shas: str = ""  # JSON array
    diff_stats: str = ""  # JSON
    result_summary: str = ""
    plan_content: str = ""
    review_content: str = ""
    error_message: str = ""
    parent_milestone_id: str = ""
    fork_branch: str = ""
    metadata: str = ""  # JSON
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "milestone_id": self.milestone_id,
            "phase": self.phase,
            "dev_round": self.dev_round,
            "round_number": self.round_number,
            "milestone_type": self.milestone_type,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "session_id": self.session_id,
            "review_session_id": self.review_session_id,
            "github_issue_number": self.github_issue_number,
            "github_pr_number": self.github_pr_number,
            "github_comment_id": self.github_comment_id,
            "commit_shas": self.commit_shas,
            "diff_stats": self.diff_stats,
            "result_summary": self.result_summary,
            "plan_content": self.plan_content,
            "review_content": self.review_content,
            "error_message": self.error_message,
            "parent_milestone_id": self.parent_milestone_id,
            "fork_branch": self.fork_branch,
            "metadata": self.metadata,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowMilestone":
        if not data:
            return cls()
        return cls(
            id=data.get("id"),
            workflow_id=data.get("workflow_id", ""),
            milestone_id=data.get("milestone_id", ""),
            phase=data.get("phase", ""),
            dev_round=data.get("dev_round", 1),
            round_number=data.get("round_number", 0),
            milestone_type=data.get("milestone_type", ""),
            status=data.get("status", "pending"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            session_id=data.get("session_id", ""),
            review_session_id=data.get("review_session_id", ""),
            github_issue_number=data.get("github_issue_number"),
            github_pr_number=data.get("github_pr_number"),
            github_comment_id=data.get("github_comment_id", ""),
            commit_shas=data.get("commit_shas", ""),
            diff_stats=data.get("diff_stats", ""),
            result_summary=data.get("result_summary", ""),
            plan_content=data.get("plan_content", ""),
            review_content=data.get("review_content", ""),
            error_message=data.get("error_message", ""),
            parent_milestone_id=data.get("parent_milestone_id", ""),
            fork_branch=data.get("fork_branch", ""),
            metadata=data.get("metadata", ""),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
        )


@dataclass
class WorkflowEvent:
    """Append-only event for real-time timeline updates."""

    id: Optional[int] = None
    workflow_id: str = ""
    milestone_id: str = ""
    event_type: str = ""
    event_data: str = ""  # JSON
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "milestone_id": self.milestone_id,
            "event_type": self.event_type,
            "event_data": self.event_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowEvent":
        if not data:
            return cls()
        return cls(
            id=data.get("id"),
            workflow_id=data.get("workflow_id", ""),
            milestone_id=data.get("milestone_id", ""),
            event_type=data.get("event_type", ""),
            event_data=data.get("event_data", ""),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
        )


@dataclass
class AgentTaskResult:
    """Result from running an agent task."""

    session_id: str = ""
    tracking_session_id: str = ""
    source_session_id: str = ""
    prompt: str = ""
    response_text: str = ""
    visible_response_text: str = ""
    structured_tags: dict[str, str] = field(default_factory=dict)
    messages: list = field(default_factory=list)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    request_count: int = 0
    tool_calls: list = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None
    # Ordered event log preserving actual message interleaving
    event_log: list = field(default_factory=list)
