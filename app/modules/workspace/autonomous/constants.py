"""Shared autonomous constants and pure helpers.

Holds constants and pure (orchestrator-state-free) helpers that are used by
both the orchestrator and the phase handlers (``phases/*.py``) to avoid
circular imports: the orchestrator imports ``phases`` at module load (for
``resolve_phase_handler``), so ``phases/*.py`` cannot import back from the
orchestrator module. Put shared constants/helpers here instead and have both
sides import them.

The helpers here are pure functions / immutable data — they take primitive
arguments and return values, never touching orchestrator bookkeeping. Methods
that read or commit orchestrator state stay on the orchestrator (and are
exposed on ``PhaseHost`` as bound aliases for handlers to call).
"""

from __future__ import annotations

import json
import re
from typing import cast

# ── Prompt / tool-policy constants ──────────────────────────────────────────

AUTONOMOUS_CONTEXT = (
    "## 重要提示\n"
    "你正在无人值守的自动化工作流中运行。请遵守以下规则：\n"
    "1. 不要请求人类确认或等待权限批准，如果操作被阻止请跳过并继续\n"
    "2. 不要使用需要交互式确认的 gh CLI 命令（如 gh pr create）\n"
    "3. 直接执行文件修改和验证，不要仅输出方案文本；不要执行 git add/commit/push，编排器负责提交和推送\n"
    "4. 遇到权限问题时跳过该步骤继续执行其他任务\n\n"
)

# PR review is an independent, read-only gate.  In particular, do not expose
# Write/Edit/Bash or Agent here: a reviewer that edits the shared worktree can
# accidentally make its own findings appear resolved without the main session
# committing or testing those changes.  The main session applies findings in
# ``_apply_pr_review_fix`` with ``AUTONOMOUS_DEV_ALLOWED_TOOLS`` instead.
REVIEW_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": [
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
        "TaskRead",
        "TaskGet",
        "TaskList",
    ],
    "qwen-code-cli": [
        "read_file",
        "list_files",
        "search_files",
        "code_search",
        "web_search",
        "web_fetch",
    ],
    "codex": [],
    "openclaw": [],
    "zcode": [],
}

# Acceptance-verification tools (#2335): read-only review set + Bash for the
# scope gate's git diff/log on merged main. No Write/Edit — the verifier must not
# mutate the acceptance target; it runs against a throwaway checkout of main and
# reads git state, not the working tree.
VERIFICATION_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": REVIEW_ALLOWED_TOOLS["claude-code"] + ["Bash"],
    "qwen-code-cli": REVIEW_ALLOWED_TOOLS["qwen-code-cli"] + ["run_shell_command"],
    "codex": [],
    "openclaw": [],
    "zcode": [],
}

# OpenClaw's current single-shot adapter does not accept per-run permission or
# tool-policy arguments.  Treating an empty allowlist as read-only would be a
# false security boundary, so autonomous review must fail closed for this tool
# until the adapter can provide an enforceable per-run sandbox.
READ_ONLY_REVIEW_UNSUPPORTED_TOOLS = frozenset({"openclaw"})

# Development-phase tools: planning read-only set + Write/Edit/Bash so the agent
# can implement, run tests, and commit. Bash is allowed wholesale (test / git /
# build commands vary by language and can't be enumerated). This bounds where
# commits land (worktree + feature branch); bash itself is NOT sandboxed —
# cd /, rm -rf, sudo, network egress are all reachable from the worktree cwd,
# same trust model as any dev agent. plan phases stay read-only via
# PLANNING_ALLOWED_TOOLS (kept on the orchestrator; the pr_review handler does
# not need it). See #996.
AUTONOMOUS_DEV_ALLOWED_TOOLS: dict[str, list[str]] = {
    "claude-code": [
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
        "Agent",
        "TaskRead",
        "TaskGet",
        "TaskList",
        "Write",
        "Edit",
        "Bash",
    ],
    "qwen-code-cli": [
        "read_file",
        "list_files",
        "search_files",
        "code_search",
        "web_search",
        "web_fetch",
        "write_file",
        "edit_file",
        "run_shell_command",
    ],
    "codex": [],
    "openclaw": [],
    "zcode": [],
}

