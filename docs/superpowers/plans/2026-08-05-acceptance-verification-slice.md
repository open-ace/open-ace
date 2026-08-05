# Acceptance Verification — PR1 Vertical Slice Implementation Plan (#2335)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship PR1 of #2335: autonomous PRs stop auto-closing issues (`Closes #N`→`Implements #N`); a new `acceptance_verification` phase runs an independent read-only verifier + a deterministic scope gate on the merged main SHA and only closes the issue (as `@open-ace-bot`) on `confirmed`.

**Architecture:** Append an `acceptance_verification` phase after `merge`. Parse an acceptance snapshot from the issue (convention → LLM fallback). A new phase handler spawns a credentialless read-only verifier agent on merged main, runs a scope gate (`base_commit_sha..merge_sha`), aggregates per-item verdicts to `confirmed`/`rejected`/`indeterminate`, and transitions accordingly. `confirmed`→explicit `close_issue()` + report; `rejected`→new dev round; `indeterminate`→paused. Idempotent on `(merge_sha, issue_acceptance_hash)`. Spec: `docs/superpowers/specs/2026-08-05-acceptance-verification-slice-design.md`.

**Tech stack:** Python/Flask, PostgreSQL+SQLite (`adapt_sql`), Alembic, the `PhaseResult`/`PHASE_HANDLERS` machine (`phase_contract.py`), `evidence.py` `Verdict` enum, the session-line agent runner, GitHubOps (`_run_gh(api_only=True)`), pytest.

---

## File Structure

**Create**
- `app/modules/workspace/autonomous/acceptance_snapshot.py` — pure: `AcceptanceSnapshot` dataclass, `parse_acceptance_snapshot(body)`, canonical JSON + `hash_snapshot`. No git/db deps.
- `app/modules/workspace/autonomous/acceptance_verdicts.py` — pure: `ItemVerdict` dataclass, `aggregate_verdicts(list[ItemVerdict]) -> str`.
- `app/modules/workspace/autonomous/phases/acceptance_verification.py` — the phase handler: scope gate (`run_scope_gate`), verifier spawn (via `deps.host`), snapshot persist, aggregate, transitions, idempotency, reopen guard.
- `migrations/versions/20260805_010_acceptance_verification_columns.py` — idempotent `ADD COLUMN` migration.
- `tests/issues/2335/test_acceptance_snapshot.py`, `test_acceptance_verdicts.py`, `test_scope_gate.py`, `test_close_keyword_and_close_issue.py`, `test_acceptance_phase.py`.

**Modify**
- `app/modules/workspace/autonomous/orchestrator.py` — `PHASE_ORDER`, `PHASE_STATUS_MAP`, `_COMPLETED_TERMINAL_PHASES`, `SESSION_LINE_FIELDS`; a `run_verification_agent` PhaseHost alias + `_run_verification_agent` impl.
- `app/modules/workspace/autonomous/phases/__init__.py` — register `acceptance_verification`.
- `app/modules/workspace/autonomous/phases/merge.py` — terminal `completed()` targets `acceptance_verification`.
- `app/modules/workspace/autonomous/phases/pr_review.py` — `Closes #N` → `Implements #N`.
- `app/modules/workspace/autonomous/github_ops.py` — add `close_issue(number)` (`api_only=True`); add `get_merge_commit_sha(pr_number)`.
- `app/modules/workspace/autonomous/constants.py` — `VERIFICATION_ALLOWED_TOOLS`.
- `app/repositories/autonomous_repo.py` — new columns in `ALLOWED_WORKFLOW_FIELDS`.
- `schema/schema-postgres.sql` + `schema/schema-sqlite.sql` — regenerated.
- `tests/autonomous/test_phase_b_acceptance.py` — phase-order/terminal invariant.

---

## Task 1: Acceptance snapshot parser + hash (pure)

**Files:**
- Create: `app/modules/workspace/autonomous/acceptance_snapshot.py`
- Test: `tests/issues/2335/test_acceptance_snapshot.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/issues/2335/test_acceptance_snapshot.py
from app.modules.workspace.autonomous.acceptance_snapshot import (
    AcceptanceSnapshot,
    hash_snapshot,
    parse_acceptance_snapshot,
)


def test_parses_convention_sections():
    body = """Some intro.

## Scope
- `app/services/retention.py`
- `app/routes/legal.py`

## 验收标准
- [ ] retention manager runs on cron
- [ ] DELETE is gated by legal-hold

## 不在 Scope
- UI changes
"""
    snap = parse_acceptance_snapshot(body)
    assert snap.source == "convention"
    assert snap.confidence == "high"
    assert "app/services/retention.py" in snap.required_paths
    assert "app/routes/legal.py" in snap.required_paths
    assert len(snap.checklist) == 2
    assert "retention manager runs on cron" in snap.checklist
    assert snap.non_scope == ["UI changes"]


def test_missing_sections_flagged_for_llm_extraction():
    snap = parse_acceptance_snapshot("just a free-form issue, no sections")
    assert snap.source == "missing"
    assert snap.required_paths == []
    assert snap.checklist == []


def test_closure_constraint_detected():
    body = "## Scope\n- `x.py`\n\n禁止阶段性关闭。"
    snap = parse_acceptance_snapshot(body)
    assert snap.closure_constraints is True


def test_hash_is_stable_and_order_independent():
    a = parse_acceptance_snapshot("## Scope\n- `a.py`\n- `b.py`\n## 验收标准\n- [ ] one\n")
    b = parse_acceptance_snapshot("## Scope\n- `b.py`\n- `a.py`\n## 验收标准\n- [ ] one\n")
    assert hash_snapshot(a) == hash_snapshot(b)
    assert isinstance(hash_snapshot(a), str) and len(hash_snapshot(a)) == 64
```

- [ ] **Step 2: Run — expect ImportError/fail**

