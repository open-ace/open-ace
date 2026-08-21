"""Contract tests for the Schema Sync Heal workflow.

The workflow applies the authoritative ``regenerated-schema`` artifact emitted
by the failed Schema Sync run. These tests pin the safety boundaries that make
that write-token workflow acceptable: same-repo PRs only, exact failed head,
the schema artifact only, and a two-file diff before push.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "schema-sync-heal.yml"

pytestmark = []


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step(workflow: dict, *, name: str | None = None, step_id: str | None = None) -> dict:
    steps = workflow["jobs"]["heal"]["steps"]
    for step in steps:
        if name is not None and step.get("name") == name:
            return step
        if step_id is not None and step.get("id") == step_id:
            return step
    wanted = name if name is not None else step_id
    raise AssertionError(f"workflow step not found: {wanted}")


def test_schema_sync_heal_is_triggered_by_schema_sync_completion_only():
    workflow = _workflow()

    assert workflow["name"] == "Schema Sync Heal"
    assert workflow["on"]["workflow_run"]["workflows"] == ["Schema Sync"]
    assert workflow["on"]["workflow_run"]["types"] == ["completed"]
    assert workflow["permissions"] == {"contents": "write", "actions": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"

    job = workflow["jobs"]["heal"]
    assert "github.event.workflow_run.conclusion == 'failure'" in job["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in job["if"]
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in job["if"]


def test_guard_requires_failed_schema_sync_job_and_exact_pr_tip():
    workflow = _workflow()
    guard = _step(workflow, step_id="guard")
    run = guard["run"]

    assert 'select(.name == "schema-sync") | .conclusion' in run
    assert 'if [ "$schema_state" != "failure" ]; then' in run
    assert "commits/${HEAD_SHA}/pulls" in run
    assert "select(.head.sha == $sha)" in run
    assert ".head.repo.full_name" in run
    assert 'if [ "$tip" != "$HEAD_SHA" ]; then' in run
    assert "branch moved" in run.lower()


def test_loop_guard_skips_existing_schema_heal_commit():
    workflow = _workflow()
    loopguard = _step(workflow, step_id="loopguard")
    run = loopguard["run"]

    assert "chore(schema): apply regenerated schema snapshots" in run
    assert "skip=true" in run
    download = _step(workflow, step_id="download")
    assert "steps.loopguard.outputs.skip != 'true'" in download["if"]


def test_artifact_download_and_validation_are_fail_closed_to_schema_files():
    workflow = _workflow()
    download = _step(workflow, step_id="download")
    validate = _step(workflow, step_id="validate")

    assert 'gh run download "$RUN_ID" --name regenerated-schema' in download["run"]

    run = validate["run"]
    assert "schema-postgres.sql" in run
    assert "schema-sqlite.sql" in run
    assert "regenerated-schema/schema-postgres.sql" in run
    assert "regenerated-schema/schema-sqlite.sql" in run
    assert "regenerated-schema/schema/schema-postgres.sql" not in run
    assert "regenerated-schema/schema/schema-sqlite.sql" not in run
    assert "schema/schema-postgres.sql" in run
    assert "schema/schema-sqlite.sql" in run
    assert "unexpected regenerated-schema artifact payload" in run
    assert "git diff --name-only" in run
    assert "unexpected files changed by schema artifact" in run
    assert "scripts/validate_schema.py" not in run
    assert "PR-controlled validation scripts" in run
    assert "git diff --check" not in run


def test_diff_guard_allows_schema_file_subset_changes_only():
    workflow = _workflow()
    validate = _step(workflow, step_id="validate")
    run = validate["run"]

    assert 'grep -vxF -f "$diff_allowed"' in run
    assert '[ "$changed" != "$(cat "$expected")" ]' not in run


def test_push_is_plain_non_force_and_retriggers_ci():
    workflow = _workflow()
    push = _step(workflow, name="Push regenerated schema snapshots and re-trigger checks")
    run = push["run"]

    assert (
        'git commit -m "chore(schema): apply regenerated schema snapshots for #${PR_NUMBER}"' in run
    )
    assert 'git push origin "HEAD:$BRANCH"' in run
    assert "--force" not in run
    assert "actions/runs/${run_id}/approve" in run
    assert "actions/runs?head_sha=${NEW_SHA}&status=action_required" in run
    assert (
        "actions/workflows/schema-sync.yml/runs?head_sha=${NEW_SHA}&status=action_required"
        not in run
    )
    assert "actions/workflows/ci.yml/dispatches" in run
    assert "actions/workflows/schema-sync.yml/dispatches" in run
    assert "sleep 5" in run
    assert "1 2" in run


def test_schema_sync_dispatch_checks_out_pr_merge_ref():
    workflow = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "schema-sync.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    checkout = next(
        step
        for step in workflow["jobs"]["schema-sync"]["steps"]
        if step.get("uses") == "actions/checkout@v6"
    )

    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert "healed_sha" not in inputs
    assert inputs["pull_request_number"]
    assert checkout["with"]["ref"] == (
        "${{ inputs.pull_request_number && "
        "format('refs/pull/{0}/merge', inputs.pull_request_number) || github.sha }}"
    )


def test_fallback_dispatch_uses_healed_sha_only_for_ci_marker():
    workflow = _workflow()
    push = _step(workflow, name="Push regenerated schema snapshots and re-trigger checks")
    run = push["run"]

    assert "{ref: $ref, inputs: {healed_sha: $sha}}" in run
    assert '--arg pr "$PR_NUMBER"' in run
    assert "inputs: {pull_request_number: $pr}" in run
    assert "inputs: {healed_sha: $sha, pull_request_number: $pr}" not in run