# ── Merge-phase shared constant (T10) ───────────────────────────────────────

MERGE_POLICY_PAUSE_REASON_PREFIX = "Merge blocked by repository policy:"

# Cap on acceptance-rejection-driven development rounds (#2335). A rejected
# acceptance verdict starts a new dev round carrying the rejection as feedback;
# after this many rounds a persistent rejection fails the workflow rather than
# looping forever.
MAX_ACCEPTANCE_DEV_ROUNDS = 3

# ── Protected security tests (CI-repair anti-tamper, #2687) ─────────────────

# Security-gate test files whose CONTENT the CI-repair path must not delete or
# weaken. Incident PR #2665 / revert PR #2672: the CI-repair agent "fixed" a
# red sudoers-hardening lock test by deleting/weakening its assertions, CI went
# green, and the merged PR carried 5 post-merge security criticals — the repair
# loop defeated the very tests that gate it. The mechanical post-repair check
# (AutonomousOrchestrator._protected_test_tampering_error) and the repair-agent
# prompt instruction both render from this registry so they cannot drift.
# Keep this list SMALL and defensible: only suites that lock security
# invariants (sudoers generation, the pre-commit table-boundary guard).
PROTECTED_CI_REPAIR_TEST_FILES: tuple[str, ...] = (
    "tests/unit/test_sudoers_hardening.py",
    "tests/unit/test_table_boundary_checker.py",
)

# ── Transient network error classification (shared with orchestrator) ───────

# Keywords identifying transient network errors at the orchestrator level
# (the error_message stored by advance's except block). Mirrors the
# _TRANSIENT_ERROR_KEYWORDS in github_ops.py but checks the wrapped message.
_TRANSIENT_ORCHESTRATOR_KEYWORDS = [
    "libressl",
    "openssl",
    "ssl",
    "tls",
    "connection reset",
    "connection refused",
    "connection timed out",
    "timed out",
    "could not resolve host",
    "network is unreachable",
    "unable to access",
    "rpc failed",
    "early eof",
    # libcurl "Empty reply from server" (git emits it verbatim, in English even
    # under a non-C locale, on a transient TLS/connection drop). Mirror the
    # Layer-1 list in github_ops.py (#2299).
    "empty reply",
    "empty response",
    # --force-with-lease rejects the push when the remote auto-dev tip moved
    # between git's read of the remote-tracking ref and the actual push (a
    # concurrent push, or a freshly-fetched ref). The worktree is unchanged, so
    # a Layer-2 retry that re-reads the remote ref succeeds; treating it as
    # non-transient strands the workflow on a recoverable race.
    "stale info",
    "fetch first",
    "non-fast-forward",
    "[rejected]",
    # Issue #2673: a PR head reporting zero check-runs after a mechanical
    # close+reopen retrigger means GitHub dropped the branch's event delivery
    # entirely (no push/synchronize/check-run events). That is transient
    # infrastructure, not a code failure — advance()'s Layer-2 retry makes the
    # stall visible (error_message + bounded retries) instead of a silent
    # wait-spin.
    "zero check-runs",
]


def _is_transient_git_error(e: Exception) -> bool:
    """Check if an exception is a transient git push error.

    Used by git_push exception handlers to decide whether to propagate
    the error (triggering Layer-2 orchestrator retry) or handle it locally.

    Args:
        e: The caught exception.

    Returns:
        True if the error is transient and should trigger retry.
    """
    from app.modules.workspace.autonomous.github_ops import GitHubOpsError

    if not isinstance(e, GitHubOpsError):
        return False
    err_str = str(e).lower()
    return any(kw in err_str for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS)


# ── PR-review helpers (shared with orchestrator) ────────────────────────────

# Language-aware approval marker for PR review (matches what the agent, writing
# in content_language, is asked to state). The structured verdict written to
# metadata.review_verdict removes progress_reported's dependency on this
# substring entirely.
_REVIEW_APPROVAL_PHRASES = {
    "en": "Code review passed",
    "zh": "代码审查通过",
    "ja": "コードレビュー合格",
    "ko": "코드 리뷰 통과",
}