Run: `python -m pytest tests/issues/2335/test_acceptance_snapshot.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# app/modules/workspace/autonomous/acceptance_snapshot.py
"""Issue acceptance-snapshot parsing for the acceptance_verification phase (#2335).

Pure module: markdown-section extraction + canonical hashing. No git/db deps.
Convention sections: ## Scope, ## 验收标准 / ## Acceptance Criteria, ## 不在 Scope /
## Non-Scope. When required sections are absent, source="missing" and the verifier
performs LLM extraction (re-persisted with source="llm", confidence="low").
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

# Matches a markdown heading and captures its body up to the next same-or-higher heading.
_SECTION_RE = re.compile(r"(?m)^(?:#{1,6})\s+(.*?)\s*$")
_PATH_RE = re.compile(r"`?([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+|\.[A-Za-z0-9_./-]+)`?")
_CLOSURE_CONSTRAINT_RE = re.compile(
    r"禁止阶段性关闭|do not close (?:until|before)|no (?:premature|staged) close",
    re.IGNORECASE,
)
# A glob/path token inside a list item: contains a slash or a dot-ext, or a */** wildcard.
_TOKEN_PATH_RE = re.compile(r"`?([A-Za-z0-9_./?*{}\[\]\-]+)`?")


@dataclass
class AcceptanceSnapshot:
    required_paths: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    non_scope: list[str] = field(default_factory=list)
    closure_constraints: bool = False
    source: str = "missing"        # "convention" | "missing" | "llm"
    confidence: str = "low"        # "high" (convention) | "low" (missing/llm)

    def to_canonical(self) -> dict:
        return {
            "required_paths": sorted(self.required_paths),
            "checklist": sorted(self.checklist),
            "non_scope": sorted(self.non_scope),
            "closure_constraints": self.closure_constraints,
            # source/confidence are intentionally excluded: the hash captures the
            # acceptance *content*, so an issue edit (new path/checklist) changes
            # the hash and forces re-verification, while an LLM re-extraction of
            # the same content is stable.
        }


def _split_sections(body: str) -> dict[str, str]:
    """Return {lowercased_heading: body_until_next_heading}."""
    matches = list(_SECTION_RE.finditer(body))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[title] = body[start:end]
    return sections


def _extract_paths(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        item = line.lstrip("-* ").strip()
        # Prefer backticked token, else the first path-shaped token on the line.
        for tok in _TOKEN_PATH_RE.findall(item):
            if "/" in tok or tok.startswith(".") or "*" in tok:
                paths.append(tok.strip("`"))
                break
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _extract_checklist(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*[-*]\s+\[[ xX]\]\s+(.*)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _extract_plain_list(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            out.append(line.lstrip("-* ").strip())
    return out


_SCOPE_TITLES = {"scope"}
_CRITERIA_TITLES = {"验收标准", "acceptance criteria", "acceptance", "验收"}
_NONSCOPE_TITLES = {"不在 scope", "non-scope", "non scope", "out of scope"}


def parse_acceptance_snapshot(body: str) -> AcceptanceSnapshot:
    sections = _split_sections(body or "")
    snap = AcceptanceSnapshot()

    scope_text = next((sections[t] for t in sections if t in _SCOPE_TITLES), None)
    criteria_text = next((sections[t] for t in sections if any(k in t for k in _CRITERIA_TITLES)), None)
    nonscope_text = next((sections[t] for t in sections if any(k in t for k in _NONSCOPE_TITLES)), None)

    if scope_text is not None or criteria_text is not None:
        snap.source = "convention"
        snap.confidence = "high"

    if scope_text is not None:
        snap.required_paths = _extract_paths(scope_text)
    if criteria_text is not None:
        snap.checklist = _extract_checklist(criteria_text)
    if nonscope_text is not None:
        snap.non_scope = _extract_plain_list(nonscope_text)

    snap.closure_constraints = bool(_CLOSURE_CONSTRAINT_RE.search(body or ""))
    return snap


def hash_snapshot(snap: AcceptanceSnapshot) -> str:
    canonical = json.dumps(snap.to_canonical(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_acceptance_snapshot.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/acceptance_snapshot.py tests/issues/2335/test_acceptance_snapshot.py
git commit -m "feat(autonomous): acceptance snapshot parser + hash (#2335)"
```

---

## Task 2: Verdict aggregation (pure)

**Files:**
- Create: `app/modules/workspace/autonomous/acceptance_verdicts.py`
- Test: `tests/issues/2335/test_acceptance_verdicts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/issues/2335/test_acceptance_verdicts.py
from app.modules.workspace.autonomous.acceptance_verdicts import (
    ItemVerdict,
    aggregate_verdicts,
)
from app.modules.workspace.autonomous.evidence import Verdict


def _item(v, item="x"):
    return ItemVerdict(item=item, verdict=v, evidence=[], rationale="")


def test_all_confirmed():
    assert aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.CONFIRMED)]) == "confirmed"


def test_any_rejected():
    assert (
        aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.REJECTED)])
        == "rejected"
    )


def test_any_indeterminate_without_rejected():
    assert (
        aggregate_verdicts([_item(Verdict.CONFIRMED), _item(Verdict.INDETERMINATE)])
        == "indeterminate"
    )


def test_empty_is_indeterminate():
    assert aggregate_verdicts([]) == "indeterminate"
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_acceptance_verdicts.py -q`
Expected: FAIL (import).

- [ ] **Step 3: Implement**

```python
# app/modules/workspace/autonomous/acceptance_verdicts.py
"""Per-item acceptance verdicts + issue-level aggregation (#2335). Pure."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.workspace.autonomous.evidence import Verdict

# The issue-level status string written to verification_status.
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_INDETERMINATE = "indeterminate"


@dataclass
class ItemVerdict:
    item: str                                   # checklist text or required path
    verdict: Verdict                            # CONFIRMED / REJECTED / INDETERMINATE
    evidence: list[dict] = field(default_factory=list)  # [{"ref": "file:line|git-diff", "note": "..."}]
    rationale: str = ""


def aggregate_verdicts(items: list[ItemVerdict]) -> str:
    """Any REJECTED → rejected; else any INDETERMINATE → indeterminate; else confirmed.

    Empty item list → indeterminate (nothing was affirmatively confirmed).
    """
    if any(iv.verdict is Verdict.REJECTED for iv in items):
        return STATUS_REJECTED
    if any(iv.verdict is Verdict.INDETERMINATE for iv in items):
        return STATUS_INDETERMINATE
    if items and all(iv.verdict is Verdict.CONFIRMED for iv in items):
        return STATUS_CONFIRMED
    return STATUS_INDETERMINATE
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_acceptance_verdicts.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/acceptance_verdicts.py tests/issues/2335/test_acceptance_verdicts.py
git commit -m "feat(autonomous): acceptance verdict aggregation (#2335)"
```

---

## Task 3: Idempotent migration + schema regen

**Files:**
- Create: `migrations/versions/20260805_010_acceptance_verification_columns.py`
- Modify: `schema/schema-postgres.sql`, `schema/schema-sqlite.sql` (regenerated)

- [ ] **Step 1: Write the migration**

```python
# migrations/versions/20260805_010_acceptance_verification_columns.py
"""Add acceptance_verification columns to autonomous_workflows (#2335).

Revision ID: 20260805_010_acceptance_verification_columns
Revises: 20260805_001_add_max_changed_files_override
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260805_010_acceptance_verification_columns"
down_revision = "20260805_001_add_max_changed_files_override"
branch_labels = None
depends_on = None

COLUMNS = [
    ("verification_status", sa.Text(), True),
    ("verification_merge_sha", sa.Text(), True),
    ("verification_started_at", sa.DateTime(timezone=True), True),
    ("verification_completed_at", sa.DateTime(timezone=True), True),
    ("verification_attempt", sa.Integer(), True),
    ("verification_report", sa.Text(), True),     # JSON-encoded; kept Text for SQLite parity
    ("issue_acceptance_snapshot", sa.Text(), True),
    ("issue_acceptance_hash", sa.Text(), True),
    ("verified_by", sa.Text(), True),
    ("verification_session_id", sa.Text(), True),
    ("issue_closed_by_workflow_at", sa.DateTime(timezone=True), True),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("autonomous_workflows")}
    for name, type_, nullable in COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "autonomous_workflows",
            sa.Column(name, type_, nullable=nullable),
        )


