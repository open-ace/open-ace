# #2658 Implementation Plan: unify acceptance-paused user actions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every acceptance-paused workflow (rejected OR indeterminate) offers its owner/admin two page exits — accept (override → close issue → completed) or resume-with-feedback — plus an in-page full verification-report viewer.

**Architecture:** Single backend route change (`acceptance_verification_override`: status guard + permission), frontend gating unification in `WorkflowTimeline.tsx`, report viewer reusing the existing `viewingContent` generic modal fed by a pure formatter over `milestone.metadata` (already returned by `/timeline` — zero API change).

**Tech Stack:** Flask + pytest (unittest.mock), React + TS + Bootstrap, Playwright E2E (headless-first per project rule).

**Spec:** `docs/dev-notes/2658-acceptance-paused-user-actions-design.md`. Issue #2658.

---

### Task 1: Backend — extend `verification_override` to rejected + owner

**Files:**
- Test: `tests/unit/test_acceptance_override_2658.py` (new)
- Modify: `app/routes/autonomous.py` (`acceptance_verification_override`, ~L1312-1445)
- Modify: `tests/issues/2335/test_acceptance_override_route.py` (legacy opt-in lane — update semantics in place; do NOT create new files there)

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_acceptance_override_2658.py` — mirror the fixture style of `tests/issues/2335/test_acceptance_override_route.py` (mocked repo + patched `_load_user_from_token` + patched `GitHubOps`):

```python
"""#2658: acceptance override for rejected AND indeterminate, owner+admin.

Regression for the route extension: the workflow owner (not just admins) may
override a paused acceptance verdict — confirming it, closing the issue and
completing the workflow. Rejected verdicts are overridable; confirmed ones
still 400; unrelated users still 403.
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
        with ExitStack() as stack, patch(
            "app.modules.workspace.autonomous.github_ops.GitHubOps",
            return_value=MagicMock(),
        ):
            stack.enter_context(_mock_auth(user_id=1, role="admin", username="root"))
            resp = _post_override(client)
        assert resp.status_code == 200

    def test_unrelated_user_gets_403(self, app_client):
        client, _repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=99, role="user", username="stranger"))
            resp = _post_override(client)
        assert resp.status_code == 403


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

    def test_reason_over_2000_chars_400(self, app_client):
        client, _repo, _ = app_client
        with ExitStack() as stack:
            stack.enter_context(_mock_auth(user_id=1, role="admin"))
            resp = _post_override(client, {"reason": "x" * 2001})
        assert resp.status_code == 400
```

Note: check whether `pytest.mark.issue` is a registered marker (`pytest.ini` / a plugin). If not registered, add the marker declaration to `pytest.ini` — first grep `tests/unit/` for an existing `pytest.mark.issue(` usage; if it exists, the marker is already registered.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_acceptance_override_2658.py -v`
Expected: `test_owner_can_override_rejected` FAIL 403, `test_owner_can_override_indeterminate` FAIL 403, `test_admin_can_override_rejected` FAIL 400 (rejected not allowed yet). Permission/guard tests that assert old behavior unchanged (`test_unrelated_user_gets_403`, `test_confirmed_still_400`, `test_wrong_phase_400`, `test_reason_over_2000_chars_400`) may pass already — that's fine.

- [ ] **Step 3: Implement the route change**

In `app/routes/autonomous.py` `acceptance_verification_override`:

1. Docstring: replace the admin-only paragraph with:

```python
    """Human override: confirm an acceptance-paused workflow (#2335 S6, #2658).

    A workflow paused at ``acceptance_verification`` with a terminal
    ``verification_status`` of ``"indeterminate"`` (verifier could not reach a
    verdict) or ``"rejected"`` (verifier rejected, a human disagrees) cannot
    complete on its own. The workflow owner or an admin may inspect the merged
    code out of band and override the verdict to ``confirmed``: this stamps
    ``verified_by`` with the human identity, emits an audit event, posts a
    report (noting when a rejection was overturned), closes the issue as
    @open-ace-bot, and completes the workflow (resting at the
    ``acceptance_verification`` phase, consistent with the confirmed terminal
    default). The override bypasses the independent verifier, so it stays
    attributable: ``verified_by: human-override:<username>``.
    """
```

2. Permission (replace the `is_admin_role` block):

```python
    if not User.is_admin_role(g.user_role) and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403
```

3. Status guard (replace the `!= "indeterminate"` block):

```python
    prior_status = (workflow.get("verification_status") or "").strip()
    if prior_status not in ("indeterminate", "rejected"):
        return (
            jsonify(
                {
                    "error": (
                        "Override is only available for indeterminate or rejected "
                        "verification status"
                    )
                }
            ),
            400,
        )
```

4. Comment title (in the `lines = [...]` construction): make the first line conditional:

```python
            override_title = (
                "## ✅ Acceptance verified (human override — rejection overturned)"
                if prior_status == "rejected"
                else "## ✅ Acceptance verified (human override)"
            )
            lines = [
                override_title,
                f"**Merge SHA:** `{merge_sha}`" if merge_sha else "**Merge SHA:** _unknown_",
                f"**Verifier:** `{verified_by}`",
            ]
```

- [ ] **Step 4: Update the legacy 2335 test file in place**

`tests/issues/2335/test_acceptance_override_route.py`: `test_non_admin_override_returns_403` currently patches the OWNER (user_id=7, role user) and expects 403 — that user must now succeed. Change it to an unrelated user, and add an owner-success test:

```python
def test_non_owner_non_admin_override_returns_403(app_client):
    client, _repo, _ = app_client
    with ExitStack() as stack:
        for p in _mock_auth(role="user", user_id=99, username="stranger"):
            stack.enter_context(p)
        resp = _post_override(client)
    assert resp.status_code == 403


def test_owner_override_now_allowed_2658(app_client):
    """#2658: the workflow owner (role=user, user_id matching the row) may override."""
    client, _repo, _ = app_client
    with (
        ExitStack() as stack,
        patch(
            "app.modules.workspace.autonomous.github_ops.GitHubOps",
            return_value=MagicMock(),
        ),
    ):
        for p in _mock_auth(role="user", user_id=7, username="owner"):
            stack.enter_context(p)
        resp = _post_override(client)
    assert resp.status_code == 200
```

Also update the module docstring line "Non-admins get 403" → "Unrelated users get 403; the workflow owner may override (#2658)".

- [ ] **Step 5: Run both test files**

Run: `python -m pytest tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/autonomous.py tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py
git commit -m "feat(#2658): acceptance override for rejected+indeterminate, owner+admin"
```

---

### Task 2: Frontend — unified button gating + report viewer

**Files:**
- Modify: `frontend/src/components/work/WorkflowTimeline.tsx`
- Modify: `frontend/src/i18n/index.ts`

- [ ] **Step 1: Unify the gating constants**

Replace (near line 705 on main):

```tsx
  // #2335 S6: admin override for a paused indeterminate acceptance workflow.
  const showAcceptanceOverride =
    workflow.status === 'paused' && workflow.verification_status === 'indeterminate';
```

and

```tsx
  // #2634: a REJECTED acceptance needs new developer feedback to make progress;
  // resuming without it hits the idempotency guard and re-pauses immediately.
  const showResumeWithFeedback =
    workflow.status === 'paused' &&
    workflow.current_phase === 'acceptance_verification' &&
    workflow.verification_status === 'rejected';
```

with:

```tsx
  // #2658: every acceptance pause that needs a human offers BOTH exits —
  // accept (override → close issue → completed; owner+admin, enforced
  // server-side) and resume-with-feedback (new dev round → new merge → fresh
  // acceptance). Resuming a rejected verdict without feedback hits the
  // idempotency guard and re-pauses immediately, which is why the feedback
  // modal is the resume path.
  const isAcceptancePaused =
    workflow.status === 'paused' &&
    workflow.current_phase === 'acceptance_verification' &&
    (workflow.verification_status === 'rejected' ||
      workflow.verification_status === 'indeterminate');
  const showAcceptanceOverride = isAcceptancePaused;
  const showResumeWithFeedback = isAcceptancePaused;
```

- [ ] **Step 2: Add the report formatter (module level, next to `PLAN_CONTENT_TYPES`)**

```tsx
// #2658: render the acceptance milestone's verification report (stored in
// milestone.metadata as JSON by the acceptance phase) as readable text for
// the generic content viewer. Sections mirror _format_report_comment in
// acceptance_verification.py.
const VERDICT_TONE: Record<string, string> = {
  confirmed: '✅',
  rejected: '❌',
  indeterminate: '⚠️',
};

const formatAcceptanceReport = (metadata: string): string => {
  let report: Record<string, unknown>;
  try {
    report = JSON.parse(metadata) as Record<string, unknown>;
  } catch {
    return metadata;
  }
  const lines: string[] = [];
  const status = String(report.status ?? '');
  lines.push(`${VERDICT_TONE[status] ?? '⚠️'} ${status || 'unknown'}`);
  if (report.merge_sha) lines.push(`Merge SHA: ${String(report.merge_sha)}`);
  if (report.verified_by) lines.push(`Verifier: ${String(report.verified_by)}`);
  if (report.infra_error) lines.push(`Infra error: ${String(report.infra_error)}`);
  const sections: Array<[string, string]> = [
    ['scope', 'Scope'],
    ['gates', 'Gates'],
    ['verifier', 'Verifier findings'],
  ];
  for (const [key, label] of sections) {
    const items = report[key];
    if (!Array.isArray(items) || items.length === 0) continue;
    lines.push('', `── ${label} ──`);
    for (const raw of items) {
      if (!raw || typeof raw !== 'object') continue;
      const item = raw as Record<string, unknown>;
      const verdict = String(item.verdict ?? '');
      const icon = VERDICT_TONE[verdict] ?? '•';
      const tail = item.rationale ? ` — ${String(item.rationale)}` : '';
      lines.push(`${icon} ${String(item.item ?? '')}${tail}`);
      const evidence = item.evidence;
      if (Array.isArray(evidence)) {
        for (const ev of evidence) {
          if (!ev || typeof ev !== 'object') continue;
          const e = ev as Record<string, unknown>;
          const note = e.note ? ` (${String(e.note)})` : '';
          lines.push(`    ↳ ${String(e.ref ?? '')}${note}`);
        }
      }
    }
  }
  return lines.join('\n');
};
```

- [ ] **Step 3: Add the milestone-card button**

Next to `canViewReviewContent` in the milestone render (~L1495):

```tsx
    const canViewAcceptanceReport =
      !compact &&
      milestone.milestone_type === 'acceptance_verification' &&
      !!milestone.metadata?.trim();
```

And in the card actions after the `canViewReviewContent` button block (~L1733):

```tsx
                    {canViewAcceptanceReport && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--warning"
                        onClick={() =>
                          setViewingContent({
                            title: t('autoViewAcceptanceReportTitle', language),
                            content: formatAcceptanceReport(mstoneMetadata(milestone)),
                          })
                        }
                      >
                        <i className="bi bi-clipboard-check me-1"></i>
                        {t('autoViewAcceptanceReport', language)}
                      </Button>
                    )}