# Machine-readable PR-review verdict contract (the review prompt in
# phases/pr_review.py asks for a single-line JSON as the last non-summary line
# before the TL;DR). These patterns are shared by the orchestrator's parser
# and the artifact-text scorer/cleaners — keep them in sync with the prompt.
# Exact-line form (fullmatch on one stripped line; group 1 = the JSON blob).
REVIEW_RESULT_LINE_FULLMATCH_RE = re.compile(r"^REVIEW_RESULT\s*:\s*(\{.*\})$")
# Multi-line text form (search across a whole artifact).
REVIEW_RESULT_LINE_SEARCH_RE = re.compile(r"(?m)^REVIEW_RESULT\s*:\s*\{.*\}\s*$")
# Summary line that may follow the verdict line — bold markers tolerated.
REVIEW_TLDR_LINE_RE = re.compile(r"^\*{0,2}TL;DR\*{0,2}\s*:", re.IGNORECASE)
# Decorative separators carry no verdict information. Match a STRIPPED line.
REVIEW_SEPARATOR_LINE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _review_approval_phrase(content_language: str | None) -> str:
    """Approval marker for PR review in the given content language."""
    from app.repositories.autonomous_repo import DEFAULT_CONTENT_LANGUAGE

    # content_language may be None; a None key is absent from the phrase map,
    # so the default (DEFAULT_CONTENT_LANGUAGE) applies. Cast to str for mypy.
    return _REVIEW_APPROVAL_PHRASES.get(
        cast("str", content_language), _REVIEW_APPROVAL_PHRASES[DEFAULT_CONTENT_LANGUAGE]
    )


def _extract_pr_number_from_error(error_text: str) -> int | None:
    """Extract a PR number from a gh "already exists" error message.

    gh's already-exists message includes the PR URL, e.g.:
      "a pull request for branch X into branch main already exists:
       https://github.com/owner/repo/pull/1877"
    Parsing the /pull/<n> URL gives the PR number directly — this avoids
    coupling recovery to the exact "already exists" wording, skips the
    eventually-consistent find_existing_pr API call, and proves the error
    really is the already-exists case (no URL → not recoverable here).
    Returns the PR number or None.
    """
    if not error_text:
        return None
    m = re.search(r"/pull/(\d+)", error_text)
    return int(m.group(1)) if m else None


def _parse_metadata(raw) -> dict:
    """Parse a milestone metadata JSON string/dict into a dict (empty on failure)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        # json.loads returns Any; cast so mypy treats it as dict (the call site
        # in _merge_milestone_metadata only reads string keys, and a non-dict
        # JSON value would surface as a TypeError on .update, caught above).
        return cast("dict", json.loads(raw))
    except (ValueError, TypeError):
        return {}


def _merge_milestone_metadata(milestone, updates: dict) -> str:
    """Merge ``updates`` into a milestone's metadata, returning the JSON string.

    ``milestone`` may be a milestone dict (read from the repo) or a raw metadata
    JSON string. Existing keys are preserved; ``updates`` keys overwrite.
    """
    raw = milestone.get("metadata") if isinstance(milestone, dict) else milestone
    merged = _parse_metadata(raw)
    merged.update(updates)
    return json.dumps(merged, ensure_ascii=False)


def _zcode_planning_mode(wf: dict) -> str:
    """Return the ZCode --mode for planning-phase calls.

    ZCode planning must stay read-only (plan mode, #761). For all other CLI
    tools, pass through the workflow's permission_mode unchanged. For ZCode,
    force "plan" so the agent can't write files or run commands during
    planning — allowed_tools is [] for both planning and dev (zcode uses its
    own built-in toolset), so the mode is the only reliable read-only signal.
    """
    if wf.get("cli_tool") in ("zcode", "zcode-code"):
        return "plan"
    # wf.get returns Any (dict value); cast to str so mypy treats it as str
    # (the default "auto-edit" covers the missing-key case; a non-str persisted
    # value would be a data-integrity bug, not a runtime concern here).
    return cast("str", wf.get("permission_mode", "auto-edit"))
