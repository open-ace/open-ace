# mypy: disable-error-code="no-any-return,assignment,var-annotated"
"""
Open ACE - GitHub Operations

Wraps the `gh` CLI for repo, issue, branch, worktree, and PR operations.
All methods invoke gh/git via subprocess and return parsed results.
"""

from __future__ import annotations

import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.utils.workspace import OPENACE_RM_WRAPPER

logger = logging.getLogger(__name__)

# ============================================================================
# 【Issue #2334】审计日志配置
# ============================================================================
# 结构化审计日志，记录 git/gh 操作用于安全审计
# 格式：JSON lines，包含 actor、target_user、cwd、command、result 等
AUDIT_LOG_PATH = os.environ.get("OPENACE_SUDOERS_AUDIT_LOG", "/var/log/openace/sudoers-audit.log")


def _log_sudo_audit(
    event: str,
    actor: str,
    target_user: str | None,
    cwd: str,
    command: str,
    command_class: str,
    workflow_id: str | None = None,
    request_id: str | None = None,
    result: str = "attempt",
    duration_ms: int | None = None,
) -> None:
    """Write structured audit log entry for sudo git/gh operations.

    Per Issue #2334, all git/gh operations through github_ops must have
    audit logging. This function writes JSON lines to the audit log
    without containing any secrets.

    Args:
        event: Event type (e.g., "sudo_git", "sudo_gh")
        actor: The user invoking the operation
        target_user: The target user for sudo (system_account or None)
        cwd: Current working directory
        command: The command being executed
        command_class: Classification (e.g., "git", "gh")
        workflow_id: Optional workflow ID
        request_id: Optional request ID
        result: Result status ("attempt", "success", "failure")
        duration_ms: Optional duration in milliseconds
    """
    import threading

    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "event": event,
        "actor": actor,
        "target_user": target_user or "",
        "cwd": cwd,
        "command": command,
        "command_class": command_class,
        "workflow_id": workflow_id or "",
        "request_id": request_id or "",
        "result": result,
        "duration_ms": duration_ms or 0,
        "thread_id": threading.current_thread().name,
    }

    # Write to audit log file
    try:
        log_dir = os.path.dirname(AUDIT_LOG_PATH)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(audit_entry) + "\n")
    except Exception:
        # Fail silently - audit logging should not break operations
        # Log to Python logger as fallback
        logger.debug("Audit log write failed for %s: %s", event, command)


# macOS BSD ``stat`` takes the format via ``-f``; GNU ``stat`` (Linux) uses
# ``-c``.  ``-L`` (follow symlinks) is supported by both.  Hard-coded
# ``stat -c`` breaks on macOS with "illegal option -- c".
_STAT_FORMAT_FLAG = "-f" if sys.platform == "darwin" else "-c"