```

(If `milestone.metadata` is directly accessible at that point — it is, the map variable is `milestone` — use `milestone.metadata` inline instead of a helper: `content: formatAcceptanceReport(milestone.metadata)`.)

- [ ] **Step 4: i18n keys ×4 locales**

In `frontend/src/i18n/index.ts`, next to the existing `autoAcceptanceOverrideButton` entries in each of the 4 locale blocks (en/zh/ja/ko):

```ts
    autoViewAcceptanceReport: 'View acceptance report',
    autoViewAcceptanceReportTitle: 'Acceptance verification report',
```

zh:

```ts
    autoViewAcceptanceReport: '查看验收报告',
    autoViewAcceptanceReportTitle: '验收报告',
```

ja:

```ts
    autoViewAcceptanceReport: '受け入れレポートを表示',
    autoViewAcceptanceReportTitle: '受け入れ検証レポート',
```

ko:

```ts
    autoViewAcceptanceReport: '승인 리포트 보기',
    autoViewAcceptanceReportTitle: '승인 검증 리포트',
```

Also update `autoAcceptanceOverrideDesc` in each locale if its text says the action is admin-only — change to "workflow owner or admin" wording (zh: 「工作流所有者或管理员可确认验收并关闭 issue」).

- [ ] **Step 5: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/work/WorkflowTimeline.tsx frontend/src/i18n/index.ts
git commit -m "feat(#2658): unified acceptance-pause exits + in-page report viewer"
```

