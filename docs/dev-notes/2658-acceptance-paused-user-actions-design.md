# #2658 design: unify user actions for acceptance-paused workflows

Date: 2026-08-14. Branch: `fix/2658-acceptance-paused-user-actions` (off `origin/main`).

## Problem

A workflow paused at `acceptance_verification` awaiting human action has
incomplete, inconsistent exits:

1. **rejected has no human-accept path.** `POST /workflows/<id>/verification_override`
   accepts only `verification_status == "indeterminate"` (rejected → 400) and is
   admin-only (the workflow owner gets 403). The 「标记完成」 button only shows
   for `status == "waiting"`. A user who disagrees with the rejection cannot
   finish the workflow and close the issue from the page; the only exit is
   resume-with-feedback into another dev round.
2. **indeterminate cannot resume with feedback.** The 「带反馈恢复」 button added
   in #2641 is gated on `verification_status === 'rejected'` only.
3. **The full verification report is not visible on the page.** Per-item
   verdicts/evidence/rationale are posted as a GitHub issue comment; the page
   timeline card shows only the one-line `result_summary` (first 6 failed
   items). The report JSON already rides in the acceptance milestone's
   `metadata` (returned by `/timeline`) but the frontend never renders it.
   Verifier infra-exhausted pauses land as `indeterminate` and had no dedicated
   action at all.

## Decisions (user-approved 2026-08-14)

- Override (人工确认验收) becomes available for **rejected AND indeterminate**,
  to **admin OR the workflow owner** (mirroring `resume_with_feedback`).
- Every acceptance pause that needs a human gets exactly two exits:
  **accept** (override → confirmed → issue comment → close issue → completed)
  or **resume with feedback** (new dev round → new merge → fresh acceptance).
- Every such pause can view the **full report** on the page.

## Backend change (single point: `app/routes/autonomous.py`)

`acceptance_verification_override`:

- Status guard: allow `verification_status in {"indeterminate", "rejected"}`
  (confirmed still 400s — nothing to override; empty status 400s as before).
- Permission: `User.is_admin_role(g.user_role) or workflow["user_id"] == g.user_id`
  (same shape as `resume_with_feedback`). Docstring updated to reflect that the
  human actor is the owner or an admin, and the override remains attributable
  via the `verified_by: human-override:<username>` stamp + audit event.
- Behavior unchanged: stamp `confirmed`, post override comment, close issue,
  complete workflow. The override comment notes when a rejection was
  overturned ("human override (rejected overturned)") so the issue history
  reads honestly.

Everything else is untouched: idempotency, replayed-rejected guard, close
semantics, audit event emission.

## Frontend changes (`frontend/src/components/work/WorkflowTimeline.tsx`)

- `showAcceptanceOverride`: `paused && acceptance_verification && verification_status ∈ {rejected, indeterminate}`.
- `showResumeWithFeedback`: same condition (was rejected-only).
- New 「查看验收报告」 action on the acceptance milestone card (same pattern as
  `canViewPlanContent`'s viewer): a modal renders the report parsed from the
  milestone's `metadata` JSON, grouped **scope / gates / verifier**, each item
  showing verdict badge + evidence (`ref`, `note`) + rationale. Zero API
  changes. i18n keys ×4 locales (en/zh/ja/ko).

Not done (YAGNI): manual "reject" action (the system already rejects), markDone
semantics, new GitHub links.

## Testing

- Unit (`tests/unit/`, `pytest.mark.regression` + `issue(2658)`):
  - permission matrix: owner ✓, admin ✓, unrelated user 403;
  - rejected override succeeds (completed + `verified_by` stamp + issue close
    called), indeterminate override still succeeds (regression);
  - boundaries: confirmed → 400, non-acceptance phase → 400, reason > 2000 → 400.
- E2E Playwright (`tests/e2e/work/`, headless-first per project rule, then
  `headless=false` demo): seeded rejected + indeterminate workflows show BOTH
  buttons and the report modal opens with per-item content; owner override on
  the rejected seed ends at completed.

No schema/migration changes.
