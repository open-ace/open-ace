"""Tests for multi-tenant save_usage fix and fetch_claude workflow annotation.

Problem 1: save_usage ON CONFLICT must include tenant_id to match the
daily_usage unique constraint (tenant_id, date, tool_name, host_name).
Without it Postgres raises InvalidColumnReference and fetch_claude crashes
every run.

Problem 4a: fetch_claude must annotate CLI sessions that came from an
autonomous workflow worktree with a readable title + workflow_id context,
so the frontend can badge them and jump to the workflow timeline.
"""

import inspect
import os
import sys
from unittest.mock import MagicMock

# conftest.py already adds scripts/shared to sys.path. We also need scripts/
# itself so the module-level imports below resolve. From tests/issues/1760/
# <file> it's 4x dirname to reach the repo root.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_SCRIPTS_PATH = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_PATH not in sys.path:
    sys.path.insert(0, _SCRIPTS_PATH)


def _load_shared_db():
    """Lazy import to keep these path-dependent imports out of the top-level
    import block (avoids isort/ruff section-split churn during pre-commit)."""
    from shared import db as shared_db

    return shared_db


# ── Problem 1: save_usage ON CONFLICT includes tenant_id ───────────────


def test_save_usage_signature_accepts_tenant_id():
    """save_usage must accept a tenant_id parameter (default 1, host-level
    aggregate semantics)."""
    shared_db = _load_shared_db()
    sig = inspect.signature(shared_db.save_usage)
    assert "tenant_id" in sig.parameters
    assert sig.parameters["tenant_id"].default == 1


def test_save_usage_pg_sql_includes_tenant_id_in_conflict_target():
    """The PostgreSQL INSERT branch must include tenant_id in BOTH the INSERT
    column list and the ON CONFLICT target, matching the unique constraint
    uq_daily_usage_date_tool_host (tenant_id, date, tool_name, host_name).
    A mismatch raises InvalidColumnReference on Postgres."""
    shared_db = _load_shared_db()
    source = inspect.getsource(shared_db.save_usage)
    # tenant_id in INSERT column list
    assert "tenant_id" in source
    # ON CONFLICT target includes tenant_id first (constraint column order)
    assert "ON CONFLICT (tenant_id, date, tool_name, host_name)" in source
    # The old buggy form must be gone
    assert "ON CONFLICT (date, tool_name, host_name)" not in source


# ── Problem 4a: fetch_claude workflow session annotation ───────────────


def test_extract_workflow_id_from_encoded_worktree_path():
    """Claude encodes the worktree cwd with /→- and stores jsonl under
    ~/.claude/projects/<encoded>/. The encoded worktree path contains a
    -worktrees-<uuid> segment that maps back to the workflow_id."""
    from fetch_claude import _extract_workflow_id_from_project_path

    # Real encoded form (matches what Claude writes on Linux)
    encoded = "-home-rhuang-open-ace--worktrees-29a825f7-c90f-485c-a74f-ac760204004c"
    assert _extract_workflow_id_from_project_path(encoded) == "29a825f7-c90f-485c-a74f-ac760204004c"


def test_extract_workflow_id_returns_empty_for_regular_cli_session():
    """A regular (non-workflow) CLI session's project_path has no worktree
    segment and must return '' so it's not misannotated."""
    from fetch_claude import _extract_workflow_id_from_project_path

    assert _extract_workflow_id_from_project_path("-home-rhuang-open-ace") == ""
    assert _extract_workflow_id_from_project_path("") == ""
    assert _extract_workflow_id_from_project_path("-Users-joe-myproject") == ""


def test_resolve_workflow_session_annotation_links_to_workflow():
    """When project_path maps to a workflow, the annotation title is prefixed
    with [Auto] and context carries workflow_id + workflow_imported flag."""
    from fetch_claude import _resolve_workflow_session_annotation

    cursor = MagicMock()
    # Simulate a workflow row lookup hit
    cursor.fetchone.return_value = {"title": "gh issue 1851"}

    title, context = _resolve_workflow_session_annotation(
        cursor,
        "-home-rhuang-open-ace--worktrees-29a825f7-c90f-485c-a74f-ac760204004c",
    )

    assert title == "[Auto] gh issue 1851"
    assert context["workflow_id"] == "29a825f7-c90f-485c-a74f-ac760204004c"
    assert context["workflow_imported"] is True


def test_resolve_workflow_session_annotation_empty_for_non_workflow():
    """Non-workflow sessions get empty title + context (no annotation)."""
    from fetch_claude import _resolve_workflow_session_annotation

    cursor = MagicMock()
    title, context = _resolve_workflow_session_annotation(cursor, "-home-rhuang-open-ace")
    assert title == ""
    assert context == {}
    # Must not hit the DB for non-workflow paths
    cursor.fetchone.assert_not_called()