---

### Task 3: E2E (Playwright, headless first — project rule)

**Files:**
- Modify: `tests/e2e/work/e2e_paused_acceptance_ui_playwright.py`

- [ ] **Step 1: Extend the E2E**

In the existing file (it already seeds rejected + indeterminate workflows and drives the paused tab), add:

1. A milestone seed helper (the timeline API needs a milestone row so the report button renders):

```python
def seed_acceptance_milestone(repo, workflow_id: str, status: str) -> str:
    import json as _json

    report = {
        "merge_sha": "abc123",
        "status": status,
        "scope": [{"item": "src/app.py", "verdict": "confirmed", "evidence": []}],
        "gates": [],
        "verifier": [
            {
                "item": "修复登录失败",
                "verdict": "rejected",
                "evidence": [{"ref": "tests/test_login.py:12", "note": "用例仍然失败"}],
                "rationale": "合并后用例依旧失败",
            }
        ],
    }
    repo.create_milestone(
        {
            "workflow_id": workflow_id,
            "phase": "acceptance_verification",
            "round_number": 1,
            "milestone_type": "acceptance_verification",
            "status": status,
            "title": f"Acceptance verification: {status}",
            "result_summary": f"status={status}",
            "metadata": _json.dumps(report, ensure_ascii=False),
        }
    )
    return report
```

