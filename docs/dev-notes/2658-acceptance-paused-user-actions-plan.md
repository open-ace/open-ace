# #2658 Implementation Plan: unify acceptance-paused user actions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every acceptance-paused workflow (rejected OR indeterminate) offers its owner/admin two page exits — accept (override → close issue → completed) or resume-with-feedback — plus an in-page full verification-report viewer; resume-with-feedback actually delivers a FRESH verification (stale merge-SHA cache cleared).

**Architecture:** Backend route changes (`acceptance_verification_override`: status guard + permission reorder; `resume_with_feedback`: clear the verification cache), frontend gating unification in `WorkflowTimeline.tsx`, report viewer reusing the existing `viewingContent` generic modal fed by a pure formatter over `milestone.metadata` (already returned by `/timeline` — zero API change).

**Tech Stack:** Flask + pytest (unittest.mock), React + TS + Bootstrap + vitest, Playwright E2E (headless-first per project rule).

**Spec:** `docs/dev-notes/2658-acceptance-paused-user-actions-design.md`. Issue #2658.
**Plan review:** independent review 2026-08-14 — all findings (1 Critical, 4 Important, 5 Minor) folded in below.

---

### Task 1: Backend — override extension + resume-cache reset

**Files:**
- Test: `tests/unit/test_acceptance_override_2658.py` (new)
- Modify: `app/routes/autonomous.py` (`acceptance_verification_override` ~L1312-1445; `resume_with_feedback` ~L1676-1710)
- Modify: `tests/issues/2335/test_acceptance_override_route.py` (existing file, updated IN PLACE — do not create new files under tests/issues/; note this dir runs only in the extended `category=issues` lane, never in required PR CI)

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_acceptance_override_2658.py` — mirror the fixture style of `tests/issues/2335/test_acceptance_override_route.py` (mocked repo + patched `_load_user_from_token` + patched `GitHubOps`):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_acceptance_override_2658.py -v`
Expected FAILs: `test_owner_can_override_rejected` (403), `test_owner_can_override_indeterminate` (403), `test_admin_can_override_rejected` (400), `test_resume_clears_cached_merge_sha` (no `verification_merge_sha` in updates). `test_unrelated_user_gets_403` / guard tests may already pass.

- [ ] **Step 3: Implement the override route change**

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

2. **Permission check MOVES after the 404** (it currently sits before
   `get_workflow` and would raise NameError on `workflow` otherwise). Delete:

```python
    if not User.is_admin_role(g.user_role):
        return jsonify({"error": "Admin permission required"}), 403
```

and insert right AFTER the `if not workflow: return 404` block (mirroring
`resume_with_feedback`'s order — an unrelated user probing an unknown id now
gets 404, consistent with every other route):

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

4. Comment title — make the first line conditional:

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

- [ ] **Step 4: Implement the resume-with-feedback cache reset**

In `resume_with_feedback`, extend the `update_workflow` dict:

```python
    # Store feedback and set to waiting (scheduler will pick up via _do_wait).
    # #2658: clear the cached acceptance merge SHA — the acceptance phase's
    # idempotency replays a terminal verdict for the same
    # (merge_sha, snapshot) pair, and nothing else resets it between dev
    # rounds, so without this the next acceptance run would re-verify the OLD
    # merge instead of the new one (stale-replay loop for both rejected and
    # indeterminate resumes).
    _get_repo().update_workflow(
        workflow_id,
        {
            "user_feedback": user_feedback,
            "current_phase": "wait",
            "status": "waiting",
            "verification_merge_sha": "",
        },
    )
```

- [ ] **Step 5: Update the legacy 2335 test file in place**

`tests/issues/2335/test_acceptance_override_route.py`: `test_non_admin_override_returns_403` currently patches the OWNER (user_id=7, role user) and expects 403 — that user must now succeed. Replace it with:

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

- [ ] **Step 6: Run both test files**

Run: `python -m pytest tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routes/autonomous.py tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py
git commit -m "feat(#2658): owner+admin override for rejected/indeterminate + fresh re-verify on feedback resume"
```

---

### Task 2: Frontend — unified button gating + report viewer

**Files:**
- Modify: `frontend/src/components/work/WorkflowTimeline.tsx`
- Modify: `frontend/src/components/work/WorkflowTimeline.test.tsx` (🔴 existing vitest locks the OLD rejected-only behavior — CI runs it via `npm run test:coverage` on `frontend/**` changes)
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
  // acceptance; resuming without feedback hits the idempotency guard and
  // re-pauses immediately, which is why the feedback modal is the resume
  // path). The explicit current_phase check also fixes a latent bug: the old
  // indeterminate-only gate could show the override button during a LATER dev
  // round (verification_status is never cleared between rounds), where the
  // server would 400.
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

Extend `showInlineActionGroup` (~L1581) — without this the button silently never renders on the `showForkCancel: false` render path (~L1903):

```tsx
    const showInlineActionGroup =
      showInlineSessionButton ||
      canViewPlanContent ||
      canViewReviewContent ||
      canViewAcceptanceReport ||
      canViewChanges ||
      canFork ||
      canCancel;
```

Card action button, after the `canViewReviewContent` button block (~L1733) — note `milestone.metadata` is a plain `string` on the `WorkflowMilestone` type (`api/autonomous.ts:139`), used directly:

```tsx
                    {canViewAcceptanceReport && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--warning"
                        onClick={() =>
                          setViewingContent({
                            title: t('autoViewAcceptanceReportTitle', language),
                            content: formatAcceptanceReport(milestone.metadata),
                          })
                        }
                      >
                        <i className="bi bi-clipboard-check me-1"></i>
                        {t('autoViewAcceptanceReport', language)}
                      </Button>
                    )}
```

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

