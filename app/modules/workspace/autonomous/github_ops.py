# mypy: disable-error-code="no-any-return,assignment,var-annotated"
"""
Open ACE - GitHub Operations

Wraps the `gh` CLI for repo, issue, branch, worktree, and PR operations.
All methods invoke gh/git via subprocess and return parsed results.
"""

import json
import logging
import os
import pwd
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GITHUB_ACTIONS_JOB_URL_RE = re.compile(
    r"https://github\.com/[^/]+/[^/]+/actions/runs/(?P<run_id>\d+)/job/(?P<job_id>\d+)"
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


class GitHubOps:
    """GitHub operations using the gh CLI."""

    # Class-level cache: track which system_accounts have safe.directory configured
    _safe_directory_configured: set[str] = set()

    def __init__(self, repo_path: str, system_account: Optional[str] = None):
        """
        Args:
            repo_path: Local path to the git repository (for cwd in gh commands).
            system_account: Optional system account name for multi-user permission isolation.
                When set, git/gh commands will be run via `sudo -u <system_account>`.
        """
        self.repo_path = repo_path
        self.system_account = system_account
        # Cached owner/repo (e.g. "open-ace/open-ace", or "host/owner/repo" for
        # GHES) resolved from the local repo's origin remote, used to target gh
        # under a sudo wrapper where gh can no longer infer the repo from cwd.
        # Lazily populated by _resolve_owner_repo(); None means "not resolvable
        # / not yet tried".
        self._owner_repo: Optional[str] = None
        # Pure OWNER/REPO slug (never carries the GHES host), used for REST API
        # paths (gh api repos/<owner>/<repo>/...). Populated alongside
        # _owner_repo by _resolve_owner_repo().
        self._repo_slug: Optional[str] = None
        # The remote host (e.g. "github.com" or "gh.example.com"), needed for
        # gh api --hostname under a sudo wrapper on GHES. None until resolved.
        self._repo_host: Optional[str] = None
        # Tracks whether _resolve_owner_repo has been attempted, so None can
        # distinguish "tried and failed" from "not yet tried".
        self._owner_repo_resolved: bool = False

    def _get_env(self) -> Optional[dict[str, str]]:
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

    def _resolve_owner_repo(self) -> Optional[str]:
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
        host: Optional[str] = None
        slug: Optional[str] = None
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
        self, args: list[str], check: bool = True, repo_scoped: bool = True
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
        """
        kwargs = self._build_subprocess_kwargs()
        account = self.system_account
        if self._needs_sudo():
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
            # Same-user (or no system_account): run gh directly with cwd so it
            # infers owner/repo from the working directory as before.
            cmd = ["gh"] + args
        last_error: Optional[GitHubOpsError] = None
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

    def _ensure_safe_directory(self) -> None:
        """Ensure git safe.directory is configured for Docker volume mounts.

        In Docker environments, git may reject operations on directories owned
        by different users (volume mounts). This configures safe.directory for
        the effective user (current user or sudo target user), preventing
        'dubious ownership' errors.

        Note: --global config writes to the executing user's ~/.gitconfig.
        When using sudo wrapper, we must run the config command as the target
        user so it writes to their ~/.gitconfig.
        """
        # Cache key: system_account or "default" for no sudo wrapper
        cache_key = self.system_account or "default"
        if cache_key in GitHubOps._safe_directory_configured:
            return

        # Build the git config command
        # Use wildcard for Docker environments with multiple mount paths
        safe_cmd: list[str] = ["git", "config", "--global", "--add", "safe.directory", "*"]

        # Wrap with sudo only when actually cross-user (same logic as _run_git).
        # This ensures the config is written to the target user's ~/.gitconfig.
        account = self.system_account
        if self._needs_sudo():
            assert account is not None  # _needs_sudo() guarantees non-empty
            safe_cmd = ["sudo", "-u", account] + safe_cmd

        # Execute without cwd (global config doesn't need repo context) and
        # without the default 120s timeout — pass our own shorter one. Both
        # must be dropped from _build_subprocess_kwargs() to avoid passing
        # `timeout` twice (a hard TypeError in Python, surfaced as a warning
        # here and which silently prevented safe.directory from ever being set).
        base_kwargs = self._build_subprocess_kwargs()
        safe_kwargs = {k: v for k, v in base_kwargs.items() if k not in ("cwd", "timeout")}
        try:
            subprocess.run(safe_cmd, **safe_kwargs, check=False, timeout=5)
            GitHubOps._safe_directory_configured.add(cache_key)
            logger.debug("Configured git safe.directory for user: %s", cache_key)
        except Exception as e:
            logger.warning("Failed to configure safe.directory for %s: %s", cache_key, e)

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command with transient-network-error retry."""
        # Ensure safe.directory is configured for Docker volume mounts
        self._ensure_safe_directory()
        kwargs = self._build_subprocess_kwargs()
        account = self.system_account
        if self._needs_sudo():
            # git supports `-C <path>` (unlike gh), so we use it to set the
            # working directory under a sudo wrapper where cwd would trigger a
            # Python permission check as the service user (Issue #1421).
            assert account is not None  # _needs_sudo() guarantees non-empty
            cmd: list[str] = ["sudo", "-u", account, "git", "-C", self.repo_path] + args
            kwargs.pop("cwd", None)  # Remove cwd to avoid Python permission check
        else:
            cmd = ["git"] + args
        last_error: Optional[GitHubOpsError] = None
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

    def create_issue(self, title: str, body: str = "", labels: Optional[list[str]] = None) -> dict:
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
        """Add a comment to an issue."""
        self._run_gh(["issue", "comment", str(number), "--body", body])
        logger.info("Added comment to issue #%s", number)
        return {"number": number}

    def list_issue_comments(self, number: int, since: Optional[str] = None) -> list:
        """List comments on an issue, optionally since a timestamp."""
        args = ["issue", "view", str(number), "--comments", "--json", "comments"]
        result = self._run_gh(args)
        data = json.loads(result.stdout.strip())
        comments = data.get("comments", [])
        if since:
            comments = [c for c in comments if c.get("createdAt", "") > since]
        return comments

    def update_issue(
        self, number: int, title: Optional[str] = None, body: Optional[str] = None
    ) -> dict:
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

    def delete_branch(self, name: str, remote: bool = True) -> None:
        """Delete a branch locally and optionally remotely."""
        self._run_git(["branch", "-D", name], check=False)
        if remote:
            self._run_git(["push", "origin", "--delete", name], check=False)

    # ── Worktree Operations ─────────────────────────────────────────

    def create_worktree(self, path: str, branch: str, base: str = "HEAD") -> dict:
        """Create a git worktree with a new branch."""
        self._run_git(["worktree", "add", "-b", branch, path, base])
        logger.info("Created worktree at %s on branch %s", path, branch)
        return {"worktree_path": path, "branch": branch}

    def add_worktree(self, path: str, branch: str) -> dict:
        """Create a worktree that checks out an EXISTING branch (no ``-b``).

        Used by merge-conflict resolution to get an isolated working tree of
        the PR branch without touching the main repo's index/HEAD. For a
        remote-only branch git auto-creates a local tracking branch.
        """
        self._run_git(["worktree", "add", path, branch])
        logger.info("Added worktree at %s for existing branch %s", path, branch)
        return {"worktree_path": path, "branch": branch}

    def remove_worktree(self, path: str) -> dict:
        """Remove a git worktree."""
        self._run_git(["worktree", "remove", path, "--force"])
        logger.info("Removed worktree at %s", path)
        return {"removed": path}

    def list_worktrees(self) -> list:
        """List all worktrees."""
        result = self._run_git(["worktree", "list", "--porcelain"])
        worktrees = []
        current = {}
        for line in result.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line.split(" ", 1)[1]}
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line == "bare":
                current["bare"] = True
        if current:
            worktrees.append(current)
        return worktrees

    # ── PR Operations ───────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        body: str = "",
        head: Optional[str] = None,
        base: str = "main",
        draft: bool = False,
    ) -> dict:
        """Create a pull request and return its details."""
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

    def add_pr_comment(self, number: int, body: str) -> dict:
        """Add a comment to a PR."""
        self._run_gh(["pr", "comment", str(number), "--body", body])
        logger.info("Added comment to PR #%s", number)
        return {"number": number, "body": body}

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
    def _parse_github_actions_job_url(url: str) -> Optional[tuple[str, str]]:
        """Extract GitHub Actions run/job ids from a check details URL."""
        if not url:
            return None
        match = _GITHUB_ACTIONS_JOB_URL_RE.match(url.strip())
        if not match:
            return None
        return match.group("run_id"), match.group("job_id")

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape sequences from GitHub Actions logs."""
        return _ANSI_ESCAPE_RE.sub("", text or "")

    def _clean_log_to_excerpt(self, raw_log: str, max_lines: int, max_chars: int) -> str:
        """Strip ANSI, keep failure-marker lines, truncate to max_chars."""
        cleaned = self._strip_ansi(raw_log or "").strip()
        if not cleaned:
            return ""
        lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""
        excerpt = "\n".join(_extract_failure_lines(lines, max_lines)).strip()
        if len(excerpt) > max_chars:
            excerpt = excerpt[-max_chars:].lstrip()
        return excerpt

    def _fetch_log_excerpt_via_run_list(self, check: dict, max_lines: int, max_chars: int) -> str:
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
        "lint", "test (3.9)"). For an umbrella workflow (run name "CI",
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
            ["run", "view", run_id, "--log-failed"],
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
        return self._clean_log_to_excerpt(view_result.stdout or "", max_lines, max_chars)

    def get_check_failure_excerpt(
        self, check: dict, max_lines: int = 80, max_chars: int = 4000
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
            result = self._run_gh(
                ["run", "view", run_id, "--job", job_id, "--log-failed"],
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
            return self._clean_log_to_excerpt(result.stdout or "", max_lines, max_chars)

        # Fallback: link is not a parseable Actions job URL (e.g. REST-API
        # check-run html_url "/runs/<id>"). Try to find the workflow run by
        # the PR head sha and pull --log-failed for the whole run.
        return self._fetch_log_excerpt_via_run_list(check, max_lines, max_chars)

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

    def git_add_all(self) -> None:
        """Stage all changes."""
        self._run_git(["add", "-A"])

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
        branch: Optional[str] = None,
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
        args = ["push", remote]
        if branch:
            args.append(branch)
        if force_with_lease:
            args.append("--force-with-lease")
        self._run_git(args)

    def git_init(self) -> None:
        """Initialize a git repository."""
        self._run_git(["init"])

    def git_add_remote(self, name: str, url: str) -> None:
        """Add a remote."""
        self._run_git(["remote", "add", name, url])