(Adapt to the file's existing `repo` seeding mechanism — `seed_workflow` uses the same repo object; keep the style consistent.)

2. Assertions, for BOTH the rejected and indeterminate seeded workflows:
   - 「带反馈恢复」 button visible (was rejected-only; indeterminate is the #2658 change),
   - 「确认验收（覆盖）」 button visible (rejected is the #2658 change),
   - open the workflow detail timeline, click 「查看验收报告」, assert the viewer modal shows the seeded item text (`修复登录失败`) and the evidence ref (`tests/test_login.py:12`).

- [ ] **Step 2: Run headless until green**

Run: `HEADLESS=true python scripts/run_extended_tests.py --category specific --target tests/e2e/work/e2e_paused_acceptance_ui_playwright.py --isolated-home --server auto --timeout 300`
Expected: PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/work/e2e_paused_acceptance_ui_playwright.py
git commit -m "test(#2658): e2e both exits on rejected+indeterminate + report viewer"
```

---

### Task 4: Full verification + PR

- [ ] **Step 1: Full unit suite locally**

Run: `python -m pytest tests/unit/ -q`
Expected: no new failures vs `origin/main`.

- [ ] **Step 2: pre-commit on changed files (NOT --all-files — scope-guard rule)**

Run: `pre-commit run --files app/routes/autonomous.py tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py frontend/src/components/work/WorkflowTimeline.tsx frontend/src/i18n/index.ts tests/e2e/work/e2e_paused_acceptance_ui_playwright.py`
Expected: clean (watch for pyupgrade `X | None`, TC003 TYPE_CHECKING moves, black 25.1.0 formatting).

- [ ] **Step 3: Push + PR**

```bash
git push -u origin fix/2658-acceptance-paused-user-actions
gh pr create --title "feat(#2658): unify acceptance-paused user actions" --body "<summary; NO closes/fixes/resolves keywords near #2658>"
```

PR body must reference the issue with "Refs #2658" only (GitHub auto-close trap).

- [ ] **Step 4: Required CI green** (lint / test(3.10) / test(3.11) / test(3.12) / build) → independent review (`superpowers:requesting-code-review` on `gh pr diff`) → merge `--merge --delete-branch` → deploy hotpatch (orchestrator/routes change needs scheduler restart; frontend build+deploy + open-ace.service restart) → verify on prod.

- [ ] **Step 5: headless=false demo to user** (project E2E rule step 3).