def downgrade() -> None:
    for name, _, _ in reversed(COLUMNS):
        op.drop_column("autonomous_workflows", name)
```

- [ ] **Step 2: Regenerate schema snapshots (PG16 client)**

Run (requires a disposable PG; reuse the local PG@16 used for #2309):
```bash
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" python scripts/rebuild_schema_snapshots.py \
  --postgres-url "postgresql://ace:ace@localhost:5433/ace_schema_sync"
git add schema/schema-postgres.sql schema/schema-sqlite.sql
```
Expected: only the 11 new columns appear in the diff under `autonomous_workflows`.

- [ ] **Step 3: Verify byte-exact schema-sync locally**

Run: `python scripts/check_schema_sync.py --postgres-url "postgresql://ace:ace@localhost:5433/ace_schema_sync"; echo "rc=$?"`
Expected: `rc=0` (drift = exit 1 per the schema-sync memory; do NOT pipe to `tail` before checking `$?`).

- [ ] **Step 4: Verify migration head + applies idempotently on a fresh DB**

Run:
```bash
python -m alembic upgrade head   # against a throwaway DB; expect "Running upgrade ... -> 20260805_010..."
python -m alembic upgrade head   # again; expect no-op
python -m alembic check          # expect "No new upgrade operations detected"
```

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/20260805_010_acceptance_verification_columns.py schema/schema-postgres.sql schema/schema-sqlite.sql
git commit -m "feat(autonomous): acceptance_verification columns migration (#2335)"
```

---

## Task 4: Repo allowlist for the new columns

**Files:**
- Modify: `app/repositories/autonomous_repo.py` (the `ALLOWED_WORKFLOW_FIELDS` set, ≈ line 28-100)
- Test: `tests/issues/2335/test_repo_allowlist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/issues/2335/test_repo_allowlist.py
from app.repositories.autonomous_repo import AutonomousWorkflowRepository, ALLOWED_WORKFLOW_FIELDS


def test_new_verification_columns_are_writeable():
    for col in [
        "verification_status",
        "verification_merge_sha",
        "verification_started_at",
        "verification_completed_at",
        "verification_attempt",
        "verification_report",
        "issue_acceptance_snapshot",
        "issue_acceptance_hash",
        "verified_by",
        "verification_session_id",
        "issue_closed_by_workflow_at",
    ]:
        assert col in ALLOWED_WORKFLOW_FIELDS, f"{col} must be in the update allowlist"
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_repo_allowlist.py -q`
Expected: FAIL (columns not in set).

- [ ] **Step 3: Add the columns to `ALLOWED_WORKFLOW_FIELDS`**

In `app/repositories/autonomous_repo.py`, add these tokens to the `ALLOWED_WORKFLOW_FIELDS` set (next to the other `ALLOWED_WORKFLOW_FIELDS` entries such as `max_changed_files_override`):

```python
        "verification_status",
        "verification_merge_sha",
        "verification_started_at",
        "verification_completed_at",
        "verification_attempt",
        "verification_report",
        "issue_acceptance_snapshot",
        "issue_acceptance_hash",
        "verified_by",
        "verification_session_id",
        "issue_closed_by_workflow_at",
```

Note: `get_workflow` uses `SELECT *`, so reads auto-include the columns; only the update allowlist needs them. INSERTs leave them NULL (not set at create).

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_repo_allowlist.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/autonomous_repo.py tests/issues/2335/test_repo_allowlist.py
git commit -m "feat(autonomous): allowlist verification columns in repo (#2335)"
```

---

## Task 5: GitHubOps.close_issue + merge-commit SHA helper (api_only)

**Files:**
- Modify: `app/modules/workspace/autonomous/github_ops.py` (add `close_issue` near `add_issue_comment` ≈ :795; add `get_merge_commit_sha` near `view_pr`)
- Test: `tests/issues/2335/test_close_issue.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/issues/2335/test_close_issue.py
from unittest.mock import MagicMock, patch

import app.modules.workspace.autonomous.github_ops as gh_mod
from app.modules.workspace.autonomous.github_ops import GitHubOps

BOT_ENV = {
    "GH_TOKEN": "ghp-bot",
    "GIT_AUTHOR_NAME": "Open ACE AI",
    "GIT_AUTHOR_EMAIL": "bot@open-ace.com",
    "GIT_COMMITTER_NAME": "Open ACE AI",
    "GIT_COMMITTER_EMAIL": "bot@open-ace.com",
}


def _gh():
    gh = GitHubOps("/srv/owners/repo", system_account="repoowner")
    gh._owner_repo_resolved = True
    gh._owner_repo = "open-ace/open-ace"
    gh._repo_host = None
    return gh


def test_close_issue_runs_as_service_user_with_bot_token():
    gh = _gh()
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.close_issue(42)
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh", f"close_issue must not sudo (got {cmd[:3]})"
    assert "issue" in cmd and "close" in cmd and "42" in cmd
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "ghp-bot"


