"""ReviewPhase handler (#2044 Phase B T11). Extracted from
``AutonomousOrchestrator._do_pr_review``. See phases/merge.py (T10) for the
pattern and the Migration Procedure in the plan.

Same decisions as the legacy inline-commit method; only the recording mechanism
changes:

- The four forbidden fields (``current_phase``/``status``/``completed_at``/
  ``paused_at``) travel on the returned ``PhaseResult`` — they are never written
  inline here (the T3 AST guard scans this file). Terminal transitions (review
  approved/capped → ``report``; no-changes/timing-issue → ``completed``) return
  ``PhaseResult.completed(next_phase=..., ...)``; failure paths return
  ``PhaseResult.failed``.
- ``phase_change`` events go through ``deps.host.emit_phase_change``.
- The orchestrator-private helpers this phase reaches for (PR/branch probes,
  agent runs, CI polling, scope validation, the review-fix sub-method, agent
  artifact readers) are NOT on a service — each is tens-to-hundreds of lines
  with its own transitive ``self._`` calls. They are exposed on ``PhaseHost``
  (duck-typed; the orchestrator satisfies them as bound aliases) so this
  handler lives in ``phases/`` without a concrete orchestrator reference.

Deviation notes (mirroring the T8/T9/T10 patterns):

- The push-failure branches (transient GitHubOpsError, non-transient
  RuntimeError) ``raise`` inline, exactly as the legacy method did. advance()'s
  exception handler maps transient errors to a retry counter bump and terminal
  errors to ``_mark_failed`` — returning ``PhaseResult.failed`` here would skip
  ``_mark_failed`` (which emits the ``error`` event and reclaims the worktree),
  a behaviour change. Same failure-path-raise deviation as T10's conflict branch.
- ``WorkflowPaused`` raised by ``_poll_ci_status`` (shutdown during CI polling)
  propagates: the review milestone is cancelled via
  ``deps.host.cancel_milestone_for_shutdown`` then re-raised, matching the
  legacy ``except WorkflowPaused: cancel + raise``. advance() catches
  WorkflowPaused without writing status=failed.
- The PR-creation failure branches (non-"already exists" GitHubOpsError, and
  the unrecoverable "already exists but no PR found" case) record a failed
  ``pr_created`` milestone inline then ``raise`` — same reason: the raise lets
  advance()'s handler run ``_mark_failed`` rather than only writing status=failed.
- Failure paths that the legacy method terminated with a normal return after
  writing ``status=failed`` inline (review agent failed / no result / read-only
  tool unsupported / summary agent failed / no result) return
  ``PhaseResult.failed`` here — the commit entrypoint writes status=failed +
  error_message. Each first updates its milestone via
  ``deps.repo.update_milestone`` (a bookkeeping field, not a forbidden one) and
  posts its PR comment via ``deps.host.post_github_comment`` before returning,
  matching the legacy ordering.
- The ``pr_created`` milestone on the success/reused branches and the
  ``pr_reviewed`` / ``pr_review_summary`` milestones are created inline via
  ``deps.host.create_milestone_idempotent`` (they anchor agent runs and are
  re-read by subsequent rounds / the summary block), matching the T9/T10
  create→reread deviation. The terminal ``ci_failed_before_report`` milestone
  on the "CI failed after review passed" branch rides in ``milestone_events``
  because it is created immediately before the phase hands off to CI repair.
- ``github_pr_number`` / ``github_pr_url`` / ``current_round`` are non-forbidden
  workflow fields; they ride in ``workflow_patch``. The handler keeps a local
  ``pr_number`` after creation (mirroring the legacy local) so downstream reads
  in the same cycle do not depend on the persisted write being visible yet.
- The "review not passed → apply fix" path returns ``PhaseResult.retry()`` when
  the fix succeeds (the scheduler re-enters pr_review for the next round; phase
  unchanged, matching the legacy bare ``return``), and propagates the failure
  when ``_apply_pr_review_fix`` raises (it writes status=failed inline itself
  before returning False; when it returns False the handler returns
  ``PhaseResult.retry`` to let advance()'s convergence point reclaim the
  worktree — see below).
"""

from __future__ import annotations

import logging