_GITHUB_ACTIONS_JOB_PATH_RE = re.compile(
    r"^/[^/]+/[^/]+/actions/runs/(?P<run_id>\d+)/job/(?P<job_id>\d+)(?:/|$)"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Transient git/gh error retry — network blips (TLS, DNS, connection reset)
# are common and recover within seconds. Fixed-interval retry is sufficient
# because git operations are lightweight and idempotent; exponential backoff
# (designed for API rate-limiting) is unnecessary here.
GIT_NETWORK_RETRY_COUNT = 3
GIT_NETWORK_RETRY_INTERVAL = 10  # seconds

# Keywords that indicate a transient network error (vs a real git failure like
# conflicts, missing branches, etc.).
_TRANSIENT_ERROR_KEYWORDS = [
    "libressl",
    "openssl",
    "ssl",
    "tls",
    "connection reset",
    "connection refused",
    "connection timed out",
    "timed out",
    "could not resolve host",
    "temporary failure in name resolution",
    "network is unreachable",
    "unable to access",
    "rpc failed",
    "early eof",
    # libcurl "Empty reply from server" — git emits this verbatim (in English,
    # even under a non-C host locale) on a transient TLS/connection drop to the
    # remote. Without it, exit-128 empty-reply permanent-fails the workflow
    # instead of retrying (#2299).
    "empty reply",
    "empty response",
]


def _is_transient_error(stderr: str, returncode: int) -> bool:
    """Whether a git/gh subprocess failure looks like a transient network issue.

    Returns True for transient issues (worth retrying) vs permanent errors
    (conflict, missing branch, auth).

    Git uses exit code 128 for fatal errors; gh CLI uses exit 1. Both can
    carry network-related messages, so we check the keywords regardless of
    the specific non-zero exit code.
    """
    if returncode == 0:
        return False
    combined = f"{stderr}".lower()
    return any(kw in combined for kw in _TRANSIENT_ERROR_KEYWORDS)


# --force-with-lease recovery: a push rejected with these markers means the
# local remote-tracking ref is stale relative to the actual remote — typically
# because the auto-dev worktree was recreated after a failure cleanup (so its
# ``refs/remotes/origin/<branch>`` lags the real tip), or a concurrent push to
# the same single-owner branch. A targeted ``git fetch`` refreshes the lease so
# one retry succeeds. Network errors are deliberately excluded: fetching cannot
# fix them, and the orchestrator Layer-2 retry remains the backstop for those.
# ``constants._TRANSIENT_ORCHESTRATOR_KEYWORDS`` already classifies these
# transient; this predicate narrows to the lease-refresh subset.
_FORCE_WITH_LEASE_REFRESH_KEYWORDS = (
    "stale info",
    "fetch first",
    "non-fast-forward",
)


def _is_stale_lease_rejection(err_str: str) -> bool:
    """Whether a force-with-lease push failure is a recoverable stale lease."""
    combined = f"{err_str}".lower()
    return any(kw in combined for kw in _FORCE_WITH_LEASE_REFRESH_KEYWORDS)


# Failure markers used to locate the real error lines inside a long pre-commit /
# pytest / mypy log. The tail-only approach drops errors that sit in the middle
# of the output (e.g. mypy failing while later hooks keep printing).
_FAILURE_LINE_RE = re.compile(
    r"(error:|Error:|ERROR:|Failed|failed|FAIL\b|"
    r"would reformat|not formatted|not sorted|"
    r"no-any-return|undefined name|"
    r"\b[FEW]\d{3}\b)",  # flake8/ruff error codes
    re.IGNORECASE,
)


def _extract_failure_lines(lines: list[str], max_lines: int = 80) -> list[str]:
    """Return lines that look like failure output, plus 1 line of context.

    Falls back to the tail when no line matches (keeps the original behavior
    for logs without recognizable markers).
    """
    hit_indices: set[int] = set()
    for i, line in enumerate(lines):
        if _FAILURE_LINE_RE.search(line):
            hit_indices.add(i)
            if i > 0:
                hit_indices.add(i - 1)
            if i < len(lines) - 1:
                hit_indices.add(i + 1)
    if not hit_indices:
        return lines[-max_lines:]
    result = [lines[i] for i in sorted(hit_indices)]
    return result[-max_lines:] if len(result) > max_lines else result


class GitHubOpsError(Exception):
    """Error raised when a GitHub operation fails."""

    pass


def _assert_path_contained(child: str, parent: str, *, label: str) -> None:
    """Reject ``/repo`` vs ``/repo-evil`` prefix confusion via commonpath().

    Canonicalizes both paths with ``os.path.realpath`` (resolving symlinks
    and ``.``/``..``) before comparing, so ``/repo-evil/x`` is correctly
    rejected as not under ``/repo`` even though it is a string prefix.
    Raises ``GitHubOpsError`` if ``child`` escapes ``parent`` or if the
    comparison cannot be made (different drives on Windows).
    """
    if not child or not parent:
        raise GitHubOpsError(f"{label} containment check failed: empty path")
    c_child = os.path.realpath(child)
    c_parent = os.path.realpath(parent)
    if not c_child or not c_parent:
        raise GitHubOpsError(f"{label} containment check failed: empty path")
    try:
        if os.path.commonpath((c_child, c_parent)) != c_parent:
            raise GitHubOpsError(f"{label} escapes containment: {c_child} not under {c_parent}")
    except ValueError:
        # Different drives / cannot relativize — fail closed.
        raise GitHubOpsError(f"{label} containment check failed: {c_child} vs {c_parent}")


class GitHubOps:
    """GitHub operations using the gh CLI."""

    # Trusted Git contexts are captured by the orchestrator before an
    # untrusted agent starts.  Subsequent GitHubOps instances for that
    # worktree bypass the replaceable ``.git`` directory entry and address the
    # original gitdir directly, preventing confused-deputy pushes.
    _trusted_git_contexts: dict[str, dict[str, str]] = {}

    def __init__(self, repo_path: str, system_account: str | None = None):
        """
        Args:
            repo_path: Local path to the git repository (for cwd in gh commands).
            system_account: Optional system account name for multi-user permission isolation.
                When set, git/gh commands will be run via `sudo -u <system_account>`.
        """
        self.repo_path = repo_path
        self.system_account = system_account
        trusted = self._trusted_git_contexts.get(os.path.realpath(repo_path))
        self._trusted_git_dir = trusted.get("git_dir", "") if trusted else ""
        self._trusted_work_tree = trusted.get("work_tree", "") if trusted else ""
        self._trusted_git_identity = trusted.get("git_identity", "") if trusted else ""
        self._trusted_common_dir = trusted.get("common_dir", "") if trusted else ""
        self._trusted_common_identity = trusted.get("common_identity", "") if trusted else ""
        # Cached owner/repo (e.g. "open-ace/open-ace", or "host/owner/repo" for
        # GHES) resolved from the local repo's origin remote, used to target gh
        # under a sudo wrapper where gh can no longer infer the repo from cwd.
        # Lazily populated by _resolve_owner_repo(); None means "not resolvable
        # / not yet tried".
        self._owner_repo: str | None = None
        # Pure OWNER/REPO slug (never carries the GHES host), used for REST API
        # paths (gh api repos/<owner>/<repo>/...). Populated alongside
        # _owner_repo by _resolve_owner_repo().
        self._repo_slug: str | None = None
        # The remote host (e.g. "github.com" or "gh.example.com"), needed for
        # gh api --hostname under a sudo wrapper on GHES. None until resolved.
        self._repo_host: str | None = None
        # Tracks whether _resolve_owner_repo has been attempted, so None can
        # distinguish "tried and failed" from "not yet tried".
        self._owner_repo_resolved: bool = False

    @classmethod
    def register_trusted_git_context(
        cls,
        repo_path: str,
        git_dir: str,
        git_identity: str,
        common_dir: str,
        common_identity: str,
    ) -> None:
        """Pin future git commands for ``repo_path`` to a pre-agent gitdir."""
        real_repo = os.path.realpath(repo_path)
        real_git_dir = os.path.realpath(git_dir)
        if not real_repo or not real_git_dir or not os.path.isabs(real_git_dir):
            raise GitHubOpsError("Cannot register an invalid trusted Git context")
        if not git_identity or not common_identity:
            raise GitHubOpsError("Cannot register Git context without filesystem identity")
        # Containment: the repo_path must live under the main repo root, i.e.
        # the parent directory of common_dir. For a main repo common_dir ==
        # <repo>/.git so its parent is <repo> == repo_path. For a linked
        # worktree common_dir points at the MAIN repo's <repo>/.git (not under
        # the worktree path), so we verify the worktree (repo_path) lives under
        # the main repo root (common_dir's parent) instead. The basename check
        # confines common_dir to a real git metadata directory named ".git",
        # preventing an arbitrary subdir from being pinned; the root guard
        # rejects a common_dir directly under "/" (e.g. /.git) which would
        # otherwise contain any repo_path. Prefix confusion (/repo vs
        # /repo-evil) and symlink escapes are rejected by _assert_path_contained
        # via os.path.realpath + os.path.commonpath on both sides.
        # NOTE: bare repos (basename "repo.git") are not supported here; open-ace
        # only uses regular repos + linked worktrees.
        # NOTE: the root guard (common_parent == os.sep) is Unix-specific; this
        # matches the orchestrator's os.sep assumption (Linux/macOS only).
        real_common_dir = os.path.realpath(common_dir)
        if os.path.basename(real_common_dir) != ".git":
            raise GitHubOpsError(f"trusted common_dir is not a .git directory: {real_common_dir}")
        common_parent = os.path.dirname(real_common_dir)
        if common_parent == os.sep:
            raise GitHubOpsError(f"trusted common_dir root escape: {real_common_dir}")
        _assert_path_contained(real_repo, common_parent, label="trusted repo_path")
        # Defense-in-depth: git_dir must live under common_dir. For a main repo
        # git_dir == common_dir == <repo>/.git; for a linked worktree git_dir is
        # <common_dir>/worktrees/<name>. An out-of-tree git_dir is rejected even
        # though device:inode identity pinning is the primary defense.
        if real_git_dir != real_common_dir:
            _assert_path_contained(real_git_dir, real_common_dir, label="trusted git_dir")
        cls._trusted_git_contexts[real_repo] = {
            "git_dir": real_git_dir,
            "work_tree": real_repo,
            "git_identity": git_identity,
            "common_dir": real_common_dir,
            "common_identity": common_identity,
        }

    @classmethod
    def clear_trusted_git_context(cls, repo_path: str) -> None:
        cls._trusted_git_contexts.pop(os.path.realpath(repo_path), None)

    def bind_trusted_git_context(
        self,
        git_dir: str,
        git_identity: str,
        common_dir: str,
        common_identity: str,
    ) -> None:
        """Pin this already-created instance and all future instances."""
        self.register_trusted_git_context(
            self.repo_path, git_dir, git_identity, common_dir, common_identity
        )
        trusted = self._trusted_git_contexts[os.path.realpath(self.repo_path)]
        self._trusted_git_dir = trusted["git_dir"]
        self._trusted_work_tree = trusted["work_tree"]
        self._trusted_git_identity = trusted["git_identity"]
        self._trusted_common_dir = trusted["common_dir"]
        self._trusted_common_identity = trusted["common_identity"]
        # Re-resolve the repository slug from the now-trusted origin.
        self._owner_repo = None
        self._repo_slug = None
        self._repo_host = None
        self._owner_repo_resolved = False

    def get_path_identity(self, path: str) -> str:
        """Return device+inode for ``path`` using the repository owner identity.

        Uses an in-process ``os.stat`` (cross-platform, follows symlinks like
        ``stat -L``) when the service already runs as the repo owner — the
        common same-user case.  Only the cross-user case shells out through
        ``sudo -u <account> stat ...``, where the GNU ``-c`` vs BSD ``-f``
        format-flag split is handled per platform: macOS BSD ``stat`` rejects
        ``-c`` with "illegal option -- c", which previously made every local
        workflow fail at the trusted-Git-context snapshot on macOS.
        """
        if not self._needs_sudo():
            try:
                st = os.stat(path)  # follows symlinks, matching ``stat -L``
            except OSError as e:
                raise GitHubOpsError(f"Cannot verify protected Git path {path}: {e}")
            return f"{st.st_dev}:{st.st_ino}"
        kwargs = self._build_subprocess_kwargs()
        kwargs.pop("cwd", None)
        kwargs["timeout"] = 10
        account = self.system_account
        assert account is not None  # _needs_sudo() guarantees non-empty
        command = [
            "sudo",
            "-n",
            "-u",
            account,
            "stat",
            "-L",
            _STAT_FORMAT_FLAG,
            "%d:%i",
            path,
        ]
        result = subprocess.run(command, **kwargs)
        if result.returncode != 0 or not result.stdout.strip():
            raise GitHubOpsError(
                f"Cannot verify protected Git path {path}: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _verify_trusted_git_context(self) -> None:
        """Fail closed if an agent replaced either pinned Git directory."""
        if not self._trusted_git_dir:
            return
        if self.get_path_identity(self._trusted_git_dir) != self._trusted_git_identity:
            raise GitHubOpsError("Protected Git directory identity changed after agent execution")
        if self.get_path_identity(self._trusted_common_dir) != self._trusted_common_identity:
            raise GitHubOpsError(
                "Protected Git common-directory identity changed after agent execution"
            )

    def _get_env(self) -> dict[str, str] | None:
        """Get environment overrides for AI GitHub account.

        Delegates to ``config.get_ai_github_env()`` which has a 60-second
        TTL cache.  Token updates propagate to all GitHubOps instances
        within ~60 seconds automatically (or immediately if the admin API
        invalidates the cache).

        Returns None if:
          - No token is configured in the database
          - Loading failed (exception caught)
        """
        try:
            from app.utils.config import get_ai_github_env

            return get_ai_github_env()
        except Exception:
            return None

    def _build_subprocess_kwargs(self) -> dict:
        """Build subprocess.run kwargs, injecting AI env if configured."""
        kwargs = {
            "cwd": self.repo_path,
            "capture_output": True,
            "text": True,
            "timeout": 120,
        }
        ai_env = self._get_env()
        if ai_env is not None:
            kwargs["env"] = {**os.environ, **ai_env}
        return kwargs

    def _needs_sudo(self) -> bool:
        """Whether git/gh commands actually need a sudo -u wrapper.

        A sudo wrapper is only required when the service process user differs
        from ``system_account`` (Issue #1395/#1421: cross-user access to a
        user's private workspace). When the service already runs as
        ``system_account`` (common dev/single-user setup, and systemd
        User=<account> deployments), sudo is a no-op that additionally breaks
        under NoNewPrivileges — mirroring the same-user short-circuit already
        used in webui_manager. Skipping it also sidesteps the gh ``-C`` flag
        incompatibility (gh has no ``-C``; see _run_gh) for this common case.
        """
        if not self.system_account:
            return False
        try:
            current_user = pwd.getpwuid(os.getuid()).pw_name
        except (KeyError, OverflowError):
            # Cannot determine the current user; assume cross-user to stay safe.
            return True
        return current_user != self.system_account

    def _resolve_owner_repo(self) -> str | None:
        """Resolve the ``owner/repo`` slug for the local repo's origin remote.

        gh CLI infers the target repo from the working directory's ``.git``,
        but under a ``sudo -u`` wrapper we drop ``cwd`` (it triggers a Python
        permission check as the service user, Issue #1421) and gh has no ``-C``
        flag to compensate. Instead we resolve ``owner/repo`` from the local
        remote (git ``-C`` *is* valid, so ``_run_git`` still works under sudo)
        and pass it to gh via ``-R``.

        Cached on the instance after the first call; returns None when the
        remote is absent or unparseable (e.g. a brand-new project before its
        GitHub repo is wired up).
        """
        if self._owner_repo_resolved:
            return self._owner_repo
        self._owner_repo_resolved = True
        try:
            url = self.get_repo_url().strip()
        except GitHubOpsError:
            # No origin remote configured (e.g. new project pre-create_repo).
            return None
        if not url:
            return None
        # Strip credentials from URL before parsing
        # (handles https://user:token@host and https://user@host formats)
        url = re.sub(r"^(https?://)([^@/:]+(?::[^@/]*)?@)", r"\1", url)
        # Parse the host and owner/repo from common git remote forms:
        #   https://github.com/<owner>/<repo>[.git]
        #   git@github.com:<owner>/<repo>[.git]            (SCP-style)
        #   ssh://git@github.com/<owner>/<repo>[.git]
        #   https://gh.example.com/<owner>/<repo>[.git]    (GHES)
        # gh's -R/--repo accepts [HOST/]OWNER/REPO, so for non-github.com hosts
        # (GHES) we include the host so the command targets the right server.
        host: str | None = None
        slug: str | None = None
        m = re.match(
            r"(?:https?://([^/]+)/|ssh://[^@]+@([^/]+)/|(?:[^@]+@([^:]+):))"  # host capture
            r"([^/]+/[^/]+?)(?:\.git)?/?$",
            url,
        )
        if m:
            host = next((g for g in (m.group(1), m.group(2), m.group(3)) if g), None)
            slug = m.group(4)
        if not slug:
            return None
        # The API-path slug is always plain OWNER/REPO (REST paths don't carry
        # the host), regardless of host.
        self._repo_slug = slug
        self._repo_host = host
        # Only github.com is the public host; any other host is a GHES instance
        # and must be passed through to gh's -R as HOST/OWNER/REPO.
        if host and host != "github.com":
            self._owner_repo = f"{host}/{slug}"
        else:
            self._owner_repo = slug
        return self._owner_repo

    def _run_gh(
        self,
        args: list[str],
        check: bool = True,
        repo_scoped: bool = True,
        api_only: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a gh CLI command with transient-network-error retry.

        Args:
            args: gh CLI argument list (e.g. ``["issue", "view", "42", "--json", ...]``).
            check: Raise ``GitHubOpsError`` on non-zero exit when True.
            repo_scoped: Whether the command targets a specific repo and should
                carry ``-R owner/repo`` under a sudo wrapper. Most gh subcommands
                (issue/pr/view) accept ``-R``, but ``repo create`` / ``repo view``
                do not — for those the caller passes ``repo_scoped=False`` so the
                sudo path runs plain ``gh`` without repo context (they don't need
                an existing-repo context anyway).
            api_only: The command is a pure GitHub API call that carries
                ``-R owner/repo`` and needs no local repo access (e.g.
                ``issue``/``pr comment``). When True AND a bot token is configured
                AND the command would otherwise sudo (cross-user), run ``gh`` as
                the *service* user instead of ``sudo -u <owner>``. This lets
                ``GH_TOKEN`` reach ``gh`` so the action is attributed to the
                configured AI bot account; the sudo wrapper would otherwise strip
                ``GH_TOKEN`` via sudo ``env_reset`` and the action would post as
                the repo owner (issue #2339). Git ops and gh commands that need
                local repo context ignore this flag and keep the sudo path.
        """
        self._verify_trusted_git_context()
        kwargs = self._build_subprocess_kwargs()

        # 【Issue #2334】审计日志：记录 gh 操作
        actor = pwd.getpwuid(os.getuid()).pw_name if os.getuid() else "unknown"
        start_time = time.time()
        command_str = " ".join(["gh"] + args)
        # gh has no `-c <key>=<val>` flag, so on the same-user path we trust the
        # canonical repo via GIT_CONFIG_COUNT env (per-command, never written to
        # a config file). This replaces the old global ``safe.directory *``.
        # _build_subprocess_kwargs only sets ``env`` when an AI token is
        # configured, so fall back to a copy of os.environ to ensure the vars
        # reach gh (and the git it shells out to) on the no-token path too.
        #
        # Under ``sudo -u`` this env is stripped by sudo's default env_reset,
        # but that is intentional and safe: the sudo path drops cwd (gh has no
        # ``-C``) and targets the repo explicitly via ``-R owner/repo``, so gh
        # has no local repo cwd that would trigger a dubious-ownership check.
        # Any git operation gh needs (e.g. resolving the remote) is routed
        # through _resolve_owner_repo -> _run_git, which carries the per-command
        # ``-c safe.directory=`` inline and does reach the sudo child. Issue
        # #2021.
        env = kwargs.get("env") or dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = os.path.realpath(self.repo_path)
        kwargs["env"] = env
        account = self.system_account
        needs_sudo = self._needs_sudo()
        # Pure-API gh subcommands (issue/pr comment with -R owner/repo) need no
        # local repo access, so when a bot token is configured and the command
        # would otherwise sudo, run gh as the service user directly. This lets
        # GH_TOKEN reach gh so the action is attributed to the configured AI bot
        # account; the sudo -u owner wrapper would otherwise strip GH_TOKEN via
        # sudo env_reset and the action would post as the repo owner (#2339).
        # _get_env is cached (60s TTL) so this call is cheap and matches the env
        # _build_subprocess_kwargs already built. Note: _resolve_owner_repo may
        # still perform a one-time sudoed git read to populate owner/repo (a read
        # the owner is entitled to, unchanged from today); only the gh API call
        # itself drops the sudo wrapper.
        api_as_service = bool(api_only) and needs_sudo and self._get_env() is not None
        if needs_sudo and not api_as_service:
            # gh has no `-C <path>` flag (that is git-only), so under a sudo
            # wrapper — where we must drop cwd to avoid a Python permission
            # check as the service user (Issue #1421) — target the repo
            # explicitly via `-R owner/repo` resolved from the local remote.
            assert account is not None  # _needs_sudo() guarantees non-empty
            cmd: list[str] = ["sudo", "-u", account]
            owner_repo = self._resolve_owner_repo() if repo_scoped else None
            if owner_repo:
                cmd += ["gh", "-R", owner_repo] + args
            else:
                # No resolvable remote, or a command (repo create/view) that
                # rejects -R. Run plain gh; commands that need repo context
                # (issue/pr) will already have a resolvable owner_repo above.
                cmd += ["gh"] + args
            kwargs.pop("cwd", None)  # cwd under sudo triggers Permission denied (Issue #1421)
        else:
            # Same-user (or no system_account), OR an api_only command running as
            # the service user (#2339): run gh directly. Same-user keeps cwd on the
            # repo so gh infers owner/repo from the working directory as before;
            # the api_only path drops cwd because the service user may lack access
            # to the owner's repo, and a pure-API call carries -R anyway.
            if api_as_service:
                kwargs.pop("cwd", None)
                owner_repo = self._resolve_owner_repo() if repo_scoped else None
            else:
                owner_repo = (
                    self._resolve_owner_repo() if repo_scoped and self._trusted_git_dir else None
                )
            cmd = ["gh", "-R", owner_repo, *args] if owner_repo else ["gh", *args]
        last_error: GitHubOpsError | None = None
        for attempt in range(GIT_NETWORK_RETRY_COUNT):
            try:
                result = subprocess.run(cmd, **kwargs)
                if check and result.returncode != 0:
                    err = GitHubOpsError(
                        f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
                    )
                    if (
                        _is_transient_error(result.stderr, result.returncode)
                        and attempt < GIT_NETWORK_RETRY_COUNT - 1
                    ):
                        last_error = err
                        logger.warning(
                            "gh %s transient error (attempt %d/%d), retrying in %ds",
                            args[0],
                            attempt + 1,
                            GIT_NETWORK_RETRY_COUNT,
                            GIT_NETWORK_RETRY_INTERVAL,
                        )
                        time.sleep(GIT_NETWORK_RETRY_INTERVAL)
                        continue
                    raise err
                # 【Issue #2334】审计日志：成功完成
                duration_ms = int((time.time() - start_time) * 1000)
                # Determine target user: account if sudo, None if running as current user
                target_user = account if needs_sudo and not api_as_service else None
                _log_sudo_audit(
                    event="sudo_gh",
                    actor=actor,
                    target_user=target_user,
                    cwd=self.repo_path,
                    command=command_str,
                    command_class="gh",
                    result="success",
                    duration_ms=duration_ms,
                )
                return result
            except subprocess.TimeoutExpired:
                if attempt < GIT_NETWORK_RETRY_COUNT - 1:
                    last_error = GitHubOpsError(f"gh {' '.join(args)} timed out after 120s")
                    logger.warning(
                        "gh %s timed out (attempt %d/%d), retrying in %ds",
                        args[0],
                        attempt + 1,
                        GIT_NETWORK_RETRY_COUNT,
                        GIT_NETWORK_RETRY_INTERVAL,
                    )
                    time.sleep(GIT_NETWORK_RETRY_INTERVAL)
                    continue
                raise GitHubOpsError(f"gh {' '.join(args)} timed out after 120s")
            except FileNotFoundError:
                raise GitHubOpsError("gh CLI not found. Please install and authenticate gh.")
        raise last_error or GitHubOpsError(f"gh {' '.join(args)} failed after retries")

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command with transient-network-error retry."""
        self._verify_trusted_git_context()
        kwargs = self._build_subprocess_kwargs()
        account = self.system_account

        # 【Issue #2334】审计日志：记录 git 操作
        actor = pwd.getpwuid(os.getuid()).pw_name if os.getuid() else "unknown"
        start_time = time.time()
        command_str = " ".join(["git"] + args)

        trusted_args: list[str] = []
        if self._trusted_git_dir:
            trusted_args = [
                f"--git-dir={self._trusted_git_dir}",
                f"--work-tree={self._trusted_work_tree or os.path.realpath(self.repo_path)}",
            ]
        # Inside the agent sandbox PATH, "git" resolves to the orchestrator-
        # only guard shim; the harness exposes the real binary via
        # OPENACE_REAL_GIT for code that must run git directly (tests,
        # tooling). Unset everywhere else, so this defaults to plain "git".
        # The sudo branch deliberately keeps the literal "git": prod sudoers
        # whitelist only the bare command name, and a resolved absolute path
        # under ``sudo -u <account>`` would be silently denied.
        needs_sudo = self._needs_sudo()
        git_bin = os.environ.get("OPENACE_REAL_GIT", "git") if not needs_sudo else "git"
        # Trust the canonical repo via per-command ``-c`` (never the global
        # ``safe.directory *`` that used to be written via
        # _ensure_safe_directory). git's dubious-ownership check covers every
        # path it touches — the work tree, the git dir, and the (shared)
        # common dir — so we emit one ``-c safe.directory=<path>`` per trusted
        # path rather than a single entry. De-duplicated and absolute; the
        # work tree, git dir, and common dir are often distinct (e.g. a linked
        # worktree's git dir lives under <repo>/.git/worktrees/<name>, while
        # the common dir is <repo>/.git), and in cross-user/isolated
        # deployments they can be owned by different accounts. Issue #2021.
        safe_paths: list[str] = []
        for p in (
            self._trusted_work_tree or os.path.realpath(self.repo_path),
            self._trusted_git_dir,
            self._trusted_common_dir,
        ):
            rp = os.path.realpath(p) if p else ""
            if rp and rp not in safe_paths:
                safe_paths.append(rp)
        safe_cfgs: list[str] = []
        for p in safe_paths:
            safe_cfgs += ["-c", f"safe.directory={p}"]
        if needs_sudo:
            # git supports `-C <path>` (unlike gh), so we use it to set the
            # working directory under a sudo wrapper where cwd would trigger a
            # Python permission check as the service user (Issue #1421).
            assert account is not None  # _needs_sudo() guarantees non-empty
            cmd: list[str] = (
                [
                    "sudo",
                    "-u",
                    account,
                    git_bin,
                    *trusted_args,
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    *safe_cfgs,
                ]
                + ([] if trusted_args else ["-C", self.repo_path])
                + args
            )
            kwargs.pop("cwd", None)  # Remove cwd to avoid Python permission check
        else:
            cmd = [
                git_bin,
                *trusted_args,
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                *safe_cfgs,
            ] + args
        last_error: GitHubOpsError | None = None
        for attempt in range(GIT_NETWORK_RETRY_COUNT):
            try:
                result = subprocess.run(cmd, **kwargs)
                if check and result.returncode != 0:
                    err = GitHubOpsError(
                        f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
                    )
                    if (
                        _is_transient_error(result.stderr, result.returncode)
                        and attempt < GIT_NETWORK_RETRY_COUNT - 1
                    ):
                        last_error = err
                        logger.warning(
                            "git %s transient error (attempt %d/%d), retrying in %ds",
                            args[0],
                            attempt + 1,
                            GIT_NETWORK_RETRY_COUNT,
                            GIT_NETWORK_RETRY_INTERVAL,
                        )
                        time.sleep(GIT_NETWORK_RETRY_INTERVAL)
                        continue
                    raise err
                # 【Issue #2334】审计日志：成功完成
                duration_ms = int((time.time() - start_time) * 1000)
                _log_sudo_audit(
                    event="sudo_git",
                    actor=actor,
                    target_user=account,
                    cwd=self.repo_path,
                    command=command_str,
                    command_class="git",
                    result="success",
                    duration_ms=duration_ms,
                )
                return result
            except subprocess.TimeoutExpired:
                if attempt < GIT_NETWORK_RETRY_COUNT - 1:
                    last_error = GitHubOpsError(f"git {' '.join(args)} timed out after 120s")
                    logger.warning(
                        "git %s timed out (attempt %d/%d), retrying in %ds",
                        args[0],
                        attempt + 1,
                        GIT_NETWORK_RETRY_COUNT,
                        GIT_NETWORK_RETRY_INTERVAL,
                    )
                    time.sleep(GIT_NETWORK_RETRY_INTERVAL)
                    continue
                raise GitHubOpsError(f"git {' '.join(args)} timed out after 120s")
            except FileNotFoundError:
                raise GitHubOpsError("git not found")
        raise last_error or GitHubOpsError(f"git {' '.join(args)} failed after retries")

    def path_exists_as_user(
        self, path: str, *, dir_only: bool = False, file_only: bool = False
    ) -> bool:
        """Check whether ``path`` exists, executing as ``system_account``.

        Python's ``Path.exists()`` stats with the service process's identity
        and raises ``PermissionError`` when the path sits under a user-private
        parent (e.g. a 700 home dir) — the same cross-user gap that the
        ``_run_git``/``_run_gh`` sudo wrapper closes for git/gh subprocesses
        (Issue #1395/#1421). This routes the check through
        ``sudo -u <system_account> test`` so it runs with the repo owner's
        privileges; ``test`` is covered by the sudoers ``OPENACE_UTILS`` alias.

        Args:
            path: Absolute path to check.
            dir_only: When True require the path to be a directory (``test -d``);
                otherwise any existing entry passes (``test -e``).
            file_only: When True require the path to be a regular file
                (``test -f``). Used to distinguish a worktree's ``.git`` file
                from a normal repo's ``.git`` directory. Mutually exclusive
                with ``dir_only``.
        """
        if not self._needs_sudo():
            if file_only:
                return Path(path).is_file()
            return Path(path).is_dir() if dir_only else Path(path).exists()
        if file_only:
            flag = "-f"
        elif dir_only:
            flag = "-d"
        else:
            flag = "-e"
        assert self.system_account is not None  # _needs_sudo() guarantees non-empty
        try:
            result = subprocess.run(
                ["sudo", "-u", self.system_account, "test", flag, path],
                capture_output=True,
                text=True,
                timeout=10,
                env=self._build_subprocess_kwargs().get("env"),
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("path_exists_as_user(%s) failed: %s", path, e)
            return False

    # ── Repo Operations ────────────────────────────────────────────

    def create_verification_worktree_dir(self, project_path: str) -> str:
        """Allocate an empty verifier worktree directory as the repo owner.

        ``git worktree add`` runs as ``system_account`` in multi-user mode, so
        its destination must not be a service-owned ``tempfile.mkdtemp``
        directory (which is mode 0700 and cannot be traversed cross-user).
        Keep verifier checkouts under the repository's established
        ``.worktrees`` root and create both the root and the unique child with
        the same identity that will run git.
        """
        project_root = os.path.realpath(project_path)
        if not project_root or not os.path.isabs(project_root):
            raise GitHubOpsError("Cannot allocate verifier worktree under an invalid project path")
        worktrees_root = os.path.join(project_root, ".worktrees")
        kwargs = self._build_subprocess_kwargs()
        kwargs.pop("cwd", None)
        kwargs["timeout"] = 10

        def _mkdir(args: list[str]) -> subprocess.CompletedProcess:
            if self._needs_sudo():
                account = self.system_account
                assert account is not None
                cmd = ["sudo", "-u", account, "mkdir", *args]
            else:
                cmd = ["mkdir", *args]
            return subprocess.run(cmd, **kwargs)

        root_result = _mkdir(["-p", "--", worktrees_root])
        if root_result.returncode != 0:
            raise GitHubOpsError(
                "Cannot create verifier worktree root: " f"{(root_result.stderr or '').strip()}"
            )

        for _attempt in range(3):
            path = os.path.join(worktrees_root, f"verify-{uuid.uuid4().hex}")
            result = _mkdir(["-m", "700", "--", path])
            if result.returncode == 0:
                return path
        raise GitHubOpsError(
            "Cannot allocate a unique verifier worktree directory: "
            f"{(result.stderr or '').strip()}"
        )

    def remove_verification_worktree_dir(self, path: str, project_path: str) -> None:
        """Remove a verifier directory with the identity that owns it.

        This is only the fallback after ``git worktree remove``. The strict
        direct-child and ``verify-`` checks keep recursive deletion scoped to
        directories allocated by :meth:`create_verification_worktree_dir`.
        """
        worktrees_root = os.path.realpath(os.path.join(project_path, ".worktrees"))
        real_path = os.path.realpath(path)
        _assert_path_contained(real_path, worktrees_root, label="verification worktree")
        if os.path.dirname(real_path) != worktrees_root or not os.path.basename(
            real_path
        ).startswith("verify-"):
            raise GitHubOpsError("Refusing to remove a non-verifier worktree directory")
        if not self._needs_sudo():
            shutil.rmtree(real_path, ignore_errors=True)
            return
        account = self.system_account
        assert account is not None
        result = subprocess.run(
            ["sudo", OPENACE_RM_WRAPPER, account, real_path, "-r", "-f"],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._build_subprocess_kwargs().get("env"),
        )
        if result.returncode != 0:
            raise GitHubOpsError(
                f"Cannot remove verifier worktree directory: {(result.stderr or '').strip()}"
            )

    def create_repo(self, name: str, private: bool = True, description: str = "") -> dict:
        """Create a new GitHub repository."""
        args = ["repo", "create", name]
        if private:
            args.append("--private")
        else:
            args.append("--public")
        if description:
            args.extend(["--description", description])

        # `gh repo create` rejects -R (unlike issue/pr subcommands), so disable
        # repo-scoped binding; it creates a new repo and needs no existing one.
        result = self._run_gh(args, repo_scoped=False)
        # gh repo create doesn't support --json; parse URL from stdout
        output = result.stdout.strip()
        repo_url = output.split("\n")[-1].strip()
        logger.info("Created repo: %s", name)
        return {"name": name, "url": repo_url}

    def get_repo_url(self) -> str:
        """Get the remote origin URL of the current repo."""
        result = self._run_git(["remote", "get-url", "origin"])
        return result.stdout.strip()

    def get_repo_name(self) -> str:
        """Get the repo name in ``owner/repo`` format (no GHES host).

        Resolved from the local ``origin`` remote via ``_resolve_owner_repo()``
        (which uses ``git remote get-url``, valid under a sudo wrapper) rather
        than ``gh repo view``: the latter rejects ``-R`` and, with ``cwd``
        dropped under sudo, can only guess the repo from the parent process's
        working directory — failing in non-repo dirs or returning the wrong
        repo. Falls back to ``gh repo view`` only when the remote is absent.
        """
        if self._resolve_owner_repo() is None:
            # No origin remote to parse (e.g. a freshly created repo before its
            # remote is wired). gh repo view rejects -R, so this runs plain;
            # under sudo it relies on cwd and is best-effort.
            result = self._run_gh(["repo", "view", "--json", "nameWithOwner"], repo_scoped=False)
            data = json.loads(result.stdout.strip())
            return data.get("nameWithOwner", "")
        return self._repo_slug or ""

    # ── Issue Operations ────────────────────────────────────────────

    def create_issue(self, title: str, body: str = "", labels: list[str] | None = None) -> dict:
        """Create a GitHub issue."""
        args = ["issue", "create", "--title", title, "--body", body or ""]
        if labels:
            for label in labels:
                args.extend(["--label", label])

        result = self._run_gh(args)
        # gh issue create doesn't support --json; parse URL from stdout
        output = result.stdout.strip()
        issue_url = output.split("\n")[-1].strip()
        try:
            issue_number = int(issue_url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            raise GitHubOpsError(f"Failed to parse issue number from output: {output}")
        logger.info("Created issue #%s", issue_number)
        return {"number": issue_number, "url": issue_url}

    def get_issue(self, number: int) -> dict:
        """Get issue details, including all comments.

        Comments are fetched so downstream phases (e.g. planning) can see
        clarifications or supplementary requirements that only appear in the
        issue discussion, not in the issue body.
        """
        result = self._run_gh(
            [
                "issue",
                "view",
                str(number),
                "--json",
                "number,title,body,url,state,labels,comments",
            ]
        )
        return json.loads(result.stdout.strip())

    def add_issue_comment(self, number: int, body: str) -> dict:
        """Add a comment, deduplicating an exact prior body from this bot.

        Acceptance verification can be re-entered after a crash between the
        GitHub mutation and the local workflow update.  Reading first makes the
        externally-visible operation idempotent for that retry instead of
        posting the same report twice.  A human-authored copy is not proof that
        the configured service account already posted its audit record.  When
        bot identity cannot be established, fail closed for deduplication and
        post the comment.
        """
        bot_login = self.get_authenticated_login()
        if bot_login:
            for comment in self.list_issue_comments(number):
                author = comment.get("author") or {}
                author_login = author.get("login") if isinstance(author, dict) else author
                if (
                    comment.get("body") == body
                    and isinstance(author_login, str)
                    and author_login.casefold() == bot_login.casefold()
                ):
                    logger.info("Issue #%s already has the requested bot comment; skipping", number)
                    return {
                        "number": number,
                        "id": comment.get("id"),
                        "deduplicated": True,
                    }
        self._run_gh(["issue", "comment", str(number), "--body", body], api_only=True)
        logger.info("Added comment to issue #%s", number)
        return {"number": number, "deduplicated": False}

    def close_issue(self, number: int) -> dict:
        """Close an issue.

        Runs as the service user (api_only) so the action attributes to the
        configured AI bot account, not the repo owner (#2339/#2335). The workflow
        closes explicitly only on acceptance confirmed.
        """
        self._run_gh(["issue", "close", str(number)], api_only=True)
        logger.info("Closed issue #%s", number)
        return {"number": number}

    def reopen_issue(self, number: int) -> dict:
        """Reopen an issue (api_only → service-user/bot identity).

        Used by the acceptance_verification reopen guard when an issue was closed
        out-of-band before confirmation (#2335).
        """
        self._run_gh(["issue", "reopen", str(number)], api_only=True)
        logger.info("Reopened issue #%s", number)
        return {"number": number}

    def close_pr(self, number: int) -> dict:
        """Close a PR (api_only → service-user/bot identity).

        Issue #2673: mechanical CI retrigger — closing and reopening a PR
        re-delivers the ``pull_request`` ``closed``/``reopened`` events when
        GitHub dropped the push/synchronize events for the head (zero
        check-runs). This is a CI nudge, not a code/PR-content change.
        """
        self._run_gh(["pr", "close", str(number)], api_only=True)
        logger.info("Closed PR #%s", number)
        return {"number": number}

    def reopen_pr(self, number: int) -> dict:
        """Reopen a PR (api_only → service-user/bot identity).

        Issue #2673: second half of the mechanical CI retrigger — see
        :meth:`close_pr`.
        """
        self._run_gh(["pr", "reopen", str(number)], api_only=True)
        logger.info("Reopened PR #%s", number)
        return {"number": number}

    def list_issue_comments(self, number: int, since: str | None = None) -> list:
        """List every issue comment, optionally since a timestamp.

        Use the paginated REST endpoint rather than ``gh issue view``'s
        connection so an older matching bot comment cannot fall off the first
        page and be posted again.  Output is normalized to the camelCase shape
        used by the existing wait/report consumers.
        """
        try:
            repo = self.get_repo_name()
        except (GitHubOpsError, json.JSONDecodeError):
            repo = ""
        if repo:
            result = self._run_gh(
                self._gh_api_args(
                    [
                        "--paginate",
                        f"repos/{repo}/issues/{number}/comments",
                        "--jq",
                        ".[] | {id, body, createdAt: .created_at, author: {login: .user.login}}",
                    ]
                ),
                repo_scoped=False,
                api_only=True,
            )
        else:
            # Pre-remote repositories cannot form a REST path. Preserve the
            # best-effort CLI fallback used before pagination hardening.
            result = self._run_gh(
                ["issue", "view", str(number), "--comments", "--json", "comments"],
                api_only=True,
            )
        comments: list[dict] = []
        for line in (result.stdout or "").splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            # Compatibility with old/mocked ``gh issue view`` output keeps
            # callers resilient while production uses NDJSON from ``--jq``.
            nested = parsed.get("comments")
            if isinstance(nested, list):
                comments.extend(item for item in nested if isinstance(item, dict))
            else:
                comments.append(parsed)
        if since:
            comments = [c for c in comments if c.get("createdAt", "") > since]
        return comments

    def get_authenticated_login(self) -> str | None:
        """Return the login associated with the configured bot credentials.

        Without an explicit AI GitHub token, ``gh`` may inherit a repository
        owner's personal login.  That identity must never be treated as the
        autonomous service account for reopen decisions.
        """
        env = self._get_env()
        if not env or not env.get("GH_TOKEN"):
            return None
        result = self._run_gh(
            self._gh_api_args(["user", "--jq", ".login"]),
            check=False,
            repo_scoped=False,
            api_only=True,
        )
        if result.returncode != 0:
            return None
        login = (result.stdout or "").strip()
        return login or None

    def get_issue_closure(self, number: int) -> dict | None:
        """Return the current issue closure timestamp and its actual actor.

        The issue view establishes that the issue is currently closed and gives
        the authoritative ``closedAt`` timestamp.  The timeline API supplies
        the actor for the latest close event.  Missing/ambiguous data returns
        ``None`` so callers fail closed and never reopen a human's issue.
        """
        state_result = self._run_gh(
            ["issue", "view", str(number), "--json", "state,closedAt"],
            check=False,
            api_only=True,
        )
        if state_result.returncode != 0:
            return None
        try:
            issue = json.loads((state_result.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return None
        closed_at = issue.get("closedAt")
        if str(issue.get("state") or "").lower() != "closed" or not closed_at:
            return None

        repo = self.get_repo_name()
        if not repo:
            return None
        timeline_result = self._run_gh(
            self._gh_api_args(
                [
                    "--paginate",
                    f"repos/{repo}/issues/{number}/timeline",
                    "--jq",
                    '.[] | select(.event == "closed") | {closed_at: .created_at, closer_login: .actor.login}',
                ]
            ),
            check=False,
            repo_scoped=False,
            api_only=True,
        )
        if timeline_result.returncode != 0:
            return None
        events: list[dict] = []
        for line in (timeline_result.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if not events:
            return None
        matching = [event for event in events if event.get("closed_at") == closed_at]
        if not matching:
            return None
        latest = matching[-1]
        closer_login = latest.get("closer_login")
        if not isinstance(closer_login, str) or not closer_login:
            return None
        return {"closed_at": closed_at, "closer_login": closer_login}

    def update_issue(self, number: int, title: str | None = None, body: str | None = None) -> dict:
        """Update an issue's title or body."""
        args = ["issue", "edit", str(number)]
        if title:
            args.extend(["--title", title])
        if body:
            args.extend(["--body", body])
        self._run_gh(args)
        logger.info("Updated issue #%s", number)
        return {"number": number}

    # ── Branch Operations ───────────────────────────────────────────

    def create_branch(self, name: str, base: str = "HEAD") -> dict:
        """Create a new branch."""
        self._run_git(
            ["checkout", "-b", name] if base == "HEAD" else ["checkout", "-b", name, base]
        )
        # Push and set upstream
        self._run_git(["push", "-u", "origin", name])
        logger.info("Created branch: %s", name)
        return {"branch": name}

    def get_current_branch(self) -> str:
        """Get the current branch name."""
        result = self._run_git(["branch", "--show-current"])
        return result.stdout.strip()

    def get_current_commit(self) -> str:
        """Get the current commit SHA."""
        result = self._run_git(["rev-parse", "HEAD"])
        return result.stdout.strip()

    def resolve_commit(self, ref: str) -> str:
        """Resolve a ref to an immutable commit SHA."""
        result = self._run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"])
        sha = result.stdout.strip()
        if not sha:
            raise GitHubOpsError(f"Unable to resolve commit ref: {ref}")
        return sha

    def checkout(self, ref: str) -> None:
        """Checkout a branch or commit."""
        self._run_git(["checkout", ref])

    def reset_hard_to_head(self) -> None:
        """Discard all uncommitted changes and staged state, reset to HEAD.

        Used by CI repair before a fresh-session retry to ensure the worktree
        is clean after an aborted agent run (e.g. a context-overflow failure
        that produced partial tool edits but no commit).
        """
        self._run_git(["reset", "--hard", "HEAD"])

    def reset_hard_to(self, ref: str) -> None:
        """Discard local CI-repair state and reset to a trusted immutable ref."""
        self._run_git(["reset", "--hard", ref])

    def delete_branch(self, name: str, remote: bool = True) -> dict:
        """Delete a branch locally and optionally remotely.

        Returns a structured result so callers can distinguish a real failure
        from an already-absent branch (#2043). Previously both commands used
        ``check=False`` and returned ``None``, silently swallowing failures —
        the caller could not tell whether deletion succeeded.

        Result shape::

            {"local": "deleted"|"absent"|"failed",
             "remote": "deleted"|"absent"|"failed"|"skipped",
             "errors": [str, ...]}

        ``absent`` is success-equivalent (the resource was already gone).
        ``failed`` means a real error warranting retry; ``errors`` carries the
        truncated stderr for diagnostics.

        Existence is checked via returncode-based probes (``show-ref`` locally,
        ``ls-remote`` remotely) rather than parsing stderr text, which varies
        across git versions and server implementations (GitHub.com / GitLab /
        GHES). A non-zero delete that follows a confirmed-existing resource is a
        real failure; a delete of an already-absent resource is reported absent.
        """
        # Local existence check: show-ref returncode is stable across git versions.
        local_exists = (
            self._run_git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], check=False
            ).returncode
            == 0
        )
        local_res = self._run_git(["branch", "-D", name], check=False)
        if local_res.returncode == 0:
            local = "deleted"
        elif not local_exists:
            local = "absent"
        else:
            local = "failed"
        result: dict = {"local": local, "remote": "skipped", "errors": []}
        if local == "failed":
            result["errors"].append((local_res.stderr or "").strip()[:500])
        if remote:
            # Remote existence check: ls-remote returncode is stable; an empty
            # stdout means the ref does not exist on the remote.
            ls_res = self._run_git(["ls-remote", "origin", name], check=False)
            remote_exists = ls_res.returncode == 0 and bool((ls_res.stdout or "").strip())
            remote_res = self._run_git(["push", "origin", "--delete", name], check=False)
            if remote_res.returncode == 0:
                result["remote"] = "deleted"
            elif not remote_exists:
                result["remote"] = "absent"
            else:
                result["remote"] = "failed"
                result["errors"].append((remote_res.stderr or "").strip()[:500])
        return result

    # ── Worktree Operations ─────────────────────────────────────────

    def create_worktree(self, path: str, branch: str, base: str = "HEAD") -> dict:
        """Create a git worktree with a new branch."""
        self._assert_worktree_contained(path)
        self.clear_trusted_git_context(path)
        self._run_git(["worktree", "add", "-b", branch, path, base])
        logger.info("Created worktree at %s on branch %s", path, branch)
        return {"worktree_path": path, "branch": branch}

    def add_worktree(self, path: str, branch: str) -> dict:
        """Create a worktree that checks out an EXISTING branch (no ``-b``).

        Used by merge-conflict resolution to get an isolated working tree of
        the PR branch without touching the main repo's index/HEAD. For a
        remote-only branch git auto-creates a local tracking branch.
        """
        self._assert_worktree_contained(path)
        self.clear_trusted_git_context(path)
        self._run_git(["worktree", "add", path, branch])
        logger.info("Added worktree at %s for existing branch %s", path, branch)
        return {"worktree_path": path, "branch": branch}

    def remove_worktree(self, path: str) -> dict:
        """Remove a git worktree."""
        self._assert_worktree_contained(path)
        self._run_git(["worktree", "remove", path, "--force"])
        self.clear_trusted_git_context(path)
        logger.info("Removed worktree at %s", path)
        return {"removed": path}

    def _assert_worktree_contained(self, path: str) -> None:
        """Reject worktree paths that escape the canonical repo root.

        Catches ``/repo`` vs ``/repo-evil`` prefix confusion, symlink escapes,
        and traversal before the path reaches ``git worktree add/remove``.
        """
        _assert_path_contained(path, os.path.realpath(self.repo_path), label="worktree")

    def list_worktrees(self) -> list:
        """List all worktrees.

        Returns entries as ``{"path": …, "branch": "refs/heads/<name>"}`` (no
        ``branch`` key when detached, but ``detached=True`` is set instead). The
        ``refs/heads/`` branch format is a contract:
        ``AutonomousOrchestrator._verify_worktree_restored`` matches against
        both ``<name>`` and ``refs/heads/<name>``. Parsing is locked by
        ``tests/issues/716/test_github_ops.py::test_list_worktrees``.

        Uses ``--porcelain -z`` so paths containing spaces or newlines are
        parsed correctly: each worktree record is NUL-terminated, and fields
        within a record are LF-separated. The plain LF-split parser used
        previously would split a newline-bearing path across records.
        """
        result = self._run_git(["worktree", "list", "--porcelain", "-z"])
        worktrees: list[dict] = []
        current: dict = {}
        for record in result.stdout.split("\0"):
            if not record:
                continue
            # Each NUL-terminated record describes one worktree; its fields
            # are LF-separated and the first is always "worktree <path>".
            for line in record.split("\n"):
                if line.startswith("worktree "):
                    if current:
                        worktrees.append(current)
                    current = {"path": line[len("worktree ") :]}
                elif line.startswith("branch "):
                    current["branch"] = line[len("branch ") :]
                elif line == "bare":
                    current["bare"] = True
                elif line == "detached":
                    # Record detached HEAD explicitly so callers can distinguish
                    # "detached" from "branch field transiently missing after a
                    # fresh `git worktree add` on APFS" (the latter has neither
                    # ``branch`` nor ``detached`` in the porcelain output).
                    current["detached"] = True
            if current:
                worktrees.append(current)
                current = {}
        return worktrees

    def resolve_worktree_branch(self, path: str) -> str | None:
        """Return the branch checked out in the worktree at ``path``.

        Reads the worktree's own HEAD via a fresh GitHubOps instance for
        ``path`` (the worktree's ``.git`` file redirects to the main repo's
        worktree metadata, so git resolves HEAD from the worktree's private
        gitdir, not the main repo's). This is the authoritative source,
        unaffected by the APFS registry-cache lag that can make
        ``git worktree list --porcelain`` transiently omit the ``branch``
        field right after ``git worktree add``.

        Returns the short branch name (e.g. ``main``), or ``None`` when the
        worktree is in detached HEAD state (``symbolic-ref`` exits non-zero)
        or git itself fails. Callers must fail closed on ``None``.
        """
        self._assert_worktree_contained(path)
        wt_gh = GitHubOps(path, system_account=self.system_account)
        try:
            result = wt_gh._run_git(["symbolic-ref", "--short", "HEAD"], check=False)
        except GitHubOpsError:
            # git binary not found / timeout — treat as "cannot resolve".
            return None
        if result.returncode != 0:
            # Non-zero exit = detached HEAD (symbolic-ref refuses non-symref HEAD).
            return None
        return result.stdout.strip() or None

    # ── PR Operations ───────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        body: str = "",
        head: str | None = None,
        base: str = "main",
        draft: bool = False,
    ) -> dict:
        """Create a pull request and return its details.

        When a bot token is configured, the PR is created via the REST API as the
        service user (api_only) so ``GH_TOKEN`` reaches ``gh`` and the PR
        attributes to the configured AI bot account — not the repo owner (#2340).
        Falls back to ``gh pr create`` (owner identity) when no token is set or
        the repo slug can't be resolved.
        """
        if self._get_env() is not None:
            # Resolve so _repo_slug (plain OWNER/REPO, never host-prefixed) is set;
            # _resolve_owner_repo() returns _owner_repo which carries the GHES host
            # and would malformed the REST path on GHES (#2340 review L1).
            self._resolve_owner_repo()
            slug = self._repo_slug
            if slug:
                fields: list[tuple[str, str]] = [
                    ("title", title),
                    ("base", base),
                    ("body", body or ""),
                ]
                if head:
                    fields.append(("head", head))
                if draft:
                    fields.append(("draft", "true"))
                api_args = ["--method", "POST", f"repos/{slug}/pulls"]
                for key, value in fields:
                    api_args += ["-f", f"{key}={value}"]
                # check=False: gh api writes the JSON error body to STDOUT (only the
                # summary to stderr). Surface both in the exception so the
                # "already exists" race recovery (#1857) in pr_review still detects
                # it and falls back to find_existing_pr.
                result = self._run_gh(
                    self._gh_api_args(api_args), repo_scoped=False, api_only=True, check=False
                )
                if result.returncode != 0:
                    detail = (result.stderr or "").strip()
                    body_out = (result.stdout or "").strip()
                    raise GitHubOpsError(
                        f"create_pr REST API failed (exit {result.returncode}): {detail}"
                        + (f" :: {body_out}" if body_out else "")
                    )
                try:
                    data = json.loads((result.stdout or "").strip() or "{}")
                    pr_number = int(data["number"])
                except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                    raise GitHubOpsError(
                        f"create_pr REST API returned no PR number: {result.stdout!r}"
                    )
                logger.info("Created PR #%s (REST API, bot identity)", pr_number)
                return self.get_pr(pr_number)

        # Fallback: gh pr create (owner identity) — same-user / no-token path.
        args = [
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body or "",
            "--base",
            base,
        ]
        if head:
            args.extend(["--head", head])
        if draft:
            args.append("--draft")

        result = self._run_gh(args)
        # gh pr create doesn't support --json; parse URL from stdout
        # e.g. "https://github.com/owner/repo/pull/123"
        output = result.stdout.strip()
        pr_url = output.split("\n")[-1].strip()

        # Extract PR number from URL
        try:
            pr_number = int(pr_url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            raise GitHubOpsError(f"Failed to parse PR number from output: {output}")
        logger.info("Created PR #%s", pr_number)

        # Fetch structured data via gh pr view
        return self.get_pr(pr_number)

    def get_pr(self, number: int) -> dict:
        """Get PR details."""
        result = self._run_gh(
            [
                "pr",
                "view",
                str(number),
                "--json",
                "number,title,body,url,state,headRefName,baseRefName,additions,deletions,changedFiles,commits",
            ]
        )
        return json.loads(result.stdout.strip())

    def get_pr_merge_state(self, number: int) -> dict:
        """Return GitHub's current mergeability classification for a PR.

        ``gh pr merge`` uses one exit status for conflicts, pending required
        checks, review rules, and other repository-rule violations.  The REST
        fields let the orchestrator distinguish a real ``dirty`` conflict from
        a policy-blocked but otherwise mergeable PR instead of launching the
        conflict resolver for every rejection.
        """
        repo = self.get_repo_name()
        result = self._run_gh(
            self._gh_api_args([f"repos/{repo}/pulls/{number}"]),
            repo_scoped=False,
        )
        data = json.loads((result.stdout or "").strip() or "{}")
        return {
            "mergeable": data.get("mergeable"),
            "mergeable_state": str(data.get("mergeable_state") or "").strip().lower(),
        }

    @staticmethod
    def _is_not_found(stderr: str) -> bool:
        """Whether ``stderr`` is GitHub reporting a definitive 404.

        Anchored on the HTTP status gh prints, NOT on a bare ``not found``
        substring: ``sudo: gh: command not found`` is a missing binary on the
        service account's PATH, and treating that as "no branch protection"
        would report an empty required set with no error at all — silently
        voiding the fail-closed contract this helper feeds. ``not protected``
        stays because gh emits it verbatim for an unprotected branch.
        """
        lowered = (stderr or "").strip().lower()
        # Exclude the shell/exec failures FIRST. gh is invoked through `sudo -u
        # <account> gh …`, so a gh missing from that account's PATH surfaces as
        # `sudo: gh: command not found` — Python found `sudo`, so no
        # FileNotFoundError is raised and a bare `not found` substring test
        # would classify a broken deployment as "this branch has no protection".
        if "command not found" in lowered or "no such file" in lowered:
            return False
        # gh prints `gh: Not Found (HTTP 404)`; the raw REST body carries
        # `"status":"404"`. Both are definitive absence.
        return "404" in lowered or "not protected" in lowered

    def _probe_gh_api(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run a read-only ``gh api`` probe, retrying transient failures.

        ``_run_gh(check=False)`` returns the first non-zero exit immediately —
        its retry loop is guarded by ``if check and result.returncode != 0``.
        The required-check probes need ``check=False`` so they can inspect a
        404, which meant a single ``Empty reply from server`` (this host reaches
        github.com through a failover proxy) resolved to "source blind".

        That is not a harmless degradation: a blind probe makes
        ``get_branch_protection`` raise, ``_blocking_pending`` then treats
        *every* pending check as blocking, and a slow non-required job re-defers
        the merge every scheduler cycle — the precise failure this issue exists
        to remove. So transient errors are retried
        here on the same schedule ``_run_gh`` uses for its checked calls.
        """
        result = self._run_gh(args, repo_scoped=False, check=False)
        for attempt in range(1, GIT_NETWORK_RETRY_COUNT):
            if result.returncode == 0 or not _is_transient_error(result.stderr, result.returncode):
                return result
            logger.warning(
                "gh api probe transient error (attempt %d/%d), retrying in %ds: %s",
                attempt,
                GIT_NETWORK_RETRY_COUNT,
                GIT_NETWORK_RETRY_INTERVAL,
                (result.stderr or "").strip()[:200],
            )
            time.sleep(GIT_NETWORK_RETRY_INTERVAL)
            result = self._run_gh(args, repo_scoped=False, check=False)
        return result

    def _classic_required_contexts(self, repo: str, branch: str) -> tuple[list[str], str]:
        """Required checks declared by *classic* branch protection.

        Returns ``(contexts, error)``. A 404 is a definitive "no classic
        protection" and yields ``([], "")``. Any other failure yields
        ``([], reason)`` — this source is blind, which the caller must weigh
        against what the other source could see rather than treat as fatal on
        its own. Notably this endpoint requires admin scope, so the autonomous
        workflow's own PAT gets 403 here even though the branch is protected.
        """
        result = self._probe_gh_api(
            self._gh_api_args([f"repos/{repo}/branches/{branch}/protection"])
        )
        if result.returncode != 0:
            if self._is_not_found(result.stderr or ""):
                return [], ""
            return [], (
                f"classic protection unreadable (exit {result.returncode}): "
                f"{(result.stderr or '').strip()}"
            )
        data = json.loads((result.stdout or "").strip() or "{}")
        rsc = data.get("required_status_checks") or {}
        contexts: list[str] = []
        # GitHub returns the legacy string ``contexts`` array and/or the newer
        # ``checks`` array of {context, integration_id, ...} objects.
        for entry in list(rsc.get("contexts", []) or []) + list(rsc.get("checks", []) or []):
            name = entry.get("context") if isinstance(entry, dict) else entry
            if name and name not in contexts:
                contexts.append(name)
        return contexts, ""

    def _ruleset_required_contexts(self, repo: str, branch: str) -> tuple[list[str], str]:
        """Required checks declared by *rulesets* (issue #2428).

        Rulesets are the modern replacement for classic branch protection, and
        the classic endpoint does not describe them — it answers 404 for a
        ruleset-protected branch, or 403 for a token without admin scope (which
        is what the autonomous workflow's own PAT gets). Either way the classic
        source alone reports no required checks on such a repository.

        This endpoint needs only read access and returns the effective rules.

        Paginated: it emits one entry per (ruleset x rule), so a repository with
        an org-level ruleset stacked on repo-level ones can push the
        ``required_status_checks`` rule past the first page. Without
        ``--paginate`` that reads as "no required checks", which makes
        ``_blocking_pending`` treat every pending check as blocking. Verified against the
        live API: ``?per_page=1`` on this repo returns a ``Link: rel="next"``
        header, so the pagination is real and not hypothetical.
        """
        result = self._probe_gh_api(
            self._gh_api_args(["--paginate", f"repos/{repo}/rules/branches/{branch}"])
        )
        if result.returncode != 0:
            # A branch with no rules answers 200 [] (verified live), so a 404
            # here means the repo/endpoint itself is unknown rather than the
            # probe having failed — treat it as "no ruleset rules", as classic.
            if self._is_not_found(result.stderr or ""):
                return [], ""
            return [], (
                f"branch rules unreadable (exit {result.returncode}): "
                f"{(result.stderr or '').strip()}"
            )
        rules = json.loads((result.stdout or "").strip() or "[]")
        if not isinstance(rules, list):
            return [], ""
        contexts: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            params = rule.get("parameters") or {}
            for entry in params.get("required_status_checks", []) or []:
                name = entry.get("context") if isinstance(entry, dict) else entry
                if name and name not in contexts:
                    contexts.append(name)
        return contexts, ""

    def get_branch_protection(self, branch: str = "main") -> dict:
        """Return the required-check contexts enforced on ``branch``.

        Used to distinguish required from optional CI checks. Only
        ``required_status_checks`` is unpacked — that is the only field
        consumers need.

        Both enforcement mechanisms are consulted and unioned (issue #2428):
        classic branch protection AND rulesets. Querying only the classic
        endpoint made every ruleset-protected repository look unprotected.

        A branch with neither yields an empty list, which remains a valid
        classification — every failing check is optional — not a probe failure.

        Raises :class:`GitHubOpsError` only when **neither** source observed
        anything and at least one was blind, because "no required checks" would
        then be a guess (#1989).

        Note the direction honestly: partial blindness (one source readable, the
        other not) does NOT raise, so the required set can *understate*. That is
        deliberate. GitHub is the final gate — understating produces a rejected
        merge attempt and a visible policy pause, whereas raising makes
        ``_blocking_pending`` treat every pending check as blocking and re-defer
        the merge each cycle on slow non-required jobs, which is the
        #2428-shaped failure this method exists to remove (the failure-targeting
        filter was removed in #27, but the pending split still depends on this
        set). Over-reporting blindness is the more damaging error here, not the
        safer one.
        """
        repo = self.get_repo_name()
        contexts, classic_err = self._classic_required_contexts(repo, branch)
        ruleset_contexts, ruleset_err = self._ruleset_required_contexts(repo, branch)
        for name in ruleset_contexts:
            if name not in contexts:
                contexts.append(name)
        # Fail closed only when nothing was positively observed AND at least one
        # source was blind. A 403 on the classic endpoint is expected here (it
        # needs admin scope), so it must not veto a successful ruleset read —
        # but if neither source could see anything, "no required checks" would
        # be a guess, and guessing is what #1989 forbids.
        if not contexts and (classic_err or ruleset_err):
            raise GitHubOpsError(
                f"get_branch_protection({branch}) could not determine required checks: "
                + "; ".join(e for e in (classic_err, ruleset_err) if e)
            )
        return {"required_status_checks": {"contexts": contexts}}

    def find_existing_pr(self, head_branch: str) -> dict | None:
        """Find an existing open PR for a head branch (scoped to base=main).

        Returns the first matching PR dict ({number, url, title, ...}) or None.
        Used by the PR-creation path to recover gracefully when gh pr create
        reports "already exists" (race between the github_pr_number guard and
        the API call, or a re-entrant advance()). ``--base main`` avoids
        matching PRs into other bases (fork / cross-repo).
        """
        if not head_branch:
            return None
        result = self._run_gh(
            [
                "pr",
                "list",
                "--head",
                head_branch,
                "--base",
                "main",
                "--state",
                "open",
                "--json",
                "number,url,title",
                "--limit",
                "1",
            ],
            check=False,
        )
        if result.returncode != 0:
            return None
        try:
            prs = json.loads((result.stdout or "").strip() or "[]")
        except json.JSONDecodeError:
            return None
        return prs[0] if prs else None

    def add_pr_comment(self, number: int, body: str) -> dict:
        """Add a comment to a PR."""
        self._run_gh(["pr", "comment", str(number), "--body", body], api_only=True)
        logger.info("Added comment to PR #%s", number)
        return {"number": number, "body": body}

    def get_merge_commit_sha(self, pr_number: int) -> str | None:
        """Return the merge commit SHA of a merged PR, or None if unmerged/unknown.

        Used by acceptance_verification to pin the exact main SHA the verifier
        runs against (#2335).
        """
        result = self._run_gh(
            ["pr", "view", str(pr_number), "--json", "mergeCommit", "--jq", ".mergeCommit.oid"],
            check=False,
        )
        out = (result.stdout or "").strip()
        return out or None

    def ensure_commit_available(self, commit_sha: str) -> bool:
        """Fetch a merge commit into the local repository if it is absent.

        ``gh pr merge`` completes server-side, so its merge SHA is not
        necessarily present in the long-lived production clone.  Fetch the
        exact object first, fall back to the reachable ``main`` ref for servers
        that disallow raw-SHA wants, then verify the object before any diff or
        worktree operation uses it.
        """
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_sha or ""):
            return False

        def _present() -> bool:
            result = self._run_git(["cat-file", "-e", f"{commit_sha}^{{commit}}"], check=False)
            return result.returncode == 0

        if _present():
            return True
        self._run_git(["fetch", "--no-tags", "origin", commit_sha], check=False)
        if _present():
            return True
        self._run_git(["fetch", "--no-tags", "origin", "main"], check=False)
        return _present()

    def _gh_api_args(self, extra: list[str]) -> list[str]:
        """Build a ``gh api`` arg list with GHES hostname handling.

        ``gh api`` rejects ``-R`` (unlike issue/pr subcommands) and resolves the
        repo from the REST path; under a sudo wrapper we therefore call it with
        ``repo_scoped=False`` (no ``-R`` injection). For a GHES host it instead
        needs ``--hostname`` to target the right server.
        """
        self._resolve_owner_repo()
        args = ["api"]
        if self._repo_host and self._repo_host != "github.com":
            args += ["--hostname", self._repo_host]
        return args + extra

    def list_pr_comments(self, number: int) -> list:
        """List review comments on a PR."""
        repo = self.get_repo_name()
        result = self._run_gh(
            self._gh_api_args(
                [
                    f"repos/{repo}/pulls/{number}/comments",
                    "--jq",
                    ".[] | {id, path, body, line, created_at, user: .user.login}",
                ]
            ),
            repo_scoped=False,
        )
        if not result.stdout.strip():
            return []
        # Parse NDJSON output
        comments = []
        for line in result.stdout.strip().split("\n"):
            try:
                comments.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return comments

    def list_pr_issue_comments(self, number: int) -> list:
        """List issue-level comments on a PR."""
        repo = self.get_repo_name()
        result = self._run_gh(
            self._gh_api_args(
                [
                    f"repos/{repo}/issues/{number}/comments",
                    "--jq",
                    ".[] | {id, body, created_at, user: .user.login}",
                ]
            ),
            repo_scoped=False,
        )
        if not result.stdout.strip():
            return []
        comments = []
        for line in result.stdout.strip().split("\n"):
            try:
                comments.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return comments

    def merge_pr(
        self, number: int, strategy: str = "merge", auto: bool = False, admin: bool = False
    ) -> dict:
        """Merge a PR.

        When ``auto`` is set, adds ``--auto`` so GitHub merges asynchronously
        once branch-protection requirements (CI, reviews) pass — used when the
        immediate merge is rejected solely because of policy, not conflicts.

        When ``admin`` is set, adds ``--admin`` to bypass branch-protection
        checks — used after conflict resolution when the only blocker is CI
        not yet catching up to the freshly-pushed merge commit.

        Security Note (Issue #1855): ``--admin`` bypasses branch protection
        and requires explicit opt-in via ``OPENACE_ALLOW_ADMIN_MERGE=1``.
        """
        args = ["pr", "merge", str(number)]
        if strategy == "squash":
            args.append("--squash")
        elif strategy == "rebase":
            args.append("--rebase")
        else:
            args.append("--merge")
        if auto:
            args.append("--auto")
        if admin:
            # Issue #1855: Check for explicit opt-in before using --admin
            if os.environ.get("OPENACE_ALLOW_ADMIN_MERGE") != "1":
                raise PermissionError(
                    "gh pr merge --admin requires explicit opt-in. "
                    "Set OPENACE_ALLOW_ADMIN_MERGE=1 to enable admin merge, "
                    "which bypasses branch protection checks."
                )
            args.append("--admin")

        self._run_gh(args)
        logger.info("Merged PR #%s (auto=%s, admin=%s)", number, auto, admin)
        return {"number": number, "merged": True}

    def list_pr_commits(self, number: int) -> list:
        """List commits in a PR."""
        result = self._run_gh(["pr", "view", str(number), "--json", "commits"])
        data = json.loads(result.stdout.strip())
        return data.get("commits", [])

    @staticmethod
    def _bucket_from_status_state(state: str) -> str:
        state = (state or "").strip().lower()
        if state in {"queued", "in_progress", "pending", "waiting", "requested"}:
            return "pending"
        if state in {"success", "passed", "pass"}:
            return "pass"
        if state in {"neutral", "skipped"}:
            return "skipping"
        if state in {"failure", "failed", "error", "cancelled", "timed_out", "action_required"}:
            return "fail"
        return "pending" if not state else "fail"

    @classmethod
    def _bucket_from_check_run(cls, status: str, conclusion: str) -> str:
        normalized_status = (status or "").strip().lower()
        if normalized_status and normalized_status != "completed":
            return "pending"
        return cls._bucket_from_status_state(conclusion or normalized_status)

    @staticmethod
    def _is_pr_checks_json_unsupported(output: str) -> bool:
        lowered = (output or "").lower()
        return "--json" in lowered and (
            "unknown flag" in lowered
            or "accepts 1 arg" in lowered
            or "unknown shorthand flag" in lowered
        )

    def _get_pr_head_sha(self, pr_number: int) -> str:
        repo = self.get_repo_name()
        result = self._run_gh(
            self._gh_api_args([f"repos/{repo}/pulls/{pr_number}"]),
            repo_scoped=False,
        )
        data = json.loads((result.stdout or "").strip() or "{}")
        return ((data.get("head") or {}).get("sha") or "").strip()

    def get_pr_head_sha(self, pr_number: int) -> str:
        """Return the current head SHA for a PR."""
        return self._get_pr_head_sha(pr_number)

    def _get_pr_checks_via_api(self, pr_number: int) -> list:
        repo = self.get_repo_name()
        head_sha = self._get_pr_head_sha(pr_number)
        if not head_sha:
            raise GitHubOpsError(f"Unable to resolve head SHA for PR #{pr_number}")

        status_result = self._run_gh(
            self._gh_api_args([f"repos/{repo}/commits/{head_sha}/status"]),
            repo_scoped=False,
        )
        check_runs_result = self._run_gh(
            self._gh_api_args([f"repos/{repo}/commits/{head_sha}/check-runs"]),
            repo_scoped=False,
        )

        status_payload = json.loads((status_result.stdout or "").strip() or "{}")
        check_runs_payload = json.loads((check_runs_result.stdout or "").strip() or "{}")

        checks: list[dict] = []
        for status in status_payload.get("statuses", []) or []:
            state = (status.get("state") or "").strip().lower()
            checks.append(
                {
                    "name": status.get("context") or "status",
                    "state": state or "unknown",
                    "bucket": self._bucket_from_status_state(state),
                    "link": status.get("target_url") or "",
                    # Attach head_sha so get_check_failure_excerpt can fall back
                    # to `gh run list --commit` when the link isn't a parseable
                    # Actions job URL (REST API returns /runs/<id>).
                    "head_sha": head_sha,
                }
            )

        for check_run in check_runs_payload.get("check_runs", []) or []:
            status = (check_run.get("status") or "").strip().lower()
            conclusion = (check_run.get("conclusion") or "").strip().lower()
            checks.append(
                {
                    "name": check_run.get("name") or "check-run",
                    "state": conclusion or status or "unknown",
                    "bucket": self._bucket_from_check_run(status, conclusion),
                    "link": check_run.get("html_url") or "",
                    "head_sha": head_sha,
                }
            )

        return checks

    def get_pr_checks(self, pr_number: int) -> list:
        """Get CI check status for a PR.

        Returns list of dicts with keys: name, state, bucket, link.
        bucket values: 'pass', 'fail', 'skipping', 'pending'.
        """
        result = self._run_gh(
            ["pr", "checks", str(pr_number), "--json", "name,state,bucket,link"],
            check=False,
        )
        try:
            if result.returncode == 0:
                raw = result.stdout if result.stdout else ""
                if not raw.strip():
                    return []
                return json.loads(raw)
        except (json.JSONDecodeError, AttributeError, TypeError):
            raw = result.stdout if result.stdout else ""
            logger.warning("Failed to parse CI checks for PR #%s: %s", pr_number, raw[:200])

        primary_parts = []
        for part in (getattr(result, "stderr", ""), getattr(result, "stdout", "")):
            if not isinstance(part, str):
                continue
            stripped = part.strip()
            if stripped:
                primary_parts.append(stripped)
        primary_output = "\n".join(primary_parts)
        if result.returncode != 0 and not self._is_pr_checks_json_unsupported(primary_output):
            logger.warning(
                "gh pr checks --json failed for PR #%s, falling back to REST API: %s",
                pr_number,
                primary_output[:200],
            )
        elif self._is_pr_checks_json_unsupported(primary_output):
            logger.info(
                "gh pr checks --json unsupported for PR #%s, falling back to REST API",
                pr_number,
            )

        try:
            return self._get_pr_checks_via_api(pr_number)
        except Exception as api_err:
            detail = primary_output[:200] if primary_output else "empty response"
            raise GitHubOpsError(
                f"Failed to query CI checks for PR #{pr_number}: {detail}; "
                f"REST fallback error: {api_err}"
            ) from api_err

    @staticmethod
    def _parse_github_actions_job_url(url: str) -> tuple[str, str] | None:
        """Extract GitHub Actions run/job ids from a check details URL."""
        if not url:
            return None
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        match = _GITHUB_ACTIONS_JOB_PATH_RE.match(parsed.path)
        if not match:
            return None
        return match.group("run_id"), match.group("job_id")

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape sequences from GitHub Actions logs."""
        return _ANSI_ESCAPE_RE.sub("", text or "")

    def _clean_log_to_excerpt(
        self, raw_log: str, max_lines: int, max_chars: int, *, filter_lines: bool = True
    ) -> str:
        """Strip ANSI; optionally keep failure-marker lines; truncate to max_chars.

        ``filter_lines=True`` (default) keeps only failure-marker lines via
        ``_extract_failure_lines`` — the historical behavior for noisy pre-commit /
        pytest / mypy logs. ``filter_lines=False`` skips that filter (ANSI-strip +
        truncate only) so callers that need the full log body (e.g. the schema-sync
        byte-exact git diff, whose ``+``/``-`` lines match no failure marker) can
        receive it verbatim.
        """
        cleaned = self._strip_ansi(raw_log or "").strip()
        if not cleaned:
            return ""
        lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""
        if filter_lines:
            lines = _extract_failure_lines(lines, max_lines)
        else:
            lines = lines[-max_lines:] if len(lines) > max_lines else lines
        excerpt = "\n".join(lines).strip()
        if len(excerpt) > max_chars:
            excerpt = excerpt[-max_chars:].lstrip()
        return excerpt

    def _fetch_log_excerpt_via_run_list(
        self, check: dict, max_lines: int, max_chars: int, *, filter_lines: bool = True
    ) -> str:
        """Fallback when the check link isn't a parseable Actions job URL.

        The REST API path (`_get_pr_checks_via_api`) populates `link` with the
        check-run `html_url` (`/runs/<check_run_id>`), which does NOT carry the
        workflow run/job ids that `gh run view --job` needs. Locate the workflow
        run by the PR head sha + check name, then pull `--log-failed` for the
        whole run. `gh run list/view` are core commands available on older gh
        versions that lack `gh pr checks --json`.

        Known limitation: `gh run list --json name` returns the WORKFLOW run
        name (the workflow YAML's top-level `name:`, defaulting to the repo
        name), while `check.name` is the JOB name inside the workflow (e.g.
        "lint", "test (3.10)"). For an umbrella workflow (run name "CI",
        jobs "lint"/"test") the name match fails and we fall back to runs[0].
        If a commit triggers multiple workflows, runs[0] may belong to a
        different workflow and its failure log gets attributed to this check.
        This does NOT affect the give-up guard (the fingerprint stays stable
        across rounds — same wrong log each time), but the repair context
        fed to the agent may be off-topic in multi-workflow repos. Single-
        workflow repos (the common case) are unaffected. Could be tightened
        with `--workflow` filtering if this becomes a real problem.
        """
        head_sha = (check.get("head_sha") or "").strip()
        if not head_sha:
            return ""
        name = str(check.get("name") or "").strip()

        # Find workflow runs for this commit.
        list_result = self._run_gh(
            ["run", "list", "--commit", head_sha, "--json", "databaseId,name", "--limit", "30"],
            check=False,
        )
        if list_result.returncode != 0:
            logger.warning(
                "gh run list --commit failed for check '%s' (sha=%s): %s",
                name,
                head_sha[:8],
                (list_result.stderr or list_result.stdout or "").strip()[:200],
            )
            return ""
        try:
            runs = json.loads((list_result.stdout or "").strip() or "[]")
        except json.JSONDecodeError:
            runs = []
        # Prefer the run whose name matches the check (Actions "lint" check ↔
        # workflow run named "lint"); fall back to the first run if no match.
        run_id = ""
        for run in runs:
            if str(run.get("name") or "").strip() == name:
                run_id = str(run.get("databaseId") or "").strip()
                break
        if not run_id and runs:
            run_id = str(runs[0].get("databaseId") or "").strip()
        if not run_id:
            return ""

        view_result = self._run_gh(
            ["run", "view", run_id, "--log-failed", "--allow-escape-sequences"],
            check=False,
        )
        if view_result.returncode != 0:
            logger.warning(
                "gh run view --log-failed failed for check '%s' (run=%s): %s",
                name,
                run_id,
                (view_result.stderr or view_result.stdout or "").strip()[:200],
            )
            return ""
        return self._clean_log_to_excerpt(
            view_result.stdout or "", max_lines, max_chars, filter_lines=filter_lines
        )

    def get_check_failure_excerpt(
        self,
        check: dict,
        max_lines: int = 80,
        max_chars: int = 4000,
        *,
        filter_lines: bool = True,
    ) -> str:
        """Fetch a concise failed-log excerpt for a CI check when available.

        Supports GitHub Actions job URLs (`gh pr checks --json` link format).
        When the link is a REST-API check-run URL without run/job ids, falls
        back to locating the workflow run via `gh run list --commit <sha>`
        (requires the check dict to carry `head_sha`). Non-Actions URLs with
        no head_sha return an empty string so callers degrade gracefully.

        pre-commit / pytest / mypy output interleaves many passing hooks with a
        few failing ones; taking the last N lines can drop the actual error
        (e.g. mypy fails in the middle while later hooks continue). Instead we
        scan every line for failure markers and return those lines (with a
        little context), falling back to the tail when no markers match.
        """
        parsed = self._parse_github_actions_job_url(str(check.get("link") or ""))
        if parsed:
            run_id, job_id = parsed
            # Fetch the job log through the REST endpoint first.  ``gh run
            # view --log-failed`` downloads a run-level zip into gh's shared
            # cache.  In a multi-user deployment that cache can be owned by a
            # different account (for example root), which made every repair
            # prompt lose its real failure output with ``permission denied``.
            # The job endpoint returns decoded log text directly and does not
            # use the run-log cache.
            self._resolve_owner_repo()
            link_host = (urlparse(str(check.get("link") or "")).hostname or "").lower()
            repo_host = (self._repo_host or "github.com").lower()
            if link_host != repo_host:
                logger.warning(
                    "Ignoring Actions job URL on unexpected host '%s' (repository host '%s')",
                    link_host,
                    repo_host,
                )
                return self._fetch_log_excerpt_via_run_list(
                    check, max_lines, max_chars, filter_lines=filter_lines
                )
            elif self._repo_slug:
                api_args = ["api"]
                if self._repo_host and self._repo_host != "github.com":
                    api_args += ["--hostname", self._repo_host]
                api_args.append(f"repos/{self._repo_slug}/actions/jobs/{job_id}/logs")
                # Recent gh (verified on server's 2.97.0) refuses to emit log
                # text containing ANSI escape sequences unless this flag is
                # passed; without it the job-log REST endpoint always fails and
                # CI repair can never read failure logs. The sequences are
                # stripped afterward by _clean_log_to_excerpt / _strip_ansi.
                api_args.append("--allow-escape-sequences")
                api_result = self._run_gh(api_args, check=False, repo_scoped=False)
                if api_result.returncode == 0 and (api_result.stdout or "").strip():
                    return self._clean_log_to_excerpt(
                        api_result.stdout or "",
                        max_lines,
                        max_chars,
                        filter_lines=filter_lines,
                    )
                logger.warning(
                    "REST job-log fetch failed for check '%s' (run=%s job=%s), "
                    "falling back to gh run view: %s",
                    check.get("name") or "unknown",
                    run_id,
                    job_id,
                    (api_result.stderr or api_result.stdout or "").strip()[:200],
                )

            result = self._run_gh(
                [
                    "run",
                    "view",
                    run_id,
                    "--job",
                    job_id,
                    "--log-failed",
                    "--allow-escape-sequences",
                ],
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to fetch failed log excerpt for check '%s' (run=%s job=%s): %s",
                    check.get("name") or "unknown",
                    run_id,
                    job_id,
                    (result.stderr or result.stdout or "").strip()[:200],
                )
                return ""
            return self._clean_log_to_excerpt(
                result.stdout or "", max_lines, max_chars, filter_lines=filter_lines
            )

        # Fallback: link is not a parseable Actions job URL (e.g. REST-API
        # check-run html_url "/runs/<id>"). Try to find the workflow run by
        # the PR head sha and pull --log-failed for the whole run.
        return self._fetch_log_excerpt_via_run_list(
            check, max_lines, max_chars, filter_lines=filter_lines
        )

    def get_pr_diff(self, number: int) -> str:
        """Get the full diff of a PR (head vs base) via `gh pr diff`.

        Returns the diff as a string. Returns "" when the PR is missing or
        the command fails, so callers can render an empty state instead of 500.
        """
        result = self._run_gh(["pr", "diff", str(number)], check=False)
        if result.returncode != 0:
            logger.warning(
                "gh pr diff #%s failed (exit %s): %s",
                number,
                result.returncode,
                (result.stderr or "").strip()[:200],
            )
            return ""
        return result.stdout or ""

    # ── Diff Operations ─────────────────────────────────────────────

    def get_diff(self, base: str = "HEAD~1", head: str = "HEAD") -> str:
        """Get the diff between two refs."""
        result = self._run_git(["diff", base, head])
        return result.stdout

    def get_diff_stats(self, base: str = "HEAD~1", head: str = "HEAD") -> dict:
        """Get diff statistics between two refs."""
        # Get numstat for additions/deletions per file
        result = self._run_git(["diff", "--numstat", base, head])
        total_additions = 0
        total_deletions = 0
        files = 0
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split("\t")
                if len(parts) >= 2:
                    total_additions += int(parts[0]) if parts[0] != "-" else 0
                    total_deletions += int(parts[1]) if parts[1] != "-" else 0
                    files += 1

        # Get commit count
        commit_result = self._run_git(["rev-list", "--count", f"{base}..{head}"])
        commits = int(commit_result.stdout.strip()) if commit_result.stdout.strip() else 0

        return {
            "additions": total_additions,
            "deletions": total_deletions,
            "files": files,
            "commits": commits,
        }

    def get_changed_files(self, base: str = "HEAD~1", head: str = "HEAD") -> list[str]:
        """Get changed file paths between two refs."""
        result = self._run_git(["diff", "--name-only", base, head])
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def get_commit_diff(self, sha: str) -> str:
        """Get the diff for a specific commit."""
        result = self._run_git(["show", "--format=", sha])
        return result.stdout

    def get_commit_diff_stats(self, sha: str) -> dict:
        """Get diff statistics for a specific commit."""
        result = self._run_git(["show", "--numstat", "--format=", sha])
        total_additions = 0
        total_deletions = 0
        files = 0
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split("\t")
                if len(parts) >= 2:
                    total_additions += int(parts[0]) if parts[0] != "-" else 0
                    total_deletions += int(parts[1]) if parts[1] != "-" else 0
                    files += 1

        return {
            "additions": total_additions,
            "deletions": total_deletions,
            "files": files,
            "commits": 1 if files or total_additions or total_deletions else 0,
        }

    def get_commit_changed_files(self, sha: str) -> list[str]:
        """Get changed file paths for a specific commit."""
        result = self._run_git(["show", "--name-only", "--format=", sha])
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ── Git Operations ──────────────────────────────────────────────

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes (staged or unstaged)."""
        result = self._run_git(["status", "--porcelain"])
        return bool(result.stdout.strip())

    def get_unmerged_paths(self) -> list[str]:
        """Return paths that still have unmerged index entries."""
        result = self._run_git(["diff", "--name-only", "--diff-filter=U"], check=False)
        if result.returncode != 0:
            raise GitHubOpsError(
                f"Unable to inspect unmerged index (exit code {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def get_worktree_changed_paths(self) -> list[str]:
        """Return tracked working-tree changes plus untracked files.

        During a conflicted merge, clean changes brought in from the other
        parent are already staged in the index.  This view therefore isolates
        the resolver's still-unstaged edits (including U-stage paths) instead
        of counting every file that arrived from ``origin/main``.
        """
        tracked = self._run_git(["diff", "--name-only"], check=False)
        if tracked.returncode != 0:
            raise GitHubOpsError(
                f"Unable to inspect working-tree changes (exit code {tracked.returncode}): "
                f"{(tracked.stderr or tracked.stdout).strip()}"
            )
        untracked = self._run_git(["ls-files", "--others", "--exclude-standard"], check=False)
        if untracked.returncode != 0:
            raise GitHubOpsError(
                f"Unable to inspect untracked files (exit code {untracked.returncode}): "
                f"{(untracked.stderr or untracked.stdout).strip()}"
            )
        return sorted(
            {
                line.strip()
                for output in (tracked.stdout, untracked.stdout)
                for line in output.splitlines()
                if line.strip()
            }
        )

    def get_index_snapshot(self) -> dict[str, tuple[str, ...]]:
        """Return the complete staged-index identity grouped by path.

        Each value contains the mode/blob/stage tuples emitted by
        ``git ls-files --stage``.  U-stage paths therefore retain all base,
        ours, and theirs entries, while normal staged files have one stage-0
        entry.  ``-z`` keeps unusual filenames unambiguous.
        """
        result = self._run_git(["ls-files", "--stage", "-z"], check=False)
        if result.returncode != 0:
            raise GitHubOpsError(
                f"Unable to snapshot git index (exit code {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )
        entries: dict[str, list[str]] = {}
        for record in result.stdout.split("\0"):
            if not record:
                continue
            identity, separator, path = record.partition("\t")
            if not separator or not identity or not path:
                raise GitHubOpsError("Unable to parse git index snapshot")
            entries.setdefault(path, []).append(identity)
        return {path: tuple(sorted(identities)) for path, identities in entries.items()}

    @staticmethod
    def get_index_changed_paths(
        before: dict[str, tuple[str, ...]], after: dict[str, tuple[str, ...]]
    ) -> list[str]:
        """Return paths whose stage entries were added, removed, or changed."""
        return sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )

    def get_conflict_marker_paths(self, paths: list[str]) -> list[str]:
        """Return files that still contain standard merge-conflict markers.

        The caller supplies the authoritative U-stage path list captured before
        staging.  Restricting the search to those files avoids false positives
        from fixtures or documentation elsewhere in the repository.
        """
        if not paths:
            return []
        existing_paths = [
            path
            for path in sorted(set(paths))
            if self.path_exists_as_user(os.path.join(self.repo_path, path), file_only=True)
        ]
        # Deleting a conflicted file is a valid resolution. Owner-side
        # ``git add -A`` records that deletion after this marker check.
        if not existing_paths:
            return []
        result = self._run_git(
            [
                "grep",
                "--no-index",
                "-l",
                "-I",
                "-E",
                "-e",
                r"^<{7,}( |$)",
                "-e",
                r"^={7,}$",
                "-e",
                r"^>{7,}( |$)",
                "--",
                *existing_paths,
            ],
            check=False,
        )
        # git grep returns 1 when there are no matches.
        if result.returncode not in (0, 1):
            raise GitHubOpsError(
                f"Unable to inspect conflict markers (exit code {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()}"
            )
        if result.returncode == 1:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def git_add_all(self) -> None:
        """Stage all changes, excluding test-pollution artifacts.

        After staging, remove any ``.worktrees/`` gitlinks that ``git add -A``
        may have picked up from nested worktree directories. These appear as
        160000-mode submodule references but have no ``.gitmodules`` entry,
        which breaks CI (``git submodule foreach`` fails with "No url found
        for submodule path") and pollutes schema-sync diffs.

        Additionally filter test-pollution artifacts that the dev agent's
        pytest run can create: empty files whose names are ``repr()`` of
        MagicMock objects (mocks passed to ``Path(...)``/``open(...)``), plus
        the standard test caches (``__pycache__``, ``.pytest_cache``). These
        are not legitimate source files; committing them inflates the scope
        guard's file count and blocks CI-repair pushes.
        """
        self._run_git(["add", "-A"])
        try:
            self._run_git(
                ["rm", "-r", "--cached", "--ignore-unmatch", ".worktrees"],
                check=False,
            )
        except Exception:
            pass
        self._unstage_test_artifacts()

    @staticmethod
    def _is_test_artifact_path(path: str) -> bool:
        """Return True for test-pollution paths that must not be committed.

        These are files created by pytest side-effects when the dev agent runs
        tests in its worktree: ``repr()`` of MagicMock objects passed to
        ``Path(...)``/``open(...)`` (e.g. ``<MagicMock id='...'>``), and the
        standard Python/pytest caches. Also rejects any path that isn't a sane
        relative filename (contains ``<``/``>``, is absolute, or is empty).
        """
        if not path:
            return True
        # MagicMock repr and angle-bracket garbage: ``<MagicMock ...>`` etc.
        if "<" in path or ">" in path:
            return True
        # Absolute paths are never valid repo-relative filenames.
        if path.startswith("/"):
            return True
        # Standard test caches that pytest/cpython create as side-effects.
        parts = path.split("/")
        if "__pycache__" in parts:
            return True
        if parts and parts[0] == ".pytest_cache":
            return True
        return False

    def _unstage_test_artifacts(self) -> None:
        """Unstage test-pollution artifacts from the git index.

        Reads the staged file list, identifies test artifacts via
        ``_is_test_artifact_path``, and unstages each with
        ``git reset HEAD -- <path>``. Fail-soft: a transient git error leaves
        the original staging intact (legitimate files stay staged).
        """
        try:
            result = self._run_git(["diff", "--cached", "--name-only"], check=False)
            if result.returncode != 0:
                return
            staged = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            return

        artifacts = [p for p in staged if self._is_test_artifact_path(p)]
        for path in artifacts:
            try:
                self._run_git(["reset", "-q", "HEAD", "--", path], check=False)
            except Exception:
                # Best-effort: continue unstaging the remaining artifacts.
                pass

    def git_commit(self, message: str, no_verify: bool = False) -> dict:
        """Create a git commit.

        Args:
            message: Commit message.
            no_verify: Skip pre-commit hooks. Use for auto-commits where the
                       agent's output hasn't been lint-checked and hooks would
                       block the commit, causing "no code changes" failures.
        """
        args = ["commit", "-m", message]
        if no_verify:
            args.append("--no-verify")
        self._run_git(args)
        sha = self.get_current_commit()
        return {"sha": sha, "message": message}

    def git_push(
        self,
        remote: str = "origin",
        branch: str | None = None,
        force_with_lease: bool = False,
    ) -> None:
        """Push to remote.

        ``force_with_lease`` appends ``--force-with-lease`` so a re-committed
        auto-dev branch (review-fix / CI-repair / dev round 2+ re-committing
        already-pushed work) can overwrite the remote tip without a
        non-fast-forward rejection. It is refused unless the resolved branch
        starts with ``auto-dev/`` — defense-in-depth so no caller can ever
        force-push main/release/user branches (Issue #1854).
        """
        if force_with_lease:
            target = branch
            if not target:
                try:
                    target = self.get_current_branch()
                except Exception as e:
                    raise GitHubOpsError(
                        "force_with_lease requires an auto-dev/* branch but the "
                        f"current branch could not be resolved: {e}"
                    )
            if not target.startswith("auto-dev/"):
                raise GitHubOpsError(
                    f"force_with_lease refused on non-auto-dev branch '{target}' "
                    "(only auto-dev/* workflow branches may be force-pushed)"
                )
            # Never leave a validated force-push target implicit. An auto-dev
            # worktree can inherit ``main`` as its upstream, and
            # ``push.default=simple`` then rejects a plain ``git push`` even
            # though the current local branch is safe and was validated above.
            branch = target
        args = ["push", remote]
        if branch:
            args.append(branch)
        if force_with_lease:
            args.append("--force-with-lease")
        try:
            self._run_git(args)
        except GitHubOpsError as e:
            if not (force_with_lease and _is_stale_lease_rejection(str(e))):
                raise
            # The push was rejected because our remote-tracking ref is stale
            # relative to the actual remote (recreated worktree / concurrent
            # push). Refresh the lease with a targeted fetch and retry once —
            # the local auto-dev branch is authoritative for this workflow, so
            # overwriting the remote after a fresh fetch is the intended
            # semantics. Without this recovery the orchestrator's Layer-2 retry
            # re-runs the identical push and loops to exhaustion (reproducer:
            # ee678c63, a reset worktree whose stale ref never refreshed).
            logger.warning(
                "force-with-lease stale lease for %s; fetching %s and retrying",
                target,
                remote,
            )
            if not target:
                # No validated branch to refresh (should not happen: the
                # force_with_lease guard above resolves target) — propagate.
                raise e
            try:
                self._run_git(["fetch", remote, target])
            except GitHubOpsError as fetch_err:
                logger.warning("lease-refresh fetch failed: %s", fetch_err)
                raise e from fetch_err
            self._run_git(args)

    def git_init(self) -> None:
        """Initialize a git repository."""
        self._run_git(["init"])

    def git_add_remote(self, name: str, url: str) -> None:
        """Add a remote."""
        self._run_git(["remote", "add", name, url])