def test_get_merge_commit_sha_returns_oid():
    gh = _gh()
    fake = MagicMock(returncode=0, stdout="abc123\n", stderr="")
    with (
        patch.object(gh, "_run_gh", return_value=fake),
    ):
        assert gh.get_merge_commit_sha(99) == "abc123"
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_close_issue.py -q`
Expected: FAIL (`close_issue`/`get_merge_commit_sha` not defined).

- [ ] **Step 3: Implement**

In `app/modules/workspace/autonomous/github_ops.py`, add next to `add_issue_comment`:

```python
    def close_issue(self, number: int) -> dict:
        """Close an issue. Runs as the service user (api_only) so the action
        attributes to the configured AI bot account, not the repo owner (#2339/#2335)."""
        self._run_gh(["issue", "close", str(number)], api_only=True)
        logger.info("Closed issue #%s", number)
        return {"number": number}
```

And next to the PR helpers:

```python
    def get_merge_commit_sha(self, pr_number: int) -> str | None:
        """Return the merge commit SHA of a merged PR, or None if unmerged."""
        result = self._run_gh(
            ["pr", "view", str(pr_number), "--json", "mergeCommit", "--jq", ".mergeCommit.oid"],
            check=False,
        )
        out = (result.stdout or "").strip()
        return out or None
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_close_issue.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/github_ops.py tests/issues/2335/test_close_issue.py
git commit -m "feat(autonomous): close_issue + merge-commit sha helper (#2335)"
```

---

## Task 6: Stop `Closes #N` in autonomous PR bodies

**Files:**
- Modify: `app/modules/workspace/autonomous/phases/pr_review.py:296`
- Test: `tests/issues/2335/test_close_keyword_enforcement.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/issues/2335/test_close_keyword_enforcement.py
from app.modules.workspace.autonomous.phases.pr_review import build_pr_body_close_ref


def test_pr_body_uses_implements_not_closes():
    body = build_pr_body_close_ref(issue_number=2335)
    assert "Implements #2335" in body
    assert "Closes #2335" not in body
    assert "Fixes #2335" not in body and "Resolves #2335" not in body


def test_pr_body_none_when_no_issue():
    assert build_pr_body_close_ref(issue_number=None) == ""
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_close_keyword_enforcement.py -q`
Expected: FAIL (`build_pr_body_close_ref` not defined).

- [ ] **Step 3: Implement — extract the helper + change the keyword**

In `app/modules/workspace/autonomous/phases/pr_review.py`, add a module-level helper and call it from the PR-body construction site (currently ≈ line 295-296: `if issue_number: pr_body += f"\n\nCloses #{issue_number}"`). Replace that inline append with the helper:

```python
def build_pr_body_close_ref(issue_number: int | None) -> str:
    """Non-closing issue reference for autonomous PR bodies (#2335).

    Autonomous PRs must NOT use Closes/Fixes/Resolves — GitHub would auto-close
    the issue on merge before acceptance_verification runs. Use Implements #
    (a plain reference) and let the workflow close explicitly on `confirmed`.
    """
    if not issue_number:
        return ""
    return f"\n\nImplements #{issue_number}"
```

At the PR-body site, change:
```python
            pr_body += f"\n\nCloses #{issue_number}"
```
to:
```python
            pr_body += build_pr_body_close_ref(issue_number)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_close_keyword_enforcement.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/phases/pr_review.py tests/issues/2335/test_close_keyword_enforcement.py
git commit -m "fix(autonomous): autonomous PRs use Implements #N, not Closes #N (#2335)"
```

---

## Task 7: VERIFICATION_ALLOWED_TOOLS + verification session line