from app.modules.workspace.autonomous.constants import (
    AUTONOMOUS_CONTEXT,
    AUTONOMOUS_DEV_ALLOWED_TOOLS,
    READ_ONLY_REVIEW_UNSUPPORTED_TOOLS,
    REVIEW_ALLOWED_TOOLS,
    _extract_pr_number_from_error,
    _is_transient_git_error,
    _merge_milestone_metadata,
    _review_approval_phrase,
    _zcode_planning_mode,
)
from app.modules.workspace.autonomous.phase_contract import PhaseResult

NAME = "pr_review"

logger = logging.getLogger(__name__)


def handle(ctx, deps) -> PhaseResult:
    """Execute one PR-review-phase cycle.

    Body moved from ``_do_pr_review`` per the Migration Procedure; see module
    docstring for the deviation notes.
    """
    wf = ctx.workflow
    gh = deps.gh
    host = deps.host
    repo = deps.repo
    round_num = wf.get("current_round", 0) + 1
    max_rounds = wf.get("max_pr_review_rounds", 5)
    force_full_rounds = host.must_run_full_review_rounds(wf)
    dev_round = wf.get("dev_round", 1)
    branch_name = wf.get("branch_name", "")
    # Language-aware approval marker for PR review (matches what the agent,
    # writing in content_language, is asked to state).
    approval_phrase = _review_approval_phrase(wf.get("content_language"))

    # workflow_patch accumulates non-forbidden field writes for the terminal
    # PhaseResult. github_pr_number / github_pr_url / current_round land here.
    workflow_patch: dict = {}

    # Check if branch has any changes vs main
    # Distinguish "branch behind main (timing issue)" from "no actual changes" (Issue #1552)
    has_changes = False
    is_timing_issue = False
    try:
        branch_sha = gh._run_git(["rev-parse", branch_name]).stdout.strip()
        main_sha = gh._run_git(["rev-parse", "main"]).stdout.strip()

        # Check if branch is an ancestor of main (behind main)
        is_ancestor = (
            gh._run_git(
                ["merge-base", "--is-ancestor", branch_sha, main_sha], check=False
            ).returncode
            == 0
        )

        if is_ancestor:
            # Branch is behind main → timing issue
            is_timing_issue = True
            has_changes = False
            logger.warning(
                "Branch %s is behind main (timing issue). base_commit_sha=%s",
                branch_name,
                wf.get("base_commit_sha", "none"),
            )
        else:
            # Branch is ahead or parallel → normal diff check
            diff_stats = gh.get_diff_stats("main", branch_name)
            has_changes = diff_stats.get("commits", 0) > 0
            if has_changes:
                scope_error = host.validate_autonomous_change_scope(
                    gh,
                    wf,
                    (wf.get("base_commit_sha") or branch_sha),
                    branch_sha,
                )
                if scope_error:
                    return PhaseResult.failed(structured_error={"message": scope_error})
    except Exception as e:
        logger.warning("Failed to check branch status: %s", e)
        pass

    if not has_changes:
        # No code changes produced — skip PR, post to issue, and mark completed
        issue_number = wf.get("github_issue_number")

        # Distinguish timing issue from no changes (Issue #1552)
        if is_timing_issue:
            no_change_msg = (
                f"## ⚠️ Timing Issue Detected\n\n"
                f"Branch `{branch_name}` is behind main (created from an older commit that was merged).\n"
                f"This indicates a race condition during workflow creation.\n\n"
                f"**Recommendation**: This issue should be fixed by locking base commit during batch creation.\n"
            )
        else:
            no_change_msg = (
                f"## ℹ️ No Changes Detected\n\n"
                f"Agent completed dev round {dev_round} without producing code changes.\n"
                f"Skipping PR creation."
            )

        if issue_number:
            host.post_github_comment(gh, issue_number, no_change_msg, context="no-changes")
        milestone_events: list[dict] = [
            {
                "phase": "pr_review",
                "dev_round": dev_round,
                "milestone_type": "timing_issue" if is_timing_issue else "no_changes",
                "status": "completed",
                "title": (
                    "Branch behind main (timing issue)"
                    if is_timing_issue
                    else "No code changes produced"
                ),
                "result_summary": (
                    "Branch behind main: possible timing issue during workflow creation"
                    if is_timing_issue
                    else "Agent did not produce any code changes. Skipping PR creation."
                ),
            }
        ]
        host.emit_phase_change({"phase": "completed"})
        # The terminal current_phase is phase-specific: pr_review's no-changes/
        # timing-issue path writes the literal "completed" (it skips report/
        # merge entirely). The commit entrypoint honours a current_phase value
        # carried in workflow_patch for next_phase="completed" (see
        # _commit_phase_result), defaulting to "merge" otherwise. Carrying the
        # literal here is the sanctioned escape hatch for the T3 forbidden-
        # field guard (the guard scans for direct _update_workflow({...}) calls
        # and patch[...] assignments, not PhaseResult(workflow_patch=...)
        # construction).
        return PhaseResult.completed(
            next_phase="completed",
            workflow_patch={**workflow_patch, "current_phase": "completed", "error_message": ""},
            milestone_events=milestone_events,
        )

    issue_number = wf.get("github_issue_number") or wf.get("github_issue_number")
    # Ensure branch is pushed to remote before PR creation
    try:
        # P1 修复（Issue #1611）：检查当前分支是否与预期一致
        current_branch = gh.get_current_branch()
        if branch_name and current_branch != branch_name:
            logger.error(
                "Branch mismatch before push: workflow=%s expected=%s actual=%s",
                host.workflow_id[:8],
                branch_name,
                current_branch,
            )
            raise RuntimeError(
                f"Branch mismatch before push: expected {branch_name}, actual {current_branch}"
            )
        gh.git_push(branch=branch_name, force_with_lease=True)
    except Exception as e:
        # Distinguish transient vs non-transient errors to enable Layer-2 retry
        # for network flakiness (Issue #1814). Failure-path raise deviation —
        # see module docstring.
        if _is_transient_git_error(e):
            # Transient: propagate GitHubOpsError to trigger Layer-2 retry
            logger.warning("Transient push failure for branch %s: %s", branch_name, e)
            raise
        else:
            # Non-transient: wrap as RuntimeError to signal permanent failure
            logger.error("Failed to push branch %s: %s", branch_name, e, exc_info=True)
            # 推送失败必须阻止后续 PR 创建，避免 "No commits" 错误 (Issue #1736)
            raise RuntimeError(f"Branch push failed before PR creation: {e}") from e

    # Create PR on first round (idempotent: skip if a PR already exists for
    # this workflow). advance() is reentrant — the scheduler may call it again
    # while a review agent is still running and current_round hasn't been
    # persisted yet. On re-entry round_num is still 1, so without this guard
    # the workflow would call gh pr create again and hit "a pull request ...
    # already exists", failing the whole workflow (#1857).
    #
    # Reads from both the passed-in wf dict AND the host's live workflow: once
    # an earlier advance() persisted github_pr_number, a later advance()'s
    # fallback sees the fresh value even though the caller's wf snapshot is
    # stale. The handler keeps a local pr_number after creation so downstream
    # reads in the same cycle do not depend on the persisted write.
    existing_pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
    pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
    if round_num == 1 and not existing_pr_number:
        try:
            # Build PR body with issue linkage
            pr_body = f"Autonomous development for dev round {dev_round}.\n\nRequirements: {wf.get('requirements_text', '')}"
            if issue_number:
                pr_body += f"\n\nCloses #{issue_number}"

            pr_data = gh.create_pr(
                title=f"[Auto] Dev round {dev_round}: {wf.get('title', 'Autonomous development')}",
                body=pr_body,
                head=branch_name,
                base="main",
            )
            pr_number = pr_data.get("number")
            pr_url = pr_data.get("url", "")
            host.create_milestone_idempotent(
                phase="pr_review",
                dev_round=dev_round,
                milestone_type="pr_created",
                status="completed",
                title=f"PR #{pr_number} created",
                github_pr_number=pr_number,
                result_summary=pr_url,
            )
            workflow_patch["github_pr_number"] = pr_number
            workflow_patch["github_pr_url"] = pr_url
        except Exception as e:
            # Graceful recovery ONLY for the "already exists" case. Failure-
            # path raise deviation (see module docstring).
            pr_number_reused = _extract_pr_number_from_error(str(e))
            if pr_number_reused:
                existing = {"number": pr_number_reused}
            else:
                if "already exists" not in str(e).lower():
                    host.create_milestone_idempotent(
                        phase="pr_review",
                        milestone_type="pr_created",
                        status="failed",
                        title="PR creation failed",
                        error_message=str(e),
                    )
                    raise
                existing = gh.find_existing_pr(branch_name)
                if not existing:
                    # GitHub's PR list API is eventually consistent — the PR
                    # that "already exists" may not be indexed yet right after
                    # a concurrent create. One short retry covers it.
                    import time

                    time.sleep(2)
                    existing = gh.find_existing_pr(branch_name)
            if existing:
                pr_number = existing.get("number")
                pr_url = existing.get("url", "")
                logger.warning(
                    "PR create for %s returned 'already exists'; reusing PR #%s",
                    branch_name,
                    pr_number,
                )
                host.create_milestone_idempotent(
                    phase="pr_review",
                    dev_round=dev_round,
                    milestone_type="pr_created",
                    status="completed",
                    title=f"PR #{pr_number} already exists (reused)",
                    github_pr_number=pr_number,
                    result_summary=pr_url,
                )
                workflow_patch["github_pr_number"] = pr_number
                workflow_patch["github_pr_url"] = pr_url
            else:
                host.create_milestone_idempotent(
                    phase="pr_review",
                    milestone_type="pr_created",
                    status="failed",
                    title="PR creation failed",
                    error_message=str(e),
                )
                raise

        # Check CI status after PR creation — poll until finished or timeout
        if pr_number:
            try:
                ci_checks_post = host.poll_ci_status(gh, pr_number)
            except Exception:
                ci_checks_post = []
            ci_fails_post = [c for c in ci_checks_post if c.get("bucket") == "fail"]
            if ci_fails_post:
                ci_summary = "\n".join(
                    f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_fails_post
                )
                host.post_github_comment(
                    gh,
                    pr_number,
                    "## ⚠️ CI 检查状态\n\n"
                    f"以下 CI 检查未通过：\n{ci_summary}\n\n"
                    "将在后续代码审查轮次中分析这些失败是否由本 PR 引入。",
                    is_pr=True,
                    context="ci-fails",
                )

    if not pr_number:
        pr_number = host.get_workflow_field("github_pr_number")

    # Code review
    review_ms = host.create_milestone_idempotent(
        phase="pr_review",
        dev_round=dev_round,
        round_number=round_num,
        milestone_type="pr_reviewed",
        status="in_progress",
        title=f"PR review round {round_num}",
    )

    # Get diff for review
    diff_text = host.get_pr_review_diff(gh, pr_number, branch_name)

    # Check CI status for the PR — poll until checks finish or timeout
    ci_checks: list = []
    ci_failures: list = []
    if pr_number:
        try:
            ci_checks = host.poll_ci_status(gh, pr_number)
            ci_failures = [c for c in ci_checks if c.get("bucket") == "fail"]
        except Exception as e:
            # WorkflowPaused (raised by _poll_ci_status on shutdown) propagates:
            # cancel the in-progress review milestone then re-raise (advance()
            # catches WorkflowPaused without failing the workflow). Identified
            # by class name to avoid importing the orchestrator module (circular
            # import — orchestrator imports phases at module load).
            if type(e).__name__ == "WorkflowPaused" or (
                type(e).__mro__ and any(c.__name__ == "WorkflowPaused" for c in type(e).__mro__)
            ):
                host.cancel_milestone_for_shutdown(review_ms.get("milestone_id", ""))
                raise

    review_prompt = (
        AUTONOMOUS_CONTEXT + f"你是一位资深代码审查专家。请审查以下 PR 的代码变更。\n\n"
        f"## 代码变更\n{host.smart_truncate_diff(diff_text)}\n\n"
    )

    # Add Issue reference
    if issue_number:
        review_prompt += (
            f"## 关联 Issue\n"
            f"本 PR 关联 GitHub Issue #{issue_number}。\n"
            f"审查时请确保代码变更满足 Issue #{issue_number} 的所有需求。\n\n"
        )

    # For rounds > 1, the previous round's review is already in this review
    # session's resumed history (--resume). Ask the reviewer to revisit it and
    # confirm whether each point was addressed.
    if round_num > 1:
        review_prompt += (
            "## 上一轮审查\n"
            "请回顾你上一轮的审查意见（在本会话历史中），逐条确认是否已落实："
            "已落实（说明如何修改）/ 未落实（说明原因）/ 不适用（说明理由）。\n\n"
        )

    review_prompt += (
        "请检查：\n"
        "1. 代码质量和可读性\n"
        "2. 潜在 bug 和安全问题\n"
        "3. 测试覆盖率\n"
        "4. 性能影响\n"
        "5. 与需求的对齐程度\n"
        "6. 上一轮审查意见的落实情况(如有)\n\n"
        "本阶段是只读审查：不要修改文件、不要创建提交，也不要执行任何会改变仓库状态的命令。\n"
        "只有在所有 Issue 验收标准均已满足、没有 P0/P1 阻塞项或未落实项时才能批准；"
        "只要仍有阻塞项，即使核心功能已基本完成，也必须要求修改。\n"
        f"如果没有重大问题，请在审查结论中明确写出批准标记：{approval_phrase}。\n"
        "必须把下面的机器可读单行 JSON 作为 TL;DR 摘要之前的最后一个非摘要行"
        "（不要放进代码块）。所有未解决的 P0/P1 都必须逐项放入 blocking_findings；"
        "只有数组为空时 verdict 才能是 APPROVE：\n"
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
        'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES",'
        '"blocking_findings":["finding 1"]}\n\n'
        "重要：直接输出审查结果，不要添加引导文字(如'我来审查...'、'让我...'等)"
        "或结尾引导(如'下一步是否...'等)。"
    )

    review_tool = wf.get("cli_tool", "claude-code")
    if review_tool in READ_ONLY_REVIEW_UNSUPPORTED_TOOLS:
        message = (
            f"PR review cannot run safely with {review_tool}: its single-shot adapter "
            "does not provide an enforceable per-run read-only sandbox. Configure a "
            "review-capable CLI and retry the workflow."
        )
        repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {"status": "failed", "error_message": message},
        )
        if pr_number:
            host.post_github_comment(
                gh,
                pr_number,
                f"## ⛔ PR Review Blocked\n\n{message}",
                is_pr=True,
                context="code-review",
            )
        return PhaseResult.failed(structured_error={"message": message})

    # Include CI failures in review prompt if any
    if ci_failures:
        ci_summary = "\n".join(
            f"- **{c['name']}**: {c.get('state', 'unknown')}" for c in ci_failures
        )
        review_prompt += (
            f"\n\n## ⚠️ CI 检查失败\n\n以下 CI 检查未通过：\n{ci_summary}\n\n"
            "请在审查时分析这些 CI 失败是否由本 PR 的代码变更引入。\n"
            "如果是预先存在的问题，在审查结论中明确说明。"
        )

    review_result = host.run_agent_with_context_recovery(
        wf=wf,
        workflow_id=host.workflow_id,
        cli_tool=review_tool,
        model=wf.get("model", ""),
        project_path=wf.get("worktree_path") or wf.get("project_path", ""),
        prompt=review_prompt,
        workspace_type=wf.get("workspace_type", "local"),
        remote_machine_id=wf.get("remote_machine_id"),
        permission_mode=_zcode_planning_mode(wf),
        allowed_tools=REVIEW_ALLOWED_TOOLS.get(review_tool, []),
        session_line="review",
        milestone_id=review_ms.get("milestone_id", ""),
    )

    host.accumulate_tokens(review_result)

    if host.abort_on_repo_integrity_violation(review_result, review_ms.get("milestone_id", "")):
        return PhaseResult.retry(workflow_patch=workflow_patch)

    if not review_result.success or host.is_context_overflow(review_result):
        message = (
            "PR review agent failed: "
            f"{review_result.error or host.artifact_text(review_result) or 'no result'}"
        )
        repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {
                "status": "failed",
                "review_session_id": review_result.session_id,
                "error_message": message,
            },
        )
        return PhaseResult.failed(
            structured_error={"message": message}, workflow_patch=workflow_patch
        )

    review_text = host.artifact_text(review_result)
    if not review_text.strip():
        message = "PR review agent returned no result"
        repo.update_milestone(
            review_ms.get("milestone_id", ""),
            {
                "status": "failed",
                "review_content": "",
                "review_session_id": review_result.session_id,
                "error_message": message,
            },
        )
        return PhaseResult.failed(
            structured_error={"message": message}, workflow_patch=workflow_patch
        )
    # Detect approval using the language-aware marker, then persist a structured
    # verdict so progress_reported doesn't re-scan review text. The legacy zh
    # marker is accepted too, for workflows whose content language predates this
    # field (mirrors _derive_review_passed).
    review_passed = host.review_is_approved(review_text, approval_phrase)
    review_metadata = _merge_milestone_metadata(
        repo.get_milestone(review_ms.get("milestone_id", "")),
        {"review_verdict": {"passed": review_passed, "round": round_num}},
    )
    repo.update_milestone(
        review_ms.get("milestone_id", ""),
        {
            "status": "completed" if review_result.success else "failed",
            "review_content": review_text,
            "review_session_id": review_result.session_id,
            "tldr": host.artifact_tldr(review_result),
            "metadata": review_metadata,
        },
    )

    # Post review as PR comment
    if pr_number:
        host.post_github_comment(
            gh,
            pr_number,
            f"## 🔍 Code Review (Round {round_num})\n\n{review_text}",
            is_pr=True,
            context="code-review",
        )

    # Check if all rounds done
    workflow_patch["current_round"] = round_num

    # Every review with findings gets a fix — including the cap round — so the
    # last review's feedback is never silently dropped. Total reviews are capped
    # at max_pr_review_rounds; after the cap-round fix we go straight to summary/
    # report. In the default mode, an approved review can also end PR review
    # early; with require_full_review_rounds enabled, only the cap ends the loop.
    at_cap = round_num >= max_rounds
    if not review_passed:
        fix_succeeded = host.apply_pr_review_fix(
            wf, gh, review_text, round_num, dev_round, ci_failures, pr_number
        )
        if not fix_succeeded:
            # _apply_pr_review_fix wrote status=failed inline (it has its own
            # failure paths that write error_message). Return retry so advance()'s
            # convergence point reclaims the worktree for the terminal-failure
            # path — the persisted status=failed is what matters, not the result.
            return PhaseResult.retry(workflow_patch=workflow_patch)
        # Context recovery may have rotated the main session line during the fix.
        # The cap-round summary runs in this same scheduler call, so refresh
        # before it resumes main again. The handler cannot re-query, so it reads
        # the refreshed fields from the host's live workflow snapshot.
        wf = host.refresh_workflow_snapshot()

    if (review_passed and not force_full_rounds) or at_cap:
        # All PR review rounds completed — summarize via the main session, then
        # move to report. The ENTIRE summary block (create milestone → run agent
        # → fill review_content → post comment) must run BEFORE the CI check.
        # Otherwise a CI failure redirects to the CI repair loop and returns,
        # leaving the milestone with status="in_progress" and empty
        # review_content (#1813).
        last_pr_review = ""
        pr_milestones = repo.list_milestones(host.workflow_id, phase="pr_review")
        for ms in reversed(pr_milestones):
            if ms.get("milestone_type") == "pr_reviewed" and ms.get("review_content"):
                last_pr_review = ms["review_content"]
                break

        summary_ms = host.create_milestone_idempotent(
            phase="pr_review",
            dev_round=dev_round,
            round_number=round_num,
            milestone_type="pr_review_summary",
            status="in_progress",
            title="PR Review Summary",
        )

        summary_prompt = (
            AUTONOMOUS_CONTEXT + "代码审查已全部完成。请根据最后一轮审查意见，"
            "并结合本会话历史中开发环节的修复记录，"
            "输出一份 PR 评审总结，明确：\n"
            "1. 最后一轮审查意见是否已全部落实\n"
            "2. 是否还有遗留问题需要处理\n"
            "3. 当前 PR 是否可以合并\n\n"
        )
        if issue_number:
            summary_prompt += (
                f"## 关联 Issue\n"
                f"本 PR 关联 GitHub Issue #{issue_number}。\n"
                f"总结中请确认修改是否满足 Issue #{issue_number} 的所有需求。\n\n"
            )
        summary_prompt += (
            f"## 最后一轮审查意见\n{host.clean_agent_text(last_pr_review)}\n\n"
            "如果审查意见已全部落实、无遗留问题，请明确说明'可以合并'。"
            "直接输出总结，不要添加引导文字。"
        )
        summary_result = host.run_agent_with_context_recovery(
            wf=wf,
            workflow_id=host.workflow_id,
            cli_tool=wf.get("cli_tool", "claude-code"),
            model=wf.get("model", ""),
            project_path=wf.get("worktree_path") or wf.get("project_path", ""),
            prompt=summary_prompt,
            workspace_type=wf.get("workspace_type", "local"),
            remote_machine_id=wf.get("remote_machine_id"),
            permission_mode=wf.get("permission_mode", "auto-edit"),
            allowed_tools=AUTONOMOUS_DEV_ALLOWED_TOOLS.get(wf.get("cli_tool", "claude-code"), []),
            session_line="main",
            milestone_id=summary_ms.get("milestone_id", ""),
        )
        host.accumulate_tokens(summary_result)
        if host.abort_on_repo_integrity_violation(
            summary_result, summary_ms.get("milestone_id", "")
        ):
            return PhaseResult.retry(workflow_patch=workflow_patch)
        if not summary_result.success or host.is_context_overflow(summary_result):
            message = (
                "PR review summary agent failed: "
                f"{summary_result.error or host.artifact_text(summary_result) or 'no result'}"
            )
            repo.update_milestone(
                summary_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "review_content": "",
                    "error_message": message,
                },
            )
            return PhaseResult.failed(
                structured_error={"message": message}, workflow_patch=workflow_patch
            )
        summary_text = host.artifact_text(summary_result)
        if not summary_text.strip():
            message = "PR review summary agent returned no result"
            repo.update_milestone(
                summary_ms.get("milestone_id", ""),
                {
                    "status": "failed",
                    "review_content": "",
                    "error_message": message,
                },
            )
            return PhaseResult.failed(
                structured_error={"message": message}, workflow_patch=workflow_patch
            )
        repo.update_milestone(
            summary_ms.get("milestone_id", ""),
            {
                "status": "completed" if summary_result.success else "failed",
                "review_content": summary_text,
                "result_summary": summary_text[:200],
                "tldr": host.artifact_tldr(summary_result),
            },
        )

        if pr_number and summary_text:
            host.post_github_comment(
                gh,
                pr_number,
                f"## ✅ PR Review Summary\n\n{summary_text}",
                is_pr=True,
                context="review-summary",
            )

        # Check CI status before proceeding to report phase (Issue #1662). If
        # CI failed, enter CI repair loop instead of reporting.
        if ci_failures:
            # Reuse merge-phase CI repair loop. Phase stays on pr_review (the
            # repair loop re-enters merge after CI is green); return retry so
            # advance() does not advance the phase. The
            # ci_failed_before_report milestone is created inline (correlation
            # record, like the pr_created milestone above).
            host.create_milestone_idempotent(
                phase="pr_review",
                dev_round=dev_round,
                round_number=round_num,
                milestone_type="ci_failed_before_report",
                status="completed",
                title=f"CI failed after review passed: {len(ci_failures)} checks",
                result_summary=", ".join(c.get("name", "unknown") for c in ci_failures),
            )
            host.start_ci_repair_round(wf, pr_number, ci_failures)
            return PhaseResult.retry(workflow_patch=workflow_patch)
        # Move to report
        host.emit_phase_change({"phase": "report"})
        return PhaseResult.completed(
            next_phase="report",
            next_status="reporting",
            workflow_patch=workflow_patch,
        )
    # Under cap, the scheduler re-enters pr_review for the next review round.
    # In the default mode this path means "not approved and the fix above
    # already ran"; with force-full enabled it also covers "approved early,
    # but keep reviewing until the configured cap". Return retry so phase/
    # status stay on pr_review for the next cycle (matches the legacy bare
    # end-of-method return).
    return PhaseResult.retry(workflow_patch=workflow_patch)