**Reword `autoAcceptanceOverrideDesc`** in all 4 locales — current text only covers indeterminate ("verifier could not reach a confident verdict"); rejected means the verifier DID decide and a human disagrees:

en: `'Accept the delivered result (confirmed by human review), close the issue and complete the workflow. Available to the workflow owner or an admin.'`
zh: `'人工确认验收通过并关闭 issue、完成工作流。工作流所有者或管理员可操作；若验收器已拒绝，此操作将推翻该拒绝。'`
ja: `'人間の確認により受理し、issue をクローズしてワークフローを完了します。ワークフロー所有者または管理者が操作できます。検証者が拒否した場合はその判定を覆します。'`
ko: `'사람의 확인으로 승인하고 issue를 닫아 워크플로를 완료합니다. 워크플로 소유자 또는 관리자가 실행할 수 있습니다. 검증자가 거부한 경우 해당 판정을 뒤집습니다.'`

- [ ] **Step 5: Rewrite the vitest that locks old behavior**

`frontend/src/components/work/WorkflowTimeline.test.tsx` (~L147-163): the test `shows resume-with-feedback only for rejected acceptance, not indeterminate` asserts indeterminate has NO resume button and finds the override button by its OLD title regex `/confirm acceptance and close the issue/i` — both flip. Rewrite as:

```tsx
  it('#2658 shows BOTH exits (override + resume-with-feedback) for rejected AND indeterminate acceptance pauses', () => {
    for (const status of ['rejected', 'indeterminate'] as const) {
      const view = renderTimeline(pausedWorkflow({ verification_status: status }));
      // The banner button's accessible name is the help text (title -> aria-label).
      expect(screen.getByRole('button', { name: /new development round/i })).toBeInTheDocument();
      // Override button: accessible name is its descriptive title (reworded for
      // #2658 — covers rejected-overturn and indeterminate alike).
      expect(
        screen.getByRole('button', { name: /accept the delivered result/i })
      ).toBeInTheDocument();
      view.unmount();
    }
  });
```

(Adjust the title regex to whatever the reworded `autoAcceptanceOverrideDesc` renders — the assertion and the i18n string MUST match; check other tests in the file that reference the old title text and update them too, e.g. grep the file for `confirm acceptance`.)

- [ ] **Step 6: Typecheck + vitest + build**

Run: `cd frontend && npx tsc --noEmit && npm run test -- --run && npm run build`
Expected: no type errors, all vitest specs pass, build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/work/WorkflowTimeline.tsx frontend/src/components/work/WorkflowTimeline.test.tsx frontend/src/i18n/index.ts
git commit -m "feat(#2658): unified acceptance-pause exits + in-page report viewer"
```

---

### Task 3: E2E (Playwright, headless first — project rule)

**Files:**
- Modify: `tests/e2e/work/e2e_paused_acceptance_ui_playwright.py`

- [ ] **Step 1: Extend + FLIP the E2E**

1. Milestone seed helper — call it INSIDE the same `app.app_context()` block the existing `seed_workflow` uses (repo calls need request/app context; mirror `seed_workflow` at ~L118):

```python
def seed_acceptance_milestone(repo, workflow_id: str, status: str) -> None:
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
```

(Keep the file's existing naming/style; `create_milestone` accepts exactly these keys — `dev_round` defaults to 1, FK matches the seeded workflow.)

2. **FLIP section 3** (~L320-334): the current assertion
`expect(page.locator(".timeline-state-banner")).not_to_contain_text("Resume with Feedback")`
now contradicts #2658 — indeterminate MUST show the resume button. Replace with:

```python
            expect(
                page.locator(".timeline-state-banner").get_by_text("Resume with Feedback")
            ).to_be_visible()
```

and update the final print to `"[PASS] indeterminate workflow shows override + resume-with-feedback"`.

3. NEW section 4 — report viewer, run for the rejected workflow: on the timeline, click 「View acceptance report」 and assert the viewer modal shows the seeded item + evidence:

```python
            page.get_by_role("button", name="View acceptance report").click()
            expect(page.get_by_text("修复登录失败")).to_be_visible()
            expect(page.get_by_text("tests/test_login.py:12")).to_be_visible()
```

(Localize the button name if the E2E drives a zh UI — follow however the existing test selects banner buttons.)

Note (plan-review #7): this E2E logs in as an admin who also owns the seeds, so it cannot distinguish owner vs admin permissions — that matrix is fully covered by the unit tests in Task 1.

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

Run: `pre-commit run --files app/routes/autonomous.py tests/unit/test_acceptance_override_2658.py tests/issues/2335/test_acceptance_override_route.py frontend/src/components/work/WorkflowTimeline.tsx frontend/src/components/work/WorkflowTimeline.test.tsx frontend/src/i18n/index.ts tests/e2e/work/e2e_paused_acceptance_ui_playwright.py`
Expected: clean (watch for pyupgrade `X | None`, TC003 TYPE_CHECKING moves, black 25.1.0 formatting).

- [ ] **Step 3: Push + PR**

```bash
git push -u origin fix/2658-acceptance-paused-user-actions
gh pr create --title "feat(#2658): unify acceptance-paused user actions" --body "<summary; NO closes/fixes/resolves keywords near #2658 — 'Refs #2658' only>"
```

- [ ] **Step 4: Required CI green** (lint / test(3.10) / test(3.11) / test(3.12) / build + Frontend CI vitest) → independent review (`superpowers:requesting-code-review` on `gh pr diff`) → merge `--merge --delete-branch` → deploy hotpatch (routes change: cp autonomous.py + restart openace-scheduler.service AND open-ace.service; frontend build+deploy + open-ace.service restart) → verify on prod.

- [ ] **Step 5: headless=false demo to user** (project E2E rule step 3).