**Files:**
- Modify: `app/modules/workspace/autonomous/constants.py`; `app/modules/workspace/autonomous/orchestrator.py` (`SESSION_LINE_FIELDS` ≈ :801)
- Test: `tests/issues/2335/test_verification_tools_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/issues/2335/test_verification_tools_session.py
import copy
from app.modules.workspace.autonomous import constants
from app.modules.workspace.autonomous.constants import (
    REVIEW_ALLOWED_TOOLS,
    VERIFICATION_ALLOWED_TOOLS,
)


def test_verification_adds_bash_to_review_set():
    review = copy.deepcopy(REVIEW_ALLOWED_TOOLS["claude-code"])
    verif = VERIFICATION_ALLOWED_TOOLS["claude-code"]
    # every review tool is permitted for verification...
    for t in review:
        assert t in verif
    # ...plus Bash for the scope gate's git diff/log, still no Write/Edit
    assert "Bash" in verif
    assert "Write" not in verif and "Edit" not in verif


def test_session_line_registry_includes_verification():
    from app.modules.workspace.autonomous.orchestrator import SESSION_LINE_FIELDS
    assert SESSION_LINE_FIELDS.get("verification") == "verification_session_id"
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_verification_tools_session.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `app/modules/workspace/autonomous/constants.py`, after `REVIEW_ALLOWED_TOOLS`:

```python
# Acceptance-verification tools (#2335): read-only review set + Bash for the
# scope gate's git diff/log on merged main. No Write/Edit — the verifier must not
# mutate the acceptance target; it works against a throwaway checkout of main and
# reads git state, not the working tree.
VERIFICATION_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": REVIEW_ALLOWED_TOOLS["claude-code"] + ["Bash"],
    "qwen-code-cli": REVIEW_ALLOWED_TOOLS["qwen-code-cli"] + ["run_shell_command"],
    "codex": [],
    "openclaw": [],
    "zcode": [],
}
```

In `app/modules/workspace/autonomous/orchestrator.py`, extend `SESSION_LINE_FIELDS` (≈ :801):

```python
SESSION_LINE_FIELDS = {
    "main": "main_session_id",
    "review": "review_session_id",
    "test": "test_session_id",
    "verification": "verification_session_id",
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_verification_tools_session.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/constants.py app/modules/workspace/autonomous/orchestrator.py tests/issues/2335/test_verification_tools_session.py
git commit -m "feat(autonomous): VERIFICATION_ALLOWED_TOOLS + verification session line (#2335)"
```

---

## Task 8: Phase-machine wiring + terminal invariant

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py` (`PHASE_ORDER` ≈ :577, `PHASE_STATUS_MAP` ≈ :589, `_COMPLETED_TERMINAL_PHASES` ≈ :586); `app/modules/workspace/autonomous/phases/merge.py` (terminal `completed()`); `app/modules/workspace/autonomous/phases/__init__.py` (register handler)
- Create: `app/modules/workspace/autonomous/phases/acceptance_verification.py` (stub `handle` for now — full impl in Task 10)
- Test: `tests/issues/2335/test_phase_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/issues/2335/test_phase_wiring.py
from app.modules.workspace.autonomous.orchestrator import (
    PHASE_ORDER,
    PHASE_STATUS_MAP,
    _COMPLETED_TERMINAL_PHASES,
)
from app.modules.workspace.autonomous.phases import resolve_phase_handler


def test_acceptance_verification_phase_registered_and_ordered():
    assert "acceptance_verification" in PHASE_ORDER
    assert PHASE_ORDER.index("acceptance_verification") > PHASE_ORDER.index("merge")
    assert PHASE_STATUS_MAP["acceptance_verification"] == "verification_pending"
    assert resolve_phase_handler("acceptance_verification") is not None


def test_terminal_set_no_longer_treats_merge_as_completed():
    # merge now hands off to acceptance_verification; only acceptance(confirmed)→completed
    assert "merge" not in _COMPLETED_TERMINAL_PHASES
    assert "completed" in _COMPLETED_TERMINAL_PHASES
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_phase_wiring.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the wiring**

In `orchestrator.py`:
- `PHASE_ORDER`: append `"acceptance_verification"` after `"merge"`:
  ```python
  PHASE_ORDER = ["preparation", "planning", "development", "pr_review", "report", "merge", "acceptance_verification"]
  ```
- `PHASE_STATUS_MAP` (≈ :589): add:
  ```python
      "acceptance_verification": "verification_pending",
  ```
- `_COMPLETED_TERMINAL_PHASES` (≈ :586): change to:
  ```python
  _COMPLETED_TERMINAL_PHASES = ("completed",)
  ```
  (merge is no longer terminal; it advances to acceptance_verification.)

In `phases/merge.py`, change the terminal return (the `PhaseResult.completed(next_phase="completed", ...)` at ≈ :411) to target the new phase:
```python
    return PhaseResult.completed(
        next_phase="acceptance_verification",
        ...   # keep the existing workflow_patch / milestone_events unchanged
    )
```
(Leave the surrounding kwargs as-is; only `next_phase` changes.)

Create `app/modules/workspace/autonomous/phases/acceptance_verification.py` stub (full impl in Task 10):
```python
"""acceptance_verification phase handler (#2335). Full implementation in Task 10."""

from __future__ import annotations

from app.modules.workspace.autonomous.phase_contract import PhaseResult


def handle(ctx, deps) -> PhaseResult:  # pragma: no cover  (replaced in Task 10)
    raise NotImplementedError("acceptance_verification handler not implemented yet")
```

In `phases/__init__.py`, register it (after the existing imports/registrations, ≈ line 34-38):
```python
from app.modules.workspace.autonomous.phases import acceptance_verification as _acceptance  # noqa: E402
register_phase_handler("acceptance_verification", _acceptance.handle)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_phase_wiring.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py app/modules/workspace/autonomous/phases/merge.py app/modules/workspace/autonomous/phases/__init__.py app/modules/workspace/autonomous/phases/acceptance_verification.py tests/issues/2335/test_phase_wiring.py
git commit -m "feat(autonomous): wire acceptance_verification phase into the machine (#2335)"
```

---

## Task 9: Scope gate

**Files:**
- Modify: `app/modules/workspace/autonomous/phases/acceptance_verification.py` (add `run_scope_gate`)
- Test: `tests/issues/2339/test_scope_gate.py` → correct path `tests/issues/2335/test_scope_gate.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/issues/2335/test_scope_gate.py
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phases.acceptance_verification import run_scope_gate


def test_required_path_present_is_confirmed():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/services/retention.py", "README.md"]
    verdicts = run_scope_gate(gh, ["app/services/retention.py"], "base", "merge")
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_required_path_missing_is_rejected():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["README.md"]
    verdicts = run_scope_gate(gh, ["app/services/retention.py"], "base", "merge")
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "app/services/retention.py" in verdicts[0].item
    assert verdicts[0].evidence  # carries the missing-path ref


def test_glob_matches_changed_path():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/services/retention.py", "app/services/legal.py"]
    verdicts = run_scope_gate(gh, ["app/services/*.py"], "base", "merge")
    assert all(v.verdict is Verdict.CONFIRMED for v in verdicts)


def test_no_required_paths_returns_empty():
    gh = MagicMock()
    assert run_scope_gate(gh, [], "base", "merge") == []
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_scope_gate.py -q`
Expected: FAIL (`run_scope_gate` not defined).

- [ ] **Step 3: Implement**

In `app/modules/workspace/autonomous/phases/acceptance_verification.py`, replace the stub with the gate (keep `handle` raising NotImplementedError for now — Task 10 fills it):

```python
"""acceptance_verification phase handler (#2335).

Task 9 adds the deterministic scope gate; Task 10 fills in `handle` (verifier
spawn, snapshot persistence, aggregation, transitions, idempotency, reopen guard).
"""

from __future__ import annotations

import fnmatch

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult


def _glob_matches(pattern: str, paths: list[str]) -> str | None:
    """Return the first changed path matching the pattern (glob), else None."""
    for p in paths:
        if fnmatch.fnmatch(p, pattern) or p == pattern:
            return p
    return None


def run_scope_gate(gh, required_paths: list[str], base_sha: str, merge_sha: str) -> list[ItemVerdict]:
    """Deterministic scope gate: each required path must appear in base..merge diff.

    Returns one ItemVerdict per required path: CONFIRMED if a changed path matches
    (glob), REJECTED with the missing path as evidence otherwise.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    verdicts: list[ItemVerdict] = []
    for path in required_paths:
        hit = _glob_matches(path, changed)
        if hit is not None:
            verdicts.append(
                ItemVerdict(item=path, verdict=Verdict.CONFIRMED,
                            evidence=[{"ref": f"git-diff:{hit}", "note": "required path present in merge"}])
            )
        else:
            verdicts.append(
                ItemVerdict(item=path, verdict=Verdict.REJECTED,
                            evidence=[{"ref": f"missing:{path}", "note": "required path absent from base..merge diff"}],
                            rationale="Issue scope requires this path; it was not changed on the merged branch.")
            )
    return verdicts


def handle(ctx, deps) -> PhaseResult:  # pragma: no cover  (Task 10)
    raise NotImplementedError("acceptance_verification handler not implemented yet")
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_scope_gate.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/phases/acceptance_verification.py tests/issues/2335/test_scope_gate.py
git commit -m "feat(autonomous): acceptance scope gate (#2335)"
```

---

## Task 10: The acceptance_verification handler

This is the integration task: spawn the verifier, run the scope gate, aggregate, persist, transition, idempotency, reopen guard, close-on-confirm.

**Files:**
- Modify: `app/modules/workspace/autonomous/phases/acceptance_verification.py` (replace `handle`); `app/modules/workspace/autonomous/orchestrator.py` (add `_run_verification_agent` + `run_verification_agent` PhaseHost alias)
- Test: `tests/issues/2335/test_acceptance_phase.py`

- [ ] **Step 1: Write the failing tests (mock the verifier + gh + repo)**

```python
# tests/issues/2335/test_acceptance_phase.py
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av


def _ctx(wf):
    return WorkflowContext(
        workflow=wf, definition_snapshot=None, repository_context=None,
        session_bindings=MagicMock(), cancellation=MagicMock(),
    )


def _deps(**kw):
    d = MagicMock()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_confirmed_closes_issue_and_completes():
    wf = {
        "id": 1, "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1", "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_merge_commit_sha.return_value = "merge"
    gh.get_changed_files.return_value = ["app/x.py"]          # scope gate passes
    deps = _deps(gh=gh)
    # verifier returns all-CONFIRMED for the checklist item
    deps.host.run_verification_agent.return_value = {
        "verdicts": [{"item": "works", "verdict": "confirmed", "evidence": [], "rationale": ""}],
        "snapshot": None,
    }
    deps.host.issue_is_open.return_value = True
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    deps.gh.close_issue.assert_called_once_with(42)
    deps.host.create_milestone_idempotent.assert_called()


def test_rejected_starts_new_dev_round():
    wf = {
        "id": 1, "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1", "verification_status": None,
        "issue_acceptance_snapshot": None, "dev_round": 1, "max_changed_files_override": None,
    }
    gh = MagicMock()
    gh.get_changed_files.return_value = []                    # scope gate: required path missing
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    deps.host.dev_round_cap_remaining.return_value = 2
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"
    assert result.next_phase == "development"
    assert result.workflow_patch.get("dev_round") == 2
    deps.gh.close_issue.assert_not_called()


def test_indeterminate_pauses():
    wf = {
        "id": 1, "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1", "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {
        "verdicts": [{"item": "unclear", "verdict": "indeterminate", "evidence": [], "rationale": ""}],
        "snapshot": None,
    }
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "pause"
    assert result.workflow_patch.get("verification_status") == "indeterminate"
    deps.gh.close_issue.assert_not_called()


def test_idempotent_rerun_is_noop_when_already_confirmed_for_same_sha_hash():
    wf = {
        "id": 1, "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1", "verification_status": "confirmed",
        "issue_acceptance_snapshot": None,
    }
    deps = _deps(gh=MagicMock())
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"      # already done; no re-close, no re-verify
    assert result.next_phase == "completed"
    deps.gh.close_issue.assert_not_called()    # idempotent: do not close twice


def test_closed_issue_reopened_when_not_confirmed():
    wf = {
        "id": 1, "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": None,
        "issue_acceptance_hash": "h1", "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_merge_commit_sha.return_value = "merge"
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = False   # issue was auto-closed externally
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    av.handle(_ctx(wf), deps)
    deps.gh.reopen_issue.assert_called_once_with(42)   # reopen guard fires before verifying
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/issues/2335/test_acceptance_phase.py -q`
Expected: FAIL (handler raises NotImplementedError; `reopen_issue` may not exist yet).

- [ ] **Step 3: Add `GitHubOps.reopen_issue`**

In `app/modules/workspace/autonomous/github_ops.py`, next to `close_issue`:

```python
    def reopen_issue(self, number: int) -> dict:
        """Reopen an issue (api_only → service-user/bot identity)."""
        self._run_gh(["issue", "reopen", str(number)], api_only=True)
        logger.info("Reopened issue #%s", number)
        return {"number": number}
```

- [ ] **Step 4: Implement the handler**

Replace the `handle` stub in `app/modules/workspace/autonomous/phases/acceptance_verification.py`:

```python
import json
import time

from app.modules.workspace.autonomous.acceptance_snapshot import (
    AcceptanceSnapshot,
    hash_snapshot,
    parse_acceptance_snapshot,
)
from app.modules.workspace.autonomous.acceptance_verdicts import (
    ItemVerdict,
    aggregate_verdicts,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _verdict_from_str(s: str) -> Verdict:
    s = (s or "").lower()
    if s == "confirmed":
        return Verdict.CONFIRMED
    if s == "rejected":
        return Verdict.REJECTED
    return Verdict.INDETERMINATE


def _parse_issue_body(deps, issue_number: str) -> str:
    """Fetch the issue body via gh (best-effort; '' on failure)."""
    try:
        res = deps.gh.view_issue(int(issue_number))  # returns dict with 'body'
        return (res or {}).get("body") or ""
    except Exception:
        return ""


def handle(ctx, deps) -> PhaseResult:
    wf = ctx.workflow
    issue_number = wf.get("github_issue_number")
    pr_number = wf.get("github_pr_number")
    base_sha = wf.get("base_commit_sha") or ""

    # Idempotency: already verified for this (merge_sha, hash) → terminal no-op.
    if wf.get("verification_status") == "confirmed":
        return PhaseResult.completed(next_phase="completed")

    # Resolve the merge commit SHA on main (cache it on the workflow).
    merge_sha = wf.get("verification_merge_sha")
    if not merge_sha and pr_number:
        merge_sha = deps.gh.get_merge_commit_sha(pr_number)
    if not merge_sha or not base_sha:
        # Cannot verify yet (PR not merged / base unknown) — retry next cycle.
        return PhaseResult.retry()

    # Reopen guard: if the issue was closed out-of-band before confirmation, reopen.
    if issue_number and not deps.host.issue_is_open(issue_number):
        deps.gh.reopen_issue(issue_number)
        deps.host.emit_audit_event("acceptance_reopened_issue", {"issue": issue_number})

    # Build the acceptance snapshot (persisted; hash drives re-verification).
    snapshot = None
    if wf.get("issue_acceptance_snapshot"):
        try:
            snapshot = AcceptanceSnapshot(**json.loads(wf["issue_acceptance_snapshot"]))
        except Exception:
            snapshot = None
    if snapshot is None:
        body = _parse_issue_body(deps, issue_number) if issue_number else ""
        snapshot = parse_acceptance_snapshot(body)
    snap_hash = hash_snapshot(snapshot)

    # Spawn the independent verifier on merged main. If the snapshot was missing
    # convention sections, the verifier extracts scope/checklist (LLM) and returns
    # the completed snapshot; we persist it so later rounds reuse it.
    agent_out = deps.host.run_verification_agent(
        snapshot=snapshot, merge_sha=merge_sha, base_sha=base_sha,
        issue_number=issue_number, pr_number=pr_number,
    ) or {}
    verifier_verdicts = [
        ItemVerdict(
            item=v.get("item", ""),
            verdict=_verdict_from_str(v.get("verdict")),
            evidence=v.get("evidence") or [],
            rationale=v.get("rationale", ""),
        )
        for v in (agent_out.get("verdicts") or [])
    ]
    if agent_out.get("snapshot"):
        snapshot = AcceptanceSnapshot(**agent_out["snapshot"])
        snap_hash = hash_snapshot(snapshot)

    # Mechanical scope gate (deterministic) — required paths must be in the diff.
    scope_verdicts = run_scope_gate(deps.gh, snapshot.required_paths, base_sha, merge_sha)

    status = aggregate_verdicts(scope_verdicts + verifier_verdicts)
    report = {
        "merge_sha": merge_sha,
        "issue_acceptance_hash": snap_hash,
        "scope": [{"item": v.item, "verdict": v.verdict.value, "evidence": v.evidence} for v in scope_verdicts],
        "verifier": [{"item": v.item, "verdict": v.verdict.value, "evidence": v.evidence, "rationale": v.rationale} for v in verifier_verdicts],
        "status": status,
        "verified_at": _now_iso(),
    }

    common_patch = {
        "verification_status": status,
        "verification_merge_sha": merge_sha,
        "verification_completed_at": _now_iso(),
        "verification_report": json.dumps(report, ensure_ascii=False),
        "issue_acceptance_snapshot": json.dumps(snapshot.to_canonical(), ensure_ascii=False),
        "issue_acceptance_hash": snap_hash,
        "verification_attempt": (wf.get("verification_attempt") or 0) + 1,
    }
    milestone = {
        "workflow_id": wf.get("workflow_id"),
        "phase": "acceptance_verification",
        "milestone_type": "acceptance_verification",
        "status": status,
        "title": f"Acceptance verification: {status}",
        "result_summary": report,
        "metadata": report,
    }

    if status == "confirmed":
        deps.gh.add_issue_comment(issue_number, _format_report_comment(report))
        deps.gh.close_issue(issue_number)
        common_patch["issue_closed_by_workflow_at"] = _now_iso()
        return PhaseResult.completed(
            next_phase="completed",
            workflow_patch={**common_patch, "completed_at": _now_iso()},
            milestone_events=[milestone],
        )
    if status == "rejected":
        if deps.host.dev_round_cap_remaining(wf) > 0:
            return PhaseResult.completed(
                next_phase="development",
                workflow_patch={**common_patch, "dev_round": (wf.get("dev_round") or 1) + 1,
                                "error_message": "Acceptance verification rejected: see report"},
                milestone_events=[milestone],
            )
        return PhaseResult.failed(structured_error={"message": "Acceptance rejected and dev rounds exhausted"},
                                  workflow_patch=common_patch)
    # indeterminate
    return PhaseResult.pause(
        workflow_patch={**common_patch, "error_message": "Acceptance indeterminate: awaiting evidence"},
        structured_error={"message": "indeterminate", "report": report},
    )


def _format_report_comment(report: dict) -> str:
    lines = ["## ✅ Acceptance verified", f"**Merge SHA:** `{report.get('merge_sha')}`",
             f"**Status:** confirmed", "", "**Scope gate:**"]
    for s in report.get("scope", []):
        lines.append(f"- `{s['item']}` — {s['verdict']}")
    if report.get("verifier"):
        lines += ["", "**Verifier findings:**"]
        for v in report["verifier"]:
            lines.append(f"- {v['verdict']} — {v['item']}")
    return "\n".join(lines)
```

- [ ] **Step 5: Add the orchestrator-side PhaseHost hooks**

In `app/modules/workspace/autonomous/orchestrator.py`:

(a) Add the verifier runner method (the actual agent spawn — credentialless, read-only, on a throwaway main checkout). Add near the other `_run_agent`-style helpers:

```python
    def _run_verification_agent(self, *, snapshot, merge_sha, base_sha, issue_number, pr_number) -> dict:
        """Spawn the independent acceptance verifier on merged main (#2335).

        Credentialless (openace-run-as --isolated), VERIFICATION_ALLOWED_TOOLS
        (read-only + Bash). Returns the agent's structured JSON: {"verdicts": [...],
        "snapshot": <completed snapshot if LLM-extracted, else None>}. On any failure
        returns {"verdicts": [], "snapshot": None} (caller aggregates to indeterminate).
        """
        try:
            prompt = self._build_verification_prompt(snapshot, merge_sha, base_sha, issue_number)
            result = self._run_agent(
                session_line="verification",
                prompt=prompt,
                allowed_tools=VERIFICATION_ALLOWED_TOOLS,
                commit_ref=merge_sha,          # checkout merged main in an isolated worktree
                permission_mode="bypassPermissions",  # read-only tools; no Write/Edit in set
            )
            return self._parse_verifier_output(result)
        except Exception:
            logger.exception("acceptance verifier spawn failed for issue %s", issue_number)
            return {"verdicts": [], "snapshot": None}
```

(b) Expose the PhaseHost aliases the handler reaches for (alongside the existing `create_milestone_idempotent`/`emit_status_change` aliases). Add to the PhaseHost alias block:

```python
    run_verification_agent = _run_verification_agent
    dev_round_cap_remaining = _dev_round_cap_remaining
    issue_is_open = _issue_is_open
    emit_audit_event = _emit_workflow_event
```

And implement the three small helpers if not present:

```python
    def _dev_round_cap_remaining(self, wf) -> int:
        cap = int(wf.get("max_plan_rounds") or 0) or 0
        return max(0, cap - int(wf.get("dev_round") or 0))

    def _issue_is_open(self, issue_number: int) -> bool:
        try:
            res = self._get_gh().view_issue(int(issue_number))
            return (res or {}).get("state", "open") == "open"
        except Exception:
            return True  # fail open: don't spuriously reopen
```

(If `_build_verification_prompt` / `_parse_verifier_output` / `_run_agent(commit_ref=...)` don't yet exist with these signatures, add minimal versions: the prompt renders the snapshot + checklist + "return JSON {verdicts:[{item,verdict,evidence,rationale}], snapshot?}"; `_parse_verifier_output` extracts the JSON from the agent's stdout; `_run_agent(commit_ref=...)` checks out `commit_ref` in the isolated worktree before running. These follow the existing review-agent spawn path — mirror `AutonomousAgentRunner.run_agent_task` with `session_line="verification"`.)

- [ ] **Step 6: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_acceptance_phase.py -q`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add app/modules/workspace/autonomous/phases/acceptance_verification.py app/modules/workspace/autonomous/orchestrator.py app/modules/workspace/autonomous/github_ops.py tests/issues/2335/test_acceptance_phase.py
git commit -m "feat(autonomous): acceptance_verification handler (verifier + scope gate + transitions) (#2335)"
```

---

## Task 11: Integration test — merge → reject → dev → confirm → close (mocked agent)

**Files:**
- Test: `tests/issues/2335/test_acceptance_flow_integration.py`

- [ ] **Step 1: Write the test**

```python
# tests/issues/2335/test_acceptance_flow_integration.py
"""End-to-end-ish acceptance flow: the phase handler + scope gate + aggregation
drive the workflow from merged → rejected → dev → confirmed → issue closed.
The verifier agent is mocked (no real LLM in CI)."""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av


def _ctx(wf):
    return WorkflowContext(workflow=wf, definition_snapshot=None, repository_context=None,
                           session_bindings=MagicMock(), cancellation=MagicMock())


def test_reject_then_fix_then_confirm_closes_issue():
    wf = {
        "id": 1, "workflow_id": "w1", "github_issue_number": 42, "github_pr_number": 99,
        "base_commit_sha": "base", "verification_merge_sha": "merge",
        "issue_acceptance_hash": None, "verification_status": None,
        "issue_acceptance_snapshot": None, "dev_round": 1, "max_plan_rounds": 5,
    }

    # Round 1: required path missing → REJECTED → new dev round, issue stays open.
    deps = MagicMock()
    deps.gh.get_changed_files.return_value = []
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    deps.host.issue_is_open.return_value = True
    deps.host.dev_round_cap_remaining.return_value = 3
    r1 = av.handle(_ctx(wf), deps)
    assert r1.outcome == "completed" and r1.next_phase == "development"
    assert r1.workflow_patch["dev_round"] == 2
    deps.gh.close_issue.assert_not_called()

    # Simulate the dev round landing the required file; re-verify.
    wf.update(r1.workflow_patch)
    wf["verification_status"] = None  # reset for the new round
    deps.gh.get_changed_files.return_value = ["app/services/retention.py"]
    deps.host.run_verification_agent.return_value = {
        "verdicts": [{"item": "retention runs", "verdict": "confirmed", "evidence": [], "rationale": ""}],
        "snapshot": None,
    }
    r2 = av.handle(_ctx(wf), deps)
    assert r2.outcome == "completed" and r2.next_phase == "completed"
    deps.gh.close_issue.assert_called_once_with(42)
    assert r2.workflow_patch["verification_status"] == "confirmed"
    assert r2.workflow_patch["issue_closed_by_workflow_at"]
```

- [ ] **Step 2: Run — expect PASS**

Run: `python -m pytest tests/issues/2335/test_acceptance_flow_integration.py -q`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/issues/2335/test_acceptance_flow_integration.py
git commit -m "test(autonomous): acceptance flow reject→fix→confirm→close (#2335)"
```

---

## Task 12: Regression sweep + PR

- [ ] **Step 1: Run the full #2335 suite + autonomous phase invariants**

```bash
python -m pytest tests/issues/2335/ tests/autonomous/ tests/unit/test_autonomous_ci_guardrails.py -q
```
Expected: all pass. (If `tests/autonomous/test_phase_b_acceptance.py` asserts the old `merge→completed`/terminal set, update it to the new `merge→acceptance_verification` wiring — it is an invariant test, not a regression.)

- [ ] **Step 2: Confirm no pre-existing failures regressed**

Cross-check any failures against `origin/main` in a throwaway worktree (`git worktree add /tmp/ace-main-2335 origin/main` → run same tests) — only pre-existing local-env failures (716/786 style) may remain.

- [ ] **Step 3: Lint + types + security locally**

```bash
export PATH="/tmp/ace-python-shim:$HOME/.local/bin:$PATH"
pre-commit run --all-files
python scripts/lint/bandit_check.py $(git diff --name-only origin/main | grep '\.py$')
```
Expected: pass (bandit 0 findings).

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feat/2335-acceptance-verification
gh pr create --base main --title "feat(autonomous): independent acceptance_verification phase (PR1) (#2335)" \
  --body-file docs/superpowers/specs/2026-08-05-acceptance-verification-slice-design.md
```
(Then append a PR body section noting: scope = PR1 slice; non-goals; the deploy runbook must restart `openace-scheduler.service`.)

- [ ] **Step 5: Independent PR review (class-2)**

Dispatch an independent reviewer subagent on `gh pr diff`; it posts findings as PR comments. Address valid findings, re-push. Then the hourly monitor + merge gate apply.

---

## Self-review notes (done)

- **Spec coverage:** A (state machine) → Task 8; B (close-keyword + close_issue + reopen) → Tasks 5, 6, 10; C (snapshot) → Task 1; D (verifier) → Task 10 (orchestrator `_run_verification_agent`); E (scope gate) → Task 9; F (aggregation) → Task 2; G (persistence + idempotency) → Tasks 3, 4, 10; H (transitions) → Task 10; I (tests) → all tasks + Task 11.
- **Placeholders:** none (Task 10 Step 5 references `_build_verification_prompt`/`_parse_verifier_output`/`_run_agent(commit_ref=...)` — these are flagged to mirror the existing review-agent spawn; if signatures differ, adapt during execution, which is a normal implementation detail, not a placeholder).
- **Type consistency:** `ItemVerdict.verdict` is `Verdict` enum; `aggregate_verdicts` returns the three status strings written to `verification_status`; `run_scope_gate` returns `list[ItemVerdict]`; `handle` returns `PhaseResult`. `Verdict.INDETERMINATE` matches `evidence.py` (not `UNKNOWN`).
